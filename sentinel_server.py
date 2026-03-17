"""
sentinel_server.py
------------------
Run once, leave it running forever:
    python sentinel_server.py

Accepts goals from any number of GUI clients over a Unix socket (Mac/Linux)
or TCP loopback (Windows). Runs them serially via sentinel.run_goal().
Streams log lines back to all connected clients in real time.

Protocol — newline-delimited JSON:
  GUI  -> server : {"type": "goal", "text": "do something"}
  server -> GUI  : {"type": "log",  "tag": "SENTINEL", "msg": "[12:00:00]  ..."}
"""

import os, sys, json, socket, threading, queue, traceback
from datetime import datetime

SENTINEL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SENTINEL_DIR)
os.chdir(SENTINEL_DIR)   # so sentinel's relative paths (workspace, summaries) work too
import sentinel as sentinel_module

# =============================================================================
# CONFIG
# =============================================================================
USE_UNIX  = (os.name != "nt")
SOCK_PATH = "/tmp/sentinel.sock"
TCP_HOST  = "127.0.0.1"
TCP_PORT  = 9999

# =============================================================================
# SHARED STATE
# =============================================================================
clients      = []
clients_lock = threading.Lock()
goal_queue   = queue.Queue()

def broadcast(tag: str, msg: str):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = json.dumps({"type": "log", "tag": tag, "msg": f"[{ts}]  {msg}"}) + "\n"
    data = line.encode()
    dead = []
    with clients_lock:
        for conn in list(clients):
            try:
                conn.sendall(data)
            except Exception:
                dead.append(conn)
        for conn in dead:
            clients.remove(conn)

# Patch sentinel.status so every log line goes to all GUI clients
_orig_status = sentinel_module.status
def _status(msg: str, call: bool = False):
    broadcast("SENTINEL", msg)
    _orig_status(msg, call)
sentinel_module.status = _status

# =============================================================================
# GOAL WORKER — single thread, goals run serially
# =============================================================================
def goal_worker():
    while True:
        goal = goal_queue.get()
        broadcast("CMD", f'Running: "{goal}"')
        try:
            sentinel_module.run_goal(goal)
        except Exception:
            broadcast("ERROR", traceback.format_exc()[:400])
        finally:
            goal_queue.task_done()

# =============================================================================
# CLIENT HANDLER
# =============================================================================
def handle_client(conn, addr):
    broadcast("SYSTEM", f"GUI connected ({addr})")
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "goal":
                    goal = (msg.get("text") or "").strip()
                    if goal:
                        broadcast("INFO", f'Queued: "{goal}"')
                        goal_queue.put(goal)
    except Exception:
        pass
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        conn.close()
        broadcast("SYSTEM", f"GUI disconnected ({addr})")

# =============================================================================
# MAIN
# =============================================================================
def main():
    threading.Thread(target=goal_worker, daemon=True).start()

    if USE_UNIX:
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK_PATH)
        addr_str = SOCK_PATH
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((TCP_HOST, TCP_PORT))
        addr_str = f"{TCP_HOST}:{TCP_PORT}"

    srv.listen(5)
    print(f"[Sentinel Server] Listening on {addr_str}")
    print(f"[Sentinel Server] Waiting for GUI...")

    try:
        while True:
            conn, addr = srv.accept()
            with clients_lock:
                clients.append(conn)
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Sentinel Server] Shutting down.")
    finally:
        srv.close()
        if USE_UNIX and os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)

if __name__ == "__main__":
    main()
