"""Genera el conjunto de entrenamiento de Jarvis: conversaciones con el formato EXACTO que ve el modelo en Jarvis
(prompt de sistema real, herramientas que se le ofrecen en esa frase, bloque [contexto…], notas internas) y la
respuesta ideal: llamada a herramienta con los argumentos correctos y confirmación corta, o charla natural sin
herramientas. Todo en español de España, como lo dice Pablo por voz (a veces sin puntuación ni tildes).

    python dev/entreno/generar_datos.py --n 2400        → dev/entreno/datos/jarvis_train.jsonl (+ _val.jsonl)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev"))
import stubs_windows  # noqa: E402

stubs_windows.install()
from jarvis import tools  # noqa: E402

tools.load_all()
from jarvis import brain as B  # noqa: E402
from jarvis.config import settings  # noqa: E402
from jarvis.memory import memory  # noqa: E402

R = random.Random(7)
pick = R.choice

NOMBRES = ["Pablo"] * 6 + ["Marcos", "Lucía", "Álex", "Sergio", "Marta", "Dani"]
DATOS = ["Le gusta el Barça", "Juega al Valorant por las noches", "Trabaja en un supermercado", "Estudia por las tardes",
         "Tiene un perro que se llama Toby", "Su novia se llama Laura", "Es de Sevilla", "Le gusta la música lo-fi",
         "Ve mucho a Ibai", "Va al gimnasio los lunes y jueves", "Prefiere que le hables de tú", "Le encanta el café",
         "Su mejor amigo se llama Marcos", "Odia madrugar", "Está aprendiendo a programar", "Juega al pádel los sábados"]
DIAS = B.DIAS
MESES = B.MESES


def franja(h: int) -> str:
    return ("de madrugada" if h < 6 else "por la mañana" if h < 13 else "a mediodía" if h < 15 else
            "por la tarde" if h < 21 else "de noche")


class Reloj:
    """Fecha y hora coherentes dentro de una conversación: el mismo día y avanzando unos minutos por frase."""

    def __init__(self, hour: int | None = None) -> None:
        self.dia, self.n, self.mes = pick(DIAS), R.randint(1, 28), pick(MESES)
        self.t = (R.randint(7, 23) if hour is None else hour) * 60 + R.randint(0, 50)

    def __call__(self, gap: str = "") -> str:
        h, m = divmod(min(self.t, 23 * 60 + 59), 60)
        self.now = f"{h}:{m:02d}"
        self.t += R.randint(0, 4)
        d = f"[contexto, no lo leas: {self.dia} {self.n} de {self.mes}, {h:02d}:{m:02d} ({franja(h)})"
        return d + (f". Llevabais {gap} sin hablar" if gap else "") + "]"


def voz(t: str) -> str:
    """Como llega del reconocedor de voz: a veces sin puntuación final, en minúscula o sin tildes."""
    r = R.random()
    if r < 0.25:
        t = t.rstrip(".?!¿¡")
    if r < 0.12:
        t = t.lower()
    if R.random() < 0.08:
        t = t.translate(str.maketrans("áéíóú", "aeiou"))
    return t


def call(_tool: str, **args) -> dict:
    return {"name": _tool, "arguments": args}


# Cada generador devuelve una lista de TURNOS: (lo que dice, nota interna, [pasos])
# paso = ("call", [llamadas], [resultados]) | ("say", texto)

# ---------------------------------------------------------------- sistema / volumen
def g_volumen():
    v = pick([5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100])
    u = pick([f"baja el volumen al {v}%", f"pon el volumen al {v} por ciento", f"sube el volumen al {v}",
              f"volumen al {v}", f"ponme el volumen a {v}", f"déjalo al {v} por ciento", f"el volumen al {v} %"])
    return [(u, "", [("call", [call("system", action="volume_set", value=v)], [f"Volumen al {v} %."]),
                     ("say", pick([f"Listo, al {v} %.", f"Hecho, volumen al {v} %.", f"Al {v} %, {{a}}.", f"Puesto al {v} %."]))])]


def g_volumen_rel():
    up = R.random() < 0.5
    now = R.randint(20, 70)
    new = now + 10 if up else now - 10
    u = pick(["sube el volumen", "súbelo un poco", "más alto", "sube un poco la música", "no se oye, sube"] if up else
             ["baja el volumen", "bájalo un poco", "más bajo", "baja un poco que está muy alto", "baja la música"])
    return [(u, "", [("call", [call("system", action="volume_up" if up else "volume_down")], [f"Volumen al {new} %."]),
                     ("say", pick([f"Listo, al {new} %.", f"Ahora al {new} %.", f"Hecho, {new} %."]))])]


def g_silencio():
    mute = R.random() < 0.6
    u = pick(["silencia el ordenador", "quita el sonido", "mutea", "pon el pc en silencio"] if mute else
             ["quita el silencio", "vuelve a poner el sonido", "activa el sonido"])
    return [(u, "", [("call", [call("system", action="mute" if mute else "unmute")], ["Volumen al 40 %." + (" (silenciado)" if mute else "")]),
                     ("say", pick(["Silenciado.", "Hecho, sin sonido."] if mute else ["Sonido de vuelta.", "Listo, ya suena."]))])]


def g_media():
    a, us, says = pick([("media_play_pause", ["pausa la música", "para la música", "dale al play", "reanuda la música"], ["Hecho.", "Listo."]),
                        ("media_next", ["siguiente canción", "pasa de canción", "la siguiente"], ["Siguiente.", "Ahí va la siguiente."]),
                        ("media_previous", ["la canción anterior", "vuelve a la anterior"], ["Anterior.", "Hecho."])])
    return [(pick(us), "", [("call", [call("system", action=a)], ["Hecho."]), ("say", pick(says))])]


def g_brillo():
    v = pick([20, 30, 40, 50, 60, 70, 80, 100])
    return [(pick([f"pon el brillo al {v}", f"brillo al {v} por ciento", f"baja el brillo a {v}"]), "",
             [("call", [call("system", action="brightness_set", value=v)], [f"Brillo al {v} %."]),
              ("say", pick([f"Brillo al {v} %.", f"Listo, {v} %."]))])]


def g_delicado():
    a, us = pick([("shutdown", ["apaga el ordenador", "apaga el pc"]), ("restart", ["reinicia el ordenador", "reinicia el pc"])])
    verbo = "apague" if a == "shutdown" else "reinicie"
    pre = pick([f"¿Seguro que quieres que {verbo} el ordenador? Si tienes algo sin guardar, se perderá.",
                f"¿Lo {verbo} ya, {{a}}? Guarda lo que tengas abierto antes."])
    yes = pick(["sí", "sí, hazlo", "venga dale", "sí, adelante"])
    return [(pick(us), "", [("say", pre)]),
            (yes, "", [("call", [call("system", action=a)], ["Apagando en 10 segundos." if a == "shutdown" else "Reiniciando en 10 segundos."]),
                       ("say", pick(["Hecho. Hasta luego, {a}.", "En marcha. Nos vemos en un momento."]))])]


# ---------------------------------------------------------------- programas y web
APPS = ["Spotify", "Discord", "Steam", "Word", "Excel", "WhatsApp", "Chrome", "la calculadora", "el bloc de notas",
        "Visual Studio Code", "OBS", "Epic Games", "Telegram", "el explorador de archivos"]


def g_abrir_app():
    a = pick(APPS)
    u = pick([f"abre {a}", f"ábreme {a}", f"puedes abrir {a}", f"oye abre {a}", f"lanza {a}", f"abre {a} porfa"])
    return [(u, "", [("call", [call("open_app", name=a.removeprefix("la ").removeprefix("el "))], [f"Abierto: {a}."]),
                     ("say", pick(["Ahí lo tienes.", f"{a[:1].upper() + a[1:]} abierto.", "Abierto, {a}.", "Hecho."]))])]


def g_abrir_cerrar():
    a = pick(["Spotify", "Discord", "Steam", "Word", "WhatsApp", "OBS"])
    return [(f"abre {a}", "", [("call", [call("open_app", name=a)], [f"Abierto: {a}."]), ("say", pick(["Ahí lo tienes.", "Abierto."]))]),
            (pick(["ciérralo", "vale ya ciérralo", "cierra eso", f"cierra {a}"]), "",
             [("call", [call("close_app", name=a)], [f"Cerrado: {a}."]), ("say", pick(["Cerrado.", "Listo, cerrado.", "Hecho."]))])]


def g_app_inexistente():
    a = pick(["Photoshop", "Fortnite", "Premiere", "Blender"])
    return [(f"abre {a}", "", [("call", [call("open_app", name=a)], [f"No encuentro ningún programa llamado '{a}' instalado."]),
                               ("say", pick([f"No encuentro {a} instalado en este ordenador, {{a}}.",
                                             f"Parece que {a} no está instalado. Si lo instalas, lo abro."]))])]


WEBS = [("youtube", "YouTube"), ("gmail", "Gmail"), ("netflix", "Netflix"), ("twitch", "Twitch"), ("amazon", "Amazon"),
        ("twitter", "Twitter"), ("instagram", "Instagram"), ("wallapop", "Wallapop"), ("marca.com", "Marca")]


def g_web():
    t, n = pick(WEBS)
    u = pick([f"abre {n}", f"métete en {n}", f"entra en {n}", f"ábreme {n} en el navegador", f"llévame a {n}"])
    return [(u, "", [("call", [call("open_website", target=t)], [f"Abierta: https://www.{t if '.' in t else t + '.com'}"]),
                     ("say", pick([f"{n} abierto.", "Ahí lo tienes.", "Listo."]))])]


# ---------------------------------------------------------------- YouTube
YT = ["Ibai", "música lo-fi para estudiar", "el último vídeo de AuronPlay", "la canción Despacito", "un tutorial de Python",
      "highlights del Madrid", "música relajante", "el tráiler de GTA 6", "un vídeo de gatos", "Bad Bunny", "TheGrefg",
      "la Velada del Año", "podcast de La Pija y la Quinqui", "Quevedo", "música para entrenar"]


def g_yt_play():
    q = pick(YT)
    u = pick([f"pon {q}", f"ponme {q} en youtube", f"ponme un vídeo de {q}", f"reproduce {q}", f"quiero ver {q}",
              f"pon en yutub {q}"])
    return [(u, "", [("call", [call("youtube", action="play", query=q)], [f"Reproduciendo: {q} - YouTube"]),
                     ("say", pick(["Ahí lo tienes.", "Ahí va.", "Puesto.", "Marchando, {a}."]))])]


def g_yt_fullscreen():
    q = pick(YT)
    return [(f"pon {q}", "", [("call", [call("youtube", action="play", query=q)], [f"Reproduciendo: {q} - YouTube"]),
                              ("say", "Ahí lo tienes.")]),
            (pick(["ponlo en pantalla completa", "pantalla completa", "ponlo a pantalla completa porfa", "ponlo en grande"]), "",
             [("call", [call("youtube", action="fullscreen")], ["Vídeo en pantalla completa."]),
              ("say", pick(["Pantalla completa.", "Listo, a pantalla completa.", "Hecho."]))]),
            *([(pick(["pausa", "páusalo", "para el vídeo"]), "", [("call", [call("youtube", action="play_pause")], ["Hecho."]),
                                                                  ("say", pick(["Pausado.", "En pausa."]))])] if R.random() < 0.5 else [])]


def g_yt_buscar():
    q = pick(YT)
    return [(pick([f"busca {q} en youtube", f"búscame vídeos de {q}", f"busca en youtube {q}"]), "",
             [("call", [call("youtube", action="search", query=q)], [f"Resultados en pantalla: 1. {q} (oficial) 2. Lo mejor de {q} 3. {q} en directo"]),
              ("say", pick(["Ahí tienes los resultados. Dime cuál quieres.", "Te los dejo en pantalla. ¿Cuál pongo?"]))])]


# ---------------------------------------------------------------- información
CIUDADES = ["Madrid", "Sevilla", "Barcelona", "Valencia", "Bilbao", "Málaga", "Zaragoza"]


def g_tiempo():
    c = pick(CIUDADES)
    t, mx = R.randint(6, 28), R.randint(10, 34)
    est = pick(["soleado", "nubes y claros", "lluvia débil", "cielo despejado", "nublado"])
    return [(pick([f"qué tiempo hace en {c}", f"¿va a llover hoy en {c}?", f"dime el tiempo de {c}", f"qué temperatura hace en {c}"]), "",
             [("call", [call("web_search", query=f"tiempo {c} hoy")],
               [f"1. El tiempo en {c} hoy: {t} °C, {est}, máxima {mx} °C. (aemet.es)"]),
              ("say", pick([f"Ahora mismo hay {t} grados en {c}, {est}, y llegará a {mx}.",
                            f"En {c}, {t} grados y {est}. Máxima de {mx}."]))])]


def g_noticias():
    tema = pick(["la Fórmula 1", "el precio de la luz", "inteligencia artificial", "el Real Madrid", "la economía", "videojuegos"])
    return [(pick([f"qué ha pasado hoy con {tema}", f"noticias de {tema}", f"cuéntame las últimas noticias de {tema}"]), "",
             [("call", [call("web_search", query=f"noticias {tema} hoy", news=True)],
               [f"1. Lo último sobre {tema}: anuncio importante esta mañana (elpais.com)\n2. Reacciones y análisis (20minutos.es)"]),
              ("say", f"Lo más destacado de hoy sobre {tema} es un anuncio importante de esta mañana, con bastantes reacciones. ¿Te leo algo más concreto?")])]


def g_informe():
    tema = pick(["la historia de Roma", "los coches eléctricos", "el cambio climático", "la inteligencia artificial"])
    f = pick(["pdf", "docx"])
    return [(f"búscame información sobre {tema} y hazme un {'pdf' if f == 'pdf' else 'word'}", "",
             [("call", [call("research_report", topic=tema, format=f)],
               [f"Documento creado: {tema}.{f}. El documento ya está abierto en pantalla.\nAHORA dile al usuario en voz alta, en dos frases, lo más importante de este resumen: punto clave A; punto clave B."]),
              ("say", "Ya tienes el documento abierto. Lo más importante es el punto clave A y, además, el punto clave B.")])]


def g_dia():
    return [(pick(["qué tengo hoy", "buenos días jarvis, qué tengo hoy", "dame el resumen del día"]), "",
             [("call", [call("daily_briefing")], ["Hoy: 2 correos nuevos (Amazon, banco). Agenda: dentista a las 17:00. Tiempo: 14 °C, nubes."]),
              ("say", "Tienes 2 correos nuevos, de Amazon y del banco. A las 17:00 tienes dentista, y hace 14 grados con nubes.")])]


# ---------------------------------------------------------------- recordatorios y rutinas
def g_recordatorio():
    m = pick([5, 10, 15, 20, 30, 45, 60])
    que = pick(["sacar la ropa de la lavadora", "llamar a mi madre", "apagar el horno", "tomarme la pastilla", "ir a por el pan"])
    mm = {5: "cinco", 10: "diez", 15: "quince", 20: "veinte", 30: "media hora", 45: "tres cuartos de hora", 60: "una hora"}[m]
    u = pick([f"recuérdame en {mm if m in (30, 45, 60) else mm + ' minutos'} {que}", f"avísame dentro de {m} minutos para {que}",
              f"ponme un recordatorio en {m} minutos para {que}"])
    return [(u, "", [("call", [call("set_reminder", minutes=m, text=que[:1].upper() + que[1:])], [f"Recordatorio para las 18:{m % 60:02d}: {que}"]),
                     ("say", pick([f"Hecho, te aviso en {m} minutos.", f"En {m} minutos te lo recuerdo.", "Apuntado."]))])]


def g_alarma_hora():
    h, mi = R.randint(6, 22), pick([0, 0, 30, 15])
    que = pick(["la reunión", "salir a correr", "el partido", "llamar a Laura"])
    hm = f"{h}:{mi:02d}"
    u = pick([f"recuérdame a las {h} {que}" if mi == 0 else f"recuérdame a las {hm} {que}", f"avísame a las {hm} de {que}"])
    return [(u, "", [("call", [call("set_reminder", at=f"{h:02d}:{mi:02d}", text=que[:1].upper() + que[1:])],
                      [f"Recordatorio para las {h:02d}:{mi:02d}: {que}"]),
                     ("say", pick([f"Te aviso a las {hm}.", f"Hecho, a las {hm} te lo recuerdo."]))])]


RUTINAS = [("abre Gmail", "Gmail"), ("pon música lo-fi en YouTube", "música lo-fi"), ("dime el resumen del día", "resumen del día"),
           ("abre Spotify", "Spotify"), ("abre Twitch", "Twitch"), ("baja el volumen al 20 %", "volumen bajo")]


def g_rutina():
    task, nom = pick(RUTINAS)
    h = pick([7, 8, 9, 10, 18, 21, 22])
    dias, dtxt = pick([("todos", "todos los días"), ("laborables", "de lunes a viernes"), ("finde", "los fines de semana")])
    u = pick([f"a partir de ahora {dtxt} a las {h} {task.replace('abre', 'ábreme')}",
              f"quiero que {dtxt} a las {h} {task.replace('abre', 'me abras').replace('pon', 'me pongas').replace('dime', 'me digas').replace('baja', 'bajes')}",
              f"{dtxt} a las {h} de la mañana {task}" if h < 13 else f"{dtxt} a las {h} {task}",
              f"programa que {dtxt} a las {h} {task.replace('abre', 'se abra').replace('pon ', 'suene ').replace('dime', 'me digas').replace('baja', 'se baje')}"])
    when = f"a las {h:02d}:00 ({dias} los días)" if dias == "todos" else f"a las {h:02d}:00 ({dias})"
    return [(u, "", [("call", [call("create_routine", name=f"{nom.capitalize()} a las {h}", trigger_type="time", task=task,
                                    time=f"{h:02d}:00", days=dias)],
                      [f"Rutina «{nom.capitalize()} a las {h}» creada: se ejecutará {when}. Confírmaselo así, en una frase: «Hecho, {when} me encargo de {task}.»"]),
                     ("say", pick([f"Hecho, {dtxt} a las {h}:00 me encargo de eso.", f"Apuntado: {dtxt} a las {h}:00, {task.split(' en ')[0]}."]))])]


def g_rutina_sin_hora():
    task, nom = pick(RUTINAS)
    return [(pick([f"a partir de ahora todas las mañanas {task}", f"cada día {task}"]), "",
             [("say", pick(["¿A qué hora quieres que lo haga?", "Perfecto. ¿A qué hora?"]))]),
            *[(hu, "", [("call", [call("create_routine", name=f"{nom.capitalize()} por la mañana", trigger_type="time", task=task,
                                       time=ht, days="todos")],
                          [f"Rutina creada: se ejecutará a las {ht} (todos los días)."]),
                         ("say", f"Hecho, cada mañana a las {ht.lstrip('0')} me encargo.")])
              for hu, ht in [pick([("a las 8", "08:00"), ("sobre las nueve", "09:00"), ("a las 10 de la mañana", "10:00")])]]]


def g_rutina_frase():
    frase = pick(["modo estudio", "me voy a dormir", "modo gaming", "buenas noches jarvis"])
    task = {"modo estudio": "pon música lo-fi y baja el volumen al 30 %", "me voy a dormir": "baja el volumen al 0 % y apaga la pantalla",
            "modo gaming": "abre Discord y Steam", "buenas noches jarvis": "pon el modo oscuro y baja el brillo al 20 %"}[frase]
    return [(f"cuando diga {frase} quiero que {task.replace('pon ', 'pongas ').replace('baja', 'bajes').replace('abre', 'abras').replace('apaga', 'apagues')}", "",
             [("call", [call("create_routine", name=frase.capitalize(), trigger_type="phrase", phrase=frase, task=task)],
               [f"Rutina «{frase.capitalize()}» creada: se ejecutará cuando digas «{frase}»."]),
              ("say", f"Hecho. Cuando digas «{frase}», me encargo.")])]


def g_rutinas_lista():
    return [(pick(["qué rutinas tengo", "dime mis rutinas", "qué automatizaciones tienes puestas"]), "",
             [("call", [call("list_routines")], ["1. Gmail a las 9 — a las 09:00 (todos) (activa)\n2. Modo estudio — al decir «modo estudio» (activa)"]),
              ("say", "Tienes dos: abrir Gmail cada día a las 9:00 y el modo estudio cuando lo digas.")])]


def g_rutina_borrar():
    return [(pick(["quita la rutina de gmail", "borra la rutina de las 9", "ya no quiero que abras gmail por las mañanas"]), "",
             [("call", [call("delete_routine", name="Gmail")], ["Borradas: Gmail a las 9"]), ("say", pick(["Quitada.", "Hecho, ya no la haré."]))])]


# ---------------------------------------------------------------- memoria
def g_recordar_dato():
    dato, frase = pick([("Trabaja en Mercadona", "trabajo en mercadona"), ("Su novia se llama Laura", "mi novia se llama Laura"),
                        ("Es de Cádiz", "soy de Cádiz"), ("Le encanta el sushi", "me encanta el sushi"),
                        ("Tiene un gato llamado Simba", "tengo un gato que se llama Simba"),
                        ("Estudia Ingeniería Informática", "estudio ingeniería informática"),
                        ("Su cumpleaños es el 12 de marzo", "mi cumple es el 12 de marzo")])
    reac = {"Trabaja en Mercadona": "Apuntado, {a}. Mucho ánimo con los turnos.", "Su novia se llama Laura": "Laura, apuntado.",
            "Es de Cádiz": "¡De Cádiz! Apuntado.", "Le encanta el sushi": "Buen gusto. Lo apunto.",
            "Tiene un gato llamado Simba": "Simba, buen nombre. Apuntado.",
            "Estudia Ingeniería Informática": "Apuntado. Si necesitas ayuda con algo de la carrera, aquí estoy.",
            "Su cumpleaños es el 12 de marzo": "12 de marzo, apuntado. Ese día no se me olvida."}[dato]
    return [(pick([f"oye que sepas que {frase}", frase, f"por cierto {frase}"]), "",
             [("call", [call("remember", fact=dato)], ["Guardado en memoria."]), ("say", reac)])]


def g_corregir_dato():
    viejo, nuevo, u = pick([("Trabaja en un supermercado", "Trabaja en una oficina", "no, ya no trabajo en el súper, ahora trabajo en una oficina"),
                            ("Es de Sevilla", "Es de Málaga", "que no soy de Sevilla, soy de Málaga"),
                            ("Su novia se llama Laura", "Su novia se llama Carla", "mi novia no se llama Laura, se llama Carla")])
    return [(u, B.understand(u), [("call", [call("remember", fact=nuevo, replaces=viejo)], ["Guardado en memoria (y corregido el dato anterior)."]),
                                  ("say", pick(["Perdona, corregido.", "Tienes razón, ya lo he corregido."]))])], [viejo]


def g_olvidar():
    dato, u = pick([("Le gusta el Barça", "olvida lo del barça, ya no me gusta el fútbol"),
                    ("Juega al Valorant por las noches", "olvídate de lo del valorant"),
                    ("Su novia se llama Laura", "olvida lo de mi novia")])
    return [(u, "", [("call", [call("forget", fact=dato)], ["Olvidados 1 datos."]), ("say", pick(["Olvidado.", "Hecho, borrado de mi memoria."]))])], [dato]


def g_nombre():
    mal = pick(["Antonio", "Juan", "Carlos", "Pedro"])
    u = pick([f"no, que me llamo {{u}}, no {mal}", f"que no me llamo {mal}, me llamo {{u}}", "me llamo {u}", f"no soy {mal}, me llamo {{u}}"])
    # (el saludo con el nombre equivocado no se incluye: con el prompt ya corregido, enseñaría a llamarle mal)
    return [(u.replace("{u}", "{u}"), "NAME", [("say", pick(["Perdona, {u}. No se me vuelve a olvidar.", "Perdona, {u}. Tienes razón, apuntado."]))]),
            *([("cómo me llamo", "", [("say", "Te llamas {u}.")])] if R.random() < 0.6 else [])]


def g_recuerdo():
    plan, hora, preg, resp = pick([
        ("esta tarde tengo examen de matemáticas a las 6", "", "a qué hora te dije que tenía el examen", "A las 6 de la tarde."),
        ("mañana tengo médico a las 10", "", "oye a qué hora tenía el médico", "Mañana a las 10."),
        ("he quedado con Marcos a las 9 para cenar", "", "con quién te dije que había quedado", "Con Marcos, a las 9, para cenar."),
        ("el viernes tengo la entrevista de trabajo", "", "qué día era la entrevista", "El viernes."),
        ("he aparcado el coche en la calle Feria", "", "dónde te dije que había aparcado", "En la calle Feria.")])
    first = pick(["Mucha suerte, {a}. Seguro que va bien.", "Apuntado en mi cabeza. Que vaya bien.", "Vale, lo tengo presente."])
    mid = pick([("abre spotify", [("call", [call("open_app", name="Spotify")], ["Abierto: Spotify."]), ("say", "Ahí lo tienes.")]),
                ("qué hora es", [("say", "Son las {hora}.")]),
                ("sube el volumen", [("call", [call("system", action="volume_up")], ["Volumen al 60 %."]), ("say", "Al 60 %.")])])
    return [(plan, "", [("say", first)]), (mid[0], "", mid[1]), (preg, "RECALL", [("say", resp)])]


# ---------------------------------------------------------------- charla (sin herramientas, sin cambiar de tema)
CHARLA = [
    ("me voy al trabajo", ["Que vaya bien el turno, {a}. Aquí estaré cuando vuelvas.", "Ánimo con el curro, {a}. Hasta luego."]),
    ("me voy a clase", ["Que vaya bien la clase. Aquí te espero.", "Venga, a por ello. Hasta luego, {a}."]),
    ("estoy cansado", ["Normal después de un día así. Descansa un rato, {a}.", "Pues toca descansar. Si necesitas algo, aquí estoy."]),
    ("estoy reventado del gimnasio", ["Eso es que has entrenado bien. Toca descansar y beber agua.", "Buen trabajo, {a}. Descansa, que te lo has ganado."]),
    ("qué tal estás", ["Funcionando al cien por cien, {a}. ¿Y tú qué tal?", "Muy bien, gracias. ¿Qué tal tu día?"]),
    ("gracias jarvis", ["A ti, {a}.", "Para eso estoy."]),
    ("eres un crack", ["Hago lo que puedo, {a}.", "Me lo apunto en el currículum."]),
    ("ya he vuelto", ["Bienvenido, {a}. ¿Qué tal ha ido?", "¡Hola de nuevo! ¿Qué tal todo?"]),
    ("hoy ha sido un día de mierda", ["Vaya, lo siento, {a}. ¿Quieres contarme qué ha pasado?", "Lo siento. Si quieres hablarlo, aquí estoy."]),
    ("he aprobado el examen", ["¡Enhorabuena, {a}! Sabía que lo sacarías.", "¡Qué bien! Te lo has currado."]),
    ("me aburro", ["Podría ponerte algo en YouTube o una partida rápida. Tú dirás.", "¿Te pongo algo de música o un vídeo?"]),
    ("me voy a dormir", ["Buenas noches, {a}. Que descanses.", "Descansa, {a}. Mañana más."]),
    ("cuéntame un chiste", ["¿Por qué los programadores confunden Halloween con Navidad? Porque Oct 31 es igual a Dec 25.",
                            "Iba un tío por la calle y se cayó. Fin. Lo sé, mejoraré, {a}."]),
    ("qué hora es", ["Son las {hora}.", "Las {hora}, {a}."]),
    ("cómo te llamas", ["Jarvis, a tu servicio.", "Soy Jarvis, {a}."]),
    ("hoy tengo turno de tarde", ["Pues a aprovechar la mañana. Que vaya bien luego.", "Vale, que se te pase rápido el turno."]),
    ("voy a jugar un rato", ["Que se den bien las partidas, {a}.", "Disfruta. Aquí estaré si me necesitas."]),
    ("tengo hambre", ["Pues a por algo rico, {a}.", "Hora de comer, entonces. Que aproveche."]),
]


def g_charla():
    u, rs = pick(CHARLA)
    h = R.randint(8, 23)
    return [(u, "", [("say", pick(rs))])], [], h


def g_charla_seguida():
    """Dos frases seguidas de charla: la segunda sigue el MISMO tema (nada de sacar el fútbol)."""
    a, b = pick([(("me voy al trabajo", "Que vaya bien el turno, {a}."), ("sí, hoy salgo tarde", "Vaya, pues ánimo. Luego me cuentas.")),
                 (("estoy cansado", "Normal, ha sido un día largo."), ("sí, he dormido fatal", "Pues esta noche, a la cama pronto. Te vendrá bien.")),
                 (("he quedado con Laura", "¡Qué bien! Pasadlo genial."), ("vamos al cine", "Buen plan. Que os guste la peli.")),
                 (("ya he vuelto del gimnasio", "¡Bien hecho, {a}! ¿Qué tal ha ido?"), ("bien, hoy pierna", "Mañana vas a notar las escaleras. Buen trabajo."))])
    return [(a[0], "", [("say", a[1])]), (b[0], "", [("say", b[1])])], ["Le gusta el Barça", "Juega al Valorant por las noches"]


# ---------------------------------------------------------------- interrupción y correcciones
def g_interrumpido():
    dicho = pick(["Por cierto, ¿has visto el último resultado del Barça?", "Te cuento: en Venus un día dura más que un año",
                  "Hoy hace un día estupendo para salir a"])
    u, steps = pick([("no para, pon música relajante", [("call", [call("youtube", action="play", query="música relajante")], ["Reproduciendo: música relajante - YouTube"]),
                                                      ("say", "Perdona. Ahí va música relajante.")]),
                     ("no, calla un momento", [("say", "Perdona. Te escucho.")]),
                     ("espera espera, abre discord", [("call", [call("open_app", name="Discord")], ["Abierto: Discord."]), ("say", "Perdona. Discord abierto.")]),
                     ("no, eso no me interesa", [("say", "Entendido, lo dejo. Dime.")])])
    return [(u, "INTERRUPT:" + dicho, steps)]


def g_corrige_accion():
    q1, q2 = pick([("música relajante", "música para entrenar"), ("Ibai", "AuronPlay"), ("lo-fi", "jazz")])
    return [(f"pon {q1}", "", [("call", [call("youtube", action="play", query=q1)], [f"Reproduciendo: {q1} - YouTube"]), ("say", "Ahí lo tienes.")]),
            (pick([f"no, te he dicho {q2}", f"que no, {q2}", f"no era eso, quería {q2}"]), "CORR",
             [("call", [call("youtube", action="play", query=q2)], [f"Reproduciendo: {q2} - YouTube"]), ("say", f"Perdona. Ahora sí, {q2}.")])]


def g_falla_herramienta():
    v = pick([10, 20, 30])
    return [(f"baja el volumen al {v}", "", [("call", [call("system", action="volume_set", value=v)], [f"ERROR: he intentado ponerlo al {v} % pero está al 50 %."]),
                                             ("say", f"No he podido cambiarlo, {{a}}: sigue al 50 %. Prueba con las teclas de volumen mientras lo miro.")])]


def g_multi():
    v = pick([20, 30, 40])
    return [(f"abre spotify y pon el volumen al {v}", "", [("call", [call("open_app", name="Spotify"), call("system", action="volume_set", value=v)],
                                                           ["Abierto: Spotify.", f"Volumen al {v} %."]),
                                                          ("say", f"Spotify abierto y volumen al {v} %.")])]


GENS = [(g_volumen, 8), (g_volumen_rel, 5), (g_silencio, 2), (g_media, 3), (g_brillo, 2), (g_delicado, 2), (g_abrir_app, 7),
        (g_abrir_cerrar, 4), (g_app_inexistente, 2), (g_web, 5), (g_yt_play, 8), (g_yt_fullscreen, 5), (g_yt_buscar, 3),
        (g_tiempo, 5), (g_noticias, 3), (g_informe, 2), (g_dia, 2), (g_recordatorio, 5), (g_alarma_hora, 2), (g_rutina, 7),
        (g_rutina_sin_hora, 2), (g_rutina_frase, 3), (g_rutinas_lista, 2), (g_rutina_borrar, 2), (g_recordar_dato, 5),
        (g_corregir_dato, 3), (g_olvidar, 3), (g_nombre, 4), (g_recuerdo, 6), (g_charla, 14), (g_charla_seguida, 6),
        (g_interrumpido, 4), (g_corrige_accion, 4), (g_falla_herramienta, 2), (g_multi, 2)]


# ---------------------------------------------------------------- montaje en formato de chat
def build(gen) -> dict:
    out = gen()
    facts_extra, hour = [], None
    if isinstance(out, tuple):
        out, facts_extra, *rest = out
        hour = rest[0] if rest else None
    name = pick(NOMBRES)
    for tb in ("facts", "kv"):
        memory.q(f"DELETE FROM {tb}")
    settings.data["user_name"] = name
    settings.data["address"] = name
    for f in R.sample(DATOS, R.randint(0, 5)) + list(facts_extra):
        memory.add_fact(f)
    if R.random() < 0.15:
        memory.set_summary(f"{name} ha comentado que hoy tiene turno de tarde. Le pidió poner música para estudiar.")
    system = B.system_prompt()
    msgs, recent = [], []
    ctx = Reloj(hour)
    for i, (u, note, steps) in enumerate(out):
        u = voz(u.replace("{u}", name))
        notes = ""
        if note == "NAME":
            mal = re.search(r"no (soy|me llamo) (\w+)|, no (\w+)$", u)
            notes = (f"(Nota interna: se llama {name}. Ya está guardado y corregido en tu memoria: llámale {name} desde ahora "
                     f"y nunca de otra forma. Si antes le llamaste de otra manera, discúlpate en pocas palabras.)")
            del mal
        elif note == "RECALL" or note == "CORR":
            notes = B.understand(u) if note == "CORR" else (
                "(Nota interna: te pregunta por algo que te dijo antes. La respuesta está en los mensajes anteriores "
                "de esta conversación o en tus datos: búscala ahí y contéstale con ese dato exacto. Si de verdad no "
                "está, dile que no lo recuerdas.)")
        elif note.startswith("INTERRUPT:"):
            notes = B.understand(u, note.split(":", 1)[1])
        elif note:
            notes = note
        content = f"{ctx()} {u}" + (f"\n{notes}" if notes else "")
        msgs.append({"role": "user", "content": content})
        for st in steps:
            if st[0] == "call":
                calls, results = st[1], st[2]
                msgs.append({"role": "assistant", "content": "",
                             "tool_calls": [{"type": "function", "function": c} for c in calls]})
                for c, r in zip(calls, results):
                    msgs.append({"role": "tool", "name": c["name"], "content": r})
                    recent.append(c["name"])
            else:
                msgs.append({"role": "assistant", "content": st[1].replace("{a}", name).replace("{u}", name)
                             .replace("{hora}", ctx.now)})
        last_user = u
    tool_names = tools.select(last_user, recent)
    for m in msgs:   # toda herramienta usada tiene que estar en la lista ofrecida
        for c in m.get("tool_calls") or []:
            if c["function"]["name"] not in tool_names:
                tool_names.append(c["function"]["name"])
    return {"messages": [{"role": "system", "content": system}] + msgs, "tools": tools.schemas(tool_names),
            "gen": gen.__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2400)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    pool = [g for g, w in GENS for _ in range(w)]
    rows = [build(pick(pool)) for _ in range(a.n)]
    R.shuffle(rows)
    nval = max(20, a.n // 20)
    for name, part in (("jarvis_train.jsonl", rows[nval:]), ("jarvis_val.jsonl", rows[:nval])):
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"{len(rows)} conversaciones ({len(rows) - nval} entrenamiento, {nval} validación)")
    print(Counter(r["gen"] for r in rows).most_common())
    return 0


if __name__ == "__main__":
    sys.exit(main())
