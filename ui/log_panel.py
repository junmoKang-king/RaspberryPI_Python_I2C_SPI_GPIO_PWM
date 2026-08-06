"""Bottom console: every bus transaction and status message lands here."""

import time

from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from core.logbus import log

from . import style

LEVEL_COLORS = {
    "INFO": style.MUTED,
    "TX": style.TX,
    "RX": style.RX,
    "WARN": style.WARN,
    "ERROR": style.ERR,
}

MAX_LINES = 2000


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.view = QPlainTextEdit(readOnly=True)
        self.view.setFont(QFont("DejaVu Sans Mono", 10))
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setStyleSheet(f"background: #151920; border: 1px solid {style.BORDER};")

        self.autoscroll = QCheckBox("자동 스크롤", checked=True)
        self.show_tx = QCheckBox("TX/RX 표시", checked=True)
        clear_btn = QPushButton("지우기")
        save_btn = QPushButton("저장…")
        clear_btn.clicked.connect(self.view.clear)
        save_btn.clicked.connect(self._save)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QLabel("<b>로그 콘솔</b>"))
        bar.addStretch(1)
        bar.addWidget(self.show_tx)
        bar.addWidget(self.autoscroll)
        bar.addWidget(clear_btn)
        bar.addWidget(save_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)
        root.addLayout(bar)
        root.addWidget(self.view, 1)

        log.message.connect(self.append)

    def append(self, level, text):
        if level in ("TX", "RX") and not self.show_tx.isChecked():
            return
        color = LEVEL_COLORS.get(level, style.TEXT)
        stamp = time.strftime("%H:%M:%S")
        html = (f'<span style="color:{style.BORDER}">{stamp}</span> '
                f'<span style="color:{color}">[{level:<5}]</span> '
                f'<span style="color:{style.TEXT}">{_escape(text)}</span>')
        self.view.appendHtml(html)
        if self.autoscroll.isChecked():
            self.view.moveCursor(QTextCursor.End)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "로그 저장", f"i2c_spi_gpio_{time.strftime('%Y%m%d_%H%M%S')}.log",
            "로그 파일 (*.log *.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.view.toPlainText())
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        log.info(f"로그를 저장했습니다: {path}")


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
