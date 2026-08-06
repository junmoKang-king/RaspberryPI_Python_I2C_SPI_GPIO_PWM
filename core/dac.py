"""12-bit DAC drivers reachable over either bus.

  * MCP4725  - I2C,  single channel, optional EEPROM store
  * MCP4921  - SPI,  single channel
  * MCP4922  - SPI,  dual channel

The Pi has no on-chip DAC, so these external parts play the role the STM32's
internal DAC peripheral would.
"""

from .logbus import log

FULL_SCALE = 4095


class DACError(Exception):
    pass


def clamp(value):
    return max(0, min(FULL_SCALE, int(value)))


def counts_to_volts(counts, vref):
    return clamp(counts) * vref / (FULL_SCALE + 1)


def volts_to_counts(volts, vref):
    if vref <= 0:
        return 0
    return clamp(round(volts * (FULL_SCALE + 1) / vref))


class MCP4725:
    """I2C 12-bit DAC. Default address 0x62 (0x63 with A0 tied high)."""

    NAME = "MCP4725 (I2C)"
    DEFAULT_ADDR = 0x62

    # Power-down resistor selection (bits PD1:PD0 of the command byte).
    PD_NORMAL = 0b00
    PD_1K = 0b01
    PD_100K = 0b10
    PD_500K = 0b11

    def __init__(self, bus, addr=DEFAULT_ADDR):
        self.bus = bus
        self.addr = addr

    def write(self, counts, powerdown=PD_NORMAL, quiet=False):
        """Fast-mode write: 2 bytes, value takes effect immediately."""
        counts = clamp(counts)
        hi = ((powerdown & 0b11) << 4) | ((counts >> 8) & 0x0F)
        lo = counts & 0xFF
        self.bus.write_bytes(self.addr, [hi, lo], quiet=True)
        if not quiet:
            log.info(f"MCP4725 0x{self.addr:02X} <- {counts} counts")

    def write_eeprom(self, counts, powerdown=PD_NORMAL):
        """DAC + EEPROM write (0x60 command): value survives power cycles."""
        counts = clamp(counts)
        cmd = 0x60 | ((powerdown & 0b11) << 1)
        self.bus.write_bytes(self.addr, [cmd, (counts >> 4) & 0xFF, (counts & 0x0F) << 4])
        log.info(f"MCP4725 0x{self.addr:02X} EEPROM <- {counts} counts")

    def read(self):
        """Return (counts, powerdown, eeprom_counts) from the 5-byte status read."""
        data = self.bus.read_bytes(self.addr, 5)
        if len(data) < 5:
            raise DACError("MCP4725 상태 읽기 응답이 짧습니다")
        counts = ((data[1] << 8) | data[2]) >> 4
        powerdown = (data[0] >> 1) & 0b11
        eeprom = (((data[3] & 0x0F) << 8) | data[4])
        return counts, powerdown, eeprom


class MCP49x1:
    """SPI 12-bit DAC (MCP4921 single / MCP4922 dual).

    Command word is 16 bits, MSB first:
        b15 A/B  0 = channel A, 1 = channel B
        b14 BUF  1 = buffered Vref input
        b13 /GA  1 = gain 1x, 0 = gain 2x
        b12 /SHDN 1 = output active
        b11..b0  12-bit value
    """

    NAME = "MCP4921/4922 (SPI)"

    def __init__(self, bus, buffered=False, gain_1x=True):
        self.bus = bus
        self.buffered = buffered
        self.gain_1x = gain_1x

    def _word(self, counts, channel, active=True):
        counts = clamp(counts)
        config = 0
        if channel:
            config |= 1 << 3
        if self.buffered:
            config |= 1 << 2
        if self.gain_1x:
            config |= 1 << 1
        if active:
            config |= 1 << 0
        hi = (config << 4) | ((counts >> 8) & 0x0F)
        return [hi, counts & 0xFF]

    def write(self, counts, channel=0, quiet=False):
        self.bus.write(self._word(counts, channel), quiet=True)
        if not quiet:
            log.info(f"MCP49x1 CH{'B' if channel else 'A'} <- {clamp(counts)} counts")

    def shutdown(self, channel=0):
        self.bus.write(self._word(0, channel, active=False), quiet=True)
        log.info(f"MCP49x1 CH{'B' if channel else 'A'} 출력 shutdown")
