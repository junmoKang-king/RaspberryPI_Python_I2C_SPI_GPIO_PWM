"""GPIO + software PWM control, with a simulation fallback.

Two backends, because neither covers the whole job on Bookworm:

  * libgpiod v2 (`gpiod`) drives direction / level / bias. lgpio's v1-uAPI path
    is rejected by this kernel for BIAS_PULL_UP ("xGpioHandleRequest: Invalid
    argument"), so pull-ups only work through the v2 interface.
  * lgpio provides `tx_pwm`, which libgpiod has no equivalent for.

A pin is only ever owned by one of them — plain I/O pins by gpiod, PWM pins by
lgpio — so the two never contend for the same line.
"""

from . import pinmux
from .logbus import log
from .pinmap import label_for

try:
    import gpiod
    from gpiod.line import Bias, Direction, Value
except ImportError:  # pragma: no cover
    gpiod = None

try:
    import lgpio
except ImportError:  # pragma: no cover
    lgpio = None

IN = "IN"
OUT = "OUT"
PWM = "PWM"

PULL_NONE = "none"
PULL_UP = "up"
PULL_DOWN = "down"

CONSUMER = "i2c-spi-gpio-control"


class GPIOError(Exception):
    pass


def _bias(pull):
    return {
        PULL_UP: Bias.PULL_UP,
        PULL_DOWN: Bias.PULL_DOWN,
        PULL_NONE: Bias.DISABLED,
    }[pull]


