import json
import os
import re
import sys
import subprocess
import traceback
from datetime import datetime
from groq import Groq

# Try to import twilio — gracefully degrade if not installed yet
try:
    from twilio.rest import Client as TwilioClient
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

MODEL_NAME       = "llama-3.3-70b-versatile"   # main reasoning
MODEL_FAST       = "llama-3.1-8b-instant"          # skill curator + code repair (cheap)

VERBOSE = False
MAX_FIX_RETRIES = 1

WORKSPACE_DIR    = os.path.abspath("sentinel_workspace")
SUMMARY_DIR      = os.path.abspath("sentinel_summaries")
SKILLS_FILE      = os.path.abspath("sentinel_skills.md")

os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(SUMMARY_DIR,   exist_ok=True)

# Create skills file if it doesn't exist
if not os.path.exists(SKILLS_FILE):
    with open(SKILLS_FILE, "w", encoding="utf-8") as f:
        f.write("# Sentinel Skills\n\n")
        f.write("This file is automatically updated as Sentinel learns new techniques.\n\n")

# =========================
# TWILIO CONFIG
# =========================
TWILIO_ACCOUNT_SID = "ACcb3703dd47c9b0422bea6073458e66f1"
TWILIO_AUTH_TOKEN  = "d1c23304a3179b7a7b2d38505e862fdc"
TWILIO_FROM        = "+14472514119"
TWILIO_TO          = "+15206392982"

SAFE_PIP_ALLOWLIST = {
    "requests",
    "pillow",
    "numpy",
    "pandas",
    "matplotlib",
    "opencv-python",
    "beautifulsoup4",
    "lxml",
    "python-docx",
    "openpyxl",
    "twilio",
}

BLOCKED_PATTERNS = []

# =========================
# TWILIO CALL
# =========================
def _sanitize_twiml(text: str) -> str:
    """Strip characters that break TwiML."""
    return text.replace("&", "and").replace("<", "").replace(">", "").replace('"', "'")

def make_call(message_text: str):
    """
    Call Daniel's phone and speak message_text via Twilio TTS.
    Falls back to print-only if Twilio is not installed yet.
    """
    if not TWILIO_AVAILABLE:
        print(f"[Sentinel] (twilio not installed — run: pip install twilio) {message_text}")
        return None
    try:
        tc = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        safe_msg = _sanitize_twiml(message_text)
        twiml = f'<Response><Say voice="alice">{safe_msg}</Say></Response>'
        call = tc.calls.create(twiml=twiml, to=TWILIO_TO, from_=TWILIO_FROM)
        print(f"[Sentinel] Call placed: {call.sid}")
        return call.sid
    except Exception as e:
        print(f"[Sentinel] Call failed: {e}")
        return None

# =========================
# LOGGING
# =========================
def status(msg: str, call: bool = False):
    """
    Print status. Pass call=True to also ring Daniel via Twilio.
    """
    print(f"[Sentinel] {msg}")
    if call:
        make_call(msg)

def debug(msg):
    if VERBOSE:
        print(msg)

