"""CLI del plugin: `set-key`, `init`, `status`, `desktop-config`, `run-script`, `sync`, `inspect`, `publish`,
`export` y `setup <herramienta>` (otras herramientas de IA, p. ej. Antigravity). Mensajes en español.

Códigos de salida (las skills los interpretan): 0 ok · 2 configuración (falta la clave o la URL)
· 3 hay que elegir organización · 4 sin organizaciones · 5 clave no válida (`init` ya no lo produce:
renueva sola una clave revocada) · 6 red o servidor
· 7 proyecto inseguro · 8 organización ajena · 9 hash de un fichero no coincide · 10 error del MCP
· 11 paquete inválido (ruta insegura) · 12 sincronización incompleta · 13 directorio ajeno
· 20 falta aprobar la conexión en el navegador (hay un enlace) · 21 sigue esperando la aprobación
(repetir `init --wait`) · 22 la persona canceló la conexión · 23 el código de aprobación caducó.

`init` termina con una marca para la skill: en una salida 0, `[BAAS_RESULT linked]` (vinculación nueva, clave
renovada o conexión aprobada en esta ejecución) o `[BAAS_RESULT unchanged]` (ya estaba vinculado y no se reescribió
nada: ni ajustes, ni vinculación, ni registro pendiente); en una salida 6, `[BAAS_APP_URL <dirección>]` con
la dirección probada (nunca con credenciales).

`run-script` es la excepción: devuelve **íntegro** el código de salida del script, que puede ser
cualquiera (también 9 o 10). Sus errores propios salen, además, con una línea `BAAS_ERROR=<código>` en
stderr (p. ej. `SCRIPT_HASH_MISMATCH`, `AUTHORIZATION_DENIED`, `KILL_SWITCH_ACTIVE`): sin esa línea, el
código de salida es el del script.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

import baas_guard

from . import __version__, desktop_config, device, handshake, mcp_config, project, sync
from .claude_home import ClaudePaths
from .envfile import (
    API_KEY_RE,
    BaasPaths,
    load_credentials,
    missing_key_error,
    normalize_app_url,
    read_env,
    remove_env_keys,
    update_env,
    validate_api_key,
)
from .errors import (
    ApprovalDeniedError,
    ApprovalExpiredError,
    ApprovalPendingError,
    ApprovalWaitingError,
    AuthError,
    BaasError,
    ConfigError,
    MissingAppUrlError,
    NetworkError,
    PlainNetworkError,
    ProjectSafetyError,
)


def _p(*lines: str) -> None:
    for line in lines:
        print(line)


def _public_url(url: str) -> str | None:
    """La dirección sin credenciales, consulta ni fragmento (es lo único que `[BAAS_APP_URL …]` puede llevar)."""
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return None
    public = f"{parts.scheme}://{host}{port}{parts.path.rstrip('/')}"
    return public if "@" not in public and not any(c.isspace() for c in public) else None


def key_prefix(key: str) -> str:
    return key[:12] + "…"


# --- set-key ---------------------------------------------------------------------------------


ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


def validate_anthropic_key(value: str) -> str:
    key = value.strip()
    if not ANTHROPIC_KEY_RE.match(key):
        raise ConfigError(
            "La clave no tiene el formato de una API key de Anthropic Console (`sk-ant-…`).",
            hint="Créala en console.anthropic.com (API keys). La suscripción de claude.ai no sirve en modo gateway.",
        )
    return key


def cmd_set_key(args: argparse.Namespace) -> int:
    paths = BaasPaths.default()
    label = "API key de Anthropic Console" if args.anthropic else "API key de BaaS-AI-IP"
    if args.stdin:
        raw = sys.stdin.readline()
    elif sys.stdin is not None and sys.stdin.isatty():
        raw = getpass.getpass(f"{label} (no se mostrará): ")
    else:
        raise ConfigError(
            "`set-key` necesita una terminal interactiva para leer la clave sin mostrarla.",
            hint="Ejecútalo en una terminal aparte, o pásala por tubería con --stdin "
            "(p. ej. desde tu gestor de contraseñas).",
        )
    if args.anthropic:
        key = validate_anthropic_key(raw)
        warnings = update_env(paths, {"ANTHROPIC_API_KEY": key})
        _p(*warnings, f"{label} {key[:10]}… guardada en {paths.env_file} (permisos 0600).")
        _p("La usa solo el helper de Claude Desktop en modo gateway (ver `desktop-config`).")
        return 0
    key = validate_api_key(raw)
    updates = {"BAAS_API_KEY": key}
    if args.app_url:
        updates["BAAS_APP_URL"] = normalize_app_url(args.app_url)
    warnings = update_env(paths, updates)
    _p(*warnings, f"API key {key_prefix(key)} guardada en {paths.env_file} (permisos 0600).")
    if args.app_url:
        _p(f"URL de la webapp: {updates['BAAS_APP_URL']}")
    _p("Vuelve a Claude Code y lanza /baas-init.")
    return 0


# --- init ------------------------------------------------------------------------------------


def _project_dir(args: argparse.Namespace) -> Path:
    return project.resolve_project_dir(args.project or os.environ.get("CLAUDE_PROJECT_DIR"), cwd=Path.cwd())


STALE_KEY_NOTICE = "Tu conexión anterior ya no es válida; vamos a renovarla."
REPAIRED_NOTICE = "He reparado una configuración dañada (copia guardada)."
WAIT_INTERVAL = device.DEFAULT_INTERVAL  # mínimo entre consultas; se respeta el del servidor si es mayor
WAIT_SECONDS = 90  # lo que espera una sola llamada de `init --wait`


def _resolve_app_url(explicit: str | None, values: dict[str, str]) -> str:
    """La dirección de la webapp (explícita > entorno > guardada > por defecto), normalizada y con mensajes llanos."""
    raw = device.resolve_app_url(explicit, dict(os.environ), values.get("BAAS_APP_URL"))
    if raw is None:
        raise MissingAppUrlError(
            "No sé a qué dirección de BaaS-AI-IP conectarme.",
            hint="Pide a quien administra BaaS-AI-IP la dirección de la aplicación y vuelve a empezar indicándola.",
        )
    try:
        return normalize_app_url(raw)
    except ConfigError:  # el mensaje técnico incluye la URL tal cual la escribió la persona: no se repite
        raise ConfigError(
            "La dirección de BaaS-AI-IP no es válida o no es segura.",
            hint="Comprueba con quien administra BaaS-AI-IP cuál es la dirección correcta y vuelve a empezar.",
        ) from None


def _client_name(proj: Path) -> str:
    return f"{proj.name or 'proyecto'} en {socket.gethostname()}"[:100]


def _graphical_session() -> bool:
    """¿Es plausible que haya un navegador de escritorio? En Linux sin pantalla, `webbrowser` puede lanzar uno de
    consola y bloquear la terminal."""
    return sys.platform in ("darwin", "win32", "cygwin") or bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


BROWSER_GRACE = 2.0  # segundos que se le dan al navegador para arrancar antes de seguir sin esperarlo


def _open_browser(url: str) -> None:
    """Intenta abrir el enlace sin bloquear el flujo: hilo aparte, fallos ignorados. El enlace siempre se muestra."""
    if not _graphical_session():
        return

    def _open() -> None:
        with contextlib.suppress(Exception):  # abrir el navegador es un extra
            webbrowser.open(url)

    thread = threading.Thread(target=_open, daemon=True)
    thread.start()
    thread.join(BROWSER_GRACE)  # solo para que el proceso no termine antes de lanzarlo; nunca bloquea más


def _handshake(app_url: str, api_key: str) -> handshake.Handshake:
    """Handshake con los fallos de red en llano (sin URLs ni rutas); una clave revocada sigue siendo `AuthError`."""
    try:
        return handshake.perform_handshake(app_url, api_key)
    except PlainNetworkError:
        raise  # límite de intentos o fallo del servidor: ya viene en llano y la skill lo distingue
    except (NetworkError, http.client.HTTPException):
        raise device.unreachable() from None


def _pending_notice(flow: device.PendingFlow) -> NoReturn:
    raise ApprovalPendingError(
        verification_url=flow.verification_url,
        user_code=flow.user_code,
        hint="Cuando hayas aprobado la conexión en el navegador, dímelo y sigo.",
    )


def _start_flow(paths: BaasPaths, proj: Path, app_url: str, *, fresh: bool = False) -> NoReturn:
    """Termina con el enlace de aprobación (código 20): el de la conexión pendiente si sigue viva y es de este
    servidor (mismo enlace y código, sin abrir otra pestaña); si no, pide uno nuevo. Con `fresh` (otra cuenta) nunca
    se reutiliza un enlace anterior: se pide uno nuevo y sustituye al pendiente. La clave guardada no se toca aquí."""
    if not fresh:
        live = device.load_pending(paths, now=time.time())
        if live is not None and normalize_app_url(live.app_url) == app_url:
            _pending_notice(live)
    flow = device.start(app_url, _client_name(proj))  # NetworkError (código 6): nada se guarda
    update_env(paths, {"BAAS_APP_URL": app_url})
    pending = device.PendingFlow(
        app_url, flow.device_code, flow.user_code, flow.verification_url, time.time() + flow.expires_in, flow.interval
    )
    device.save_pending(paths, pending)
    _open_browser(flow.verification_url)
    _pending_notice(pending)


def _await_approval(paths: BaasPaths, app_url: str) -> str | None:
    """`init --wait`: espera la aprobación pendiente. Devuelve la clave nueva (ya guardada), o `None` si no hay
    nada pendiente (o es de otro servidor o caducó) y hay que empezar de cero."""
    pending = device.load_pending(paths, now=time.time())
    if pending is None or normalize_app_url(pending.app_url) != app_url:
        return None
    result = device.wait(
        pending.app_url,
        pending.device_code,
        interval=max(pending.interval, WAIT_INTERVAL),
        max_seconds=WAIT_SECONDS,
    )
    if result.status == "approved" and result.api_key:
        update_env(paths, {"BAAS_API_KEY": result.api_key, "BAAS_APP_URL": app_url})
        device.clear_pending(paths)
        return result.api_key
    if result.status == "denied":
        device.clear_pending(paths)
        raise ApprovalDeniedError
    if result.status == "expired":
        device.clear_pending(paths)
        raise ApprovalExpiredError
    raise ApprovalWaitingError


def cmd_init(args: argparse.Namespace) -> int:
    paths = BaasPaths.default()
    values, _ = read_env(paths)
    app_url = _resolve_app_url(args.app_url, values)
    try:
        return _init(args, paths, values, app_url)
    except NetworkError as exc:
        exc.app_url = _public_url(app_url)  # la skill la ofrece ya escrita al pedir otra dirección
        raise


def _init(args: argparse.Namespace, paths: BaasPaths, values: dict[str, str], app_url: str) -> int:
    proj = _project_dir(args)
    project.check_not_tracked(proj)

    hs = None
    api_key: str | None = None
    via_approval = False
    if args.new_account and not args.wait:
        # Otra cuenta: se ignora la clave guardada y se pide una aprobación nueva (siempre un enlace nuevo). La clave
        # de siempre NO se borra: la usan el MCP, la sincronización y los demás proyectos, y si la persona abandona,
        # un `init` normal sigue diciendo «ya conectado». Solo una aprobación correcta la sustituye (`--wait`).
        _start_flow(paths, proj, app_url, fresh=True)
    if args.wait:
        api_key = _await_approval(paths, app_url)
        via_approval = api_key is not None
        if api_key is None:
            # Sin conexión pendiente aquí: puede que otro proyecto la terminara y guardara ya la clave.
            stored = read_env(paths)[0].get("BAAS_API_KEY", "").strip()
            if API_KEY_RE.match(stored):
                try:
                    hs = _handshake(app_url, stored)
                except AuthError:
                    pass  # una clave que no vale no es una conexión: se empieza la aprobación
                else:
                    api_key, via_approval = stored, True  # hay que reiniciar, como tras una aprobación
                    update_env(paths, {"BAAS_APP_URL": app_url})
    else:
        stored = values.get("BAAS_API_KEY", "").strip()
        if stored:
            api_key = stored if API_KEY_RE.match(stored) else None
            if api_key:
                try:
                    hs = _handshake(app_url, api_key)
                except AuthError:  # revocada, de otra base de datos o caducada: no es un fallo, se renueva
                    api_key = None
            if api_key is None:
                remove_env_keys(paths, ["BAAS_API_KEY"])  # para que ni init ni status la vuelvan a probar
                _p(STALE_KEY_NOTICE)
            elif args.app_url or not values.get("BAAS_APP_URL"):
                update_env(paths, {"BAAS_APP_URL": app_url})  # solo tras contactar con éxito
    if api_key is None:
        _start_flow(paths, proj, app_url)
    if hs is None:
        hs = _handshake(app_url, api_key)

    try:
        previous = project.read_binding(proj) or {}
    except ProjectSafetyError:  # dañado: se repara más abajo, ya con la organización elegida
        previous = {}
    current = previous.get("org")
    org = handshake.select_organization(hs, requested=args.org, current=current)

    # Solo ahora se toca el proyecto: una salida 3 o 4 no cambia nada. Las copias de lo dañado pueden llevar la
    # clave, así que se ignoran en git antes de escribir nada y siempre que existan.
    repaired = [project.repair_json_file(proj / name) for name in project.GITIGNORE_ENTRIES]
    if any(repaired):
        _p(REPAIRED_NOTICE)
    ignore = project.GITIGNORE_ENTRIES + (
        project.BACKUP_ENTRIES if any(repaired) or project.backups_exist(proj) else ()
    )
    added = project.ensure_gitignore(proj, ignore)

    settings_changed = project.write_settings_local(proj, org.proxy_anthropic_url, api_key)
    binding_changed = project.write_binding(
        proj,
        {
            "org": org.slug,
            "org_name": org.name,
            "user_email": hs.email,
            "app_url": app_url,
            "mcp_url": org.mcp_url,
            "proxy_anthropic_url": org.proxy_anthropic_url,
            "proxy_openai_url": org.proxy_openai_url,
            "bound_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if current != org.slug
            else previous.get("bound_at"),
            "plugin_version": __version__,
        },
    )
    helper = mcp_config.install_headers_helper(paths)
    reg = mcp_config.register_mcp_server(proj, org.mcp_url, mcp_config.helper_command(helper))

    action = "vinculado" if current != org.slug else "actualizado"
    # `unchanged` solo si nada cambia para la persona: misma organización, clave que ya valía y nada reescrito
    # en el proyecto (ajustes, vinculación ni reparaciones) ni pendiente en el MCP. Si se reescribió algo, la sesión
    # en marcha aún tiene lo anterior y hay que reiniciar. El registro del MCP se rehace siempre, así que "nuevo" se
    # deduce de que cambie la vinculación (su dirección) o de que no haya podido registrarse.
    unchanged = (
        current == org.slug
        and not via_approval
        and not any(repaired)
        and not settings_changed
        and not binding_changed
        and reg.registered
    )
    _p(
        f"Proyecto {proj} {action}: organización {org.describe()}.",
        f"Usuario: {hs.email} ({hs.platform_role}).",
        f"Proxy de Anthropic: {org.proxy_anthropic_url} (en .claude/settings.local.json).",
        f"MCP `baas`: {org.mcp_url}.",
    )
    if added:
        _p(f"Añadido a .gitignore: {', '.join(added)}.")
    if reg.registered:
        _p("Servidor MCP `baas` registrado en Claude Code (ámbito local de este proyecto).")
    else:
        _p(
            f"AVISO: no se pudo registrar el MCP automáticamente ({reg.detail}).",
            "Ejecuta en una terminal (no contiene la API key):",
            f"  {reg.manual_command}",
        )
    for warning in handshake.kill_switch_warnings(hs, org):
        _p(f"AVISO: {warning}")
    _p(
        "Siguiente paso: reinicia Claude Code en este proyecto (acepta la confianza en la carpeta si la pide)"
        " para cargar el MCP `baas` y el proxy; comprueba con /baas-status.",
        "Después, /baas-sync trae las skills de tu organización (también se sincronizan solas al iniciar y"
        " cerrar la sesión).",
        f"[BAAS_RESULT {'unchanged' if unchanged else 'linked'}]",
    )
    return 0


# --- status ----------------------------------------------------------------------------------


def _yes(flag: bool) -> str:
    return "ACTIVO" if flag else "no"


def cmd_status(args: argparse.Namespace) -> int:
    paths = BaasPaths.default()
    if not paths.env_file.exists():
        raise missing_key_error(paths)
    creds = load_credentials(paths)
    proj = _project_dir(args)
    binding = project.read_binding(proj)
    _p(f"API key: {key_prefix(creds.api_key)} en {paths.env_file} · webapp: {creds.app_url or 'sin configurar'}")
    if not creds.app_url:
        raise MissingAppUrlError(
            "No sé a qué dirección de BaaS-AI-IP conectarme.", hint="Escribe /baas-init y te la pregunto."
        )
    hs = handshake.perform_handshake(creds.app_url, creds.api_key)
    _p(
        f"Usuario: {hs.email} · rol de plataforma: {hs.platform_role}",
        f"Kill switch global: {_yes(hs.global_kill_switch)}",
    )
    if binding is None:
        _p(f"El proyecto {proj} no está vinculado a ninguna organización: lanza /baas-init.")
        _p("Tus organizaciones:", *(f"  - {o.describe()}" for o in hs.organizations))
        return 0
    org = hs.find(str(binding.get("org")))
    if org is None:
        _p(
            f"AVISO: el proyecto está vinculado a «{binding.get('org')}», pero ya no tienes acceso a esa"
            " organización. Lanza /baas-init para elegir otra.",
        )
        return 0
    roles = ", ".join(org.functional_roles) or "ninguno"
    _p(
        f"Organización del proyecto: {org.slug} · {org.name}",
        f"Rol de gobierno: {org.governance_role} · roles funcionales: {roles}",
        f"Bloqueo de plataforma: {_yes(org.blocked)} · pausa de la organización: {_yes(org.paused)}"
        + (" (pausada por su administrador)" if org.paused else ""),
        f"AI-IP: {'activado' if org.ai_ip_enabled else 'desactivado (bypass)'}",
    )
    for warning in handshake.kill_switch_warnings(hs, org):
        _p(f"AVISO: {warning}")
    _check_local_config(proj, org)
    claude = sync.claude_paths(args)
    if args.no_mcp:
        _p(*sync.status_lines(binding, proj, claude))
    else:
        _check_mcp(creds.api_key, org.mcp_url, binding, proj, claude)
    return 0


def _check_local_config(proj: Path, org: handshake.Organization) -> None:
    env = project.read_settings_env(proj)
    if env.get("ANTHROPIC_BASE_URL") != org.proxy_anthropic_url or project.HEADER_NAME not in str(
        env.get("ANTHROPIC_CUSTOM_HEADERS", "")
    ):
        _p("AVISO: .claude/settings.local.json no coincide con la organización; lanza /baas-init.")
    if os.environ.get("CLAUDECODE") == "1":
        # Mismo diagnóstico que la guardia de los hooks (scripts/baas_guard.py).
        problem = baas_guard.diagnose(project.read_binding(proj), dict(os.environ))
        if problem:
            _p(f"AVISO: {problem.removeprefix('BaaS-AI-IP: ')}")
        else:
            _p("Proxy activo en esta sesión: sí.")


# --- desktop-config --------------------------------------------------------------------------


def cmd_desktop_config(args: argparse.Namespace) -> int:
    paths = BaasPaths.default()
    if not paths.env_file.exists():
        raise missing_key_error(paths)
    creds = load_credentials(paths)
    if not creds.app_url:
        raise MissingAppUrlError(
            "No sé a qué dirección de BaaS-AI-IP conectarme.", hint="Escribe /baas-init y te la pregunto."
        )
    proj = _project_dir(args)
    current = (project.read_binding(proj) or {}).get("org")
    hs = handshake.perform_handshake(creds.app_url, creds.api_key)
    org = handshake.select_organization(hs, requested=args.org, current=current)
    helper = desktop_config.install_credentials_helper(paths)
    config = desktop_config.gateway_config(org.proxy_anthropic_url, helper)
    values, _ = read_env(paths)
    _p(
        f"Claude Desktop en modo gateway hacia el proxy de la organización {org.describe()}.",
        "",
        "Ten en cuenta:",
        "  - Es GLOBAL a Claude Desktop: todas sus sesiones pasarán por esta organización.",
        "  - Desktop deja de usar tu suscripción de claude.ai: el proxy reenvía a Anthropic la API key"
        " de Anthropic Console guardada en ~/baas-ai-ip/.env (ANTHROPIC_API_KEY).",
        "  - El soporte del modo gateway en Desktop para Linux no está confirmado.",
        "",
        "Pasos:",
        "  1. Claude Desktop → Help → Troubleshooting → Enable Developer Mode.",
        "  2. Developer → Configure Third-Party Inference… e introduce estos valores"
        " (o distribúyelos por MDM / fichero de configuración):",
        "",
        json.dumps(config, indent=2, ensure_ascii=False),
        "",
        "  3. Reinicia Claude Desktop y elige el modo de terceros (no «Sign in to Claude.ai»).",
        "  4. En un proyecto vinculado, /baas-status debe decir «Proxy activo en esta sesión: sí».",
        "",
        f"Helper de credenciales instalado en {helper} (lee las claves de {paths.env_file}; no contiene secretos).",
    )
    if not values.get("ANTHROPIC_API_KEY"):
        _p(
            "AVISO: falta ANTHROPIC_API_KEY en ~/baas-ai-ip/.env. Guárdala en una terminal, sin pasar por el chat:",
            f"  python3 {Path(__file__).resolve().parent.parent / 'baas.py'} set-key --anthropic",
        )
    for warning in handshake.kill_switch_warnings(hs, org):
        _p(f"AVISO: {warning}")
    return 0


def _check_mcp(
    api_key: str, mcp_url: str, binding: dict | None = None, proj: Path | None = None, claude: ClaudePaths | None = None
) -> None:
    from .mcp_client import McpClient  # import diferido: status sin MCP no lo necesita

    client = None
    try:
        with McpClient(mcp_url, api_key) as client:
            try:
                client.call_tool("baas_whoami", {})
                _p("MCP `baas`: accesible.")
            except BaasError as exc:
                _p(f"MCP `baas`: ERROR · {exc.message}")
            if proj is not None and claude is not None:  # skills sincronizadas, desfasadas y revocadas (H5)
                _p(*sync.status_lines(binding, proj, claude, client))
    except BaasError as exc:
        _p(f"MCP `baas`: ERROR · {exc.message}")
        if proj is not None and claude is not None and client is None:
            _p(*sync.status_lines(binding, proj, claude))


# --- main ------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baas", description="Plugin de BaaS-AI-IP para Claude Code")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    from .skillcmds import add_skill_package_parsers  # inspect, publish y export (H5)

    add_skill_package_parsers(sub)

    p = sub.add_parser("set-key", help="Guarda la API key en ~/baas-ai-ip/.env sin mostrarla")
    p.add_argument("--stdin", action="store_true", help="Lee la clave de la entrada estándar (tubería)")
    p.add_argument("--app-url", help="URL de la webapp, p. ej. https://app.<dominio>")
    p.add_argument(
        "--anthropic",
        action="store_true",
        help="Guarda la API key de Anthropic Console (ANTHROPIC_API_KEY) para Claude Desktop en modo gateway",
    )
    p.set_defaults(func=cmd_set_key)

    p = sub.add_parser("init", help="Handshake y vinculación del proyecto a una organización")
    p.add_argument("--project", help="Raíz del proyecto (por defecto CLAUDE_PROJECT_DIR o la raíz git)")
    p.add_argument("--org", help="Slug de la organización (obligatorio si perteneces a varias)")
    p.add_argument("--app-url", help="URL de la webapp (se recuerda cuando el servidor responde)")
    p.add_argument(
        "--new-account",
        action="store_true",
        help="Pide aprobar la conexión de nuevo con otra cuenta; la clave guardada solo se sustituye si se aprueba",
    )
    p.add_argument("--wait", action="store_true", help="Espera (hasta 90 s) la aprobación en el navegador y termina")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="Usuario, organización, roles, kill switch, AI-IP y skills sincronizadas")
    p.add_argument("--project")
    p.add_argument("--no-mcp", action="store_true", help="No comprobar la conexión con el MCP")
    sync.add_claude_home_args(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("desktop-config", help="Configuración de Claude Desktop en modo gateway hacia el proxy")
    p.add_argument("--project", help="Proyecto cuya organización se toma por defecto")
    p.add_argument("--org", help="Slug de la organización (obligatorio si perteneces a varias y no hay proyecto)")
    p.set_defaults(func=cmd_desktop_config)

    from .runner import add_run_script_parser  # el ejecutor vive en su propio módulo
    from .setup_tools import add_setup_parser  # `setup <herramienta>`: otras herramientas de IA (T3, #165)

    add_run_script_parser(sub)
    sync.add_sync_parser(sub)
    add_setup_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    script_args: list[str] = []
    if "--" in argv:  # lo que sigue a `--` es para el script de run-script
        cut = argv.index("--")
        argv, script_args = argv[:cut], argv[cut + 1 :]
    args = build_parser().parse_args(argv)
    args.script_args = script_args
    try:
        return int(args.func(args))
    except BaasError as exc:
        if args.command == "run-script":
            # Un script puede salir con cualquier código (también 9 o 10): esta línea marca un error del plugin.
            print(f"BAAS_ERROR={exc.error_code}", file=sys.stderr)
        print(exc.render(), file=sys.stderr)
        tried = getattr(exc, "app_url", None)
        if tried:  # solo con la dirección que se probó, sin credenciales (ver `_public_url`)
            print(f"[BAAS_APP_URL {tried}]", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("Cancelado.", file=sys.stderr)
        return 130
