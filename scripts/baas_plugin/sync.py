"""Sincronización de las skills de la organización con el equipo del usuario (hito H5, T7; diseño §7).

`baas.py sync` mantiene un plugin de usuario de Claude Code **por organización**:

```text
~/.claude/skills/<slug>/                       plugin `<slug>@skills-dir`; sus skills salen como `/<slug>:<skill>`
├── .claude-plugin/plugin.json                 name = slug, version = hash corto del conjunto
├── .baas-managed                              JSON: marca de «gestionado» y estado de la última sincronización
└── skills/<skill>/…                           los ficheros del paquete (sin `baas.yaml`)
```

Reglas (contrato §10, diseño §7.2 y §9):

- **Solo toca lo suyo.** Un directorio `<slug>` sin `.baas-managed` es de otro origen: se aborta sin tocar
  nada. Dentro de uno gestionado todo es del plugin: lo que no esté autorizado desaparece.
- **Revocación.** Se borra lo que ya no lista `baas_list_skills` (rol quitado, skill retirada o inexistente);
  con `KILL_SWITCH_ACTIVE` se retiran **todas**. Las retiradas van antes que las descargas.
- **Integridad y confinamiento.** Cada fichero se verifica con su `sha256` (`package_fetch`); las rutas no
  pueden salir del paquete (`..`, absolutas, `\\`); no se crean enlaces simbólicos y los que haya en el sitio
  de una skill se sustituyen sin seguirlos. Cada skill se escribe en un directorio temporal junto al destino
  y se renombra; si algo falla, la versión anterior queda como estaba.
- **Dependencias.** `depends_on.skills` se resuelve de forma transitiva; las inaccesibles se avisan y **no se
  conceden** (D16). Los ciclos terminan.
- **Activación por proyecto** (spike T1): el plugin queda apagado a nivel de usuario (`settings.json`) y se
  activa en `.claude/settings.local.json` del proyecto vinculado; en ese proyecto se apagan los plugins de
  otras organizaciones que gestione `sync`.
- **Falla en abierto.** Sin MCP, o con el MCP lento, no se toca nada. Primero se hace toda la lectura (lista
  y manifiestos); solo después se escribe. Un solo `sync` a la vez (bloqueo en `~/baas-ai-ip/sync.lock`).
- Las altas y cambios necesitan poder apagar el plugin a nivel de usuario: si `settings.json` no se puede
  editar con seguridad se avisa y no se instala nada nuevo (las retiradas sí se aplican).

Un cambio se ve al reiniciar Claude Code o con `/reload-plugins` (validado en el spike T1).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sys
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import claude_home, package_fetch, project
from .claude_home import ClaudePaths
from .envfile import BaasPaths, ensure_private_dir, load_credentials
from .errors import (
    BaasError,
    ConfigError,
    ForeignDirectoryError,
    McpToolError,
    PackageError,
    ProjectSafetyError,
    ScriptIntegrityError,
    SyncIncompleteError,
)
from .package_fetch import Manifest

try:  # no existe en Windows (sin sincronización simultánea protegida)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

MARKER = ".baas-managed"
PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
TMP_PREFIX = ".baas-tmp-"
LOCK_FILE = "sync.lock"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATE_FORMAT = 1
KILL_SWITCH = "KILL_SWITCH_ACTIVE"
NOT_FOR_YOU = ("AUTHORIZATION_DENIED", "NOT_FOUND")  # el servidor dice que ese recurso no es para este usuario
RELOAD_HINT = (
    "Cierra y vuelve a abrir Claude Code para ver los cambios "
    "(`/reload-plugins` solo basta si el plugin ya estaba cargado)."
)


@dataclass
class SyncOptions:
    dry_run: bool = False
    force: bool = False
    quiet: bool = False
    timeout: float = 15.0  # espera máxima del MCP en la conexión y la lista: pasado esto no se toca nada
    transfer_timeout: float = 300.0  # espera máxima de cada descarga de ficheros (sin límite de tamaño)
    trigger: str = "manual"


@dataclass
class SyncResult:
    org: str
    plugin_dir: Path
    dry_run: bool = False
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)
    kill_switch: str | None = None
    version: str = ""
    activated: bool = False
    skipped: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.activated)

    def render(self) -> list[str]:
        if self.skipped:
            return [self.skipped]
        lines = []
        if self.dry_run:
            lines.append("Simulación (--dry-run): no se ha escrito nada. Esto es lo que se haría:")
        lines.append(f"Organización «{self.org}»: plugin en {self.plugin_dir}.")
        if self.kill_switch is not None:
            lines.append(
                f"{KILL_SWITCH} ({self.kill_switch or '?'}): el acceso de la organización está cortado; "
                "se retiran todas las skills sincronizadas."
            )
        for mark, names in (("+", self.added), ("~", self.updated), ("-", self.removed)):
            label = {"+": "nueva", "~": "actualizada", "-": "retirada"}[mark]
            lines += [f"  {mark} {n} ({label}{self.details.get(n, '')})" for n in names]
        lines += [f"  ! {n}: NO instalada ({why})" for n, why in sorted(self.failed.items())]
        lines += [f"AVISO: {w}" for w in self.warnings]
        lines += self.notes
        if not (self.changed or self.failed or self.kill_switch is not None):
            count = len(self.unchanged)
            lines.append(f"Todo al día: sin cambios ({count} {'skill' if count == 1 else 'skills'} sincronizadas).")
        elif not self.dry_run and self.changed:
            installed = sorted([*self.added, *self.updated, *self.unchanged])
            lines.append(RELOAD_HINT)
            if installed:
                shown = ", ".join(f"/{self.org}:{n}" for n in installed[:6])
                lines.append(f"Se invocan como {shown}{' …' if len(installed) > 6 else ''}")
        return lines


# --- utilidades ----------------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_slug(value: object) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise ConfigError(
            f"La organización del proyecto no tiene un slug válido: {value!r}.",
            hint="Vuelve a vincular el proyecto con /baas-init.",
        )
    return value


def _remove(path: Path) -> None:
    """Borra un fichero, un enlace (sin seguirlo) o un directorio."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def _combined_hash(skills: dict[str, str]) -> str:
    """Hash corto del conjunto de skills (`version` del plugin)."""
    text = "".join(f"{name} {sha}\n" for name, sha in sorted(skills.items()))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@contextlib.contextmanager
