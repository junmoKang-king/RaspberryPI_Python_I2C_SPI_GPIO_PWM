"""Pin-function (ALT mode) inspection and repair.

Bus peripherals reach the header through an alternate pin function: SPI0 needs
GPIO 9/10/11 in ALT0, I2C1 needs GPIO 2/3 in ALT0. libgpiod and lgpio can claim
those pins away from the controller, and **releasing them does not restore the
alternate function** — the line stays a plain GPIO. The bus then goes quiet in a
way that looks like a wiring fault: the other signals still toggle, so a scope on
the header shows healthy waveforms while the peripheral reads nothing.

`pinctrl` is used rather than a sysfs write because it is the only interface that
sets an ALT function, and on Raspberry Pi OS it works without root.
"""

import re
import subprocess

from .logbus import log

#: bcm -> (expected function, human label)
SPI0_PINS = {
    9: ("a0", "SPI0 MISO"),
    10: ("a0", "SPI0 MOSI"),
    11: ("a0", "SPI0 SCLK"),
}
I2C1_PINS = {
    2: ("a0", "I2C1 SDA"),
    3: ("a0", "I2C1 SCL"),
}

#: CE0/CE1 are driven as plain outputs because the Pi device tree wires the SPI
#: chip selects through `cs-gpios`, not the controller's native ALT0 lines.
SPI0_CS_PINS = {8: ("op", "SPI0 CE0"), 7: ("op", "SPI0 CE1")}

ALT_PINS = {**SPI0_PINS, **I2C1_PINS}


def _run(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out.returncode == 0 else None


def available():
    return _run(["pinctrl", "help"]) is not None or _run(["pinctrl", "get", "9"]) is not None


def get(pins):
    """Current function of each pin as {bcm: func}, or None if unreadable."""
    pins = sorted(pins)
    if not pins:
        return {}
    out = _run(["pinctrl", "get", f"{pins[0]}-{pins[-1]}"])
    if out is None:
        return None
    found = {}
    for line in out.stdout.splitlines():
        match = re.match(r"\s*(\d+):\s*(\S+)", line)
        if match:
            found[int(match.group(1))] = match.group(2)
    return {bcm: found[bcm] for bcm in pins if bcm in found} or None


def set_function(bcm, func):
    """Force one pin to a function; returns True on success."""
    return _run(["pinctrl", "set", str(bcm), func]) is not None


def check(expected):
    """Compare live functions against `expected` ({bcm: (func, label)}).

    Returns (mux, mismatched) where `mismatched` is {bcm: (actual, want, label)}.
    """
    mux = get(expected)
    if mux is None:
        return None, {}
    bad = {}
    for bcm, (want, label) in expected.items():
        actual = mux.get(bcm)
        if actual != want:
            bad[bcm] = (actual, want, label)
    return mux, bad


def restore(expected, quiet=False):
    """Put any pin that drifted off `expected` back. Returns list of repaired bcm."""
    _mux, bad = check(expected)
    repaired = []
    for bcm, (actual, want, label) in bad.items():
        if set_function(bcm, want):
            repaired.append(bcm)
            if not quiet:
                log.warn(f"{label}(GPIO{bcm})가 '{actual}' 상태여서 "
                         f"'{want}'로 되돌렸습니다")
        elif not quiet:
            log.error(f"{label}(GPIO{bcm})를 '{want}'로 되돌리지 못했습니다 "
                      f"(현재 '{actual}')")
    return repaired
