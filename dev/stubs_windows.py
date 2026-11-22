"""Permite importar las herramientas de Jarvis fuera de Windows (para el banco de pruebas): todo lo de Windows es falso."""
import ctypes
import sys
import types
from unittest import mock

WIN_MODULES = ["win32gui", "win32process", "win32con", "win32api", "win32event", "winerror", "win32clipboard",
               "win32com", "win32com.client", "pythoncom", "pywintypes", "pyautogui", "pycaw", "pycaw.pycaw",
               "comtypes", "comtypes.client", "sounddevice", "playwright", "playwright.sync_api", "win32timezone",
               "docx", "docx.shared", "docx.enum", "docx.enum.text", "fpdf", "trafilatura", "ddgs"]


def install() -> None:
    if not hasattr(ctypes, "windll"):
        ctypes.windll = mock.MagicMock()
        ctypes.WinDLL = mock.MagicMock()
    __import__("ctypes.wintypes")
    for name in WIN_MODULES:
        if name not in sys.modules:
            try:
                __import__(name)
                if name in ("sounddevice",):
                    raise ImportError
            except Exception:
                sys.modules[name] = mock.MagicMock(name=name)
