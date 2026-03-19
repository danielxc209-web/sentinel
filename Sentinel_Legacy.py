import json
import os
import re
import sys
import subprocess
import traceback
from datetime import datetime
from groq import Groq

# Import twilio at top level
try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

# =========================
# CONFIG
# =========================
my_key = os.getenv("GROQ_API_KEY")
if not my_key:
    raise RuntimeError("Missing GROQ_API_KEY environment variable.")

client = Groq(api_key=my_key)

MODEL_NAME = "openai/gpt-oss-120b"
MODEL_FAST = "llama-3.1-8b-instant"  # used for repair

VERBOSE = False
MAX_FIX_RETRIES = 1

WORKSPACE_DIR = os.path.abspath("sentinel_workspace")
SUMMARY_DIR = os.path.abspath("sentinel_summaries")
os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(SUMMARY_DIR, exist_ok=True)

SAFE_PIP_ALLOWLIST = {
    "requests", "pillow", "numpy", "pandas", "matplotlib",
    "opencv-python", "beautifulsoup4", "lxml", "python-docx",
    "openpyxl", "cv2", "twilio", "yt-dlp",
}

BLOCKED_PATTERNS = []

# =========================
# TWILIO
# =========================
FROM_NUMBER = '+14472514119'
PHONE_TO    = '+15209342069'

def make_call(message_text: str):
    if not TWILIO_AVAILABLE:
        print(f"[Sentinel] twilio not installed. Message: {message_text}")
        return None
    safe = (message_text
            .replace("&", "and")
            .replace("<", "")
            .replace(">", "")
            .replace('"', "'"))
    try:
        twilio_client = Client('ACcb3703dd47c9b0422bea6073458e66f1',
                               'd1c23304a3179b7a7b2d38505e862fdc')
        call = twilio_client.calls.create(
            twiml=f'<Response><Say voice="alice">{safe}</Say></Response>',
            to=PHONE_TO,
            from_=FROM_NUMBER,
        )
        print(f"[Sentinel] Call placed: {call.sid}")
        return call.sid
    except Exception as e:
        print(f"[Sentinel] Call failed: {e}")
        return None

# =========================
# LOGGING
# =========================
def status(msg):
    print(f"[Sentinel] {msg}")

def debug(msg):
    if VERBOSE:
        print(msg)

