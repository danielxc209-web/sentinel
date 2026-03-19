# Screen tool — take screenshots, read screen content
import pyautogui
import PIL.Image
from pathlib import Path
from datetime import datetime

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def take_screenshot(label: str = "") -> str:
    """Take a screenshot, save it, return the file path."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{label}.png" if label else f"{ts}.png"
    path = SCREENSHOT_DIR / name
    img = pyautogui.screenshot()
    img.save(path)
    return str(path)

def get_screen_size() -> tuple:
    return pyautogui.size()

def click(x: int, y: int):
    pyautogui.click(x, y)

def type_text(text: str):
    pyautogui.typewrite(text, interval=0.05)

def hotkey(*keys):
    pyautogui.hotkey(*keys)
