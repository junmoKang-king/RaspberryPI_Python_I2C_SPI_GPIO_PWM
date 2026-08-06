"""Sequence tab: build a list of I2C / SPI / GPIO / PWM / DAC steps and run them.

Steps execute on a worker thread (`core.sequence.SequenceRunner`) so the
per-step delays do not freeze the UI, and each row shows its own result.
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                             QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.i2c_bus import I2CBus
from core.logbus import log
from core.sequence import (MODE_MAP, MODES, SequenceError, SequenceRunner,
                           SequenceStep, load_steps, save_steps)

from . import style

COL_DESC, COL_MODE, COL_CMD, COL_LEN, COL_DELAY, COL_RESULT, COL_ON, COL_RUN, COL_REP = range(9)

HEADERS = ["설명", "모드", "명령", "읽기 길이", "지연(ms)", "결과", "사용", "실행", "반복"]

SAMPLE = [
    ("I2C 스캔",      "I2C Scan",      "",              10),
    ("DAC 1.65V",     "DAC Write I2C", "0x62, 2048",    100),
    ("GPIO17 HIGH",   "GPIO Write",    "17, 1",         500),
    ("GPIO17 LOW",    "GPIO Write",    "17, 0",         500),
    ("PWM 1kHz 50%",  "PWM Start",     "12, 1000, 50",  1000),
    ("PWM 정지",       "PWM Stop",      "12",            10),
]


class SequenceTab(QWidget):
    #: True while a sequence is executing; the main window locks other tabs.
    running_changed = pyqtSignal(bool)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.steps = []
        self.runner = None

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_output())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 420])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        for description, mode, command, delay in SAMPLE:
            step = SequenceStep(description=description, mode=mode,
                                command=command, delay_ms=delay)
            self.steps.append(step)
        self._rebuild()
        self.refresh_bus_info()

    # ------------------------------------------------------------------ layout

    def _build_left(self):
        panel = QWidget()

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_CMD, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_RESULT, QHeaderView.Stretch)
        for column, width in ((COL_MODE, 130), (COL_LEN, 80), (COL_DELAY, 80),
                              (COL_ON, 46), (COL_RUN, 66), (COL_REP, 62)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)

        root = QVBoxLayout(panel)
        root.setContentsMargins(8, 8, 4, 8)
        root.setSpacing(8)
        root.addWidget(self.table, 1)
        root.addLayout(self._build_edit_bar())
        root.addLayout(self._build_run_bar())
        root.addWidget(self._build_info())
        return panel

    def _build_edit_bar(self):
        bar = QHBoxLayout()
        self.edit_buttons = {}
        for key, text, slot in (
            ("load", "불러오기", self._load),
            ("save", "저장", self._save),
            ("add", "추가", self._add),
            ("copy", "복사", self._copy),
            ("up", "위로", lambda: self._move(-1)),
            ("down", "아래로", lambda: self._move(1)),
            ("delete", "삭제", self._delete),
            ("clear", "전체 지우기", self._clear),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.edit_buttons[key] = button
        bar.addStretch(1)
        return bar

    def _build_run_bar(self):
        bar = QHBoxLayout()
        self.stop_on_error = QCheckBox("오류 시 중단", checked=True)
        self.cycles_spin = QSpinBox(minimum=1, maximum=10_000, value=1)
        self.run_btn = QPushButton("시퀀스 실행")
        self.run_sel_btn = QPushButton("선택 단계만 실행")
        self.stop_btn = QPushButton("정지")
        self.stop_btn.setEnabled(False)

        self.run_btn.clicked.connect(self._run_all)
        self.run_sel_btn.clicked.connect(self._run_selected)
        self.stop_btn.clicked.connect(self._stop)

        bar.addWidget(self.stop_on_error)
        bar.addStretch(1)
        bar.addWidget(QLabel("반복"))
        bar.addWidget(self.cycles_spin)
        bar.addWidget(self.run_sel_btn)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.stop_btn)
        return bar

    def _build_info(self):
        box = QGroupBox("버스 정보")
        self.i2c_speed_label = QLabel("-")
        self.i2c_speed_label.setToolTip(
            "I2C 클럭은 실행 중에 바꿀 수 없습니다.\n"
            "config.txt의 dtparam=i2c_arm_baudrate=... 로 설정한 뒤 재부팅하세요.")
        refresh_btn = QPushButton("새로 고침")
        refresh_btn.clicked.connect(self.refresh_bus_info)

        self.hint_label = QLabel("-")
        self.hint_label.setStyleSheet(f"color: {style.MUTED};")

        lay = QHBoxLayout(box)
        lay.addWidget(QLabel("I2C 클럭"))
        lay.addWidget(self.i2c_speed_label)
        lay.addWidget(refresh_btn)
        lay.addSpacing(16)
        lay.addWidget(QLabel("명령 형식"))
        lay.addWidget(self.hint_label, 1)
        return box

    def _build_output(self):
        panel = QWidget()
        self.output = QPlainTextEdit(readOnly=True)
        self.output.setFont(QFont("DejaVu Sans Mono", 9))
        self.output.setMaximumBlockCount(4000)
        self.output.setStyleSheet(f"background: #151920; border: 1px solid {style.BORDER};")
        self.output.setPlaceholderText("실행 결과가 여기에 표시됩니다.")

        clear_btn = QPushButton("출력창 지우기")
        clear_btn.clicked.connect(self.output.clear)
        save_btn = QPushButton("출력 저장…")
        save_btn.clicked.connect(self._save_output)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("<b>실행 결과</b>"))
        bar.addStretch(1)
        bar.addWidget(save_btn)
        bar.addWidget(clear_btn)

        root = QVBoxLayout(panel)
        root.setContentsMargins(4, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(bar)
        root.addWidget(self.output, 1)
        return panel

    # ------------------------------------------------------------------- table

    def _rebuild(self):
        selected = self.table.currentRow()
        self.table.clearContents()
        self.table.setRowCount(len(self.steps))
        self.table.setVerticalHeaderLabels([str(i + 1) for i in range(len(self.steps))])
        for row, step in enumerate(self.steps):
            self._build_row(row, step)
        if 0 <= selected < len(self.steps):
            self.table.selectRow(selected)
        elif self.steps:
            self.table.selectRow(min(selected if selected >= 0 else 0,
                                     len(self.steps) - 1))
        self._update_hint()

    def _build_row(self, row, step):
        mono = QFont("DejaVu Sans Mono", 9)

        desc = QLineEdit(step.description)
        desc.setPlaceholderText("설명")
        desc.textChanged.connect(lambda text, s=step: setattr(s, "description", text))
        self.table.setCellWidget(row, COL_DESC, desc)

        mode = QComboBox()
        for spec in MODES:
            mode.addItem(spec.key, spec.key)
        index = mode.findData(step.mode)
        mode.setCurrentIndex(index if index >= 0 else 0)
        self.table.setCellWidget(row, COL_MODE, mode)

        command = QLineEdit(step.command)
        command.setFont(mono)
        command.textChanged.connect(lambda text, s=step: setattr(s, "command", text))
        self.table.setCellWidget(row, COL_CMD, command)

        length = QSpinBox(minimum=1, maximum=256, value=step.read_length)
        length.valueChanged.connect(lambda value, s=step: setattr(s, "read_length", value))
        self.table.setCellWidget(row, COL_LEN, length)

        delay = QSpinBox(minimum=0, maximum=600_000, value=step.delay_ms, singleStep=10)
        delay.valueChanged.connect(lambda value, s=step: setattr(s, "delay_ms", value))
        self.table.setCellWidget(row, COL_DELAY, delay)

        result = QTableWidgetItem(step.result)
        result.setFont(mono)
        result.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(row, COL_RESULT, result)

        enabled = QCheckBox(checked=step.enabled)
        enabled.toggled.connect(lambda on, s=step: setattr(s, "enabled", on))
        self.table.setCellWidget(row, COL_ON, _centered(enabled))

        run = QPushButton("실행")
        run.clicked.connect(lambda _, s=step: self._run_one(s))
        self.table.setCellWidget(row, COL_RUN, run)

        repeat = QSpinBox(minimum=1, maximum=10_000, value=step.repeat)
        repeat.valueChanged.connect(lambda value, s=step: setattr(s, "repeat", value))
        self.table.setCellWidget(row, COL_REP, repeat)

        mode.currentIndexChanged.connect(
            lambda _index, s=step, m=mode, c=command, n=length:
            self._mode_changed(s, m, c, n))
        self._apply_mode(step.mode, command, length)

    def _mode_changed(self, step, mode_combo, command_edit, length_spin):
        step.mode = mode_combo.currentData()
        self._apply_mode(step.mode, command_edit, length_spin)
        self._update_hint()

    @staticmethod
    def _apply_mode(mode, command_edit, length_spin):
        spec = MODE_MAP.get(mode)
        if spec is None:
            return
        command_edit.setPlaceholderText(spec.hint)
        command_edit.setToolTip(f"형식: {spec.hint}")
        length_spin.setEnabled(spec.needs_length)

    def _update_hint(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.steps):
            self.hint_label.setText("-")
            return
        spec = MODE_MAP.get(self.steps[row].mode)
        self.hint_label.setText(f"{spec.key}  →  {spec.hint}" if spec else "-")

    # ------------------------------------------------------------ list editing

    def _current(self):
        row = self.table.currentRow()
        return row if 0 <= row < len(self.steps) else -1

    def _add(self):
        row = self._current()
        at = row + 1 if row >= 0 else len(self.steps)
        self.steps.insert(at, SequenceStep())
        self._rebuild()
        self.table.selectRow(at)

    def _copy(self):
        row = self._current()
        if row < 0:
            return
        source = self.steps[row]
        clone = SequenceStep.from_dict(source.to_dict())
        self.steps.insert(row + 1, clone)
        self._rebuild()
        self.table.selectRow(row + 1)

    def _move(self, offset):
        row = self._current()
        target = row + offset
        if row < 0 or not 0 <= target < len(self.steps):
            return
        self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
        self._rebuild()
        self.table.selectRow(target)

    def _delete(self):
        row = self._current()
        if row < 0:
            return
        del self.steps[row]
        self._rebuild()

    def _clear(self):
        if not self.steps:
            return
        answer = QMessageBox.question(
            self, "전체 지우기", f"{len(self.steps)}개 단계를 모두 삭제할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.steps.clear()
        self._rebuild()

    # -------------------------------------------------------------- file I/O

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "시퀀스 불러오기", "", "시퀀스 파일 (*.json);;모든 파일 (*)")
        if not path:
            return
        try:
            steps = load_steps(path)
        except (OSError, ValueError, SequenceError) as exc:
            log.error(f"시퀀스 불러오기 실패: {exc}")
            QMessageBox.critical(self, "불러오기 실패", str(exc))
            return
        self.steps = steps
        self._rebuild()
        log.info(f"시퀀스 {len(steps)}단계를 불러왔습니다: {path}")
        self._emit(f"시퀀스를 불러왔습니다 ({len(steps)}단계): {path}")

    def _save(self):
        if not self.steps:
            QMessageBox.information(self, "저장", "저장할 단계가 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "시퀀스 저장", "sequence.json", "시퀀스 파일 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            save_steps(path, self.steps)
        except OSError as exc:
            log.error(f"시퀀스 저장 실패: {exc}")
            QMessageBox.critical(self, "저장 실패", str(exc))
            return
        log.info(f"시퀀스를 저장했습니다: {path}")
        self._emit(f"시퀀스를 저장했습니다: {path}")

    def _save_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "출력 저장", "sequence_output.txt", "텍스트 파일 (*.txt *.log)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.output.toPlainText())
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        log.info(f"시퀀스 출력을 저장했습니다: {path}")

    # -------------------------------------------------------------- execution

    def _run_all(self):
        self._start(range(len(self.steps)), self.cycles_spin.value())

    def _run_selected(self):
        row = self._current()
        if row < 0:
            QMessageBox.information(self, "선택 없음", "실행할 단계를 선택하세요.")
            return
        self._start([row], self.cycles_spin.value())

    def _run_one(self, step):
        try:
            index = self.steps.index(step)
        except ValueError:
            return
        self.table.selectRow(index)
        self._start([index], 1)

    def _start(self, indices, cycles):
        if self.runner is not None and self.runner.isRunning():
            return
        indices = [i for i in indices if self.steps[i].enabled]
        if not indices:
            QMessageBox.information(self, "실행할 단계 없음",
                                    "사용으로 표시된 단계가 없습니다.")
            return

        for index in indices:
            self._set_result(index, "", None)

        self.runner = SequenceRunner(self.ctx, self.steps, indices, cycles,
                                     stop_on_error=self.stop_on_error.isChecked(),
                                     parent=self)
        self.runner.step_started.connect(self._on_step_started)
        self.runner.step_result.connect(self._on_step_result)
        self.runner.output.connect(self._emit)
        self.runner.finished_run.connect(self._on_finished)

        self._set_running(True)
        count = len(indices)
        self._emit(f"▶ 실행 시작 — {count}단계"
                   + (f" × {cycles}사이클" if cycles > 1 else ""))
        log.info(f"시퀀스 실행 시작 ({count}단계, {cycles}사이클)")
        self.runner.start()

    def _stop(self):
        if self.runner is not None:
            self.runner.stop()

    def _on_step_started(self, index, iteration):
        self.table.selectRow(index)
        self._set_result(index, "실행 중…" if iteration == 1 else f"실행 중… ({iteration})",
                         None)

    def _on_step_result(self, index, ok, text):
        self._set_result(index, text, ok)

    def _set_result(self, index, text, ok):
        item = self.table.item(index, COL_RESULT)
        if item is None:
            return
        item.setText(text)
        color = style.MUTED if ok is None else (style.OK if ok else style.ERR)
        item.setForeground(QColor(color))

    def _on_finished(self, completed):
        self._set_running(False)
        self._emit("■ 실행 완료" if completed else "■ 실행 중단됨")
        log.info("시퀀스 실행 완료" if completed else "시퀀스 실행 중단")
        self.runner = None

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.run_sel_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.cycles_spin.setEnabled(not running)
        for button in self.edit_buttons.values():
            button.setEnabled(not running)
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, COL_RUN)
            if widget is not None:
                widget.setEnabled(not running)
        self.running_changed.emit(running)

    def _emit(self, text):
        self.output.appendPlainText(text)

    # ------------------------------------------------------------------- misc

    def refresh_bus_info(self):
        buses = [n for n in I2CBus.available_buses() if n not in (20, 21)]
        num = self.ctx.i2c.bus_number if self.ctx.i2c.is_open else (
            buses[0] if buses else None)
        speed = I2CBus.bus_speed_hz(num) if num is not None else None
        if speed:
            self.i2c_speed_label.setText(f"i2c-{num}: {speed / 1000:g} kHz")
            self.i2c_speed_label.setStyleSheet(f"color: {style.TEXT};")
        else:
            self.i2c_speed_label.setText("확인 불가")
            self.i2c_speed_label.setStyleSheet(f"color: {style.MUTED};")

    def shutdown(self):
        if self.runner is not None and self.runner.isRunning():
            self.runner.stop()
            self.runner.wait(3000)


def _centered(widget):
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignCenter)
    layout.addWidget(widget)
    return holder
