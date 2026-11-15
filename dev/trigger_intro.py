import time, subprocess, sys, win32event
from PIL import ImageGrab
p = subprocess.Popen([sys.executable, "run_jarvis.py", "--quiet"])
time.sleep(18)
ev = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, "JarvisAssistant.Activate"); win32event.SetEvent(ev)
for t in (3, 9, 17, 26):
    time.sleep(t - (0 if t == 3 else prev)) if False else None
    prev = t
start = time.time()
for t in (3, 9, 17, 26, 40):
    while time.time() - start < t: time.sleep(0.2)
    ImageGrab.grab().resize((1280, 720)).save(f"dev/intro_{t}.png")
time.sleep(12)
p.kill()
