import json
import queue
import threading
from groq import Groq
from memory import Memory

GROQ_MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """You are an orchestration AI. You receive live input (transcriptions, sensor data, etc.) and decide two things:

1. MODE SCORE (0.0–1.0):
   - 0.0 = pure conversation (casual chat, no action needed)
   - 0.5 = mixed (light assistance, maybe a small task)
   - 1.0 = pure command (focused task execution needed)
   Score based on urgency, intent, and context.

2. TASK (optional):
   - If mode_score >= 0.4, produce a clear task description for the executor.
   - If mode_score < 0.4, leave task null.

3. REPLY (optional):
   - A short conversational reply if the user needs a response directly.
   - Keep it brief. If executing a task, focus on that instead.

Always respond with valid JSON only, no other text:
{
  "mode_score": 0.0,
  "reasoning": "one sentence explaining the score",
  "task": null,
  "reply": "optional conversational response"
}"""

class Orchestrator:
    def __init__(
        self,
        input_queue: queue.Queue,
        task_queue: queue.Queue,
        result_queue: queue.Queue,
        memory: Memory,
        groq_api_key: str
    ):
        self.input_queue = input_queue
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.memory = memory
        self.client = Groq(api_key=groq_api_key)
        self._stop = threading.Event()
        self.mode_score = 0.0
        self.context_window = []   # recent inputs for rolling context

    def _add_context(self, text: str):
        self.context_window.append(text)
        if len(self.context_window) > 10:
            self.context_window.pop(0)

    def _drain_results(self):
        while not self.result_queue.empty():
            result = self.result_queue.get_nowait()
            summary = f"Task completed: {result.get('task', '')} | Outcome: {result.get('outcome', '')}"
            self.memory.save_command(summary, metadata={"type": "task_result"})
            print(f"[Orchestrator] Result stored: {summary[:80]}...")

    def _process(self, input_text: str):
        self._add_context(input_text)

        # Pull relevant memory blended by current mode
        memory_context = self.memory.retrieve(input_text, self.mode_score)

        rolling = "\n".join(self.context_window[-5:])
        user_message = (
            f"MEMORY:\n{memory_context}\n\n"
            f"RECENT CONTEXT:\n{rolling}\n\n"
            f"NEW INPUT:\n{input_text}"
        )

        try:
            completion = self.client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.2,
                max_tokens=400
            )
            raw = completion.choices[0].message.content.strip()
            data = json.loads(raw)
        except Exception as e:
            print(f"[Orchestrator] Error: {e}")
            return

        self.mode_score = float(data.get("mode_score", 0.0))
        reasoning = data.get("reasoning", "")
        task = data.get("task")
        reply = data.get("reply")

        print(f"[Orchestrator] Mode: {self.mode_score:.2f} | {reasoning}")

        if reply:
            print(f"[Orchestrator] Reply: {reply}")

        # Save to appropriate memory store
        if self.mode_score < 0.5:
            self.memory.save_conversation(
                input_text,
                metadata={"mode_score": str(self.mode_score), "reply": reply or ""}
            )
        else:
            self.memory.save_command(
                input_text,
                metadata={"mode_score": str(self.mode_score), "task": task or ""}
            )

        if task and self.mode_score >= 0.4:
            print(f"[Orchestrator] Dispatching task: {task[:80]}...")
            self.task_queue.put({
                "task": task,
                "mode_score": self.mode_score,
                "context": rolling
            })

    def run(self):
        print("[Orchestrator] Running...")
        while not self._stop.is_set():
            self._drain_results()
            try:
                item = self.input_queue.get(timeout=1.0)
                text = item.get("text", "")
                if text:
                    self._process(text)
            except queue.Empty:
                continue

    def stop(self):
        self._stop.set()
