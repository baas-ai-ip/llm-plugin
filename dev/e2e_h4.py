#!/usr/bin/env python3
"""Prueba de punta a punta del hito H4 contra una pila de BaaS-AI-IP con el proveedor falso con eco.

Por HTTP directo (sin Claude Code), con las claves deterministas del contrato §8 de `observador@observacion.test`,
`admin@baas.test` (PLATFORM_ADMIN), `orgadmin@demo.test` y `analista@demo.test`:

  0-4.  La webapp responde; `/_debug/requests` del proveedor falso responde y se vacía; *handshake* de
        `observador@observacion.test` (AI-IP activo, anonimizador apagado); sesiones; `observacion` con el
        anonimizador apagado.
  5-7.  `observacion` (anonimizador apagado), en Messages, Chat Completions y Responses, con y sin *streaming*: un DNI,
        un email y un IBAN llegan **en claro** al proveedor (el cuerpo sale tal como entró), el cliente recibe lo mismo,
        el turno sale `WARNING` con las coincidencias de las tres reglas y la webapp dice que el anonimizador estaba
        apagado. El PLATFORM_ADMIN no obtiene el contenido por la API (lista, detalle, turnos y revelado) ni por el
        WebSocket, tampoco con AI-IP apagado (`demo`).
  8.    El interruptor, en los dos sentidos: permisos y validaciones; una conversación que empezó con el anonimizador
        apagado sigue en claro aunque se encienda, una nueva sale tokenizada; la tokenizada sigue tokenizada (y
        restaurada) aunque se apague, una nueva sale en claro. El interruptor vuelve a su valor inicial.
  9.    Eliminar una organización: crear, bloquear y eliminar con confirmación del slug; la webapp, el proxy y el MCP
        dejan de servirla, el slug no se reutiliza, la auditoría lo recoge, el vault queda vacío, Redis (las dos
        instancias) sin sus claves y el directorio del MCP en cuarentena.
  10.   Los originales no llegan al disco de Redis (H4-02, la prueba canario): turnos con valores centinela en bypass,
        observación y AI-IP con anonimizador, por las tres APIs; ni el AOF de la instancia persistente (antes y
        después de forzar su reescritura) ni nada de `/data` de la efímera (sin AOF, sin RDB, sin ficheros) los
        contiene. Un control positivo demuestra que el escáner ve el disco.
  11.   Usuarios que se quedan sin organización al eliminar una (contrato §6): la vista previa los lista, la
        eliminación los desactiva (`disable_orphans`, `true` por omisión) y revoca sus claves personales, quien
        conserva otra organización no cambia, la auditoría `user.disabled` no lleva su email, un ORG_ADMIN no los
        reactiva (`409 USER_DISABLED`) y el PLATFORM_ADMIN sí (`user.reactivated`, visible para el ORG_ADMIN de la
        organización; `user.disabled`, no). Crea sus propias organizaciones y usuarios (`huerf-<ejecución>-…`) y exige
        `BAAS_DEV_SKIP_TOTP=1` para activarlos.
  12.   Observabilidad de uso (Fase 4, grupo «uso», `dev/e2e_uso.py`): tráfico con tokens, bytes y franja local;
        invocaciones de skill (`/<org>:<skill>` y la herramienta `Skill`); sincronización; lecturas por área sin rutas
        en ningún dato; foto de disco frente al volumen del MCP; permisos y `usage.viewed`; el proxy con el consumidor
        y el MCP parados; retención y borrado; eventos repetidos; sobrecoste del proxy. Cada paso crea y elimina su
        organización (`uso-<ejecución>-…`).
  13.   ACL de Redis (H4-05, grupo «acl», `dev/e2e_acl.py`): con las credenciales reales de cada usuario, cada
        servicio solo escribe lo suyo y ninguno entra como `default`; lo que la matriz prohíbe recibe `NOPERM`.
  14.   Límite de claves no válidas por IP (H4-13, contrato §7.5), desde el contenedor `fake-upstream` (otra IP que la
        del equipo) a través de Caddy: 30 claves desconocidas distintas, cada una con un `X-Forwarded-For` falso
        distinto, reciben `401` y la 31.ª `429` con `Retry-After` en el proxy (`baas_auth_rate_limited`) y en el MCP
        (`RATE_LIMITED`); en una ráfaga lenta, el `429` puede llegar tras las fichas que volvieron mientras duraba. Una
        clave ya válida pasa desde la IP frenada; desde el equipo, una desconocida sigue en `401`.
  16.   Consumo de la plataforma (Fase 2, grupo «consumo», `dev/e2e_consumo.py`): el informe
        `GET /platform/usage` con `period` y datos hasta por tubería (16.0, y si falta se saltan los demás); turnos por
        modo (bypass, observación y anonimizado) que cuadran con los enviados (16.1); disco con media, máximo, última
        y el índice aparte (16.2); el CSV igual que el JSON (16.3); `403` a un ORG_ADMIN y a un USER y `401` sin sesión
        en el informe y en las cuatro rutas globales (16.4); el tráfico global es la suma de las organizaciones y cada
        una es su propio informe (16.5); el anonimizador de una organización es el de plataforma, con sus permisos
        (16.6); las consultas globales y del anonimizador no dejan `usage.viewed` (16.7); con el NER, las columnas de
        L2 se llenan (16.8; se salta sin NER, salvo que se declare con `--ner` o `BAAS_E2E_NER=1`: entonces, que no
        llegue ningún `verification.l2` es un fallo). Cada paso crea y elimina su organización
        (`consumo-<ejecución>-…`); no mira dentro de los contenedores.

  15.   NER de la Fase 2 (L2, grupo «ner», `dev/e2e_ner.py`): los interruptores del NER con motivo y auditoría; un
        nombre, una empresa y una dirección salen como tokens y vuelven restaurados (tres APIs, con y sin *streaming*);
        el segundo turno solo analiza lo nuevo con el historial idéntico; con la caché de la efímera perdida el
        historial sale igual y no se espera; con el servicio `ner` parado, `DEGRADE` da `DEGRADED` y `REJECT` da
        `503`; la observación (`observed_detections`) sin cambiar el estado del turno; y la prueba en vivo con
        `ALLOWLISTED`. Cada paso crea y elimina su organización (`ner-<ejecución>-…`).

Uso (pila de `deploy` con el proveedor falso: `scripts/dev-up.sh --fake-upstream`):

    uv run python dev/e2e_h4.py --app-url http://app.localhost:8080 --fake-url http://127.0.0.1:18090

Los pasos 9.1b y 9.8 (vault, Redis y cuarentena), el grupo 10, los pasos 12.4, 12.5, 12.7, 12.8 y 12.9, los grupos
13 y 14 y los pasos 15.4 y 15.5 miran dentro de los contenedores (o los paran, o envían desde uno) con
`podman`/`docker` (`--inspect none` los omite). Sale con 0 si todo pasa, 1 si algún paso falla y 2 si los argumentos
no valen. No se distribuye con el plugin.
`dev/fake_h4_stack.py` simula la pila (sin el grupo 12, ni el 15, ni el disco y el CSV de 16.2 y 16.3) para probarlo
sin ella.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import select
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_acl
import e2e_consumo
import e2e_h3 as h3
import e2e_ner
import e2e_uso
from e2e_h2 import DEV_PASSWORD, DEV_TOTP_SECRET, Http, Runner, StepError, check, dev_api_key

from baas_plugin.errors import AuthError, McpToolError
from baas_plugin.mcp_client import McpClient

DNI, EMAIL, IBAN, VALUES = h3.DNI, h3.EMAIL, h3.IBAN, h3.VALUES
SENSITIVE_TEXT, TOKENS = h3.SENSITIVE_TEXT, h3.TOKENS
APIS, API_NAMES = h3.APIS, h3.API_NAMES
GROUPS = ("observacion", "interruptor", "eliminar", "originales", "huerfanos", "uso", "acl", "limite", "ner", "consumo")
META_ORG = "baas-ai-ip"
FORBIDDEN_TURN_KEYS = ("request_sent", "response_received", "request_original", "response_returned")
VALUE_KEYS = ("original_value", "value")
REASON = "Prueba de punta a punta de H4"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# Grupo «huérfanos»: organizaciones (O1 a O4) y personas de la prueba. Persona → (organización, rol de gobierno)
ORPHAN_ORGS = ("o1", "o2", "o3", "o4")
ORPHAN_PEOPLE = {"a": ("o1", "USER"), "b": ("o1", "ORG_ADMIN"), "x": ("o2", "ORG_ADMIN"), "c": ("o3", "USER"),
                 "e": ("o3", "USER"), "f": ("o4", "USER"), "g": ("o4", "USER")}  # fmt: skip
ORPHAN_ACTIVATED = ("a", "b", "x", "c", "f")  # las demás (e, g) se quedan INVITED, sin activar la cuenta
ORPHAN_KEYED = ("a", "b", "c", "f")  # con una clave personal
ORPHAN_DOMAIN = "huerf.test"
REVOKED_SUFFIX = " (clave revocada)"
# Grupo «limite» (H4-13, contrato §7.5): el texto del 429 del proxy, sin la espera (`; vuelve a intentarlo en N s`)
LIMIT_MESSAGE = "BaaS-AI-IP: demasiadas API keys no válidas desde tu red"
LIMIT_MARGIN = 1.0  # segundos de más al esperar a que se llene un cubo
# Claves desconocidas de más en cada ráfaga, por si vuelven fichas mientras dura (una cada 6 s): cubren una ráfaga de
# hasta 36 s. Tras el primer `429` no cuentan.
LIMIT_SLACK = 5


# --- utilidades puras ------------------------------------------------------------------------------


def anonymizer_flag(item: Any) -> bool | None:
    """El anonimizador con el que empezó la conversación (o el de la organización) que expone la webapp."""
    if isinstance(item, dict):
        for key in ("anonymizer_enabled_at_start", "anonymizer_enabled"):
            if isinstance(item.get(key), bool):
                return item[key]
    return None


def segments_text(value: Any) -> str:
    """Texto de un campo de la vista de turno: segmentos `{"text"}` y `{"token"}` o una cadena."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(s.get("text") or s.get("token") or "") for s in value if isinstance(s, dict))
    return ""


def needles_for(nonce: str) -> list[str]:
    """Lo que no puede aparecer en lo que ve un PLATFORM_ADMIN: los valores, el texto del prompt y el eco."""
    return [DNI, EMAIL, IBAN, f"caso-{nonce}", "Eco:"]


def disk_needles(nonce: str) -> list[str]:
    """Lo que no puede estar en el disco de Redis: los tres valores (el IBAN también sin espacios) y la cadena de la
    ejecución, que lleva cada texto enviado."""
    return [DNI, EMAIL, IBAN, IBAN.replace(" ", ""), *run_needles(nonce)]


def run_needles(nonce: str) -> list[str]:
    """Lo único que es de ESTA ejecución: ningún AOF de una ejecución anterior puede contenerlo."""
    return [f"caso-{nonce}"]


def content_leaks(raw: str | bytes, needles: Iterable[str]) -> list[str]:
    """Qué `needles` aparecen en una respuesta, en bruto o en cualquiera de sus cadenas decodificadas."""
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    try:
        decoded = "\n".join(h3.strings_in(json.loads(text)))
    except ValueError:
        decoded = ""
    haystack = f"{text}\n{decoded}"
    return [f"«{needle}»" for needle in needles if needle in haystack]


def _keys_in(value: Any) -> set[str]:
    out: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            out |= {str(k) for k in item}
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out


def admin_turn_problems(turn: Any, *, metrics: bool = True) -> list[str]:
    """Un turno de la vista de verificación sin contenido: `content_available` falso, ningún campo de texto ni valor."""
    if not isinstance(turn, dict):
        return [f"el turno no es un objeto JSON: {turn!r}"]
    problems = []
    if turn.get("content_available") is not False:
        problems.append(f"content_available = {turn.get('content_available')!r} (se esperaba false)")
    present = [key for key in FORBIDDEN_TURN_KEYS if key in turn]
    if present:
        problems.append(f"el turno trae {', '.join(present)}")
    values = sorted(_keys_in(turn.get("replacements")) & set(VALUE_KEYS))
    if values:
        problems.append(f"los reemplazos traen valores ({', '.join(values)})")
    if metrics and not isinstance(turn.get("verification"), dict):
        problems.append("el turno no conserva las métricas de verificación")
    return problems


def observation_problems(verification: Any) -> list[str]:
    """Lo que impide dar por buena la verificación de un turno en modo observación (contrato §5.2)."""
    if not isinstance(verification, dict):
        return [f"el turno no tiene verificación ({verification!r})"]
    problems = []
    if verification.get("status") != "WARNING":
        problems.append(f"verification.status = {verification.get('status')} (se esperaba WARNING)")
    for name in ("request_replacements_by_type", "response_replacements_by_type"):
        if verification.get(name):
            problems.append(f"{name} = {verification.get(name)} (con el anonimizador apagado no se sustituye nada)")
    counts: dict[str, int] = {}
    for match in verification.get("residual_matches") or []:
        if isinstance(match, dict):
            kind = str(match.get("token_type"))
            counts[kind] = counts.get(kind, 0) + int(match.get("count") or 0)
    missing = [kind for kind in VALUES if counts.get(kind, 0) < 1]
    if missing:
        problems.append(f"residual_matches sin {', '.join(missing)}: {verification.get('residual_matches')}")
    for name in ("unresolved_response_tokens", "orphan_request_tokens"):
        if verification.get(name):
            problems.append(f"{name} = {verification.get(name)}")
    return problems


def case_model(nonce: str, group: str, api: str, mode: str) -> str:
    return f"e2e-h4-{nonce}-{group}-{api}-{mode}"


def new_slug(nonce: str) -> str:
    return f"e2e-{nonce}"


def org_data(body: Any) -> dict[str, Any]:
    """La organización de una respuesta: anidada en `organization` o con sus campos en la raíz."""
    if isinstance(body, dict):
        nested = body.get("organization")
        return nested if isinstance(nested, dict) else body
    return {}


@dataclass
class Person:
    """Un usuario que crea el grupo «huérfanos» (11): su email y su nombre son únicos de la ejecución."""

    label: str
    email: str
    name: str
    user_id: str = ""
    link: str = ""  # token del enlace de activación vigente («» si ya activó la cuenta)
    key_id: str = ""  # su clave personal vigente


def activation_token(url: Any) -> str:
    """El token de un enlace de activación: lo último de la ruta de `activation_url`."""
    return str(url or "").rstrip("/").rsplit("/", 1)[-1]


def is_count(value: Any) -> bool:
    """Un entero (no un booleano), como `disabled_users` o `personal_keys`."""
    return isinstance(value, int) and not isinstance(value, bool)


def audit_rows(body: Any, action: str, target_id: str) -> list[dict[str, Any]]:
    """Las filas de `action` sobre `target_id` de una respuesta de `GET /api/v1/audit`."""
    items = body.get("items") if isinstance(body, dict) else None
    return [
        i for i in items or [] if isinstance(i, dict) and i.get("action") == action and i.get("target_id") == target_id
    ]


def api_error(res: Http) -> str:
    """`HTTP <estado> <error>` de una respuesta, sin su cuerpo (puede traer enlaces de activación o claves)."""
    data = res.json()
    code = data.get("error") if isinstance(data, dict) else None
    return f"HTTP {res.status}" + (f" {code}" if isinstance(code, str) else "")


def reactivated_audit_problems(row: Any, *, org_slug: str, actor_email: str, new_status: str) -> list[str]:
    """Una fila `user.reactivated` (contrato §6): quien invita como actor, la organización a la que se invita como
    `org_id` (su ORG_ADMIN la ve) y `{org_slug, new_status}`; `new_status` es `ACTIVE` si el usuario ya tenía contraseña
    e `INVITED` si nunca activó la cuenta. Ya no hay `previous_status`."""
    if not isinstance(row, dict):
        return [f"la fila no es un objeto JSON: {row!r}"]
    problems = []
    actor = row.get("actor")
    if not isinstance(actor, dict) or actor.get("email") != actor_email:
        problems.append(f"user.reactivated: actor = {actor!r} (se esperaba {actor_email})")
    if row.get("org_slug") != org_slug:
        problems.append(
            f"user.reactivated: org_slug = {row.get('org_slug')!r} (se esperaba la organización {org_slug})"
        )
    meta = row.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    for name, want in (("org_slug", org_slug), ("new_status", new_status)):
        if meta.get(name) != want:
            problems.append(f"user.reactivated: metadata.{name} = {meta.get(name)!r} (se esperaba {want!r})")
    if "previous_status" in meta:
        problems.append(f"user.reactivated: metadata.previous_status = {meta['previous_status']!r} (ya no existe)")
    return problems


def preview_problems(body: Any, orphans: Sequence[tuple[Person, str, int]], *, members: int) -> list[str]:
    """La vista previa de eliminar una organización (contrato §6): lista **exactamente** a `orphans` (persona, estado
    y claves personales vigentes) y a nadie más, y cuenta los `members` de la organización."""
    if not isinstance(body, dict):
        return [f"la vista previa no es un objeto JSON: {body!r}"]
    problems = []
    if not is_count(body.get("members")) or body.get("members") != members:
        problems.append(f"members = {body.get('members')!r} (se esperaba {members})")
    if not is_count(body.get("conversations")) or body["conversations"] < 0:
        problems.append(f"conversations = {body.get('conversations')!r} (se esperaba un entero)")
    listed = body.get("orphans")
    if not isinstance(listed, list):
        return [*problems, f"orphans no es una lista: {listed!r}"]
    got = {str(o.get("email")): o for o in listed if isinstance(o, dict)}
    if len(got) != len(listed):
        problems.append(f"orphans trae entradas repetidas o sin email: {listed!r}")
    wanted = {person.email for person, _, _ in orphans}
    if sorted(set(got) - wanted):
        problems.append(f"lista a quien no se quedaría sin organización: {', '.join(sorted(set(got) - wanted))}")
    if sorted(wanted - set(got)):
        problems.append(f"no lista a: {', '.join(sorted(wanted - set(got)))}")
    for person, status, keys in orphans:
        entry = got.get(person.email)
        if entry is None:
            continue
        expected = {"id": person.user_id, "name": person.name, "status": status, "personal_keys": keys}
        for name, want in expected.items():
            if entry.get(name) != want or (name == "personal_keys" and not is_count(entry.get(name))):
                problems.append(f"{person.label}: {name} = {entry.get(name)!r} (se esperaba {want!r})")
    return problems


def disabled_audit_problems(
    row: Any, *, org_slug: str, previous_status: str, actor_email: str, reason: str, forbidden: Sequence[str],
    min_keys: int,
) -> list[str]:  # fmt: skip
    """Una fila `user.disabled` de la auditoría (contrato §6): quien elimina como actor, `org_id` nulo, el motivo de la
    eliminación, `{cause: org.deleted, org_slug, previous_status, keys_revoked}` y **ni el email ni el nombre** del
    usuario. `target_label` no cuenta: la webapp lo deriva al leer, resolviendo la cuenta, y no se guarda en la fila."""
    if not isinstance(row, dict):
        return [f"la fila no es un objeto JSON: {row!r}"]
    problems = []
    if row.get("target_type") != "user":
        problems.append(f"target_type = {row.get('target_type')!r} (se esperaba user)")
    if row.get("reason") != reason:
        problems.append(f"reason = {row.get('reason')!r} (se esperaba el motivo de la eliminación)")
    if row.get("org_slug"):
        problems.append(f"org_slug = {row.get('org_slug')!r} (se esperaba nulo: la organización ya no existe)")
    actor = row.get("actor")
    if not isinstance(actor, dict) or actor.get("email") != actor_email:
        problems.append(f"actor = {actor!r} (se esperaba quien elimina, {actor_email})")
    meta = row.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    for name, want in (("cause", "org.deleted"), ("org_slug", org_slug), ("previous_status", previous_status)):
        if meta.get(name) != want:
            problems.append(f"metadata.{name} = {meta.get(name)!r} (se esperaba {want!r})")
    if not is_count(meta.get("keys_revoked")) or meta["keys_revoked"] < min_keys:
        problems.append(f"metadata.keys_revoked = {meta.get('keys_revoked')!r} (se esperaba un entero ≥ {min_keys})")
    visible = "\n".join(h3.strings_in({k: v for k, v in row.items() if k != "target_label"})).lower()
    leaked = [f"«{value}»" for value in forbidden if value and value.lower() in visible]
    if leaked:
        problems.append(f"la fila lleva {', '.join(leaked)}: no debe llevar el email ni el nombre")
    return problems


# --- grupo «limite»: claves no válidas frenadas por IP (H4-13, contrato §7.5) ----------------------------------


@dataclass
class CaddyRequest:
    """Un `POST` que el grupo «limite» envía desde el contenedor `fake-upstream` a Caddy, que lo enruta por `host`
    (`proxy.<dominio>` o `mcp.<dominio>`) como a cualquier petición de fuera."""

    host: str
    path: str
    headers: dict[str, str]
    body: str


MCP_INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "baas-e2e-h4", "version": "1"}}}  # fmt: skip


def unknown_key() -> str:
    """Una clave bien formada que no existe: `baas_` y 43 caracteres base64url, como las de verdad. Una mal formada se
    rechaza sin consultar y no gastaría ficha."""
    return "baas_" + secrets.token_urlsafe(32)


def spoofed_ips(count: int) -> list[str]:
    """`count` direcciones IPv4 distintas y aleatorias para un `X-Forwarded-For` falso (Caddy debe descartarlo)."""
    ips: dict[str, None] = {}
    while len(ips) < count:
        ips[str(ipaddress.IPv4Address(secrets.randbits(32)))] = None
    return list(ips)


def leading(replies: Sequence[Http], status: int) -> int:
    """Cuántas respuestas seguidas, desde la primera, tienen `status`."""
    return next((i for i, res in enumerate(replies) if res.status != status), len(replies))


def response_header(res: Http, name: str) -> str | None:
    return next((v for k, v in res.headers if k.lower() == name.lower()), None)


def reply_text(res: Http) -> str:
    """`HTTP <estado>` y el principio del cuerpo (las respuestas de esta parte no llevan la clave)."""
    return f"HTTP {res.status} {h3.snippet(res.body, 160)}"


def rejected_problems(res: Http, kind: str) -> list[str]:
    """Una clave desconocida que se consultó: `401` (en el proxy, `baas_invalid_api_key`). `kind`: `chat` o `mcp`."""
    if res.status != 401:
        return [f"{reply_text(res)} (se esperaba 401)"]
    if kind != "mcp" and h3.provider_error(res.body)[1] != "baas_invalid_api_key":
        return [f"{reply_text(res)} (se esperaba 401 baas_invalid_api_key)"]
    return []


def limited_problems(res: Http, kind: str) -> list[str]:
    """El `429` del limitador: `Retry-After` en segundos (entero ≥ 1) y el cuerpo de `kind` con esa espera. `chat` y
    `messages`, el proxy con la forma del proveedor (OpenAI `requests` · `baas_auth_rate_limited`; Anthropic
    `rate_limit_error`) y su mensaje; `mcp`, `{"error": "RATE_LIMITED", "message": …}`."""
    if res.status != 429:
        return [f"{reply_text(res)} (se esperaba 429)"]
    value = response_header(res, "Retry-After")
    seconds = int(value) if value is not None and re.fullmatch(r"[0-9]{1,9}", value) and int(value) >= 1 else None
    problems = [] if seconds is not None else [f"Retry-After = {value!r} (se esperaba un entero de segundos ≥ 1)"]
    wait = f"vuelve a intentarlo en {seconds} s"
    if kind == "mcp":
        data = res.json()
        data = data if isinstance(data, dict) else {}
        if data.get("error") != "RATE_LIMITED":
            problems.append(f"error = {data.get('error')!r} (se esperaba RATE_LIMITED): {reply_text(res)}")
        elif seconds is not None and wait not in str(data.get("message")):
            problems.append(f"message = {data.get('message')!r} (se esperaba la espera, «{wait}»)")
        return problems
    etype, code, message = h3.provider_error(res.body)
    want = ("rate_limit_error", None) if kind == "messages" else ("requests", "baas_auth_rate_limited")
    if (etype, code) != want:
        problems.append(f"error.type · error.code = {etype} · {code} (se esperaba {want[0]} · {want[1]})")
    elif seconds is not None and message != f"{LIMIT_MESSAGE}; {wait}":
        problems.append(f"message = {message!r} (se esperaba «{LIMIT_MESSAGE}; {wait}»)")
    return problems


