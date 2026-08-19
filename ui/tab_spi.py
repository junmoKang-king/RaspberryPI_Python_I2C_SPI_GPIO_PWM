"""SPI tab: device open/close, mode & clock setup, full-duplex transfers."""

from PyQt5.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from core.logbus import log
from core import pinmux
from core.spi_bus import (CS_BLOCK, CS_PER_BYTE, EXPECTED_PIN_FUNCS,
                          MAX_DELAY_USEC, SPIBus, SPIError)
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

LOOPBACK_PATTERN = [0xAA, 0x55, 0xF0, 0x0F]


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

        self.speed_combo.currentIndexChanged.connect(self._speed_changed)

        self.lsb_check = QCheckBox("LSB first")
        self.lsb_check.setToolTip(
            "라즈베리파이의 spi-bcm2835 드라이버는 LSB first를 지원하지 않습니다.\n"
            "체크해도 경고만 남기고 MSB first로 동작합니다.")
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
        self.effective_label = MonoLabel("-", color=style.MUTED, size=9)
        self.effective_label.setToolTip(
            "드라이버가 실제로 적용한 설정입니다.\n"
            "라즈베리파이의 SCLK는 코어 클럭을 짝수로 나눈 값이라 요청값과 다를 수 있습니다.")

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
        grid.addWidget(self.loopback_btn(), 4, 3)
        grid.addWidget(QLabel("상태"), 5, 0)
        grid.addWidget(self.state_label, 5, 1, 1, 3)
        grid.addWidget(QLabel("실제"), 6, 0)
        grid.addWidget(self.effective_label, 6, 1, 1, 3)
        grid.setRowStretch(7, 1)
        form_stretch(grid, 1, 3)
        return box

    def loopback_btn(self):
        self.loop_btn = QPushButton("루프백 진단")
        self.loop_btn.setToolTip(
            "19번(MOSI)과 21번(MISO)을 점퍼로 직결한 뒤 실행하세요.\n"
            "보낸 값이 그대로 돌아오면 소프트웨어 경로는 정상입니다.")
        self.loop_btn.clicked.connect(self._loopback)
        return self.loop_btn

    def _build_pins(self):
        box = QGroupBox("SPI0 배선 (40핀 헤더)")
        rows = [
            ("MOSI", "GPIO10", "19번 핀", 10),
            ("MISO", "GPIO9", "21번 핀", 9),
            ("SCLK", "GPIO11", "23번 핀", 11),
            ("CE0", "GPIO8", "24번 핀", 8),
            ("CE1", "GPIO7", "26번 핀", 7),
            ("GND", "-", "20 / 25번 핀", None),
            ("3V3", "-", "1 / 17번 핀", None),
        ]
        self.pin_state_labels = {}
        grid = QGridLayout(box)
        grid.setVerticalSpacing(4)
        for row, (signal, gpio, phys, bcm) in enumerate(rows):
            grid.addWidget(MonoLabel(signal, color=style.ACCENT), row, 0)
            grid.addWidget(MonoLabel(gpio), row, 1)
            grid.addWidget(MonoLabel(phys, color=style.MUTED), row, 2)
            if bcm is not None:
                label = MonoLabel("-", color=style.MUTED, size=9)
                self.pin_state_labels[bcm] = label
                grid.addWidget(label, row, 3)

        check_btn = QPushButton("핀 기능 확인")
        check_btn.setToolTip(
            "SPI0 신호선이 ALT0로 잡혀 있는지 확인합니다.\n"
            "GPIO9가 ALT0가 아니면 파형이 보여도 MISO가 읽히지 않습니다.")
        check_btn.clicked.connect(self.refresh_pin_state)
        self.repair_btn = QPushButton("ALT0로 되돌리기")
        self.repair_btn.setToolTip("어긋난 SPI0 신호선을 ALT0로 되돌립니다.")
        self.repair_btn.clicked.connect(self._repair_pins)
        grid.addWidget(check_btn, len(rows), 0, 1, 2)
        grid.addWidget(self.repair_btn, len(rows), 2, 1, 2)
        grid.setRowStretch(len(rows) + 1, 1)
        grid.setColumnStretch(3, 1)
        self.refresh_pin_state()
        return box

    def refresh_pin_state(self):
        mux = pinmux.get(list(self.pin_state_labels))
        broken = False
        for bcm, label in self.pin_state_labels.items():
            if mux is None:
                label.setText("확인 불가")
                label.setStyleSheet(f"color: {style.MUTED};")
                continue
            actual = mux.get(bcm, "?")
            want = EXPECTED_PIN_FUNCS[bcm][0]
            ok = actual == want
            broken = broken or not ok
            label.setText(actual if ok else f"{actual} ← {want} 필요")
            label.setStyleSheet(f"color: {style.OK if ok else style.ERR};")
        self.repair_btn.setEnabled(broken)

    def _repair_pins(self):
        repaired = pinmux.restore(pinmux.SPI0_PINS)
        self.refresh_pin_state()
        if repaired:
            QMessageBox.information(
                self, "핀 기능 복구",
                "다음 핀을 ALT0로 되돌렸습니다: "
                + ", ".join(f"GPIO{bcm}" for bcm in sorted(repaired)))
        else:
            QMessageBox.warning(self, "핀 기능 복구", "되돌리지 못했습니다.")

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
        self.rx_view.setMaximumBlockCount(2000)

        # Slave-response timing controls. An MCU slave answers on the clocks
        # *after* the command, so without these the reply is never sampled.
        self.extra_spin = QSpinBox(minimum=0, maximum=256, value=0)
        self.extra_spin.setToolTip(
            "명령 뒤에 추가로 클럭할 더미 바이트 수입니다.\n"
            "MCU 슬레이브는 대개 한 바이트 늦게 응답하므로 1 이상이 필요합니다.")
        self.filler_combo = QComboBox()
        self.filler_combo.addItem("0x00", 0x00)
        self.filler_combo.addItem("0xFF", 0xFF)
        self.filler_combo.setToolTip("더미 구간에 실어 보낼 값입니다.")

        self.cs_combo = QComboBox()
        self.cs_combo.addItem("CS 블록 유지 (기본)", CS_BLOCK)
        self.cs_combo.addItem("CS 바이트마다 토글 (MCU 슬레이브 권장)", CS_PER_BYTE)
        self.cs_combo.setToolTip(
            "블록 유지: CS를 내린 채 전 바이트를 한 번에 전송합니다(기본).\n"
            "바이트마다 토글: 바이트마다 전송을 끊어 CS를 올렸다 내립니다.\n"
            "느린 슬레이브가 다음 바이트를 준비할 시간이 필요할 때 씁니다.")

        self.delay_spin = QSpinBox(minimum=0, maximum=MAX_DELAY_USEC, value=0,
                                   singleStep=10, suffix=" us")
        self.delay_spin.setToolTip(
            "각 전송 끝에 붙는 지연입니다.\n"
            "'CS 블록 유지'에서는 프레임이 하나라 맨 끝에 한 번만 적용됩니다.\n"
            "바이트 사이 간격이 필요하면 CS 모드를 '바이트마다 토글'로 바꾸세요.")

        bar = QHBoxLayout()
        bar.addWidget(QLabel("MOSI"))
        bar.addWidget(self.tx_edit, 1)
        bar.addWidget(QLabel("반복"))
        bar.addWidget(self.repeat_spin)
        bar.addWidget(self.xfer_btn)
        bar.addWidget(self.write_btn)

        timing = QHBoxLayout()
        timing.addWidget(QLabel("읽기 더미"))
        timing.addWidget(self.extra_spin)
        timing.addWidget(self.filler_combo)
        timing.addSpacing(12)
        timing.addWidget(self.cs_combo)
        timing.addSpacing(12)
        timing.addWidget(QLabel("바이트 간 지연"))
        timing.addWidget(self.delay_spin)
        timing.addStretch(1)

        lay = QVBoxLayout(box)
        lay.addLayout(bar)
        lay.addLayout(timing)
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
            self._show_effective()

    def _speed_changed(self, _index):
        """Apply a clock change to an already-open bus.

        Lowering the clock is the first thing to try against a slave that
        cannot keep up, so it must take effect without closing and reopening.
        """
        if not self.bus.is_open:
            return
        self.bus.set_speed(self.speed_combo.currentData())
        self._show_effective()

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
        self.loop_btn.setEnabled(opened and not self.bus.simulated)
        if opened:
            mode = " (시뮬레이션)" if self.bus.simulated else ""
            self.state_label.setText(f"열림: spidev{self.bus.label}{mode}")
            self.state_label.setStyleSheet(f"color: {style.OK};")
        else:
            self.state_label.setText("닫힘")
            self.state_label.setStyleSheet(f"color: {style.MUTED};")
        self._show_effective()

    def _show_effective(self):
        if not self.bus.is_open:
            self.effective_label.setText("-")
            self.effective_label.setStyleSheet(f"color: {style.MUTED};")
            return
        text = self.bus.effective_summary
        requested = self.speed_combo.currentData()
        actual = self.bus.effective.get("speed_hz")
        # Flag a driver-side clamp: the Pi rounds SCLK to core_clock / even N.
        if actual and requested and actual != requested:
            text += "  ← 요청값과 다름"
            color = style.WARN
        else:
            color = style.MUTED
        self.effective_label.setText(text)
        self.effective_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------- transactions

    def _payload(self):
        data = parse_bytes(self.tx_edit.text())
        if not data:
            raise ParseError("보낼 데이터가 비어 있습니다")
        return data

    def _transfer(self):
        extra = self.extra_spin.value()
        repeat = self.repeat_spin.value()
        # Per-byte mode is one ioctl per byte and runs on the GUI thread, so a
        # big repeat count would lock the window up with no way to stop it.
        if repeat > 100 and self.cs_combo.currentData() == CS_PER_BYTE:
            answer = QMessageBox.question(
                self, "반복 확인",
                f"'CS 바이트마다 토글'로 {repeat}회 반복하면 화면이 한동안 멈춥니다.\n"
                "계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        try:
            data = self._payload()
            for _ in range(self.repeat_spin.value()):
                rx = self.bus.transfer(data,
                                       read_extra=extra,
                                       filler=self.filler_combo.currentData(),
                                       cs_mode=self.cs_combo.currentData(),
                                       delay_usec=self.delay_spin.value())
                self.rx_view.appendPlainText(self._format_rx(data, rx, extra))
        except (ParseError, SPIError) as exc:
            log.error(str(exc))
            self.rx_view.appendPlainText(f"오류: {exc}")

    def _format_rx(self, data, rx, extra):
        """Split RX at the command boundary.

        Which byte the reply lands on is the whole diagnosis when a slave
        answers late, so the dummy window is shown separately.
        """
        prefix = "[SIM] " if self.bus.simulated else ""
        if not extra:
            return f"{prefix}TX {fmt_bytes(data)}    RX {fmt_bytes(rx)}"
        split = len(data)
        return (f"{prefix}TX {fmt_bytes(data)}    "
                f"RX {fmt_bytes(rx[:split])} | 더미 {fmt_bytes(rx[split:])}")

    def _write(self):
        try:
            data = self._payload()
            for _ in range(self.repeat_spin.value()):
                self.bus.write(data)
            self.rx_view.appendPlainText(
                f"TX {fmt_bytes(data)}    x{self.repeat_spin.value()} "
                "(쓰기 전용 — MISO는 읽지 않습니다)")
        except (ParseError, SPIError) as exc:
            log.error(str(exc))
            self.rx_view.appendPlainText(f"오류: {exc}")

    def _loopback(self):
        answer = QMessageBox.question(
            self, "루프백 진단",
            "19번 핀(MOSI)과 21번 핀(MISO)을 점퍼로 직결한 뒤 진행하세요.\n"
            f"{fmt_bytes(LOOPBACK_PATTERN)} 를 보내고 그대로 돌아오는지 확인합니다.\n\n"
            "지금 실행할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        try:
            ok, rx, message = self.bus.loopback_test(LOOPBACK_PATTERN)
        except SPIError as exc:
            log.error(str(exc))
            QMessageBox.warning(self, "루프백 진단", str(exc))
            return

        self.rx_view.appendPlainText(
            f"루프백  TX {fmt_bytes(LOOPBACK_PATTERN)}    RX {fmt_bytes(rx)}    "
            f"{'통과' if ok else '실패'}")
        detail = (f"보냄: {fmt_bytes(LOOPBACK_PATTERN)}\n"
                  f"받음: {fmt_bytes(rx)}\n\n{message}")
        if ok:
            QMessageBox.information(self, "루프백 통과", detail)
        else:
            QMessageBox.warning(self, "루프백 실패", detail)
