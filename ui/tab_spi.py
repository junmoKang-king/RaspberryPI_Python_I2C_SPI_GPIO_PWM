"""SPI tab: device open/close, mode & clock setup, full-duplex transfers."""

from PyQt5.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from core.logbus import log
from core.spi_bus import SPIBus, SPIError
from core.util import ParseError, fmt_bytes, parse_bytes

from . import style
from .widgets import MonoLabel, form_stretch

SPEEDS = [
    ("125 kHz", 125_000),
    ("500 kHz", 500_000),
    ("1 MHz", 1_000_000),
    ("2 MHz", 2_000_000),
    ("4 MHz", 4_000_000),
    ("8 MHz", 8_000_000),
    ("16 MHz", 16_000_000),
    ("32 MHz", 32_000_000),
]

def _device_key(bus, dev):
    """Combo item data for a spidev node.

    A string, deliberately: PyQt wraps non-native types (tuples, lists) in an
    opaque QVariant that QComboBox.findData compares by object identity, so a
    tuple key would never match on refresh and the selection would silently
    reset to the first device.
    """
    return f"{bus}.{dev}"


def _parse_device_key(key):
    bus, dev = key.split(".")
    return int(bus), int(dev)


MODE_HELP = {
    0: "CPOL=0, CPHA=0  (유휴 LOW, 상승 에지 샘플)",
    1: "CPOL=0, CPHA=1  (유휴 LOW, 하강 에지 샘플)",
    2: "CPOL=1, CPHA=0  (유휴 HIGH, 하강 에지 샘플)",
    3: "CPOL=1, CPHA=1  (유휴 HIGH, 상승 에지 샘플)",
}


