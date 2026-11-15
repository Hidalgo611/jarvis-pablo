"""Correo (Gmail / Outlook-Hotmail), calendario, Google Drive, notificaciones y resumen del día.

Sin APIs ni claves: Jarvis usa SU navegador, donde el usuario inicia sesión una sola vez. Gratis y sin cuotas.
"""
from __future__ import annotations

import json
import time
import urllib.parse

from .. import notifications
from ..config import settings
from ..memory import memory
from . import N, S, tool
from .browser import browser, _ddg

MAIL = {"gmail": "https://mail.google.com/mail/u/0/#inbox", "outlook": "https://outlook.live.com/mail/0/"}
CAL = {"google": "https://calendar.google.com/calendar/r/agenda", "outlook": "https://outlook.live.com/calendar/0/view/day"}
LOGIN_HINTS = ("accounts.google.com", "login.live.com", "login.microsoftonline.com", "signin", "ServiceLogin")


def _account(name: str | None, kind: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("hotmail", "outlook", "live", "microsoft")):
        return "outlook"
    if kind == "cal" and "google" not in n and "gmail" not in n and settings["calendar_account"] == "outlook":
        return "outlook"
    return "gmail" if kind == "mail" else "google"


def _open_tab(url: str):
    browser._ensure()
    page = browser.ctx.new_page()
    browser.page = page
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    browser._front(page)
    return page


def _needs_login(page) -> bool:
    return any(h in page.url for h in LOGIN_HINTS)


def _remember_account(key: str, value: str) -> None:
    lst = list(settings[key] or [])
    if value not in lst:
        lst.append(value)
        settings.set(key, lst)


def _gmail(page, unread_only: bool, n: int) -> list[dict]:
    page.wait_for_selector("tr.zA", timeout=20000)
    return page.evaluate("""([unreadOnly, n]) => [...document.querySelectorAll('tr.zA')]
        .filter(r => !unreadOnly || r.classList.contains('zE')).slice(0, n).map(r => ({
          de: (r.querySelector('.yW [name]') || r.querySelector('.yW') || {}).getAttribute ?
              (r.querySelector('.yW [name]')?.getAttribute('name') || r.querySelector('.yW')?.innerText || '') : '',
          asunto: r.querySelector('.bog')?.innerText || '',
          resumen: (r.querySelector('.y2')?.innerText || '').replace(/^\\s*-\\s*/, '').slice(0, 140),
          cuando: r.querySelector('.xW span')?.getAttribute('title') || r.querySelector('.xW')?.innerText || '',
          no_leido: r.classList.contains('zE')}))""", [unread_only, n])


def _outlook(page, unread_only: bool, n: int) -> list[dict]:
    page.wait_for_selector("div[role=option]", timeout=25000)
    labels = page.evaluate("(n) => [...document.querySelectorAll('div[role=option]')].slice(0, 40).map(e => e.getAttribute('aria-label') || e.innerText)", n)
    out = []
    for lab in labels:
        lab = " ".join((lab or "").split())
        unread = any(w in lab.lower() for w in ("no leído", "sin leer", "unread"))
        if unread_only and not unread:
            continue
        out.append({"mensaje": lab[:220], "no_leido": unread})
        if len(out) >= n:
            break
    return out


@tool("email_check", "Revisa el correo (Gmail o Hotmail/Outlook) en el navegador de Jarvis: lista los correos (remitente, asunto, resumen). Para leer uno entero después usa browser click_text + read_page.",
      {"account": S("gmail u outlook/hotmail (por defecto el que tenga configurado)"),
       "unread_only": {"type": "boolean", "description": "Solo los no leídos (por defecto sí)"},
       "max": N("Cuántos (por defecto 8)")}, [], label=lambda a: f"Revisando el correo {a.get('account') or ''}")
def email_check(account: str | None = None, unread_only: bool = True, max: float = 8) -> str:
    acc = _account(account or (settings["mail_accounts"] or ["gmail"])[0], "mail")

    def work():
        page = _open_tab(MAIL[acc])
        time.sleep(2.5)
        if _needs_login(page):
            return None
        return _gmail(page, unread_only, int(max)) if acc == "gmail" else _outlook(page, unread_only, int(max))
    try:
        items = browser.call(work, timeout=90)
    except Exception as e:
        return f"No he podido leer el correo de {acc}: {e}"
    if items is None:
        return (f"No has iniciado sesión en {acc} en mi navegador. Te lo he dejado abierto: inicia sesión una vez "
                f"y a partir de ahí podré revisarlo siempre.")
    _remember_account("mail_accounts", acc)
    if not items:
        return f"No tienes correos {'sin leer ' if unread_only else ''}en {acc}."
    return f"Correo {acc} ({len(items)}):\n" + json.dumps(items, ensure_ascii=False)


@tool("calendar_today", "Mira el calendario (Google Calendar u Outlook) en el navegador de Jarvis y devuelve los próximos eventos.",
      {"account": S("google u outlook")}, [], label="Mirando tu calendario")
