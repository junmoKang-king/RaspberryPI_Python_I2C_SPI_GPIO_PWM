"""Shared hardware handles.

All tabs talk to the same three objects so that, for example, the DAC tab can
reuse whatever bus the I2C tab already opened.
"""

from PyQt5.QtCore import QObject, pyqtSignal

from .gpio_ctrl import GPIOController
from .i2c_bus import I2CBus
from .spi_bus import SPIBus


class AppContext(QObject):
    #: emitted whenever a bus is opened or closed, so the status bar can refresh
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i2c = I2CBus()
        self.spi = SPIBus()
        self.gpio = GPIOController()

    def shutdown(self):
        self.gpio.close()
        self.spi.close()
        self.i2c.close()
