"""PWM tab — the Raspberry Pi counterpart to an STM32 timer output channel.

lgpio generates these in software, so the pin choice is free but jitter grows
with frequency; the hardware-PWM-capable pins are flagged in the selector.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QSlider, QVBoxLayout, QWidget)

from core import pinmap
from core.gpio_ctrl import GPIOError
from core.logbus import log

from . import style
from .widgets import MonoLabel, form_stretch

PRESETS = [
    ("일반 (1 kHz)", 1000.0, 50.0),
    ("LED 디밍 (200 Hz)", 200.0, 30.0),
    ("서보 (50 Hz, 중립)", 50.0, 7.5),
    ("부저 (2 kHz)", 2000.0, 50.0),
]


class PWMChannel(QGroupBox):
    def __init__(self, ctx, title, default_bcm, parent=None):
        super().__init__(title, parent)
        self.ctx = ctx
        self.gpio = ctx.gpio
        self._bcm = None

        self.pin_combo = QComboBox()
        for bcm in pinmap.BCM_PINS:
            hw = pinmap.HW_PWM.get(bcm)
            note = pinmap.RESERVED.get(bcm)
            text = pinmap.label_for(bcm)
            if hw:
                text += f"  [{hw}]"
            if note:
                text += f"  ⚠ {note}"
            self.pin_combo.addItem(text, bcm)
        index = self.pin_combo.findData(default_bcm)
        if index >= 0:
            self.pin_combo.setCurrentIndex(index)

        self.freq_spin = QDoubleSpinBox(minimum=0.1, maximum=50000.0, value=1000.0,
                                        decimals=1, singleStep=10.0, suffix=" Hz")
        self.duty_spin = QDoubleSpinBox(minimum=0.0, maximum=100.0, value=50.0,
                                        decimals=1, singleStep=1.0, suffix=" %")
        self.duty_slider = QSlider(Qt.Horizontal, minimum=0, maximum=1000, value=500)

        self.duty_slider.valueChanged.connect(
            lambda v: self.duty_spin.setValue(v / 10.0))
        self.duty_spin.valueChanged.connect(
            lambda v: self.duty_slider.setValue(int(round(v * 10))))
        self.duty_spin.valueChanged.connect(self._live_update)
        self.freq_spin.valueChanged.connect(self._live_update)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("프리셋…", None)
        for label, freq, duty in PRESETS:
            self.preset_combo.addItem(label, (freq, duty))
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)

        self.start_btn = QPushButton("시작")
        self.stop_btn = QPushButton("정지")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)

        self.info = MonoLabel("정지됨", color=style.MUTED)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("핀"), 0, 0)
        grid.addWidget(self.pin_combo, 0, 1, 1, 3)
        grid.addWidget(QLabel("주파수"), 1, 0)
        grid.addWidget(self.freq_spin, 1, 1)
        grid.addWidget(QLabel("듀티"), 1, 2)
        grid.addWidget(self.duty_spin, 1, 3)
        grid.addWidget(self.duty_slider, 2, 0, 1, 4)
        grid.addWidget(self.preset_combo, 3, 0, 1, 2)
        grid.addWidget(self.start_btn, 3, 2)
        grid.addWidget(self.stop_btn, 3, 3)
        grid.addWidget(self.info, 4, 0, 1, 4)
        form_stretch(grid, 1, 3)

    @property
    def running(self):
        return self._bcm is not None

    def _apply_preset(self, _index):
        data = self.preset_combo.currentData()
        if data is None:
            return
        freq, duty = data
        self.freq_spin.setValue(freq)
        self.duty_spin.setValue(duty)

    def _live_update(self):
        if self.running:
            self.start()

    def start(self):
        if not self.gpio.is_open:
            QMessageBox.information(self, "GPIO 미연결",
                                    "GPIO 탭에서 먼저 GPIO를 열어 주세요.")
            return
        bcm = self.pin_combo.currentData()
        if self._bcm is not None and self._bcm != bcm:
            self._stop_pin(self._bcm)

        note = pinmap.RESERVED.get(bcm)
        if note and self._bcm != bcm:
            answer = QMessageBox.question(
                self, "예약된 핀",
                f"{pinmap.label_for(bcm)}은(는) {note} 용도입니다.\n계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        freq = self.freq_spin.value()
        duty = self.duty_spin.value()
        try:
            self.gpio.start_pwm(bcm, freq, duty)
        except GPIOError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "PWM 시작 실패", str(exc))
            return

        self._bcm = bcm
        self.pin_combo.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        period_ms = 1000.0 / freq
        self.info.setText(f"출력 중 — 주기 {period_ms:.3f} ms, "
                          f"HIGH {period_ms * duty / 100:.3f} ms")
        self.info.setStyleSheet(f"color: {style.OK};")
        self.ctx.changed.emit()

    def _stop_pin(self, bcm):
        try:
            self.gpio.stop_pwm(bcm)
            self.gpio.free(bcm, quiet=True)
        except GPIOError as exc:
            log.error(str(exc))

    def stop(self):
        if self._bcm is None:
            return
        self._stop_pin(self._bcm)
        self._bcm = None
        self.pin_combo.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.info.setText("정지됨")
        self.info.setStyleSheet(f"color: {style.MUTED};")
        self.ctx.changed.emit()


class PWMTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.channels = [
            PWMChannel(ctx, "채널 1", 12),
            PWMChannel(ctx, "채널 2", 13),
        ]

        row = QHBoxLayout()
        row.setSpacing(10)
        for channel in self.channels:
            row.addWidget(channel)

        note = QGroupBox("참고")
        note_lay = QVBoxLayout(note)
        for line in (
            "· lgpio의 PWM은 소프트웨어 방식이라 수 kHz를 넘기면 지터가 커집니다.",
            f"· 하드웨어 PWM 가능 핀: {', '.join(f'GPIO{p} ({n})' for p, n in sorted(pinmap.HW_PWM.items()))}",
            "· 서보는 50Hz에서 듀티 5%(≈1ms) ~ 10%(≈2ms) 범위를 사용합니다.",
            "· 정지하면 핀은 LOW로 내려간 뒤 해제됩니다.",
        ):
            label = QLabel(line)
            label.setStyleSheet(f"color: {style.MUTED};")
            note_lay.addWidget(label)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addLayout(row)
        root.addWidget(note)
        root.addStretch(1)

    def stop_all(self):
        for channel in self.channels:
            channel.stop()
