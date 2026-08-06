"""Command sequence engine.

A sequence is a list of steps; each step names a mode (an I2C / SPI / GPIO /
PWM / DAC operation), a comma-separated argument string, a read length, a
post-step delay and its own repeat count. The whole list can also be looped.

Steps run on a worker thread so per-step delays never block the UI, and every
step reports its result back through signals.
"""

import json
import time
from dataclasses import asdict, dataclass

from PyQt5.QtCore import QThread, pyqtSignal

from .dac import MCP49x1, MCP4725, DACError, clamp
from .gpio_ctrl import PULL_DOWN, PULL_NONE, PULL_UP, GPIOError
from .i2c_bus import I2CError
from .spi_bus import SPIError
from .util import ParseError, fmt_bytes, parse_bytes, parse_int


class SequenceError(Exception):
    pass


#: Errors that already carry a user-facing message; anything else gets its
#: exception type prefixed so unexpected failures stay identifiable.
EXPECTED_ERRORS = (SequenceError, ParseError, I2CError, SPIError, GPIOError, DACError)


def _need(args, count, form):
    if len(args) < count:
        raise SequenceError(f"인자가 부족합니다 — 형식: {form}")


def _rest(args, start):
    """Re-join trailing argument fields so `40, 00` and `40 00` both work."""
    return ",".join(args[start:])


# --------------------------------------------------------------------- runners

def _i2c_write(ctx, args, length):
    _need(args, 2, "주소, 데이터")
    data = parse_bytes(_rest(args, 1))
    if not data:
        raise SequenceError("보낼 데이터가 없습니다")
    ctx.i2c.write_bytes(parse_int(args[0]), data)
    return f"{len(data)}B 전송"


def _i2c_read(ctx, args, length):
    _need(args, 1, "주소")
    return fmt_bytes(ctx.i2c.read_bytes(parse_int(args[0]), length))


def _i2c_reg_write(ctx, args, length):
    _need(args, 3, "주소, 레지스터, 데이터")
    data = parse_bytes(_rest(args, 2))
    if not data:
        raise SequenceError("보낼 데이터가 없습니다")
    ctx.i2c.write_reg(parse_int(args[0]), parse_int(args[1]), data)
    return f"{len(data)}B 전송"


def _i2c_reg_read(ctx, args, length):
    _need(args, 2, "주소, 레지스터")
    return fmt_bytes(ctx.i2c.read_reg(parse_int(args[0]), parse_int(args[1]), length))


def _i2c_scan(ctx, args, length):
    found = ctx.i2c.scan()
    return " ".join(f"0x{a:02X}" for a in found) if found else "응답 없음"


def _spi_transfer(ctx, args, length):
    data = parse_bytes(_rest(args, 0))
    if not data:
        raise SequenceError("보낼 데이터가 없습니다")
    return fmt_bytes(ctx.spi.transfer(data))


def _spi_write(ctx, args, length):
    data = parse_bytes(_rest(args, 0))
    if not data:
        raise SequenceError("보낼 데이터가 없습니다")
    ctx.spi.write(data)
    return f"{len(data)}B 전송"


_PULLS = {"up": PULL_UP, "pu": PULL_UP, "down": PULL_DOWN, "pd": PULL_DOWN,
          "none": PULL_NONE, "": PULL_NONE}


def _gpio_mode(ctx, args, length):
    _need(args, 2, "핀, out|in[, up|down|none]")
    pin = parse_int(args[0])
    direction = args[1].strip().lower()
    if direction.startswith("o"):
        ctx.gpio.claim_output(pin, 0)
        return "출력"
    if not direction.startswith("i"):
        raise SequenceError(f"방향은 out 또는 in 이어야 합니다: {args[1]!r}")
    pull_text = args[2].strip().lower() if len(args) > 2 else ""
    if pull_text not in _PULLS:
        raise SequenceError(f"풀 저항은 up/down/none 중 하나입니다: {args[2]!r}")
    pull = _PULLS[pull_text]
    ctx.gpio.claim_input(pin, pull)
    return f"입력({pull})"


def _gpio_write(ctx, args, length):
    _need(args, 2, "핀, 0|1")
    pin = parse_int(args[0])
    level = 1 if parse_int(args[1]) else 0
    # Claim on first use so a bare "GPIO Write" step works without a setup step.
    if pin not in ctx.gpio.pins:
        ctx.gpio.claim_output(pin, level)
    else:
        ctx.gpio.write(pin, level)
    return str(level)


