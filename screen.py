# Screen tool — take screenshots, read screen content
import pyautogui
import PIL.Image
from pathlib import Path
from datetime import datetime

# BUG FIX 1: pyautogui has a built-in failsafe — moving the mouse to the top-
# left corner (0,0) raises FailSafeException and aborts the program. This is
# fine for interactive use but will crash the executor mid-task silently.
# Disable it so the agent can click anywhere, OR catch the exception in click().
# Keeping failsafe ON but catching it is the safer choice.
# pyautogui.FAILSAFE = False  # ← don't do this; see click() fix below instead

# BUG FIX 2: pyautogui operations have no default pause between actions,
# which can cause clicks/types to fire faster than the OS can process them.
# A small pause makes automation more reliable.
pyautogui.PAUSE = 0.05

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def take_screenshot(label: str = "") -> str:
    """Take a screenshot, save it, return the file path."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # BUG FIX 3: If label contains slashes or special characters it would
    # corrupt the filename or create unintended subdirectories.
    safe_label = "".join(c for c in label if c.isalnum() or c in ("_", "-"))
    name = f"{ts}_{safe_label}.png" if safe_label else f"{ts}.png"
    path = SCREENSHOT_DIR / name
    img = pyautogui.screenshot()
    img.save(path)
    return str(path)

def get_screen_size() -> tuple:
    return pyautogui.size()

def click(x: int, y: int):
    # BUG FIX 4: No bounds checking. Clicking outside screen bounds raises
    # pyautogui.FailSafeException or produces an OS error. Validate first.
    width, height = pyautogui.size()
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(
            f"Click coordinates ({x}, {y}) are outside screen bounds "
            f"({width}x{height})."
        )
    try:
        pyautogui.click(x, y)
    except pyautogui.FailSafeException:
        raise RuntimeError(
            "PyAutoGUI failsafe triggered (mouse moved to corner). "
            "Action aborted for safety."
        )

def type_text(text: str):
    # BUG FIX 5: pyautogui.typewrite() only supports ASCII characters.
    # Non-ASCII (accented letters, emoji, CJK) are silently dropped.
    # Use pyperclip + hotkey paste as a fallback for non-ASCII content.
    if all(ord(c) < 128 for c in text):
        pyautogui.typewrite(text, interval=0.05)
    else:
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
        except ImportError:
            # pyperclip not installed — fall back to typewrite (lossy)
            print("[Screen] Warning: pyperclip not installed; non-ASCII chars may be dropped.")
            pyautogui.typewrite(text, interval=0.05)

def hotkey(*keys):
    # BUG FIX 6: No validation on keys. Passing an empty tuple or an invalid
    # key name causes a cryptic pyautogui error. Guard against empty input.
    if not keys:
        raise ValueError("hotkey() requires at least one key argument.")
    pyautogui.hotkey(*keys)