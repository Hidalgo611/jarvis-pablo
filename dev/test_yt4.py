import sys, time; sys.path.insert(0,".")
from jarvis import tools; tools.load_all()
import jarvis.tools.system as _s; _s.move_mouse_visibly = lambda *a, **k: None
from jarvis.tools import browser as B
def t():
    p = B.browser._goto("https://www.youtube.com/@IbaiLlanos/videos"); time.sleep(4)
    return p.evaluate("""()=>{const out={}; for (const s of ['a#video-title-link','a#video-title','a.yt-lockup-metadata-view-model__title','yt-lockup-view-model','ytd-rich-item-renderer','ytd-rich-grid-media','h3 a']) out[s]=document.querySelectorAll(s).length;
      const l=document.querySelector('ytd-rich-item-renderer a[href*=watch]'); out.sample = l? (l.outerHTML.slice(0,300)) : null; 
      const h=document.querySelector('ytd-rich-item-renderer h3'); out.h3 = h? h.outerHTML.slice(0,400):null; return out}""")
print(B.browser.call(t))
print(B.youtube("play_title", query="sobreviví"))
