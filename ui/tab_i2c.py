"""I2C tab: bus open/close, i2cdetect-style scan, register and raw transfers."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                             QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from core.i2c_bus import DDC_BUSES, SIM_DEVICES, I2CBus, I2CError
from core.logbus import log
from core.util import ParseError, fmt_bytes, parse_bytes, parse_int

from . import style
from .widgets import MonoLabel, fit_table_height, form_stretch

SCAN_START, SCAN_END = 0x03, 0x77


class I2CTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.bus = ctx.i2c

        root = QVBoxLayout(self)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._build_connection(), 0)
        top.addWidget(self._build_scan(), 1)
        root.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addWidget(self._build_register(), 1)
        bottom.addWidget(self._build_raw(), 1)
        root.addLayout(bottom)
        root.addStretch(1)

        self.refresh_buses()
        self._update_enabled()

    # ------------------------------------------------------------------ layout

    def _build_connection(self):
        box = QGroupBox("버스 연결")
        self.bus_combo = QComboBox()
        self.bus_combo.setMinimumWidth(150)
        self.sim_check = QCheckBox("시뮬레이션 모드")
        self.sim_check.setToolTip(
            "장치 노드 없이 GUI를 검증합니다.\n"
            f"가상 장치: {', '.join(f'0x{a:02X}' for a in SIM_DEVICES)}")
        self.open_btn = QPushButton("열기")
        self.close_btn = QPushButton("닫기")
        refresh_btn = QPushButton("새로 고침")

        self.open_btn.clicked.connect(self._open)
        self.close_btn.clicked.connect(self._close)
        refresh_btn.clicked.connect(self.refresh_buses)
        self.sim_check.toggled.connect(self.refresh_buses)

        self.state_label = MonoLabel("닫힘", color=style.MUTED)

        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("버스"), 0, 0)
        grid.addWidget(self.bus_combo, 0, 1, 1, 2)
        grid.addWidget(refresh_btn, 0, 3)
        grid.addWidget(self.sim_check, 1, 1, 1, 3)
        grid.addWidget(self.open_btn, 2, 1)
        grid.addWidget(self.close_btn, 2, 2)
        grid.addWidget(QLabel("상태"), 3, 0)
        grid.addWidget(self.state_label, 3, 1, 1, 3)
        grid.setRowStretch(4, 1)
        form_stretch(grid, 1, 2)
        return box

    def _build_scan(self):
        box = QGroupBox("주소 스캔 (i2cdetect)")
        self.scan_btn = QPushButton("스캔 (0x03 ~ 0x77)")
        self.scan_btn.clicked.connect(self._scan)
        self.scan_result = MonoLabel("-", color=style.MUTED)

        self.table = QTableWidget(8, 16)
        self.table.setHorizontalHeaderLabels([f"{i:X}" for i in range(16)])
        self.table.setVerticalHeaderLabels([f"{r * 16:02X}" for r in range(8)])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        fit_table_height(self.table, 8, 26)
        self.table.cellClicked.connect(self._cell_clicked)
        self._clear_table()

        bar = QHBoxLayout()
        bar.addWidget(self.scan_btn)
        bar.addWidget(self.scan_result, 1)

        lay = QVBoxLayout(box)
        lay.addLayout(bar)
        lay.addWidget(self.table)
        lay.addWidget(QLabel("<span style='color:%s'>셀을 클릭하면 아래 주소 칸에 "
                             "채워집니다.</span>" % style.MUTED))
        return box

    def _build_register(self):
        box = QGroupBox("레지스터 접근")
        self.reg_addr = QLineEdit("0x62")
        self.reg_num = QLineEdit("0x00")
        self.reg_len = QSpinBox(minimum=1, maximum=32, value=1)
        self.reg_data = QLineEdit("00")
        self.reg_data.setPlaceholderText("예: 0F 8A  또는  0F8A")
        self.reg_out = MonoLabel("-", color=style.RX)
        self.reg_out.setWordWrap(True)

        self.reg_read_btn = QPushButton("읽기")
        self.reg_write_btn = QPushButton("쓰기")
        self.reg_read_btn.clicked.connect(self._reg_read)
        self.reg_write_btn.clicked.connect(self._reg_write)

        grid = QGridLayout(box)
        grid.addWidget(QLabel("슬레이브 주소"), 0, 0)
        grid.addWidget(self.reg_addr, 0, 1)
        grid.addWidget(QLabel("레지스터"), 0, 2)
        grid.addWidget(self.reg_num, 0, 3)
        grid.addWidget(QLabel("읽을 길이"), 1, 0)
        grid.addWidget(self.reg_len, 1, 1)
        grid.addWidget(self.reg_read_btn, 1, 2, 1, 2)
        grid.addWidget(QLabel("쓸 데이터"), 2, 0)
        grid.addWidget(self.reg_data, 2, 1)
        grid.addWidget(self.reg_write_btn, 2, 2, 1, 2)
        grid.addWidget(QLabel("결과"), 3, 0)
        grid.addWidget(self.reg_out, 3, 1, 1, 3)
        grid.setRowStretch(4, 1)
        form_stretch(grid, 1, 3)
        return box

    def _build_raw(self):
        box = QGroupBox("Raw 전송 (레지스터 포인터 없음)")
        self.raw_addr = QLineEdit("0x62")
        self.raw_data = QLineEdit("40 00")
        self.raw_data.setPlaceholderText("예: 40 00")
        self.raw_len = QSpinBox(minimum=1, maximum=32, value=2)
        self.raw_out = MonoLabel("-", color=style.RX)
        self.raw_out.setWordWrap(True)

        self.raw_write_btn = QPushButton("쓰기")
        self.raw_read_btn = QPushButton("읽기")
        self.raw_write_btn.clicked.connect(self._raw_write)
        self.raw_read_btn.clicked.connect(self._raw_read)

        grid = QGridLayout(box)
        grid.addWidget(QLabel("슬레이브 주소"), 0, 0)
        grid.addWidget(self.raw_addr, 0, 1, 1, 3)
        grid.addWidget(QLabel("보낼 데이터"), 1, 0)
        grid.addWidget(self.raw_data, 1, 1, 1, 2)
        grid.addWidget(self.raw_write_btn, 1, 3)
        grid.addWidget(QLabel("읽을 길이"), 2, 0)
        grid.addWidget(self.raw_len, 2, 1)
        grid.addWidget(self.raw_read_btn, 2, 3)
        grid.addWidget(QLabel("결과"), 3, 0)
        grid.addWidget(self.raw_out, 3, 1, 1, 3)
        grid.setRowStretch(4, 1)
        form_stretch(grid, 1, 2)
        return box

    # ------------------------------------------------------------------ actions

    def refresh_buses(self):
        current = self.bus_combo.currentData()
        self.bus_combo.clear()
        buses = I2CBus.available_buses()
        for num in buses:
            suffix = "  (HDMI DDC)" if num in DDC_BUSES else ""
            self.bus_combo.addItem(f"/dev/i2c-{num}{suffix}", num)
        if not buses:
            self.bus_combo.addItem("사용 가능한 버스 없음", None)
        if self.sim_check.isChecked() and 1 not in buses:
            self.bus_combo.addItem("i2c-1 (가상)", 1)
        index = self.bus_combo.findData(current)
        if index >= 0:
            self.bus_combo.setCurrentIndex(index)
        elif 1 in buses:
            self.bus_combo.setCurrentIndex(self.bus_combo.findData(1))

    def _open(self):
        num = self.bus_combo.currentData()
        if num is None:
            QMessageBox.information(
                self, "버스 없음",
                "/dev/i2c-* 노드가 없습니다.\n"
                "raspi-config 또는 config.txt에서 I2C를 켠 뒤 재부팅하거나,\n"
                "시뮬레이션 모드를 사용하세요.")
            return
        try:
            self.bus.open(num, simulated=self.sim_check.isChecked())
        except I2CError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "I2C 열기 실패", str(exc))
            return
        self._update_enabled()
        self.ctx.changed.emit()

    def _close(self):
        self.bus.close()
        self._clear_table()
        self.scan_result.setText("-")
        self._update_enabled()
        self.ctx.changed.emit()

    def _update_enabled(self):
        opened = self.bus.is_open
        self.open_btn.setEnabled(not opened)
        self.close_btn.setEnabled(opened)
        self.bus_combo.setEnabled(not opened)
        self.sim_check.setEnabled(not opened)
        for widget in (self.scan_btn, self.reg_read_btn, self.reg_write_btn,
                       self.raw_read_btn, self.raw_write_btn):
            widget.setEnabled(opened)
        if opened:
            mode = " (시뮬레이션)" if self.bus.simulated else ""
            self.state_label.setText(f"열림: /dev/i2c-{self.bus.bus_number}{mode}")
            self.state_label.setStyleSheet(f"color: {style.OK};")
        else:
            self.state_label.setText("닫힘")
            self.state_label.setStyleSheet(f"color: {style.MUTED};")

    def _clear_table(self):
        mono = QFont("DejaVu Sans Mono", 10)
        for row in range(8):
            for col in range(16):
                addr = row * 16 + col
                text = "--" if SCAN_START <= addr <= SCAN_END else "  "
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFont(mono)
                item.setForeground(QColor(style.BORDER))
                self.table.setItem(row, col, item)

    def _scan(self):
        self._clear_table()
        try:
            found = self.bus.scan(SCAN_START, SCAN_END)
        except I2CError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "스캔 실패", str(exc))
            return

        for addr in found:
            item = self.table.item(addr // 16, addr % 16)
            item.setText(f"{addr:02X}")
            item.setForeground(QColor("#0f1319"))
            item.setBackground(QColor(style.OK))

        if found:
            text = "  ".join(f"0x{a:02X}" for a in found)
            self.scan_result.setText(f"{len(found)}개 발견: {text}")
            self.scan_result.setStyleSheet(f"color: {style.OK};")
            log.info(f"I2C 스캔 결과 {len(found)}개: {text}")
        else:
            self.scan_result.setText("응답한 장치 없음")
            self.scan_result.setStyleSheet(f"color: {style.WARN};")
            log.warn("I2C 스캔: 응답한 장치가 없습니다")

    def _cell_clicked(self, row, col):
        addr = row * 16 + col
        if not SCAN_START <= addr <= SCAN_END:
            return
        self.reg_addr.setText(f"0x{addr:02X}")
        self.raw_addr.setText(f"0x{addr:02X}")

    # ------------------------------------------------------------- transactions

    def _reg_read(self):
        try:
            addr = parse_int(self.reg_addr.text())
            reg = parse_int(self.reg_num.text())
            data = self.bus.read_reg(addr, reg, self.reg_len.value())
        except (ParseError, I2CError) as exc:
            self._fail(self.reg_out, exc)
            return
        self.reg_out.setText(fmt_bytes(data))
        self.reg_out.setStyleSheet(f"color: {style.RX};")

    def _reg_write(self):
        try:
            addr = parse_int(self.reg_addr.text())
            reg = parse_int(self.reg_num.text())
            payload = parse_bytes(self.reg_data.text())
            if not payload:
                raise ParseError("쓸 데이터가 비어 있습니다")
            self.bus.write_reg(addr, reg, payload)
        except (ParseError, I2CError) as exc:
            self._fail(self.reg_out, exc)
            return
        self.reg_out.setText(f"{len(payload)}바이트 쓰기 완료")
        self.reg_out.setStyleSheet(f"color: {style.OK};")

    def _raw_write(self):
        try:
            addr = parse_int(self.raw_addr.text())
            payload = parse_bytes(self.raw_data.text())
            if not payload:
                raise ParseError("보낼 데이터가 비어 있습니다")
            self.bus.write_bytes(addr, payload)
        except (ParseError, I2CError) as exc:
            self._fail(self.raw_out, exc)
            return
        self.raw_out.setText(f"{len(payload)}바이트 전송 완료")
        self.raw_out.setStyleSheet(f"color: {style.OK};")

    def _raw_read(self):
        try:
            addr = parse_int(self.raw_addr.text())
            data = self.bus.read_bytes(addr, self.raw_len.value())
        except (ParseError, I2CError) as exc:
            self._fail(self.raw_out, exc)
            return
        self.raw_out.setText(fmt_bytes(data))
        self.raw_out.setStyleSheet(f"color: {style.RX};")

    def _fail(self, label, exc):
        log.error(str(exc))
        label.setText(str(exc))
        label.setStyleSheet(f"color: {style.ERR};")
