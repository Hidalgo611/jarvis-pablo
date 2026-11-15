import sys, time; sys.path.insert(0,".")
from jarvis.tools.browser import browser
def t():
    p = browser._goto("https://www.youtube.com/results?search_query=trailer+iron+man")
    time.sleep(3)
    return p.evaluate("""()=>{const d=document.querySelector('ytd-consent-bump-v2-lightbox'); if(!d) return 'no dialog';
      return [...d.querySelectorAll('button, [role=button], a')].map(b=>(b.tagName+'|'+(b.getAttribute('aria-label')||'')+'|'+(b.innerText||'').trim()).slice(0,120))}""")
print(browser.call(t))
