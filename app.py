import os, json, queue, threading, traceback, requests, subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, Response, request
from openai import OpenAI
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
AI_MODEL           = "anthropic/claude-sonnet-4-5"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEATHER_API_KEY    = os.getenv("WEATHER_API_KEY")
SEARCH_API_KEY     = os.getenv("SEARCH_API_KEY")
MEMORY_FILE        = Path("memory.json")
HOME               = Path.home()   # relative paths resolve from here

# ── Clients ────────────────────────────────────────────────────────────────────
ai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
sse_queue = queue.Queue()
app = Flask(__name__)

# ── Memory ─────────────────────────────────────────────────────────────────────
def load_memory() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {"about_user": {}, "preferences": {}, "notes": []}

def save_memory(mem: dict):
    MEMORY_FILE.write_text(json.dumps(mem, indent=2))

# ── Conversation history ───────────────────────────────────────────────────────
conversation_history: list[dict] = []

# ── Tools ──────────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_webpage",
            "description": "Fetch and read the text content of any URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Persist a fact about the user across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key":   {"type": "string"},
                    "value": {"type": "string"}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files/folders at a path. Absolute or relative to home. Defaults to home dir.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Absolute path or relative to home dir.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or fully overwrite a file. Absolute or relative to home.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Targeted find-and-replace on a file. "
                "Always read_file first so old_str matches exactly. "
                "old_str must appear exactly once in the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "old_str": {"type": "string", "description": "Exact unique string to replace"},
                    "new_str": {"type": "string", "description": "Replacement string"}
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run any shell command on the host machine as the current Linux user. "
                "Can run scripts, install packages, use git, interact with other processes, "
                "pipe data to running programs, call other APIs via curl, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd":     {"type": "string", "description": "Working dir. Defaults to home."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search",
            "description": "Search the user's Gmail inbox.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_events",
            "description": "Get upcoming Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "instagram_selenium_action",
            "description": "Interact with Instagram using Selenium. Can fetch profile details, post comments, or check recent direct messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string", 
                        "enum": ["get_profile", "post_comment", "get_messages"],
                        "description": "The action to perform."
                    },
                    "target_user": {
                        "type": "string", 
                        "description": "Instagram username to view (required for 'get_profile')."
                    },
                    "post_url": {
                        "type": "string", 
                        "description": "The direct URL of the Instagram post (required for 'post_comment')."
                    },
                    "comment_text": {
                        "type": "string", 
                        "description": "The text content of the comment to drop."
                    }
                },
                "required": ["action"]
            }
        }
    },
]

# ── Helpers ────────────────────────────────────────────────────────────────────
def _resolve(p: str) -> Path:
    """Absolute paths used as-is; relative paths anchored to home dir."""
    path = Path(p)
    return path if path.is_absolute() else (HOME / path)