def extra_tokens(elapsed: float, per_minute: float) -> int:
    """Fichas que pueden volver a un cubo durante una ráfaga de `elapsed` segundos."""
    return math.floor(elapsed * per_minute / 60)


def token_came_back(replies: Sequence[Http], *, burst: int, elapsed: float, per_minute: float) -> bool:
    """La última respuesta es una `401` que pudo llevarse una ficha que volvió durante la ráfaga: en una ráfaga que
    empezó con el cubo lleno, las `401` no pasan de `burst` más las que pudieron volver en `elapsed` segundos. En una
    ráfaga rápida (menos de lo que tarda en volver una ficha), nunca."""
    if not replies or replies[-1].status != 401:
        return False
    return sum(r.status == 401 for r in replies) <= burst + extra_tokens(elapsed, per_minute)


def burst_problems(
    replies: Sequence[Http], *, burst: int, kind: str, elapsed: float, per_minute: float
) -> list[str]:  # fmt: skip
    """Una ráfaga de claves desconocidas desde un cliente con el cubo lleno: las `burst` primeras se consultan (`401`)
    y el primer `429` del limitador llega en cuanto se gastan las fichas, las del cubo y las que volvieron mientras
    duraba la ráfaga (`extra_tokens`): en la posición `burst + 1` o, en una ráfaga lenta, hasta
    `burst + 1 + floor(elapsed · per_minute / 60)`. Las respuestas tras el primer `429` no cuentan."""
    most = burst + extra_tokens(elapsed, per_minute)
    n = leading(replies, 401)
    for i, res in enumerate(replies[: min(n, most)], 1):
        problems = rejected_problems(res, kind)
        if problems:
            return [f"la {i}.ª clave: {problems[0]} (el cubo admite {burst} antes del 429)"]
    if len(replies) <= burst:
        return [f"faltan respuestas: {len(replies)} de {burst + 1}"]
    if n < burst:
        return [f"la {n + 1}.ª clave: {rejected_problems(replies[n], kind)[0]} (el cubo admite {burst} antes del 429)"]
    without_limit = (
        "sin límite, o el cliente sale del X-Forwarded-For que envía la petición y no de la IP que pone Caddy"
    )
    allowed = f"como mucho {most}: {burst} del cubo y {most - burst} que pudieron volver en {elapsed:.1f} s"
    if n == len(replies):
        if n > most:
            return [f"las {n} claves recibieron 401 y ninguna 429 ({allowed}): {without_limit}"]
        return [
            f"las {n} claves recibieron 401 y ninguna 429, pero pudieron volver {most - burst} fichas en "
            f"{elapsed:.1f} s: la ráfaga fue demasiado lenta para concluir"
        ]
    if n > most:
        return [f"{n} × 401 antes del primer 429 ({allowed}): {without_limit}"]
    return [f"la {n + 1}.ª clave: {p}" for p in limited_problems(replies[n], kind)]


def refill_reason(replies: Sequence[Http], *, burst: int) -> str | None:
    """Por qué una ráfaga de claves no prueba nada y hay que repetirla con el cubo lleno (`None` si sí): el `429` llega
    antes de `burst` (otra ejecución dejó a medias el cubo de esa IP). Una ráfaga lenta no se repite: `burst_problems`
    acepta las fichas que volvieron mientras duraba. Cualquier otra respuesta (un `502`, un `200`) no se arregla
    esperando."""
    n = leading(replies, 401)
    if n < burst and n < len(replies) and replies[n].status == 429:
        return (
            f"{n} × 401 antes del primer 429: la IP del contenedor no tenía el cubo lleno (¿otra ejecución hace poco?)"
        )
    return None


# --- WebSocket mínimo (RFC 6455) con la biblioteca estándar --------------------------------------------


class WsClosedError(Exception):
    def __init__(self, code: int | None) -> None:
        super().__init__(f"el servidor cerró el WebSocket (código {code})")
        self.code = code


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()  # noqa: S324 (RFC 6455)


def ws_frame(opcode: int, payload: bytes = b"", *, mask: bool = True, fin: bool = True) -> bytes:
    """Una trama; las del cliente van enmascaradas."""
    head = bytes([(0x80 if fin else 0) | opcode])
    length = len(payload)
    flag = 0x80 if mask else 0
    if length < 126:
        head += bytes([flag | length])
    elif length < 1 << 16:
        head += bytes([flag | 126]) + struct.pack(">H", length)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", length)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


class WsClient:
    """Cliente de solo lectura de un WebSocket de texto (con cookie y `Origin`), sin dependencias."""

    def __init__(self, url: str, *, cookie: str, origin: str, timeout: float = 10) -> None:
        parts = urlsplit(url)
        host, port = parts.hostname or "localhost", parts.port or (443 if parts.scheme == "wss" else 80)
        if parts.scheme == "wss":
            raise StepError("wss no está soportado (la pila de desarrollo es HTTP)")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buffer = b""
        self.partial = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n"
            f"Cookie: baas_session={cookie}\r\nUser-Agent: {h3.USER_AGENT}\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n", timeout)
        lines = head.decode(errors="replace").split("\r\n")
        if " 101 " not in lines[0] + " ":
            self.close()
            raise StepError(f"WebSocket {url}: {lines[0]!r} (se esperaba 101)")
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:]) if k}
        if headers.get("sec-websocket-accept") != ws_accept(key):
            self.close()
            raise StepError("WebSocket: Sec-WebSocket-Accept incorrecto")

    def _fill(self, deadline: float) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.sock], [], [], remaining)[0]:
            return False
        chunk = self.sock.recv(65536)
        if not chunk:
            raise WsClosedError(None)
        self.buffer += chunk
        return True

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self.buffer:
            if not self._fill(deadline):
                raise StepError("el servidor no completó el handshake del WebSocket")
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def _need(self, count: int, deadline: float) -> bool:
        while len(self.buffer) < count:
            if not self._fill(deadline):
                return False
        return True

    def recv(self, timeout: float = 1.0) -> str | None:
        """Siguiente mensaje de texto o `None` si pasa `timeout` s. Responde `ping`; si cierra, `WsClosedError`."""
        deadline = time.monotonic() + timeout
        while True:
            if not self._need(2, deadline):
                return None
            first, second = self.buffer[0], self.buffer[1]
            length, offset = second & 0x7F, 2
            if length == 126:
                if not self._need(4, deadline):
                    return None
                length, offset = struct.unpack(">H", self.buffer[2:4])[0], 4
            elif length == 127:
                if not self._need(10, deadline):
                    return None
                length, offset = struct.unpack(">Q", self.buffer[2:10])[0], 10
            masked = bool(second & 0x80)
            if not self._need(offset + (4 if masked else 0) + length, deadline):
                return None
            key = self.buffer[offset : offset + 4] if masked else b""
            start = offset + (4 if masked else 0)
            payload = self.buffer[start : start + length]
            self.buffer = self.buffer[start + length :]
            if masked:
                payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
            opcode, fin = first & 0x0F, bool(first & 0x80)
            if opcode == 0x8:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                raise WsClosedError(code)
            if opcode == 0x9:
                self.sock.sendall(ws_frame(0xA, payload))
                continue
            if opcode in (0x1, 0x0):  # texto y continuación
                self.partial += payload
                if fin:
                    text, self.partial = self.partial, b""
                    return text.decode(errors="replace")
            # 0xA (pong) y binarios: se ignoran

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.sendall(ws_frame(0x8, struct.pack(">H", 1000)))
        with contextlib.suppress(OSError):
            self.sock.close()


def ws_url(app_url: str, path: str) -> str:
    return re.sub(r"^http", "ws", app_url.rstrip("/"), count=1) + path


# --- lo que hay dentro de los contenedores (vault, Redis y cuarentena del MCP) ------------------------------

PERSISTENT, EPHEMERAL = "redis", "redis-ephemeral"  # servicios de Redis (contrato §1, hito H4, H4-02)
REDIS_SERVICES = (PERSISTENT, EPHEMERAL)
REDIS_DATA_DIR = "/data"


