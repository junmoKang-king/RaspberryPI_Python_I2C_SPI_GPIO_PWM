"""DAC tab: manual output plus timer-driven waveform generation.

Targets an external 12-bit DAC on either bus — MCP4725 over I2C or MCP4921/4922
over SPI — since the Pi has no on-chip DAC peripheral.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                             QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QSlider, QSpinBox, QVBoxLayout,
                             QWidget)

from core import waveform
from core.dac import (FULL_SCALE, MCP49x1, MCP4725, DACError, counts_to_volts,
                      volts_to_counts)
from core.i2c_bus import I2CError
from core.logbus import log
from core.spi_bus import SPIError

from . import style
from .widgets import MonoLabel, WaveformPreview, form_stretch

I2C_DEVICE = "MCP4725 (I2C)"
SPI_DEVICE = "MCP4921 / MCP4922 (SPI)"

BUS_ERRORS = (DACError, I2CError, SPIError, OSError)


class DACTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.worker = None

        root = QHBoxLayout(self)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self._build_device())
        left.addWidget(self._build_manual())
        left.addStretch(1)
        root.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._build_waveform())
        self.preview = WaveformPreview()
        right.addWidget(self.preview, 1)
        root.addLayout(right, 1)

        self._device_changed()
        self._sync_preview()

    # ------------------------------------------------------------------ layout

    def _build_device(self):
        box = QGroupBox("DAC 장치")
        self.device_combo = QComboBox()
        self.device_combo.addItem(I2C_DEVICE, I2C_DEVICE)
        self.device_combo.addItem(SPI_DEVICE, SPI_DEVICE)
        self.device_combo.currentIndexChanged.connect(self._device_changed)

        self.addr_spin = QSpinBox(minimum=0x00, maximum=0x7F, value=0x62,
                                  displayIntegerBase=16, prefix="0x")
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("채널 A", 0)
        self.channel_combo.addItem("채널 B (MCP4922)", 1)
        self.gain_combo = QComboBox()
        self.gain_combo.addItem("이득 1x", True)
        self.gain_combo.addItem("이득 2x", False)
        self.buffered_check = QCheckBox("Vref 버퍼 사용")

        self.vref_spin = QDoubleSpinBox(minimum=0.5, maximum=5.5, value=3.3,
                                        decimals=3, singleStep=0.1, suffix=" V")
        self.vref_spin.valueChanged.connect(self._vref_changed)

        self.addr_label = QLabel("I2C 주소")
        self.channel_label = QLabel("채널")
        self.gain_label = QLabel("이득")

        grid = QGridLayout(box)
        grid.addWidget(QLabel("종류"), 0, 0)
        grid.addWidget(self.device_combo, 0, 1, 1, 2)
        grid.addWidget(self.addr_label, 1, 0)
        grid.addWidget(self.addr_spin, 1, 1)
        grid.addWidget(self.channel_label, 2, 0)
        grid.addWidget(self.channel_combo, 2, 1, 1, 2)
        grid.addWidget(self.gain_label, 3, 0)
        grid.addWidget(self.gain_combo, 3, 1)
        grid.addWidget(self.buffered_check, 3, 2)
        grid.addWidget(QLabel("Vref"), 4, 0)
        grid.addWidget(self.vref_spin, 4, 1)
        form_stretch(grid, 1, 2)
        return box

    def _build_manual(self):
        box = QGroupBox("수동 출력")
        self.counts_spin = QSpinBox(minimum=0, maximum=FULL_SCALE, value=2048)
        self.volts_spin = QDoubleSpinBox(minimum=0.0, maximum=5.5, value=1.65,
                                         decimals=4, singleStep=0.05, suffix=" V")
        self.counts_slider = QSlider(Qt.Horizontal, minimum=0, maximum=FULL_SCALE,
                                     value=2048)

        self.counts_spin.valueChanged.connect(self._counts_changed)
        self.counts_slider.valueChanged.connect(self.counts_spin.setValue)
        self.volts_spin.valueChanged.connect(self._volts_changed)

        self.auto_check = QCheckBox("값 변경 시 즉시 출력", checked=True)
        self.apply_btn = QPushButton("출력")
        self.zero_btn = QPushButton("0V")
        self.mid_btn = QPushButton("½ Vref")
        self.full_btn = QPushButton("최대")
        self.eeprom_btn = QPushButton("EEPROM 저장")
        self.read_btn = QPushButton("현재값 읽기")

        self.apply_btn.clicked.connect(lambda: self._write_current(force=True))
        self.zero_btn.clicked.connect(lambda: self.counts_spin.setValue(0))
        self.mid_btn.clicked.connect(lambda: self.counts_spin.setValue((FULL_SCALE + 1) // 2))
        self.full_btn.clicked.connect(lambda: self.counts_spin.setValue(FULL_SCALE))
        self.eeprom_btn.clicked.connect(self._write_eeprom)
        self.read_btn.clicked.connect(self._read_back)

        self.manual_info = MonoLabel("-", color=style.MUTED)
        self.manual_info.setWordWrap(True)

        grid = QGridLayout(box)
        grid.addWidget(QLabel("코드"), 0, 0)
        grid.addWidget(self.counts_spin, 0, 1)
        grid.addWidget(QLabel("전압"), 0, 2)
        grid.addWidget(self.volts_spin, 0, 3)
        grid.addWidget(self.counts_slider, 1, 0, 1, 4)
        grid.addWidget(self.zero_btn, 2, 0)
        grid.addWidget(self.mid_btn, 2, 1)
        grid.addWidget(self.full_btn, 2, 2)
        grid.addWidget(self.apply_btn, 2, 3)
        grid.addWidget(self.auto_check, 3, 0, 1, 2)
        grid.addWidget(self.read_btn, 3, 2)
        grid.addWidget(self.eeprom_btn, 3, 3)
        grid.addWidget(self.manual_info, 4, 0, 1, 4)
        form_stretch(grid, 1, 3)
        return box

    def _build_waveform(self):
        box = QGroupBox("파형 생성 (타이머 구동)")
        self.shape_combo = QComboBox()
        for shape in waveform.SHAPES:
            self.shape_combo.addItem(shape, shape)

        self.freq_spin = QDoubleSpinBox(minimum=0.1, maximum=500.0, value=10.0,
                                        decimals=2, singleStep=1.0, suffix=" Hz")
        self.amp_spin = QSpinBox(minimum=0, maximum=FULL_SCALE, value=4000,
                                 suffix=" counts p-p")
        self.offset_spin = QSpinBox(minimum=0, maximum=FULL_SCALE, value=2048,
                                    suffix=" counts")
        self.rate_spin = QSpinBox(minimum=100, maximum=50_000, value=5000,
                                  singleStep=500, suffix=" S/s")
        self.rate_spin.setToolTip("샘플 레이트 상한. 버스 속도가 못 따라가면 낮추세요.")

        for widget in (self.shape_combo, self.freq_spin, self.amp_spin,
                       self.offset_spin):
            signal = (widget.currentIndexChanged if widget is self.shape_combo
                      else widget.valueChanged)
            signal.connect(self._sync_preview)

        self.start_btn = QPushButton("시작")
        self.stop_btn = QPushButton("정지")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_wave)
        self.stop_btn.clicked.connect(self._stop_wave)

        self.wave_info = MonoLabel("정지됨", color=style.MUTED)

        grid = QGridLayout(box)
        grid.addWidget(QLabel("파형"), 0, 0)
        grid.addWidget(self.shape_combo, 0, 1)
        grid.addWidget(QLabel("주파수"), 0, 2)
        grid.addWidget(self.freq_spin, 0, 3)
        grid.addWidget(QLabel("진폭"), 1, 0)
        grid.addWidget(self.amp_spin, 1, 1)
        grid.addWidget(QLabel("오프셋"), 1, 2)
        grid.addWidget(self.offset_spin, 1, 3)
        grid.addWidget(QLabel("최대 레이트"), 2, 0)
        grid.addWidget(self.rate_spin, 2, 1)
        grid.addWidget(self.start_btn, 2, 2)
        grid.addWidget(self.stop_btn, 2, 3)
        grid.addWidget(self.wave_info, 3, 0, 1, 4)
        form_stretch(grid, 1, 3)
        return box

    # ------------------------------------------------------------------- device

    def _device_changed(self):
        is_i2c = self.device_combo.currentData() == I2C_DEVICE
        for widget in (self.addr_label, self.addr_spin):
            widget.setVisible(is_i2c)
        for widget in (self.channel_label, self.channel_combo, self.gain_label,
                       self.gain_combo, self.buffered_check):
            widget.setVisible(not is_i2c)
        self.eeprom_btn.setEnabled(is_i2c)
        self.read_btn.setEnabled(is_i2c)

    def _make_dac(self):
        """Build a driver bound to the currently open bus, or raise DACError."""
        if self.device_combo.currentData() == I2C_DEVICE:
            if not self.ctx.i2c.is_open:
                raise DACError("I2C 탭에서 먼저 버스를 열어 주세요")
            return MCP4725(self.ctx.i2c, self.addr_spin.value())
        if not self.ctx.spi.is_open:
            raise DACError("SPI 탭에서 먼저 장치를 열어 주세요")
        return MCP49x1(self.ctx.spi,
                       buffered=self.buffered_check.isChecked(),
                       gain_1x=self.gain_combo.currentData())

    def _write_counts(self, counts, quiet=False):
        dac = self._make_dac()
        if isinstance(dac, MCP4725):
            dac.write(counts, quiet=quiet)
        else:
            dac.write(counts, channel=self.channel_combo.currentData(), quiet=quiet)

    # ------------------------------------------------------------------- manual

    def _vref_changed(self, vref):
        self.volts_spin.blockSignals(True)
        self.volts_spin.setMaximum(vref)
        self.volts_spin.setValue(counts_to_volts(self.counts_spin.value(), vref))
        self.volts_spin.blockSignals(False)
        self._sync_preview()

    def _counts_changed(self, counts):
        self.counts_slider.blockSignals(True)
        self.counts_slider.setValue(counts)
        self.counts_slider.blockSignals(False)

        self.volts_spin.blockSignals(True)
        self.volts_spin.setValue(counts_to_volts(counts, self.vref_spin.value()))
        self.volts_spin.blockSignals(False)

        if self.auto_check.isChecked():
            self._write_current(quiet=True)

    def _volts_changed(self, volts):
        counts = volts_to_counts(volts, self.vref_spin.value())
        if counts != self.counts_spin.value():
            self.counts_spin.setValue(counts)

    def _write_current(self, force=False, quiet=False):
        if self.worker is not None and self.worker.isRunning():
            if force:
                QMessageBox.information(self, "파형 출력 중",
                                        "파형을 정지한 뒤 수동으로 출력하세요.")
            return
        counts = self.counts_spin.value()
        try:
            self._write_counts(counts, quiet=quiet)
        except BUS_ERRORS as exc:
            self._manual_fail(exc, force)
            return
        volts = counts_to_volts(counts, self.vref_spin.value())
        self.manual_info.setText(f"출력: {counts} counts ≈ {volts:.4f} V")
        self.manual_info.setStyleSheet(f"color: {style.OK};")

    def _manual_fail(self, exc, loud):
        log.error(str(exc))
        self.manual_info.setText(str(exc))
        self.manual_info.setStyleSheet(f"color: {style.ERR};")
        if loud:
            QMessageBox.warning(self, "DAC 출력 실패", str(exc))

    def _write_eeprom(self):
        try:
            dac = self._make_dac()
            if not isinstance(dac, MCP4725):
                raise DACError("EEPROM 저장은 MCP4725에서만 지원됩니다")
            dac.write_eeprom(self.counts_spin.value())
        except BUS_ERRORS as exc:
            self._manual_fail(exc, True)
            return
        self.manual_info.setText("EEPROM에 저장했습니다 (전원 재인가 후에도 유지)")
        self.manual_info.setStyleSheet(f"color: {style.OK};")

    def _read_back(self):
        try:
            dac = self._make_dac()
            if not isinstance(dac, MCP4725):
                raise DACError("현재값 읽기는 MCP4725에서만 지원됩니다")
            counts, powerdown, eeprom = dac.read()
        except BUS_ERRORS as exc:
            self._manual_fail(exc, True)
            return
        volts = counts_to_volts(counts, self.vref_spin.value())
        self.manual_info.setText(
            f"DAC {counts} counts ({volts:.4f} V) / EEPROM {eeprom} / PD {powerdown:02b}")
        self.manual_info.setStyleSheet(f"color: {style.RX};")

    # ----------------------------------------------------------------- waveform

    def _sync_preview(self):
        self.preview.set_params(self.shape_combo.currentData(),
                                self.amp_spin.value(),
                                self.offset_spin.value(),
                                self.vref_spin.value())

    def _start_wave(self):
        try:
            dac = self._make_dac()
        except DACError as exc:
            log.error(str(exc))
            QMessageBox.information(self, "버스 미연결", str(exc))
            return

        if isinstance(dac, MCP4725):
            def write(counts):
                dac.write(counts, quiet=True)
        else:
            channel = self.channel_combo.currentData()

            def write(counts):
                dac.write(counts, channel=channel, quiet=True)

        self.worker = waveform.WaveformWorker(self)
        rate = self.worker.configure(write,
                                     self.shape_combo.currentData(),
                                     self.freq_spin.value(),
                                     self.amp_spin.value(),
                                     self.offset_spin.value(),
                                     max_rate_hz=self.rate_spin.value())
        self.worker.tick.connect(self._on_tick)
        self.worker.failed.connect(self._on_wave_failed)
        self.worker.finished_run.connect(self._on_wave_finished)
        self.worker.start()

        self._set_wave_running(True)
        self.wave_info.setText(f"출력 중 — 목표 {rate:,.0f} S/s")
        self.wave_info.setStyleSheet(f"color: {style.OK};")
        log.info(f"파형 출력 시작: {self.shape_combo.currentData()} "
                 f"{self.freq_spin.value():g}Hz, 목표 {rate:,.0f} S/s")

    def _stop_wave(self):
        if self.worker is None:
            return
        self.worker.stop()
        self.worker.wait(2000)

    def _on_tick(self, counts, rate):
        self.preview.set_cursor(counts)
        self.wave_info.setText(
            f"출력 중 — 실제 {rate:,.0f} S/s, 현재 {counts} counts "
            f"({counts_to_volts(counts, self.vref_spin.value()):.3f} V)")

    def _on_wave_failed(self, message):
        log.error(f"파형 출력 오류: {message}")
        self.wave_info.setText(f"오류: {message}")
        self.wave_info.setStyleSheet(f"color: {style.ERR};")

    def _on_wave_finished(self):
        self._set_wave_running(False)
        self.preview.set_cursor(None)
        if self.wave_info.text().startswith("출력 중"):
            self.wave_info.setText("정지됨")
            self.wave_info.setStyleSheet(f"color: {style.MUTED};")
        log.info("파형 출력 정지")
        self.worker = None

    def _set_wave_running(self, running):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        for widget in (self.device_combo, self.shape_combo, self.freq_spin,
                       self.amp_spin, self.offset_spin, self.rate_spin,
                       self.counts_slider, self.counts_spin, self.volts_spin,
                       self.apply_btn, self.zero_btn, self.mid_btn, self.full_btn,
                       self.eeprom_btn, self.read_btn):
            widget.setEnabled(not running)
        if not running:
            # MCP4725-only buttons must not come back enabled for the SPI part.
            self._device_changed()

    def shutdown(self):
        self._stop_wave()
