import sys, time, subprocess; sys.path.insert(0,".")
from PySide6.QtCore import QCoreApplication
app=QCoreApplication([])
from jarvis.tools.vision import locate, capture
from PIL import ImageGrab, ImageDraw
subprocess.Popen(["calc.exe"]); time.sleep(3)
img=ImageGrab.grab(); print("pantalla", img.size)
d=ImageDraw.Draw(img)
from jarvis.tools.vision import focus; print("focus", focus("calculadora")); time.sleep(1); img=ImageGrab.grab(); d=ImageDraw.Draw(img)
for t in ["el botón 7 de la calculadora","el botón = (igual) de la calculadora","el botón + de la calculadora","el icono de la papelera de reciclaje"]:
    t0=time.time(); p=locate(t); print(t, p, "%.1fs"%(time.time()-t0))
    if p: d.ellipse((p[0]-14,p[1]-14,p[0]+14,p[1]+14),outline=(255,0,0),width=5); d.text((p[0]+16,p[1]),t[:20],fill=(255,0,0))
img.resize((img.size[0]//2,img.size[1]//2)).save("dev/locate.png")
