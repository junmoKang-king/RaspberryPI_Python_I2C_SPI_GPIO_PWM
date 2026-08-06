"""Dark instrument-panel theme."""

BG = "#1b1f27"
PANEL = "#232833"
PANEL_HI = "#2b3140"
BORDER = "#39414f"
TEXT = "#dde3ec"
MUTED = "#8b95a7"
ACCENT = "#4da3ff"
OK = "#3fcf8e"
WARN = "#f5b942"
ERR = "#ff6b6b"
TX = "#7bd4ff"
RX = "#c792ea"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Noto Sans CJK KR", "DejaVu Sans", sans-serif;
    font-size: 13px;
}}
QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: {ACCENT};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 18px;
    margin-right: 2px;
    color: {MUTED};
}}
QTabBar::tab:selected {{
    background: {PANEL_HI};
    color: {ACCENT};
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}

QPushButton {{
    background: {PANEL_HI};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover  {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL}; }}
QPushButton:checked {{ background: {ACCENT}; color: #0f1319; font-weight: 600; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 7px;
    selection-background-color: {ACCENT};
    selection-color: #0f1319;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #0f1319;
}}

QTableWidget {{
    background: {BG};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
QHeaderView::section {{
    background: {PANEL_HI};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 5px;
    font-weight: 600;
}}
QTableWidget::item:selected {{ background: {ACCENT}; color: #0f1319; }}
QTableCornerButton::section {{
    background: {PANEL_HI};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

QSlider::groove:horizontal {{
    height: 5px; background: {BORDER}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 15px; margin: -6px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {BORDER}; border-radius: 3px; background: {BG};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
}}
QRadioButton::indicator {{ border-radius: 7px; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {BORDER}; height: 3px; }}
QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QToolTip {{
    background: {PANEL_HI}; color: {TEXT};
    border: 1px solid {ACCENT}; padding: 4px;
}}
"""
