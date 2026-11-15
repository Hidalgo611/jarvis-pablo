import sys, time; sys.path.insert(0,".")
from jarvis.tools.browser import browser as b
from jarvis.experience import INTRO_SONG, INTRO_SONG_SEARCH
t=time.time(); print("start", b.call(b._music_start, INTRO_SONG, INTRO_SONG_SEARCH, timeout=60), "%.1fs"%(time.time()-t))
b.call(b._music_volume, 0.08)
for i in range(5):
    print(round(time.time()-t,1), b.call(b._music_state)); time.sleep(1)
b.call(b._music_stop); print("stopped")
