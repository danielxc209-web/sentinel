"""
Sentinel — multi-agent AI system
Threads: Transcriber → input_queue → Orchestrator → task_queue → Executor
         TextInput  ↗              ←  result_queue ←

Commands:
  /speak  — switch to mic input mode
  /type   — switch to text input mode (mutes mic)
"""
import os
import queue
import signal
import threading

from transcriber import Transcriber
from orchestrator import Orchestrator
from executor import Executor
from memory import Memory
from speaker import Speaker

GROQ_API_KEY = os.environ["GROQ_API_KEY"]

def text_input_loop(
    input_queue: queue.Queue,
    stop_event: threading.Event,
    transcriber: "Transcriber",
    input_mode: list  # mutable single-element list: ["speak"] or ["type"]
):
    print("[Input] Type messages and press Enter (Ctrl+C to quit)")
    print("[Input] Commands: /type  /speak")

    while not stop_event.is_set():
        try:
            prompt = "(type) > " if input_mode[0] == "type" else "(speak) > "
            text = input(prompt).strip()

            if not text:
                continue

            if text == "/type":
                input_mode[0] = "type"
                transcriber.mute()
                print("[Input] Switched to type mode — mic muted")
                continue

            if text == "/speak":
                input_mode[0] = "speak"
                transcriber.unmute()
                print("[Input] Switched to speak mode — mic active")
                continue

            # In speak mode, typed text still goes through (useful fallback)
            input_queue.put({"type": "text", "text": text})

        except (EOFError, KeyboardInterrupt):
            break


def main():
    input_queue  = queue.Queue()
    task_queue   = queue.Queue()
    result_queue = queue.Queue()
    stop_event   = threading.Event()
    input_mode   = ["speak"]  # default to mic

    memory  = Memory()
    speaker = Speaker()

    transcriber  = Transcriber(input_queue, model_size="base")
    orchestrator = Orchestrator(input_queue, task_queue, result_queue, memory, GROQ_API_KEY, speaker)
    executor     = Executor(task_queue, result_queue, memory, GROQ_API_KEY)

    threads = [
        threading.Thread(target=transcriber.run,  daemon=True, name="Transcriber"),
        threading.Thread(target=orchestrator.run, daemon=True, name="Orchestrator"),
        threading.Thread(target=executor.run,     daemon=True, name="Executor"),
        threading.Thread(
            target=text_input_loop,
            daemon=True,
            name="TextInput",
            args=(input_queue, stop_event, transcriber, input_mode)
        ),
    ]

    def shutdown(sig, frame):
        print("\n[Main] Shutting down...")
        stop_event.set()
        transcriber.stop()
        orchestrator.stop()
        executor.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[Main] Starting Sentinel...")
    for t in threads:
        t.start()

    for t in threads:
        t.join()

    print("[Main] Done.")

if __name__ == "__main__":
    main()