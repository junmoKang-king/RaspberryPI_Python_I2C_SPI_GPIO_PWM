"""SPI master wrapper around spidev, with a simulation fallback.

The simulator echoes back a deterministic pattern so full-duplex transfers still
produce plausible RX data with no /dev/spidev node present.
"""

import glob
import os

from .logbus import log
from .util import fmt_bytes

try:
    import spidev
except ImportError:  # pragma: no cover
    spidev = None


class SPIError(Exception):
    pass


class SPIBus:
    def __init__(self):
        self._spi = None
        self._bus = None
        self._dev = None
        self.simulated = False
        self._settings = {}

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
            self._spi.lsbfirst = bool(lsb_first)
        except (OSError, IOError) as exc:
            self._spi = None
            raise SPIError(f"/dev/spidev{bus}.{dev} 열기 실패: {exc}") from exc
        log.info(f"SPI{bus}.{dev} 열림 (mode {mode}, {speed_hz / 1e6:.3f} MHz, "
                 f"{bits}bit, {'LSB' if lsb_first else 'MSB'} first)")

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

    def _require_open(self):
        if not self.is_open:
            raise SPIError("SPI 버스가 열려 있지 않습니다")

    # -------------------------------------------------------------- reconfigure

    def set_speed(self, speed_hz):
        self._settings["speed_hz"] = int(speed_hz)
        if self._spi is not None:
            self._spi.max_speed_hz = int(speed_hz)

    def set_mode(self, mode):
        self._settings["mode"] = int(mode)
        if self._spi is not None:
            self._spi.mode = int(mode)

    # --------------------------------------------------------------- transfers

    def transfer(self, data, quiet=False):
        """Full-duplex transfer; returns the same number of bytes read back."""
        self._require_open()
        data = list(data)
        if not data:
            raise SPIError("보낼 데이터가 없습니다")

        if self.simulated:
            # Deterministic stand-in for MISO: bitwise complement of MOSI.
            rx = [(~b) & 0xFF for b in data]
        else:
            try:
                rx = list(self._spi.xfer2(list(data)))
            except (OSError, IOError) as exc:
                raise SPIError(f"SPI 전송 실패: {exc}") from exc

        if not quiet:
            log.tx(f"SPI MOSI -> {fmt_bytes(data)}")
            log.rx(f"SPI MISO <- {fmt_bytes(rx)}")
        return rx

    def write(self, data, quiet=False):
        """Write-only transfer (RX discarded)."""
        self._require_open()
        data = list(data)
        if self.simulated:
            pass
        else:
            try:
                self._spi.writebytes2(list(data))
            except (OSError, IOError) as exc:
                raise SPIError(f"SPI 쓰기 실패: {exc}") from exc
        if not quiet:
            log.tx(f"SPI W -> {fmt_bytes(data)}")