# ── Tool Executors ─────────────────────────────────────────────────────────────
def execute_tool(name: str, args: dict) -> str:
    try:
        if name == "get_weather":
            if not WEATHER_API_KEY:
                return "⚠ WEATHER_API_KEY not set in .env"
            r = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": args["city"], "appid": WEATHER_API_KEY, "units": "imperial"},
                timeout=5
            )
            d = r.json()
            if r.status_code != 200:
                return f"Error: {d.get('message')}"
            return (f"{args['city']}: {d['weather'][0]['description'].capitalize()}, "
                    f"{d['main']['temp']:.0f}°F, humidity {d['main']['humidity']}%")

        elif name == "web_search":
            if not SEARCH_API_KEY:
                return "⚠ SEARCH_API_KEY not set in .env"
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": SEARCH_API_KEY},
                params={"q": args["query"], "count": 5},
                timeout=5
            )
            results = r.json().get("web", {}).get("results", [])
            if not results:
                return "No results found."
            return "\n".join(
                f"• {x['title']}: {x['url']}\n  {x.get('description','')}"
                for x in results[:5]
            )

        elif name == "browse_webpage":
            r = requests.get(args["url"], headers={"User-Agent": "SENTINEL/2.0"}, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:4000] + "\n… [truncated]" if len(text) > 4000 else text

        elif name == "update_memory":
            mem = load_memory()
            mem["about_user"][args["key"]] = args["value"]
            save_memory(mem)
            return f"Stored: {args['key']} = {args['value']}"

        elif name == "list_directory":
            target = _resolve(args["path"]) if args.get("path") else HOME
            if not target.exists():
                return f"Not found: {target}"
            items = sorted(target.iterdir())
            return "\n".join(("📁 " if p.is_dir() else "📄 ") + p.name for p in items) or "(empty)"

        elif name == "read_file":
            target = _resolve(args["path"])
            if not target.exists():
                return f"File not found: {target}"
            return target.read_text(errors="replace")

        elif name == "write_file":
            target = _resolve(args["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"])
            return f"Written: {target} ({len(args['content'])} chars)"

        elif name == "edit_file":
            target = _resolve(args["path"])
            if not target.exists():
                return f"File not found: {target}"
            content = target.read_text(errors="replace")
            old_str, new_str = args["old_str"], args["new_str"]
            count = content.count(old_str)
            if count == 0:
                return "⚠ old_str not found — no changes made."
            if count > 1:
                return f"⚠ old_str matches {count} places — must be unique. Narrow it down."
            target.write_text(content.replace(old_str, new_str, 1))
            return f"Edited: {target} (1 replacement)"

        elif name == "run_shell":
            cwd = str(_resolve(args["cwd"])) if args.get("cwd") else str(HOME)
            result = subprocess.run(
                args["command"], shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=60
            )
            parts = []
            if result.stdout.strip(): parts.append(result.stdout.strip())
            if result.stderr.strip(): parts.append(f"[stderr]\n{result.stderr.strip()}")
            return "\n".join(parts) or "(no output)"

        elif name == "gmail_search":
            return "⚠ Gmail not yet authorized. Set up Google OAuth and add credentials to .env."

        elif name == "calendar_events":
            return "⚠ Google Calendar not yet authorized. Set up Google OAuth and add credentials to .env."
        elif name == "instagram_selenium_action":
            username = os.getenv("INSTAGRAM_USERNAME")
            password = os.getenv("INSTAGRAM_PASSWORD")
            if not username or not password:
                return "⚠ INSTAGRAM_USERNAME or INSTAGRAM_PASSWORD not set in .env"

            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time

            chrome_options = Options()
            chrome_options.add_argument("--headless")  # Runs quietly in the background
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

            driver = webdriver.Chrome(options=chrome_options)
            wait = WebDriverWait(driver, 15)

            try:
                # 1. Login Sequence
                driver.get("https://www.instagram.com/accounts/login/")
                user_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
                pass_input = driver.find_element(By.NAME, "password")
                
                user_input.send_keys(username)
                pass_input.send_keys(password)
                
                driver.find_element(By.XPATH, "//button[@type='submit']").click()
                time.sleep(6) # Allow login to process and skip potential "Save Info" popups

                action = args["action"]

                # ACTION A: Get Profile Info
                if action == "get_profile":
                    target = args.get("target_user")
                    if not target: return "Error: target_user is required for get_profile"
                    
                    driver.get(f"https://www.instagram.com/{target}/")
                    time.sleep(3)
                    
                    bio_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "header section")))
                    return f"Successfully read profile for @{target}.\n\nProfile Data:\n{bio_element.text}"

                # ACTION B: Post a Comment
                elif action == "post_comment":
                    post_url = args.get("post_url")
                    comment_text = args.get("comment_text")
                    if not post_url or not comment_text: return "Error: post_url and comment_text are required."
                    
                    driver.get(post_url)
                    textarea = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "textarea[aria-label='Add a comment…']")))
                    textarea.click()
                    
                    textarea = driver.find_element(By.CSS_SELECTOR, "textarea[aria-label='Add a comment…']")
                    textarea.send_keys(comment_text)
                    
                    driver.find_element(By.XPATH, "//div[contains(text(), 'Post')]").click()
                    time.sleep(2)
                    return f"Comment successfully left on post: '{comment_text}'"

                # ACTION C: Check Direct Messages (DMs)
                elif action == "get_messages":
                    # Navigate straight to the Instagram inbox
                    driver.get("https://www.instagram.com/direct/inbox/")
                    time.sleep(5) # Give the chat list time to load render layout
                    
                    # Target the sidebar items that represent conversation rows
                    # Instagram layouts often use role="button" or specific structural divs for preview rows
                    chat_rows = driver.find_elements(By.XPATH, "//div[@role='button']//span")
                    
                    if not chat_rows:
                        # Fallback check using common structural div classes if the broad XPATH fails
                        chat_rows = driver.find_elements(By.CSS_SELECTOR, "div._ab8w._ab94._ab99._ab9f._ab9m._ab9p")
                        
                    # Extract text content from the first few found preview lines
                    message_previews = []
                    for row in chat_rows[:12]: # Grabbing top nodes to extract names & preview snippets
                        text = row.text.strip()
                        if text and text not in message_previews:
                            message_previews.append(text)
                            
                    if not message_previews:
                        return "Successfully accessed inbox, but couldn't parse any message previews. The inbox might be empty or layout changed."
                        
                    return "Recent Direct Messages / Inbox Activity:\n" + "\n".join(f"- {msg}" for msg in message_previews)

            except Exception as e:
                return f"Selenium Automation Error: {str(e)}"
            finally:
                driver.quit()
        else:
            return f"Unknown tool: {name}"

    except subprocess.TimeoutExpired:
        return "⚠ Command timed out after 60s."
    except Exception as e:
        return f"Tool error: {e}"

