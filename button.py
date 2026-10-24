"""The wearer's button and the light inside it.

Kept apart from gadget_realtime.py, and free of slow imports, so the light can start blinking
while that program's big libraries load (about 10 seconds on a Pi at boot).
"""

import itertools
import queue
import sys
import threading

# The AIY Voice HAT's button connector (BCM numbering). Off the Pi, Enter on the keyboard works instead.
BUTTON_PIN = 23
BUTTON_LIGHT_PIN = 25


def gpio_device(cls_name: str, *args, **kwargs):
    """A gpiozero device, or None when not running on a Pi."""
    try:
        import gpiozero

        return getattr(gpiozero, cls_name)(*args, **kwargs)
    except Exception:
        return None


class Button:
    """The wearer's button: the GPIO button on the Pi, plus Enter on the keyboard everywhere."""

    def __init__(self):
        self.presses: queue.Queue[None] = queue.Queue()
        self.gpio = gpio_device("Button", BUTTON_PIN, bounce_time=0.05)
        if self.gpio:
            self.gpio.when_pressed = self.press
        threading.Thread(target=self._read_keyboard, daemon=True).start()

    def press(self):
        self.presses.put(None)

    def wait(self, timeout: float | None = None) -> bool:
        """True if pressed within `timeout` seconds."""
        try:
            self.presses.get(timeout=timeout)
            return True
        except queue.Empty:
            return False

    def clear(self):
        while not self.presses.empty():
            self.presses.get_nowait()

    def _read_keyboard(self):
        for _ in sys.stdin:
            self.press()


class ButtonLight:
    """The light in the button: fast blink while starting up, ringing, or hanging up; slow
    breathing when ready; solid on a call; and three quick blinks then a pause with no internet."""

    def __init__(self):
        # PWM so the light can fade in and out when ready.
        self.led = gpio_device("PWMLED", BUTTON_LIGHT_PIN)

    def busy(self):
        """Starting up, ringing, or hanging up: wait."""
        if self.led:
            self.led.source = None
            self.led.blink(on_time=0.15, off_time=0.15)

    def ready(self):
        if self.led:
            self.led.source = None
            self.led.pulse(fade_in_time=1.0, fade_out_time=1.0)

    def on_call(self):
        if self.led:
            self.led.source = None
            self.led.on()

    def no_internet(self):
        if self.led:
            self.led.off()  # stops any blinking, which would fight the pattern below
            self.led.source_delay = 0.15
            self.led.source = itertools.cycle([1, 0] * 3 + [0] * 6)
