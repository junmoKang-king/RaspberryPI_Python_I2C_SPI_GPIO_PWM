"""Virtual I2C slaves used by the simulation mode.

Each model exposes the two operations a real slave sees on the wire:
`write(data)` for a START+W transaction and `read(length)` for START+R. A
register-pointer write is therefore just `write([reg])` followed by `read(n)`,
exactly as the driver layer issues it.
"""


class SimRegisterFile:
    """Generic slave with a 256-byte auto-incrementing register file."""

    def __init__(self, name, initial=None):
        self.name = name
        self.regs = bytearray(256)
        self._ptr = 0
        for reg, value in (initial or {}).items():
            self.regs[reg & 0xFF] = value & 0xFF

    def write(self, data):
        if not data:
            return
        self._ptr = data[0] & 0xFF
        for offset, value in enumerate(data[1:]):
            self.regs[(self._ptr + offset) & 0xFF] = value & 0xFF

    def read(self, length):
        return [self.regs[(self._ptr + i) & 0xFF] for i in range(length)]


class SimMCP4725:
    """Virtual MCP4725: 12-bit DAC register plus its EEPROM shadow."""

    name = "MCP4725 (DAC)"

    def __init__(self):
        self.dac = 0
        self.eeprom = 0
        self.powerdown = 0

    def write(self, data):
        if len(data) == 2:
            # Fast mode: [PD1 PD0 D11..D8], [D7..D0]
            self.powerdown = (data[0] >> 4) & 0b11
            self.dac = ((data[0] & 0x0F) << 8) | data[1]
        elif len(data) >= 3:
            # Command mode: [C2 C1 C0 x x PD1 PD0 x], [D11..D4], [D3..D0 x x x x]
            command = data[0]
            self.powerdown = (command >> 1) & 0b11
            self.dac = (data[1] << 4) | (data[2] >> 4)
            if command & 0xE0 == 0x60:  # write DAC *and* EEPROM
                self.eeprom = self.dac

    def read(self, length):
        # Byte 0: RDY | POR | x x x | PD1 PD0 | x
        status = 0xC0 | ((self.powerdown & 0b11) << 1)
        frame = [
            status,
            (self.dac >> 4) & 0xFF,
            (self.dac & 0x0F) << 4,
            ((self.powerdown & 0b11) << 5) | ((self.eeprom >> 8) & 0x0F),
            self.eeprom & 0xFF,
        ]
        return (frame + [0] * length)[:length]


def default_devices():
    """Address -> virtual slave, as seen by a simulated bus scan."""
    return {
        0x62: SimMCP4725(),
        # ADS1115: conversion + config registers with their reset values.
        0x48: SimRegisterFile("ADS1115 (ADC)", {0x00: 0x7F, 0x01: 0x85, 0x02: 0x83}),
        # DS3231: 00:00:00, 2026-01-01.
        0x68: SimRegisterFile("DS3231 (RTC)", {0x04: 0x01, 0x05: 0x01, 0x06: 0x26}),
    }
