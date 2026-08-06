"""Waveform generation.

`samples()` is pure maths so the preview widget and the output thread always
agree on what a given set of parameters looks like. `WaveformWorker` walks the
same table on a timer and pushes each point to the DAC, which is the Raspberry
Pi stand-in for an STM32 TIM-triggered DAC + DMA chain.
"""

import math
import time

from PyQt5.QtCore import QThread, pyqtSignal

from .dac import clamp

SINE = "Sine"
TRIANGLE = "Triangle"
SAWTOOTH = "Sawtooth"
SQUARE = "Square"
DC = "DC"

SHAPES = (SINE, TRIANGLE, SAWTOOTH, SQUARE, DC)


def unit_sample(shape, phase):
    """One cycle of `shape` in [0, 1]; `phase` is also in [0, 1)."""
    phase %= 1.0
    if shape == SINE:
        return 0.5 * (1.0 + math.sin(2.0 * math.pi * phase))
    if shape == TRIANGLE:
        return 2.0 * phase if phase < 0.5 else 2.0 * (1.0 - phase)
    if shape == SAWTOOTH:
        return phase
    if shape == SQUARE:
        return 1.0 if phase < 0.5 else 0.0
    return 1.0  # DC


def samples(shape, count, amplitude, offset, cycles=1.0):
    """Table of DAC counts. `amplitude`/`offset` are counts, output is clamped."""
    if count <= 0:
        return []
    if shape == DC:
        return [clamp(offset)] * count
    return [clamp(offset + amplitude * (2.0 * unit_sample(shape, cycles * i / count) - 1.0) / 2.0)
            for i in range(count)]


class WaveformWorker(QThread):
    """Streams a waveform table to a DAC write callback at a fixed sample rate."""

    #: emitted a few times per second with (counts, achieved_sample_rate)
    tick = pyqtSignal(int, float)
    failed = pyqtSignal(str)
    finished_run = pyqtSignal()

    #: how many table points make up one cycle
    TABLE_LEN = 256

    def __init__(self, parent=None):
        super().__init__(parent)
        self._write = None
        self._table = []
        self._interval = 0.001
        self._running = False

    def configure(self, write_fn, shape, freq_hz, amplitude, offset, max_rate_hz=20_000):
        """Build the table and the pacing interval for one set of parameters.

        The sample rate is `TABLE_LEN * freq`, capped at `max_rate_hz`; above the
        cap the table is decimated so the output frequency stays correct.
        """
        self._write = write_fn
        wanted_rate = self.TABLE_LEN * max(freq_hz, 0.01)
        if wanted_rate > max_rate_hz:
            points = max(4, int(max_rate_hz / max(freq_hz, 0.01)))
        else:
            points = self.TABLE_LEN
        self._table = samples(shape, points, amplitude, offset, cycles=1.0)
        rate = points * max(freq_hz, 0.01)
        self._interval = 1.0 / rate
        return rate

    def stop(self):
        self._running = False

    def run(self):
        if self._write is None or not self._table:
            self.failed.emit("파형이 설정되지 않았습니다")
            return

        self._running = True
        table = self._table
        n = len(table)
        interval = self._interval
        index = 0
        start = next_due = time.perf_counter()
        emitted = last_emit = 0

        try:
            while self._running:
                value = table[index]
                self._write(value)
                index = (index + 1) % n
                emitted += 1

                next_due += interval
                now = time.perf_counter()
                slack = next_due - now
                if slack > 0.0005:
                    time.sleep(slack)
                elif slack < -0.05:
                    # Fell far behind (bus too slow for this rate); resync rather
                    # than spin trying to catch up on every missed sample.
                    next_due = now

                if now - last_emit >= 0.1:
                    elapsed = now - start
                    self.tick.emit(value, emitted / elapsed if elapsed > 0 else 0.0)
                    last_emit = now
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self._running = False
            self.finished_run.emit()