def calendar_today(account: str | None = None) -> str:
    acc = _account(account, "cal")

    def work():
        page = _open_tab(CAL[acc])
        time.sleep(3.5)
        if _needs_login(page):
            return None
        return page.locator("[role=main]").first.inner_text(timeout=15000)[:2500]
    try:
        txt = browser.call(work, timeout=90)
    except Exception as e:
        return f"No he podido abrir el calendario: {e}"
    if txt is None:
        return "No has iniciado sesión en el calendario en mi navegador. Te lo he dejado abierto para que entres una vez."
    settings.set("calendar_account", acc)
    return "Calendario (texto de la vista de agenda):\n" + " ".join(txt.split())


@tool("drive_search", "Busca archivos en Google Drive (en el navegador de Jarvis) y muestra los resultados. Para abrir uno, usa browser click_text con su nombre.",
      {"query": S("Qué buscar")}, ["query"], label=lambda a: f"Buscando «{a.get('query', '')}» en Drive")
def drive_search(query: str) -> str:
    def work():
        page = _open_tab("https://drive.google.com/drive/search?q=" + urllib.parse.quote(query))
        time.sleep(4)
        if _needs_login(page):
            return None
        names = page.evaluate("""() => [...document.querySelectorAll('[data-tooltip][role=gridcell], div[data-tooltip-unhoverable] , [role=row] [data-tooltip]')]
            .map(e => e.getAttribute('data-tooltip')).filter(Boolean)""")
        if not names:
            names = [l for l in page.locator("[role=main]").first.inner_text(timeout=10000).split("\n") if l.strip()][:40]
        seen, out = set(), []
        for x in names:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out[:15]
    try:
        res = browser.call(work, timeout=90)
    except Exception as e:
        return f"No he podido buscar en Drive: {e}"
    if res is None:
        return "No has iniciado sesión en Google Drive en mi navegador. Te lo he dejado abierto para que entres una vez."
    return "Resultados en Drive:\n" + "\n".join(f"- {r}" for r in res) if res else "No he encontrado nada en Drive con eso."


@tool("notifications", "Notificaciones recientes del PC y del móvil (WhatsApp, Telegram, Discord…). Por privacidad, SOLO incluye el contenido si el usuario ha pedido explícitamente que se lo leas.",
      {"app": S("Filtrar por app (whatsapp, móvil…)"),
       "read_content": {"type": "boolean", "description": "Leer el texto de los mensajes (solo si lo ha pedido)"}},
      [], label="Revisando notificaciones")
def notifications_tool(app: str | None = None, read_content: bool = False) -> str:
    items = notifications.latest(app, 8)
    if not items:
        return "No hay notificaciones nuevas desde que arranqué."
    lines = []
    for it in items:
        when = time.strftime("%H:%M", time.localtime(it["ts"]))
        line = f"{when} {it['app']}" + (f" de {it['sender']}" if it["sender"] else "")
        if read_content and it["body"]:
            line += f": «{it['body'][:300]}»"
        lines.append(line)
    return "\n".join(lines)


@tool("daily_briefing", "Resumen del día: fecha, tiempo, agenda, correos sin leer, notificaciones, recordatorios y titulares de lo que le interesa. Úsalo para «¿qué tengo hoy?», «resumen del día», «buenos días».",
      {}, [], label="Preparando tu resumen del día")
def daily_briefing() -> str:
    out = {}
    city = settings["city"]
    if city:
        w = _ddg(f"el tiempo en {city} hoy", False, 3)
        out["tiempo"] = " | ".join(r["body"][:200] for r in w[:2])
    if settings["calendar_account"]:
        out["agenda"] = calendar_today(settings["calendar_account"])[:1200]
    if settings["mail_accounts"]:
        out["correo"] = email_check(settings["mail_accounts"][0], True, 6)[:1500]
    now = time.time()
    rem = memory.q("SELECT due, text FROM reminders WHERE done=0 AND due < ? ORDER BY due", (now + 86400,))
    out["recordatorios"] = [f"{time.strftime('%H:%M', time.localtime(d))} {t}" for d, t in rem]
    out["notificaciones"] = notifications_tool()
    interests = [f for f in memory.facts() if any(k in f.lower() for k in ("equipo", "fan", "le gusta", "le encanta", "sigue"))][:3]
    news = _ddg("noticias de hoy España", True, 4)
    out["titulares"] = [r["title"] for r in news[:4]]
    for f in interests:
        nn = _ddg(f, True, 2)
        if nn:
            out.setdefault("de_tus_intereses", []).extend(r["title"] for r in nn[:2])
    if not settings["mail_accounts"] or not settings["calendar_account"] or not city:
        out["consejo"] = ("Si quiere correo/agenda/tiempo en el resumen: que inicie sesión en Gmail o Hotmail y su calendario "
                          "en tu navegador (pídeselo con email_check / calendar_today) y dime su ciudad (settings city).")
    return json.dumps(out, ensure_ascii=False)
