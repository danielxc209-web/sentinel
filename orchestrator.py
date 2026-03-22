import json
import queue
import threading
from groq import Groq
from memory import Memory
from speaker import Speaker

GROQ_MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """You are an orchestration AI and personal assistant. You have memory of past conversations and tasks.

You receive live input (transcriptions, typed messages, sensor data) and decide:

1. MODE SCORE (0.0–1.0):
   - 0.0 = pure conversation (casual chat, no action needed)
   - 0.5 = mixed (light assistance, maybe a small task)
   - 1.0 = pure command (focused task execution needed)

2. TASK (optional):
   - If mode_score >= 0.4, produce a clear task for the executor.
   - If mode_score < 0.4, leave task null.

3. REPLY (optional):
   - A short reply to the user. Use memory context to make it personal and relevant.
   - If dispatching a task, keep reply null — the executor will report back.

4. NEEDS_CONTEXT (bool):
   - true if you genuinely need more info before acting or replying.

5. SHOULD_STORE (bool):
   - false if input is noise, filler, mic artifacts, "um", "uh", random sounds.
   - true for anything worth remembering.

Always respond with valid JSON only:
{
  "mode_score": 0.0,
  "reasoning": "one sentence",
  "task": null,
  "reply": null,
  "needs_context": false,
  "context_question": null,
  "should_store": true
}"""

class Orchestrator:
    def __init__(
        self,
        input_queue: queue.Queue,
        task_queue: queue.Queue,
        result_queue: queue.Queue,
        memory: Memory,
        groq_api_key: str,
        speaker: Speaker
    ):
        self.input_queue  = input_queue
        self.task_queue   = task_queue
        self.result_queue = result_queue
        self.memory       = memory
        self.speaker      = speaker
        self.client       = Groq(api_key=groq_api_key)
        self._stop        = threading.Event()
        self.mode_score   = 0.0
        self.context_window = []

    def _add_context(self, text: str):
        self.context_window.append(text)
        if len(self.context_window) > 10:
            self.context_window.pop(0)

    def _drain_results(self):
        while not self.result_queue.empty():
            result = self.result_queue.get_nowait()
            task    = result.get("task", "")
            outcome = result.get("outcome", "")
            summary = f"Task completed: {task} | Outcome: {outcome}"
            self.memory.save_command(summary, metadata={"type": "task_result"})
            print(f"[Orchestrator] Result: {outcome[:120]}")
            threading.Thread(
                target=self.speaker.speak,
                args=(outcome,),
                daemon=True
            ).start()

    def _process(self, input_text: str):
        self._add_context(input_text)

        # Pull blended memory — this is what gives her context about you
        memory_context = self.memory.retrieve(input_text, self.mode_score)
        rolling = "\n".join(self.context_window[-5:])

        user_message = (
            f"MEMORY (what you know about the user and past interactions):\n{memory_context}\n\n"
            f"RECENT CONVERSATION:\n{rolling}\n\n"
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

        self.mode_score  = float(data.get("mode_score", 0.0))
        reasoning        = data.get("reasoning", "")
        task             = data.get("task")
        reply            = data.get("reply")
        needs_context    = data.get("needs_context", False)
        context_question = data.get("context_question")
        should_store     = data.get("should_store", True)

        print(f"[Orchestrator] Mode: {self.mode_score:.2f} | {reasoning}")

        if reply:
            print(f"[Orchestrator] Reply: {reply}")

        # Save to memory only if relevant
        if should_store:
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
        else:
            print(f"[Orchestrator] Skipping memory store (irrelevant)")

        # Dispatch task — memory context travels with it so executor knows who you are
        if task and self.mode_score >= 0.4:
            print(f"[Orchestrator] Dispatching task: {task[:80]}...")
            self.task_queue.put({
                "task": task,
                "mode_score": self.mode_score,
                "context": rolling,
                "memory": memory_context
            })
            return

        # Pure conversation — speak reply or ask for context
        if needs_context and context_question:
            print(f"[Orchestrator] Asking for context: {context_question}")
            threading.Thread(
                target=self.speaker.speak,
                args=(context_question,),
                daemon=True
            ).start()
        elif reply:
            threading.Thread(
                target=self.speaker.speak,
                args=(reply,),
                daemon=True
            ).start()

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