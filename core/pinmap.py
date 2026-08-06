"""Raspberry Pi 40-pin header map (matches `pin map/pinmaip.png`)."""

POWER = "PWR"
GROUND = "GND"
GPIO = "GPIO"

# physical pin -> (kind, bcm number or None, label, alternate function)
HEADER = {
    1:  (POWER,  None, "3V3",  ""),
    2:  (POWER,  None, "5V",   ""),
    3:  (GPIO,   2,    "GPIO2",  "SDA1"),
    4:  (POWER,  None, "5V",   ""),
    5:  (GPIO,   3,    "GPIO3",  "SCL1"),
    6:  (GROUND, None, "GND",  ""),
    7:  (GPIO,   4,    "GPIO4",  "GPCLK0"),
    8:  (GPIO,   14,   "GPIO14", "TXD"),
    9:  (GROUND, None, "GND",  ""),
    10: (GPIO,   15,   "GPIO15", "RXD"),
    11: (GPIO,   17,   "GPIO17", ""),
    12: (GPIO,   18,   "GPIO18", "PCM_CLK"),
    13: (GPIO,   27,   "GPIO27", ""),
    14: (GROUND, None, "GND",  ""),
    15: (GPIO,   22,   "GPIO22", ""),
    16: (GPIO,   23,   "GPIO23", ""),
    17: (POWER,  None, "3V3",  ""),
    18: (GPIO,   24,   "GPIO24", ""),
    19: (GPIO,   10,   "GPIO10", "MOSI"),
    20: (GROUND, None, "GND",  ""),
    21: (GPIO,   9,    "GPIO9",  "MISO"),
    22: (GPIO,   25,   "GPIO25", ""),
    23: (GPIO,   11,   "GPIO11", "SCLK"),
    24: (GPIO,   8,    "GPIO8",  "CE0"),
    25: (GROUND, None, "GND",  ""),
    26: (GPIO,   7,    "GPIO7",  "CE1"),
    27: (GPIO,   0,    "GPIO0",  "ID_SD"),
    28: (GPIO,   1,    "GPIO1",  "ID_SC"),
    29: (GPIO,   5,    "GPIO5",  ""),
    30: (GROUND, None, "GND",  ""),
    31: (GPIO,   6,    "GPIO6",  ""),
    32: (GPIO,   12,   "GPIO12", "PWM0"),
    33: (GPIO,   13,   "GPIO13", "PWM1"),
    34: (GROUND, None, "GND",  ""),
    35: (GPIO,   19,   "GPIO19", "PCM_FS"),
    36: (GPIO,   16,   "GPIO16", ""),
    37: (GPIO,   26,   "GPIO26", ""),
    38: (GPIO,   20,   "GPIO20", "PCM_DIN"),
    39: (GROUND, None, "GND",  ""),
    40: (GPIO,   21,   "GPIO21", "PCM_DOUT"),
}

#: BCM numbers exposed on the header, in ascending order.
BCM_PINS = sorted(bcm for kind, bcm, _, _ in HEADER.values() if kind == GPIO)

#: BCM -> physical pin
BCM_TO_PHYS = {bcm: phys for phys, (kind, bcm, _, _) in HEADER.items() if kind == GPIO}

#: Pins that belong to a bus peripheral; the UI warns before driving them.
RESERVED = {
    2:  "I2C1 SDA",
    3:  "I2C1 SCL",
    7:  "SPI0 CE1",
    8:  "SPI0 CE0",
    9:  "SPI0 MISO",
    10: "SPI0 MOSI",
    11: "SPI0 SCLK",
    14: "UART TXD",
    15: "UART RXD",
    0:  "HAT EEPROM ID_SD",
    1:  "HAT EEPROM ID_SC",
}

#: Pins with a hardware PWM channel on the BCM2711.
HW_PWM = {12: "PWM0", 13: "PWM1", 18: "PWM0", 19: "PWM1"}


def label_for(bcm):
    """Human label for a BCM pin, e.g. "GPIO18 (PCM_CLK) / pin 12"."""
    phys = BCM_TO_PHYS.get(bcm)
    if phys is None:
        return f"GPIO{bcm}"
    _, _, name, alt = HEADER[phys]
    return f"{name} ({alt}) / {phys}번 핀" if alt else f"{name} / {phys}번 핀"
