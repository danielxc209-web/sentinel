"""
Sentinel — multi-agent AI system
Threads: Transcriber → input_queue → Orchestrator → task_queue → Executor
                                   ← result_queue ←
"""
import os
import queue
import signal
import threading

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

    def shutdown(sig, frame):
        print("\n[Main] Shutting down...")
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
