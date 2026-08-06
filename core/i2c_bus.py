"""I2C master wrapper around smbus2, with a simulation fallback.

The simulator answers from the virtual slaves in `sim_devices`, so the GUI stays
fully exercisable on a board where /dev/i2c-1 does not exist yet (interface
disabled, or no hardware wired up).
"""

import glob

from .logbus import log
from .sim_devices import default_devices
from .util import fmt_bytes

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:  # pragma: no cover - smbus2 is a hard requirement at runtime
    SMBus = None
    i2c_msg = None


class I2CError(Exception):
    pass


#: Addresses the simulator pretends to see on the bus.
SIM_DEVICES = {
    0x62: "MCP4725 (DAC, simulated)",
    0x48: "ADS1115 (ADC, simulated)",
    0x68: "DS3231 (RTC, simulated)",
}

# HDMI DDC buses on the Pi carry monitor EDID, not user peripherals. Scanning
# them is harmless but never what the user means, so they are flagged in the UI.
DDC_BUSES = (20, 21)


class I2CBus:
    def __init__(self):
        self._bus = None
        self._num = None
        self.simulated = False
        self._sim = default_devices()

    # ---------------------------------------------------------------- discovery

    @staticmethod
    def available_buses():
        """Bus numbers backed by an actual /dev/i2c-N node."""
        nums = []
        for path in glob.glob("/dev/i2c-*"):
            try:
                nums.append(int(path.rsplit("-", 1)[1]))
            except ValueError:
                continue
        return sorted(nums)

    @staticmethod
    def bus_speed_hz(num):
        """Configured SCL clock for /dev/i2c-N from the device tree, or None.

        Set with `dtparam=i2c_arm_baudrate=` in config.txt; it cannot be changed
        at runtime.
        """
        path = f"/sys/bus/i2c/devices/i2c-{num}/of_node/clock-frequency"
        try:
            with open(path, "rb") as handle:
                raw = handle.read(4)
        except OSError:
            return None
        return int.from_bytes(raw, "big") if len(raw) == 4 else None

    # ------------------------------------------------------------- open / close

    @property
    def is_open(self):
        return self._bus is not None or self.simulated

    @property
    def bus_number(self):
        return self._num

    def open(self, num, simulated=False):
        self.close()
        if simulated:
            self.simulated = True
            self._num = num
            log.info(f"I2C-{num} 시뮬레이션 모드로 열었습니다")
            return

        if SMBus is None:
            raise I2CError("smbus2 모듈이 설치되어 있지 않습니다")
        try:
            self._bus = SMBus(num)
        except (OSError, IOError) as exc:
            raise I2CError(f"/dev/i2c-{num} 열기 실패: {exc}") from exc
        self._num = num
        log.info(f"I2C-{num} 열림")

    def close(self):
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            log.info(f"I2C-{self._num} 닫힘")
        self._bus = None
        self.simulated = False
        self._num = None

    def _require_open(self):
        if not self.is_open:
            raise I2CError("I2C 버스가 열려 있지 않습니다")

    # ------------------------------------------------------------------- scan

    def scan(self, start=0x03, end=0x77):
        """Probe the address range and return the addresses that responded.

        Mirrors i2cdetect's heuristic: 0x30-0x37 and 0x50-0x5F are probed with a
        read (a quick-write can corrupt EEPROMs there), everything else with a
        zero-length write.
        """
        self._require_open()
        if self.simulated:
            return sorted(a for a in self._sim if start <= a <= end)

        found = []
        for addr in range(start, end + 1):
            try:
                if 0x30 <= addr <= 0x37 or 0x50 <= addr <= 0x5F:
                    self._bus.read_byte(addr)
                else:
                    self._bus.write_quick(addr)
            except OSError:
                continue
            found.append(addr)
        return found

    # ------------------------------------------------------------ raw transfers

    def write_bytes(self, addr, data, quiet=False):
        """Raw write: START, addr+W, data..., STOP."""
        self._require_open()
        data = list(data)
        if self.simulated:
            self._sim_write(addr, data)
        else:
            try:
                self._bus.i2c_rdwr(i2c_msg.write(addr, data))
            except OSError as exc:
                raise I2CError(f"0x{addr:02X} 쓰기 실패: {exc}") from exc
        if not quiet:
            log.tx(f"I2C W 0x{addr:02X} <- {fmt_bytes(data)}")

    def read_bytes(self, addr, length):
        """Raw read: START, addr+R, length bytes, STOP."""
        self._require_open()
        if length <= 0:
            raise I2CError("읽을 바이트 수는 1 이상이어야 합니다")
        if self.simulated:
            data = self._sim_read(addr, None, length)
        else:
            try:
                msg = i2c_msg.read(addr, length)
                self._bus.i2c_rdwr(msg)
                data = list(msg)
            except OSError as exc:
                raise I2CError(f"0x{addr:02X} 읽기 실패: {exc}") from exc
        log.rx(f"I2C R 0x{addr:02X} -> {fmt_bytes(data)}")
        return data

    # -------------------------------------------------------- register accesses

    def write_reg(self, addr, reg, data):
        """Write `data` starting at register `reg`."""
        self._require_open()
        data = list(data)
        if self.simulated:
            self._sim_write(addr, [reg] + data)
        else:
            try:
                self._bus.i2c_rdwr(i2c_msg.write(addr, [reg] + data))
            except OSError as exc:
                raise I2CError(f"0x{addr:02X} reg 0x{reg:02X} 쓰기 실패: {exc}") from exc
        log.tx(f"I2C W 0x{addr:02X}[0x{reg:02X}] <- {fmt_bytes(data)}")

    def read_reg(self, addr, reg, length):
        """Write the register pointer, then read `length` bytes (repeated START)."""
        self._require_open()
        if length <= 0:
            raise I2CError("읽을 바이트 수는 1 이상이어야 합니다")
        if self.simulated:
            data = self._sim_read(addr, reg, length)
        else:
            try:
                write = i2c_msg.write(addr, [reg])
                read = i2c_msg.read(addr, length)
                self._bus.i2c_rdwr(write, read)
                data = list(read)
            except OSError as exc:
                raise I2CError(f"0x{addr:02X} reg 0x{reg:02X} 읽기 실패: {exc}") from exc
        log.rx(f"I2C R 0x{addr:02X}[0x{reg:02X}] -> {fmt_bytes(data)}")
        return data

    # -------------------------------------------------------------- simulation

    def _sim_device(self, addr):
        device = self._sim.get(addr)
        if device is None:
            raise I2CError(f"시뮬레이션 버스에 0x{addr:02X} 장치가 없습니다 (NACK)")
        return device

    def _sim_write(self, addr, payload):
        if payload:
            self._sim_device(addr).write(list(payload))

    def _sim_read(self, addr, reg, length):
        """`reg is None` means a plain read; otherwise set the pointer first."""
        device = self._sim_device(addr)
        if reg is not None:
            device.write([reg])
        return device.read(length)
