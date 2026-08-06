#!/usr/bin/env python3
"""Entry point for the Raspberry Pi I2C / SPI / GPIO control GUI.

    python3 main.py
"""

import os
import sys

# Allow `python3 main.py` from any working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.style import QSS  # noqa: E402


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("I2C SPI GPIO Control")
    app.setStyleSheet(QSS)

    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