def _gpio_read(ctx, args, length):
    _need(args, 1, "핀")
    pin = parse_int(args[0])
    if pin not in ctx.gpio.pins:
        ctx.gpio.claim_input(pin, PULL_NONE)
    return str(ctx.gpio.read(pin))


def _gpio_toggle(ctx, args, length):
    _need(args, 1, "핀")
    pin = parse_int(args[0])
    if pin not in ctx.gpio.pins:
        ctx.gpio.claim_output(pin, 0)
    return str(ctx.gpio.toggle(pin))


def _pwm_start(ctx, args, length):
    _need(args, 3, "핀, 주파수(Hz), 듀티(%)")
    pin = parse_int(args[0])
    try:
        freq, duty = float(args[1]), float(args[2])
    except ValueError:
        raise SequenceError("주파수와 듀티는 숫자여야 합니다")
    ctx.gpio.start_pwm(pin, freq, duty)
    return f"{freq:g}Hz {duty:g}%"


def _pwm_stop(ctx, args, length):
    _need(args, 1, "핀")
    ctx.gpio.stop_pwm(parse_int(args[0]))
    return "정지"


def _dac_i2c_write(ctx, args, length):
    _need(args, 2, "주소, 값(0~4095)")
    addr, counts = parse_int(args[0]), parse_int(args[1])
    MCP4725(ctx.i2c, addr).write(counts, quiet=True)
    return f"{clamp(counts)} counts"


def _dac_i2c_read(ctx, args, length):
    _need(args, 1, "주소")
    counts, powerdown, eeprom = MCP4725(ctx.i2c, parse_int(args[0])).read()
    return f"DAC {counts} / EEPROM {eeprom} / PD {powerdown:02b}"


def _dac_spi_write(ctx, args, length):
    _need(args, 2, "채널(0=A,1=B), 값(0~4095)")
    channel, counts = parse_int(args[0]), parse_int(args[1])
    MCP49x1(ctx.spi).write(counts, channel=1 if channel else 0, quiet=True)
    return f"CH{'B' if channel else 'A'} {clamp(counts)} counts"


def _delay(ctx, args, length):
    return "대기"


# ----------------------------------------------------------------------- modes

@dataclass(frozen=True)
class ModeSpec:
    key: str
    hint: str          # argument format, shown as the cell placeholder
    needs_length: bool
    run: object

    @property
    def group(self):
        return self.key.split()[0]


MODES = (
    ModeSpec("I2C Write",     "0x62, 40 00",        False, _i2c_write),
    ModeSpec("I2C Read",      "0x62",               True,  _i2c_read),
    ModeSpec("I2C Reg Write", "0x48, 0x01, 84 83",  False, _i2c_reg_write),
    ModeSpec("I2C Reg Read",  "0x48, 0x00",         True,  _i2c_reg_read),
    ModeSpec("I2C Scan",      "(인자 없음)",         False, _i2c_scan),
    ModeSpec("SPI Transfer",  "30 80",              False, _spi_transfer),
    ModeSpec("SPI Write",     "30 80",              False, _spi_write),
    ModeSpec("GPIO Mode",     "17, out  /  27, in, up", False, _gpio_mode),
    ModeSpec("GPIO Write",    "17, 1",              False, _gpio_write),
    ModeSpec("GPIO Read",     "27",                 False, _gpio_read),
    ModeSpec("GPIO Toggle",   "17",                 False, _gpio_toggle),
    ModeSpec("PWM Start",     "12, 1000, 50",       False, _pwm_start),
    ModeSpec("PWM Stop",      "12",                 False, _pwm_stop),
    ModeSpec("DAC Write I2C", "0x62, 2048",         False, _dac_i2c_write),
    ModeSpec("DAC Read I2C",  "0x62",               False, _dac_i2c_read),
    ModeSpec("DAC Write SPI", "0, 2048",            False, _dac_spi_write),
    ModeSpec("Delay",         "(지연 열만 사용)",     False, _delay),
)

MODE_MAP = {spec.key: spec for spec in MODES}
DEFAULT_MODE = MODES[0].key


# ------------------------------------------------------------------------ step

