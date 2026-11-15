import base64, io, json, re, time, requests, sys
from PIL import Image, ImageDraw, ImageFont
W,H=1920,1080
im=Image.new("RGB",(W,H),(240,240,240)); d=ImageDraw.Draw(im)
f=ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf",28)
targets={"Aceptar":(400,300),"Cancelar":(1500,850),"Descargar informe":(960,540),"Configuración":(1700,120),"Enviar mensaje":(250,950)}
cols=[(30,120,220),(200,60,60),(40,160,90),(120,120,120),(150,80,200)]
for (t,(x,y)),c in zip(targets.items(),cols):
    w=d.textlength(t,font=f)+60; d.rounded_rectangle((x-w/2,y-35,x+w/2,y+35),12,fill=c); d.text((x-w/2+30,y-20),t,font=f,fill="white")
d.text((60,40),"Panel de control de pruebas",font=f,fill=(30,30,30))
buf=io.BytesIO(); im.save(buf,"PNG"); b64=base64.b64encode(buf.getvalue()).decode()
for model in sys.argv[1:]:
    for t,(x,y) in targets.items():
        q=f'Localiza el botón "{t}" en la imagen. Responde SOLO con JSON: {{"x": <0-1000>, "y": <0-1000>}} con el centro del botón en coordenadas normalizadas de 0 a 1000.'
        t0=time.time()
        r=requests.post("http://localhost:11434/api/chat",json={"model":model,"stream":False,"think":False,"keep_alive":"5m","options":{"num_ctx":8192,"temperature":0},
            "messages":[{"role":"user","content":q,"images":[b64]}]}).json()
        out=r["message"]["content"]; m=re.search(r'"x"\s*:\s*([\d.]+).*?"y"\s*:\s*([\d.]+)',out,re.S)
        if m:
            px,py=float(m.group(1))/1000*W,float(m.group(2))/1000*H
            print(f"{model:12s} {t:18s} pred=({px:6.0f},{py:6.0f}) real=({x},{y}) err={((px-x)**2+(py-y)**2)**.5:5.0f}px {time.time()-t0:4.1f}s")
        else: print(model,t,"??",out[:80])
