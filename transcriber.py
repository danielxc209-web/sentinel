import queue
import threading
import numpy as np
import sounddevice as sd
import whisper

SAMPLE_RATE = 16000
BLOCK_SECONDS = 5      # how many seconds of audio per transcription chunk
SILENCE_THRESHOLD = 0.01

class Transcriber:
    def __init__(self, input_queue: queue.Queue, model_size: str = "base"):
        self.input_queue = input_queue
        self.model = whisper.load_model(model_size)
        self._stop = threading.Event()
        self._muted = threading.Event()  # set = muted

    def mute(self):
        self._muted.set()

    def unmute(self):
        self._muted.clear()

    def _is_silent(self, audio: np.ndarray) -> bool:
        return np.abs(audio).mean() < SILENCE_THRESHOLD

    def _record_chunk(self) -> np.ndarray:
        frames = int(SAMPLE_RATE * BLOCK_SECONDS)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        return audio.flatten()

    def run(self):
        print("[Transcriber] Listening...")
        while not self._stop.is_set():
            if self._muted.is_set():
                self._stop.wait(timeout=0.5)
                continue
            audio = self._record_chunk()
            if self._is_silent(audio):
                continue
            result = self.model.transcribe(audio, fp16=False, language="en")
            text = result["text"].strip()
            if text:
                print(f"[Transcriber] {text}")
                self.input_queue.put({"type": "transcription", "text": text})

    def stop(self):
        self._stop.set()