class SPITab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.bus = ctx.spi

        root = QVBoxLayout(self)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._build_connection(), 0)
        top.addWidget(self._build_pins(), 1)
        root.addLayout(top)
        root.addWidget(self._build_transfer(), 1)

        self.refresh_devices()
        self._update_enabled()

    # ------------------------------------------------------------------ layout

    def _build_connection(self):
        box = QGroupBox("장치 연결")
        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(170)
        self.speed_combo = QComboBox()
        for label, hz in SPEEDS:
            self.speed_combo.addItem(label, hz)
        self.speed_combo.setCurrentIndex(2)
        self.mode_combo = QComboBox()
        for mode in range(4):
            self.mode_combo.addItem(f"Mode {mode}", mode)
        self.mode_combo.setToolTip(MODE_HELP[0])
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)

        self.lsb_check = QCheckBox("LSB first")
        self.sim_check = QCheckBox("시뮬레이션 모드")
        self.sim_check.setToolTip("MISO는 MOSI의 비트 반전값으로 응답합니다.")
        self.sim_check.toggled.connect(self.refresh_devices)

        self.open_btn = QPushButton("열기")
        self.close_btn = QPushButton("닫기")
        refresh_btn = QPushButton("새로 고침")
        self.open_btn.clicked.connect(self._open)
        self.close_btn.clicked.connect(self._close)
        refresh_btn.clicked.connect(self.refresh_devices)

        self.mode_help = MonoLabel(MODE_HELP[0], color=style.MUTED, size=9)
        self.state_label = MonoLabel("닫힘", color=style.MUTED)

        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("장치"), 0, 0)
        grid.addWidget(self.dev_combo, 0, 1, 1, 2)
        grid.addWidget(refresh_btn, 0, 3)
        grid.addWidget(QLabel("클럭"), 1, 0)
        grid.addWidget(self.speed_combo, 1, 1)
        grid.addWidget(QLabel("모드"), 1, 2)
        grid.addWidget(self.mode_combo, 1, 3)
        grid.addWidget(self.mode_help, 2, 1, 1, 3)
        grid.addWidget(self.lsb_check, 3, 1)
        grid.addWidget(self.sim_check, 3, 2, 1, 2)
        grid.addWidget(self.open_btn, 4, 1)
        grid.addWidget(self.close_btn, 4, 2)
        grid.addWidget(QLabel("상태"), 5, 0)
        grid.addWidget(self.state_label, 5, 1, 1, 3)
        grid.setRowStretch(6, 1)
        form_stretch(grid, 1, 3)
        return box

    def _build_pins(self):
        box = QGroupBox("SPI0 배선 (40핀 헤더)")
        rows = [
            ("MOSI", "GPIO10", "19번 핀"),
            ("MISO", "GPIO9", "21번 핀"),
            ("SCLK", "GPIO11", "23번 핀"),
            ("CE0", "GPIO8", "24번 핀"),
            ("CE1", "GPIO7", "26번 핀"),
            ("GND", "-", "20 / 25번 핀"),
            ("3V3", "-", "1 / 17번 핀"),
        ]
        grid = QGridLayout(box)
        grid.setVerticalSpacing(4)
        for row, (signal, gpio, phys) in enumerate(rows):
            grid.addWidget(MonoLabel(signal, color=style.ACCENT), row, 0)
            grid.addWidget(MonoLabel(gpio), row, 1)
            grid.addWidget(MonoLabel(phys, color=style.MUTED), row, 2)
        grid.setRowStretch(len(rows), 1)
        grid.setColumnStretch(3, 1)
        return box

    def _build_transfer(self):
        box = QGroupBox("전송")
        self.tx_edit = QLineEdit("01 80 00")
        self.tx_edit.setPlaceholderText("보낼 바이트: 01 80 00  또는  018000")
        self.tx_edit.returnPressed.connect(self._transfer)

        self.xfer_btn = QPushButton("전송 (전이중)")
        self.write_btn = QPushButton("쓰기 전용")
        self.repeat_spin = QSpinBox(minimum=1, maximum=1000, value=1)
        self.xfer_btn.clicked.connect(self._transfer)
        self.write_btn.clicked.connect(self._write)

        self.rx_view = QPlainTextEdit(readOnly=True)
        self.rx_view.setPlaceholderText("MISO 수신 데이터가 여기에 표시됩니다.")
        self.rx_view.setMaximumBlockCount(400)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("MOSI"))
        bar.addWidget(self.tx_edit, 1)
        bar.addWidget(QLabel("반복"))
        bar.addWidget(self.repeat_spin)
        bar.addWidget(self.xfer_btn)
        bar.addWidget(self.write_btn)

        lay = QVBoxLayout(box)
        lay.addLayout(bar)
        lay.addWidget(self.rx_view, 1)
        return box

    # ------------------------------------------------------------------ actions

    def _mode_changed(self, _index):
        mode = self.mode_combo.currentData()
        self.mode_help.setText(MODE_HELP[mode])
        self.mode_combo.setToolTip(MODE_HELP[mode])
        if self.bus.is_open:
            self.bus.set_mode(mode)
            log.info(f"SPI 모드를 {mode}(으)로 변경했습니다")

    def refresh_devices(self):
        current = self.dev_combo.currentData()
        self.dev_combo.clear()
        devices = SPIBus.available_devices()
        for bus, dev in devices:
            self.dev_combo.addItem(f"/dev/spidev{bus}.{dev}", _device_key(bus, dev))
        if not devices:
            self.dev_combo.addItem("사용 가능한 장치 없음", None)
        if self.sim_check.isChecked() and (0, 0) not in devices:
            self.dev_combo.addItem("spidev0.0 (가상)", _device_key(0, 0))
        index = self.dev_combo.findData(current)
        if index >= 0:
            self.dev_combo.setCurrentIndex(index)

    def _open(self):
        key = self.dev_combo.currentData()
        if key is None:
            QMessageBox.information(
                self, "장치 없음",
                "/dev/spidev*.* 노드가 없습니다.\n"
                "raspi-config 또는 config.txt에서 SPI를 켠 뒤 재부팅하거나,\n"
                "시뮬레이션 모드를 사용하세요.")
            return
        bus, dev = _parse_device_key(key)
        try:
            self.bus.open(bus, dev,
                          speed_hz=self.speed_combo.currentData(),
                          mode=self.mode_combo.currentData(),
                          lsb_first=self.lsb_check.isChecked(),
                          simulated=self.sim_check.isChecked())
        except SPIError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "SPI 열기 실패", str(exc))
            return
        self._update_enabled()
        self.ctx.changed.emit()

    def _close(self):
        self.bus.close()
        self._update_enabled()
        self.ctx.changed.emit()

    def _update_enabled(self):
        opened = self.bus.is_open
        self.open_btn.setEnabled(not opened)
        self.close_btn.setEnabled(opened)
        self.dev_combo.setEnabled(not opened)
        self.sim_check.setEnabled(not opened)
        self.lsb_check.setEnabled(not opened)
        self.xfer_btn.setEnabled(opened)
        self.write_btn.setEnabled(opened)
        if opened:
            mode = " (시뮬레이션)" if self.bus.simulated else ""
            self.state_label.setText(f"열림: spidev{self.bus.label}{mode}")
            self.state_label.setStyleSheet(f"color: {style.OK};")
        else:
            self.state_label.setText("닫힘")
            self.state_label.setStyleSheet(f"color: {style.MUTED};")

    # ------------------------------------------------------------- transactions

    def _payload(self):
        data = parse_bytes(self.tx_edit.text())
        if not data:
            raise ParseError("보낼 데이터가 비어 있습니다")
        return data

    def _transfer(self):
        try:
            data = self._payload()
            for _ in range(self.repeat_spin.value()):
                rx = self.bus.transfer(data)
                self.rx_view.appendPlainText(
                    f"TX {fmt_bytes(data)}    RX {fmt_bytes(rx)}")
        except (ParseError, SPIError) as exc:
            log.error(str(exc))
            self.rx_view.appendPlainText(f"오류: {exc}")

    def _write(self):
        try:
            data = self._payload()
            for _ in range(self.repeat_spin.value()):
                self.bus.write(data)
            self.rx_view.appendPlainText(
                f"TX {fmt_bytes(data)}    x{self.repeat_spin.value()} (쓰기 전용)")
        except (ParseError, SPIError) as exc:
            log.error(str(exc))
            self.rx_view.appendPlainText(f"오류: {exc}")