# =========================
# SKILLS FILE
# =========================
def load_skills() -> str:
    """Read the full skills file and return its contents."""
    try:
        with open(SKILLS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "No skills file found."

def append_skill(skill_name: str, skill_description: str, code_snippet: str = ""):
    """
    Append a new skill entry to the skills file.
    Called automatically after a successful code run.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n---\n\n## Skill: {skill_name}\n"
    entry += f"*Learned: {timestamp}*\n\n"
    entry += f"{skill_description}\n"
    if code_snippet.strip():
        entry += f"\n```python\n{code_snippet.strip()}\n```\n"
    with open(SKILLS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    status(f"Skill saved: {skill_name}")

def overwrite_skills(new_content: str):
    """
    Fully overwrite the skills file (used when model wants to reorganize/prune it).
    """
    with open(SKILLS_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)
    status("Skills file updated (full rewrite).")

def update_skill_from_model(goal: str, thought: str, code: str, observation: str):
    """
    Ask the model whether this run produced a reusable skill worth saving.
    If yes, append it. If the model wants a full rewrite, overwrite.
    """
    skill_prompt = f"""
You are Sentinel's skill curator.

A task just completed successfully.

GOAL: {goal}
THOUGHT: {thought}
CODE:
{code}

OBSERVATION (output):
{observation}

CURRENT SKILLS FILE CONTENTS:
{load_skills()}

Decide:
1. Is there a reusable technique, pattern, or approach here worth saving as a skill?
2. Should the existing skills file be reorganized/pruned?

Respond ONLY in valid JSON with this schema:

{{
  "save_skill": true,
  "skill_name": "Short descriptive name",
  "skill_description": "1-3 sentence plain-English description of what this skill does and when to use it.",
  "code_snippet": "Optional short representative code snippet (or empty string)",
  "rewrite_skills_file": false,
  "new_skills_file_content": ""
}}

If no skill is worth saving, set save_skill to false and leave other fields empty.
If the file should be rewritten, set rewrite_skills_file to true and provide full new content.
Respond ONLY in valid JSON. No markdown. No commentary.
"""

    try:
        completion = client.chat.completions.create(
            model=MODEL_FAST,
            messages=[{"role": "user", "content": skill_prompt}],
            temperature=0.0,
            stream=False,
        )
        raw = completion.choices[0].message.content.strip()
        data = extract_json(raw)
        if data is None:
            debug(f"Skill update skipped: could not parse model response.")
            return

        if data.get("rewrite_skills_file") and data.get("new_skills_file_content"):
            overwrite_skills(data["new_skills_file_content"])
        elif data.get("save_skill"):
            append_skill(
                skill_name=data.get("skill_name", "Unnamed Skill"),
                skill_description=data.get("skill_description", ""),
                code_snippet=data.get("code_snippet", ""),
            )
        else:
            debug("No new skill to save from this run.")
    except Exception as e:
        debug(f"Skill update skipped (error): {e}")

# =========================
# MEMORY / SUMMARIES
# =========================
def save_summary(goal, thought, observation, files=None, status_text="ok", code=""):
    if files is None:
        files = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    summary = {
        "timestamp":   timestamp,
        "goal":        goal,
        "thought":     thought,
        "observation": observation,
        "files":       files,
        "status":      status_text,
        "code":        code,          # ← FIX: persist generated code
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
            except Exception:
                pass
    return summaries[-limit:]

def summaries_to_text(limit=8):
    items = load_summaries(limit=limit)
    if not items:
        return "No prior summaries."
    lines = []
    for s in items:
        code_snippet = (s.get("code") or "").strip()
        code_preview = ""
        if code_snippet:
            # Show up to first 20 lines so the model can actually reuse it
            snippet_lines = code_snippet.splitlines()
            preview_lines = snippet_lines[:20]
            code_preview = "\n    ".join(preview_lines)
            if len(snippet_lines) > 20:
                code_preview += f"\n    ... ({len(snippet_lines) - 20} more lines)"
        lines.append(
            f"[{s.get('timestamp')}]\n"
            f"  goal={s.get('goal')}\n"
            f"  status={s.get('status')}\n"
            f"  observation={s.get('observation')}\n"
            f"  files={s.get('files')}\n"
            f"  code:\n    {code_preview or 'N/A'}"
        )
    return "\n\n".join(lines)

# =========================
# FILE / WORKSPACE HELPERS
# =========================
def list_workspace_files():
    found = []
    for root, _, files in os.walk(WORKSPACE_DIR):
        for f in files:
            full = os.path.join(root, f)
            rel  = os.path.relpath(full, WORKSPACE_DIR)
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
            except Exception:
                pass
    return files

def diff_workspace(before, after):
    created  = [k for k in after if k not in before]
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
            code = code[first_newline + 1:]
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
        "PIL":   "pillow",
        "cv2":   "opencv-python",
        "bs4":   "beautifulsoup4",
        "docx":  "python-docx",
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
# RESTRICTED EXECUTION
# =========================
def run_generated_code(code: str):
    code = strip_code_fences(code)

    safe, reason = is_code_safe(code)
    if not safe:
        raise RuntimeError(f"Unsafe code blocked: {reason}")

    before    = snapshot_workspace()
    captured  = []

    def sentinel_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        captured.append(text)

    exec_globals = {
        "__builtins__": __builtins__,
        "WORKSPACE_DIR": WORKSPACE_DIR,
        "os":       os,
        "json":     json,
        "datetime": datetime,
        "print":    sentinel_print,
        "range":    range,
        "len":      len,
        "str":      str,
        "int":      int,
        "float":    float,
        "bool":     bool,
        "list":     list,
        "dict":     dict,
        "set":      set,
        "tuple":    tuple,
        "min":      min,
        "max":      max,
        "sum":      sum,
        "abs":      abs,
        "enumerate":enumerate,
        "zip":      zip,
        "open":     open,
        # Twilio helper — generated code can call make_call("message") directly
        "make_call": make_call,
        "TWILIO_FROM": TWILIO_FROM,
        "TWILIO_TO":   TWILIO_TO,
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
# JSON EXTRACTION
# =========================
def extract_json(raw: str) -> dict | None:
    """
    Robustly pull a JSON object out of messy model output.
    Tries in order:
      1. Direct parse (model behaved)
      2. Strip markdown fences then parse
      3. Find the first { ... } blob and parse that
      4. Regex-scan for ALL { blobs, try each
    Returns None if everything fails.
    """
    if not raw:
        return None

    # 1. Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    cleaned = re.sub(r"^```[a-z]*\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Find outermost { ... } using brace matching
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
                    except json.JSONDecodeError:
                        break

    # 4. Scan every { position as a last resort
    for m in re.finditer(r"\{", raw):
        candidate = raw[m.start():]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None

def safe_parse(raw: str, fallback: dict) -> dict:
    """Parse model output with extract_json; return fallback on total failure."""
    result = extract_json(raw)
    if result is not None:
        return result
    status(f"WARNING: Could not parse model JSON. Raw output:\n{raw[:300]}")
    return fallback

# =========================
# MODEL PROMPTS
# =========================
SYSTEM_PROMPT = r"""
You are Sentinel, a single-action goal-directed assistant.

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
8. Use "speak" ONLY for important results Daniel needs to hear. It will trigger a phone call.
   Leave "speak" empty for routine progress — the call is reserved for real results.
9. If the goal is satisfied from memory/context alone, return finish with a speak summary.
10. Do NOT plan multiple future steps. One action only.
11. Check SKILLS FILE for reusable code patterns before writing new code from scratch.
12. In generated code, use make_call("message") instead of print() for any result
    Daniel should be notified about. make_call() is already available in the execution
    environment — do NOT import twilio yourself. Use print() only for debug/internal info.
"""

def build_user_prompt(goal, last_observation="", last_error=""):
    memory_text    = summaries_to_text(limit=8)
    workspace_files = list_workspace_files()
    skills_text    = load_skills()

    return f"""
ADMIN USER: Daniel Kibbey

GOAL:
{goal}

SKILLS FILE (reusable patterns you have learned):
{skills_text}

PAST MEMORY SUMMARIES (most recent last, includes prior code):
{memory_text}

CURRENT WORKSPACE FILES:
{workspace_files}

LAST OBSERVATION:
{last_observation or "None"}

LAST ERROR:
{last_error or "None"}

Decide the single best action.
Reuse code from SKILLS FILE or MEMORY when applicable.
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
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.0,
        stream=False,
    )

    raw_text = completion.choices[0].message.content.strip()
    debug("\n--- RAW MODEL OUTPUT ---")
    debug(raw_text)

    return safe_parse(raw_text, fallback={
        "response": {
            "speak":  "Model returned invalid JSON.",
            "status": "done",
            "thought": "Invalid JSON response.",
            "action": {"type": "finish"},
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

SKILLS FILE (reusable patterns):
{load_skills()}

Return corrected JSON in the exact same schema with ONE code action or finish.
Still solve in ONE step if possible. Check skills file for a better approach.
"""

    completion = client.chat.completions.create(
        model=MODEL_FAST,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": repair_prompt},
        ],
        temperature=0.0,
        stream=False,
    )

    raw_text = completion.choices[0].message.content.strip()
    debug("\n--- RAW FIX OUTPUT ---")
    debug(raw_text)

    return safe_parse(raw_text, fallback={
        "response": {
            "speak":  "",
            "status": "done",
            "thought": "Failed to parse repair response.",
            "action": {"type": "finish"},
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

            # ← FIX: save the actual code that succeeded
            save_summary(
                goal=goal,
                thought=thought,
                observation=observation,
                files=files,
                status_text="ok",
                code=code,
            )

            # ← NEW: update skills file from this successful run
            update_skill_from_model(goal, thought, code, observation)

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
                    code=code,   # ← also save failed code so model can learn from it
                )
                return False, "", err_text, []

            status("Attempting one repair...")
            repaired = repair_code_with_model(
                goal=goal,
                bad_code=code,
                error_text=err_text,
                last_observation=last_observation,
            )

            resp   = repaired.get("response", {})
            action = resp.get("action", {})

            if action.get("type") != "code":
                return False, "", "Repair returned no executable code.", []

            code    = action.get("code", "")
            thought = resp.get("thought", thought)

    return False, "", "Unknown execution failure.", []

# =========================
# MAIN
# =========================
def run_goal(goal: str):
    """Execute a goal string directly — called by voice listener or typed input."""
    if not goal:
        status("No goal provided.")
        return

    status("Thinking...")

    data  = ask_model(goal)
    resp  = data.get("response", {})

    speak_text  = (resp.get("speak") or "").strip()
    thought     = resp.get("thought", "")
    action      = resp.get("action", {})

    # If the model has something to say — call Daniel instead of just printing
    if speak_text:
        status(f"Speaking: {speak_text}")
        make_call(speak_text)

    action_type = action.get("type")

    if action_type == "finish":
        status("Goal complete.")
        # Call Daniel if there was a speak message (already done above),
        # or if the model finished without running code (e.g. answered from memory)
        if not speak_text:
            make_call(f"Sentinel here. Goal complete: {goal}")
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
            # Only call if model didn't already speak — avoid double-calling
            if not speak_text:
                make_call(f"Sentinel here. Goal complete: {goal}")
        else:
            status("Goal failed.")
            # Always call Daniel on failure so he knows
            make_call(f"Sentinel here. Goal failed: {goal}. Error: {error[:200]}")
        return

    status("Invalid action type. Stopping.")


def main():
    goal = input("Type goal: ").strip()
    run_goal(goal)


if __name__ == "__main__":
    main()
