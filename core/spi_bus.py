"""SPI master wrapper around spidev, with a simulation fallback.

The simulator echoes back a deterministic pattern so full-duplex transfers still
produce plausible RX data with no /dev/spidev node present.
"""

import glob
import os

from . import pinmux
from .logbus import log
from .util import fmt_bytes

try:
    import spidev
except ImportError:  # pragma: no cover
    spidev = None


class SPIError(Exception):
    pass


#: Chip-select behaviour across a multi-byte payload.
CS_BLOCK = "block"      # one ioctl: CS stays asserted for the whole frame
CS_PER_BYTE = "byte"    # one ioctl per byte: CS is released between bytes

#: spi_ioc_transfer.delay_usecs is a uint16.
MAX_DELAY_USEC = 0xFFFF

#: Pin functions SPI0 needs. CE0/CE1 are plain outputs because the Pi device
#: tree drives the chip selects via `cs-gpios`, not the controller's ALT0 lines.
EXPECTED_PIN_FUNCS = {**pinmux.SPI0_PINS, **pinmux.SPI0_CS_PINS}


class SPIBus:
    def __init__(self):
        self._spi = None
        self._bus = None
        self._dev = None
        self.simulated = False
        self._settings = {}
        #: what the driver actually applied, read back after open
        self.effective = {}

    # ---------------------------------------------------------------- discovery

    @staticmethod
    def available_devices():
        """(bus, device) pairs backed by an actual /dev/spidevB.D node."""
        found = []
        for path in glob.glob("/dev/spidev*.*"):
            name = os.path.basename(path).replace("spidev", "")
            try:
                bus, dev = name.split(".")
                found.append((int(bus), int(dev)))
            except ValueError:
                continue
        return sorted(found)

    # ------------------------------------------------------------- open / close

    @property
    def is_open(self):
        return self._spi is not None or self.simulated

    @property
    def label(self):
        if not self.is_open:
            return "-"
        return f"{self._bus}.{self._dev}"

    def open(self, bus, dev, speed_hz=1_000_000, mode=0, bits=8, lsb_first=False,
             simulated=False):
        self.close()
        self._bus, self._dev = bus, dev
        self._settings = dict(speed_hz=speed_hz, mode=mode, bits=bits, lsb_first=lsb_first)

        if simulated:
            self.simulated = True
            self._read_effective()
            log.info(f"SPI{bus}.{dev} 시뮬레이션 모드로 열었습니다 "
                     f"(mode {mode}, {speed_hz / 1e6:.3f} MHz)")
            return

        if spidev is None:
            raise SPIError("spidev 모듈이 설치되어 있지 않습니다")
        try:
            self._spi = spidev.SpiDev()
            self._spi.open(bus, dev)
            self._spi.max_speed_hz = int(speed_hz)
            self._spi.mode = int(mode)
            self._spi.bits_per_word = int(bits)
        except (OSError, IOError) as exc:
            self._spi = None
            raise SPIError(f"/dev/spidev{bus}.{dev} 열기 실패: {exc}") from exc

        if lsb_first:
            # spi-bcm2835 has no SPI_LSB_FIRST support. Losing bit order is far
            # better than losing the whole bus, so warn and stay MSB-first.
            try:
                self._spi.lsbfirst = True
            except (OSError, IOError) as exc:
                log.warn(f"이 드라이버는 LSB first를 지원하지 않습니다 ({exc}). "
                         "MSB first로 계속합니다")

        pinmux.restore(pinmux.SPI0_PINS)

        self._read_effective()
        log.info(f"SPI{bus}.{dev} 열림 — 요청 {speed_hz / 1e6:.3f} MHz / "
                 f"mode {mode} / {bits}bit, 실제 {self.effective_summary}")

    def close(self):
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
            log.info(f"SPI{self._bus}.{self._dev} 닫힘")
        self._spi = None
        self.simulated = False
        self._bus = self._dev = None
        self.effective = {}

    def _require_open(self):
        if not self.is_open:
            raise SPIError("SPI 버스가 열려 있지 않습니다")

    # --------------------------------------------------------- effective config

    def _read_effective(self):
        """Record what the driver actually applied.

        The Pi derives SCLK by dividing the core clock by an even number, so the
        rate in force is rarely the one requested; without reading it back a
        silent clamp looks like a working setting.
        """
        if self._spi is None:
            self.effective = dict(self._settings)
            return
        self.effective = {
            "speed_hz": self._spi.max_speed_hz,
            "mode": self._spi.mode,
            "bits": self._spi.bits_per_word,
            "lsb_first": bool(self._spi.lsbfirst),
        }

    @property
    def effective_summary(self):
        if not self.effective:
            return "-"
        speed = self.effective.get("speed_hz", 0)
        rate = f"{speed / 1e6:.3f} MHz" if speed >= 1e6 else f"{speed / 1e3:.1f} kHz"
        return (f"{rate}, mode {self.effective.get('mode', '?')}, "
                f"{self.effective.get('bits', '?')}bit, "
                f"{'LSB' if self.effective.get('lsb_first') else 'MSB'} first")

    # -------------------------------------------------------------- reconfigure

    def set_speed(self, speed_hz):
        self._settings["speed_hz"] = int(speed_hz)
        if self._spi is not None:
            self._spi.max_speed_hz = int(speed_hz)
            self._read_effective()
            log.info(f"SPI 클럭 변경 — 요청 {speed_hz / 1e6:.3f} MHz, "
                     f"실제 {self.effective_summary}")

    def set_mode(self, mode):
        self._settings["mode"] = int(mode)
        if self._spi is not None:
            self._spi.mode = int(mode)
            self._read_effective()

    # --------------------------------------------------------------- transfers

    def transfer(self, data, read_extra=0, filler=0x00, cs_mode=CS_BLOCK,
                 delay_usec=0, speed_hz=None, quiet=False):
        """Full-duplex transfer; returns one RX byte per byte clocked out.

        read_extra: filler bytes appended after `data`. An MCU slave typically
            loads its TX register from the command byte's RX interrupt and has
            nothing to send during the command itself, so its answer only lands
            on the *following* clocks. With nothing extra to clock, that answer
            is never sampled and RX reads back as zeros.
        cs_mode: CS_BLOCK sends the frame as a single spi_ioc_transfer with CS
            held low throughout. CS_PER_BYTE issues one ioctl per byte, so the
            slave sees a complete transaction teardown — and delay_usec — after
            every byte; that is the usual cure when even the filler slots come
            back empty.
        delay_usec: trailing delay on each spi_ioc_transfer. Under CS_BLOCK the
            frame is one transfer, so this lands only at the end — it is an
            inter-byte gap only under CS_PER_BYTE. (Verified against py-spidev
            3.6 with strace: xfer/xfer2/xfer3 all emit a single
            SPI_IOC_MESSAGE(32), i.e. one transfer struct, regardless of length.)
        speed_hz: per-transfer clock override; 0/None keeps the device setting.
        """
        self._require_open()
        data = list(data)
        read_extra = max(0, int(read_extra))
        if not data and read_extra <= 0:
            raise SPIError("보낼 데이터가 없습니다")
        if not 0 <= filler <= 0xFF:
            raise SPIError("더미 바이트는 0~255 범위여야 합니다")
        delay_usec = max(0, min(MAX_DELAY_USEC, int(delay_usec)))

        payload = data + [filler & 0xFF] * read_extra

        if self.simulated:
            # Deterministic stand-in for MISO: bitwise complement of MOSI.
            rx = [(~b) & 0xFF for b in payload]
        else:
            speed = int(speed_hz or 0)
            try:
                if cs_mode == CS_PER_BYTE:
                    rx = []
                    for byte in payload:
                        rx.extend(self._spi.xfer2([byte], speed, delay_usec, 0))
                else:
                    rx = list(self._spi.xfer2(list(payload), speed, delay_usec, 0))
            except (OSError, IOError) as exc:
                raise SPIError(f"SPI 전송 실패: {exc}") from exc

        if not quiet:
            detail = "CS 바이트별" if cs_mode == CS_PER_BYTE else "CS 블록 유지"
            if delay_usec:
                detail += f", 지연 {delay_usec}us"
            if read_extra:
                detail += f", 더미 {read_extra}B(0x{filler:02X})"
            prefix = "[시뮬] " if self.simulated else ""
            log.tx(f"{prefix}SPI MOSI -> {fmt_bytes(payload)}  [{detail}]")
            log.rx(f"{prefix}SPI MISO <- {fmt_bytes(rx)}")
        return rx

    def loopback_test(self, pattern=(0xAA, 0x55, 0xF0, 0x0F)):
        """Send `pattern` and check it comes back; returns (ok, rx, message).

        Proves the master side end to end when MOSI (pin 19) is jumpered to
        MISO (pin 21). Refuses to run simulated, where the fabricated RX would
        make any result meaningless.
        """
        self._require_open()
        if self.simulated:
            raise SPIError("시뮬레이션 모드에서는 루프백 진단을 할 수 없습니다")

        pattern = list(pattern)
        rx = self.transfer(pattern, quiet=True)
        log.tx(f"루프백 TX -> {fmt_bytes(pattern)}")
        log.rx(f"루프백 RX <- {fmt_bytes(rx)}")

        return (rx == pattern, rx, self._classify(pattern, rx))

    @staticmethod
    def _classify(tx, rx):
        """Turn a loopback mismatch into the specific thing to go fix."""
        if rx == tx:
            return "통과 — 드라이버·핀 먹스·전이중 경로 모두 정상입니다"

        _mux, bad = pinmux.check(pinmux.SPI0_PINS)
        if bad:
            names = ", ".join(f"{label}(GPIO{bcm})는 '{actual}'"
                              for bcm, (actual, _want, label) in sorted(bad.items()))
            return (f"핀 먹스가 어긋났습니다 — {names}. "
                    "'핀 기능 확인'으로 되돌린 뒤 다시 시도하세요")

        if not any(rx):
            return ("RX가 전부 00입니다 — MISO를 구동하는 쪽이 없습니다. "
                    "점퍼(19↔21) 연결을 확인하세요")
        if all(b == 0xFF for b in rx):
            return ("RX가 전부 FF입니다 — MISO에 풀업만 보입니다. "
                    "점퍼 연결을 확인하세요")

        # A slave that fills its TX register from the command's RX interrupt
        # answers exactly one byte late; that is the case the dummy-byte
        # control exists for, so name it explicitly.
        if len(rx) > 1 and rx[1:] == tx[:-1]:
            return ("응답이 1바이트 늦습니다 — '읽기 더미'를 1 이상으로 두면 잡힙니다")

        if [int(f"{b:08b}"[::-1], 2) for b in tx] == rx:
            return "비트 순서가 반대입니다 — LSB/MSB 설정을 확인하세요"

        return ("보낸 값과 다릅니다 — 모드(CPOL/CPHA)가 어긋났거나 클럭이 너무 빠릅니다. "
                "mode 0, 125 kHz로 낮춰 다시 시도하세요")

    def write(self, data, quiet=False):
        """Write-only transfer (RX discarded)."""
        self._require_open()
        data = list(data)
        if not data:
            raise SPIError("보낼 데이터가 없습니다")
        if self.simulated:
            pass
        else:
            try:
                self._spi.writebytes2(list(data))
            except (OSError, IOError) as exc:
                raise SPIError(f"SPI 쓰기 실패: {exc}") from exc
        if not quiet:
            log.tx(f"SPI W -> {fmt_bytes(data)}")
