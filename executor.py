import os
import sys
import json
import queue
import threading
import importlib
import traceback
from pathlib import Path
from groq import Groq
from memory import Memory

# BUG FIX 1: "llama-3.1-70b-versatile" was deprecated/removed from Groq's API.
# The correct current model name is "llama-3.3-70b-versatile".
GROQ_MODEL = "llama-3.3-70b-versatile"
TOOLS_DIR = Path(__file__).parent / "tools"

SYSTEM_PROMPT = """You are an executor AI. You receive tasks and carry them out by writing and running Python code.

You have access to a tools/ directory of Python modules. You can:
1. USE existing tools by importing from tools/
2. CREATE new tools by writing new .py files to tools/
3. EDIT existing tools to add new capabilities
4. RUN arbitrary Python code directly

Available tool files will be listed in your context.

Always respond with valid JSON only, no markdown, no code fences:
{
  "action": "run_code" | "create_tool" | "edit_tool" | "use_tool",
  "tool_name": "optional - name of tool file (no .py)",
  "code": "the Python code to execute or write",
  "explanation": "what you're doing and why"
}

For "run_code": code is executed directly in the current process.
For "create_tool": code is written to tools/<tool_name>.py, then imported.
For "edit_tool": code overwrites tools/<tool_name>.py entirely.
For "use_tool": code calls functions from an existing tool module.

Write complete, working Python. Import what you need. Print results so they appear in the outcome.
"""

class Executor:
    def __init__(
        self,
        task_queue: queue.Queue,
        result_queue: queue.Queue,
        memory: Memory,
        groq_api_key: str
    ):
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.memory = memory
        self.client = Groq(api_key=groq_api_key)
        self._stop = threading.Event()
        TOOLS_DIR.mkdir(exist_ok=True)
        (TOOLS_DIR / "__init__.py").touch()

    def _list_tools(self) -> str:
        tools = list(TOOLS_DIR.glob("*.py"))
        if not tools:
            return "No tools yet."
        lines = []
        for t in tools:
            if t.name == "__init__.py":
                continue
            # BUG FIX 2: If a tool file is empty, splitlines()[0] raises
            # IndexError. Use a default fallback.
            first_line = t.read_text()[:200].splitlines()
            lines.append(f"- {t.stem}: {first_line[0] if first_line else '(empty)'}")
        return "\n".join(lines)

    def _write_tool(self, name: str, code: str):
        # BUG FIX 3: No validation on tool_name. A malicious or hallucinated
        # name like "../../../etc/passwd" could write outside the tools dir.
        # Sanitise to alphanumeric + underscores only.
        safe_name = "".join(c for c in name if c.isalnum() or c == "_")
        if not safe_name:
            raise ValueError(f"Invalid tool name: {name!r}")
        path = TOOLS_DIR / f"{safe_name}.py"
        path.write_text(code)
        print(f"[Executor] Wrote tool: {path}")
        return safe_name  # return the sanitised name for downstream use

    def _reload_tool(self, name: str):
        full = f"tools.{name}"
        if full in sys.modules:
            del sys.modules[full]
        # BUG FIX 4: If the tool has a syntax error, importlib.import_module()
        # raises SyntaxError or ImportError and propagates uncaught up to
        # _execute(). Catch and return a useful error string instead.
        try:
            importlib.import_module(full)
        except Exception as e:
            raise RuntimeError(f"Failed to import tool '{name}': {e}") from e

    def _run_code(self, code: str) -> str:
        """Execute code string, capture stdout-style prints via exec scope."""
        output_lines = []
        exec_globals = {
            "__builtins__": __builtins__,
            "print": lambda *a, **k: output_lines.append(" ".join(str(x) for x in a)),
            "TOOLS_DIR": TOOLS_DIR,
        }
        # BUG FIX 5: sys.path.insert() is called on every _run_code() invocation
        # without ever cleaning up, causing the path to grow indefinitely over
        # a long session. Check before inserting.
        parent = str(Path(__file__).parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        try:
            exec(compile(code, "<executor>", "exec"), exec_globals)
            return "\n".join(output_lines) if output_lines else "Code ran with no output."
        except Exception:
            return f"Error:\n{traceback.format_exc()}"

    def _plan(self, task: str, context: str, mode_score: float) -> dict:
        tools_list = self._list_tools()
        user_msg = (
            f"AVAILABLE TOOLS:\n{tools_list}\n\n"
            f"CONTEXT:\n{context}\n\n"
            f"MODE SCORE: {mode_score:.2f} (higher = more focused execution)\n\n"
            f"TASK:\n{task}"
        )
        completion = self.client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.1,
            max_tokens=2000
        )
        raw = completion.choices[0].message.content.strip()

        # BUG FIX 6: Same as Orchestrator — LLMs wrap JSON in markdown fences.
        # Strip them before parsing or json.loads() will raise.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # BUG FIX 7: json.loads() failure here propagates as an unhandled
        # exception all the way up to _process() which catches it generically.
        # Raise a more descriptive error so the log is useful.
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM returned invalid JSON: {e}\nRaw: {raw!r}") from e

    def _execute(self, plan: dict) -> str:
        action = plan.get("action")
        code = plan.get("code", "")
        tool_name = plan.get("tool_name", "")
        explanation = plan.get("explanation", "")

        print(f"[Executor] Action: {action} | {explanation[:80]}")

        # BUG FIX 8: create_tool action wrote the file then immediately ran
        # the raw code with _run_code(). But tool files typically define
        # functions and don't have a __main__ block — running the definition
        # code does nothing useful and may error. The correct behaviour is to
        # import the tool after writing it. Changed to _reload_tool().
        if action == "create_tool":
            safe_name = self._write_tool(tool_name, code)
            try:
                self._reload_tool(safe_name)
                return f"Created and imported tool '{safe_name}'."
            except RuntimeError as e:
                return str(e)

        elif action == "edit_tool":
            safe_name = self._write_tool(tool_name, code)
            try:
                self._reload_tool(safe_name)
                return f"Edited tool '{safe_name}'."
            except RuntimeError as e:
                return str(e)

        elif action in ("run_code", "use_tool"):
            return self._run_code(code)

        return f"Unknown action: {action}"

    def _process(self, item: dict):
        task = item.get("task", "")
        context = item.get("context", "")
        mode_score = item.get("mode_score", 0.5)

        print(f"[Executor] Received task: {task[:80]}...")

        try:
            plan = self._plan(task, context, mode_score)
            outcome = self._execute(plan)
        except Exception:
            outcome = f"Executor failed: {traceback.format_exc()}"

        print(f"[Executor] Outcome: {outcome[:120]}...")

        self.result_queue.put({
            "task": task,
            "outcome": outcome
        })

        self.memory.save_command(
            f"Task: {task}\nOutcome: {outcome}",
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