def _lock(baas: BaasPaths, enabled: bool) -> Iterator[bool]:
    """Bloqueo exclusivo no bloqueante; `False` si otra sincronización está en curso."""
    if not enabled or fcntl is None:
        yield True
        return
    ensure_private_dir(baas.root)
    fd = os.open(baas.root / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


# --- estado local --------------------------------------------------------------------------------


def read_state(plugin_dir: Path) -> dict[str, Any] | None:
    """`.baas-managed` del plugin: `None` si no existe; `{}` si está dañado (se reconstruye)."""
    marker = plugin_dir / MARKER
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def inspect_plugin_dir(plugin_dir: Path) -> dict[str, Any] | None:
    """Estado del plugin gestionado, o `None` si el directorio no existe. Aborta si no es nuestro."""

    def foreign(why: str) -> ForeignDirectoryError:
        return ForeignDirectoryError(
            f"{plugin_dir} {why}: BaaS no lo gestiona y no se toca nada.",
            hint="Renómbralo o muévelo si quieres que `baas sync` mantenga ahí el plugin de la organización.",
        )

    if plugin_dir.is_symlink():
        raise foreign("es un enlace simbólico")
    if not plugin_dir.exists():
        return None
    if not plugin_dir.is_dir():
        raise foreign("existe y no es un directorio")
    state = read_state(plugin_dir)
    if state is None:
        raise foreign("existe y no tiene la marca .baas-managed (es de otro origen)")
    for inner in (".claude-plugin", "skills", MARKER):
        if (plugin_dir / inner).is_symlink():
            raise foreign(f"contiene un enlace simbólico inesperado ({inner})")
    return state


def local_skills(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    skills = (state or {}).get("skills")
    if not isinstance(skills, dict):
        return {}
    return {n: e for n, e in skills.items() if isinstance(n, str) and isinstance(e, dict)}


# --- lectura (sin escribir): qué skills deben estar --------------------------------------------


@dataclass
class SkillPlan:
    name: str
    action: str  # add | update | keep
    package_sha256: str
    manifest: Manifest | None = None
    depends_on: list[str] = field(default_factory=list)


def list_skills(client: Any, notes: list[str]) -> dict[str, str]:
    """`baas_list_skills` → `{nombre: package_sha256}` de las skills activas y con nombre válido."""
    data = client.call_tool("baas_list_skills", {})
    rows = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise PackageError("Respuesta de baas_list_skills incompleta (falta `skills`).")
    listed: dict[str, str] = {}
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if not package_fetch.valid_skill_name(name):
            notes.append(f"AVISO: se ignora una skill con nombre no válido: {name!r}.")
            continue
        if str(row.get("status") or "active") != "active":
            continue
        listed[name] = str(row.get("package_sha256") or "").lower()
    return listed


def _dependency_warning(parent: str, dep: str) -> str:
    return (
        f"La skill «{parent}» depende de «{dep}», que no está accesible con tus roles: no se instala"
        " (pide acceso a tu administrador; el sincronizador no concede nada)."
    )


def resolve_plans(
    client: Any,
    listed: dict[str, str],
    local: dict[str, dict[str, Any]],
    plugin_dir: Path,
    opts: SyncOptions,
    result: SyncResult,
) -> dict[str, SkillPlan]:
    """Las skills que deben quedar instaladas: las listadas y, de forma transitiva, sus dependencias accesibles."""
    plans: dict[str, SkillPlan] = {}
    queue: deque[tuple[str, str | None]] = deque((name, None) for name in sorted(listed))
    unreachable: set[tuple[str, str]] = set()

    def warn_dependency(parent: str, dep: str) -> None:
        if (parent, dep) not in unreachable:
            unreachable.add((parent, dep))
            result.warnings.append(_dependency_warning(parent, dep))

    while queue:
        name, parent = queue.popleft()
        if name in plans or name in result.failed:
            continue
        entry = local.get(name)
        server_hash = listed.get(name)
        intact = (plugin_dir / "skills" / name / "SKILL.md").is_file()
        if not opts.force and entry and server_hash and entry.get("package_sha256") == server_hash and intact:
            deps = [d for d in entry.get("depends_on") or [] if isinstance(d, str)]
            plans[name] = SkillPlan(name, "keep", server_hash, depends_on=deps)
            queue.extend((d, name) for d in deps if d not in plans)
            continue
        try:
            manifest = package_fetch.fetch_manifest(client, name)
        except McpToolError as exc:
            if exc.code not in NOT_FOR_YOU:  # kill switch, servidor no disponible…: no se decide nada
                raise
            if parent is not None and name not in listed:  # una dependencia que no se puede leer
                warn_dependency(parent, name)
            continue  # y una skill listada que ya no se puede leer (rol quitado a mitad): se retira
        except PackageError as exc:
            result.failed[name] = exc.message
            continue
        if manifest.status != "active":
            if parent is not None:
                warn_dependency(parent, name)
            continue
        action = "update" if entry else "add"
        if entry and entry.get("package_sha256") == manifest.package_sha256 and intact and not opts.force:
            action = "keep"
        deps = [d.name for d in manifest.dependencies]
        plans[name] = SkillPlan(name, action, manifest.package_sha256, manifest, deps)
        for dep in manifest.dependencies:
            if not package_fetch.valid_skill_name(dep.name):
                continue
            if not dep.accessible and dep.name not in listed:
                warn_dependency(name, dep.name)
            elif dep.name not in plans:
                queue.append((dep.name, name))
    # Una skill cuyo manifiesto no es válido no se retira: se conserva la versión anterior hasta que se corrija
    for name in result.failed:
        entry = local.get(name)
        if entry and name not in plans:
            deps = [d for d in entry.get("depends_on") or [] if isinstance(d, str)]
            plans[name] = SkillPlan(name, "keep", str(entry.get("package_sha256", "")), depends_on=deps)
    # Las dependencias que ya estaban instaladas y siguen sin estar accesibles (estado local de una `keep`)
    for name, plan in sorted(plans.items()):
        for dep in plan.depends_on:
            if dep not in plans and package_fetch.valid_skill_name(dep):
                warn_dependency(name, dep)
    return plans


# --- escritura -----------------------------------------------------------------------------------


def _install_skill(client: Any, plugin_dir: Path, tmp_root: Path, name: str, manifest: Manifest) -> None:
    """Descarga la skill a un temporal junto al destino y la renombra; si falla, deja la anterior.

    Los ficheros grandes se escriben a disco según llegan (por trozos): sincronizar no carga en memoria un fichero
    de cientos de MiB.
    """
    staged = tmp_root / f"new-{name}"
    package_fetch.fetch_to_dir(client, manifest, staged)
    final = plugin_dir / "skills" / name
    backup = tmp_root / f"old-{name}"
    had_previous = final.is_symlink() or final.exists()
    if had_previous:
        os.rename(final, backup)
    try:
        os.rename(staged, final)
    except OSError:
        if had_previous:
            os.rename(backup, final)
        raise
    if had_previous:
        _remove(backup)


def _file_entries(manifest: Manifest) -> list[dict[str, Any]]:
    return [{"path": f.path, "sha256": f.sha256, "executable": f.executable} for f in manifest.files]


def _plugin_json(slug: str, org_name: str | None, version: str) -> dict[str, Any]:
    return {
        "name": slug,
        "version": version,
        "description": (
            f"Skills de la organización {org_name or slug} en BaaS-AI-IP, sincronizadas por el plugin baas. "
            "No las edites: se sobrescriben en cada sincronización."
        ),
        "author": {"name": "BaaS-AI-IP"},
    }


def _write_if_changed(path: Path, data: dict[str, Any]) -> bool:
    try:
        if json.loads(path.read_text(encoding="utf-8")) == data:
            return False
    except (OSError, ValueError):
        pass
    claude_home.write_json_atomic(path, data)
    return True


def _activate_in_project(project_dir: Path, slug: str, claude: ClaudePaths, result: SyncResult, dry_run: bool) -> None:
    key = claude_home.plugin_key(slug)
    try:
        enabled = project.read_enabled_plugins(project_dir)
        suffix = f"@{claude_home.MARKETPLACE}"
        drop = [
            k
            for k in enabled
            if k != key and k.endswith(suffix) and (claude.plugin_dir(k[: -len(suffix)]) / MARKER).is_file()
        ]
        if dry_run:
            result.activated = enabled.get(key) is not True or bool(drop)
        else:
            result.activated = project.set_enabled_plugins(project_dir, key, drop)
        if result.activated:
            verb = "se activaría" if dry_run else "activado"
            result.notes.append(
                f"Plugin «{slug}» {verb} en este proyecto (enabledPlugins de .claude/settings.local.json)."
            )
    except ProjectSafetyError as exc:
        result.warnings.append(f"No se activó el plugin en el proyecto: {exc.message} {exc.hint or ''}".strip())


def sync_organization(
    binding: dict[str, Any],
    api_key: str,
    project_dir: Path,
    claude: ClaudePaths,
    baas: BaasPaths,
    opts: SyncOptions,
) -> SyncResult:
    from .mcp_client import McpClient

    slug = validate_slug(binding.get("org"))
    mcp_url = str(binding.get("mcp_url") or "")
    if not mcp_url:
        raise ConfigError("La vinculación del proyecto no tiene la URL del MCP.", hint="Lanza /baas-init.")
    plugin_dir = claude.plugin_dir(slug)
    result = SyncResult(slug, plugin_dir, dry_run=opts.dry_run)

    with _lock(baas, enabled=not opts.dry_run) as acquired:
        if not acquired:
            result.skipped = "Otra sincronización está en curso; no se hace nada. Vuelve a intentarlo en unos segundos."
            return result
        state = inspect_plugin_dir(plugin_dir)
        local = local_skills(state)
        client = McpClient(mcp_url, api_key, timeout=opts.timeout)
        with client:  # sin MCP (o con el MCP lento) esto falla y no se ha tocado nada
            plans: dict[str, SkillPlan] = {}
            try:
                listed = list_skills(client, result.notes)
                plans = resolve_plans(client, listed, local, plugin_dir, opts, result)
            except McpToolError as exc:
                if exc.code != KILL_SWITCH:
                    raise
                result.kill_switch = str(exc.payload.get("scope") or "")
                result.failed.clear()
            client.timeout = opts.transfer_timeout
            _apply(client, binding, project_dir, claude, plugin_dir, state, local, plans, opts, result)
    return result


def _apply(
    client: Any,
    binding: dict[str, Any],
    project_dir: Path,
    claude: ClaudePaths,
    plugin_dir: Path,
    state: dict[str, Any] | None,
    local: dict[str, dict[str, Any]],
    plans: dict[str, SkillPlan],
    opts: SyncOptions,
    result: SyncResult,
) -> None:
    slug = result.org
    skills_dir = plugin_dir / "skills"
    to_fetch = sorted(n for n, p in plans.items() if p.action in ("add", "update"))
    result.unchanged = sorted(n for n, p in plans.items() if p.action == "keep" and n not in result.failed)
    for name in to_fetch:
        manifest = plans[name].manifest
        assert manifest is not None
        result.details[name] = f", {len(manifest.files)} ficheros, {_human_size(manifest.size)}"
    existing = sorted(p.name for p in skills_dir.iterdir()) if skills_dir.is_dir() else []
    result.removed = [n for n in existing if n not in plans]
    for name in result.removed:
        result.details[name] = ""

    if state is None and not to_fetch:
        result.notes.append("No hay skills accesibles que sincronizar: no se crea nada.")
        result.unchanged = []
        result.version = _combined_hash({})
        return
    result.added = [n for n in to_fetch if n not in local]
    result.updated = [n for n in to_fetch if n in local]

    if opts.dry_run:
        _activate_in_project(project_dir, slug, claude, result, dry_run=True)
        return

    # 1. El plugin no debe verse a nivel de usuario antes de que exista en disco.
    can_install = True
    try:
        outcome = claude_home.disable_user_plugin(claude, slug)
        if outcome == "created":
            result.notes.append(
                f"Plugin «{slug}» apagado a nivel de usuario ({claude.settings}): solo se activa en los proyectos"
                " vinculados a la organización."
            )
        elif outcome == "was-enabled":
            result.notes.append(
                "El plugin estaba activado a nivel de usuario: se desactiva para que sus skills solo aparezcan"
                " en los proyectos vinculados a la organización."
            )
    except ConfigError as exc:
        can_install = False
        result.warnings.append(
            f"{exc.message} Sin poder apagar el plugin a nivel de usuario no se instalan ni actualizan skills"
            " (las retiradas sí se aplican)."
        )
    if not can_install:
        result.added, result.updated = [], []
        to_fetch = []
        if state is None:
            return

    # 2. El directorio gestionado y su marca existen antes que cualquier skill.
    skills: dict[str, dict[str, Any]] = {n: dict(e) for n, e in local.items() if n in plans}
    for name, plan in plans.items():
        if plan.action == "keep":
            skills.setdefault(name, {"package_sha256": plan.package_sha256, "depends_on": plan.depends_on})
    if state is None:
        plugin_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_state(plugin_dir, binding, slug, {}, [], "")
    skills_dir.mkdir(mode=0o700, exist_ok=True)
    tmp_root = plugin_dir / f"{TMP_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
    for stale in plugin_dir.glob(f"{TMP_PREFIX}*"):  # restos de una ejecución interrumpida (hay bloqueo)
        _remove(stale)

    try:
        # 3. Retiradas primero: son locales y no dependen de que las descargas salgan bien.
        for name in result.removed:
            _remove(skills_dir / name)
        skills = {n: e for n, e in skills.items() if n in plans}
        # 4. Altas y cambios, una skill cada vez.
        tmp_root.mkdir(mode=0o700)
        for name in to_fetch:
            plan = plans[name]
            assert plan.manifest is not None
            try:
                _install_skill(client, plugin_dir, tmp_root, name, plan.manifest)
            except McpToolError as exc:
                if exc.code not in NOT_FOR_YOU:
                    raise  # kill switch o servidor no disponible a mitad: se aborta (el estado ya es coherente)
                result.failed[name] = exc.message
                continue
            except (PackageError, ScriptIntegrityError) as exc:
                result.failed[name] = exc.message
                continue
            skills[name] = {
                "package_sha256": plan.package_sha256,
                "depends_on": plan.depends_on,
                "files": _file_entries(plan.manifest),
            }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        result.added = [n for n in result.added if n not in result.failed]
        result.updated = [n for n in result.updated if n not in result.failed]
        result.version = _combined_hash({n: str(e.get("package_sha256", "")) for n, e in skills.items()})
        _finish(plugin_dir, binding, slug, skills, result)
    _activate_in_project(project_dir, slug, claude, result, dry_run=False)


def _write_state(
    plugin_dir: Path, binding: dict[str, Any], slug: str, skills: dict[str, Any], warnings: list[str], version: str
) -> None:
    state = {
        "managed_by": "baas-llm-plugin",
        "format": STATE_FORMAT,
        "org": slug,
        "server": str(binding.get("mcp_url") or ""),
        "synced_at": now_iso(),
        "version": version,
        "skills": skills,
        "warnings": warnings,
    }
    claude_home.write_json_atomic(plugin_dir / MARKER, state)


def _finish(plugin_dir: Path, binding: dict[str, Any], slug: str, skills: dict[str, Any], result: SyncResult) -> None:
    """`plugin.json` y `.baas-managed` reflejan lo que de verdad hay en disco."""
    manifest_path = plugin_dir / PLUGIN_MANIFEST
    manifest_path.parent.mkdir(mode=0o700, exist_ok=True)
    _write_if_changed(manifest_path, _plugin_json(slug, binding.get("org_name"), result.version))
    _write_state(plugin_dir, binding, slug, skills, list(result.warnings), result.version)


# --- /baas-status --------------------------------------------------------------------------------


def _reachable(listing: dict[str, str], local: dict[str, dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    queue = deque(listing)
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        queue.extend(d for d in (local.get(name) or {}).get("depends_on") or [] if isinstance(d, str))
    return seen


def status_lines(
    binding: dict[str, Any] | None, project_dir: Path, claude: ClaudePaths, client: Any = None
) -> list[str]:
    """Líneas de `/baas-status`: skills sincronizadas, desfasadas, revocadas y pendientes (no escribe nada)."""
    if not binding or not isinstance(binding.get("org"), str) or not SLUG_RE.match(binding["org"]):
        return []
    slug = binding["org"]
    plugin_dir = claude.plugin_dir(slug)
    try:
        state = inspect_plugin_dir(plugin_dir)
    except ForeignDirectoryError as exc:
        return [f"AVISO: {exc.message}"]
    local = local_skills(state)
    lines = []
    if not local:
        lines.append("Skills sincronizadas: ninguna · ejecuta /baas-sync para traer las de tu organización.")
    else:
        lines.append(
            f"Skills sincronizadas: {len(local)} en {plugin_dir} (versión {(state or {}).get('version', '?')})"
        )
    listing: dict[str, str] | None = None
    if client is not None:
        try:
            listing = list_skills(client, [])
        except McpToolError as exc:
            if exc.code != KILL_SWITCH:
                lines.append(f"AVISO: no se pudo consultar las skills del MCP ({exc.code}).")
            else:
                scope = exc.payload.get("scope") or "?"
                lines.append(
                    f"  {KILL_SWITCH} ({scope}): en la próxima sincronización se retirarán todas las skills"
                    " sincronizadas de la organización."
                )
        except BaasError as exc:
            lines.append(f"AVISO: no se pudo consultar las skills del MCP ({exc.message}).")
    if listing is not None:
        kept = _reachable(listing, local)
        in_sync = [n for n in sorted(local) if n in listing and listing[n] == local[n].get("package_sha256")]
        outdated = [n for n in sorted(local) if n in listing and n not in in_sync]
        revoked = [n for n in sorted(local) if n not in kept]
        pending = [n for n in sorted(listing) if n not in local]
        for label, names in (
            ("al día", in_sync),
            ("desfasadas", outdated),
            ("revocadas o retiradas", revoked),
            ("pendientes de instalar", pending),
        ):
            if names:
                extra = {
                    "desfasadas": " (el servidor tiene otra versión: /baas-sync)",
                    "revocadas o retiradas": " (se borrarán en la próxima sincronización)",
                    "pendientes de instalar": " (/baas-sync)",
                }.get(label, "")
                lines.append(f"  {label}: {', '.join(names)}{extra}")
    elif local:
        lines.append(f"  instaladas: {', '.join(sorted(local))}")
    if state is not None:
        try:
            active = project.read_enabled_plugins(project_dir).get(claude_home.plugin_key(slug)) is True
        except ProjectSafetyError:
            active = False
        lines.append(f"  Plugin activo en este proyecto: {'sí' if active else 'no (lanza /baas-sync)'}")
        if state.get("synced_at"):
            lines.append(f"  Última sincronización: {state['synced_at']}")
        lines += [f"AVISO: {w}" for w in state.get("warnings") or [] if isinstance(w, str)]
    return lines


# --- CLI -----------------------------------------------------------------------------------------


def add_claude_home_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-home", help="Directorio de usuario de Claude Code (por defecto BAAS_CLAUDE_HOME o ~/.claude)"
    )
    parser.add_argument("--skills-root", help="Carpeta de plugins de usuario (por defecto <claude-home>/skills)")


def claude_paths(args: argparse.Namespace) -> ClaudePaths:
    return ClaudePaths.resolve(getattr(args, "claude_home", None), getattr(args, "skills_root", None))


def cmd_sync(args: argparse.Namespace) -> int:
    baas = BaasPaths.default()
    creds = load_credentials(baas)
    proj = project.resolve_project_dir(args.project or os.environ.get("CLAUDE_PROJECT_DIR"), cwd=Path.cwd())
    binding = project.read_binding(proj)
    if not binding or not binding.get("org"):
        raise ConfigError(f"El proyecto {proj} no está vinculado a BaaS-AI-IP: lanza /baas-init.")
    opts = SyncOptions(
        dry_run=args.dry_run, force=args.force, quiet=args.quiet, timeout=args.timeout, trigger=args.trigger
    )

    def _terminate(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)  # los `finally` borran los temporales

    with contextlib.suppress(ValueError):  # fuera del hilo principal no hay manejadores
        signal.signal(signal.SIGTERM, _terminate)
    try:
        result = sync_organization(binding, creds.api_key, proj, claude_paths(args), baas, opts)
    except BaasError as exc:
        if not opts.quiet:
            raise
        print(f"[{opts.trigger}] {exc.render()}", file=sys.stderr)  # falla en abierto: el hook nunca estorba
        return 0
    if not opts.quiet:
        for line in result.render():
            print(line)
    if result.failed:
        why = "; ".join(f"{n}: {w}" for n, w in sorted(result.failed.items()))
        error = SyncIncompleteError(
            f"No se pudieron sincronizar {len(result.failed)} skill(s): {why}.",
            hint="El resto se aplicó. Avisa al administrador de la organización si persiste.",
        )
        if not opts.quiet:
            raise error
        print(f"[{opts.trigger}] {error.render()}", file=sys.stderr)
    return 0


def add_sync_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "sync", help="Sincroniza las skills de la organización del proyecto en ~/.claude/skills/<organización>/"
    )
    p.add_argument("--project", help="Raíz del proyecto (por defecto CLAUDE_PROJECT_DIR o la raíz git)")
    p.add_argument("--dry-run", action="store_true", help="Muestra qué cambiaría sin escribir nada")
    p.add_argument("--force", action="store_true", help="Vuelve a descargar todas las skills aunque no hayan cambiado")
    p.add_argument("--quiet", action="store_true", help="Sin salida si todo va bien y sin fallar si no hay MCP (hooks)")
    p.add_argument(
        "--timeout", type=float, default=15.0, help="Segundos máximos de espera del MCP antes de renunciar (15)"
    )
    p.add_argument("--trigger", default="manual", help=argparse.SUPPRESS)
    add_claude_home_args(p)
    p.set_defaults(func=cmd_sync)
