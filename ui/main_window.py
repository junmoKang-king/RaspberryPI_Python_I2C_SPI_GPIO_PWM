"""Main window: tab stack over a shared log console, plus a live status bar."""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QAction, QDialog, QHBoxLayout, QLabel, QMainWindow,
                             QMessageBox, QScrollArea, QSplitter, QTabWidget,
                             QVBoxLayout, QWidget)

from core.context import AppContext
from core.logbus import log

from . import style
from .log_panel import LogPanel
from .tab_dac import DACTab
from .tab_gpio import GPIOTab
from .tab_i2c import I2CTab
from .tab_pwm import PWMTab
from .tab_sequence import SequenceTab
from .tab_spi import SPITab
from .widgets import StatusDot

PINMAP_IMAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pin map", "pinmaip.png")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Raspberry Pi I2C / SPI / GPIO Control")
        self.resize(1180, 840)

        self.ctx = AppContext(self)

        self.tabs = QTabWidget()
        self.i2c_tab = I2CTab(self.ctx)
        self.spi_tab = SPITab(self.ctx)
        self.gpio_tab = GPIOTab(self.ctx)
        self.pwm_tab = PWMTab(self.ctx)
        self.dac_tab = DACTab(self.ctx)
        self.sequence_tab = SequenceTab(self.ctx)
        self.tabs.addTab(self.i2c_tab, "I2C")
        self.tabs.addTab(self.spi_tab, "SPI")
        self.tabs.addTab(self.gpio_tab, "GPIO")
        self.tabs.addTab(self.pwm_tab, "PWM")
        self.tabs.addTab(self.dac_tab, "DAC")
        self.tabs.addTab(self.sequence_tab, "Sequence")

        # A running sequence owns the buses; lock the manual tabs meanwhile.
        self.sequence_tab.running_changed.connect(self._on_sequence_running)
        self.ctx.changed.connect(self.sequence_tab.refresh_bus_info)

        self.log_panel = LogPanel()

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 200])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self._build_menu()
        self._build_status_bar()
        self.ctx.changed.connect(self._refresh_status)
        self._refresh_status()

        log.info("Raspberry Pi I2C / SPI / GPIO Control 시작")
        self._warn_if_interfaces_off()

    # ------------------------------------------------------------------ chrome

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("파일(&F)")
        quit_action = QAction("종료(&Q)", self, shortcut="Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        tools_menu = self.menuBar().addMenu("도구(&T)")
        release_action = QAction("모든 버스 닫기 / GPIO 해제", self)
        release_action.triggered.connect(self._release_all)
        tools_menu.addAction(release_action)

        help_menu = self.menuBar().addMenu("도움말(&H)")
        pinmap_action = QAction("40핀 핀맵 보기", self)
        pinmap_action.triggered.connect(self._show_pinmap)
        help_menu.addAction(pinmap_action)
        about_action = QAction("정보", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_status_bar(self):
        self.i2c_dot = StatusDot("I2C")
        self.spi_dot = StatusDot("SPI")
        self.gpio_dot = StatusDot("GPIO")

        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 8, 0)
        layout.addWidget(self.i2c_dot)
        layout.addWidget(self.spi_dot)
        layout.addWidget(self.gpio_dot)
        self.statusBar().addPermanentWidget(bar)
        self.statusBar().showMessage("준비됨")

    # ------------------------------------------------------------------ actions

    def _refresh_status(self):
        i2c = self.ctx.i2c
        self.i2c_dot.set_state(
            i2c.is_open,
            f"I2C-{i2c.bus_number}{' SIM' if i2c.simulated else ''}" if i2c.is_open
            else "I2C 닫힘",
            warn=i2c.simulated)

        spi = self.ctx.spi
        self.spi_dot.set_state(
            spi.is_open,
            f"SPI{spi.label}{' SIM' if spi.simulated else ''}" if spi.is_open
            else "SPI 닫힘",
            warn=spi.simulated)

        gpio = self.ctx.gpio
        if gpio.is_open:
            text = f"GPIO {len(gpio.pins)}핀{' SIM' if gpio.simulated else ''}"
        else:
            text = "GPIO 닫힘"
        self.gpio_dot.set_state(gpio.is_open, text, warn=gpio.simulated)

    def _on_sequence_running(self, running):
        for index in range(self.tabs.count()):
            if self.tabs.widget(index) is not self.sequence_tab:
                self.tabs.setTabEnabled(index, not running)
        self.statusBar().showMessage("시퀀스 실행 중…" if running else "준비됨")

    def _release_all(self):
        self.sequence_tab.shutdown()
        self.pwm_tab.stop_all()
        self.dac_tab.shutdown()
        self.gpio_tab._close()
        self.spi_tab._close()
        self.i2c_tab._close()
        self._refresh_status()

    def _warn_if_interfaces_off(self):
        from core.i2c_bus import DDC_BUSES, I2CBus
        from core.spi_bus import SPIBus

        missing = []
        if not [n for n in I2CBus.available_buses() if n not in DDC_BUSES]:
            missing.append("I2C (/dev/i2c-1 없음)")
        if not SPIBus.available_devices():
            missing.append("SPI (/dev/spidev0.* 없음)")
        if not missing:
            return
        log.warn("사용할 수 없는 인터페이스: " + ", ".join(missing)
                 + " — 시뮬레이션 모드로 GUI를 사용할 수 있습니다")
        self.statusBar().showMessage(
            "비활성 인터페이스: " + ", ".join(missing)
            + "  ·  config.txt 설정 후 재부팅하거나 시뮬레이션 모드를 사용하세요")

    def _show_pinmap(self):
        if not os.path.exists(PINMAP_IMAGE):
            QMessageBox.information(self, "핀맵", f"이미지를 찾을 수 없습니다:\n{PINMAP_IMAGE}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Raspberry Pi 40핀 핀맵")
        dialog.resize(900, 620)

        label = QLabel()
        label.setPixmap(QPixmap(PINMAP_IMAGE).scaledToWidth(1400, Qt.SmoothTransformation))
        area = QScrollArea()
        area.setWidget(label)
        area.setWidgetResizable(True)

        layout = QVBoxLayout(dialog)
        layout.addWidget(area)
        dialog.exec_()

    def _show_about(self):
        QMessageBox.about(
            self, "정보",
            "<b>Raspberry Pi I2C / SPI / GPIO Control</b><br><br>"
            "PyQt5 기반 라즈베리파이 주변장치 제어 GUI.<br>"
            "I2C 스캔·레지스터 R/W, SPI 전이중 전송, GPIO 입출력,<br>"
            "PWM 출력, 외부 12비트 DAC 제어 및 파형 생성을 지원합니다.<br><br>"
            f"<span style='color:{style.MUTED}'>백엔드: smbus2 · spidev · lgpio</span>")

    # ------------------------------------------------------------------- close

    def closeEvent(self, event):
        self.sequence_tab.shutdown()
        self.pwm_tab.stop_all()
        self.dac_tab.shutdown()
        self.ctx.shutdown()
        super().closeEvent(event)