# ── AI Loop ────────────────────────────────────────────────────────────────────
def sentinel_loop(user_input: str):
    global conversation_history

    mem = load_memory()
    memory_block = json.dumps(mem, indent=2) if (mem["about_user"] or mem["notes"]) else "No memory yet."

    system_prompt = f"""You are SENTINEL — a sharp, capable personal AI agent for Daniel.
Concise and direct. Never sycophantic.

## Tools
- ## Tools
- get_weather, web_search, browse_webpage, instagram_selenium_action
- update_memory — persist facts about the user
- list_directory, read_file, write_file, edit_file — full filesystem (runs as this Linux user)
- run_shell — any shell command on the host; pipe data to other processes, run scripts, curl APIs, etc.
- gmail_search, calendar_events — Google (OAuth setup required)

## Skills
Save new capabilities as markdown files in ~/skills/.
When asked to "add a skill" or "learn X", write the skill file and confirm.

## File editing
Always read_file before edit_file so old_str matches exactly.

Home: {HOME}
Date/time: {datetime.now().strftime("%A %B %d %Y %H:%M")}

## Memory
{memory_block}"""

    conversation_history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": system_prompt}] + conversation_history

    try:
        while True:
            response = ai_client.chat.completions.create(
                model=AI_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            msg    = response.choices[0].message
            finish = response.choices[0].finish_reason

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": msg.tool_calls or []
            })

            if finish == "tool_calls" and msg.tool_calls:
                tool_results = []
                for tc in msg.tool_calls:
                    fn   = tc.function.name
                    args = json.loads(tc.function.arguments)
                    sse_queue.put({"type": "tool", "content": f"[{fn}] {args}"})
                    result = execute_tool(fn, args)
                    tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                messages.extend(tool_results)
                continue

            final = msg.content or "(no response)"
            conversation_history.append({"role": "assistant", "content": final})
            sse_queue.put({"type": "text", "content": final})
            break

    except Exception as e:
        err = f"SENTINEL ERROR: {e}\n{traceback.format_exc()}"
        print(err)
        sse_queue.put({"type": "text", "content": err})