class Inspector(Protocol):
    def vault_rows(self, slug: str) -> int: ...
    def conversation_ids(self, slug: str) -> list[str]: ...
    def redis_services(self) -> list[str]: ...
    def redis_keys(self, slug: str, conversation_ids: Sequence[str] = ()) -> list[str]: ...
    def redis_setting(self, service: str, name: str) -> str: ...
    def redis_residue(self, service: str) -> dict[str, int]: ...
    def disk_files(self, service: str) -> list[str]: ...
    def force_aof_rewrite(self, service: str) -> None: ...
    def disk_contains(self, service: str, needles: Sequence[str]) -> list[str]: ...
    def redis_set_temp(self, service: str, key: str, value: str, ttl: int) -> None: ...
    def redis_delete(self, service: str, key: str) -> None: ...
    def mcp_org_dirs(self) -> list[str]: ...
    def mcp_quarantine(self) -> list[str]: ...
    # Fase 4 (grupo «uso»)
    def usage_rows(self, org_id: str) -> dict[str, int]: ...
    def tagged_rows(self, tag: str) -> dict[str, int]: ...
    def usage_dump(self) -> str: ...
    def source_status(self) -> dict[str, str]: ...
    def insert_old_usage(self, org_id: str, user_id: str, *, months: int, tag: str) -> None: ...
    def snapshot_count(self, event_id: str) -> int: ...
    def key_exists(self, service: str, key: str) -> bool: ...
    def stream_group(self, service: str, stream: str, group: str) -> dict[str, str] | None: ...
    def stream_entries(self, service: str, stream: str) -> list[tuple[str, str]]: ...
    def stream_add(self, service: str, stream: str, data: str) -> str: ...
    def stream_has(self, service: str, stream: str, entry_id: str) -> bool: ...
    def service_control(self, service: str, action: str) -> None: ...
    def service_logs(self, service: str, since: str) -> str: ...
    def mcp_files(self, slug: str) -> list[tuple[str, int]]: ...
    # Fase 1 H4-05 (grupo «acl»)
    def redis_as(self, service: str, user: str, command: str) -> str: ...
    def redis_admin(self, service: str, *args: str) -> str: ...
    # H4-13 (grupo «limite»)
    def caddy_requests(self, requests: Sequence[CaddyRequest]) -> tuple[float, list[Http]]: ...
    # Fase 2, L2 (grupo «ner»)
    def ephemeral_scan(self, pattern: str) -> list[str]: ...
    def ephemeral_delete(self, keys: Sequence[str]) -> int: ...
    def ner_control(self, action: str) -> None: ...
    def ner_health(self) -> str: ...
    def service_health(self, service: str) -> str: ...


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SETTING = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
# Fase 2, L2 (grupo «ner»): lo único que se busca y se borra de la efímera son las cachés de una organización y la copia
# caliente del vault de una conversación; del servicio `ner`, solo se para y se arranca
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EPHEMERAL_PATTERN = re.compile(rf"^baas:(ner:{_UUID_RE}:\*|conv:{_UUID_RE}:vault)$")
_EPHEMERAL_KEY = re.compile(rf"^baas:(ner:{_UUID_RE}:[A-Za-z0-9@._:-]{{1,200}}|conv:{_UUID_RE}:vault)$")
EPHEMERAL_DEL_BATCH = 100
NER_SERVICE = "ner"
NER_ACTIONS = ("stop", "start")
_E2E_KEY = re.compile(r"^baas:e2e:[a-z0-9-]{1,60}$")
_REDIS_CLI = 'REDISCLI_AUTH="$BAAS_REDIS_PASSWORD" exec redis-cli "$@"'
# Busca cada patrón (argumentos 2…) en todos los ficheros del directorio (argumento 1) y escribe el número de los que
# aparecen. `-E`: el patrón es el que construye `needle_pattern` (el centinela, literal o con escapes de JSON); `-a`:
# como texto aunque el fichero sea binario (el preámbulo RDB del AOF); `-e`: aunque empiece por «-»; `LC_ALL=C`: bytes,
# no caracteres. Los centinelas llegan como argumentos: ni el script ni el intérprete del host los ven.
_SCAN_SCRIPT = """LC_ALL=C; export LC_ALL
dir=$1; shift; i=0
for pattern in "$@"; do
  i=$((i+1))
  grep -rEqas -e "$pattern" -- "$dir"; rc=$?
  if [ "$rc" -eq 0 ]; then echo "$i"; elif [ "$rc" -ne 1 ]; then echo "error:$i:$rc"; fi
done
"""
_ERE_SPECIAL = frozenset("\\.[](){}*+?|^$")
_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "/": "\\/", "\b": "\\b", "\f": "\\f", "\r": "\\r", "\t": "\\t"}
MAX_NEEDLE = 200
# Fase 4 (grupo «uso»): lo único que el inspector lee, escribe, para o arranca
CONTROL_DB = "baas_control"
USAGE_STREAMS = {PERSISTENT: ("baas:usage",), EPHEMERAL: ("baas:traces",)}
USAGE_KEYS = ("baas:usage",)  # EXISTS en cualquiera de las dos instancias
CONTROLLED_SERVICES = ("webapp-api", "mcp-server", "llm-interceptor-proxy")  # el proxy: 15.4 (grupo «ner»)
SERVICE_ACTIONS = ("stop", "start", "pause", "unpause")
# `podman start webapp-api` vuelve a ejecutar la migración y la semilla (dependencias de compose ya terminadas)
SERVICE_TIMEOUT = 180.0
_TAG = re.compile(r"^[a-z0-9-]{1,26}$")
_EVENT_ID = re.compile(r"^[0-9A-Z]{1,26}$")
_ENTRY = re.compile(r"^[0-9]+-[0-9]+$")
_SINCE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$")
# H4-13 (grupo «limite»): las peticiones salen de este contenedor (otra IP que la del equipo) hacia Caddy
LIMIT_SOURCE = "fake-upstream"
CADDY_URL = "http://caddy:80"
MAX_CADDY_REQUESTS = 100
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_PATH = re.compile(r"^/[A-Za-z0-9._~/%-]{0,500}$")
_HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Envía, una tras otra, las peticiones que recibe en JSON por la entrada estándar (las claves no van en la línea de
# órdenes), sin el proxy del entorno y con su `Host` (Caddy enruta por él), y escribe en JSON lo que tardó todo y el
# estado, las cabeceras y el cuerpo de cada respuesta (estado 0 y el error si no conecta).
_CADDY_SCRIPT = """import json, sys, time, urllib.error, urllib.request
spec = json.load(sys.stdin)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
replies, start = [], time.monotonic()
for r in spec["requests"]:
    req = urllib.request.Request(spec["base"] + r["path"], data=r["body"].encode(), method="POST",
                                 headers={**r["headers"], "Host": r["host"]})
    try:
        with opener.open(req, timeout=spec["timeout"]) as res:
            status, headers, body = res.status, res.headers.items(), res.read(65536)
    except urllib.error.HTTPError as exc:
        status, headers, body = exc.code, exc.headers.items(), exc.read(65536)
    except OSError as exc:
        status, headers, body = 0, [], repr(exc).encode()
    replies.append({"status": status, "headers": headers, "body": body.decode("utf-8", "replace")})
print(json.dumps({"elapsed": time.monotonic() - start, "replies": replies}))
"""


def _ere_literal(text: str) -> str:
    return "".join(f"\\{c}" if c in _ERE_SPECIAL else c for c in text)


def _hex_unit(unit: int) -> str:
    digits = "".join(f"[{d}{d.upper()}]" if d.isalpha() else d for d in f"{unit:04x}")
    return "\\\\u" + digits


def needle_pattern(needle: str) -> str:
    """Expresión regular (ERE) que reconoce el centinela tal cual y con cualquier mezcla de los escapes de JSON
    habituales, carácter a carácter: `\\"`, `\\\\`, `\\/`, `\\t`… y `\\uXXXX` (de Python, Go o PHP) para todo lo que no
    sea alfanumérico ASCII (un autor de JSON puede escapar solo la `@` o solo la `ñ`)."""
    parts = []
    for char in needle:
        forms = [_ere_literal(char)]
        if char in _SHORT_ESCAPES:
            forms.append(_ere_literal(_SHORT_ESCAPES[char]))
        if not (char.isascii() and char.isalnum()):
            code = ord(char)
            units = (
                [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
            )
            forms.append("".join(_hex_unit(u) for u in units))
        parts.append(forms[0] if len(forms) == 1 else f"({'|'.join(forms)})")
    return "".join(parts)


class ContainerInspector:
    """Mira dentro de los contenedores de la pila de `compose.dev.yml` con `podman exec` o `docker exec`."""

    def __init__(self, engine: str, project: str, mcp_data_dir: str = "/data") -> None:
        self.engine, self.project, self.mcp_data_dir = engine, project, mcp_data_dir.rstrip("/")
        self.poll_interval = 0.2  # entre dos consultas mientras Redis reescribe el AOF
        self.rewrite_timeout = 30.0  # lo más que se espera a una reescritura
        self.caddy_url = CADDY_URL  # grupo «limite»: Caddy visto desde el contenedor `fake-upstream`
        self.request_timeout = 20.0  # cada petición de ese grupo

    def container(self, service: str) -> str | None:
        out = self._run(["ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}",
                         "--filter", f"label=com.docker.compose.service={service}"], check_rc=False)  # fmt: skip
        ids = out.split()
        return ids[0] if ids else None

    def _run(
        self, argv: Sequence[str], *, check_rc: bool = True, merge: bool = False, timeout: float = 60,
        stdin: str | None = None,
    ) -> str:  # fmt: skip
        try:
            done = subprocess.run([self.engine, *argv], capture_output=True, text=True, timeout=timeout, check=False,
                                  errors="replace", input=stdin)  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StepError(f"{self.engine} {' '.join(argv[:3])}: {type(exc).__name__}") from None
        if check_rc and done.returncode != 0:
            raise StepError(
                f"{self.engine} {' '.join(argv[:3])} salió con {done.returncode}: {done.stderr.strip()[:200]}"
            )
        return done.stdout + done.stderr if merge else done.stdout

    def _exec(
        self, service: str, *argv: str, check_rc: bool = True, stdin: str | None = None, timeout: float = 60
    ) -> str:
        """`exec` en el contenedor del servicio; con `stdin`, `exec -i` y eso por la entrada estándar."""
        cid = self.container(service)
        if cid is None:
            raise StepError(f"no hay contenedor «{service}» en el proyecto «{self.project}» ({self.engine})")
        flags = ["-i"] if stdin is not None else []
        return self._run(["exec", *flags, cid, *argv], check_rc=check_rc, stdin=stdin, timeout=timeout)

    def _redis_cli(self, service: str, *args: str, check_rc: bool = True) -> str:
        """`redis-cli` dentro del contenedor: se autentica con SU `$BAAS_REDIS_PASSWORD`; `args` son argumentos."""
        return self._exec(service, "sh", "-c", _REDIS_CLI, "sh", *args, check_rc=check_rc)

    def redis_admin(self, service: str, *args: str) -> str:
        """Como `default` (administrador): `ACL GETUSER`, `CLIENT LIST`… del grupo «acl»."""
        return self._redis_cli(self._service(service), *args)

    def redis_as(self, service: str, user: str, command: str) -> str:
        """Ejecuta `command` en Redis como `user` con SU contraseña (la del entorno del contenedor) y devuelve su
        respuesta. La conexión se llama `e2e-acl` antes de la orden: así el rechazo queda en ACL LOG con ese nombre."""
        e2e_acl.safe_command(user, command)
        auth = f'REDISCLI_AUTH="$BAAS_REDIS_{user.upper()}_PASSWORD"'
        script = (f"printf '%s\\n%s\\n' 'CLIENT SETNAME {e2e_acl.CLIENT_NAME}' \"$1\""
                  f" | {auth} redis-cli --raw --user {user} 2>&1")  # fmt: skip
        out = self._exec(self._service(service), "sh", "-c", script, "sh", command, check_rc=False)
        first, _, rest = out.strip().partition("\n")
        if first.strip() != "OK":
            # sin el OK de SETNAME el intento no se hizo (contraseña, conexión o SETNAME denegado): no es un rechazo
            # de la orden, y sin nombre de cliente `e2e-ci.sh` vería el rechazo como ajeno
            raise StepError(
                f"{user} en {service}: CLIENT SETNAME no respondió OK, el intento no se hizo: {first.strip()[:150]!r}"
            )
        return rest.strip()

    @staticmethod
    def _slug(slug: str) -> str:
        if not _SLUG.fullmatch(slug):
            raise StepError(f"slug no válido: {slug!r}")
        return slug

    @staticmethod
    def _service(service: str) -> str:
        if service not in REDIS_SERVICES:
            raise StepError(f"servicio de Redis no válido: {service!r} (se esperaba {' o '.join(REDIS_SERVICES)})")
        return service

    @staticmethod
    def _lines(out: str) -> list[str]:
        return [line.strip() for line in out.splitlines() if line.strip()]

    def vault_rows(self, slug: str) -> int:
        s = self._slug(slug)
        tables = ("vault_entries", "vault_counters", "conversation_state")
        sql = "SELECT " + " + ".join(f"(SELECT count(*) FROM {t} WHERE org_slug = '{s}')" for t in tables)  # noqa: S608
        out = self._exec("postgres", "psql", "-U", "postgres", "-d", "baas_vault", "-Atc", sql).strip()
        if not out.isdigit():
            raise StepError(f"psql no devolvió un número: {out[:100]!r}")
        return int(out)

    def conversation_ids(self, slug: str) -> list[str]:
        """Las conversaciones que el vault conoce de la organización (identificadores del proxy)."""
        s = self._slug(slug)
        sql = (
            f"SELECT conversation_id FROM conversation_state WHERE org_slug = '{s}'"  # noqa: S608
            f" UNION SELECT conversation_id FROM vault_entries WHERE org_slug = '{s}'"
        )
        ids = self._lines(self._exec("postgres", "psql", "-U", "postgres", "-d", "baas_vault", "-Atc", sql))
        bad = [i for i in ids if not _UUID.fullmatch(i)]
        if bad:
            raise StepError(f"psql no devolvió un identificador de conversación: {bad[0][:100]!r}")
        return sorted(ids)

    def redis_services(self) -> list[str]:
        """Las instancias de Redis de la pila: la persistente y, si existe, la efímera (una antigua no la tiene)."""
        if self.container(PERSISTENT) is None:
            raise StepError(f"no hay contenedor «{PERSISTENT}» en el proyecto «{self.project}» ({self.engine})")
        return [PERSISTENT, *([EPHEMERAL] if self.container(EPHEMERAL) is not None else [])]

    def redis_keys(self, slug: str, conversation_ids: Sequence[str] = ()) -> list[str]:
        """Claves de la organización en cada instancia, como `[servicio] clave`: su política y sus membresías y, en la
        efímera, las de sus conversaciones (`baas:conv:<id>:*`: la copia caliente del vault y la copia de la lápida)."""
        s = self._slug(slug)
        ids = list(conversation_ids)
        for conv in ids:
            if not _UUID.fullmatch(conv):
                raise StepError(f"identificador de conversación no válido: {conv!r}")
        keys: list[str] = []
        for service in self.redis_services():
            patterns = [f"baas:policy:org:{s}", f"baas:member:*:{s}"]
            if service == EPHEMERAL:
                patterns += [f"baas:conv:{conv}:*" for conv in ids]
            for pattern in patterns:
                keys += [
                    f"[{service}] {key}"
                    for key in self._lines(self._redis_cli(service, "--scan", "--pattern", pattern))
                ]
        return keys

    def redis_setting(self, service: str, name: str) -> str:
        """`CONFIG GET <name>` de la instancia (`save ""` da una cadena vacía)."""
        svc = self._service(service)
        if not _SETTING.fullmatch(name):
            raise StepError(f"parámetro de Redis no válido: {name!r}")
        lines = self._redis_cli(svc, "--raw", "CONFIG", "GET", name).replace("\r", "").split("\n")
        if lines[0] != name:
            raise StepError(f"CONFIG GET {name} en «{svc}» no devolvió el parámetro: {lines[0][:100]!r}")
        return lines[1] if len(lines) > 1 else ""

    def redis_residue(self, service: str) -> dict[str, int]:
        """Lo que una pila anterior a A′ (H4-02) dejó en la instancia y lleva valores originales: cuántas claves
        `baas:conv:*:vault` y `baas:conv:*:events` hay (con SCAN, que no bloquea Redis) y cuántas entradas tiene el
        stream `baas:traces`. Solo devuelve lo que no es cero."""
        svc = self._service(service)
        residue: dict[str, int] = {}
        for pattern in ("baas:conv:*:vault", "baas:conv:*:events"):
            found = len(self._lines(self._redis_cli(svc, "--scan", "--pattern", pattern)))
            if found:
                residue[pattern] = found
        length = self._redis_cli(svc, "XLEN", "baas:traces").strip()
        if not length.isdigit():  # redis-cli escribe los errores en la salida estándar y sale con 0
            raise StepError(f"XLEN baas:traces en «{svc}»: {length[:100]!r}")
        if int(length):
            residue["baas:traces"] = int(length)
        return residue

    def disk_files(self, service: str) -> list[str]:
        """Los ficheros (todo lo que no es un directorio) de `/data` del contenedor."""
        svc = self._service(service)
        out = self._exec(svc, "find", REDIS_DATA_DIR, "-mindepth", "1", "!", "-type", "d")
        return sorted(self._lines(out))

    def force_aof_rewrite(self, service: str) -> None:
        """`BGREWRITEAOF` y espera a que Redis termine (`aof_rewrite_in_progress:0`). Solo para la persistente: en una
        con `appendonly no` crearía ficheros. Si ya había una en marcha, espera a que acabe y pide la suya."""
        svc = self._service(service)
        deadline = time.monotonic() + self.rewrite_timeout

        def late() -> StepError:
            return StepError(f"la reescritura del AOF de «{svc}» no terminó en {self.rewrite_timeout:g} s")

        while True:
            reply = self._redis_cli(svc, "BGREWRITEAOF", check_rc=False).strip()
            if "started" in reply or "scheduled" in reply:
                break
            if "already in progress" not in reply:
                raise StepError(f"BGREWRITEAOF en «{svc}»: {reply[:200] or 'sin respuesta'}")
            if time.monotonic() > deadline:
                raise late()
            time.sleep(self.poll_interval)
        while True:
            info = dict(
                line.split(":", 1) for line in self._redis_cli(svc, "INFO", "persistence").splitlines() if ":" in line
            )
            busy = (
                info.get("aof_rewrite_in_progress", "").strip() != "0"
                or info.get("aof_rewrite_scheduled", "0").strip() != "0"
            )
            if not busy:
                status = info.get("aof_last_bgrewrite_status", "").strip()
                if status != "ok":
                    raise StepError(f"la reescritura del AOF de «{svc}» falló: aof_last_bgrewrite_status = {status!r}")
                return
            if time.monotonic() > deadline:
                raise late()
            time.sleep(self.poll_interval)

    def disk_contains(self, service: str, needles: Sequence[str]) -> list[str]:
        """Cuáles de los `needles` aparecen en algún fichero de `/data` del contenedor (tal cual o con escapes de JSON).
        Se busca con `grep` dentro del contenedor: no se copia nada al host."""
        svc = self._service(service)
        for needle in needles:
            if not needle or "\n" in needle or "\x00" in needle or len(needle) > MAX_NEEDLE:
                raise StepError(
                    f"centinela no válido (vacío, con salto de línea o NUL, o de más de {MAX_NEEDLE}): {needle!r}"
                )
        if not needles:
            return []
        patterns = [needle_pattern(needle) for needle in needles]
        out = self._lines(self._exec(svc, "sh", "-c", _SCAN_SCRIPT, "sh", REDIS_DATA_DIR, *patterns))
        errors = [line for line in out if line.startswith("error:")]
        if errors:
            raise StepError(f"no se pudo leer {REDIS_DATA_DIR} de «{svc}» con grep ({errors[0]})")
        found = {int(line) for line in out if line.isdigit()}
        return [needle for index, needle in enumerate(needles, 1) if index in found]

    def redis_set_temp(self, service: str, key: str, value: str, ttl: int) -> None:
        """`SET <key> <value> EX <ttl>`: una clave de la prueba (`baas:e2e:…`) que caduca sola si no se borra."""
        svc = self._service(service)
        if not _E2E_KEY.fullmatch(key):
            raise StepError(f"clave no permitida: {key!r} (la prueba solo escribe «baas:e2e:<nombre>»)")
        if not value or "\x00" in value or not 1 <= ttl <= 3600:
            raise StepError("valor o caducidad no válidos para la clave temporal")
        reply = self._redis_cli(svc, "SET", key, value, "EX", str(ttl)).strip()
        if reply != "OK":
            raise StepError(f"SET {key} en «{svc}»: {reply[:100]!r}")

    def redis_delete(self, service: str, key: str) -> None:
        svc = self._service(service)
        if not _E2E_KEY.fullmatch(key):
            raise StepError(f"clave no permitida: {key!r} (la prueba solo borra «baas:e2e:<nombre>»)")
        self._redis_cli(svc, "DEL", key)

    def mcp_org_dirs(self) -> list[str]:
        out = self._exec("mcp-server", "ls", "-1", f"{self.mcp_data_dir}/orgs", check_rc=False)
        return self._lines(out)

    def mcp_quarantine(self) -> list[str]:
        out = self._exec("mcp-server", "ls", "-1", f"{self.mcp_data_dir}/.quarantine", check_rc=False)
        return self._lines(out)

    # --- Fase 4: métricas de uso (grupo «uso», `dev/e2e_uso.py`) ---------------------------------------------
    @staticmethod
    def _uuid(value: str) -> str:
        if not _UUID.fullmatch(value):
            raise StepError(f"identificador no válido: {value!r}")
        return value

    def _control(self, sql: str) -> list[str]:
        """`psql` contra la base de la webapp (`baas_control`); cada fila, una línea con `|` entre columnas."""
        return self._lines(self._exec("postgres", "psql", "-U", "postgres", "-d", CONTROL_DB, "-Atc", sql))

    def _counts(self, sql: str, names: Sequence[str]) -> dict[str, int]:
        row = (self._control(sql) or [""])[0].split("|")
        if len(row) != len(names) or not all(v.isdigit() for v in row):
            raise StepError(f"psql no devolvió {len(names)} número(s): {'|'.join(row)[:100]!r}")
        return dict(zip(names, map(int, row), strict=True))

    def usage_rows(self, org_id: str) -> dict[str, int]:
        """Filas de métricas de la organización: tráfico, contenido y fotos de disco."""
        o = self._uuid(org_id)
        tables = ("usage_traffic_hourly", "usage_content_hourly", "storage_snapshots")
        sql = "SELECT " + ", ".join(f"(SELECT count(*) FROM {t} WHERE org_id = '{o}')" for t in tables)  # noqa: S608
        return self._counts(sql, ("traffic", "content", "snapshots"))

    def tagged_rows(self, tag: str) -> dict[str, int]:
        """Las filas que `insert_old_usage` escribió con `tag` (modelo, skill e identificador de la foto)."""
        t = self._tag(tag)
        sql = (
            f"SELECT (SELECT count(*) FROM usage_traffic_hourly WHERE model = '{t}'),"  # noqa: S608
            f" (SELECT count(*) FROM usage_content_hourly WHERE skill = '{t}'),"
            f" (SELECT count(*) FROM storage_snapshots WHERE event_id = '{t.upper()}')"
        )
        return self._counts(sql, ("traffic", "content", "snapshots"))

    @staticmethod
    def _tag(tag: str) -> str:
        if not _TAG.fullmatch(tag):
            raise StepError(f"etiqueta no válida: {tag!r}")
        return tag

    def usage_dump(self) -> str:
        """Todas las filas de las tablas de métricas como texto (para buscar valores centinela)."""
        tables = ("usage_traffic_hourly", "usage_content_hourly", "storage_snapshots", "storage_snapshot_items",
                  "usage_source_status")  # fmt: skip
        sql = " UNION ALL ".join(f"SELECT '{t}', x::text FROM {t} x" for t in tables)  # noqa: S608
        return "\n".join(self._control(sql))

    def source_status(self) -> dict[str, str]:
        """«Datos hasta» de cada tubería (`usage_source_status`): `source` → `last_event_at`."""
        rows = self._control("SELECT source, last_event_at FROM usage_source_status")
        return dict(line.split("|", 1) for line in rows if "|" in line)

    def insert_old_usage(self, org_id: str, user_id: str, *, months: int, tag: str) -> None:
        """Una fila de tráfico, una de contenido y una foto de disco de hace `months` meses, marcadas con `tag`."""
        o, u, t = self._uuid(org_id), self._uuid(user_id), self._tag(tag)
        if not 1 <= months <= 120:
            raise StepError(f"meses no válidos: {months!r}")
        at = f"now() - make_interval(months => {int(months)})"
        sql = (
            "INSERT INTO usage_traffic_hourly (org_id, user_id, hour, model, provider, requests) VALUES "  # noqa: S608
            f"('{o}', '{u}', date_trunc('hour', {at}), '{t}', 'ANTHROPIC', 1); "
            "INSERT INTO usage_content_hourly (org_id, user_id, hour, source, skill, area, count) VALUES "
            f"('{o}', '{u}', date_trunc('hour', {at}), 'SYNCED', '{t}', '', 1); "
            "INSERT INTO storage_snapshots (id, org_id, taken_at, event_id, files, bytes_skills, bytes_scripts, "
            "bytes_schemas, bytes_knowledge, bytes_versions, bytes_index) VALUES "
            f"(gen_random_uuid(), '{o}', {at}, '{t.upper()}', 1, 1, 0, 0, 0, 0, 0)"
        )
        self._control(sql)

    def snapshot_count(self, event_id: str) -> int:
        if not _EVENT_ID.fullmatch(event_id):
            raise StepError(f"event_id no válido: {event_id!r}")
        sql = f"SELECT count(*) FROM storage_snapshots WHERE event_id = '{event_id}'"  # noqa: S608
        return self._counts(sql, ("n",))["n"]

    @staticmethod
    def _stream(service: str, stream: str) -> tuple[str, str]:
        if stream not in USAGE_STREAMS.get(service, ()):
            raise StepError(f"stream no permitido: {stream!r} en {service!r}")
        return service, stream

    def key_exists(self, service: str, key: str) -> bool:
        svc = self._service(service)
        if key not in USAGE_KEYS:
            raise StepError(f"clave no permitida: {key!r}")
        reply = self._redis_cli(svc, "EXISTS", key).strip()
        if reply not in ("0", "1"):
            raise StepError(f"EXISTS {key} en «{svc}»: {reply[:100]!r}")
        return reply == "1"

    def stream_group(self, service: str, stream: str, group: str) -> dict[str, str] | None:
        svc, key = self._stream(service, stream)
        return e2e_uso.group_info(self._redis_cli(svc, "--raw", "XINFO", "GROUPS", key, check_rc=False), group)

    def stream_entries(self, service: str, stream: str) -> list[tuple[str, str]]:
        svc, key = self._stream(service, stream)
        return e2e_uso.stream_entries(self._redis_cli(svc, "--raw", "XRANGE", key, "-", "+"))

    def stream_add(self, service: str, stream: str, data: str) -> str:
        """`XADD <stream> * data <data>` (como el MCP y el proxy): devuelve el id de la entrada."""
        svc, key = self._stream(service, stream)
        reply = self._redis_cli(svc, "XADD", key, "*", "data", data).strip()
        if not _ENTRY.fullmatch(reply):
            raise StepError(f"XADD {key} en «{svc}»: {reply[:100]!r}")
        return reply

    def stream_has(self, service: str, stream: str, entry_id: str) -> bool:
        svc, key = self._stream(service, stream)
        if not _ENTRY.fullmatch(entry_id):
            raise StepError(f"id de entrada no válido: {entry_id!r}")
        return bool(e2e_uso.stream_entries(self._redis_cli(svc, "--raw", "XRANGE", key, entry_id, entry_id)))

    @staticmethod
    def _controlled(service: str) -> str:
        if service not in CONTROLLED_SERVICES:
            raise StepError(f"servicio no permitido: {service!r} (solo {', '.join(CONTROLLED_SERVICES)})")
        return service

    def service_control(self, service: str, action: str) -> None:
        """`stop`, `start`, `pause` o `unpause` de la webapp o del MCP (con podman, `start` arranca también las
        dependencias que ya terminaron: la migración y la semilla de la webapp, que son idempotentes)."""
        svc = self._controlled(service)
        if action not in SERVICE_ACTIONS:
            raise StepError(f"acción no permitida: {action!r}")
        cid = self.container(svc)
        if cid is None:
            raise StepError(f"no hay contenedor «{svc}» en el proyecto «{self.project}» ({self.engine})")
        self._run([action, cid], timeout=SERVICE_TIMEOUT)

    def service_logs(self, service: str, since: str) -> str:
        """Los logs (salida y errores) del contenedor desde `since` (RFC 3339 UTC)."""
        svc = self._controlled(service)
        if not _SINCE.fullmatch(since):
            raise StepError(f"instante no válido: {since!r}")
        cid = self.container(svc)
        if cid is None:
            raise StepError(f"no hay contenedor «{svc}» en el proyecto «{self.project}» ({self.engine})")
        return self._run(["logs", "--since", since, cid], merge=True)

    def mcp_files(self, slug: str) -> list[tuple[str, int]]:
        """Los ficheros regulares de `orgs/<slug>/` del MCP (sin seguir enlaces): ruta relativa y `st_size`."""
        s = self._slug(slug)
        out = self._exec("mcp-server", "find", f"{self.mcp_data_dir}/orgs/{s}", "-mindepth", "1",
                         "-printf", "%y\t%s\t%P\n")  # fmt: skip
        files = []
        for line in out.splitlines():
            kind, _, rest = line.partition("\t")
            size, _, path = rest.partition("\t")
            if kind == "f" and size.isdigit() and path:
                files.append((path, int(size)))
        return files

    # --- H4-13: peticiones desde otra IP (grupo «limite») -----------------------------------------------------
    def caddy_requests(self, requests: Sequence[CaddyRequest]) -> tuple[float, list[Http]]:
        """Envía `requests`, una tras otra, desde el contenedor `fake-upstream` a Caddy: un cliente con otra IP que la
        del equipo. Devuelve lo que tardaron (medido dentro del contenedor) y cada respuesta."""
        if not 1 <= len(requests) <= MAX_CADDY_REQUESTS:
            raise StepError(f"se envían de 1 a {MAX_CADDY_REQUESTS} peticiones, no {len(requests)}")
        spec = {"base": self.caddy_url, "timeout": self.request_timeout,
                "requests": [self._caddy_request(r) for r in requests]}  # fmt: skip
        out = self._exec(LIMIT_SOURCE, "python3", "-c", _CADDY_SCRIPT, stdin=json.dumps(spec),
                         timeout=30 + self.request_timeout * len(requests))  # fmt: skip
        try:
            data = json.loads(out)
            replies = [Http(int(r["status"]), [(str(k), str(v)) for k, v in r["headers"]], str(r["body"]).encode())
                       for r in data["replies"]]  # fmt: skip
            elapsed = float(data["elapsed"])
        except (ValueError, KeyError, TypeError):
            raise StepError(f"«{LIMIT_SOURCE}» no devolvió las respuestas: {out[:200]!r}") from None
        if len(replies) != len(requests):
            raise StepError(f"«{LIMIT_SOURCE}» devolvió {len(replies)} respuesta(s) de {len(requests)}")
        return elapsed, replies

    @staticmethod
    def _caddy_request(r: CaddyRequest) -> dict[str, Any]:
        if not _HOST.fullmatch(r.host) or not _PATH.fullmatch(r.path):
            raise StepError(f"petición no válida para Caddy: host {r.host!r}, ruta {r.path!r}")
        for name, value in r.headers.items():
            if not _HEADER.fullmatch(name) or name.lower() == "host" or any(c in value for c in "\r\n\x00"):
                raise StepError(f"cabecera no válida: {name!r}")
        return {"host": r.host, "path": r.path, "headers": dict(r.headers), "body": r.body}

    # --- Fase 2, L2: caché del NER y servicio `ner` (grupo «ner», `dev/e2e_ner.py`) -----------------------------
    def ephemeral_scan(self, pattern: str) -> list[str]:
        """Las claves de la efímera que casan con `pattern`: solo la caché del NER de una organización
        (`baas:ner:<org_id>:*`) o la copia caliente del vault de una conversación (`baas:conv:<id>:vault`)."""
        if not _EPHEMERAL_PATTERN.fullmatch(pattern):
            raise StepError(f"patrón no permitido en la efímera: {pattern!r}")
        return self._lines(self._redis_cli(self._service(EPHEMERAL), "--scan", "--pattern", pattern))

    def ephemeral_delete(self, keys: Sequence[str]) -> int:
        """Borra esas claves de la efímera (solo cachés del NER y copias calientes del vault; si alguna no vale, no
        se borra ninguna) y devuelve cuántas existían. Es lo que el e2e usa en vez de `FLUSHALL`."""
        keys = list(keys)
        for key in keys:
            if not _EPHEMERAL_KEY.fullmatch(key):
                raise StepError(f"clave no permitida en la efímera: {key!r}")
        svc, deleted = self._service(EPHEMERAL), 0
        for i in range(0, len(keys), EPHEMERAL_DEL_BATCH):
            reply = self._redis_cli(svc, "DEL", *keys[i : i + EPHEMERAL_DEL_BATCH]).strip()
            if not reply.isdigit():
                raise StepError(f"DEL en «{svc}»: {reply[:100]!r}")
            deleted += int(reply)
        return deleted

    def _ner_container(self) -> str:
        cid = self.container(NER_SERVICE)
        if cid is None:
            raise StepError(f"no hay contenedor «{NER_SERVICE}» en el proyecto «{self.project}» ({self.engine})")
        return cid

    def ner_control(self, action: str) -> None:
        """Para o arranca el servicio `ner` (la pila se queda sin NER; `start` lo devuelve)."""
        if action not in NER_ACTIONS:
            raise StepError(f"acción no permitida: {action!r} (solo {', '.join(NER_ACTIONS)})")
        self._run([action, self._ner_container()], timeout=SERVICE_TIMEOUT)

    def _health(self, cid: str, name: str) -> str:
        """`healthy`, `starting`, `unhealthy`… del contenedor; sin healthcheck, lo dice (no hay nada que esperar)."""
        try:
            status = self._run(["inspect", "-f", "{{.State.Health.Status}}", cid]).strip()
        except StepError as exc:
            raise StepError(f"no se pudo leer la salud de «{name}» (¿sin healthcheck?): {exc}") from None
        if status in ("", "<no value>", "none"):
            raise StepError(f"el contenedor «{name}» no define healthcheck: no se puede esperar a que esté sano")
        return status

    def ner_health(self) -> str:
        """El estado de salud del contenedor `ner`."""
        return self._health(self._ner_container(), NER_SERVICE)

    def service_health(self, service: str) -> str:
        """El estado de salud de la webapp, el MCP o el proxy."""
        svc = self._controlled(service)
        cid = self.container(svc)
        if cid is None:
            raise StepError(f"no hay contenedor «{svc}» en el proyecto «{self.project}» ({self.engine})")
        return self._health(cid, svc)


class MissingInspector:
    """No se encontró el motor de contenedores: cada consulta falla diciendo cómo seguir."""

    def __init__(self, why: str) -> None:
        self.why = why

    def _fail(self, *_: Any, **__: Any) -> Any:
        raise StepError(f"{self.why}. Usa --inspect podman|docker (y --compose-project) o --inspect none para omitirlo")

    vault_rows = conversation_ids = redis_services = redis_keys = redis_setting = redis_residue = disk_files = _fail
    force_aof_rewrite = disk_contains = redis_set_temp = redis_delete = mcp_org_dirs = mcp_quarantine = _fail
    usage_rows = tagged_rows = usage_dump = source_status = insert_old_usage = snapshot_count = key_exists = _fail
    stream_group = stream_entries = stream_add = stream_has = service_control = service_logs = mcp_files = _fail
    redis_as = redis_admin = _fail
    caddy_requests = _fail
    ephemeral_scan = ephemeral_delete = ner_control = ner_health = service_health = _fail


def build_inspector(args: argparse.Namespace) -> Inspector | None:
    if args.inspect == "none":
        return None
    engines = ("podman", "docker") if args.inspect == "auto" else (args.inspect,)
    tried = []
    for engine in engines:
        if shutil.which(engine) is None:
            tried.append(f"{engine} no está instalado")
            continue
        inspector = ContainerInspector(engine, args.compose_project, args.mcp_data_dir)
        try:
            found = inspector.container("postgres")
        except StepError as exc:
            tried.append(str(exc))
            continue
        if found:
            return inspector
        tried.append(f"{engine} no ve el proyecto «{args.compose_project}»")
    return MissingInspector("; ".join(tried))


# --- argumentos ------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    http_port = os.environ.get("BAAS_DEV_HTTP_PORT") or "8080"
    fake_port = os.environ.get("BAAS_DEV_FAKE_UPSTREAM_PORT") or "18090"
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--app-url", default=os.environ.get("BAAS_E2E_APP_URL") or f"http://app.localhost:{http_port}",
                   help="URL pública de la webapp (las del proxy y del MCP salen del handshake)")  # fmt: skip
    p.add_argument("--fake-url", default=os.environ.get("BAAS_E2E_FAKE_URL") or f"http://127.0.0.1:{fake_port}",
                   help="URL del proveedor falso publicada en el host (para /_debug/requests)")  # fmt: skip
    p.add_argument("--proxy-url", default=os.environ.get("BAAS_E2E_PROXY_URL") or None,
                   help="Sustituye el origen de las URLs del proxy del handshake (conserva /org/<slug>/…)")  # fmt: skip
    p.add_argument("--org", default="observacion", help="Organización con AI-IP activo y anonimizador apagado")
    p.add_argument("--email", default="observador@observacion.test", help="Usuario (USER) de esa organización")
    p.add_argument("--admin-email", default="admin@baas.test", help="PLATFORM_ADMIN")
    p.add_argument("--orgadmin-email", default="orgadmin@demo.test",
                   help="ORG_ADMIN de otra organización: primer miembro de la que se elimina")  # fmt: skip
    p.add_argument("--bypass-org", default="demo", help="Organización con AI-IP apagado")
    p.add_argument("--bypass-email", default="analista@demo.test", help="Usuario de la organización sin AI-IP")
    p.add_argument("--anon-org", default="privada", help="Organización con AI-IP y anonimizador encendidos (grupo 10)")
    p.add_argument("--anon-email", default="analista@privada.test", help="Usuario (USER) de esa organización")
    p.add_argument("--password", default=DEV_PASSWORD)
    p.add_argument("--totp-secret", default=DEV_TOTP_SECRET)
    p.add_argument("--only", type=h3._csv(GROUPS), default=list(GROUPS),
                   help=f"Partes que se ejecutan, separadas por comas: {', '.join(GROUPS)} (todas)")  # fmt: skip
    p.add_argument("--apis", type=h3._csv(APIS), default=list(APIS),
                   help=f"APIs que se prueban, separadas por comas: {', '.join(APIS)} (todas)")  # fmt: skip
    p.add_argument("--inspect", choices=("auto", "podman", "docker", "none"), default="auto",
                   help="Motor para mirar los contenedores («none» omite 9.1b, 9.8, el grupo 10, 12.4-12.5, "
                        "12.7-12.9, 13.x, 14.x, 15.4 y 15.5)")  # fmt: skip
    p.add_argument("--compose-project", default=os.environ.get("COMPOSE_PROJECT_NAME") or "baas-dev",
                   help="Proyecto de compose de la pila (por defecto, $COMPOSE_PROJECT_NAME o baas-dev)")  # fmt: skip
    p.add_argument("--mcp-data-dir", default="/data", help="BAAS_DATA_DIR del contenedor del MCP")
    p.add_argument("--trace-timeout", type=float, default=30, help="Segundos de espera de cada traza o propagación")
    p.add_argument("--propagation-wait", type=float, default=1.5,
                   help="Segundos entre cambiar el anonimizador y el turno siguiente (Pub/Sub)")  # fmt: skip
    p.add_argument("--flush-wait", type=float, default=2.0,
                   help="Segundos de espera antes de mirar el AOF vivo (Redis lo escribe con retraso)")  # fmt: skip
    p.add_argument("--timeout", type=float, default=60, help="Tiempo máximo de cada petición HTTP")
    p.add_argument("--usage-timeout", type=float, default=90,
                   help="Grupos uso y consumo: espera (s) del uso del MCP, de la foto de disco (12.5 y 16.2) y de un "
                        "servicio reiniciado")  # fmt: skip
    p.add_argument("--overhead-n", type=int, default=200, help="Grupo uso (12.10): pares de peticiones medidos")
    p.add_argument("--overhead-p95-ms", type=float, default=10.0,
                   help="Grupo uso (12.10): umbral (ms) del p95 del sobrecoste")  # fmt: skip
    p.add_argument("--usage-window", type=float, default=5,
                   help="Grupo uso: BAAS_USAGE_WINDOW_SECONDS del MCP (5 en desarrollo)")  # fmt: skip
    p.add_argument("--auth-fail-burst", type=int, default=30,
                   help="Grupo limite: BAAS_AUTH_FAIL_BURST del proxy y del MCP de la pila (30)")  # fmt: skip
    p.add_argument("--auth-fail-per-minute", type=float, default=10,
                   help="Grupo limite: BAAS_AUTH_FAIL_PER_MINUTE de la pila (10); con él se calcula cuántas fichas "
                        "pudieron volver durante una ráfaga lenta y cuánto esperar a que se llene el cubo si una "
                        "ejecución anterior lo dejó a medias")  # fmt: skip
    p.add_argument("--ner-timeout", type=float, default=150,
                   help="Grupo ner (15.5): espera (s) a que ner vuelva y el disyuntor se cierre")  # fmt: skip
    p.add_argument("--ner", action="store_true",
                   default=os.environ.get("BAAS_E2E_NER", "").lower() in ("1", "true", "yes"),
                   help="Grupo consumo (16.8): declara que la pila trae el NER (también con BAAS_E2E_NER=1). Sin "
                        "esto, 16.8 se salta (OK con «SALTADO») si ningún turno trae verification.l2; con esto, "
                        "falla")  # fmt: skip
    p.add_argument("--json", type=Path, help="Escribe aquí el informe en JSON")
    ns = p.parse_args(argv)
    if ns.auth_fail_burst < 1 or not ns.auth_fail_per_minute > 0:
        p.error(
            "--auth-fail-burst debe ser ≥ 1 y --auth-fail-per-minute > 0 (con el límite apagado no hay grupo limite)"
        )
    ns.app_url, ns.fake_url = ns.app_url.rstrip("/"), ns.fake_url.rstrip("/")
    ns.proxy_url = ns.proxy_url.rstrip("/") if ns.proxy_url else None
    return ns


# --- la prueba ------------------------------------------------------------------------------------


@dataclass
class Conv:
    """Una conversación de la prueba del interruptor (una por API)."""

    case: h3.Case
    first_text: str
    answer: str = ""
    response_id: str | None = None
    follow: list[h3.Case] = field(default_factory=list)
    past: list[tuple[str, str]] = field(default_factory=list)  # (usuario, asistente) de cada turno ya respondido


@dataclass
class OriginalCase:
    """Un turno del grupo 10: lo que se envió y de quién es la conversación que debería aparecer en la webapp."""

    case: h3.Case
    email: str
    org: str


CONTROL_TTL = 300  # segundos que vive la clave del control positivo si la limpieza falla
SETTLE = 5.0  # lo más que se espera a que Redis suelte los ficheros viejos tras reescribir el AOF


def fmt_needles(needles: Iterable[str]) -> str:
    return ", ".join(f"«{needle}»" for needle in needles)


class E2EH4:
    def __init__(self, args: argparse.Namespace, inspector: Inspector | None = None, nonce: str | None = None) -> None:
        self.args = args
        self.nonce = nonce or h3.new_nonce()
        self.inspector = inspector
        emails = {args.email, args.admin_email, args.orgadmin_email, args.bypass_email, args.anon_email}
        self.keys = {email: dev_api_key(email) for email in emails}
        self.sessions: dict[str, str] = {}
        self.binding: dict[str, Any] = {}
        self.bypass_binding: dict[str, Any] = {}
        self.anon_binding: dict[str, Any] = {}
        self.original_cases: list[OriginalCase] = []
        self.observed: list[h3.Case] = []  # casos con el anonimizador apagado que respondieron 200
        self.conversations: dict[str, dict[str, Conv]] = {"A": {}, "B": {}, "C": {}}
        self.slug = new_slug(self.nonce)
        self.member_binding: dict[str, Any] = {}  # handshake de orgadmin_email en la organización que se elimina
        self.org_conversations: list[
            str
        ] = []  # las conversaciones de esa organización según el vault (antes de eliminarla)
        prefix = f"huerf-{self.nonce}"  # grupo «huérfanos» (11)
        self.orph_slugs = {key: f"{prefix}-{key}" for key in ORPHAN_ORGS}
        self.orph = {label: Person(label, f"{prefix}-{label}@{ORPHAN_DOMAIN}", f"Huerfano {label.upper()} {self.nonce}")
                     for label in ORPHAN_PEOPLE}  # fmt: skip
        self.orph_bindings: dict[tuple[str, str], dict[str, Any]] = {}  # (persona, organización) → handshake
        self.probes = 0

    # --- utilidades ------------------------------------------------------------------------------
    def case(self, group: str, api: str, stream: bool = False) -> h3.Case:
        mode = "stream" if stream else "json"
        return h3.Case(group, api, stream, h3.case_tag(self.nonce, group, api, mode),
                       case_model(self.nonce, group, api, mode))  # fmt: skip

    def key_of(self, email: str) -> str:
        return self.keys[email]

    def send(self, binding: dict[str, Any], email: str, api: str, payload: dict[str, Any]) -> tuple[bytes, Http]:
        headers = {"X-Baas-Api-Key": self.key_of(email)}
        if api == "messages":
            headers |= {"x-api-key": h3.FAKE_ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
        else:
            headers["Authorization"] = f"Bearer {h3.FAKE_OPENAI_KEY}"
        if payload.get("stream"):
            headers["Accept"] = "text/event-stream"
        raw = h3.encode(payload)
        return raw, h3.http("POST", h3.api_url(binding, api), headers=headers, data=raw, timeout=self.args.timeout)

    def webapp(self, method: str, path: str, who: str, payload: Any = None) -> Http:
        headers = {"Cookie": f"baas_session={self.sessions[who]}"}
        if method != "GET":
            headers["X-Requested-With"] = "baas-webapp"
        return h3.http(
            method, f"{self.args.app_url}{path}", headers=headers, payload=payload, timeout=self.args.timeout
        )

    def debug_entries(self) -> list[dict[str, Any]]:
        res = h3.http("GET", f"{self.args.fake_url}/_debug/requests", timeout=self.args.timeout)
        check(res.status == 200, f"GET {self.args.fake_url}/_debug/requests → HTTP {res.status}")
        data = res.json()
        check(
            isinstance(data, dict) and isinstance(data.get("requests"), list),
            f"forma inesperada: {h3.snippet(res.body)}",
        )
        return [e for e in data["requests"] if isinstance(e, dict)]

    def poll(self, fn: Callable[[], list[str]], timeout: float | None = None) -> list[str]:
        """Repite `fn` (devuelve problemas) hasta que no haya ninguno o se acabe el tiempo; devuelve los últimos."""
        deadline = time.monotonic() + (self.args.trace_timeout if timeout is None else timeout)
        while True:
            problems = fn()
            if not problems or time.monotonic() > deadline:
                return problems
            time.sleep(0.25)

    def list_conversations(self, who: str, org: str | None = None) -> list[dict[str, Any]]:
        res = self.webapp("GET", f"/api/v1/conversations?org={org or self.args.org}&q={self.nonce}&limit=200", who)
        check(res.status == 200, f"GET /api/v1/conversations ({who}) → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        return [i for i in (res.json() or {}).get("items") or [] if isinstance(i, dict)]

    def wait_conversation(self, who: str, model: str, org: str | None = None, turns: int = 1) -> dict[str, Any]:
        deadline = time.monotonic() + self.args.trace_timeout
        while True:
            for item in self.list_conversations(who, org):
                if item.get("model") == model and (item.get("turn_count") or 0) >= turns:
                    return item
            if time.monotonic() > deadline:
                raise StepError(
                    f"la conversación ({model}) no aparece en GET /api/v1/conversations"
                    f" tras {self.args.trace_timeout:g} s"
                )
            time.sleep(0.25)

    def turn(self, who: str, conversation_id: str, index: int) -> Http:
        return self.webapp("GET", f"/api/v1/conversations/{conversation_id}/turns/{index}", who)

    def turn_indexes(self, who: str, conversation_id: str) -> list[int]:
        res = self.webapp("GET", f"/api/v1/conversations/{conversation_id}", who)
        check(res.status == 200, f"GET /api/v1/conversations/{conversation_id} ({who}) → HTTP {res.status}")
        turns = [t for t in (res.json() or {}).get("turns") or [] if isinstance(t, dict)]
        check(turns, f"la conversación {conversation_id} no tiene turnos")
        return sorted(int(t.get("turn_index") or 0) for t in turns)

    def provider_entries(self, tag: str) -> list[dict[str, Any]]:
        return h3.tagged(self.debug_entries(), tag)

    def path_problems(self, entries: list[dict[str, Any]], api: str) -> list[str]:
        problems = []
        for entry in entries:
            path = str(entry.get("path"))
            if not path.split("?")[0].endswith(h3.UPSTREAM_PATHS[api]):
                problems.append(f"el proveedor la recibió en {path} (se esperaba {h3.UPSTREAM_PATHS[api]})")
        return problems

    def clear_problems(self, tag: str, api: str, raw: bytes | None = None) -> list[str]:
        """Lo que recibió el proveedor para `tag`: los tres valores en claro, ningún token (y, con `raw`, idéntico)."""
        entries = self.provider_entries(tag)
        if not entries:
            return [f"el proveedor no recibió la petición (ninguna en /_debug/requests con «{tag}»)"]
        problems = self.path_problems(entries, api)
        for entry in entries:
            text = h3.debug_text(entry)
            missing = [f"{kind} «{value}»" for kind, value in VALUES.items() if value not in text]
            if missing:
                problems.append(f"el proveedor no recibió en claro: {', '.join(missing)}")
            if "[[" in text:
                problems.append(
                    f"el proveedor recibió tokens ({sorted(set(h3.TOKEN_RE.findall(text)))})"
                    " con el anonimizador apagado"
                )
            if raw is not None:
                same, _ = h3.same_payload(raw, entry.get("body"))
                if not same:
                    received = entry.get("body")
                    with contextlib.suppress(ValueError):
                        received = json.loads(received) if isinstance(received, str) else received
                    problems.append(
                        "el cuerpo que recibió el proveedor no es idéntico al enviado: "
                        f"{h3.first_difference(json.loads(raw), received)}"
                    )
        return problems

    def tokenized_problems(self, tag: str, api: str) -> list[str]:
        """Lo que recibió el proveedor para `tag`: los tres tokens y ningún valor."""
        entries = self.provider_entries(tag)
        if not entries:
            return [f"el proveedor no recibió la petición (ninguna en /_debug/requests con «{tag}»)"]
        problems = self.path_problems(entries, api)
        for entry in entries:
            text = h3.debug_text(entry)
            leaked = [f"{kind} «{value}»" for kind, value in VALUES.items() if value in text]
            if leaked:
                problems.append(f"el proveedor recibió en claro con el anonimizador encendido: {', '.join(leaked)}")
            missing = [token for token in TOKENS if token not in text]
            if missing:
                problems.append(f"el proveedor no recibió {', '.join(missing)}")
        return problems

    def reply_texts(self, api: str, res: Http, stream: bool, text: str | None = None) -> list[str]:
        """Problemas de lo que recibe el cliente: los tres valores en cada copia, sin `[[` y, con `text`, lo enviado."""
        problems = []
        ctype = next((v for k, v in res.headers if k.lower() == "content-type"), "")
        if stream and "text/event-stream" not in ctype:
            problems.append(f"Content-Type {ctype!r} en una respuesta en streaming")
        body = res.body.decode(errors="replace")
        if "[[" in body:
            at = body.index("[[")
            problems.append(f"el cuerpo de la respuesta contiene «[[»: {body[max(0, at - 20) : at + 30]!r}")
        reply = h3.extract_reply(api, res.body, stream=stream)
        if not reply.texts:
            problems.append("la respuesta no trae texto")
        for where, copy in reply.texts:
            problems += [f"{where}: {p}" for p in h3.restoration_problems(copy)]
            if text is not None and text not in copy:
                problems.append(f"{where}: no devuelve lo que se envió («{text[:60]}…»)")
        return problems

    # --- 0 a 4 -----------------------------------------------------------------------------------
    def webapp_up(self) -> str:
        res = h3.http("GET", f"{self.args.app_url}/healthz", timeout=self.args.timeout)
        check(res.status == 200, f"GET {self.args.app_url}/healthz → HTTP {res.status}")
        return f"{self.args.app_url} responde"

    def fake_debug(self) -> str:
        before = self.debug_entries()
        res = h3.http("DELETE", f"{self.args.fake_url}/_debug/requests", timeout=self.args.timeout)
        check(200 <= res.status < 300, f"DELETE {self.args.fake_url}/_debug/requests → HTTP {res.status}")
        check(not self.debug_entries(), "tras vaciarlo, /_debug/requests aún tiene peticiones")
        return f"{self.args.fake_url}/_debug/requests responde; vaciado ({len(before)} petición(es) previas)"

    def handshake(self, email: str, org: str, *, ai_ip: bool) -> dict[str, Any]:
        res = h3.http("POST", f"{self.args.app_url}/api/v1/plugin/handshake",
                      headers={"Authorization": f"Bearer {self.key_of(email)}"}, payload={},
                      timeout=self.args.timeout)  # fmt: skip
        check(res.status == 200, f"handshake de {email} → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        orgs = {o.get("slug"): o for o in (res.json() or {}).get("organizations") or [] if isinstance(o, dict)}
        check(org in orgs, f"{email} no tiene la organización «{org}» (¿seed-dev de H4?): {sorted(map(str, orgs))}")
        binding = orgs[org]
        check(binding.get("ai_ip_enabled") is ai_ip,
              f"ai_ip_enabled = {binding.get('ai_ip_enabled')!r} en «{org}» (se esperaba {ai_ip})")  # fmt: skip
        check(binding.get("proxy_anthropic_url") and binding.get("proxy_openai_url"), f"sin URLs del proxy: {binding}")
        return h3.override_proxy(binding, self.args.proxy_url) if self.args.proxy_url else binding

    def handshake_observation(self) -> str:
        self.binding = self.handshake(self.args.email, self.args.org, ai_ip=True)
        return f"«{self.args.org}» con AI-IP activo · {self.binding['proxy_anthropic_url']}"

    def handshake_bypass(self) -> str:
        self.bypass_binding = self.handshake(self.args.bypass_email, self.args.bypass_org, ai_ip=False)
        return f"«{self.args.bypass_org}» con AI-IP apagado · {self.bypass_binding['proxy_anthropic_url']}"

    def handshake_anon(self) -> str:
        self.anon_binding = self.handshake(self.args.anon_email, self.args.anon_org, ai_ip=True)
        return f"«{self.args.anon_org}» con AI-IP activo · {self.anon_binding['proxy_anthropic_url']}"

    def login(self, *emails: str) -> str:
        a = self.args
        for email in dict.fromkeys(emails):
            self.sessions[email] = h3.webapp_login(a.app_url, email, a.password, a.totp_secret, a.timeout)
        return "sesión de " + ", ".join(dict.fromkeys(emails))

    def org_state(self) -> dict[str, Any]:
        res = self.webapp("GET", f"/api/v1/orgs/{self.args.org}", self.args.admin_email)
        check(res.status == 200, f"GET /api/v1/orgs/{self.args.org} → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        data = org_data(res.json())
        check(
            "anonymizer_enabled" in data, f"GET /api/v1/orgs/{self.args.org} no trae anonymizer_enabled: {sorted(data)}"
        )
        return data

    def toggle(self, enabled: bool, who: str | None = None, reason: str | None = REASON) -> Http:
        body: dict[str, Any] = {"enabled": enabled}
        if reason is not None:
            body["reason"] = reason
        return self.webapp("PUT", f"/api/v1/orgs/{self.args.org}/anonymizer", who or self.args.admin_email, body)

    def switch(self, enabled: bool) -> str:
        """Cambia el anonimizador como PLATFORM_ADMIN, comprueba la respuesta y espera a que llegue al proxy."""
        res = self.toggle(enabled)
        check(
            res.status == 200,
            f"PUT …/anonymizer {{enabled: {str(enabled).lower()}}} → HTTP {res.status}: {h3.snippet(res.body, 200)}",
        )
        data = res.json() or {}
        propagated = data.get("propagated")
        check(isinstance(propagated, bool), f"la respuesta no trae «propagated»: {h3.snippet(res.body, 200)}")
        flag = org_data(data).get("anonymizer_enabled")
        check(flag is enabled, f"la respuesta dice anonymizer_enabled = {flag!r} (se esperaba {enabled})")
        check(self.org_state().get("anonymizer_enabled") is enabled, "GET de la organización no refleja el cambio")
        time.sleep(self.args.propagation_wait)  # ai_ip.changed llega al proxy por Pub/Sub, sin esperar respuesta
        return f"anonimizador {'encendido' if enabled else 'apagado'} (propagated: {str(propagated).lower()})"

    def ensure_off(self) -> str:
        state = self.org_state()
        check(
            state.get("ai_ip_enabled") is True,
            f"«{self.args.org}» tiene AI-IP apagado: no se puede probar el modo observación",
        )
        if state.get("anonymizer_enabled") is False:
            return f"«{self.args.org}» ya tiene el anonimizador apagado"
        return f"estaba encendido (¿una ejecución anterior?): {self.switch(False)}"

    # --- 5 a 7: anonimizador apagado y PLATFORM_ADMIN -----------------------------------------------
    def observe_case(self, api: str, stream: bool) -> str:
        case = self.case("observacion", api, stream)
        text = f"{case.tag}. {SENSITIVE_TEXT}."
        case.payload = h3.request_body(api, case.model, text, stream=stream)
        raw, res = self.send(self.binding, self.args.email, api, case.payload)
        check(res.status == 200, f"{API_NAMES[api]} → HTTP {res.status}: {h3.snippet(res.body)}")
        self.observed.append(case)
        problems = self.reply_texts(api, res, stream, text) + self.clear_problems(case.tag, api, raw)
        check(not problems, "\n".join(problems))
        return (
            "el proveedor recibió lo mismo que envió el cliente, con los tres valores en claro y sin tokens; "
            "el cliente recibió el eco completo"
        )

    def causes(self) -> str:
        """Como dueño: cada caso es una conversación sin sustituciones, con `WARNING` y la causa (anonimizador ✘)."""
        check(self.observed, "ningún caso con el anonimizador apagado llegó a responder 200")
        who = self.args.email
        deadline = time.monotonic() + self.args.trace_timeout
        while True:
            items = {i.get("model"): i for i in self.list_conversations(who)}
            ready = all(
                items.get(c.model, {}).get("turn_count") and items[c.model].get("verification_status")
                for c in self.observed
            )
            if ready or time.monotonic() > deadline:
                break
            time.sleep(0.25)
        problems: list[str] = []
        for case in self.observed:
            item = items.get(case.model)
            if item is None:
                problems.append(f"{case.label}: la conversación no aparece en GET /api/v1/conversations")
                continue
            if item.get("ai_ip_enabled") is not True:
                problems.append(f"{case.label}: ai_ip_enabled = {item.get('ai_ip_enabled')!r}")
            flag = anonymizer_flag(item)
            if flag is not False:
                problems.append(f"{case.label}: la causa no consta: anonymizer_enabled = {flag!r} (se esperaba false)")
            if item.get("verification_status") != "WARNING":
                problems.append(f"{case.label}: verificación {item.get('verification_status')} (se esperaba WARNING)")
            turns = self.turn_indexes(who, str(item["id"]))
            res = self.turn(who, str(item["id"]), turns[-1])
            check(res.status == 200, f"GET …/turns/{turns[-1]} ({who}) → HTTP {res.status}")
            body = res.json() or {}
            problems += [f"{case.label}: {p}" for p in observation_problems(body.get("verification"))]
            sent = segments_text(body.get("request_sent"))
            missing = [kind for kind, value in VALUES.items() if value not in sent]
            if missing:  # control positivo: el dueño sí lee su propio contenido
                problems.append(
                    f"{case.label}: el dueño no lee el texto enviado en claro (faltan {', '.join(missing)})"
                )
        check(not problems, "\n".join(problems))
        return (
            f"{len(self.observed)} turno(s) WARNING sin reemplazos, con DNI, EMAIL e IBAN en residual_matches, "
            "y la causa (anonimizador ✘)"
        )

    def admin_view(self, case: h3.Case, org: str | None = None, *, metrics: bool = True) -> list[str]:
        """Lo que ve el PLATFORM_ADMIN de una conversación: lista, detalle, turnos y revelado, sin contenido."""
        who, needles = self.args.admin_email, needles_for(self.nonce)
        res = self.webapp("GET", f"/api/v1/conversations?org={org or self.args.org}&q={self.nonce}&limit=200", who)
        if res.status != 200:
            return [f"{case.label}: GET /api/v1/conversations → HTTP {res.status}"]
        problems = [
            f"{case.label}: la lista de conversaciones contiene {leak}" for leak in content_leaks(res.body, needles)
        ]
        item = next((i for i in (res.json() or {}).get("items") or [] if i.get("model") == case.model), None)
        if item is None:
            return [*problems, f"{case.label}: el PLATFORM_ADMIN no ve la conversación en la lista"]
        cid = str(item["id"])
        if item.get("content_available") not in (None, False):
            problems.append(f"{case.label}: la lista dice content_available = {item.get('content_available')!r}")
        if item.get("originals") not in (None, "LOCKED"):
            problems.append(f"{case.label}: originals = {item.get('originals')!r} (se esperaba LOCKED)")
        detail = self.webapp("GET", f"/api/v1/conversations/{cid}", who)
        if detail.status != 200:
            return [*problems, f"{case.label}: GET /api/v1/conversations/{cid} → HTTP {detail.status}"]
        problems += [
            f"{case.label}: el detalle de la conversación contiene {leak}"
            for leak in content_leaks(detail.body, needles)
        ]
        for index in sorted(int(t.get("turn_index") or 0) for t in (detail.json() or {}).get("turns") or []):
            turn = self.turn(who, cid, index)
            if turn.status != 200:
                problems.append(f"{case.label}: GET …/turns/{index} → HTTP {turn.status}")
                continue
            problems += [f"{case.label}: turno {index}: {p}" for p in admin_turn_problems(turn.json(), metrics=metrics)]
            problems += [
                f"{case.label}: el turno {index} contiene {leak}" for leak in content_leaks(turn.body, needles)
            ]
            reveal = self.webapp("POST", f"/api/v1/conversations/{cid}/turns/{index}/reveal", who, {"reason": REASON})
            problems += [
                f"{case.label}: el revelado del turno {index} contiene {leak}"
                for leak in content_leaks(reveal.body, needles)
            ]
            if reveal.status == 200:
                problems.append(
                    f"{case.label}: el PLATFORM_ADMIN puede revelar el turno {index} "
                    "de una organización ajena (HTTP 200)"
                )
        return problems

    def admin_without_content(self) -> str:
        check(self.observed, "ningún caso con el anonimizador apagado llegó a responder 200")
        problems: list[str] = []
        for case in self.observed:
            problems += self.poll(lambda case=case: self.admin_view(case))
        check(not problems, "\n".join(problems))
        return (
            f"{len(self.observed)} conversación(es): lista, detalle, turnos y revelado sin contenido ni valores "
            "(content_available: false)"
        )

    def admin_without_ai_ip(self) -> str:
        """Con AI-IP apagado (`demo`) el texto enviado es el original: tampoco lo ve el PLATFORM_ADMIN."""
        case = self.case("bypass", "messages")
        case.payload = h3.request_body("messages", case.model, f"{case.tag}. {SENSITIVE_TEXT}.")
        _, res = self.send(self.bypass_binding, self.args.bypass_email, "messages", case.payload)
        check(res.status == 200, f"turno en «{self.args.bypass_org}» → HTTP {res.status}: {h3.snippet(res.body)}")
        listing = lambda: self.list_conversations(self.args.bypass_email, self.args.bypass_org)  # noqa: E731
        deadline = time.monotonic() + self.args.trace_timeout
        while not any(i.get("model") == case.model for i in listing()):
            check(time.monotonic() < deadline, f"la conversación ({case.model}) no aparece para su dueño")
            time.sleep(0.25)
        problems = self.poll(
            lambda: self.admin_view(case, self.args.bypass_org, metrics=False)
        )  # AI-IP apagado: sin verificación
        check(not problems, "\n".join(problems))
        return f"conversación de «{self.args.bypass_org}» (AI-IP apagado) sin contenido para el PLATFORM_ADMIN"

    def websocket(self) -> str:
        a = self.args
        url = ws_url(a.app_url, f"/api/v1/ws/traces?org={a.org}")
        sockets = {who: WsClient(url, cookie=self.sessions[who], origin=a.app_url, timeout=a.timeout)
                   for who in (a.admin_email, a.email)}  # fmt: skip
        try:
            for who, ws in sockets.items():
                hello = ws.recv(5)
                check(hello is not None and '"hello"' in hello, f"{who}: el WebSocket no envió «hello»: {hello!r}")
            case = self.case("vivo", "messages")
            case.payload = h3.request_body("messages", case.model, f"{case.tag}. {SENSITIVE_TEXT}.")
            _, res = self.send(self.binding, a.email, "messages", case.payload)
            check(res.status == 200, f"turno en vivo → HTTP {res.status}: {h3.snippet(res.body)}")
            frames: dict[str, list[str]] = {who: [] for who in sockets}
            done: set[str] = set()
            deadline = time.monotonic() + a.trace_timeout
            while len(done) < len(sockets) and time.monotonic() < deadline:
                for who, ws in sockets.items():
                    try:
                        frame = ws.recv(0.2)
                    except WsClosedError as exc:
                        raise StepError(f"{who}: {exc}") from None
                    if frame is not None:
                        frames[who].append(frame)
                        if '"turn.completed"' in frame and case.model in frame:
                            done.add(who)
            time.sleep(0.3)  # lo que aún viaje
            for who, ws in sockets.items():
                while (frame := ws.recv(0.1)) is not None:
                    frames[who].append(frame)
        finally:
            for ws in sockets.values():
                ws.close()
        admin = frames[a.admin_email]
        about = [f for f in admin if case.model in f]
        problems = (
            [f"el PLATFORM_ADMIN no recibió ninguna trama de la conversación ({len(admin)} trama(s) en total)"]
            if not about
            else []
        )
        for frame in admin:
            problems += [
                f"trama del PLATFORM_ADMIN con {leak}: {frame[:100]!r}"
                for leak in content_leaks(frame, needles_for(self.nonce))
            ]
            parsed = None
            with contextlib.suppress(ValueError):
                parsed = json.loads(frame).get("turn")
            if isinstance(parsed, dict):
                problems += [f"trama del PLATFORM_ADMIN: {p}" for p in admin_turn_problems(parsed, metrics=False)]
        check(not problems, "\n".join(problems))
        owner_saw = any(content_leaks(f, [DNI, EMAIL, IBAN]) for f in frames[a.email])
        control = (
            "el dueño sí recibió el contenido (control positivo)"
            if owner_saw
            else "el dueño no recibió contenido por el WebSocket (sin control positivo)"
        )
        return f"el PLATFORM_ADMIN recibió {len(about)} trama(s) de la conversación sin contenido; {control}"

    # --- 8: el interruptor --------------------------------------------------------------------------
    @staticmethod
    def expect(problems: list[str], label: str, res: Http, status: int, code: str | None = None) -> None:
        """Anota un problema si `res` no es la respuesta `status` (y el `error` `code`) que se esperaba."""
        got = res.json().get("error") if isinstance(res.json(), dict) else None
        if res.status != status or (code and got != code):
            problems.append(
                f"{label}: HTTP {res.status} {got or ''} (se esperaba {status}{' ' + code if code else ''})"
            )

    def switch_rules(self) -> str:
        a, problems = self.args, []
        expect = lambda *args: self.expect(problems, *args)  # noqa: E731
        state = self.org_state().get("anonymizer_enabled")
        expect("sin motivo", self.toggle(not state, reason=None), 422, "REASON_REQUIRED")
        expect("motivo de 2 caracteres", self.toggle(not state, reason="ab"), 422, "REASON_REQUIRED")
        expect(f"{a.email} (USER de la organización)", self.toggle(not state, who=a.email), 403)
        expect(
            f"{a.orgadmin_email} (ORG_ADMIN de otra organización)", self.toggle(not state, who=a.orgadmin_email), 404
        )
        off = self.webapp(
            "PUT", f"/api/v1/orgs/{a.bypass_org}/anonymizer", a.admin_email, {"enabled": True, "reason": REASON}
        )
        expect(f"«{a.bypass_org}» con AI-IP apagado", off, 409, "AI_IP_DISABLED")
        after = self.org_state().get("anonymizer_enabled")
        if after != state:
            problems.append(f"una petición rechazada cambió el anonimizador de {state} a {after}")
        check(not problems, "\n".join(problems))
        return (
            "sin motivo → 422 REASON_REQUIRED; USER → 403; ORG_ADMIN ajeno → 404; "
            "AI-IP apagado → 409 AI_IP_DISABLED; nada cambió"
        )

    def first_turn(self, kind: str, api: str) -> str:
        """Primer turno de la conversación `kind` (A, B o C) en `api`, sin streaming."""
        case = self.case(f"i{kind.lower()}", api)
        first = f"{case.tag}. {SENSITIVE_TEXT}."
        case.payload = h3.request_body(api, case.model, first)
        raw, res = self.send(self.binding, self.args.email, api, case.payload)
        check(
            res.status == 200, f"{API_NAMES[api]}: primer turno de {kind} → HTTP {res.status}: {h3.snippet(res.body)}"
        )
        problems = self.reply_texts(api, res, False, first)
        problems += self.tokenized_problems(case.tag, api) if kind == "B" else self.clear_problems(case.tag, api, raw)
        reply = h3.extract_reply(api, res.body, stream=False)
        conv = Conv(case, first, reply.texts[0][1] if reply.texts else "", reply.response_id)
        conv.past.append((first, conv.answer))
        if api == "responses" and not conv.response_id:
            problems.append("la respuesta de Responses no trae «id»")
        self.conversations[kind][api] = conv
        return "\n".join(f"{API_NAMES[api]}: {p}" for p in problems)

    def follow_turn(self, kind: str, api: str, *, tokenized: bool) -> str:
        """Siguiente turno de la conversación `kind`, con el historial completo: en claro o tokenizado."""
        conv = self.conversations[kind][api]
        n = len(conv.follow) + 1
        case = self.case(f"i{kind.lower()}-sigue" + ("" if n == 1 else str(n)), api)
        follow = f"{case.tag}. Sigue. {SENSITIVE_TEXT}."
        if api == "responses":
            case.payload = h3.request_body(api, conv.case.model, follow, previous_response_id=conv.response_id)
        else:
            past = [m for user, answer in conv.past for m in ({"role": "user", "content": user},
                                                              {"role": "assistant", "content": answer})]  # fmt: skip
            case.payload = h3.request_body(api, conv.case.model, follow, history=past)
        raw, res = self.send(self.binding, self.args.email, api, case.payload)
        check(
            res.status == 200,
            f"{API_NAMES[api]}: turno {n + 1} de {kind} → HTTP {res.status}: {h3.snippet(res.body)}",
        )
        problems = self.reply_texts(api, res, False, follow)
        problems += self.tokenized_problems(case.tag, api) if tokenized else self.clear_problems(case.tag, api, raw)
        reply = h3.extract_reply(api, res.body, stream=False)
        conv.past.append((follow, reply.texts[0][1] if reply.texts else ""))
        conv.response_id = reply.response_id or conv.response_id
        conv.follow.append(case)
        return "\n".join(f"{API_NAMES[api]}: {p}" for p in problems)

    def phase(self, steps: Iterable[tuple[str, str]], what: str, *, tokenized: bool = False) -> str:
        problems = []
        for kind, api in steps:
            if what == "first":
                problems.append(self.first_turn(kind, api))
            else:
                problems.append(self.follow_turn(kind, api, tokenized=tokenized))
        check(not any(problems), "\n".join(p for p in problems if p))
        return ""

    def apis(self) -> list[str]:
        return [api for api in APIS if api in self.args.apis]

    def api_names(self) -> str:
        return ", ".join(API_NAMES[api] for api in self.apis())

    def initial(self, kind: str) -> str:
        self.phase([(kind, api) for api in self.apis()], "first")
        what = {
            "A": "con el anonimizador apagado: los tres valores en claro hasta el proveedor",
            "B": "con él encendido: solo tokens hasta el proveedor y valores restaurados para el cliente",
            "C": "con él apagado otra vez: los tres valores en claro",
        }[kind]
        return f"conversación {kind} ({self.api_names()}) {what}"

    def continues(self, kind: str, *, then: str = "same") -> str:
        """Otro turno de `kind`. `then`: `same` (conserva su estado), `raise` (A, que empezó en claro, sube a
        anonimizada con el anonimizador encendido: trinquete, H4-08) o `stay` (A ya subió y no baja al apagarlo)."""
        tokenized = kind == "B" or then in ("raise", "stay")
        self.phase([(kind, api) for api in self.apis()], "follow", tokenized=tokenized)
        what = {
            "raise": "sube: su turno sale tokenizado y restaurado",
            "stay": "sigue anonimizada aunque se haya apagado el anonimizador",
        }.get(then, "sigue tokenizada y restaurada" if kind == "B" else "sigue en claro")
        return f"la conversación {kind} {what} ({self.api_names()})"

    def switch_state(self) -> str:
        """La webapp: A y C empezaron con el anonimizador apagado (`WARNING`), B con él encendido (`CLEAN`); A subió
        a anonimizada en su turno 2 (`protected_from_turn`), los otros no tienen `protected_from_turn`."""
        who, problems = self.args.email, []
        expected = {"A": (False, "WARNING", 2), "B": (True, "CLEAN", None), "C": (False, "WARNING", None)}
        for kind, (flag, status, raised_at) in expected.items():
            for api, conv in self.conversations[kind].items():
                item = self.wait_conversation(who, conv.case.model, turns=1 + len(conv.follow))
                label = f"{kind} · {API_NAMES[api]}"
                if anonymizer_flag(item) is not flag:
                    problems.append(f"{label}: anonymizer_enabled = {anonymizer_flag(item)!r} (se esperaba {flag})")
                if item.get("verification_status") != status:
                    problems.append(f"{label}: verificación {item.get('verification_status')} (se esperaba {status})")
                if item.get("protected_from_turn") != raised_at:
                    problems.append(
                        f"{label}: protected_from_turn = {item.get('protected_from_turn')!r} (se esperaba {raised_at})"
                    )
        check(not problems, "\n".join(problems))
        return (
            "A: anonimizador ✘ al empezar, WARNING y protegida desde el turno 2; B: ✔ y CLEAN; "
            "C: ✘ y WARNING, sin subir"
        )

    def raised_turn_problems(self, who: str, cid: str, index: int, *, visible: bool) -> list[str]:
        """Un turno de A visto por el PLATFORM_ADMIN: sin contenido (salió en claro) o solo con tokens."""
        turn = self.turn(who, cid, index)
        if turn.status != 200:
            return [f"GET …/turns/{index} → HTTP {turn.status}"]
        data = turn.json() or {}
        if not visible:
            return [f"turno {index}: {p}" for p in admin_turn_problems(data)] + [
                f"el turno {index} (salió en claro) contiene {leak}"
                for leak in content_leaks(turn.body, needles_for(self.nonce))
            ]
        problems = []
        if data.get("content_available") is not True:
            problems.append(f"turno {index}: content_available = {data.get('content_available')!r} (salió anonimizado)")
        sent = segments_text(data.get("request_sent"))
        problems += [f"turno {index}: lo enviado no trae {token}" for token in TOKENS if token not in sent]
        raw = turn.body.decode(errors="replace") if isinstance(turn.body, bytes) else str(turn.body)
        problems += [f"turno {index}: contiene el valor original de {kind} «{value}»"
                     for kind, value in VALUES.items() if value in raw]  # fmt: skip
        for key in ("request_original", "response_returned"):
            if data.get(key):
                problems.append(f"turno {index}: el PLATFORM_ADMIN recibe «{key}»")
        reveal = self.webapp("POST", f"/api/v1/conversations/{cid}/turns/{index}/reveal", who, {"reason": REASON})
        if reveal.status == 200:
            problems.append(f"turno {index}: el PLATFORM_ADMIN puede revelar los originales (HTTP 200)")
        return problems

    def admin_raised(self) -> str:
        """El PLATFORM_ADMIN ve por turno (H4-08): el 1 de A salió en claro (sin contenido), los siguientes salieron
        anonimizados (los ve con tokens, sin valores)."""
        who = self.args.admin_email
        problems: list[str] = []
        for api, conv in self.conversations["A"].items():
            label = f"A · {API_NAMES[api]}"
            item = next((i for i in self.list_conversations(who) if i.get("model") == conv.case.model), None)
            if item is None:
                problems.append(f"{label}: el PLATFORM_ADMIN no ve la conversación en la lista")
                continue
            cid, last = str(item["id"]), 1 + len(conv.follow)
            if item.get("protected_from_turn") != 2:
                problems.append(f"{label}: protected_from_turn = {item.get('protected_from_turn')!r} (se esperaba 2)")
            problems += [f"{label}: {p}" for p in self.raised_turn_problems(who, cid, 1, visible=False)]
            for index in range(2, last + 1):
                problems += [f"{label}: {p}" for p in self.raised_turn_problems(who, cid, index, visible=True)]
        check(not problems, "\n".join(problems))
        return (
            "turno 1 de A sin contenido (salió en claro) y los siguientes visibles solo con tokens, "
            "sin valores ni revelado"
        )

    def back_to_off(self) -> str:
        state = self.org_state().get("anonymizer_enabled")
        if state is False:
            return f"«{self.args.org}» tiene el anonimizador apagado, como al empezar"
        return self.switch(False)

    # --- 9: eliminar una organización ------------------------------------------------------------------
    def admin_post(self, path: str, payload: Any = None, who: str | None = None, method: str = "POST") -> Http:
        return self.webapp(method, path, who or self.args.admin_email, payload)

    def delete_body(self, **fields: Any) -> dict[str, Any]:
        """Cuerpo de eliminar una organización: lleva la reautenticación del PLATFORM_ADMIN (H4-17).
        La pila de desarrollo tiene BAAS_DEV_SKIP_TOTP=1, así que basta la contraseña."""
        return {**fields, "current_password": self.args.password}

    def member_chat(self) -> Http:
        """Un turno de Chat Completions de `orgadmin_email` por la ruta de la organización que se elimina."""
        tag = h3.case_tag(self.nonce, "eliminar", "chat", "json")
        payload = h3.request_body("chat", f"e2e-h4-{self.nonce}-eliminar-chat", f"{tag}. Hola.")
        return self.send(self.member_binding, self.args.orgadmin_email, "chat", payload)[1]

    def create_org(self) -> str:
        a, slug = self.args, self.slug
        res = self.admin_post("/api/v1/orgs", {"slug": slug, "name": f"E2E eliminar {self.nonce}"})
        check(res.status == 201, f"POST /api/v1/orgs → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        res = self.admin_post(
            f"/api/v1/orgs/{slug}/members", {"email": a.orgadmin_email, "governance_role": "ORG_ADMIN"}
        )
        check(res.status == 201, f"invitar a {a.orgadmin_email} → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        res = self.admin_post(f"/api/v1/orgs/{slug}/ai-ip", {"enabled": True, "reason": REASON}, method="PUT")
        check(res.status == 200, f"PUT …/ai-ip → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        self.member_binding = self.handshake(a.orgadmin_email, slug, ai_ip=True)
        # Datos en el vault (un turno con valores) y en el MCP (un documento).
        case = self.case("eliminar", "messages")
        case.payload = h3.request_body("messages", case.model, f"{case.tag}. {SENSITIVE_TEXT}.")
        _, turn = self.send(self.member_binding, a.orgadmin_email, "messages", case.payload)
        check(turn.status == 200, f"turno en «{slug}» → HTTP {turn.status}: {h3.snippet(turn.body)}")
        problems = self.tokenized_problems(case.tag, "messages")
        check(not problems, "\n".join(problems))
        doc = f"knowledge/e2e/e2e-{self.nonce}.md"
        content = f"---\ntitle: Prueba {self.nonce}\ntype: knowledge\nroles: []\n---\nDocumento de la prueba de H4.\n"
        try:
            with McpClient(
                str(self.member_binding["mcp_url"]), self.key_of(a.orgadmin_email), timeout=a.timeout
            ) as mcp:
                written = mcp.call_tool("baas_write_document", {"path": doc, "content": content})
        except Exception as exc:
            raise StepError(f"baas_write_document en «{slug}»: {type(exc).__name__}: {exc}") from None
        check(written.get("path") == doc, f"baas_write_document no devolvió la ruta: {written}")
        return (
            f"organización «{slug}» con AI-IP, {a.orgadmin_email} como ORG_ADMIN, "
            "un turno con valores y un documento en el MCP"
        )

    def vault_before(self) -> str:
        """Antes de eliminar, el vault tiene entradas de la organización (si no, «vacío» después no probaría nada) y
        se anotan sus conversaciones: las claves de la instancia efímera se buscan por conversación (H4-02)."""
        insp, slug = self.inspector, self.slug
        assert insp is not None  # el paso solo se registra con inspector
        problems = self.poll(lambda: [] if insp.vault_rows(slug) > 0 else ["el vault no tiene entradas"], 5)
        check(not problems, f"antes de eliminar, el vault de «{slug}» debería tener entradas: {problems}")
        self.org_conversations = insp.conversation_ids(slug)
        check(
            self.org_conversations, f"el vault tiene filas de «{slug}» pero ninguna conversación: ¿cambió el esquema?"
        )
        rows, convs = insp.vault_rows(slug), len(self.org_conversations)
        detail = f"el vault tiene {rows} fila(s) de «{slug}» en {convs} conversación(es)"
        if EPHEMERAL in insp.redis_services():
            held = [k for k in insp.redis_keys(slug, self.org_conversations) if k.startswith(f"[{EPHEMERAL}]")]
            return f"{detail}; {EPHEMERAL} guarda {len(held)} clave(s) de ellas"
        return f"{detail}; la pila no tiene {EPHEMERAL}"

    def delete_rules(self) -> str:
        a, slug, problems = self.args, self.slug, []
        expect = lambda *args: self.expect(problems, *args)  # noqa: E731
        body = self.delete_body(reason=REASON, confirm_slug=slug)
        expect("sin bloquear", self.admin_post(f"/api/v1/orgs/{slug}/delete", body), 409, "ORG_NOT_BLOCKED")
        expect(f"{a.orgadmin_email} (ORG_ADMIN de la organización)",
               self.admin_post(f"/api/v1/orgs/{slug}/delete", body, who=a.orgadmin_email), 403)  # fmt: skip
        expect(
            "la organización meta",
            self.admin_post(f"/api/v1/orgs/{META_ORG}/delete", self.delete_body(reason=REASON, confirm_slug=META_ORG)),
            403,
            "ORG_PROTECTED",
        )
        expect(
            "sin motivo",
            self.admin_post(f"/api/v1/orgs/{slug}/delete", self.delete_body(confirm_slug=slug)),
            422,
            "REASON_REQUIRED",
        )
        # H4-17: sin contraseña o con otra no se elimina nada (el 401 cuenta para el bloqueo de la cuenta)
        no_password = {"reason": REASON, "confirm_slug": slug}
        expect("sin contraseña", self.admin_post(f"/api/v1/orgs/{slug}/delete", no_password), 422, "VALIDATION_ERROR")
        wrong = {**no_password, "current_password": "no-es-la-contraseña"}
        expect(
            "contraseña incorrecta", self.admin_post(f"/api/v1/orgs/{slug}/delete", wrong), 401, "INVALID_CREDENTIALS"
        )
        res = self.webapp("GET", f"/api/v1/orgs/{slug}", a.admin_email)
        if res.status != 200:
            problems.append(f"tras las peticiones rechazadas, GET /api/v1/orgs/{slug} → HTTP {res.status}")
        check(not problems, "\n".join(problems))
        return (
            "sin bloquear → 409 ORG_NOT_BLOCKED; ORG_ADMIN → 403; meta → 403 ORG_PROTECTED; sin motivo → 422; "
            "sin contraseña → 422; contraseña incorrecta → 401; "
            "la organización sigue existiendo"
        )

    def block_org(self) -> str:
        a, slug = self.args, self.slug
        res = self.admin_post(f"/api/v1/orgs/{slug}/block", {"blocked": True, "reason": REASON}, method="PUT")
        check(res.status == 200, f"PUT …/block → HTTP {res.status}: {h3.snippet(res.body, 200)}")

        def blocked() -> list[str]:
            got = self.member_chat()
            etype, code, _ = h3.provider_error(got.body)
            ok = got.status == 403 and code == "baas_access_blocked"
            return (
                []
                if ok
                else [f"el proxy respondió HTTP {got.status} {etype} {code} (se esperaba 403 baas_access_blocked)"]
            )

        problems = self.poll(blocked)
        check(not problems, "\n".join(problems))
        res = self.admin_post(
            f"/api/v1/orgs/{slug}/delete",
            {"reason": REASON, "confirm_slug": f"{slug}-otro", "current_password": self.args.password},
        )
        err = (res.json() or {}).get("error") if isinstance(res.json(), dict) else None
        check(
            res.status == 422 and err == "SLUG_MISMATCH",
            f"con otro slug: HTTP {res.status} {err} (se esperaba 422 SLUG_MISMATCH)",
        )
        return (
            f"bloqueada: el proxy responde 403 baas_access_blocked a {a.orgadmin_email}; "
            "con otro slug → 422 SLUG_MISMATCH"
        )

    def delete_org(self) -> str:
        slug = self.slug
        res = self.admin_post(f"/api/v1/orgs/{slug}/delete", self.delete_body(reason=REASON, confirm_slug=slug))
        check(res.status == 200, f"POST …/delete → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        propagated = (res.json() or {}).get("propagated")
        check(isinstance(propagated, bool), f"la respuesta no trae «propagated»: {h3.snippet(res.body, 200)}")
        return f"organización «{slug}» eliminada (propagated: {str(propagated).lower()})"

    def gone(self) -> str:
        a, slug = self.args, self.slug
        problems: list[str] = []
        res = self.webapp("GET", f"/api/v1/orgs/{slug}", a.admin_email)
        if res.status != 404:
            problems.append(f"GET /api/v1/orgs/{slug} → HTTP {res.status} (se esperaba 404)")
        again = self.admin_post(f"/api/v1/orgs/{slug}/delete", self.delete_body(reason=REASON, confirm_slug=slug))
        if again.status != 404:
            problems.append(f"la segunda eliminación → HTTP {again.status} (se esperaba 404)")
        listing = self.webapp("GET", f"/api/v1/conversations?org={slug}", a.admin_email)
        if listing.status == 200 and (listing.json() or {}).get("items"):
            problems.append("la webapp aún lista conversaciones de la organización eliminada")

        def proxy_denied() -> list[str]:
            got = self.member_chat()
            etype, code, _ = h3.provider_error(got.body)
            ok = got.status == 403 and code == "baas_access_denied"
            return (
                []
                if ok
                else [f"el proxy respondió HTTP {got.status} {etype} {code} (se esperaba 403 baas_access_denied)"]
            )

        problems += self.poll(proxy_denied)
        try:
            with McpClient(
                str(self.member_binding["mcp_url"]), self.key_of(a.orgadmin_email), timeout=a.timeout
            ) as mcp:
                mcp.call_tool("baas_whoami", {})
            problems.append("el MCP sigue sirviendo la organización eliminada")
        except McpToolError as exc:
            if exc.code != "NOT_FOUND":
                problems.append(f"el MCP respondió {exc.code} (se esperaba 404 NOT_FOUND)")
        except Exception as exc:
            problems.append(f"el MCP: {type(exc).__name__}: {exc}")
        hs = h3.http("POST", f"{a.app_url}/api/v1/plugin/handshake",
                     headers={"Authorization": f"Bearer {self.key_of(a.orgadmin_email)}"}, payload={},
                     timeout=a.timeout)  # fmt: skip
        listed = {o.get("slug") for o in (hs.json() or {}).get("organizations") or [] if isinstance(o, dict)}
        if hs.status != 200:
            problems.append(
                f"el handshake de {a.orgadmin_email} → HTTP {hs.status} "
                "(los usuarios no se eliminan con su organización)"
            )
        elif slug in listed:
            problems.append("el handshake sigue listando la organización eliminada")
        check(not problems, "\n".join(problems))
        return (
            "webapp 404 (también la segunda eliminación); proxy 403 baas_access_denied; MCP 404; "
            "el handshake sigue funcionando sin ella"
        )

    def slug_not_reusable(self) -> str:
        a, slug = self.args, self.slug
        reuse = self.admin_post("/api/v1/orgs", {"slug": slug, "name": "Otra vez"})
        taken = self.admin_post("/api/v1/orgs", {"slug": a.bypass_org, "name": "Duplicada"})
        got = (reuse.json() or {}).get("error") if isinstance(reuse.json(), dict) else None
        want = (taken.json() or {}).get("error") if isinstance(taken.json(), dict) else None
        check(
            reuse.status == taken.status == 409,
            f"crear «{slug}» → HTTP {reuse.status}; crear «{a.bypass_org}» (en uso) → HTTP {taken.status}"
            " (se esperaba 409 en los dos)",
        )
        check(got == want, f"el slug eliminado da {got}, un slug en uso da {want}: deberían ser el mismo error")
        return f"crear «{slug}» de nuevo → 409 {got}, igual que un slug en uso"

    def audit_survives(self) -> str:
        res = self.webapp("GET", "/api/v1/audit?action=org.deleted&limit=200", self.args.admin_email)
        check(res.status == 200, f"GET /api/v1/audit → HTTP {res.status}: {h3.snippet(res.body, 200)}")
        items = [i for i in (res.json() or {}).get("items") or [] if isinstance(i, dict)]
        mine = [i for i in items if self.slug in h3.strings_in(i)]
        check(
            mine,
            f"la auditoría no tiene ninguna entrada org.deleted de «{self.slug}» ({len(items)} org.deleted en total)",
        )
        reason_seen = any(REASON in h3.strings_in(i) for i in mine)
        check(reason_seen, "la entrada de auditoría de la eliminación no lleva el motivo")
        return f"org.deleted de «{self.slug}» en la auditoría, con su motivo"

    def inside(self) -> str:
        insp = self.inspector
        assert insp is not None  # el paso solo se registra con inspector
        problems = self.poll(
            lambda: self.inside_problems(insp, self.slug)
        )  # el proxy y el MCP lo hacen al recibir org.deleted
        check(not problems, "\n".join(problems))
        services = insp.redis_services()
        redis = " y ".join(f"«{s}»" for s in services)
        note = "" if EPHEMERAL in services else f" (la pila no tiene {EPHEMERAL}: solo se miró «{PERSISTENT}»)"
        return f"vault vacío, sin claves en Redis ({redis}) y el directorio del MCP en cuarentena (.quarantine/){note}"

    def inside_problems(self, insp: Inspector, slug: str) -> list[str]:
        problems = []
        rows = insp.vault_rows(slug)
        if rows:
            problems.append(f"el vault conserva {rows} fila(s) de la organización")
        if not self.org_conversations:
            problems.append("no se anotaron las conversaciones de la organización antes de eliminarla (¿falló 9.1b?)")
        keys = insp.redis_keys(slug, self.org_conversations)
        if keys:
            problems.append(f"Redis conserva claves de la organización: {', '.join(keys[:5])}")
        return problems + self.quarantine_problems(insp, slug)

    def quarantine_problems(self, insp: Inspector, slug: str) -> list[str]:
        problems = []
        if slug in insp.mcp_org_dirs():
            problems.append(f"el directorio del MCP «orgs/{slug}» sigue en su sitio")
        if not [name for name in insp.mcp_quarantine() if name.startswith(f"{slug}-")]:
            problems.append(f"no hay ninguna entrada «{slug}-<fecha>» en .quarantine/ del MCP")
        return problems

    # --- 10: los originales no llegan al disco de Redis (H4-02, la prueba canario) ---------------------------------
    def settle(self) -> float:
        return min(self.args.trace_timeout, SETTLE)

    def redis_inspector(self) -> Inspector:
        assert self.inspector is not None  # los pasos del grupo solo se registran con inspector
        return self.inspector

    def has_ephemeral(self) -> str:
        services = self.redis_inspector().redis_services()
        check(
            EPHEMERAL in services,
            f"sin {EPHEMERAL}: los originales siguen llegando al AOF de «{PERSISTENT}» (la pila es anterior a H4-02 o "
            "le falta el servicio; sin BAAS_REDIS_EPHEMERAL_URL el proxy y la webapp usan una sola instancia)",
        )
        return f"la pila tiene {' y '.join(f'«{s}»' for s in services)}"

    def no_residue(self) -> str:
        """La persistente no guarda lo que dejó una pila anterior a A′ (H4-02): copias calientes del vault y trazas con
        valores originales. Se mira antes de enviar nada, para que no se confunda con una fuga de esta ejecución."""
        residue = self.redis_inspector().redis_residue(PERSISTENT)
        if residue:
            what = {
                "baas:conv:*:vault": "clave(s) `baas:conv:*:vault` (copias calientes del vault)",
                "baas:conv:*:events": "clave(s) `baas:conv:*:events`",
                "baas:traces": "entrada(s) en el stream `baas:traces`",
            }
            found = ", ".join(f"{n} {what.get(pattern, pattern)}" for pattern, n in residue.items())
            raise StepError(
                f"«{PERSISTENT}» (persistente) conserva {found}: residuo de una pila anterior a A′ (H4-02): "
                "ejecuta `deploy/scripts/purge-redis-originals.sh` (y vuelve a lanzar la prueba). Si ya lo habías "
                "ejecutado y vuelve a salir, algo escribe todavía los originales en la persistente: busca en "
                f"BAAS_REDIS_EPHEMERAL_URL del proxy y de la webapp (10.4 y 10.6 lo delatan con `--only originales`)"
            )
        return f"«{PERSISTENT}» no guarda copias calientes del vault, claves de eventos ni trazas de una pila anterior"

    def send_originals(self) -> str:
        """Un turno con los tres valores y la cadena de la ejecución por cada modo, API y forma (con y sin stream)."""
        a = self.args
        modes = [
            ("bypass", self.bypass_binding, a.bypass_email, a.bypass_org),
            ("observe", self.binding, a.email, a.org),
            ("anon", self.anon_binding, a.anon_email, a.anon_org),
        ]
        problems: list[str] = []
        self.original_cases = []
        for api in self.apis():
            for stream in (False, True):
                for mode, binding, email, org in modes:
                    case = self.case(f"orig-{mode}", api, stream)
                    case.payload = h3.request_body(api, case.model, f"{case.tag}. {SENSITIVE_TEXT}.", stream=stream)
                    res = self.send(binding, email, api, case.payload)[1]
                    if res.status == 200:
                        self.original_cases.append(OriginalCase(case, email, org))
                    else:
                        problems.append(f"«{org}» · {case.label} → HTTP {res.status}: {h3.snippet(res.body)}")
        check(not problems, "\n".join(problems))
        return (
            f"{len(self.original_cases)} turno(s) con DNI, email e IBAN: «{a.bypass_org}» (AI-IP apagado), "
            f"«{a.org}» (anonimizador apagado) y «{a.anon_org}» (anonimizador encendido), en {self.api_names()}, "
            "con y sin streaming"
        )

    def originals_consumed(self) -> str:
        """La webapp ya consumió las trazas: cada conversación aparece en la lista de su dueño."""

        def missing() -> list[str]:
            seen: dict[tuple[str, str], set[str]] = {}
            lost = []
            for item in self.original_cases:
                key = (item.email, item.org)
                if key not in seen:
                    seen[key] = {str(i.get("model")) for i in self.list_conversations(*key) if i.get("turn_count")}
                if item.case.model not in seen[key]:
                    lost.append(f"«{item.org}» · {item.case.label}")
            return [f"la webapp no muestra {len(lost)} conversación(es): {'; '.join(lost[:4])}"] if lost else []

        problems = self.poll(missing)
        check(not problems, "\n".join(problems))
        return f"las {len(self.original_cases)} conversaciones aparecen en la webapp: sus trazas ya se consumieron"

    def data_dir_problems(self, service: str) -> list[str]:
        """Si Redis escribe en otro directorio, el escáner (que mira `/data`) no vería sus ficheros."""
        where = self.redis_inspector().redis_setting(service, "dir").rstrip("/") or "/"
        if where != REDIS_DATA_DIR:
            return [f"«{service}» escribe en {where}, no en {REDIS_DATA_DIR}: el escáner no vería sus ficheros"]
        return []

    def aof_live_clean(self) -> str:
        """Antes de reescribir nada: la traza de cada turno se escribió (y se borró) en alguna instancia; si fue en la
        persistente, su texto sigue en el AOF vivo aunque ya no esté en memoria (el problema de H4-02)."""
        insp = self.redis_inspector()
        time.sleep(self.args.flush_wait)  # con appendfsync everysec, Redis puede retrasar hasta 2 s escribir el AOF
        problems = self.data_dir_problems(PERSISTENT)
        found = insp.disk_contains(PERSISTENT, run_needles(self.nonce))
        if found:
            problems.append(
                f"«{PERSISTENT}» (persistente) tiene en el AOF, sin reescribirlo: {fmt_needles(found)}. "
                "Las trazas con los originales llegan a su disco y borrarlas no las saca del fichero (H4-02)"
            )
        check(not problems, "\n".join(problems))
        files = insp.disk_files(PERSISTENT)
        return (
            f"{len(files)} fichero(s) en {REDIS_DATA_DIR} de «{PERSISTENT}» y ningún «caso-{self.nonce}» "
            "(el AOF vivo, sin reescribir; vale si el control positivo 10.5 pasa)"
        )

    def remove_control(self, key: str, sentinel: str) -> list[str]:
        """Borra la clave del control y reescribe el AOF para que su valor salga del fichero. No lanza: devuelve los
        problemas (si no se puede, la clave caduca sola)."""
        insp = self.redis_inspector()
        try:
            insp.redis_delete(PERSISTENT, key)
            insp.force_aof_rewrite(PERSISTENT)
            return self.poll(
                lambda: (
                    [f"el control positivo sigue en el disco de «{PERSISTENT}» tras borrar «{key}» y reescribir"]
                    if insp.disk_contains(PERSISTENT, [sentinel])
                    else []
                ),
                self.settle(),
            )
        except StepError as exc:
            return [
                f"no se pudo limpiar el control positivo: {exc}. La clave «{key}» (valor «{sentinel}») "
                f"caduca sola en {CONTROL_TTL} s"
            ]

    def scanner_control(self) -> str:
        """Control positivo del escáner: un valor propio en la persistente, que el escáner tiene que encontrar en el
        AOF vivo y tras reescribirlo (si no, «ningún original» no probaría nada). Se borra y se reescribe siempre."""
        insp = self.redis_inspector()
        key, sentinel = f"baas:e2e:escaner-{self.nonce}", f"e2e-control-{self.nonce}"
        problems: list[str] = []
        try:
            insp.redis_set_temp(PERSISTENT, key, sentinel, CONTROL_TTL)
            if self.poll(lambda: [] if insp.disk_contains(PERSISTENT, [sentinel]) else ["no"], self.settle()):
                problems.append(
                    f"el escáner no encuentra el control positivo «{sentinel}» en el AOF vivo de «{PERSISTENT}»: "
                    "no vería los originales que escribiera Redis"
                )
            insp.force_aof_rewrite(PERSISTENT)
            if not insp.disk_contains(PERSISTENT, [sentinel]):
                problems.append(
                    f"el escáner no encuentra el control positivo «{sentinel}» tras reescribir el AOF de "
                    f"«{PERSISTENT}»: no vería lo que Redis conserva en la base reescrita"
                )
        except StepError as exc:
            problems.append(str(exc))
        finally:
            problems += self.remove_control(key, sentinel)
        check(not problems, "\n".join(problems))
        return (
            f"el escáner encuentra «{sentinel}» en el AOF vivo y tras reescribirlo, y ya no lo encuentra tras "
            f"borrar «{key}» y reescribir"
        )

    def aof_rewritten_clean(self) -> str:
        """Tras `BGREWRITEAOF` el AOF es lo que Redis tiene en memoria: ningún original vivo (el vault caliente)."""
        insp = self.redis_inspector()
        insp.force_aof_rewrite(PERSISTENT)
        needles = disk_needles(self.nonce)

        def scan() -> list[str]:
            found = insp.disk_contains(PERSISTENT, needles)
            if not found:
                return []
            return [
                f"«{PERSISTENT}» (persistente) contiene tras reescribir el AOF: {fmt_needles(found)}. "
                "Lo que Redis guarda en memoria incluye originales y llega a su disco (H4-02)"
            ]

        problems = self.poll(scan, self.settle())  # Redis borra los ficheros viejos un instante después
        check(not problems, "\n".join(problems))
        files = insp.disk_files(PERSISTENT)
        return (
            f"tras BGREWRITEAOF, ninguno de los {len(needles)} centinelas en los {len(files)} fichero(s) "
            f"de «{PERSISTENT}»"
        )

    def ephemeral_clean(self) -> str:
        """La efímera no persiste nada: sin AOF ni RDB, sin ficheros en `/data` y sin centinelas en ellos."""
        insp = self.redis_inspector()
        problems = []
        for name, want in (("appendonly", "no"), ("save", "")):
            got = insp.redis_setting(EPHEMERAL, name)
            if got != want:
                problems.append(f"«{EPHEMERAL}» tiene {name} = {got!r} (se esperaba {want!r})")
        problems += self.data_dir_problems(EPHEMERAL)
        files = insp.disk_files(EPHEMERAL)
        if files:
            problems.append(
                f"«{EPHEMERAL}» tiene {len(files)} fichero(s) en {REDIS_DATA_DIR} ({', '.join(files[:4])}): "
                "sin volumen no debería haber ninguno"
            )
        found = insp.disk_contains(EPHEMERAL, disk_needles(self.nonce))
        if found:
            problems.append(f"«{EPHEMERAL}» contiene en disco: {fmt_needles(found)}")
        check(not problems, "\n".join(problems))
        return f'«{EPHEMERAL}»: appendonly no, save "", {REDIS_DATA_DIR} sin ficheros y sin centinelas'

    # --- 11: usuarios que se quedan sin organización (huérfanos) --------------------------------------------
    def slug_of(self, key: str) -> str:
        return self.orph_slugs[key]

    def raw_login(self, email: str) -> Http:
        """`POST /api/v1/auth/login` con la contraseña de la prueba. Con `429` (20 inicios por minuto y por IP) espera
        lo que diga `retry_after_seconds` y repite: esta parte inicia sesión muchas veces."""
        for attempt in range(3):
            res = h3.http("POST", f"{self.args.app_url}/api/v1/auth/login",
                          headers={"X-Requested-With": "baas-webapp"},
                          payload={"email": email, "password": self.args.password},
                          timeout=self.args.timeout)  # fmt: skip
            if res.status != 429 or attempt == 2:
                break
            wait = min(int((res.json() or {}).get("retry_after_seconds") or 30), 65)
            print(f"          (RATE_LIMITED al iniciar sesión como {email}: espero {wait} s)")
            time.sleep(wait + 1)
        return res

    def new_session(self, email: str) -> str:
        res = self.raw_login(email)
        check(res.status == 200, f"inicio de sesión de {email} → {api_error(res)}")
        cookie = h3.cookies_from(res.headers).get("baas_session")
        check(cookie, f"{email}: sin sesión (¿la pila tiene BAAS_DEV_SKIP_TOTP=1?)")
        return str(cookie)

    def login_refused(self, email: str) -> str | None:
        """`None` si la webapp no deja iniciar sesión (con la contraseña correcta); si no, qué ocurrió."""
        res = self.raw_login(email)
        if res.status in (401, 403) and "baas_session" not in h3.cookies_from(res.headers):
            return None
        return f"inicia sesión con la contraseña correcta: {api_error(res)} (se esperaba 401 INVALID_CREDENTIALS)"

    def status_problems(self, email: str, *, orgs: set[str] | None) -> list[str]:
        """`GET /api/v1/me` con la sesión que `email` ya tenía: sigue `ACTIVE` y, con `orgs`, tiene esas organizaciones
        y ninguna más."""
        res = self.webapp("GET", "/api/v1/me", email)
        if res.status != 200:
            return [f"{email}: GET /api/v1/me → {api_error(res)} (se esperaba 200)"]
        data = res.json() or {}
        problems = []
        if data.get("status") != "ACTIVE":
            problems.append(f"{email}: status = {data.get('status')!r} (se esperaba ACTIVE)")
        got = {str(m.get("org_slug")) for m in data.get("memberships") or [] if isinstance(m, dict)}
        if orgs is not None and got != orgs:
            problems.append(f"{email}: organizaciones {sorted(got)} (se esperaba {sorted(orgs)})")
        return problems

    def key_revoked(self, email: str, key_id: str) -> bool | None:
        """¿Está revocada la clave `key_id` en `GET /api/v1/api-keys?scope=mine`? `None` si no aparece."""
        res = self.webapp("GET", "/api/v1/api-keys?scope=mine", email)
        check(res.status == 200, f"GET /api/v1/api-keys ({email}) → {api_error(res)}")
        for item in (res.json() or {}).get("items") or []:
            if isinstance(item, dict) and item.get("id") == key_id:
                return bool(item.get("revoked_at"))
        return None

    def member_invite(
        self, org_key: str, email: str, role: str, *, name: str | None = None, who: str | None = None
    ) -> Http:
        body: dict[str, Any] = {"email": email, "governance_role": role}
        if name:
            body["name"] = name
        return self.admin_post(f"/api/v1/orgs/{self.slug_of(org_key)}/members", body, who=who)

    def invite_person(self, label: str, org_key: str, role: str) -> None:
        """Invita a un usuario nuevo: nace `INVITED` y la respuesta trae su enlace de activación."""
        p = self.orph[label]
        res = self.member_invite(org_key, p.email, role, name=p.name)
        check(res.status == 201, f"invitar a {p.email} a «{self.slug_of(org_key)}» → {api_error(res)}")
        body = res.json() or {}
        member = body.get("member") or {}
        p.user_id, p.link = str(member.get("user_id") or ""), activation_token(body.get("activation_url"))
        check(
            p.user_id and p.link and member.get("status") == "INVITED",
            f"invitar a {p.email}: se esperaba un usuario INVITED con enlace de activación "
            f"(status = {member.get('status')!r}, enlace: {'sí' if p.link else 'no'})",
        )

    def activate_person(self, label: str) -> None:
        """Fija la contraseña con el enlace: con `BAAS_DEV_SKIP_TOTP=1` la webapp responde `200 {"next": "DONE"}`."""
        p = self.orph[label]
        res = h3.http("POST", f"{self.args.app_url}/api/v1/auth/activation/{p.link}/password",
                      headers={"X-Requested-With": "baas-webapp"},
                      payload={"password": self.args.password, "locale": "es"}, timeout=self.args.timeout)  # fmt: skip
        got = (res.json() or {}).get("next")
        check(
            res.status == 200 and got == "DONE",
            f"activar a {p.email} → {api_error(res)} next = {got!r} (esta parte necesita BAAS_DEV_SKIP_TOTP=1)",
        )
        p.link = ""

    def create_key(self, label: str) -> None:
        """La clave personal de `label` (`POST /api/v1/api-keys`); se guarda en `self.keys` y nunca se imprime."""
        p = self.orph[label]
        res = self.webapp("POST", "/api/v1/api-keys", p.email, {"label": f"huerf-{self.nonce}"})
        check(res.status == 201, f"crear la clave de {p.email} → {api_error(res)}")
        body = res.json() or {}
        secret = str(body.get("secret") or "")
        check(secret.startswith("baas_"), f"la clave de {p.email} no tiene la forma esperada")
        p.key_id = str((body.get("key") or {}).get("id") or "")
        check(p.key_id, f"la respuesta de crear la clave de {p.email} no trae el id de la clave")
        self.keys[p.email] = secret

    def handshake_orgs(self, who: str) -> tuple[Http, dict[str, dict[str, Any]]]:
        """El handshake con la clave de `who`: la respuesta y, si fue `200`, sus organizaciones por slug."""
        res = h3.http("POST", f"{self.args.app_url}/api/v1/plugin/handshake",
                      headers={"Authorization": f"Bearer {self.key_of(who)}"}, payload={},
                      timeout=self.args.timeout)  # fmt: skip
        listed = (res.json() or {}).get("organizations") if res.status == 200 else None
        orgs: dict[str, dict[str, Any]] = {}
        for org in listed or []:
            if isinstance(org, dict) and org.get("slug"):
                orgs[str(org["slug"])] = h3.override_proxy(org, self.args.proxy_url) if self.args.proxy_url else org
        return res, orgs

    def chat_probe(self, binding: dict[str, Any], who: str) -> tuple[int, str | None]:
        """Un turno de Chat Completions de `who` por el proxy: (estado, `error.code` del proveedor)."""
        self.probes += 1
        tag = h3.case_tag(self.nonce, "huerfanos", "chat", f"sonda{self.probes}")
        payload = h3.request_body("chat", f"e2e-h4-{self.nonce}-huerfanos-chat-{self.probes}", f"{tag}. Hola.")
        got = self.send(binding, who, "chat", payload)[1]
        return got.status, h3.provider_error(got.body)[1]

    def mcp_whoami(self, binding: dict[str, Any], who: str) -> None:
        with McpClient(str(binding["mcp_url"]), self.key_of(who), timeout=self.args.timeout) as mcp:
            mcp.call_tool("baas_whoami", {})

    def key_serves(self, binding: dict[str, Any], who: str) -> list[str]:
        """La clave de `who` vale en el proxy (`200`) y en el MCP (`baas_whoami`) de `binding`."""
        problems = []
        status, code = self.chat_probe(binding, who)
        if status != 200:
            problems.append(f"el proxy respondió HTTP {status} {code or ''} (se esperaba 200)")
        try:
            self.mcp_whoami(binding, who)
        except Exception as exc:
            problems.append(f"el MCP: {type(exc).__name__}: {exc}")
        return problems

    def key_rejected(self, binding: dict[str, Any], who: str) -> list[str]:
        """La clave de `who` está revocada: el proxy responde `401 baas_invalid_api_key` y el MCP `401`."""
        problems = []
        status, code = self.chat_probe(binding, who)
        if (status, code) != (401, "baas_invalid_api_key"):
            problems.append(f"el proxy respondió HTTP {status} {code or ''} (se esperaba 401 baas_invalid_api_key)")
        try:
            self.mcp_whoami(binding, who)
            problems.append("el MCP sigue aceptando la clave")
        except AuthError:
            pass
        except McpToolError as exc:
            problems.append(f"el MCP respondió {exc.code} (se esperaba 401: clave rechazada)")
        except Exception as exc:
            problems.append(f"el MCP: {type(exc).__name__}: {exc}")
        return problems

    def key_without_org(self, binding: dict[str, Any], who: str) -> list[str]:
        """La clave de `who` sigue vigente pero la organización de `binding` ya no existe: el proxy responde
        `403 baas_access_denied` y el MCP `404` (no `401`)."""
        problems = []
        status, code = self.chat_probe(binding, who)
        if (status, code) != (403, "baas_access_denied"):
            problems.append(f"el proxy respondió HTTP {status} {code or ''} (se esperaba 403 baas_access_denied)")
        try:
            self.mcp_whoami(binding, who)
            problems.append("el MCP sigue sirviendo la organización eliminada")
        except McpToolError as exc:
            if exc.code != "NOT_FOUND":
                problems.append(f"el MCP respondió {exc.code} (se esperaba 404 NOT_FOUND)")
        except AuthError:
            problems.append("el MCP rechazó la clave (401): no estaba revocada")
        except Exception as exc:
            problems.append(f"el MCP: {type(exc).__name__}: {exc}")
        return problems

    def disabled_problems(self, label: str, org_key: str) -> list[str]:
        """Quien se quedó sin organización: no inicia sesión (con su contraseña, que es la correcta), su sesión de antes
        está cerrada y su clave se rechaza en la webapp (handshake), el proxy y el MCP."""
        p, problems = self.orph[label], []
        refused = self.login_refused(p.email)
        if refused:
            problems.append(refused)
        res = self.webapp("GET", "/api/v1/me", p.email)  # la sesión de antes de desactivarlo
        if res.status != 401:
            problems.append(f"su sesión de antes sigue abierta: GET /api/v1/me → {api_error(res)} (se esperaba 401)")
        hs, _ = self.handshake_orgs(p.email)
        if hs.status != 401:
            problems.append(f"el handshake con su clave → {api_error(hs)} (se esperaba 401)")
        binding = self.orph_bindings[(label, org_key)]
        problems += self.poll(lambda: self.key_rejected(binding, p.email))
        return [f"{label}: {m}" for m in problems]

    def delete_with(self, org_key: str, **fields: Any) -> dict[str, Any]:
        """Bloquea la organización y la elimina con `fields` (la reautenticación va aparte): devuelve la respuesta."""
        slug = self.slug_of(org_key)
        res = self.admin_post(f"/api/v1/orgs/{slug}/block", {"blocked": True, "reason": REASON}, method="PUT")
        check(res.status == 200, f"PUT …/block de «{slug}» → {api_error(res)}")
        body = self.delete_body(reason=REASON, confirm_slug=slug, **fields)
        res = self.admin_post(f"/api/v1/orgs/{slug}/delete", body)
        check(
            res.status == 200,
            f"POST …/delete de «{slug}» con {sorted(fields) or 'sin disable_orphans'} → {api_error(res)}",
        )
        answer = res.json() or {}
        check(
            isinstance(answer.get("propagated"), bool),
            f"la respuesta no trae «propagated»: {h3.snippet(res.body, 200)}",
        )
        check(
            is_count(answer.get("disabled_users")),
            f"la respuesta no trae «disabled_users» (un entero): {h3.snippet(res.body, 200)}",
        )
        return answer

    def audit_body(self, action: str) -> Any:
        res = self.webapp("GET", f"/api/v1/audit?action={action}&limit=200", self.args.admin_email)
        check(res.status == 200, f"GET /api/v1/audit?action={action} → {api_error(res)}")
        return res.json()

    def audit_targets(self, action: str) -> set[str]:
        items = (self.audit_body(action) or {}).get("items") or []
        return {str(i.get("target_id")) for i in items if isinstance(i, dict)}

    # 11.1
    def orph_setup(self) -> str:
        a = self.args
        for key, slug in self.orph_slugs.items():
            res = self.admin_post("/api/v1/orgs", {"slug": slug, "name": f"Huérfanos {key.upper()} {self.nonce}"})
            check(res.status == 201, f"crear «{slug}» → {api_error(res)}")
        for label, (org, role) in ORPHAN_PEOPLE.items():
            self.invite_person(label, org, role)
        for label in ORPHAN_ACTIVATED:
            self.activate_person(label)
        b = self.orph["b"]  # B es ORG_ADMIN de O1 y USER de O2: ya activa, la segunda invitación no lleva enlace
        res = self.member_invite("o2", b.email, "USER")
        check(
            res.status == 201 and not (res.json() or {}).get("activation_url"),
            f"invitar a {b.email} a «{self.slug_of('o2')}» → {api_error(res)}",
        )
        res = self.member_invite("o1", a.admin_email, "USER")  # el PLATFORM_ADMIN también es miembro de O1
        check(res.status == 201, f"invitar a {a.admin_email} a «{self.slug_of('o1')}» → {api_error(res)}")
        for label in ORPHAN_ACTIVATED:
            self.sessions[self.orph[label].email] = self.new_session(self.orph[label].email)
        for label in ORPHAN_KEYED:
            self.create_key(label)
        return (
            f"4 organizaciones (huerf-{self.nonce}-o1…o4), 7 usuarios (5 activados con sesión, E y G sin activar) y "
            f"{len(ORPHAN_KEYED)} claves personales; O1: A (USER), B (ORG_ADMIN) y {a.admin_email}; "
            "O2: X (ORG_ADMIN) y B (USER); O3: C y E; O4: F y G"
        )

    # 11.2
    def orph_keys_serve(self) -> str:
        expected = {"a": ["o1"], "b": ["o1", "o2"], "c": ["o3"], "f": ["o4"]}
        for label, orgs in expected.items():
            p = self.orph[label]
            res, got = self.handshake_orgs(p.email)
            check(res.status == 200, f"handshake de {p.email} → {api_error(res)}")
            want = {self.slug_of(o) for o in orgs}
            check(set(got) == want, f"el handshake de {p.email} lista {sorted(got)} (se esperaba {sorted(want)})")
            for org in orgs:
                self.orph_bindings[(label, org)] = got[self.slug_of(org)]
        problems: list[str] = []
        for label, orgs in expected.items():
            for org in orgs:
                binding, who = self.orph_bindings[(label, org)], self.orph[label].email
                found = self.poll(partial(self.key_serves, binding, who))
                problems += [f"{label} en {org}: {m}" for m in found]
        check(not problems, "\n".join(problems))
        return "A (O1), B (O1 y O2), C (O3) y F (O4): handshake, proxy (200) y MCP (baas_whoami) con su clave personal"

    # 11.3
    def orph_preview(self) -> str:
        a, slug, person = self.args, self.slug_of("o1"), self.orph["a"]
        org = self.webapp("GET", f"/api/v1/orgs/{slug}", a.admin_email)
        check(
            org.status == 200 and org_data(org.json()).get("blocked") is False,
            f"GET /api/v1/orgs/{slug} → {api_error(org)}: se esperaba la organización sin bloquear",
        )
        res = self.webapp("GET", f"/api/v1/orgs/{slug}/delete-preview", a.admin_email)
        check(res.status == 200, f"GET …/delete-preview (organización sin bloquear) → {api_error(res)}")
        problems = preview_problems(res.json(), [(person, "ACTIVE", 1)], members=3)
        check(not problems, "\n".join(problems))
        return (
            f"«{slug}» sin bloquear: 3 miembros y un solo candidato, {person.email} (ACTIVE, 1 clave personal); "
            f"no lista a B (tiene O2) ni a {a.admin_email}"
        )

    # 11.4
    def orph_preview_permissions(self) -> str:
        a, slug, listed = self.args, self.slug_of("o1"), self.orph["a"].email
        path = f"/api/v1/orgs/{slug}/delete-preview"
        problems: list[str] = []
        cases = (("a", "USER de la organización", (403,)), ("b", "ORG_ADMIN de la organización", (403,)),
                 ("x", "ORG_ADMIN de otra organización", (403, 404)))  # fmt: skip
        for label, what, allowed in cases:
            res = self.webapp("GET", path, self.orph[label].email)
            if res.status not in allowed:
                problems.append(f"{what}: {api_error(res)} (se esperaba {' o '.join(map(str, allowed))})")
            if listed.encode() in res.body:
                problems.append(f"{what}: la respuesta lista a {listed}")
        anonymous = h3.http("GET", f"{a.app_url}{path}", timeout=a.timeout)
        if anonymous.status != 401:
            problems.append(f"sin sesión: {api_error(anonymous)} (se esperaba 401)")
        missing = self.webapp("GET", f"/api/v1/orgs/{slug}-no-existe/delete-preview", a.admin_email)
        if missing.status != 404:
            problems.append(f"una organización inexistente: {api_error(missing)} (se esperaba 404)")
        problems += self.status_problems(listed, orgs={slug})  # solo lectura: nada cambió
        still = self.webapp("GET", f"/api/v1/orgs/{slug}", a.admin_email)
        if still.status != 200:
            problems.append(f"tras las consultas, GET /api/v1/orgs/{slug} → {api_error(still)}")
        check(not problems, "\n".join(problems))
        return (
            "USER y ORG_ADMIN de la organización → 403, ORG_ADMIN de otra → 403 o 404, sin sesión → 401, "
            "organización inexistente → 404; la vista previa no cambió nada"
        )

    # 11.5
    def orph_delete_explicit(self) -> str:
        slug = self.slug_of("o1")
        answer = self.delete_with("o1", disable_orphans=True)
        check(answer["disabled_users"] == 1, f"disabled_users = {answer['disabled_users']} (se esperaba 1: solo A)")
        gone = self.webapp("GET", f"/api/v1/orgs/{slug}", self.args.admin_email)
        check(gone.status == 404, f"tras eliminar, GET /api/v1/orgs/{slug} → {api_error(gone)} (se esperaba 404)")
        return (
            f"«{slug}» bloqueada y eliminada con disable_orphans: true → 200, disabled_users = 1 "
            f"(propagated: {str(answer['propagated']).lower()})"
        )

    # 11.6
    def orph_a_disabled(self) -> str:
        problems = self.disabled_problems("a", "o1")
        check(not problems, "\n".join(problems))
        return (
            f"{self.orph['a'].email}: no inicia sesión con su contraseña, su sesión de antes está cerrada y su clave "
            "se rechaza (handshake 401, proxy 401 baas_invalid_api_key, MCP 401)"
        )

    # 11.7
    def orph_b_intact(self) -> str:
        a, b, o2 = self.args, self.orph["b"], self.slug_of("o2")
        problems = self.status_problems(b.email, orgs={o2})  # la sesión que ya tenía sigue abierta
        try:
            self.new_session(b.email)
        except StepError as exc:
            problems.append(str(exc))
        if self.key_revoked(b.email, b.key_id) is not False:
            problems.append(f"{b.email}: su clave personal no figura como vigente en GET /api/v1/api-keys")
        res, orgs = self.handshake_orgs(b.email)
        if res.status != 200 or set(orgs) != {o2}:
            problems.append(f"handshake de {b.email}: {api_error(res)}, organizaciones {sorted(orgs)} (solo {o2})")
        problems += self.poll(lambda: self.key_serves(self.orph_bindings[("b", "o2")], b.email))
        for who in (a.admin_email, self.orph["x"].email, self.orph["c"].email, self.orph["f"].email):
            problems += self.status_problems(who, orgs=None)
        check(not problems, "\n".join(problems))
        return (
            f"{b.email} sigue ACTIVE, solo con «{o2}»: su sesión de antes y su contraseña valen y su clave sigue "
            "vigente (handshake, proxy y MCP en O2); el PLATFORM_ADMIN, X, C y F siguen ACTIVE"
        )

    # 11.8
    def orph_audit_disabled(self) -> str:
        a, person, o1 = self.args, self.orph["a"], self.slug_of("o1")
        body = self.audit_body("user.disabled")
        rows = audit_rows(body, "user.disabled", person.user_id)
        check(len(rows) == 1, f"user.disabled de A: {len(rows)} fila(s) (se esperaba 1)")
        problems = disabled_audit_problems(
            rows[0], org_slug=o1, previous_status="ACTIVE", actor_email=a.admin_email, reason=REASON,
            forbidden=[person.email, person.name], min_keys=1,
        )  # fmt: skip
        targets = {str(i.get("target_id")) for i in (body or {}).get("items") or [] if isinstance(i, dict)}
        problems += [f"hay una fila user.disabled de {label}, que conserva su organización"
                     for label in ("b", "x", "c", "f") if self.orph[label].user_id in targets]  # fmt: skip
        check(not problems, "\n".join(problems))
        return (
            f"user.disabled de A: actor {a.admin_email}, motivo de la eliminación, cause org.deleted, org_slug «{o1}», "
            "previous_status ACTIVE, keys_revoked ≥ 1, sin organización y sin su email ni su nombre "
            "(salvo target_label, que la webapp deriva al leer); ninguna fila de B, X, C ni F"
        )

    # 11.9
    def orph_keep(self) -> str:
        c, e, o3 = self.orph["c"], self.orph["e"], self.slug_of("o3")
        answer = self.delete_with("o3", disable_orphans=False)
        check(
            answer["disabled_users"] == 0,
            f"disabled_users = {answer['disabled_users']} (se esperaba 0 con disable_orphans: false)",
        )
        problems = self.status_problems(c.email, orgs=set())  # sigue ACTIVE, ahora sin ninguna organización
        try:
            self.new_session(c.email)
        except StepError as exc:
            problems.append(str(exc))
        if self.key_revoked(c.email, c.key_id) is not False:
            problems.append(f"{c.email}: su clave personal no figura como vigente en GET /api/v1/api-keys")
        res, orgs = self.handshake_orgs(c.email)
        if res.status != 200 or orgs:
            problems.append(f"handshake de {c.email}: {api_error(res)}, organizaciones {sorted(orgs)} (200 y ninguna)")
        problems += self.poll(lambda: self.key_without_org(self.orph_bindings[("c", "o3")], c.email))
        link = h3.http("GET", f"{self.args.app_url}/api/v1/auth/activation/{e.link}", timeout=self.args.timeout)
        if (link.json() or {}).get("status") != "VALID":
            problems.append(f"el enlace de activación de {e.email} (INVITED) ya no vale")
        disabled = self.audit_targets("user.disabled")
        problems += [f"hay una fila user.disabled de {p.label}" for p in (c, e) if p.user_id in disabled]
        check(not problems, "\n".join(problems))
        return (
            f"«{o3}» eliminada con disable_orphans: false → disabled_users = 0: C sigue ACTIVE sin organización "
            "(contraseña y clave vigentes; el proxy responde 403 baas_access_denied y el MCP 404), el enlace de E "
            "sigue valiendo y no hay filas user.disabled de ellos"
        )

    # 11.10
    def orph_default(self) -> str:
        f_, g, o4, app = self.orph["f"], self.orph["g"], self.slug_of("o4"), self.args.app_url
        pre = self.webapp("GET", f"/api/v1/orgs/{o4}/delete-preview", self.args.admin_email)
        check(pre.status == 200, f"GET …/delete-preview de «{o4}» → {api_error(pre)}")
        problems = preview_problems(pre.json(), [(f_, "ACTIVE", 1), (g, "INVITED", 0)], members=2)
        check(not problems, "vista previa de O4 (F ACTIVE con clave, G INVITED):\n" + "\n".join(problems))
        old_link = g.link
        answer = self.delete_with("o4")  # sin «disable_orphans»: vale true
        check(
            answer["disabled_users"] == 2,
            f"disabled_users = {answer['disabled_users']} (se esperaba 2, F y G, sin el campo disable_orphans)",
        )
        problems = self.disabled_problems("f", "o4")
        link = h3.http("GET", f"{app}/api/v1/auth/activation/{old_link}", timeout=self.args.timeout)
        if (link.json() or {}).get("status") == "VALID":
            problems.append(f"g: el enlace de activación sin usar de {g.email} sigue valiendo")
        res = h3.http("POST", f"{app}/api/v1/auth/activation/{old_link}/password",
                      headers={"X-Requested-With": "baas-webapp"},
                      payload={"password": self.args.password, "locale": "es"}, timeout=self.args.timeout)  # fmt: skip
        if res.status == 200:
            problems.append(f"g: se pudo activar la cuenta de {g.email} con el enlace de antes")
        body = self.audit_body("user.disabled")
        for person, previous, min_keys in ((f_, "ACTIVE", 1), (g, "INVITED", 0)):
            rows = audit_rows(body, "user.disabled", person.user_id)
            if len(rows) != 1:
                problems.append(f"{person.label}: {len(rows)} fila(s) user.disabled (se esperaba 1)")
                continue
            found = disabled_audit_problems(
                rows[0], org_slug=o4, previous_status=previous, actor_email=self.args.admin_email, reason=REASON,
                forbidden=[person.email, person.name], min_keys=min_keys,
            )  # fmt: skip
            problems += [f"{person.label}: {m}" for m in found]
        check(not problems, "\n".join(problems))
        return (
            f"«{o4}» eliminada sin el campo disable_orphans → disabled_users = 2: F (ACTIVE) ya no inicia sesión, su "
            "sesión está cerrada y su clave se rechaza; G (INVITED) no puede activar la cuenta con su enlace; una "
            "fila user.disabled de cada uno (previous_status ACTIVE e INVITED), sin email ni nombre"
        )

    # 11.11
    def orph_reactivate_denied(self) -> str:
        a, x, o2 = self.orph["a"], self.orph["x"], self.slug_of("o2")
        res = self.member_invite("o2", a.email, "USER", who=x.email)
        check(
            res.status == 409 and (res.json() or {}).get("error") == "USER_DISABLED",
            f"X (ORG_ADMIN de «{o2}») invita a {a.email}, desactivado → {api_error(res)} "
            "(se esperaba 409 USER_DISABLED)",
        )
        listing = self.webapp("GET", f"/api/v1/orgs/{o2}/members", x.email)
        check(listing.status == 200, f"GET /api/v1/orgs/{o2}/members (X) → {api_error(listing)}")
        emails = {str(m.get("email")) for m in (listing.json() or {}).get("members") or [] if isinstance(m, dict)}
        problems = []
        if a.email in emails:
            problems.append(f"{a.email} figura como miembro de «{o2}»")
        refused = self.login_refused(a.email)
        if refused:
            problems.append(refused)
        check(not problems, "\n".join(problems))
        return f"el ORG_ADMIN de «{o2}» que invita a {a.email} recibe 409 USER_DISABLED: no se añade ni se reactiva"

    # 11.12
    def orph_reactivate_a(self) -> str:
        a, o2, adm = self.orph["a"], self.slug_of("o2"), self.args.admin_email
        old_who, old_key, old_id = a.email + REVOKED_SUFFIX, self.keys[a.email], a.key_id
        res = self.member_invite("o2", a.email, "USER")
        check(
            res.status == 201, f"el PLATFORM_ADMIN invita a {a.email}, desactivado → {api_error(res)} (se esperaba 201)"
        )
        body = res.json() or {}
        member = body.get("member") or {}
        check(
            member.get("user_id") == a.user_id and member.get("status") == "ACTIVE" and not body.get("activation_url"),
            "se esperaba reactivar a A como ACTIVE y sin enlace (tenía contraseña): "
            f"status = {member.get('status')!r}, enlace: {'sí' if body.get('activation_url') else 'no'}",
        )
        self.sessions[a.email] = self.new_session(a.email)  # la contraseña se conserva
        problems = self.status_problems(a.email, orgs={o2})
        if self.key_revoked(a.email, old_id) is not True:
            problems.append("su clave de antes no figura como revocada en GET /api/v1/api-keys")
        self.keys[old_who] = old_key  # la clave revocada, para seguir comprobándola
        self.create_key("a")
        res, orgs = self.handshake_orgs(a.email)
        if res.status != 200 or set(orgs) != {o2}:
            problems.append(f"handshake con la clave nueva: {api_error(res)}, organizaciones {sorted(orgs)}")
        else:
            binding = self.orph_bindings[("a", "o2")] = orgs[o2]  # miembro de O2: la clave de antes solo falla revocada
            problems += [f"clave nueva: {m}" for m in self.poll(lambda: self.key_serves(binding, a.email))]
            problems += [f"clave de antes: {m}" for m in self.poll(lambda: self.key_rejected(binding, old_who))]
        hs, _ = self.handshake_orgs(old_who)
        if hs.status != 401:
            problems.append(f"el handshake con la clave de antes → {api_error(hs)} (se esperaba 401)")
        rows = audit_rows(self.audit_body("user.reactivated"), "user.reactivated", a.user_id)
        if len(rows) != 1:
            problems.append(f"{len(rows)} fila(s) user.reactivated de A (se esperaba 1)")
        else:
            problems += reactivated_audit_problems(rows[0], org_slug=o2, actor_email=adm, new_status="ACTIVE")
        invited = audit_rows(self.audit_body("member.invited"), "member.invited", a.user_id)
        if not [r for r in invited if r.get("org_slug") == o2]:
            problems.append(f"ninguna fila member.invited de A en «{o2}» (la reactivación también da de alta)")
        check(not problems, "\n".join(problems))
        return (
            f"el PLATFORM_ADMIN invita a {a.email} a «{o2}» → 201, ACTIVE y sin enlace; inicia sesión con la "
            "contraseña de siempre; su clave de antes sigue revocada (api-keys, handshake 401, proxy 401, MCP 401) y "
            "la nueva vale; auditoría user.reactivated (en O2, new_status ACTIVE) y member.invited"
        )

    # 11.12b
    def orph_audit_visibility(self) -> str:
        """W10: el ORG_ADMIN de O2 ve `user.reactivated` de A (es de su organización) y ninguna fila `user.disabled`
        (no tienen organización: pertenecen a la plataforma); el PLATFORM_ADMIN ve las dos."""
        a, x, o2, adm = self.orph["a"], self.orph["x"], self.slug_of("o2"), self.args.admin_email

        def seen_by_x(query: str) -> Any:
            res = self.webapp("GET", f"/api/v1/audit?{query}limit=200", x.email)
            check(res.status == 200, f"GET /api/v1/audit?{query.rstrip('&')} (X, ORG_ADMIN de O2) → {api_error(res)}")
            return res.json()

        problems: list[str] = []
        listing = seen_by_x("")
        rows = audit_rows(listing, "user.reactivated", a.user_id)
        if len(rows) != 1:
            problems.append(f"X ve {len(rows)} fila(s) user.reactivated de A en la lista (se esperaba 1)")
        else:
            problems += [
                f"X: {m}"
                for m in reactivated_audit_problems(rows[0], org_slug=o2, actor_email=adm, new_status="ACTIVE")
            ]
        for where, body in (("en la lista", listing), ("filtrando por la acción", seen_by_x("action=user.disabled&"))):
            items = [i for i in (body or {}).get("items") or [] if isinstance(i, dict)]
            disabled = [i for i in items if i.get("action") == "user.disabled"]
            if disabled:
                problems.append(f"X ve {len(disabled)} fila(s) user.disabled {where}: no debe ver ninguna")
        for action in ("user.disabled", "user.reactivated"):  # el PLATFORM_ADMIN ve las dos
            found = len(audit_rows(self.audit_body(action), action, a.user_id))
            if found != 1:
                problems.append(f"el PLATFORM_ADMIN ve {found} fila(s) {action} de A (se esperaba 1)")
        check(not problems, "\n".join(problems))
        return (
            f"el ORG_ADMIN X de «{o2}» ve user.reactivated de A (con org_slug «{o2}») y ninguna fila user.disabled, "
            "ni en la lista ni filtrando por la acción; el PLATFORM_ADMIN ve las dos"
        )

    # 11.13
    def orph_reactivate_g(self) -> str:
        g, o2, app = self.orph["g"], self.slug_of("o2"), self.args.app_url
        old = g.link
        res = self.member_invite("o2", g.email, "USER")
        check(
            res.status == 201,
            f"el PLATFORM_ADMIN invita a {g.email}, desactivado y sin activar → {api_error(res)} (se esperaba 201)",
        )
        body = res.json() or {}
        member, new = body.get("member") or {}, activation_token(body.get("activation_url"))
        check(
            member.get("user_id") == g.user_id and member.get("status") == "INVITED" and new and new != old,
            f"se esperaba reactivar a G como INVITED con un enlace nuevo: status = {member.get('status')!r}, "
            f"enlace: {'nuevo' if new and new != old else 'ninguno o el mismo de antes'}",
        )
        problems = []
        for what, token, valid in (("de antes", old, False), ("nuevo", new, True)):
            res = h3.http("GET", f"{app}/api/v1/auth/activation/{token}", timeout=self.args.timeout)
            if ((res.json() or {}).get("status") == "VALID") is not valid:
                problems.append(f"el enlace {what} {'no vale' if valid else 'vuelve a valer'}")
        check(not problems, "\n".join(problems))
        g.link = new
        self.activate_person("g")
        self.sessions[g.email] = self.new_session(g.email)
        problems = self.status_problems(g.email, orgs={o2})
        rows = audit_rows(self.audit_body("user.reactivated"), "user.reactivated", g.user_id)
        if len(rows) != 1:
            problems.append(f"{len(rows)} fila(s) user.reactivated de G (se esperaba 1)")
        else:
            problems += reactivated_audit_problems(
                rows[0], org_slug=o2, actor_email=self.args.admin_email, new_status="INVITED"
            )
        check(not problems, "\n".join(problems))
        return (
            f"{g.email} (nunca activó la cuenta): el PLATFORM_ADMIN lo reactiva en «{o2}» como INVITED con un enlace "
            "nuevo; el de antes sigue invalidado; con el nuevo activa la cuenta, inicia sesión y queda ACTIVE; "
            "auditoría user.reactivated (en O2, new_status INVITED)"
        )

    # 11.14
    def orph_cleanup(self) -> str:
        o2 = self.slug_of("o2")
        answer = self.delete_with("o2", disable_orphans=True)
        check(
            answer["disabled_users"] == 4, f"disabled_users = {answer['disabled_users']} (se esperaba 4: A, B, X y G)"
        )
        problems = self.disabled_problems("a", "o2")
        check(not problems, "\n".join(problems))
        return (
            f"«{o2}» (la última de la prueba) bloqueada y eliminada → disabled_users = 4 (A reactivado, B, X y G, sin "
            "otra organización); A, desactivado otra vez, ya no inicia sesión y su clave nueva se rechaza. Quedan "
            f"cuentas huerf-{self.nonce}-* sin organización (las cuentas no se eliminan)"
        )

    # --- 13: claves no válidas frenadas por IP a través de Caddy (H4-13, contrato §7.5) -----------------------
    def limit_inspector(self) -> Inspector:
        assert self.inspector is not None  # los pasos del grupo solo se registran con inspector
        return self.inspector

    def refill(self, seconds: float) -> None:
        """Espera a que se llene el cubo de la IP del contenedor (las pruebas adelantan el reloj de la pila falsa)."""
        time.sleep(seconds)

    def proxy_request(self, api: str, key: str, xff: str | None = None) -> CaddyRequest:
        """Un turno de `api` con la clave `key` en la organización sin AI-IP (`--bypass-org`), al proxy por Caddy;
        `xff`, un `X-Forwarded-For` falso."""
        parts = urlsplit(h3.api_url(self.bypass_binding, api))
        headers = {"Content-Type": "application/json", "X-Baas-Api-Key": key}
        if api == "messages":
            headers |= {"x-api-key": h3.FAKE_ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
        else:
            headers["Authorization"] = f"Bearer {h3.FAKE_OPENAI_KEY}"
        if xff:
            headers["X-Forwarded-For"] = xff
        text = f"{h3.case_tag(self.nonce, 'limite', api, 'json')}. Hola."
        body = h3.encode(h3.request_body(api, f"e2e-h4-{self.nonce}-limite-{api}", text))
        return CaddyRequest(parts.hostname or "", parts.path, headers, body.decode())

    def mcp_request(self, key: str, xff: str | None = None) -> CaddyRequest:
        """Un `initialize` con la clave `key` al MCP de la organización sin AI-IP, por Caddy."""
        parts = urlsplit(str(self.bypass_binding["mcp_url"]))
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Accept-Language": "es", "Authorization": f"Bearer {key}"}  # fmt: skip
        if xff:
            headers["X-Forwarded-For"] = xff
        return CaddyRequest(parts.hostname or "", parts.path, headers, json.dumps(MCP_INITIALIZE))

    def limit_burst(
        self, make: Callable[[str, str], CaddyRequest], tail: Callable[[], list[CaddyRequest]]
    ) -> tuple[list[Http], list[Http], float]:
        """Desde el contenedor, `burst + 1` claves desconocidas distintas y `LIMIT_SLACK` de más (por si vuelven fichas
        durante la ráfaga), cada una con un `X-Forwarded-For` falso distinto (`make(clave, ip)`), y después `tail()`.
        Si el `429` llega antes de tiempo (`refill_reason`: el cubo de la IP no estaba lleno), espera a que se llene
        (`burst` fichas a `per_minute`) y la repite una vez. Devuelve las respuestas de la ráfaga, las de `tail()` y lo
        que tardó todo."""
        a = self.args

        def send() -> tuple[list[Http], list[Http], float, str | None]:
            after = tail()
            size = min(a.auth_fail_burst + 1 + LIMIT_SLACK, MAX_CADDY_REQUESTS - len(after))
            burst = [make(unknown_key(), ip) for ip in spoofed_ips(size)]
            elapsed, replies = self.limit_inspector().caddy_requests([*burst, *after])
            head = replies[: len(burst)]
            return head, replies[len(burst) :], elapsed, refill_reason(head, burst=a.auth_fail_burst)

        replies, rest, elapsed, reason = send()
        if reason is not None:
            wait = a.auth_fail_burst * 60 / a.auth_fail_per_minute + LIMIT_MARGIN
            print(f"          ({reason}: espero {wait:g} s a que se llene su cubo y repito)")
            self.refill(wait)
            replies, rest, elapsed, _ = send()
        return replies, rest, elapsed

    def burst_summary(self, replies: Sequence[Http], elapsed: float) -> tuple[int, str]:
        """Las `401` antes del primer `429` y, si pasan de `burst`, cuántas fichas volvieron durante la ráfaga."""
        burst, n = self.args.auth_fail_burst, leading(replies, 401)
        back = f" ({burst} del cubo y {n - burst} que volvieron en {elapsed:.1f} s)" if n > burst else ""
        return n, f"{n} × 401{back}"

    def limit_proxy(self) -> str:
        a = self.args
        replies, rest, elapsed = self.limit_burst(
            lambda key, ip: self.proxy_request("chat", key, ip),
            lambda: [self.proxy_request("messages", unknown_key(), spoofed_ips(1)[0])],
        )
        problems = burst_problems(replies, burst=a.auth_fail_burst, kind="chat", elapsed=elapsed,
                                  per_minute=a.auth_fail_per_minute)  # fmt: skip
        messages, again = rest[-1], ""
        if not problems and token_came_back(
            [*replies, messages], burst=a.auth_fail_burst, elapsed=elapsed, per_minute=a.auth_fail_per_minute
        ):
            # Una ráfaga lenta: la de Messages, la última, se llevó una ficha que acababa de volver. Otra justo
            # después la encuentra gastada.
            _, (messages,) = self.limit_inspector().caddy_requests(
                [self.proxy_request("messages", unknown_key(), spoofed_ips(1)[0])]
            )
            again = " (la primera recibió 401: volvió una ficha durante la ráfaga y se la llevó; la siguiente)"
        problems += [f"Messages, con la IP ya frenada: {p}" for p in limited_problems(messages, "messages")]
        check(not problems, "\n".join([*problems, f"(la ráfaga tardó {elapsed:.2f} s)"]))
        n, rejected = self.burst_summary(replies, elapsed)
        seconds = response_header(replies[n], "Retry-After")
        return (
            f"claves desconocidas, cada una con un X-Forwarded-For distinto → {rejected} baas_invalid_api_key; la "
            f"{n + 1}.ª → 429 baas_auth_rate_limited (Retry-After {seconds} s) y una de Messages → 429 "
            f"rate_limit_error{again} ({elapsed:.2f} s): el remitente no elige su cubo con X-Forwarded-For"
        )

    def limit_known(self) -> str:
        """Desde el equipo, la clave de `--bypass-email` (el proxy la resuelve como válida); desde el contenedor, con su
        IP frenada (comprobado: una desconocida justo antes recibe 429), esa clave sigue pasando."""
        a = self.args
        text = f"{h3.case_tag(self.nonce, 'limite', 'chat', 'equipo')}. Hola."
        res = self.send(self.bypass_binding, a.bypass_email, "chat",
                        h3.request_body("chat", f"e2e-h4-{self.nonce}-limite-chat", text))[1]  # fmt: skip
        check(res.status == 200, f"desde el equipo, la clave de {a.bypass_email}: {reply_text(res)} (se esperaba 200)")
        unknown = [self.proxy_request("chat", unknown_key()) for _ in range(3)]
        _, replies = self.limit_inspector().caddy_requests(
            [*unknown, self.proxy_request("chat", self.key_of(a.bypass_email))]
        )
        check(
            replies[2].status == 429,
            "la IP del contenedor no estaba frenada al enviar la clave válida: tres desconocidas → "
            f"{', '.join(str(r.status) for r in replies[:3])} (se esperaba 429 al menos en la última)",
        )
        known = replies[3]
        check(
            known.status == 200,
            f"la clave ya válida de {a.bypass_email} desde la IP frenada: {reply_text(known)} "
            "(se esperaba 200, no 429)",
        )
        return (
            f"desde el equipo, la clave de {a.bypass_email} → 200; desde el contenedor, frenado (una desconocida → "
            "429), la misma clave → 200"
        )

    def limit_mcp(self) -> str:
        a = self.args
        self.mcp_whoami(self.bypass_binding, a.bypass_email)  # desde el equipo: el MCP ya la resolvió como válida
        key = self.key_of(a.bypass_email)
        replies, rest, elapsed = self.limit_burst(self.mcp_request, lambda: [self.mcp_request(key)])
        problems = burst_problems(replies, burst=a.auth_fail_burst, kind="mcp", elapsed=elapsed,
                                  per_minute=a.auth_fail_per_minute)  # fmt: skip
        if rest[-1].status != 200:
            problems.append(
                f"la clave ya válida de {a.bypass_email} desde la IP frenada: {reply_text(rest[-1])} "
                "(se esperaba 200, no 429)"
            )
        check(not problems, "\n".join([*problems, f"(la ráfaga tardó {elapsed:.2f} s)"]))
        n, rejected = self.burst_summary(replies, elapsed)
        seconds = response_header(replies[n], "Retry-After")
        return (
            f"claves desconocidas, cada una con un X-Forwarded-For distinto → {rejected}; la {n + 1}.ª → 429 "
            f"RATE_LIMITED (Retry-After {seconds} s) ({elapsed:.2f} s); la clave ya válida de {a.bypass_email} → 200 "
            "desde la IP frenada"
        )

    def limit_other_ip(self) -> str:
        """Desde el equipo, otra IP que la del contenedor: una clave desconocida sigue recibiendo 401 (el límite es por
        cliente)."""
        a = self.args
        payload = h3.request_body("chat", f"e2e-h4-{self.nonce}-limite-chat", "Hola.")
        headers = {"X-Baas-Api-Key": unknown_key(), "Authorization": f"Bearer {h3.FAKE_OPENAI_KEY}"}
        proxy = h3.http(
            "POST", h3.api_url(self.bypass_binding, "chat"), headers=headers, payload=payload, timeout=a.timeout
        )
        mcp = h3.http("POST", str(self.bypass_binding["mcp_url"]), payload=MCP_INITIALIZE, timeout=a.timeout,
                      headers={"Authorization": f"Bearer {unknown_key()}",
                               "Accept": "application/json, text/event-stream"})  # fmt: skip
        problems = [f"proxy: {p}" for p in rejected_problems(proxy, "chat")]
        problems += [f"MCP: {p}" for p in rejected_problems(mcp, "mcp")]
        check(not problems, "\n".join(problems) + "\n(desde otra IP que la del contenedor frenado)")
        return "desde el equipo (otra IP): proxy → 401 baas_invalid_api_key y MCP → 401, sin 429"

    # --- el recorrido ------------------------------------------------------------------------------
    def run(self, runner: Runner) -> None:
        a = self.args
        groups = set(a.only)
        step = runner.step
        s0, s1 = "0. webapp accesible", "1. proveedor falso: /_debug/requests"
        s2 = f"2. handshake de {a.email} ({a.org}, AI-IP activo)"
        s2b = f"2b. handshake de {a.bypass_email} ({a.bypass_org}, AI-IP apagado)"
        s3 = f"3. sesiones: {a.admin_email}, {a.email}"
        s4 = f"4. «{a.org}» con el anonimizador apagado"
        step(s0, self.webapp_up)
        step(s1, self.fake_debug)
        present = residue = None
        if "originales" in groups and self.inspector is not None:
            # lo primero de todo, antes de enviar nada: así el residuo de una pila anterior no se confunde con lo que
            # escriban en la persistente los grupos 5 a 9 si el proxy o la webapp siguen usando una sola instancia
            present = f"10.1 la pila tiene {EPHEMERAL} (sin ella los originales siguen en el AOF)"
            step(present, self.has_ephemeral)
            residue = "10.0 la persistente no guarda residuo de una pila anterior a A′ (H4-02)"
            step(residue, self.no_residue, needs=[present])
        s2c = f"2c. handshake de {a.anon_email} ({a.anon_org}, AI-IP activo)"
        need_obs = bool(groups & {"observacion", "interruptor", "originales"})
        if need_obs:
            step(s2, self.handshake_observation, needs=[s0])
        if groups & {"observacion", "originales", "limite"}:
            step(s2b, self.handshake_bypass, needs=[s0])
        if "originales" in groups:
            step(s2c, self.handshake_anon, needs=[s0])
        logins = (
            [a.admin_email, a.email]
            + ([a.bypass_email] if groups & {"observacion", "originales"} else [])
            + ([a.anon_email] if "originales" in groups else [])
            + ([a.orgadmin_email] if groups & {"interruptor", "eliminar"} else [])
        )
        uso = e2e_uso.UsoGroup(self) if "uso" in groups else None
        logins += uso.logins() if uso else []
        consumo = e2e_consumo.ConsumoGroup(self) if "consumo" in groups else None
        logins += consumo.logins() if consumo else []
        ner = e2e_ner.NerGroup(self) if "ner" in groups else None
        logins += ner.logins() if ner else []
        step(s3, lambda: self.login(*logins), needs=[s0])
        cases = [(api, stream) for api in APIS for stream in (False, True)]
        if groups & {"observacion", "interruptor", "originales"}:
            step(s4, self.ensure_off, needs=[s3])
        if "observacion" in groups:
            names = []
            for i, (api, stream) in enumerate(cases, 1):
                if api in a.apis:
                    name = f"5.{i} anonimizador apagado · {API_NAMES[api]} · {h3.mode_name(stream)}"
                    names.append(name)
                    step(name, lambda api=api, stream=stream: self.observe_case(api, stream), needs=[s1, s2, s4])
            step("6. WARNING y la causa en la webapp (como dueño)", self.causes, needs=[s3, *names])
            step(
                "7.1 el PLATFORM_ADMIN no obtiene el contenido por la API",
                self.admin_without_content,
                needs=[s3, *names],
            )
            step(f"7.2 lo mismo con AI-IP apagado («{a.bypass_org}»)", self.admin_without_ai_ip, needs=[s2b, s3])
            step("7.3 el PLATFORM_ADMIN no obtiene el contenido por el WebSocket", self.websocket, needs=[s2, s3, s4])
        if "interruptor" in groups:
            sw = "8.1 permisos y validaciones del interruptor"
            step(sw, self.switch_rules, needs=[s3, s4])
            base = [s1, s2, s3, s4, sw]
            a1 = "8.2 conversaciones A: primer turno con el anonimizador apagado"
            on = "8.3 encender el anonimizador"
            b1 = "8.4 con él encendido: B nueva sale tokenizada"
            a2 = "8.5 con él encendido: A sube a anonimizada en su siguiente turno"
            off = "8.6 apagar el anonimizador"
            b2 = "8.7 con él apagado: B sigue tokenizada y restaurada"
            c1 = "8.8 con él apagado: C nueva sale en claro"
            a3 = "8.9 con él apagado: A sigue anonimizada (no baja)"
            step(a1, lambda: self.initial("A"), needs=base)
            step(on, lambda: self.switch(True), needs=[a1])
            step(b1, lambda: self.initial("B"), needs=[on])
            step(a2, lambda: self.continues("A", then="raise"), needs=[a1, on])
            step(off, lambda: self.switch(False), needs=[on])
            step(b2, lambda: self.continues("B"), needs=[b1, off])
            step(c1, lambda: self.initial("C"), needs=[off])
            step(a3, lambda: self.continues("A", then="stay"), needs=[a2, off])
            sync = "8.10 la webapp: A ✘/WARNING y protegida desde el turno 2, B ✔/CLEAN, C ✘/WARNING"
            step(sync, self.switch_state, needs=[a3, b2, c1])
            step("8.11 el PLATFORM_ADMIN ve por turno: el 1 de A sin contenido, los demás con tokens",
                 self.admin_raised, needs=[sync])  # fmt: skip
            step("8.12 el interruptor vuelve a su valor inicial (apagado)", self.back_to_off, needs=[s3])
        if "eliminar" in groups:
            created = "9.1 crear la organización con datos"
            step(created, self.create_org, needs=[s0, s1, s3])
            if self.inspector is not None:
                step(
                    "9.1b el vault tiene entradas de la organización antes de eliminar",
                    self.vault_before,
                    needs=[created],
                )
            rules = "9.2 condiciones de la eliminación"
            step(rules, self.delete_rules, needs=[created])
            blocked = "9.3 bloquear la organización"
            step(blocked, self.block_org, needs=[rules])
            deleted = "9.4 eliminar con motivo y confirmación del slug"
            step(deleted, self.delete_org, needs=[blocked])
            step("9.5 la organización ya no existe (webapp, proxy, MCP, handshake)", self.gone, needs=[deleted])
            step("9.6 el slug no se puede reutilizar", self.slug_not_reusable, needs=[deleted])
            step("9.7 la auditoría conserva org.deleted", self.audit_survives, needs=[deleted])
            if self.inspector is not None:
                step("9.8 vault vacío, Redis sin claves y MCP en cuarentena", self.inside, needs=[deleted])
        if "originales" in groups and present is not None and residue is not None:
            sent = "10.2 turnos con valores centinela: bypass, observación y AI-IP con anonimizador"
            step(sent, self.send_originals, needs=[present, s2, s2b, s2c, s4])
            consumed = "10.3 la webapp consume las trazas (las conversaciones aparecen)"
            step(consumed, self.originals_consumed, needs=[sent, s3])
            step("10.4 AOF de la persistente sin originales, tal como está", self.aof_live_clean,
                 needs=[consumed, residue])  # fmt: skip
            control = "10.5 control positivo del escáner (valor propio, reescritura y limpieza)"
            step(control, self.scanner_control, needs=[present])
            step("10.6 AOF de la persistente sin originales tras forzar la reescritura", self.aof_rewritten_clean,
                 needs=[consumed, control, residue])  # fmt: skip
            step("10.7 la efímera no persiste nada: sin AOF, sin RDB y sin ficheros", self.ephemeral_clean,
                 needs=[consumed, control])  # fmt: skip
        if "huerfanos" in groups:
            base = [s0, s1, s3]
            setup = "11.1 crear 4 organizaciones y 7 usuarios (invitar, activar, claves personales)"
            serve = "11.2 las claves personales valen (handshake, proxy y MCP)"
            preview = "11.3 vista previa: lista a quien se quedaría sin organización y a nadie más"
            perms = "11.4 vista previa: solo el PLATFORM_ADMIN (y solo lectura)"
            explicit = "11.5 eliminar con disable_orphans: true desactiva solo a quien no tiene otra organización"
            disabled = "11.6 el usuario desactivado: sin inicio de sesión, sesión cerrada y clave rechazada"
            intact = "11.7 quien conserva otra organización no cambia"
            audit = "11.8 auditoría user.disabled: causa y claves revocadas, sin email ni nombre"
            keep = "11.9 disable_orphans: false no desactiva a nadie"
            default = "11.10 sin el campo disable_orphans se desactiva (el valor por omisión es true)"
            denied = "11.11 un ORG_ADMIN no reactiva a un desactivado: 409 USER_DISABLED"
            react_a = "11.12 el PLATFORM_ADMIN lo reactiva: contraseña, clave antigua revocada y user.reactivated"
            visible = "11.12b auditoría (W10): el ORG_ADMIN ve user.reactivated y no user.disabled"
            react_g = "11.13 un invitado sin activar vuelve a INVITED con un enlace nuevo"
            step(setup, self.orph_setup, needs=base)
            step(serve, self.orph_keys_serve, needs=[setup])
            step(preview, self.orph_preview, needs=[serve])
            step(perms, self.orph_preview_permissions, needs=[serve])
            step(explicit, self.orph_delete_explicit, needs=[preview, perms])
            step(disabled, self.orph_a_disabled, needs=[explicit])
            step(intact, self.orph_b_intact, needs=[explicit])
            step(audit, self.orph_audit_disabled, needs=[explicit])
            step(keep, self.orph_keep, needs=[serve, intact])
            step(default, self.orph_default, needs=[serve, intact])
            step(denied, self.orph_reactivate_denied, needs=[disabled])
            step(react_a, self.orph_reactivate_a, needs=[denied, audit])
            step(visible, self.orph_audit_visibility, needs=[react_a])
            step(react_g, self.orph_reactivate_g, needs=[default, react_a])
            step("11.14 eliminar la última organización de la prueba: 4 usuarios desactivados",
                 self.orph_cleanup, needs=[react_a, visible, react_g, keep])  # fmt: skip
        if uso is not None:
            uso.register(runner, [s0, s1, s3])
        if consumo is not None:
            consumo.register(runner, [s0, s1, s3])
        if "acl" in groups and self.inspector is not None:
            e2e_acl.register(runner, self, [s0])
        if "limite" in groups and self.inspector is not None:
            burst = a.auth_fail_burst
            l1 = (f"14.1 proxy: {burst} claves desconocidas distintas desde una IP → 401 y la {burst + 1}.ª 429 "
                  "(con un X-Forwarded-For falso distinto en cada una)")  # fmt: skip
            step(l1, self.limit_proxy, needs=[s0, s2b])
            step("14.2 proxy: una clave ya válida pasa desde la IP frenada", self.limit_known, needs=[l1])
            step(f"14.3 MCP: {burst} claves desconocidas distintas → 401 y la {burst + 1}.ª 429 RATE_LIMITED; una ya "
                 "válida pasa", self.limit_mcp, needs=[s0, s2b])  # fmt: skip
            step("14.4 desde el equipo (otra IP), una clave desconocida → 401 en el proxy y en el MCP",
                 self.limit_other_ip, needs=[s2b])  # fmt: skip
        if ner is not None:
            ner.register(runner, [s0, s1, s3])


def main(argv: list[str] | None = None, inspector: Inspector | None = None) -> int:
    args = parse_args(argv)
    runner = Runner()
    wants_containers = bool({"eliminar", "originales", "uso", "acl", "limite", "ner"} & set(args.only))
    inspector = inspector if inspector is not None else build_inspector(args) if wants_containers else None
    e2e = E2EH4(args, inspector)
    print(f"Prueba de punta a punta H4 · webapp {args.app_url} · proveedor falso {args.fake_url}"
          f" · organización {args.org} · ejecución {e2e.nonce}")  # fmt: skip
    if "originales" in args.only and inspector is None:
        print("NOTA: el grupo «originales» mira dentro de los contenedores (--inspect podman|docker): se omite")
    if "uso" in args.only and inspector is None:
        print("NOTA: sin --inspect, el grupo «uso» omite 12.4, 12.5, 12.7, 12.8 y 12.9 (miran o paran contenedores)")
    if "acl" in args.only and inspector is None:
        print("NOTA: el grupo «acl» entra en Redis con las credenciales de cada servicio (--inspect): se omite")
    if "limite" in args.only and inspector is None:
        print(f"NOTA: el grupo «limite» envía desde el contenedor {LIMIT_SOURCE} (--inspect podman|docker): se omite")
    if "ner" in args.only and inspector is None:
        print("NOTA: sin --inspect, el grupo «ner» omite 15.4 y 15.5 (vacían la caché de la efímera y paran el NER)")
    e2e.run(runner)
    report = {**runner.report(), "app_url": args.app_url, "fake_url": args.fake_url, "nonce": e2e.nonce,
              "only": args.only, "apis": args.apis,
              "inspect": args.inspect if inspector is not None else "none"}  # fmt: skip
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    failed = [r.name for r in runner.results if r.status != "OK"]
    print("RESULTADO:", "todo OK" if not failed else f"{len(failed)} paso(s) sin éxito: {', '.join(failed)}")
    return runner.exit_code()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
    sys.exit(130)
