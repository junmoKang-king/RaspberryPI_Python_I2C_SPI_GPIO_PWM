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

#: Pad default pull per pin. Restored along with the function: a pull left over
#: from GPIO use survives a mode change, and an idle bus then reads back FF
#: instead of 00, which misdirects the loopback diagnosis.
DEFAULT_PULLS = {9: "pd", 10: "pd", 11: "pd", 2: "pu", 3: "pu"}

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


def set_function(bcm, func, pull=None):
    """Force one pin to a function (and its default pull); True on success."""
    args = ["pinctrl", "set", str(bcm), func]
    pull = pull or DEFAULT_PULLS.get(bcm)
    if pull:
        args.append(pull)
    return _run(args) is not None


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


def continuity(driver_bcm, sense_bcm, chip_path="/dev/gpiochip0"):
    """Is `driver_bcm` physically wired to `sense_bcm`? (True / False / None)

    Drives one pin and reads the other with the *opposite* internal bias, twice
    with the levels swapped. A push-pull output beats the ~50k pull, so a joined
    pair follows the driver both times while an open pair follows its own pull
    both times. That distinguishes a missing jumper from a bus misconfiguration,
    which otherwise look identical: both read back all-zero.

    Steals both pins from whatever owns them, so the caller must restore the
    pin functions afterwards.
    """
    try:
        import gpiod
        from gpiod.line import Bias, Direction, Value
    except ImportError:
        return None

    import time
    seen = {}
    try:
        for drive in (1, 0):
            out = gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.ACTIVE if drive else Value.INACTIVE)
            sense = gpiod.LineSettings(
                direction=Direction.INPUT,
                bias=Bias.PULL_DOWN if drive else Bias.PULL_UP)
            with gpiod.request_lines(chip_path, consumer="continuity",
                                     config={driver_bcm: out}):
                with gpiod.request_lines(chip_path, consumer="continuity",
                                         config={sense_bcm: sense}) as reader:
                    time.sleep(0.02)
                    seen[drive] = 1 if reader.get_value(sense_bcm) == Value.ACTIVE else 0
    except (OSError, PermissionError):
        return None
    if seen == {1: 1, 0: 0}:
        return True
    if seen == {1: 0, 0: 1}:
        return False
    return None


def normalize(expected, quiet=True):
    """Force function *and* default pull on every pin, whatever they read now.

    `restore` only touches pins whose function drifted; after a continuity probe
    the function may be back but the bias left inverted, so the caller that
    borrowed the pins normalizes unconditionally.
    """
    for bcm, (want, label) in expected.items():
        if not set_function(bcm, want) and not quiet:
            log.error(f"{label}(GPIO{bcm})를 '{want}'로 되돌리지 못했습니다")
