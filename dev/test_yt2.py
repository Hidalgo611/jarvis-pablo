import sys, time; sys.path.insert(0,".")
from jarvis import tools; tools.load_all()
import jarvis.tools.system as sysm
sysm.move_mouse_visibly = lambda x,y,duration=0.6: print("  (mouse ->",x,y,")")
from jarvis.tools.browser import browser
def t():
    p = browser._goto("https://www.youtube.com/results?search_query=trailer+iron+man")
    p.wait_for_selector("ytd-video-renderer", timeout=12000)
    el = p.locator("ytd-video-renderer a#video-title").first
    print("count", el.count(), "visible", el.is_visible(), "href", el.get_attribute("href"))
    try:
        el.click(timeout=5000); print("click ok")
    except Exception as e:
        print("click err", str(e)[:600])
    time.sleep(3)
    return p.evaluate("()=>({url:location.href, t: (document.querySelector('video')||{}).currentTime, paused:(document.querySelector('video')||{}).paused})")
print(browser.call(t))
