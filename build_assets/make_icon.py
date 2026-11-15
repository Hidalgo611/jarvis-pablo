from PIL import Image, ImageDraw, ImageFilter
S=256; im=Image.new("RGBA",(S,S),(0,0,0,0)); d=ImageDraw.Draw(im)
for r,a in [(120,40),(100,70),(80,110)]:
    d.ellipse((S/2-r,S/2-r,S/2+r,S/2+r),fill=(64,210,255,a))
im=im.filter(ImageFilter.GaussianBlur(10)); d=ImageDraw.Draw(im)
d.ellipse((20,20,236,236),outline=(64,210,255,255),width=12)
d.ellipse((58,58,198,198),outline=(140,230,255,255),width=6)
d.ellipse((92,92,164,164),fill=(235,250,255,255))
im.save("build_assets/jarvis.ico",sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
