"""Application-wide log bus.

Any module can emit a log line without knowing about the UI; the log panel
subscribes to `log.message` and renders it.
"""

from PyQt5.QtCore import QObject, pyqtSignal


class LogBus(QObject):
    #: level ("INFO" | "TX" | "RX" | "WARN" | "ERROR"), text
    message = pyqtSignal(str, str)

    def info(self, text):
        self.message.emit("INFO", text)

    def tx(self, text):
        self.message.emit("TX", text)

    def rx(self, text):
        self.message.emit("RX", text)

    def warn(self, text):
        self.message.emit("WARN", text)

    def error(self, text):
        self.message.emit("ERROR", text)


log = LogBus()