# ── HTML (inlined) ─────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SENTINEL</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Ubuntu+Mono:ital,wght@0,400;0,700;1,400&family=Ubuntu:wght@300;400;500&display=swap');
  :root {
    --bg:#300a24; --bg2:#2c0920; --term:#1a0a14;
    --green:#4e9a06; --green-b:#8ae234;
    --yellow:#c4a000; --yellow-b:#fce94f;
    --blue-b:#729fcf; --red:#cc0000;
    --purple-b:#ad7fa8; --cyan-b:#34e2e2;
    --white:#d3d7cf; --white-b:#eeeeec;
    --bar:#3c1a31;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);font-family:'Ubuntu Mono',monospace;height:100vh;display:flex;flex-direction:column;overflow:hidden;color:var(--white)}
  .window-bar{background:var(--bar);height:28px;display:flex;align-items:center;padding:0 8px;gap:6px;flex-shrink:0;border-bottom:1px solid #1a0014}
  .wbtn{width:12px;height:12px;border-radius:50%;cursor:pointer}
  .wbtn.close{background:#e25252;border:1px solid #c0392b}
  .wbtn.min{background:#f0c040;border:1px solid #c9a227}
  .wbtn.max{background:#5cc05c;border:1px solid #3d9b3d}
  .window-title{flex:1;text-align:center;font-family:'Ubuntu',sans-serif;font-size:12px;color:#b08090;letter-spacing:1px}
  .tab-bar{background:var(--bg2);height:30px;display:flex;align-items:flex-end;padding:0 4px;flex-shrink:0;border-bottom:1px solid #1a0014}
  .tab{background:var(--term);border:1px solid #4a1535;border-bottom:none;padding:0 14px;height:24px;display:flex;align-items:center;gap:6px;font-size:11px;color:var(--white);border-radius:4px 4px 0 0;font-family:'Ubuntu',sans-serif}
  .tab .dot{width:8px;height:8px;border-radius:50%;background:var(--green)}
  .terminal{flex:1;background:var(--term);display:flex;flex-direction:column;overflow:hidden}
  #output{flex:1;overflow-y:auto;padding:10px 16px;font-size:13.5px;line-height:1.55;scroll-behavior:smooth}
  #output::-webkit-scrollbar{width:6px}
  #output::-webkit-scrollbar-thumb{background:#4a1535;border-radius:3px}
  .line{display:flex;margin-bottom:1px;white-space:pre-wrap;word-break:break-word}
  .sentinel-block{margin:6px 0 8px}
  .prompt-user{color:var(--green-b);font-weight:700}
  .prompt-host{color:var(--cyan-b);font-weight:700}
  .prompt-path{color:var(--blue-b)}
  .prompt-dollar{color:var(--white-b)}
  .user-text{color:var(--white-b);margin-left:4px}
  .sentinel-label{color:var(--purple-b);font-weight:700}
  .sentinel-text{color:var(--white);margin-top:2px}
  .tool-line{color:var(--yellow);font-style:italic;font-size:12px;margin:2px 0}
  .boot-line{color:var(--green);animation:fi .3s ease}
  @keyframes fi{from{opacity:0}to{opacity:1}}
  .input-row{display:flex;align-items:center;padding:8px 16px 10px;border-top:1px solid #2a0a20;background:var(--term);flex-shrink:0;gap:4px}
  .input-prompt{white-space:nowrap;font-size:13.5px;flex-shrink:0}
  #user-input{flex:1;background:transparent;border:none;outline:none;color:var(--white-b);font-family:'Ubuntu Mono',monospace;font-size:13.5px;caret-color:var(--white-b)}
  .status-bar{background:var(--bar);height:20px;display:flex;align-items:center;padding:0 12px;gap:16px;font-family:'Ubuntu',sans-serif;font-size:10px;color:#906070;flex-shrink:0;border-top:1px solid #1a0014}
  .status-item{display:flex;align-items:center;gap:4px}
  .sdot{width:6px;height:6px;border-radius:50%}
  .sdot.on{background:var(--green)}.sdot.off{background:var(--red)}
  #status-text{color:var(--cyan-b)}
  .sentinel-text code{background:#2a0a1e;color:var(--cyan-b);padding:0 4px;border-radius:2px;font-family:'Ubuntu Mono',monospace}
  .sentinel-text pre{background:#200818;border:1px solid #4a1535;border-radius:4px;padding:8px 10px;margin:6px 0;overflow-x:auto;color:var(--green-b);font-size:12.5px}
  .sentinel-text strong{color:var(--yellow-b)}
  .sentinel-text em{color:var(--purple-b);font-style:italic}
</style>
</head>
<body>
<div class="window-bar">
  <div class="wbtn close"></div><div class="wbtn min"></div><div class="wbtn max"></div>
  <div class="window-title">daniel@sentinel: ~</div>
</div>
<div class="tab-bar"><div class="tab"><div class="dot"></div>sentinel</div></div>
<div class="terminal">
  <div id="output"></div>
  <div class="input-row">
    <div class="input-prompt">
      <span class="prompt-user">daniel</span><span style="color:var(--white)">@</span><span class="prompt-host">sentinel</span><span class="prompt-path">:~</span><span class="prompt-dollar">$ </span>
    </div>
    <input id="user-input" type="text" autofocus autocomplete="off" spellcheck="false" placeholder="type a command...">
  </div>
</div>
<div class="status-bar">
  <div class="status-item"><div class="sdot on" id="conn-dot"></div><span id="status-text">connected</span></div>
  <div class="status-item">claude-sonnet-4-5</div>
  <div class="status-item" id="mem-status">memory: —</div>
  <div style="flex:1"></div>
  <div class="status-item" id="clock"></div>
</div>
<script>
const out=document.getElementById('output'),inp=document.getElementById('user-input');
['SENTINEL v2.0 — Personal Agent','Memory.................. OK','Claude.................. OK',
 'Tools: fs · shell · search · weather · gmail · calendar','─'.repeat(52),''].forEach((line,i)=>{
  setTimeout(()=>{const el=document.createElement('div');el.className='line boot-line';
    el.textContent=line;out.appendChild(el);out.scrollTop=out.scrollHeight;},i*60);});
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString();},1000);
document.getElementById('clock').textContent=new Date().toLocaleTimeString();
function refreshMem(){fetch('/memory').then(r=>r.json()).then(m=>{
  const n=Object.keys(m.about_user||{}).length;
  document.getElementById('mem-status').textContent=`memory: ${n} entr${n===1?'y':'ies'}`;}).catch(()=>{});}
refreshMem();
const es=new EventSource('/stream');
es.onmessage=e=>{
  const msg=JSON.parse(e.data);
  if(msg.type==='tool'){const el=document.createElement('div');el.className='line tool-line';
    el.textContent=msg.content;out.appendChild(el);out.scrollTop=out.scrollHeight;return;}
  if(msg.type==='text'){
    const block=document.createElement('div');block.className='sentinel-block';
    const lbl=document.createElement('div');lbl.className='line';
    lbl.innerHTML='<span class="sentinel-label">SENTINEL:</span>';
    const txt=document.createElement('div');txt.className='sentinel-text';
    txt.innerHTML=md(msg.content);
    block.appendChild(lbl);block.appendChild(txt);
    out.appendChild(block);out.scrollTop=out.scrollHeight;refreshMem();}};
es.onerror=()=>{document.getElementById('conn-dot').className='sdot off';
  document.getElementById('status-text').textContent='disconnected';};
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function md(t){return t
  .replace(/```(\\w*)\\n([\\s\\S]*?)```/g,(_,l,c)=>`<pre><code>${esc(c.trim())}</code></pre>`)
  .replace(/`([^`]+)`/g,(_,c)=>`<code>${esc(c)}</code>`)
  .replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>')
  .replace(/\\*(.+?)\\*/g,'<em>$1</em>')
  .replace(/\\n/g,'<br>');}
const hist=[];let hi=-1;
inp.addEventListener('keydown',e=>{
  if(e.key==='Enter'){const t=inp.value.trim();if(!t)return;
    hist.unshift(t);hi=-1;inp.value='';echoUser(t);
    fetch('/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});}
  if(e.key==='ArrowUp'){hi=Math.min(hi+1,hist.length-1);inp.value=hist[hi]||'';
    setTimeout(()=>inp.setSelectionRange(9999,9999),0);}
  if(e.key==='ArrowDown'){hi=Math.max(hi-1,-1);inp.value=hi===-1?'':hist[hi];}
  if(e.key==='l'&&e.ctrlKey){e.preventDefault();out.innerHTML='';}});
function echoUser(t){const el=document.createElement('div');el.className='line';
  el.innerHTML=`<span class="prompt-user">daniel</span><span style="color:var(--white)">@</span><span class="prompt-host">sentinel</span><span class="prompt-path">:~</span><span class="prompt-dollar">$</span><span class="user-text">${esc(t)}</span>`;
  out.appendChild(el);out.scrollTop=out.scrollHeight;}
</script>
</body>
</html>"""

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return HTML

@app.route("/input", methods=["POST"])
def handle_input():
    text = request.json.get("text", "").strip()
    if not text:
        return {"status": "empty"}, 400
    threading.Thread(target=sentinel_loop, args=(text,), daemon=True).start()
    return {"status": "ok"}

@app.route("/memory")
def get_memory():
    return load_memory()

@app.route("/history")
def get_history():
    return {"history": conversation_history}

@app.route("/reset", methods=["POST"])
def reset():
    global conversation_history
    conversation_history = []
    return {"status": "reset"}

@app.route("/stream")
def stream():
    def gen():
        while True:
            msg = sse_queue.get()
            yield f"data: {json.dumps(msg)}\n\n"
    return Response(gen(), mimetype="text/event-stream")

if __name__ == "__main__":
    print(f"SENTINEL online → http://localhost:5000")
    print(f"Home: {HOME}")
    app.run(host="0.0.0.0", port=5000, debug=False)