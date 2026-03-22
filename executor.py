import os
import re
import sys
import json
import queue
import threading
import importlib
import traceback
from pathlib import Path
from groq import Groq
from memory import Memory

GROQ_MODEL  = "llama-3.1-70b-versatile"
TOOLS_DIR   = Path(__file__).parent / "tools"
MAX_RETRIES = 3

PLANNER_PROMPT = """You are a planning AI. Break the given task into an ordered list of concrete steps.
Each step should be small enough to execute in a single block of Python code.
Steps can depend on the output of previous steps.
Use the memory context to inform your plan — it tells you about the user and past tasks.

Respond with valid JSON only:
{
  "steps": [
    {"step": 1, "description": "what this step does"},
    {"step": 2, "description": "what this step does, possibly using result of step 1"}
  ],
  "summary": "one sentence describing the overall goal"
}"""

EXECUTOR_PROMPT = """You are an executor AI. You execute a single step of a multi-step plan by writing and running Python code.

You have access to a tools/ directory of Python modules. You can:
1. USE existing tools by importing from tools/
2. CREATE new tools by writing new .py files to tools/
3. EDIT existing tools to add new capabilities
4. RUN arbitrary Python code directly

Use the memory context to personalize behavior — it tells you about the user, their preferences, and past tasks.
The output of previous steps is provided so you can chain results together.

Always respond with valid JSON only. No markdown, no code fences:
{
  "action": "run_code" | "create_tool" | "edit_tool" | "use_tool",
  "tool_name": "optional - name of tool file (no .py)",
  "code": "plain Python only, no backticks",
  "explanation": "what you're doing and why"
}"""

RETRY_PROMPT = """Your previous attempt failed with this error:

{error}

Previous code:
{code}

Fix the code and try again. Same JSON format, plain Python only."""

def _strip_code(code: str) -> str:
    code = code.strip()
    code = re.sub(r'^```(?:python|py)?\s*\n?', '', code, flags=re.IGNORECASE)
    code = re.sub(r'\n?```\s*$', '', code)
    return code.strip()

def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if match:
        raw = match.group(1)
    return json.loads(raw)