@dataclass
class SequenceStep:
    description: str = ""
    mode: str = DEFAULT_MODE
    command: str = ""
    read_length: int = 1
    delay_ms: int = 10
    repeat: int = 1
    enabled: bool = True
    result: str = ""

    def args(self):
        fields = [part.strip() for part in self.command.split(",")]
        while fields and not fields[-1]:
            fields.pop()
        return fields

    def to_dict(self):
        data = asdict(self)
        data.pop("result")
        return data

    @classmethod
    def from_dict(cls, data):
        step = cls()
        for key in ("description", "mode", "command"):
            if key in data:
                setattr(step, key, str(data[key]))
        for key in ("read_length", "delay_ms", "repeat"):
            if key in data:
                try:
                    setattr(step, key, max(0, int(data[key])))
                except (TypeError, ValueError):
                    pass
        step.enabled = bool(data.get("enabled", True))
        if step.mode not in MODE_MAP:
            raise SequenceError(f"알 수 없는 모드: {step.mode!r}")
        step.repeat = max(1, step.repeat)
        step.read_length = max(1, step.read_length)
        return step


def save_steps(path, steps):
    payload = {"version": 1, "steps": [step.to_dict() for step in steps]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_steps(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        raw = payload.get("steps", [])
    elif isinstance(payload, list):
        raw = payload
    else:
        raise SequenceError("시퀀스 파일 형식이 올바르지 않습니다")
    return [SequenceStep.from_dict(item) for item in raw]


# ---------------------------------------------------------------------- runner

class SequenceRunner(QThread):
    """Executes selected steps of a sequence on a worker thread."""

    step_started = pyqtSignal(int, int)        # index, repeat iteration (1-based)
    step_result = pyqtSignal(int, bool, str)   # index, ok, result text
    output = pyqtSignal(str)
    finished_run = pyqtSignal(bool)            # True if it ran to completion

    #: delay is slept in slices this long so Stop stays responsive
    SLICE_S = 0.02

    def __init__(self, ctx, steps, indices, cycles=1, stop_on_error=True, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.steps = steps
        self.indices = list(indices)
        self.cycles = max(1, int(cycles))
        self.stop_on_error = stop_on_error
        self._running = False

    def stop(self):
        self._running = False

    def _sleep(self, ms):
        remaining = ms / 1000.0
        while remaining > 0 and self._running:
            slice_s = min(self.SLICE_S, remaining)
            time.sleep(slice_s)
            remaining -= slice_s

    def run(self):
        self._running = True
        completed = True

        for cycle in range(self.cycles):
            if not self._running:
                completed = False
                break
            if self.cycles > 1:
                self.output.emit(f"───── 사이클 {cycle + 1}/{self.cycles} ─────")

            for index in self.indices:
                if not self._running:
                    completed = False
                    break
                step = self.steps[index]
                if not step.enabled:
                    continue

                spec = MODE_MAP.get(step.mode)
                if spec is None:
                    self.step_result.emit(index, False, f"알 수 없는 모드: {step.mode}")
                    self.output.emit(f"{index + 1:>3}  ✗ 알 수 없는 모드: {step.mode}")
                    if self.stop_on_error:
                        completed = False
                        self._running = False
                    break

                for iteration in range(max(1, step.repeat)):
                    if not self._running:
                        completed = False
                        break
                    self.step_started.emit(index, iteration + 1)
                    try:
                        result = spec.run(self.ctx, step.args(), step.read_length)
                        ok = True
                    except EXPECTED_ERRORS as exc:
                        result, ok = str(exc), False
                    except Exception as exc:
                        result, ok = f"{type(exc).__name__}: {exc}", False

                    self.step_result.emit(index, ok, result)
                    tag = f"{index + 1:>3}"
                    if step.repeat > 1:
                        tag += f".{iteration + 1}"
                    label = step.description or step.mode
                    self.output.emit(
                        f"{tag}  {'✓' if ok else '✗'} {label}  [{step.mode}] "
                        f"{step.command}".rstrip() + f"  →  {result}")

                    if not ok and self.stop_on_error:
                        self.output.emit("오류가 발생하여 시퀀스를 중단합니다.")
                        completed = False
                        self._running = False
                        break

                    self._sleep(step.delay_ms)

        self._running = False
        self.finished_run.emit(completed)