class GPIOController:
    #: gpiochip labels that correspond to the 40-pin header.
    PREFERRED_CHIP_LABELS = ("pinctrl-bcm2711", "pinctrl-bcm2712", "pinctrl-bcm2835")

    def __init__(self):
        self._chip_num = None
        self._chip_path = None
        self._requests = {}   # bcm -> gpiod.LineRequest, for IN/OUT pins
        self._lg = None       # lgpio handle, opened lazily for PWM pins
        self.simulated = False
        #: bcm -> {"mode", "pull", "level", "freq", "duty"}
        self.pins = {}

    # ---------------------------------------------------------------- discovery

    @staticmethod
    def find_header_chip():
        """Return (chip_number, path) for the 40-pin header, or (None, None)."""
        if gpiod is None:
            return None, None
        for num in range(8):
            path = f"/dev/gpiochip{num}"
            try:
                if not gpiod.is_gpiochip_device(path):
                    continue
                with gpiod.Chip(path) as chip:
                    label = chip.get_info().label
            except (OSError, PermissionError):
                continue
            if any(label.startswith(p) for p in GPIOController.PREFERRED_CHIP_LABELS):
                return num, path
        return None, None

    # ------------------------------------------------------------- open / close

    @property
    def is_open(self):
        return self._chip_path is not None or self.simulated

    @property
    def chip(self):
        return self._chip_num

    def open(self, chip=None, simulated=False):
        self.close()
        if simulated:
            self.simulated = True
            self._chip_num = chip if chip is not None else 0
            log.info("GPIO 시뮬레이션 모드로 열었습니다")
            return

        if gpiod is None:
            raise GPIOError("gpiod(libgpiod v2) 모듈이 설치되어 있지 않습니다")

        if chip is None:
            num, path = self.find_header_chip()
            if num is None:
                raise GPIOError("40핀 헤더에 해당하는 gpiochip을 찾지 못했습니다")
        else:
            num, path = chip, f"/dev/gpiochip{chip}"
            if not gpiod.is_gpiochip_device(path):
                raise GPIOError(f"{path}은(는) gpiochip 장치가 아닙니다")

        try:
            with gpiod.Chip(path) as probe:
                label = probe.get_info().label
        except (OSError, PermissionError) as exc:
            raise GPIOError(f"{path} 열기 실패: {exc}") from exc

        self._chip_num, self._chip_path = num, path
        log.info(f"GPIO {path} 열림 ({label})")

    def close(self):
        if self._chip_path is not None:
            for bcm in list(self.pins):
                try:
                    self.free(bcm, quiet=True)
                except Exception:
                    pass
            log.info(f"GPIO gpiochip{self._chip_num} 닫힘")
        self._close_lgpio()
        self._chip_num = self._chip_path = None
        self.simulated = False
        self.pins.clear()
        self._requests.clear()

    def _require_open(self):
        if not self.is_open:
            raise GPIOError("GPIO가 열려 있지 않습니다")

    # -------------------------------------------------------------- claim / free

    def _request(self, bcm, settings):
        try:
            return gpiod.request_lines(self._chip_path, consumer=CONSUMER,
                                       config={bcm: settings})
        except (OSError, PermissionError) as exc:
            raise GPIOError(f"{label_for(bcm)} 요청 실패: {exc}") from exc

    def claim_output(self, bcm, level=0):
        self._require_open()
        self.free(bcm, quiet=True)
        level = int(bool(level))
        if not self.simulated:
            settings = gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.ACTIVE if level else Value.INACTIVE)
            self._requests[bcm] = self._request(bcm, settings)
        self.pins[bcm] = {"mode": OUT, "pull": PULL_NONE, "level": level,
                          "freq": 0, "duty": 0}
        log.info(f"{label_for(bcm)} → 출력 (초기값 {level})")

    def claim_input(self, bcm, pull=PULL_NONE):
        self._require_open()
        self.free(bcm, quiet=True)
        if not self.simulated:
            settings = gpiod.LineSettings(direction=Direction.INPUT, bias=_bias(pull))
            self._requests[bcm] = self._request(bcm, settings)
        self.pins[bcm] = {"mode": IN, "pull": pull, "level": 0, "freq": 0, "duty": 0}
        log.info(f"{label_for(bcm)} → 입력 (pull={pull})")
        if not self.simulated:
            self.read(bcm)

    def free(self, bcm, quiet=False):
        state = self.pins.pop(bcm, None)
        if state is None:
            return
        restore_alt = not self.simulated and bcm in pinmux.ALT_PINS
        if not self.simulated:
            if state["mode"] == PWM:
                self._free_pwm_pin(bcm, quiet)
            else:
                request = self._requests.pop(bcm, None)
                if request is not None:
                    try:
                        request.release()
                    except Exception as exc:
                        if not quiet:
                            raise GPIOError(f"{label_for(bcm)} 해제 실패: {exc}") from exc
        if restore_alt:
            # Releasing a line leaves it a plain GPIO; the peripheral that owned
            # the pin stays silently disconnected until its ALT mode is put back.
            want, peripheral = pinmux.ALT_PINS[bcm]
            if pinmux.set_function(bcm, want):
                log.info(f"{label_for(bcm)}를 {peripheral}({want})로 되돌렸습니다")
            else:
                log.warn(f"{label_for(bcm)}를 {peripheral}({want})로 되돌리지 "
                         f"못했습니다 — 해당 버스가 동작하지 않을 수 있습니다")
        if not quiet:
            log.info(f"{label_for(bcm)} 해제됨")

    def free_all(self):
        for bcm in list(self.pins):
            self.free(bcm, quiet=True)
        log.info("모든 GPIO 해제됨")

    # ------------------------------------------------------------- read / write

    def write(self, bcm, level, quiet=False):
        self._require_open()
        state = self.pins.get(bcm)
        if state is None or state["mode"] not in (OUT, PWM):
            raise GPIOError(f"{label_for(bcm)}은(는) 출력으로 설정되어 있지 않습니다")

        level = int(bool(level))
        if not self.simulated:
            if state["mode"] == PWM:
                # Leaving PWM: drop the lgpio claim and take the pin back on gpiod.
                self.free(bcm, quiet=True)
                self.claim_output(bcm, level)
                state = self.pins[bcm]
            else:
                request = self._requests.get(bcm)
                try:
                    request.set_value(bcm, Value.ACTIVE if level else Value.INACTIVE)
                except Exception as exc:
                    raise GPIOError(f"{label_for(bcm)} 쓰기 실패: {exc}") from exc
        state["mode"] = OUT
        state["level"] = level
        if not quiet:
            log.tx(f"{label_for(bcm)} = {level}")

    def read(self, bcm, quiet=True):
        self._require_open()
        state = self.pins.get(bcm)
        if state is None:
            raise GPIOError(f"{label_for(bcm)}은(는) 설정되어 있지 않습니다")

        if self.simulated:
            level = state["level"]
        elif state["mode"] == PWM:
            level = state["level"]
        else:
            request = self._requests.get(bcm)
            try:
                level = 1 if request.get_value(bcm) == Value.ACTIVE else 0
            except Exception as exc:
                raise GPIOError(f"{label_for(bcm)} 읽기 실패: {exc}") from exc
        state["level"] = level
        if not quiet:
            log.rx(f"{label_for(bcm)} -> {level}")
        return level

    def toggle(self, bcm):
        state = self.pins.get(bcm)
        if state is None or state["mode"] != OUT:
            raise GPIOError(f"{label_for(bcm)}은(는) 출력으로 설정되어 있지 않습니다")
        self.write(bcm, 0 if state["level"] else 1)
        return state["level"]

    # -------------------------------------------------------------------- PWM

    def _ensure_lgpio(self):
        if self._lg is not None:
            return self._lg
        if lgpio is None:
            raise GPIOError("lgpio 모듈이 설치되어 있지 않아 PWM을 사용할 수 없습니다")
        try:
            self._lg = lgpio.gpiochip_open(self._chip_num)
        except Exception as exc:
            raise GPIOError(f"PWM용 gpiochip{self._chip_num} 열기 실패: {exc}") from exc
        return self._lg

    def _close_lgpio(self):
        if self._lg is None:
            return
        try:
            lgpio.gpiochip_close(self._lg)
        except Exception:
            pass
        self._lg = None

    def _free_pwm_pin(self, bcm, quiet):
        if self._lg is None:
            return
        try:
            lgpio.tx_pwm(self._lg, bcm, 0, 0)
            lgpio.gpio_write(self._lg, bcm, 0)
            lgpio.gpio_free(self._lg, bcm)
        except Exception as exc:
            if not quiet:
                raise GPIOError(f"{label_for(bcm)} PWM 해제 실패: {exc}") from exc

    def start_pwm(self, bcm, freq_hz, duty_pct):
        """Start (or retune) PWM on a pin. lgpio drives this in software."""
        self._require_open()
        if not 0.1 <= freq_hz <= 50_000:
            raise GPIOError("PWM 주파수는 0.1Hz ~ 50kHz 범위여야 합니다")
        duty_pct = max(0.0, min(100.0, float(duty_pct)))

        state = self.pins.get(bcm)
        if not self.simulated and (state is None or state["mode"] != PWM):
            # Hand the pin over from gpiod (if it held it) to lgpio.
            self.free(bcm, quiet=True)
            handle = self._ensure_lgpio()
            try:
                lgpio.gpio_claim_output(handle, bcm, 0)
            except Exception as exc:
                raise GPIOError(f"{label_for(bcm)} PWM 출력 설정 실패: {exc}") from exc
            state = None

        if state is None:
            state = {"mode": PWM, "pull": PULL_NONE, "level": 0, "freq": 0, "duty": 0}
            self.pins[bcm] = state

        if not self.simulated:
            try:
                lgpio.tx_pwm(self._lg, bcm, float(freq_hz), duty_pct)
            except Exception as exc:
                raise GPIOError(f"{label_for(bcm)} PWM 시작 실패: {exc}") from exc

        state.update(mode=PWM, freq=float(freq_hz), duty=duty_pct)
        log.tx(f"{label_for(bcm)} PWM {freq_hz:g}Hz / duty {duty_pct:.1f}%")

    def stop_pwm(self, bcm):
        state = self.pins.get(bcm)
        if state is None or state["mode"] != PWM:
            return
        if not self.simulated:
            self._free_pwm_pin(bcm, quiet=False)
            self.pins.pop(bcm, None)
            self.claim_output(bcm, 0)
        else:
            state.update(mode=OUT, level=0, freq=0, duty=0)
        log.info(f"{label_for(bcm)} PWM 정지")
