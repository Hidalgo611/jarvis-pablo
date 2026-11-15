import sys, time; sys.path.insert(0,".")
from jarvis import notifications as N
said=[]
w=N.NotificationWatcher(said.append)
wa=b'<toast><visual><binding template="ToastGeneric"><text>Ana</text><text>Oye, esta noche cenamos?</text></binding></visual></toast>'
ph=b'<toast><visual><binding template="ToastGeneric"><text>Mam\xc3\xa1</text><text>Llama cuando puedas</text><text placement="attribution">WhatsApp \xe2\x80\xa2 iPhone</text></binding></visual></toast>'
other=b'<toast><visual><binding><text>Captura guardada</text></binding></visual></toast>'
rows=[(1,"Microsoft.ScreenSketch",other)]
w._read=lambda: rows; w._poll()
rows=rows+[(2,"5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",wa),(3,"Microsoft.YourPhone_8wekyb3d8bbwe!App",ph),(4,"Microsoft.ScreenSketch",other)]
w._poll(); w.first_pending-=10; w._poll()
print("DICE:", said)
from jarvis.tools.accounts import notifications_tool
print("sin contenido:", notifications_tool().replace("\n"," | "))
print("con contenido:", notifications_tool(read_content=True).replace("\n"," | "))
