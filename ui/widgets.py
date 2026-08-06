"""Reusable widgets shared by the tabs."""

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QLabel, QSizePolicy, QWidget

from core import waveform
from core.dac import FULL_SCALE

from . import style


def form_stretch(grid, *field_columns):
    """Keep label columns at their hint and let the named field columns grow.

    Without this a QGridLayout shares surplus width equally, which leaves the
    short label cells as wide as the inputs next to them.
    """
    for column in range(grid.columnCount()):
        grid.setColumnStretch(column, 1 if column in field_columns else 0)


def fit_table_height(table, rows, row_height):
    """Size a fixed-size table so every row is visible without scrolling."""
    header = table.verticalHeader()
    # The default minimum section size is derived from the font metrics and
    # would silently clamp anything shorter than ~28 px.
    header.setMinimumSectionSize(row_height)
    header.setDefaultSectionSize(row_height)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    height = rows * row_height + 2 * table.frameWidth()
    if not table.horizontalHeader().isHidden():
        height += table.horizontalHeader().sizeHint().height()
    table.setFixedHeight(height)


class StatusDot(QLabel):
    """Small coloured pill used in the status bar / group headers."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setFont(QFont("DejaVu Sans Mono", 10))
        self.set_state(False, text)

    def set_state(self, active, text, warn=False):
        color = style.OK if active else (style.WARN if warn else style.MUTED)
        self.setText(f"● {text}")
        self.setStyleSheet(f"color: {color}; padding: 0 8px;")


class MonoLabel(QLabel):
    """Fixed-width readout for hex / numeric values."""

    def __init__(self, text="", parent=None, color=None, size=11):
        super().__init__(text, parent)
        self.setFont(QFont("DejaVu Sans Mono", size))
        if color:
            self.setStyleSheet(f"color: {color};")


class WaveformPreview(QWidget):
    """Draws two cycles of the configured waveform.

    Painted by hand rather than pulling in matplotlib/pyqtgraph, which are not
    installed on this Pi.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._shape = waveform.SINE
        self._amplitude = FULL_SCALE
        self._offset = FULL_SCALE // 2
        self._vref = 3.3
        self._cursor = None

    def set_params(self, shape, amplitude, offset, vref):
        self._shape = shape
        self._amplitude = amplitude
        self._offset = offset
        self._vref = vref
        self.update()

    def set_cursor(self, counts):
        self._cursor = counts
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(46, 10, -10, -20)
        p.fillRect(self.rect(), QColor(style.BG))
        p.fillRect(rect, QColor("#151920"))

        grid = QPen(QColor(style.BORDER), 1, Qt.DotLine)
        p.setPen(grid)
        p.setFont(QFont("DejaVu Sans Mono", 8))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            p.setPen(grid)
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
            p.setPen(QColor(style.MUTED))
            volts = self._vref * (4 - i) / 4
            p.drawText(2, int(y) + 4, f"{volts:4.2f}V")
        p.setPen(grid)
        for i in range(1, 8):
            x = rect.left() + rect.width() * i / 8
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())

        p.setPen(QColor(style.BORDER))
        p.drawRect(rect)

        pts = max(2, rect.width())
        table = waveform.samples(self._shape, pts, self._amplitude, self._offset, cycles=2.0)

        path = QPainterPath()
        for i, counts in enumerate(table):
            x = rect.left() + i
            y = rect.bottom() - (counts / FULL_SCALE) * rect.height()
            point = QPointF(x, y)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        p.setPen(QPen(QColor(style.ACCENT), 2))
        p.drawPath(path)

        if self._cursor is not None:
            y = rect.bottom() - (self._cursor / FULL_SCALE) * rect.height()
            p.setPen(QPen(QColor(style.OK), 1, Qt.DashLine))
            p.drawLine(rect.left(), int(y), rect.right(), int(y))

        p.setPen(QColor(style.MUTED))
        p.drawText(rect.left(), rect.bottom() + 14, "2 주기 미리보기")
        p.end()
