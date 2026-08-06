"""GPIO tab: 40-pin header view, per-pin direction/level control, live monitor."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                             QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core import pinmap
from core.gpio_ctrl import (IN, OUT, PWM, PULL_DOWN, PULL_NONE, PULL_UP,
                            GPIOError)
from core.logbus import log

from . import style
from .widgets import MonoLabel, fit_table_height, form_stretch

KIND_COLORS = {
    pinmap.POWER: "#c0392b",
    pinmap.GROUND: "#4b5263",
    pinmap.GPIO: "#2b3140",
}

POLL_MS = 200


class GPIOTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.gpio = ctx.gpio
        self._layout = None   # pin set currently rendered in the active table

        root = QHBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(self._build_header_view(), 0)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._build_connection())
        right.addWidget(self._build_control())
        right.addWidget(self._build_active(), 1)
        root.addLayout(right, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)

        self._update_enabled()

    # ------------------------------------------------------------------ layout

    def _build_header_view(self):
        box = QGroupBox("40핀 헤더")
        self.header_table = QTableWidget(20, 4)
        self.header_table.setHorizontalHeaderLabels(["기능", "핀", "핀", "기능"])
        self.header_table.verticalHeader().setVisible(False)
        self.header_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.header_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.header_table.cellClicked.connect(self._header_clicked)

        mono = QFont("DejaVu Sans Mono", 9)
        for row in range(20):
            left, right = row * 2 + 1, row * 2 + 2
            for col, phys in ((1, left), (2, right)):
                num = QTableWidgetItem(str(phys))
                num.setTextAlignment(Qt.AlignCenter)
                num.setFont(mono)
                num.setForeground(QColor(style.MUTED))
                self.header_table.setItem(row, col, num)

            for col, phys in ((0, left), (3, right)):
                kind, bcm, name, alt = pinmap.HEADER[phys]
                text = f"{name} ({alt})" if alt else name
                item = QTableWidgetItem(text)
                item.setFont(mono)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if col == 0
                                      else Qt.AlignLeft | Qt.AlignVCenter)
                item.setBackground(QColor(KIND_COLORS[kind]))
                item.setForeground(QColor(style.TEXT if kind == pinmap.GPIO else "#e6e6e6"))
                item.setData(Qt.UserRole, bcm)
                self.header_table.setItem(row, col, item)

        header = self.header_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.header_table.setColumnWidth(1, 34)
        self.header_table.setColumnWidth(2, 34)
        self.header_table.setFixedWidth(376)
        fit_table_height(self.header_table, 20, 21)

        lay = QVBoxLayout(box)
        lay.addWidget(self.header_table)
        lay.addWidget(QLabel(f"<span style='color:{style.MUTED}'>GPIO 칸을 클릭하면 "
                             "오른쪽 제어판에서 선택됩니다.</span>"))
        return box

    def _build_connection(self):
        box = QGroupBox("GPIO 연결")
        self.sim_check = QCheckBox("시뮬레이션 모드")
        self.open_btn = QPushButton("열기")
        self.close_btn = QPushButton("닫기")
        self.open_btn.clicked.connect(self._open)
        self.close_btn.clicked.connect(self._close)
        self.state_label = MonoLabel("닫힘", color=style.MUTED)

        lay = QHBoxLayout(box)
        lay.addWidget(self.sim_check)
        lay.addWidget(self.open_btn)
        lay.addWidget(self.close_btn)
        lay.addWidget(self.state_label, 1)
        return box

    def _build_control(self):
        box = QGroupBox("핀 제어")
        self.pin_combo = QComboBox()
        for bcm in pinmap.BCM_PINS:
            note = pinmap.RESERVED.get(bcm)
            text = pinmap.label_for(bcm) + (f"  ⚠ {note}" if note else "")
            self.pin_combo.addItem(text, bcm)
        self.pin_combo.setCurrentIndex(self.pin_combo.findData(17))

        self.pull_combo = QComboBox()
        for label, value in (("풀 없음", PULL_NONE), ("풀업", PULL_UP), ("풀다운", PULL_DOWN)):
            self.pull_combo.addItem(label, value)

        self.out_btn = QPushButton("출력으로 설정")
        self.in_btn = QPushButton("입력으로 설정")
        self.high_btn = QPushButton("HIGH")
        self.low_btn = QPushButton("LOW")
        self.toggle_btn = QPushButton("토글")
        self.free_btn = QPushButton("해제")

        self.out_btn.clicked.connect(lambda: self._claim(OUT))
        self.in_btn.clicked.connect(lambda: self._claim(IN))
        self.high_btn.clicked.connect(lambda: self._write(1))
        self.low_btn.clicked.connect(lambda: self._write(0))
        self.toggle_btn.clicked.connect(self._toggle)
        self.free_btn.clicked.connect(self._free)

        grid = QGridLayout(box)
        grid.addWidget(QLabel("핀"), 0, 0)
        grid.addWidget(self.pin_combo, 0, 1, 1, 3)
        grid.addWidget(QLabel("풀 저항"), 1, 0)
        grid.addWidget(self.pull_combo, 1, 1)
        grid.addWidget(self.in_btn, 1, 2)
        grid.addWidget(self.out_btn, 1, 3)
        grid.addWidget(self.high_btn, 2, 1)
        grid.addWidget(self.low_btn, 2, 2)
        grid.addWidget(self.toggle_btn, 2, 3)
        grid.addWidget(self.free_btn, 3, 3)
        form_stretch(grid, 1, 2, 3)
        return box

    def _build_active(self):
        box = QGroupBox("설정된 핀")
        self.active_table = QTableWidget(0, 5)
        self.active_table.setHorizontalHeaderLabels(["핀", "방향", "풀", "레벨", "동작"])
        self.active_table.verticalHeader().setVisible(False)
        self.active_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.active_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        self.monitor_check = QCheckBox("입력 실시간 감시 (200ms)", checked=True)
        self.monitor_check.toggled.connect(self._monitor_toggled)
        self.free_all_btn = QPushButton("모두 해제")
        self.free_all_btn.clicked.connect(self._free_all)

        bar = QHBoxLayout()
        bar.addWidget(self.monitor_check)
        bar.addStretch(1)
        bar.addWidget(self.free_all_btn)

        lay = QVBoxLayout(box)
        lay.addWidget(self.active_table, 1)
        lay.addLayout(bar)
        return box

    # ------------------------------------------------------------------ actions

    def _header_clicked(self, row, col):
        if col not in (0, 3):
            return
        bcm = self.header_table.item(row, col).data(Qt.UserRole)
        if bcm is None:
            return
        index = self.pin_combo.findData(bcm)
        if index >= 0:
            self.pin_combo.setCurrentIndex(index)

    def _open(self):
        try:
            self.gpio.open(simulated=self.sim_check.isChecked())
        except GPIOError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "GPIO 열기 실패", str(exc))
            return
        self._update_enabled()
        self._monitor_toggled(self.monitor_check.isChecked())
        self.ctx.changed.emit()

    def _close(self):
        self.timer.stop()
        self.gpio.close()
        self._refresh_active()
        self._update_enabled()
        self.ctx.changed.emit()

    def _update_enabled(self):
        opened = self.gpio.is_open
        self.open_btn.setEnabled(not opened)
        self.close_btn.setEnabled(opened)
        self.sim_check.setEnabled(not opened)
        for widget in (self.in_btn, self.out_btn, self.high_btn, self.low_btn,
                       self.toggle_btn, self.free_btn, self.free_all_btn):
            widget.setEnabled(opened)
        if opened:
            mode = " (시뮬레이션)" if self.gpio.simulated else ""
            self.state_label.setText(f"열림: gpiochip{self.gpio.chip}{mode}")
            self.state_label.setStyleSheet(f"color: {style.OK};")
        else:
            self.state_label.setText("닫힘")
            self.state_label.setStyleSheet(f"color: {style.MUTED};")

    def _selected(self):
        return self.pin_combo.currentData()

    def _confirm_reserved(self, bcm):
        note = pinmap.RESERVED.get(bcm)
        if note is None:
            return True
        answer = QMessageBox.question(
            self, "예약된 핀",
            f"{pinmap.label_for(bcm)}은(는) {note} 용도입니다.\n"
            "직접 제어하면 해당 버스 통신이 깨질 수 있습니다.\n\n계속할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _claim(self, mode):
        bcm = self._selected()
        if not self._confirm_reserved(bcm):
            return
        try:
            if mode == OUT:
                self.gpio.claim_output(bcm, 0)
            else:
                self.gpio.claim_input(bcm, self.pull_combo.currentData())
        except GPIOError as exc:
            log.error(str(exc))
            QMessageBox.critical(self, "설정 실패", str(exc))
            return
        self._refresh_active()

    def _write(self, level):
        try:
            self.gpio.write(self._selected(), level)
        except GPIOError as exc:
            log.error(str(exc))
            QMessageBox.warning(self, "쓰기 실패", str(exc))
            return
        self._refresh_active()

    def _toggle(self):
        try:
            self.gpio.toggle(self._selected())
        except GPIOError as exc:
            log.error(str(exc))
            QMessageBox.warning(self, "토글 실패", str(exc))
            return
        self._refresh_active()

    def _free(self):
        try:
            self.gpio.free(self._selected())
        except GPIOError as exc:
            log.error(str(exc))
            return
        self._refresh_active()

    def _free_all(self):
        self.gpio.free_all()
        self._refresh_active()

    def _monitor_toggled(self, on):
        if on and self.gpio.is_open:
            self.timer.start()
        else:
            self.timer.stop()

    def _poll(self):
        changed = False
        for bcm, state in list(self.gpio.pins.items()):
            if state["mode"] != IN:
                continue
            before = state["level"]
            try:
                if self.gpio.read(bcm) != before:
                    changed = True
            except GPIOError:
                continue
        if changed:
            self._refresh_active()

    # ------------------------------------------------------------------ display

    def _refresh_active(self):
        pins = sorted(self.gpio.pins.items())
        mono = QFont("DejaVu Sans Mono", 9)

        # Only rebuild rows when the pin set itself changes; the 200 ms input
        # monitor otherwise just rewrites the level text.
        layout = tuple((bcm, state["mode"]) for bcm, state in pins)
        if layout != self._layout:
            self._layout = layout
            self.active_table.clearContents()
            self.active_table.setRowCount(len(pins))
            for row, (bcm, state) in enumerate(pins):
                self.active_table.setItem(row, 0, _cell(pinmap.label_for(bcm), mono))
                for col in (1, 2, 3):
                    self.active_table.setItem(row, col, _cell("", mono))
                if state["mode"] == OUT:
                    btn = QPushButton("토글")
                    btn.clicked.connect(lambda _, pin=bcm: self._row_toggle(pin))
                    self.active_table.setCellWidget(row, 4, btn)
                else:
                    self.active_table.setCellWidget(row, 4, None)
                    self.active_table.setItem(row, 4, _cell("-", mono))

        for row, (_bcm, state) in enumerate(pins):
            self.active_table.item(row, 1).setText(
                {IN: "입력", OUT: "출력", PWM: "PWM"}[state["mode"]])
            self.active_table.item(row, 2).setText(state["pull"])

            if state["mode"] == PWM:
                text, color = f"{state['freq']:g}Hz {state['duty']:.0f}%", style.WARN
            elif state["level"]:
                text, color = "HIGH", style.OK
            else:
                text, color = "LOW", style.MUTED
            level = self.active_table.item(row, 3)
            level.setText(text)
            level.setForeground(QColor(color))

        self._paint_header_levels()

    def _row_toggle(self, bcm):
        try:
            self.gpio.toggle(bcm)
        except GPIOError as exc:
            log.error(str(exc))
            return
        self._refresh_active()

    def _paint_header_levels(self):
        for row in range(20):
            for col in (0, 3):
                item = self.header_table.item(row, col)
                bcm = item.data(Qt.UserRole)
                if bcm is None:
                    continue
                state = self.gpio.pins.get(bcm)
                if state is None:
                    item.setBackground(QColor(KIND_COLORS[pinmap.GPIO]))
                elif state["mode"] == PWM:
                    item.setBackground(QColor(style.WARN))
                elif state["level"]:
                    item.setBackground(QColor(style.OK))
                else:
                    item.setBackground(QColor(style.ACCENT).darker(160))


def _cell(text, font):
    item = QTableWidgetItem(text)
    item.setFont(font)
    return item
