"""Configuración del proyecto (contrato §10): solo ficheros locales, no versionables y en `.gitignore`.

- `.claude/settings.local.json`: bloque `env` con `ANTHROPIC_BASE_URL` y `ANTHROPIC_CUSTOM_HEADERS`
  (la cabecera `X-Baas-Api-Key` es el único sitio del proyecto donde va la clave; ver docs/spike-s1.md).
- `.claude/baas.local.json`: organización y URLs devueltas por el handshake (sin secretos).
- `enabledPlugins` de `.claude/settings.local.json` (hito H5): activa en este proyecto el plugin de la
  organización que mantiene `baas.py sync` (`<slug>@skills-dir`); mismas reglas que el resto del fichero.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .envfile import atomic_write
from .errors import ProjectSafetyError

SETTINGS_LOCAL = ".claude/settings.local.json"
BINDING = ".claude/baas.local.json"
GITIGNORE_ENTRIES = (SETTINGS_LOCAL, BINDING)
BACKUP_SUFFIX = ".bak"
# La copia de un fichero dañado puede conservar la clave: se ignora igual que el original.
# (`.bak`, `.bak.1`, `.bak.2`…): el patrón con `*` las cubre todas.
BACKUP_ENTRIES = tuple(entry + BACKUP_SUFFIX + "*" for entry in GITIGNORE_ENTRIES)
HEADER_NAME = "X-Baas-Api-Key"
GITIGNORE_COMMENT = "# BaaS-AI-IP (plugin baas): configuración local con credenciales; no versionar"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def resolve_project_dir(explicit: str | None, *, cwd: Path) -> Path:
    """`CLAUDE_PROJECT_DIR` si llega; si no, la raíz del repositorio git; si no, el directorio actual."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    res = _git(cwd, "rev-parse", "--show-toplevel")
    if res is not None and res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip()).resolve()
    return cwd.resolve()


def check_not_tracked(
    project: Path, entries: Iterable[str] = GITIGNORE_ENTRIES, *, command: str = "/baas-init"
) -> None:
    """Un `.gitignore` no protege un fichero ya versionado: en ese caso no escribimos la clave."""
    if not project.is_dir():
        return
    for rel in entries:
        res = _git(project, "ls-files", "--error-unmatch", "--", rel)
        if res is not None and res.returncode == 0:
            raise ProjectSafetyError(
                f"`{rel}` está versionado en git; escribir en él podría publicar tu API key.",
                hint=f"Sácalo del índice con `git rm --cached {rel}` (y haz commit) antes de lanzar {command}.",
            )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        raise ProjectSafetyError(f"{path} no es JSON válido; corrígelo y vuelve a lanzar /baas-init.") from exc
    if not isinstance(data, dict):
        raise ProjectSafetyError(f"{path} no contiene un objeto JSON.")
    return data


def _free_backup(path: Path) -> Path:
    """`<nombre>.bak`, o `.bak.1`, `.bak.2`… si ya hay una copia: nunca se pisa una copia anterior."""
    base = path.with_name(path.name + BACKUP_SUFFIX)
    candidate, n = base, 0
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{base.name}.{n}")
    return candidate


def repair_json_file(path: Path) -> bool:
    """Si `path` existe pero no es un objeto JSON válido, lo guarda como copia `.bak` (0600) y devuelve `True`;
    así el siguiente `write_*` lo regenera en vez de fallar. Un fichero válido o inexistente no se toca."""
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        data = None
    if isinstance(data, dict):
        return False
    backup = _free_backup(path)
    os.replace(path, backup)
    if os.name == "posix":
        backup.chmod(0o600)
    return True


def backups_exist(project: Path) -> bool:
    """¿Hay copias de ficheros dañados en `.claude/`? (pueden conservar la clave: hay que ignorarlas en git)."""
    return any(
        any((project / entry).parent.glob(Path(entry).name + BACKUP_SUFFIX + "*")) for entry in GITIGNORE_ENTRIES
    )


def _write_json(path: Path, data: dict[str, Any]) -> bool:
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, content)
    return True


def merge_custom_headers(existing: str | None, api_key: str) -> str:
    """Sustituye la línea `X-Baas-Api-Key` y conserva las demás cabeceras del usuario."""
    keep = [
        line
        for line in (existing or "").split("\n")
        if line.strip() and line.split(":", 1)[0].strip().lower() != HEADER_NAME.lower()
    ]
    return "\n".join([*keep, f"{HEADER_NAME}: {api_key}"])


def write_settings_local(project: Path, proxy_anthropic_url: str, api_key: str) -> bool:
    path = project / SETTINGS_LOCAL
    data = _load_json(path)
    env = data.get("env")
    if env is None:
        env = {}
    if not isinstance(env, dict):
        raise ProjectSafetyError(f"El bloque `env` de {path} no es un objeto JSON.")
    env["ANTHROPIC_BASE_URL"] = proxy_anthropic_url
    env["ANTHROPIC_CUSTOM_HEADERS"] = merge_custom_headers(env.get("ANTHROPIC_CUSTOM_HEADERS"), api_key)
    data["env"] = env
    return _write_json(path, data)


def read_enabled_plugins(project: Path) -> dict[str, Any]:
    plugins = _load_json(project / SETTINGS_LOCAL).get("enabledPlugins")
    return plugins if isinstance(plugins, dict) else {}


def set_enabled_plugins(project: Path, enable: str, drop: Iterable[str] = ()) -> bool:
    """Activa el plugin `enable` y quita las claves `drop` de `enabledPlugins` en `settings.local.json`.

    Respeta las reglas del fichero (no versionable, en `.gitignore`) y conserva el resto. Devuelve `True`
    si lo modificó; si ya estaba como se pide, no lo toca. Lanza `ProjectSafetyError` si está versionado.
    """
    path = project / SETTINGS_LOCAL
    data = _load_json(path)
    plugins = data.get("enabledPlugins")
    if plugins is None:
        plugins = {}
    if not isinstance(plugins, dict):
        raise ProjectSafetyError(f"El bloque `enabledPlugins` de {path} no es un objeto JSON.")
    wanted = {k: v for k, v in plugins.items() if k not in set(drop) or k == enable}
    wanted[enable] = True
    if wanted == plugins:
        return False
    check_not_tracked(project)
    data["enabledPlugins"] = wanted
    _write_json(path, data)
    ensure_gitignore(project)
    return True


def _present(lines: list[str], entry: str) -> bool:
    variants = {entry, "/" + entry, "**/" + entry}
    return any(line.strip() in variants for line in lines)


def ensure_gitignore(project: Path, entries: Iterable[str] = GITIGNORE_ENTRIES) -> list[str]:
    path = project / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    missing = [e for e in entries if not _present(lines, e)]
    if not missing:
        return []
    if text and not text.endswith("\n"):
        text += "\n"
    block = ([GITIGNORE_COMMENT] if GITIGNORE_COMMENT not in lines else []) + missing
    path.write_text(text + "\n".join(block) + "\n", encoding="utf-8")
    return missing


def write_binding(project: Path, binding: dict[str, Any]) -> bool:
    return _write_json(project / BINDING, binding)


def read_binding(project: Path) -> dict[str, Any] | None:
    path = project / BINDING
    if not path.exists():
        return None
    return _load_json(path)


def read_settings_env(project: Path) -> dict[str, Any]:
    env = _load_json(project / SETTINGS_LOCAL).get("env")
    return env if isinstance(env, dict) else {}
