import pyttsx3
import threading

class Speaker:
    """Thread-safe local TTS via pyttsx3."""

    def __init__(self):
        self._lock = threading.Lock()
        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", 175)
        self._engine.setProperty("volume", 1.0)

    def speak(self, text: str):
        with self._lock:
            self._engine.say(text)
            self._engine.runAndWait()