class Executor:
    def __init__(
        self,
        task_queue: queue.Queue,
        result_queue: queue.Queue,
        memory: Memory,
        groq_api_key: str
    ):
        self.task_queue   = task_queue
        self.result_queue = result_queue
        self.memory       = memory
        self.client       = Groq(api_key=groq_api_key)
        self._stop        = threading.Event()
        TOOLS_DIR.mkdir(exist_ok=True)
        (TOOLS_DIR / "__init__.py").touch()
        sys.path.insert(0, str(Path(__file__).parent))

    def _list_tools(self) -> str:
        tools = [t for t in TOOLS_DIR.glob("*.py") if t.name != "__init__.py"]
        if not tools:
            return "No tools yet."
        return "\n".join(
            f"- {t.stem}: {t.read_text()[:200].splitlines()[0]}" for t in tools
        )

    def _write_tool(self, name: str, code: str):
        path = TOOLS_DIR / f"{name}.py"
        path.write_text(code)
        print(f"[Executor] Wrote tool: {path}")

    def _reload_tool(self, name: str):
        full = f"tools.{name}"
        if full in sys.modules:
            del sys.modules[full]
        importlib.import_module(full)

    def _run_code(self, code: str) -> tuple[bool, str]:
        output_lines = []
        exec_globals = {
            "__builtins__": __builtins__,
            "print": lambda *a, **k: output_lines.append(" ".join(str(x) for x in a)),
            "TOOLS_DIR": TOOLS_DIR,
        }
        try:
            exec(compile(code, "<executor>", "exec"), exec_globals)
            out = "\n".join(output_lines) if output_lines else "Code ran with no output."
            return True, out
        except Exception:
            return False, traceback.format_exc()

    def _call_llm(self, messages: list) -> dict:
        completion = self.client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=2000
        )
        raw = completion.choices[0].message.content.strip()
        plan = _parse_json(raw)
        plan["code"] = _strip_code(plan.get("code", ""))
        return plan

    def _plan_steps(self, task: str, context: str, memory: str) -> list[dict]:
        completion = self.client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": (
                    f"MEMORY:\n{memory}\n\n"
                    f"RECENT CONTEXT:\n{context}\n\n"
                    f"TASK:\n{task}"
                )}
            ],
            temperature=0.1,
            max_tokens=1000
        )
        raw = completion.choices[0].message.content.strip()
        data = _parse_json(raw)
        steps = data.get("steps", [])
        print(f"[Executor] Plan: {len(steps)} steps")
        for s in steps:
            print(f"  Step {s['step']}: {s['description']}")
        return steps

    def _execute_plan(self, plan: dict) -> tuple[bool, str]:
        action      = plan.get("action")
        code        = plan.get("code", "")
        tool_name   = plan.get("tool_name", "")
        explanation = plan.get("explanation", "")

        print(f"[Executor] Action: {action} | {explanation[:80]}")

        if action == "create_tool":
            self._write_tool(tool_name, code)
            ok, out = self._run_code(code)
            return ok, f"Created tool '{tool_name}'. {out}"
        elif action == "edit_tool":
            self._write_tool(tool_name, code)
            self._reload_tool(tool_name)
            return True, f"Edited tool '{tool_name}'."
        elif action in ("run_code", "use_tool"):
            return self._run_code(code)

        return False, f"Unknown action: {action}"

    def _run_step(self, step: dict, context: str, memory: str, previous_results: list[str], mode_score: float) -> tuple[bool, str]:
        description = step.get("description", "")
        step_num    = step.get("step", "?")

        prev_ctx = ""
        if previous_results:
            prev_ctx = "\n\nPREVIOUS STEP RESULTS:\n" + "\n---\n".join(
                f"Step {i+1} output:\n{r}" for i, r in enumerate(previous_results)
            )

        messages = [
            {"role": "system", "content": EXECUTOR_PROMPT},
            {"role": "user", "content": (
                f"AVAILABLE TOOLS:\n{self._list_tools()}\n\n"
                f"MEMORY:\n{memory}\n\n"
                f"RECENT CONTEXT:\n{context}\n\n"
                f"MODE SCORE: {mode_score:.2f}"
                f"{prev_ctx}\n\n"
                f"CURRENT STEP {step_num}:\n{description}"
            )}
        ]

        outcome   = f"Step {step_num} failed after all retries."
        last_code = ""

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                plan       = self._call_llm(messages)
                last_code  = plan.get("code", "")
                ok, result = self._execute_plan(plan)

                if ok:
                    print(f"[Executor] Step {step_num} success (attempt {attempt}): {result[:100]}")
                    return True, result

                # Failed — feed error back so LLM can fix it
                print(f"[Executor] Step {step_num} attempt {attempt} failed: {result[:120]}")
                messages.append({"role": "assistant", "content": json.dumps(plan)})
                messages.append({"role": "user", "content": RETRY_PROMPT.format(
                    error=result, code=last_code
                )})
                outcome = result

            except Exception:
                err = traceback.format_exc()
                print(f"[Executor] Step {step_num} exception on attempt {attempt}: {err[:120]}")
                # Feed exception back too so LLM can fix it
                messages.append({"role": "user", "content": RETRY_PROMPT.format(
                    error=err, code=last_code
                )})
                outcome = err
                # Only hard-stop on last attempt
                if attempt == MAX_RETRIES:
                    break

        return False, outcome

    def _process(self, item: dict):
        task       = item.get("task", "")
        context    = item.get("context", "")
        memory     = item.get("memory", "No memory available.")
        mode_score = item.get("mode_score", 0.5)

        print(f"[Executor] Received task: {task[:80]}...")

        try:
            steps = self._plan_steps(task, context, memory)
        except Exception:
            err = traceback.format_exc()
            self.result_queue.put({"task": task, "outcome": f"Planning failed: {err}"})
            return

        if not steps:
            self.result_queue.put({"task": task, "outcome": "Planner returned no steps."})
            return

        previous_results = []
        final_outcome    = ""

        for step in steps:
            ok, result = self._run_step(step, context, memory, previous_results, mode_score)
            previous_results.append(result)
            final_outcome = result
            if not ok:
                print(f"[Executor] Aborting — step {step.get('step')} failed.")
                break

        full_log = "\n\n".join(
            f"Step {i+1}: {r}" for i, r in enumerate(previous_results)
        )
        print(f"[Executor] Done. Final: {final_outcome[:120]}")

        self.result_queue.put({"task": task, "outcome": final_outcome, "full_log": full_log})
        self.memory.save_command(
            f"Task: {task}\nOutcome: {full_log}",
            metadata={"type": "execution", "mode_score": str(mode_score)}
        )

    def run(self):
        print("[Executor] Running...")
        while not self._stop.is_set():
            try:
                item = self.task_queue.get(timeout=1.0)
                self._process(item)
            except queue.Empty:
                continue

    def stop(self):
        self._stop.set()