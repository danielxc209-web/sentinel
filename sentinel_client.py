"""
sentinel_client.py
------------------
Terminal client for Sentinel server.

    # terminal 1
    python sentinel_server.py

    # terminal 2
    python sentinel_client.py

Type a goal, hit enter. Logs stream back in real time.
Type 'exit' or Ctrl+C to quit.
"""

import os, sys, json, socket, threading

USE_UNIX  = (os.name != "nt")
SOCK_PATH = "/tmp/sentinel.sock"
TCP_HOST  = "127.0.0.1"
TCP_PORT  = 9999

# client has no file dependencies — run it from anywhere

def connect():
    if USE_UNIX:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK_PATH)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((TCP_HOST, TCP_PORT))
    return s

def send_goal(sock, text):
    line = json.dumps({"type": "goal", "text": text}) + "\n"
    sock.sendall(line.encode())

def log_listener(sock):
    """Reads log lines from server and prints them."""
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                print("\n[Client] Server closed connection.")
                os._exit(0)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    tag = msg.get("tag", "")
                    text = msg.get("msg", "")
                    print(f"  {tag:<10} {text}")
                except Exception:
                    print(f"  {line.decode(errors='replace')}")
    except Exception as e:
        print(f"\n[Client] Disconnected: {e}")
        os._exit(1)

def main():
    print("[Client] Connecting to Sentinel server...")
    try:
        sock = connect()
    except Exception as e:
        print(f"[Client] Could not connect: {e}")
        print("[Client] Is sentinel_server.py running?")
        sys.exit(1)

    print("[Client] Connected. Type a goal and press enter. Ctrl+C to quit.\n")

    # Start log listener in background
    t = threading.Thread(target=log_listener, args=(sock,), daemon=True)
    t.start()

    try:
        while True:
            goal = input("Goal > ").strip()
            if not goal:
                continue
            if goal.lower() in ("exit", "quit"):
                break
            send_goal(sock, goal)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        sock.close()
        print("\n[Client] Bye.")

if __name__ == "__main__":
    main()