# =========================
# SKILLS FILE
# =========================
def load_skills() -> str:
    try:
        with open(os.path.abspath("sentinel_skills.md"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "No skills file found."

# =========================
# MEMORY / SUMMARIES
# =========================
def save_summary(goal, thought, observation, files=None, status_text="ok", code=""):
    if files is None:
        files = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    summary = {
        "timestamp": timestamp,
        "goal": goal,
        "thought": thought,
        "observation": observation,
        "files": files,
        "status": status_text,
        "code": code,
    }
    filename = os.path.join(SUMMARY_DIR, f"summary_{timestamp}.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

def load_summaries(limit=8):
    summaries = []
    for fname in sorted(os.listdir(SUMMARY_DIR)):
        if fname.endswith(".json"):
            path = os.path.join(SUMMARY_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    summaries.append(json.load(f))
            except:
                pass
    return summaries[-limit:]

def summaries_to_text(limit=8):
    items = load_summaries(limit=limit)
    if not items:
        return "No prior summaries."
    lines = []
    for s in items:
        code_snippet = (s.get("code") or "").strip().splitlines()
        preview = "\n    ".join(code_snippet[:20])
        if len(code_snippet) > 20:
            preview += f"\n    ...({len(code_snippet)-20} more lines)"
        lines.append(
            f"{s.get('timestamp')} | goal={s.get('goal')} | "
            f"status={s.get('status')} | observation={s.get('observation')} | "
            f"files={s.get('files')}\n  code:\n    {preview or 'N/A'}"
        )
    return "\n".join(lines)

# =========================
# FILE / WORKSPACE HELPERS
# =========================
def list_workspace_files():
    found = []
    for root, _, files in os.walk(WORKSPACE_DIR):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, WORKSPACE_DIR)
            found.append(rel)
    return sorted(found)

def snapshot_workspace():
    files = {}
    for root, _, fs in os.walk(WORKSPACE_DIR):
        for f in fs:
            full = os.path.join(root, f)
            try:
                rel = os.path.relpath(full, WORKSPACE_DIR)
                files[rel] = os.path.getmtime(full)
            except:
                pass
    return files

def diff_workspace(before, after):
    created = [k for k in after if k not in before]
    modified = [k for k in after if k in before and after[k] != before[k]]
    return sorted(created), sorted(modified)

# =========================
# SAFETY / CODE CHECKS
# =========================
def strip_code_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        first_newline = code.find("\n")
        if first_newline != -1:
            code = code[first_newline+1:]
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()

def is_code_safe(code: str):
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, code):
            return False, f"Blocked pattern matched: {pat}"
    return True, "ok"

def detect_missing_module(err_text: str):
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", err_text)
    if m:
        return m.group(1)
    return None

def normalize_pip_name(module_name: str):
    mapping = {
        "PIL": "pillow",
        "cv2": "opencv-python",
        "bs4": "beautifulsoup4",
        "docx": "python-docx",
        "yt_dlp": "yt-dlp",
    }
    return mapping.get(module_name, module_name)

def maybe_install_package(module_name: str):
    pkg = normalize_pip_name(module_name)
    if pkg not in SAFE_PIP_ALLOWLIST:
        return False, f"Package '{pkg}' is not in SAFE_PIP_ALLOWLIST."
    status(f"Missing module detected: {module_name}")
    status(f"Install allowed package '{pkg}'? (y/n)")
    choice = input("> ").strip().lower()
    if choice != "y":
        return False, f"User declined install of {pkg}."
    try:
        status(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return True, f"Installed {pkg}."
    except Exception as e:
        return False, f"pip install failed for {pkg}: {e}"

# =========================
# JSON EXTRACTION
# =========================
def extract_json(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    cleaned = re.sub(r"^```[a-z]*\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i+1])
                    except Exception:
                        break
    return None

def safe_parse(raw: str, fallback: dict) -> dict:
    result = extract_json(raw)
    if result is not None:
        return result
    status(f"WARNING: Could not parse model JSON. Raw:\n{raw[:300]}")
    return fallback

# =========================
# RESTRICTED EXECUTION
# =========================
def run_generated_code(code: str):
    code = strip_code_fences(code)
    safe, reason = is_code_safe(code)
    if not safe:
        raise RuntimeError(f"Unsafe code blocked: {reason}")

    before = snapshot_workspace()
    captured = []

    def sentinel_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        captured.append(text)

    exec_globals = {
        "__builtins__": __builtins__,
        "WORKSPACE_DIR": WORKSPACE_DIR,
        "os": os,
        "json": json,
        "datetime": datetime,
        "subprocess": subprocess,
        "sys": sys,
        "print": sentinel_print,
        "range": range,
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "min": min,
        "max": max,
        "sum": sum,
        "abs": abs,
        "enumerate": enumerate,
        "zip": zip,
        "open": open,
        # Twilio — injected so generated code never needs to import
        "make_call": make_call,
        "Client": Client if TWILIO_AVAILABLE else None,
        "FROM_NUMBER": FROM_NUMBER,
        "PHONE_TO": PHONE_TO,
    }

    local_vars = {}
    exec(code, exec_globals, local_vars)

    after = snapshot_workspace()
    created, modified = diff_workspace(before, after)

    observation = "\n".join(captured).strip()
    if not observation:
        observation = "Code executed successfully with no printed output."

    return observation, created, modified

# =========================
# MODEL PROMPTS
# =========================
SYSTEM_PROMPT = r"""
You are Sentinel, a single-action goal-directed assistant.

TWILIO — make_call() is already available. NEVER import twilio. Just call it:
    make_call("Hello, this is Sentinel.")

For yt-dlp downloads, use subprocess since yt-dlp is a command line tool:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3", "-o",
                    os.path.join(WORKSPACE_DIR, "%(title)s.%(ext)s"), "URL"], check=True)

CRITICAL RULES:
1. Respond in VALID JSON ONLY. No markdown, no commentary outside JSON.
2. You MUST choose ONE of:
   - "finish": if the goal is complete or no action is needed
   - "code": if you need to execute exactly one Python action
3. JSON schema:

{
  "response": {
    "speak": "<optional short user-facing text, usually empty unless important>",
    "status": "done",
    "thought": "<brief reasoning summary>",
    "action": {
      "type": "finish"
    }
  }
}

OR

{
  "response": {
    "speak": "<optional short user-facing text, usually empty unless important>",
    "status": "continue",
    "thought": "<brief reasoning summary>",
    "action": {
      "type": "code",
      "language": "python",
      "code": "<python code>"
    }
  }
}

4. Single action only. Never return multiple actions.
5. Prefer solving the goal in ONE code step whenever possible.
6. Use WORKSPACE_DIR for file writes.
7. If a relevant file likely already exists, use it instead of recreating it.
8. Keep "speak" empty unless the user truly needs to know something.
9. If the goal is satisfied from memory/context alone, return finish.
10. Do NOT plan multiple future steps. One action only.
11. NEVER import twilio in generated code. Use make_call() directly.
12. Check SKILLS FILE for reusable patterns before writing new code.
"""

def build_user_prompt(goal, last_observation="", last_error=""):
    memory_text = summaries_to_text(limit=8)
    workspace_files = list_workspace_files()
    skills_text = load_skills()

    return f"""
ADMIN USER: Daniel Kibbey

GOAL:
{goal}

SKILLS FILE (reusable patterns — always read fresh):
{skills_text}

PAST MEMORY SUMMARIES:
{memory_text}

CURRENT WORKSPACE FILES:
{workspace_files}

LAST OBSERVATION:
{last_observation or "None"}

LAST ERROR:
{last_error or "None"}

Decide the single best action.
If no code is needed, return finish.
Respond ONLY in valid JSON.
"""

# =========================
# MODEL CALLS
# =========================
def ask_model(goal, last_observation="", last_error=""):
    user_prompt = build_user_prompt(goal, last_observation, last_error)

    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        stream=False
    )

    raw_text = completion.choices[0].message.content.strip()
    debug("\n--- RAW MODEL OUTPUT ---")
    debug(raw_text)

    return safe_parse(raw_text, fallback={
        "response": {
            "speak": "Model returned invalid JSON.",
            "status": "done",
            "thought": "Invalid JSON response.",
            "action": {"type": "finish"}
        }
    })

def repair_code_with_model(goal, bad_code, error_text, last_observation):
    repair_prompt = f"""
The previous Python code failed.

GOAL:
{goal}

FAILED CODE:
{bad_code}

ERROR:
{error_text}

LAST OBSERVATION:
{last_observation}

SKILLS FILE:
{load_skills()}

Return corrected JSON in the exact same schema with ONE code action or finish.
NEVER import twilio — use make_call() directly.
For yt-dlp use: subprocess.run([sys.executable, "-m", "yt_dlp", ...], check=True)
"""

    completion = client.chat.completions.create(
        model=MODEL_FAST,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": repair_prompt},
        ],
        temperature=0.0,
        stream=False
    )

    raw_text = completion.choices[0].message.content.strip()
    debug("\n--- RAW FIX OUTPUT ---")
    debug(raw_text)

    return safe_parse(raw_text, fallback={
        "response": {
            "speak": "",
            "status": "done",
            "thought": "Failed to repair invalid JSON.",
            "action": {"type": "finish"}
        }
    })

# =========================
# SINGLE EXECUTION
# =========================
def execute_code_once(goal, thought, code):
    last_observation = ""

    for attempt in range(1, MAX_FIX_RETRIES + 2):
        try:
            status("Running action...")
            debug(code)

            observation, created, modified = run_generated_code(code)
            files = sorted(set(created + modified))

            save_summary(
                goal=goal,
                thought=thought,
                observation=observation,
                files=files,
                status_text="ok",
                code=code,
            )

            status("Action complete.")
            if files:
                status(f"Files changed: {files}")
            return True, observation, "", files

        except Exception as e:
            err_text = "".join(traceback.format_exception_only(type(e), e)).strip()
            status(f"Action failed: {err_text}")

            missing = detect_missing_module(err_text)
            if missing:
                installed, msg = maybe_install_package(missing)
                status(msg)
                if installed:
                    continue

            if attempt > MAX_FIX_RETRIES:
                save_summary(
                    goal=goal,
                    thought=thought,
                    observation=err_text,
                    files=[],
                    status_text="error",
                    code=code,
                )
                return False, "", err_text, []

            status("Attempting one repair...")
            repaired = repair_code_with_model(
                goal=goal,
                bad_code=code,
                error_text=err_text,
                last_observation=last_observation
            )

            resp = repaired.get("response", {})
            action = resp.get("action", {})

            if action.get("type") != "code":
                return False, "", "Repair returned no executable code.", []

            code = action.get("code", "")
            thought = resp.get("thought", thought)

    return False, "", "Unknown execution failure.", []

# =========================
# MAIN
# =========================
def run_goal(goal: str):
    if not goal:
        status("No goal provided.")
        return

    status("Thinking...")

    data = ask_model(goal)
    resp = data.get("response", {})

    speak_text = (resp.get("speak") or "").strip()
    thought = resp.get("thought", "")
    action = resp.get("action", {})

    if speak_text:
        status(speak_text)
        make_call(speak_text)

    action_type = action.get("type")

    if action_type == "finish":
        status("Goal complete.")
        save_summary(
            goal=goal,
            thought=thought,
            observation="Goal marked complete by model.",
            files=[],
            status_text="done",
            code="",
        )
        return

    if action_type == "code" and action.get("language", "").lower() == "python":
        code = action.get("code", "")
        if not code.strip():
            status("Empty code action. Stopping.")
            return

        ok, observation, error, files = execute_code_once(goal, thought, code)

        if ok:
            status("Goal complete.")
        else:
            status("Goal failed.")
            status(error)
        return

    status("Invalid action type. Stopping.")


def main():
    goal = input("Type goal: ").strip()
    if not goal:
        status("No goal provided.")
        return
    run_goal(goal)


if __name__ == "__main__":
    main()