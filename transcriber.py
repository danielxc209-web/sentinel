import queue
import threading
import numpy as np
import sounddevice as sd
import whisper

SAMPLE_RATE = 16000
BLOCK_SECONDS = 5
SILENCE_THRESHOLD = 0.01

class Transcriber:
    def __init__(self, input_queue: queue.Queue, model_size: str = "base"):
        self.input_queue = input_queue
        # BUG FIX 1: whisper.load_model() can raise if the model name is invalid
        # or if there's no internet on first run. Wrapped in try/except with a
        # clear error message instead of a cryptic crash.
        try:
            self.model = whisper.load_model(model_size)
        except Exception as e:
            raise RuntimeError(
                f"[Transcriber] Failed to load Whisper model '{model_size}': {e}\n"
                "Valid sizes: tiny, base, small, medium, large"
            ) from e
        self._stop = threading.Event()

    def _is_silent(self, audio: np.ndarray) -> bool:
        return np.abs(audio).mean() < SILENCE_THRESHOLD

    def _record_chunk(self) -> np.ndarray:
        frames = int(SAMPLE_RATE * BLOCK_SECONDS)
        # BUG FIX 2: sd.rec() + sd.wait() blocks the thread and offers no way
        # to respect self._stop mid-recording. Replaced with sd.InputStream so
        # the stop event is checked between chunks.
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        return audio.flatten()

    def run(self):
        print("[Transcriber] Listening...")
        while not self._stop.is_set():
            # BUG FIX 3: _record_chunk() had no exception handling. A missing
            # microphone or PortAudio error would crash the entire thread
            # silently (daemon thread swallows the exception). Now logged clearly.
            try:
                audio = self._record_chunk()
            except Exception as e:
                print(f"[Transcriber] Audio capture error: {e}")
                continue

            if self._is_silent(audio):
                continue

            # BUG FIX 4: model.transcribe() had no exception handling either.
            # A corrupt audio chunk or Whisper internal error would kill the thread.
            try:
                result = self.model.transcribe(audio, fp16=False, language="en")
            except Exception as e:
                print(f"[Transcriber] Transcription error: {e}")
                continue

            text = result["text"].strip()
            if text:
                print(f"[Transcriber] {text}")
                self.input_queue.put({"type": "transcription", "text": text})

    def stop(self):
        self._stop.set()
        # BUG FIX 5: stop() had no way to unblock sd.wait() which could cause
        # the thread to hang for up to BLOCK_SECONDS after stop() is called.
        # sd.stop() signals sounddevice to abort the current recording.
        sd.stop()