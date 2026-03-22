"""
Sentinel — multi-agent AI system
Threads: Transcriber → input_queue → Orchestrator → task_queue → Executor
                                   ← result_queue ←
"""
import os
import queue
import signal
import threading
import sys

from transcriber import Transcriber
from orchestrator import Orchestrator
from executor import Executor
from memory import Memory

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def main():
    if not GROQ_API_KEY:
        raise ValueError("Set GROQ_API_KEY environment variable.")

    input_queue  = queue.Queue()
    task_queue   = queue.Queue()
    result_queue = queue.Queue()

    memory = Memory(path="./chroma_db")

    transcriber  = Transcriber(input_queue, model_size="base")
    orchestrator = Orchestrator(input_queue, task_queue, result_queue, memory, GROQ_API_KEY)
    executor     = Executor(task_queue, result_queue, memory, GROQ_API_KEY)

    threads = [
        threading.Thread(target=transcriber.run,  daemon=True, name="Transcriber"),
        threading.Thread(target=orchestrator.run, daemon=True, name="Orchestrator"),
        threading.Thread(target=executor.run,     daemon=True, name="Executor"),
    ]

    # BUG FIX 1: The original shutdown() called .stop() on all agents but then
    # returned, letting the main thread fall through to the t.join() loop.
    # However, daemon threads are killed instantly when the main thread exits,
    # so the joins may never complete. Use a threading.Event to let the main
    # thread block cleanly until shutdown is confirmed.
    shutdown_event = threading.Event()

    def shutdown(sig, frame):
        print("\n[Main] Shutting down...")
        transcriber.stop()
        orchestrator.stop()
        executor.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[Main] Starting Sentinel...")
    for t in threads:
        t.start()

    # BUG FIX 2: The original called t.join() on daemon threads with no
    # timeout. If a thread hangs (e.g. Transcriber blocked on sd.wait()),
    # the process will never exit even after Ctrl+C. Use a timeout so the
    # main thread can force-exit if threads don't stop cleanly within 5s.
    shutdown_event.wait()  # block here until signal handler fires

    for t in threads:
        t.join(timeout=5.0)
        if t.is_alive():
            print(f"[Main] Warning: thread '{t.name}' did not stop cleanly.")

    # BUG FIX 3: There was no final log line confirming clean exit vs forced
    # exit, making it hard to tell from logs whether shutdown was clean.
    print("[Main] Done.")
    sys.exit(0)

if __name__ == "__main__":
    main()