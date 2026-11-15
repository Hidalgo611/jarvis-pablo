"""Habilidades que Jarvis se escribe a sí mismo, con sistema de seguridad.

- Viven en %LOCALAPPDATA%/Jarvis/skills, nunca dentro del .exe: no pueden tocar el núcleo.
- Antes de activarse: análisis del código (AST) con lista blanca de imports y lista negra de llamadas.
- Cada versión anterior se guarda en skills/backup. Tres fallos seguidos → se desactiva sola.
- Solo pueden actuar sobre el PC a través de `jarvis_api` (que llama a herramientas existentes).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import re
import shutil
import sys
import time
import types
from pathlib import Path

from .config import SKILLS
from .tools import REGISTRY, Tool, execute

log = logging.getLogger("skills")

ALLOWED_IMPORTS = {"math", "datetime", "json", "re", "random", "time", "statistics", "itertools", "collections",
                   "string", "textwrap", "urllib.parse", "calendar", "jarvis_api"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "open", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "input", "breakpoint", "exit", "quit", "memoryview"}
STATE = SKILLS / "state.json"


class UnsafeSkill(Exception):
    pass


def check_code(code: str) -> None:
    tree = ast.parse(code)
    has_skill = has_run = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    raise UnsafeSkill(f"import no permitido: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "") not in ALLOWED_IMPORTS:
                raise UnsafeSkill(f"import no permitido: {node.module}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise UnsafeSkill(f"uso no permitido: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeSkill(f"atributo no permitido: {node.attr}")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SKILL":
                    has_skill = True
        elif isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
    if not (has_skill and has_run):
        raise UnsafeSkill("falta SKILL = {...} o def run(**kwargs)")


def _make_api() -> types.ModuleType:
    api = types.ModuleType("jarvis_api")

    def call(tool_name: str, **kwargs) -> str:
        t = REGISTRY.get(tool_name)
        if not t or not t.core or tool_name in ("create_skill", "undo_skill", "check_updates", "system"):
            return f"herramienta no permitida: {tool_name}"
        return execute(tool_name, kwargs)

    api.call = call
    api.open_app = lambda name: call("open_app", name=name)
    api.open_website = lambda target: call("open_website", target=target)
    api.search = lambda query: call("web_search", query=query)
    api.write_document = lambda title, content, format="docx": call("write_document", title=title, content=content, format=format)
    api.now = time.time
    return api


class SkillManager:
    def __init__(self) -> None:
        (SKILLS / "backup").mkdir(parents=True, exist_ok=True)
        self.state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        sys.modules["jarvis_api"] = _make_api()
        self.load_all()

    def _save(self) -> None:
        STATE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_all(self) -> None:
        for f in SKILLS.glob("*.py"):
            st = self.state.get(f.stem, {})
            if st.get("enabled", True):
                try:
                    self._load(f)
                except Exception:
                    log.exception("habilidad %s no cargada", f.stem)

    def _load(self, f: Path) -> str:
        code = f.read_text(encoding="utf-8")
        check_code(code)
        spec = importlib.util.spec_from_file_location(f"skill_{f.stem}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        meta = mod.SKILL
        name = "skill_" + f.stem
        if name in REGISTRY and REGISTRY[name].core:
            raise UnsafeSkill("no puede sustituir a una herramienta del núcleo")
        params = meta.get("parameters", {}) or {}

        def runner(_mod=mod, _stem=f.stem, **kw):
            try:
                out = _mod.run(**kw)
                self.state.setdefault(_stem, {})["fails"] = 0
                self._save()
                return str(out)
            except Exception as e:
                s = self.state.setdefault(_stem, {})
                s["fails"] = s.get("fails", 0) + 1
                if s["fails"] >= 3:
                    s["enabled"] = False
                    REGISTRY.pop(name, None)
                self._save()
                return f"ERROR en la habilidad: {e}"

        REGISTRY[name] = Tool(name, "[habilidad propia] " + meta.get("description", ""), params,
                              list(params.keys())[:0], runner, lambda a, n=meta.get("name", f.stem): f"Habilidad {n}", core=False)
        log.info("habilidad cargada: %s", name)
        return name

    def install(self, name: str, description: str, code: str) -> str:
        stem = re.sub(r"\W", "_", name).lower()[:40]
        try:
            check_code(code)
            compile(code, stem, "exec")
        except UnsafeSkill as e:
            return f"Rechazada por seguridad: {e}"
        except SyntaxError as e:
            return f"Error de sintaxis: {e}"
        f = SKILLS / f"{stem}.py"
        if f.exists():
            shutil.copy2(f, SKILLS / "backup" / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.py")
        f.write_text(code, encoding="utf-8")
        try:
            tname = self._load(f)
        except Exception as e:
            f.unlink(missing_ok=True)
            return f"La habilidad no ha pasado la prueba de carga: {e}"
        self.state[stem] = {"enabled": True, "fails": 0, "description": description, "ts": time.time()}
        self._save()
        return f"Habilidad instalada como herramienta '{tname}'."

    def disable(self, name: str) -> str:
        stem = re.sub(r"\W", "_", name).lower()
        hits = [k for k in self.state if stem in k]
        for k in hits:
            self.state[k]["enabled"] = False
            REGISTRY.pop("skill_" + k, None)
        self._save()
        return f"Desactivadas: {', '.join(hits)}" if hits else "No existe esa habilidad."
