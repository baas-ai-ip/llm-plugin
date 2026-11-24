"""Grupo «consumo» (pasos 16.0–16.8) de `dev/e2e_h4.py`: el consumo de la plataforma por organización (Fase 2) contra
la pila real.

Un paso por criterio de aceptación del contrato (`contrato-fase-2-consumo.md`, §9):

  16.0  la pila tiene el consumo: `GET /api/v1/platform/usage?month=<mes en curso UTC>` como PLATFORM_ADMIN da `200` con
        `period` y `data_until_by_source`. Sin ellos (la pila no incluye T2–T3) falla con ese mensaje y los demás
        pasos quedan saltados.
  16.1  turnos por modo: turnos reales por el proxy en *bypass* (AI-IP apagado), observación (AI-IP sin anonimizador) y
        anonimizado; el informe de la organización cuenta exactamente los enviados por modo, `anonymizer.turns` es
        `requests`, y las sustituciones y la verificación de L1 cuadran con lo que la webapp dice de cada turno.
  16.2  disco: con una foto nueva, `disk.last` es lo de las cinco categorías facturables de la foto de W15 (sin el
        índice), `disk.index_last` es su índice aparte y `disk.last + disk.index_last` es su `total_bytes`; con otra
        foto tras crecer, la media recoge las anteriores y el máximo es la última.
  16.3  el CSV es el JSON: una fila por organización, sin totales, las 46 columnas del contrato §7.9 y los mismos
        valores (un nombre que empieza por `=` sale con apóstrofo), para un rango y para un mes.
  16.4  permisos: `403` a un ORG_ADMIN y a un USER (y a un USER de otra organización) en `GET /platform/usage` y en las
        cuatro rutas globales (`/platform/usage/{traffic,content,storage,anonymizer}`), en JSON y en CSV, también con
        parámetros no válidos (los permisos van antes que la validación); `401` sin sesión.
  16.5  global = suma de las organizaciones: con dos organizaciones de zona UTC y un número distinto de peticiones, cada
        fila del tráfico global (`group_by=org`) es el informe de tráfico de su organización (y su fila de
        `GET /platform/usage`), los totales son la suma, y `day` y `org_day` dicen los mismos totales.
  16.6  el anonimizador de una organización es el de plataforma para ella (`totals` = su bloque de `/platform/usage` y
        su fila de `/platform/usage/anonymizer`), con sus días y sus horas sumando el total, y los permisos del §7.2:
        `200` el ORG_ADMIN y el PLATFORM_ADMIN, `403` el USER, `404` el ORG_ADMIN de otra, `401` sin sesión.
  16.7  sin `usage.viewed`: tras las consultas globales y las del anonimizador (también en una organización donde el
        PLATFORM_ADMIN solo es USER), la auditoría no tiene ninguna fila nueva; un control positivo (el detalle por
        persona sí deja la fila) prueba que la auditoría la mostraría.
  16.8  L2 con el NER: con la pila del NER, las columnas `l2_*` suman lo que dice cada turno (`verification.l2`) en
        plataforma y en la organización. Saltarlo depende de una **capacidad explícita**: sin `--ner` (ni
        `BAAS_E2E_NER=1`) y sin ningún turno con `verification.l2`, el paso se **salta** con un mensaje (`SALTADO`, sin
        contar como fallo); **con** `--ner`, que no llegue ningún `l2` es un **fallo** (el NER está declarado: un
        `l2` que dejara de emitirse sería una regresión, no una pila sin NER).

Cada paso con datos crea su organización (`consumo-<ejecución>-<paso>`, con usuarios de la semilla de desarrollo como
miembros; las de 16.5, 16.6 y 16.8 con zona UTC, para que el informe de plataforma, en UTC, y el de la organización
hablen de los mismos días) y la elimina al terminar: se reutilizan los ayudantes del grupo `uso` (`e2e_uso.UsoGroup`).
Ningún paso mira dentro de los contenedores (`--inspect none` basta).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import e2e_h3 as h3
import e2e_uso
from e2e_h2 import Http, StepError, check

if TYPE_CHECKING:
    from e2e_h2 import Runner
    from e2e_h4 import E2EH4

MODES = ("ANONYMIZED", "OBSERVATION", "BYPASS")
BILLABLE = ("skills", "scripts", "schemas", "knowledge", "versions")  # lo que se factura: sin el índice (contrato §6.2)
VERIFIED = ("clean", "warning", "error")
NEWER_SNAPSHOT = "no son de la misma foto"
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
REASON = "Prueba de punta a punta del grupo consumo (F2)"
PLATFORM = "/api/v1/platform/usage"
GLOBAL_KINDS = ("traffic", "content", "storage", "anonymizer")  # las cuatro rutas globales (contrato §7.4–§7.7)
# 16.5: las cifras de tráfico que el informe global (una fila por organización) y el de la propia organización dicen
# igual (contrato §6.5), y las que se suman: sin los percentiles, que no se suman, y con los usuarios activos
TRAFFIC_COMPARED = ("requests", "blocked", "errors", "tokens_in", "tokens_out", "bytes_in", "bytes_out",
                    "latency_p50_ms", "latency_p95_ms", "ttft_avg_ms")  # fmt: skip
TRAFFIC_ADDED = (*TRAFFIC_COMPARED[:7], "active_users")
ORG_DAY_TOP = 10  # el tope de organizaciones de `org_day` (contrato §7.4, §11.14)
# 16.6 y 16.8: el bloque del anonimizador (contrato §6.5) sin `turns_by_mode`, que va por modo
L2_COLUMNS = ("l2_turns", "l2_enabled", "l2_applied", "l2_degraded", "l2_observed_turns", "l2_known_values_only",
              "l2_analyzed_bytes", "l2_cached_bytes", "l2_unanalyzed_bytes", "l2_observed")  # fmt: skip
ANONYMIZER_COLUMNS = ("turns", "tokens_in", "tokens_out", "bytes_in", "replaced_in", "restored_out", "verified_clean",
                      "verified_warning", "verified_error", *L2_COLUMNS)  # fmt: skip
L2_STATUS = {"APPLIED": "l2_applied", "DEGRADED": "l2_degraded", "OBSERVED": "l2_observed_turns",
             "KNOWN_VALUES_ONLY": "l2_known_values_only"}  # fmt: skip
L2_BYTES = {"l2_analyzed_bytes": "analyzed_bytes", "l2_cached_bytes": "cached_bytes",
            "l2_unanalyzed_bytes": "unanalyzed_bytes", "l2_observed": "observed"}  # fmt: skip
# Texto que un NER reconoce (un nombre, una empresa y una ciudad), con los valores de L1 para que el turno tenga de todo
L2_TEXT = "Hola, soy Ana Gómez, trabajo en Telefónica y vivo en Valencia."
VIEWED = "usage.viewed"
CSV_SETTLE = 10.0  # s que se espera a que el JSON y el CSV (dos peticiones) hablen del mismo instante
# Contrato §7.9, `GET /platform/usage`: columnas fijas, en este orden
PLATFORM_CSV_COLUMNS = (
    "slug", "name", "timezone", "requests", "blocked_requests", "errors", "tokens_in", "tokens_out", "bytes_in",
    "bytes_out", "active_users", "invocations", "storage_bytes", "storage_taken_at", "synced", "skill_read",
    "script_downloaded", "knowledge_read", "knowledge_search", "skills", "skills_capped", "areas", "areas_capped",
    "disk_avg_bytes", "disk_max_bytes", "disk_index_last_bytes", "disk_snapshots", "disk_first_taken_at",
    "anonymized_turns", "observation_turns", "bypass_turns", "replaced_in", "restored_out", "verified_clean",
    "verified_warning", "verified_error", "l2_turns", "l2_enabled", "l2_applied", "l2_degraded",
    "l2_observed_turns", "l2_known_values_only", "l2_analyzed_bytes", "l2_cached_bytes", "l2_unanalyzed_bytes",
    "l2_observed",
)  # fmt: skip
_CSV_CONTENT = ("synced", "skill_read", "script_downloaded", "knowledge_read", "knowledge_search", "skills",
                "skills_capped", "areas", "areas_capped")  # fmt: skip
_CSV_DISK = {"disk_avg_bytes": "avg", "disk_max_bytes": "max", "disk_index_last_bytes": "index_last",
             "disk_snapshots": "snapshots", "disk_first_taken_at": "first_taken_at"}  # fmt: skip
_CSV_MODES = {"anonymized_turns": "ANONYMIZED", "observation_turns": "OBSERVATION", "bypass_turns": "BYPASS"}
_CSV_TOP = ("slug", "name", "timezone", "requests", "blocked_requests", "errors", "tokens_in", "tokens_out",
            "bytes_in", "bytes_out", "active_users", "invocations", "storage_bytes", "storage_taken_at")  # fmt: skip


# --- utilidades puras ----------------------------------------------------------------------------


def current_month(now: datetime | None = None) -> str:
    """El mes natural en curso en UTC (`YYYY-MM`), el valor por defecto de `GET /platform/usage` (contrato §6.1)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def utc_range(now: datetime | None = None) -> dict[str, str]:
    """`from`/`to` UTC que cubren desde **ayer** hasta hoy: un dato de las 23:59:5x consultado a las 00:00:0x sigue
    dentro (las organizaciones de cada paso se crean al empezar, sin histórico)."""
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    return {"from": (today - timedelta(days=1)).isoformat(), "to": today.isoformat()}


def mode_of(ai_ip: Any, anonymizer: Any) -> str:
    """El modo con el que sale un turno (contrato §5.1): sin AI-IP, bypass, tenga lo que tenga el anonimizador."""
    if not ai_ip:
        return "BYPASS"
    return "ANONYMIZED" if anonymizer else "OBSERVATION"


def replacements_total(by_type: Any) -> int:
    """Suma de los valores enteros de un `*_replacements_by_type` (contrato §5.3); lo que no es un entero no cuenta."""
    if not isinstance(by_type, dict):
        return 0
    return sum(v for v in by_type.values() if isinstance(v, int) and not isinstance(v, bool))


def consumo_problem(status: int, data: Any) -> str | None:
    """16.0: qué decir si `GET /platform/usage?month=…` (como PLATFORM_ADMIN) no trae el informe de consumo."""
    if status == 200:
        missing = [
            k
            for k in ("period", "data_until_by_source")
            if not (isinstance(data, dict) and isinstance(data.get(k), dict))
        ]
        if not missing:
            return None
        return (
            "pila sin el informe de consumo de la Fase 2 (el informe trae `period` y `data_until_by_source`; "
            f"esta respuesta no trae {', '.join(missing)}): reconstruye con la webapp de T2–T3 (agregado del "
            "anonimizador e informe de consumo), el mcp-server de T4 y deploy"
        )
    return f"GET …/platform/usage → HTTP {status} (se esperaba 200): ¿la pila tiene el consumo de la Fase 2 (T2–T3)?"


@dataclass
class Sent:
    """Un turno de 16.1: el modo que el paso pidió, los tokens que el proveedor declaró y, tras consumirse la traza, lo
    que la webapp dice del turno (el modo con el que salió y su verificación)."""

    mode: str
    tokens_in: int
    tokens_out: int
    state: str | None = None
    verification: Any = None
    model: str = ""


def expected_figures(sent: Sequence[Sent]) -> dict[str, Any]:
    """Lo que el informe de la organización debe decir de esos turnos (contrato §5.2): turnos por modo, tokens, las
    sustituciones de L1 (ida y vuelta) y cuántos turnos salieron `CLEAN`, `WARNING` y `ERROR`."""
    verified = dict.fromkeys(VERIFIED, 0)
    replaced = restored = 0
    for turn in sent:
        if not isinstance(turn.verification, dict):
            continue
        replaced += replacements_total(turn.verification.get("request_replacements_by_type"))
        restored += replacements_total(turn.verification.get("response_replacements_by_type"))
        status = str(turn.verification.get("status") or "").lower()
        if status in verified:
            verified[status] += 1
    return {
        "requests": len(sent),
        "turns_by_mode": {m: sum(1 for t in sent if t.mode == m) for m in MODES},
        "tokens_in": sum(t.tokens_in for t in sent),
        "tokens_out": sum(t.tokens_out for t in sent),
        "replaced_in": replaced,
        "restored_out": restored,
        "verified": verified,
    }


def state_problems(sent: Sequence[Sent]) -> list[str]:
    """Precondición de 16.1: cada turno salió en el modo que se pidió (si no, el interruptor no llegó al proxy y la
    comparación con el informe no diría nada del consumo)."""
    return [
        f"el turno {i} ({t.model}) pidió {t.mode} y la webapp dice que salió {t.state}: "
        "el interruptor no llegó al proxy"
        for i, t in enumerate(sent, 1)
        if t.state != t.mode
    ]


def mode_problems(row: dict[str, Any], want: dict[str, Any]) -> list[str]:
    """Diferencias entre la fila de la organización en `GET /platform/usage` y `want` (`expected_figures`), más las
    invariantes del contrato §6.5: `turns = Σ turns_by_mode = requests`, el anonimizador cuelga del tráfico (mismos
    tokens y bytes) y los verificados no superan a los turnos."""
    problems: list[str] = []
    anon = row.get("anonymizer") if isinstance(row.get("anonymizer"), dict) else {}

    def same(name: str, got: Any, expected: Any) -> None:
        if got != expected or type(got) is not type(expected):
            problems.append(f"{name} = {got!r} (se esperaba {expected!r})")

    for name in ("requests", "tokens_in", "tokens_out"):
        same(name, row.get(name), want[name])
    modes = anon.get("turns_by_mode")
    if not isinstance(modes, dict) or set(modes) != set(MODES):
        problems.append(f"anonymizer.turns_by_mode no trae siempre {', '.join(MODES)}: {modes!r}")
        modes = {}
    for mode in MODES:
        if mode in modes:
            same(f"anonymizer.turns_by_mode.{mode}", modes[mode], want["turns_by_mode"][mode])
    for name in ("replaced_in", "restored_out"):
        same(f"anonymizer.{name}", anon.get(name), want[name])
    for status in VERIFIED:
        same(f"anonymizer.verified_{status}", anon.get(f"verified_{status}"), want["verified"][status])
    turns = anon.get("turns")
    if modes and turns != sum(modes.values()):
        problems.append(f"anonymizer.turns = {turns!r} ≠ Σ turns_by_mode = {sum(modes.values())}")
    if turns != row.get("requests"):
        problems.append(f"anonymizer.turns = {turns!r} ≠ requests = {row.get('requests')!r}")
    for name in ("tokens_in", "tokens_out", "bytes_in"):
        if anon.get(name) != row.get(name):
            problems.append(f"anonymizer.{name} = {anon.get(name)!r} ≠ {name} = {row.get(name)!r} (el mismo tráfico)")
    verified = sum(anon.get(f"verified_{s}") or 0 for s in VERIFIED)
    if isinstance(turns, int) and verified > turns:
        problems.append(
            f"se esperaba verified_clean + verified_warning + verified_error ≤ turns ({verified} > {turns})"
        )
    return problems


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def billable_problems(row: dict[str, Any], latest: Any) -> list[str]:
    """16.2: el bloque `disk` de la fila de `GET /platform/usage` frente a `latest` del informe de disco de la
    organización (Fase 4 §8.5), que son las **dos** lecturas de la misma foto: `disk.last` es la suma de las cinco
    categorías facturables (sin el índice), `disk.index_last` es el índice y los dos suman `latest.total_bytes`. Si las
    dos lecturas no son de la misma foto, lo dice con `NEWER_SNAPSHOT` (una foto nueva llegó entre las dos: se vuelve
    a leer; no es un valor equivocado)."""
    disk = row.get("disk")
    if not isinstance(disk, dict) or not isinstance(latest, dict) or not isinstance(latest.get("bytes"), dict):
        return [f"sin bloque disk en el informe o sin latest.bytes en el de la organización: {disk!r}, {latest!r}"]
    problems = [
        f"disk.{key} = {disk.get(key)!r} (se esperaba un entero)"
        for key in ("avg", "max", "last", "index_last", "snapshots")
        if not _int(disk.get(key))
    ]
    if problems:
        return problems
    if disk.get("taken_at") != latest.get("taken_at"):
        return [
            f"las dos lecturas {NEWER_SNAPSHOT}: disk.taken_at = {disk.get('taken_at')}, "
            f"latest.taken_at = {latest.get('taken_at')}"
        ]
    data = latest["bytes"]
    billable = sum(data.get(c) or 0 for c in BILLABLE)
    if disk["snapshots"] < 1:
        problems.append(f"disk.snapshots = {disk['snapshots']} (la foto de latest cuenta: se esperaba ≥ 1)")
    if disk["last"] != billable:
        problems.append(
            f"disk.last = {disk['last']}, las cinco categorías de latest.bytes (sin el índice) suman {billable}"
        )
    if disk["index_last"] != data.get("index"):
        problems.append(f"disk.index_last = {disk['index_last']}, latest.bytes.index = {data.get('index')}")
    if disk["last"] + disk["index_last"] != latest.get("total_bytes"):
        problems.append(
            f"disk.last + disk.index_last = {disk['last'] + disk['index_last']}, "
            f"latest.total_bytes = {latest.get('total_bytes')} (difieren exactamente en el índice)"
        )
    if disk["avg"] > disk["max"]:
        problems.append(f"disk.avg = {disk['avg']} > disk.max = {disk['max']}")
    if disk["last"] > disk["max"]:
        problems.append(f"disk.last = {disk['last']} > disk.max = {disk['max']}")
    if row.get("storage_bytes") != disk["last"]:
        problems.append(
            f"storage_bytes = {row.get('storage_bytes')!r} "
            f"(se esperaba disk.last = {disk['last']}: ya no suma el índice)"
        )
    if row.get("storage_taken_at") != disk.get("taken_at"):
        problems.append(f"storage_taken_at = {row.get('storage_taken_at')!r} (se esperaba disk.taken_at)")
    first = disk.get("first_taken_at")
    if not isinstance(first, str) or not isinstance(disk.get("taken_at"), str) or first > disk["taken_at"]:
        problems.append(f"disk.first_taken_at = {first!r} (se esperaba un instante no posterior a disk.taken_at)")
    return problems


def published_problems(row: dict[str, Any], *, skills: int, areas: int) -> list[str]:
    """16.2: las skills y las áreas publicadas de la última foto (contrato §6.3): las que se escribieron, sin tope."""
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    problems = []
    for name, want in (("skills", skills), ("areas", areas)):
        if content.get(name) != want:
            problems.append(f"content.{name} = {content.get(name)!r} (se esperaba {want})")
        if content.get(f"{name}_capped") is not False:
            problems.append(f"content.{name}_capped = {content.get(f'{name}_capped')!r} (se esperaba false)")
    return problems


def growth_problems(row: dict[str, Any], *, first: int) -> list[str]:
    """16.2: con una segunda foto mayor que la primera (`first` bytes facturables), la última es el máximo y la media
    recoge las fotos de antes (es menor que el máximo)."""
    disk = row.get("disk") if isinstance(row.get("disk"), dict) else {}
    problems = []
    if not _int(disk.get("snapshots")) or disk["snapshots"] < 2:
        problems.append(f"disk.snapshots = {disk.get('snapshots')!r} (con dos fotos se esperaba ≥ 2)")
    if disk.get("max") != disk.get("last"):
        problems.append(f"disk.max = {disk.get('max')!r} ≠ disk.last = {disk.get('last')!r} (el disco solo creció)")
    if _int(disk.get("avg")) and _int(disk.get("max")) and disk["avg"] >= disk["max"]:
        problems.append(f"disk.avg = {disk['avg']} ≥ disk.max = {disk['max']}: la media no recoge las fotos de antes")
    if _int(disk.get("last")) and disk["last"] <= first:
        problems.append(f"disk.last = {disk['last']} no supera los {first} B de la primera foto")
    return problems


def csv_cell(value: Any) -> str:
    """Una celda del CSV (contrato §7.9 y Fase 4 §8.8): `null` vacío, los booleanos en minúsculas y, en un texto que
    empieza por `=`, `+`, `-`, `@`, tabulador o retorno, un apóstrofo delante (no se interpreta como fórmula)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return str(value)


def csv_expected(org: dict[str, Any]) -> list[str]:
    """La fila del CSV de una organización del JSON, en el orden de `PLATFORM_CSV_COLUMNS`."""
    content, disk, anon = org.get("content") or {}, org.get("disk") or {}, org.get("anonymizer") or {}
    cells = []
    for column in PLATFORM_CSV_COLUMNS:
        if column in _CSV_TOP:
            value = org.get(column)
        elif column in _CSV_CONTENT:
            value = content.get(column)
        elif column in _CSV_DISK:
            value = disk.get(_CSV_DISK[column])
        elif column in _CSV_MODES:
            value = (anon.get("turns_by_mode") or {}).get(_CSV_MODES[column])
        else:
            value = anon.get(column)
        cells.append(csv_cell(value))
    return cells


def csv_problems(text: str, orgs: Sequence[dict[str, Any]]) -> list[str]:
    """16.3: el CSV frente a las organizaciones del JSON: BOM y `\\r\\n`, la cabecera exacta, una fila por organización
    (en su orden) sin totales y cada celda igual al valor del JSON."""
    problems = []
    if not text.startswith("﻿"):
        problems.append("el CSV no empieza por el BOM (U+FEFF)")
    body = text.removeprefix("﻿")
    if "\n" in body.replace("\r\n", ""):
        problems.append("el CSV tiene saltos de línea que no son \\r\\n")
    rows = list(csv.reader(io.StringIO(body, newline="")))
    columns = list(PLATFORM_CSV_COLUMNS)
    if not rows or rows[0] != columns:
        got = rows[0] if rows else []
        extra, missing = [c for c in got if c not in columns], [c for c in columns if c not in got]
        problems.append(
            f"cabecera distinta de la del contrato §7.9 ({len(got)} columnas; sobran {extra}, faltan {missing})"
        )
        return problems
    data = rows[1:]
    if len(data) != len(orgs):
        problems.append(
            f"{len(data)} fila(s) para {len(orgs)} organización(es): una por organización y sin fila de totales "
            f"(slugs del CSV: {[r[0] for r in data][:8]})"
        )
    for got, org in zip(data, orgs, strict=False):
        want = csv_expected(org)
        if len(got) != len(columns):
            problems.append(f"«{org.get('slug')}»: {len(got)} celdas (se esperaban {len(columns)})")
            continue
        problems += [
            f"«{org.get('slug')}»: {column} = {cell!r} en el CSV y {expected!r} en el JSON"
            for column, cell, expected in zip(columns, got, want, strict=True)
            if cell != expected
        ]
    return problems


def csv_filename(period: dict[str, str]) -> str:
    return f"consumo-plataforma-{period['from']}-{period['to']}.csv"


def disposition_problems(header: str | None, filename: str) -> list[str]:
    if header != f'attachment; filename="{filename}"':
        return [f'Content-Disposition = {header!r} (se esperaba attachment; filename="{filename}")']
    return []


def denied_problems(status: int, data: Any, want: int) -> list[str]:
    """16.4 y 16.6: la respuesta es `want` (`401 UNAUTHENTICATED`, `403 FORBIDDEN` o `404 NOT_FOUND`) y no lleva ningún
    dato del informe (ni `orgs`, `totals`, `period` ni `series`)."""
    error = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND"}[want]
    leaked = {"orgs", "totals", "period", "series"}
    problems = []
    if status != want:
        problems.append(f"HTTP {status} (se esperaba {want})")
    elif (data.get("error") if isinstance(data, dict) else None) != error:
        problems.append(f"error = {data.get('error') if isinstance(data, dict) else data!r} (se esperaba {error})")
    if isinstance(data, dict) and leaked & data.keys():
        problems.append(f"la respuesta lleva datos del informe ({sorted(leaked & data.keys())})")
    return problems


def platform_routes() -> list[str]:
    """Las cinco rutas de plataforma que solo ve el PLATFORM_ADMIN: el informe de consumo y las cuatro globales."""
    return [PLATFORM, *(f"{PLATFORM}/{kind}" for kind in GLOBAL_KINDS)]


# --- 16.5 el tráfico global es la suma de las organizaciones -----------------------------------------


def _same(a: Any, b: Any) -> bool:
    """Igual y del mismo tipo (un `3.0` no es un `3`, ni un `True` un `1`)."""
    return a == b and type(a) is type(b)


def traffic_row_problems(slug: str, row: Any, own: dict[str, Any], users: Sequence[dict[str, Any]]) -> list[str]:
    """16.5: la fila de `slug` del tráfico global (`group_by=org`) frente al informe de tráfico de la propia
    organización (`own` = su `totals`; `users` = su `group_by=user`): las diez cifras iguales y `active_users` = las
    personas con peticiones."""
    if not isinstance(row, dict):
        return [f"«{slug}» no sale en el tráfico global (group_by=org)"]
    problems = [
        f"{name} = {row.get(name)!r} en el tráfico global y {own.get(name)!r} en el informe de «{slug}»"
        for name in TRAFFIC_COMPARED
        if not _same(row.get(name), own.get(name))
    ]
    active = sum(1 for u in users if _int(u.get("requests")) and u["requests"] > 0)
    if not _same(row.get("active_users"), active):
        problems.append(
            f"active_users = {row.get('active_users')!r} en el tráfico global y {active} personas con peticiones en "
            f"el informe de «{slug}»"
        )
    return problems


def traffic_sum_problems(report: dict[str, Any], orgs: int | None = None) -> list[str]:
    """16.5: los `totals` de un informe de tráfico global son la suma de sus `series` (sin los percentiles; los
    `active_users` solo con `group_by=org`, donde `orgs` es el número de series), y `totals.orgs` es `orgs`."""
    series = [s for s in report.get("series") or [] if isinstance(s, dict)]
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    group_by = report.get("group_by")
    names = TRAFFIC_ADDED if group_by == "org" else TRAFFIC_ADDED[:7]
    problems = [
        f"group_by={group_by}: totals.{name} = {totals.get(name)!r}, la suma de las series es {total}"
        for name in names
        if (total := sum(s.get(name) or 0 for s in series)) != totals.get(name)
    ]
    expected = len(series) if group_by == "org" else orgs
    if expected is not None and totals.get("orgs") != expected:
        problems.append(f"group_by={group_by}: totals.orgs = {totals.get('orgs')!r} (se esperaban {expected})")
    return problems


def days_problems(report: dict[str, Any], first: str, last: str) -> list[str]:
    """16.5: `group_by=day` trae todos los días UTC del rango, en orden y con ceros (contrato §7.4)."""
    start, end = datetime.fromisoformat(first).date(), datetime.fromisoformat(last).date()
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    got = [s.get("key") for s in report.get("series") or [] if isinstance(s, dict)]
    if got != days:
        return [f"group_by=day: las series son {got} (se esperaban los días del rango {days})"]
    return []


def same_totals_problems(reports: dict[str, dict[str, Any]]) -> list[str]:
    """16.5: `group_by=org`, `day` y `org_day` del mismo rango dicen los mismos totales (las siete cifras que se suman y
    el número de organizaciones)."""
    base = reports["org"].get("totals") or {}
    problems = []
    for group_by, report in reports.items():
        if group_by == "org":
            continue
        totals = report.get("totals") or {}
        problems += [
            f"group_by={group_by}: totals.{name} = {totals.get(name)!r} y {base.get(name)!r} con group_by=org"
            for name in (*TRAFFIC_ADDED[:7], "orgs")
            if name in totals and totals.get(name) != base.get(name)
        ]
    return problems


def org_day_problems(report: dict[str, Any], own_requests: dict[str, int]) -> list[str]:
    """16.5: `org_day` nombra como mucho 10 organizaciones en `top` y, de cada una de las nuestras que están en él, sus
    parejas (organización, día) suman sus peticiones. Las que no entran en el top van en «(otras)»: no se comprueban."""
    top = report.get("top") if isinstance(report.get("top"), list) else []
    problems = []
    if len(top) > ORG_DAY_TOP:
        problems.append(f"group_by=org_day: top trae {len(top)} organizaciones (el tope es {ORG_DAY_TOP})")
    for slug, requests in own_requests.items():
        if slug not in top:
            continue
        got = sum(
            s.get("requests") or 0
            for s in report.get("series") or []
            if isinstance(s, dict) and (s.get("org") or {}).get("slug") == slug
        )
        if got != requests:
            problems.append(
                f"group_by=org_day: las parejas de «{slug}» suman {got} peticiones (se esperaban {requests})"
            )
    return problems


def platform_row_problems(slug: str, row: Any, glob: dict[str, Any]) -> list[str]:
    """16.5: la fila de `slug` en `GET /platform/usage` y la del tráfico global dicen lo mismo (`blocked_requests` es
    `blocked`)."""
    if not isinstance(row, dict):
        return [f"«{slug}» no sale en el informe de consumo (GET /platform/usage)"]
    pairs = (("requests", "requests"), ("blocked_requests", "blocked"), ("errors", "errors"),
             ("tokens_in", "tokens_in"), ("tokens_out", "tokens_out"), ("bytes_in", "bytes_in"),
             ("bytes_out", "bytes_out"), ("active_users", "active_users"))  # fmt: skip
    return [
        f"{mine} = {row.get(mine)!r} en GET /platform/usage y {glob.get(theirs)!r} ({theirs}) en el tráfico global"
        for mine, theirs in pairs
        if not _same(row.get(mine), glob.get(theirs))
    ]


# --- 16.6 el anonimizador de una organización es el de plataforma ------------------------------------


def anonymizer_equal_problems(slug: str, own: Any, platform: Any, glob: Any) -> list[str]:
    """16.6: las tres lecturas del anonimizador de `slug` (`own` = `totals` de `GET /orgs/{slug}/usage/anonymizer`;
    `platform` = el bloque `anonymizer` de su fila de `GET /platform/usage`; `glob` = su elemento de
    `GET /platform/usage/anonymizer`) dicen lo mismo en las 19 columnas y en los tres modos."""
    problems = []
    for label, reading in (("el anonimizador de la organización", own), ("el informe de plataforma", platform),
                           ("el anonimizador global (plataforma)", glob)):  # fmt: skip
        if not isinstance(reading, dict):
            problems.append(f"«{slug}» no sale en {label}")
    if problems:
        return problems
    readings = {"organización": own, "plataforma": platform, "global": glob}
    for column in ANONYMIZER_COLUMNS:
        values = {name: r.get(column) for name, r in readings.items()}
        if not all(_same(v, values["organización"]) for v in values.values()):
            problems.append(f"{column}: " + " · ".join(f"{name} {value!r}" for name, value in values.items()))
    modes = {name: (r.get("turns_by_mode") if isinstance(r.get("turns_by_mode"), dict) else {}) for name, r in
             readings.items()}  # fmt: skip
    for mode in MODES:
        values = {name: m.get(mode) for name, m in modes.items()}
        if not all(_same(v, values["organización"]) for v in values.values()):
            problems.append(
                f"turns_by_mode.{mode}: " + " · ".join(f"{name} {value!r}" for name, value in values.items())
            )
    return problems


def anonymizer_series_problems(
    report: dict[str, Any], *, days: Sequence[str] | None = None, count: int | None = None
) -> list[str]:
    """16.6: las series del anonimizador de una organización (`group_by=day` u `hour`, todas con ceros) suman `totals`
    en cada columna y en cada modo; `days` son los días del rango y `count` cuántas series se esperan."""
    series = [s for s in report.get("series") or [] if isinstance(s, dict)]
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    problems = []
    if days is not None and [s.get("key") for s in series] != list(days):
        problems.append(
            f"group_by={report.get('group_by')}: las series son {[s.get('key') for s in series]} "
            f"(se esperaban los días del rango {list(days)})"
        )
    if count is not None and len(series) != count:
        problems.append(f"group_by={report.get('group_by')}: {len(series)} series (se esperaban {count})")
    for column in ANONYMIZER_COLUMNS:
        total = sum(s.get(column) or 0 for s in series)
        if total != totals.get(column):
            problems.append(f"{column}: las series suman {total} y totals dice {totals.get(column)!r}")
    for mode in MODES:
        total = sum((s.get("turns_by_mode") or {}).get(mode) or 0 for s in series)
        got = (totals.get("turns_by_mode") or {}).get(mode)
        if total != got:
            problems.append(f"turns_by_mode.{mode}: las series suman {total} y totals dice {got!r}")
    return problems


# --- 16.7 las consultas globales no auditan -------------------------------------------------------------


def viewed_snapshot(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Las filas `usage.viewed` de la auditoría con su contador (una fila agregada cuenta las consultas de una hora)."""
    return {str(r.get("id")): int(r.get("count") or 1) for r in rows if isinstance(r, dict)}


def viewed_problems(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """16.7: tras las consultas no hay ninguna fila `usage.viewed` nueva ni una agregada que haya crecido."""
    problems = [f"fila {VIEWED} nueva ({row_id}, {count} vez/veces)" for row_id, count in after.items()
                if row_id not in before]  # fmt: skip
    problems += [f"la fila {VIEWED} {row_id} pasó de {before[row_id]} a {count} consultas"
                 for row_id, count in after.items() if row_id in before and count != before[row_id]]  # fmt: skip
    return problems


def org_viewed_problems(slug: str, rows: Sequence[dict[str, Any]]) -> list[str]:
    """16.7: la auditoría de la organización (vista del PLATFORM_ADMIN) no tiene ninguna fila `usage.viewed`."""
    if not rows:
        return []
    kinds = [str(r.get("target_id")) for r in rows]
    return [f"la auditoría de «{slug}» tiene {len(rows)} fila(s) {VIEWED} ({', '.join(kinds)}) tras las consultas "
            "globales y del anonimizador"]  # fmt: skip


def control_problems(slug: str, rows: Sequence[dict[str, Any]]) -> list[str]:
    """16.7, control positivo: el detalle por persona de un PLATFORM_ADMIN que no es ORG_ADMIN de la organización deja
    exactamente una fila (`traffic`, `group_by=user`); sin ella, que no haya filas antes no probaría nada."""
    found = [(r.get("target_id"), (r.get("metadata") or {}).get("group_by")) for r in rows]
    if found != [("traffic", "user")]:
        return [f"control: la auditoría de «{slug}» tras el detalle por persona tiene {found} (se esperaba "
                "[('traffic', 'user')]: sin esa fila, que no haya otras no probaría nada)"]  # fmt: skip
    return []


# --- 16.8 L2 con el NER ---------------------------------------------------------------------------------


def _l2(turn: Sent) -> Any:
    return turn.verification.get("l2") if isinstance(turn.verification, dict) else None


def has_l2(sent: Sequence[Sent]) -> bool:
    """¿Hay NER en la pila? Algún turno trae `verification.l2` como objeto (§5.3: un proxy sin NER no lo emite)."""
    return any(isinstance(_l2(t), dict) for t in sent)


def _bytes(value: Any) -> int:
    return value if _int(value) and value >= 0 else 0


def l2_expected(sent: Sequence[Sent]) -> dict[str, int]:
    """Lo que las columnas `l2_*` deben sumar de esos turnos (contrato §5.2): `l2_turns` por cada `verification.l2`
    que es un objeto, `l2_enabled` si `enabled` es el literal `true`, un turno en la columna de su `status`, y los
    bytes (y las detecciones) que declara."""
    want = dict.fromkeys(L2_COLUMNS, 0)
    for turn in sent:
        l2 = _l2(turn)
        if not isinstance(l2, dict):
            continue
        want["l2_turns"] += 1
        want["l2_enabled"] += l2.get("enabled") is True
        if l2.get("status") in L2_STATUS:
            want[L2_STATUS[l2["status"]]] += 1
        for column, field in L2_BYTES.items():
            want[column] += _bytes(l2.get(field))
    return want


def l2_problems(anon: dict[str, Any], want: dict[str, int]) -> list[str]:
    """16.8: el bloque del anonimizador frente a `want` (`l2_expected`), columna a columna, y que con el NER se llenen:
    `l2_turns > 0` y, observando o aplicando, `l2_observed_turns` o `l2_applied` > 0."""
    problems = [f"{column} = {anon.get(column)!r} (se esperaba {value})" for column, value in want.items()
                if not _same(anon.get(column), value)]  # fmt: skip
    if want["l2_turns"] > 0 and not anon.get("l2_turns"):
        problems.append(f"con el NER las columnas l2_* se llenan, y l2_turns = {anon.get('l2_turns')!r} "
                        f"(los turnos traían verification.l2: {want['l2_turns']})")  # fmt: skip
    if not (anon.get("l2_observed_turns") or 0) + (anon.get("l2_applied") or 0):
        problems.append(f"con el NER observando o aplicando, l2_observed_turns o l2_applied son > 0 (son "
                        f"{anon.get('l2_observed_turns')!r} y {anon.get('l2_applied')!r})")  # fmt: skip
    return problems


def no_ner_message() -> str:
    return (
        "SALTADO (la pila no trae el NER): ningún turno trae verification.l2 (L2 no está desplegado o el proxy no lo "
        "emite), así que las columnas l2_* se quedan a cero y no hay nada que comprobar; con la pila del NER el paso "
        "comprueba que se llenan y suman lo que dice cada turno. Para exigir el NER (y que su ausencia falle), "
        "declara la capacidad con --ner o BAAS_E2E_NER=1"
    )


def ner_declared_message() -> str:
    """16.8 con el NER declarado (`--ner` o `BAAS_E2E_NER=1`) y ningún turno con `verification.l2`: un fallo."""
    return (
        "se declaró el NER (--ner o BAAS_E2E_NER=1) y ningún turno trae verification.l2: o el NER no está "
        "desplegado en esta pila, o no está encendido por omisión, o el proxy ha dejado de emitirlo (una regresión "
        "que, sin esa declaración, este paso habría dado por «pila sin NER»); las columnas l2_* se quedan a cero. "
        "Quita --ner solo si la pila de verdad no trae el NER"
    )


# --- la prueba ------------------------------------------------------------------------------------


class ConsumoGroup:
    def __init__(self, e2e: E2EH4) -> None:
        self.e = e2e
        self.a = e2e.args
        self.nonce = e2e.nonce
        self.uso = e2e_uso.UsoGroup(e2e)  # las organizaciones de cada paso, los turnos y las esperas
        self.uso.slug_prefix = "consumo"

    # --- quién -------------------------------------------------------------------------------------
    @property
    def admin(self) -> str:
        return str(self.a.admin_email)

    @property
    def org_admin(self) -> str:
        return str(self.a.orgadmin_email)

    @property
    def user(self) -> str:
        return str(self.a.bypass_email)

    @property
    def other_user(self) -> str:
        return str(self.a.anon_email)  # USER de otra organización (la semilla trae «privada»)

    def logins(self) -> list[str]:
        return [self.admin, self.org_admin, self.user, self.other_user]

    # --- el informe --------------------------------------------------------------------------------
    def platform(self, who: str | None, params: dict[str, Any] | None = None, kind: str | None = None) -> Http:
        """`GET /platform/usage` o, con `kind`, `GET /platform/usage/<kind>` (como `who`; `None` es sin sesión)."""
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        path = PLATFORM + (f"/{kind}" if kind else "") + (f"?{query}" if query else "")
        if who is None:
            return h3.http("GET", f"{self.a.app_url}{path}", timeout=self.a.timeout)
        return self.e.webapp("GET", path, who)

    def global_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """Un informe global (`/platform/usage/<kind>`) como PLATFORM_ADMIN: `200` con `totals` y `series`."""
        res = self.platform(self.admin, params, kind)
        data = res.json()
        hint = " (¿la pila tiene los informes globales de T7?)" if res.status == 404 else ""
        check(
            res.status == 200 and isinstance(data, dict) and isinstance(data.get("series"), list),
            f"GET {PLATFORM}/{kind}?{urlencode(params)} ({self.admin}) → {e2e_uso.describe(res)}{hint}",
        )
        return data

    def report(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        res = self.platform(self.admin, params if params is not None else utc_range())
        data = res.json()
        check(
            res.status == 200 and isinstance(data, dict) and isinstance(data.get("orgs"), list),
            f"GET {PLATFORM}?{urlencode(params or utc_range())} ({self.admin}) → {e2e_uso.describe(res)}",
        )
        return data

    def row(self, slug: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """La fila de `slug` en el informe (todas las organizaciones salen, con ceros)."""
        rows = {o.get("slug"): o for o in self.report(params)["orgs"] if isinstance(o, dict)}
        check(slug in rows, f"«{slug}» no sale en el informe de consumo: {sorted(map(str, rows))}")
        return rows[slug]

    # --- 16.0 la pila tiene el consumo ------------------------------------------------------------------
    def ready(self) -> str:
        month = current_month()
        res = self.platform(self.admin, {"month": month})
        data = res.json()
        problem = consumo_problem(res.status, data)
        check(problem is None, f"{problem}: {h3.snippet(res.body, 200)}")
        period, sources = data["period"], data["data_until_by_source"]
        return (
            f"GET {PLATFORM}?month={month} → 200: period {{month: {period.get('month')}, open: {period.get('open')}}}; "
            f"datos hasta por tubería {sources}"
        )

    # --- 16.1 turnos por modo -------------------------------------------------------------------------
    def org_state(self, org: e2e_uso.UsoOrg) -> dict[str, Any]:
        res = self.e.webapp("GET", f"/api/v1/orgs/{org.slug}", self.admin)
        check(res.status == 200, f"GET /api/v1/orgs/{org.slug} → {e2e_uso.describe(res)}")
        body = res.json() or {}
        return body["organization"] if isinstance(body.get("organization"), dict) else body

    def set_mode(self, org: e2e_uso.UsoOrg, mode: str) -> None:
        """Pone la organización en `mode` (BYPASS = AI-IP apagado; OBSERVATION = AI-IP con el anonimizador apagado;
        ANONYMIZED = los dos) como PLATFORM_ADMIN y espera a que llegue al proxy. Una conversación nueva sale con lo que
        el proxy tenga entonces (las que ya están abiertas solo suben, H4-08)."""
        want_ai_ip, want_anonymizer = mode != "BYPASS", mode == "ANONYMIZED"
        changed = False
        if self.org_state(org).get("ai_ip_enabled") is not want_ai_ip:
            self.toggle(org, "ai-ip", want_ai_ip)
            changed = True
        if want_ai_ip and self.org_state(org).get("anonymizer_enabled") is not want_anonymizer:
            self.toggle(org, "anonymizer", want_anonymizer)
            changed = True
        if changed:
            time.sleep(self.a.propagation_wait)  # el cambio llega al proxy por Pub/Sub, sin esperar respuesta

    def toggle(self, org: e2e_uso.UsoOrg, what: str, enabled: bool) -> None:
        res = self.e.admin_post(f"/api/v1/orgs/{org.slug}/{what}", {"enabled": enabled, "reason": REASON}, method="PUT")
        check(
            res.status == 200, f"PUT …/{org.slug}/{what} {{enabled: {str(enabled).lower()}}} → {e2e_uso.describe(res)}"
        )

    def read_turns(self, org: e2e_uso.UsoOrg, sent: Sequence[Sent], reader: str | None = None) -> None:
        """Completa `sent` con lo que la webapp dice de cada turno (como su dueño): con qué modo salió y su
        verificación. `reader` es el ORG_ADMIN de la organización que lista sus conversaciones (por omisión, el de la
        semilla)."""
        convs = self.uso.conversations(org, reader)
        for turn in sent:
            conv = convs.get(turn.model)
            check(conv is not None, f"la conversación {turn.model} no aparece en la organización «{org.slug}»")
            indexes = self.e.turn_indexes(self.user, str(conv["id"]))  # type: ignore[index]
            res = self.e.turn(self.user, str(conv["id"]), indexes[-1])  # type: ignore[index]
            check(res.status == 200, f"GET …/{conv['id']}/turns/{indexes[-1]} ({self.user}) → {e2e_uso.describe(res)}")  # type: ignore[index]
            data = res.json() or {}
            turn.state = mode_of(data.get("ai_ip_enabled"), data.get("anonymizer_enabled"))
            turn.verification = data.get("verification")

    def cases(self) -> list[tuple[str, bool]]:
        """(API, *streaming*) que se reparten los turnos según `--apis`."""
        cases = [
            (api, stream)
            for api, stream in (("messages", False), ("messages", True), ("chat", False), ("responses", False))
            if api in self.a.apis
        ]
        check(cases, "--apis no deja ninguna API que probar")
        return cases

    def send_plan(
        self, org: e2e_uso.UsoOrg, plan: Sequence[tuple[str, int]], *, text: str = "", who: str | None = None
    ) -> list[Sent]:
        """Envía por el proxy, en orden, `count` turnos de cada modo de `plan` (cada uno, una conversación nueva con su
        `model`) y devuelve lo enviado con los tokens que declara el proveedor. Antes de cada modo, lo pone."""
        cases, sender = self.cases(), who or self.user
        sent: list[Sent] = []
        for mode, count in plan:
            self.set_mode(org, mode)
            for i in range(count):
                api, stream = cases[len(sent) % len(cases)]
                model = f"consumo-{self.nonce}-{org.slug.rsplit('-', 1)[-1]}-{mode.lower()}-{i}"
                body = f"consumo {self.nonce} {mode.lower()} {i}. {text + ' ' if text else ''}{h3.SENSITIVE_TEXT}."
                _, res = self.uso.send(org, sender, api, body, stream=stream, model=model)
                tokens = e2e_uso.reply_tokens(api, res.body, stream=stream)
                check(tokens, f"{h3.API_NAMES[api]}: la respuesta del proveedor no declara los tokens")
                sent.append(Sent(mode, tokens[0], tokens[1], model=model))  # type: ignore[index]
        return sent

    def wait_row(self, org: e2e_uso.UsoOrg, turns: int) -> dict[str, Any]:
        """Espera a que la fila de la organización en `GET /platform/usage` cuente `turns` peticiones (las trazas, ya
        consumidas) y la devuelve."""
        got: dict[str, Any] = {}

        def consumed() -> list[str]:
            got["row"] = self.row(org.slug)
            n = got["row"].get("requests")
            return [] if n == turns else [f"requests = {n!r} (se esperaba {turns}: trazas sin consumir)"]

        self.uso.wait(consumed, self.a.trace_timeout, f"el informe de «{org.slug}» no llega a {turns} turnos", 0.5)
        return got["row"]

    def modes(self) -> str:
        plan = (("BYPASS", 1), ("OBSERVATION", 2), ("ANONYMIZED", 3))  # una cifra distinta por modo
        with self.uso.org("modos", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            sent = self.send_plan(org, plan, text="")
            row = self.wait_row(org, len(sent))
            self.read_turns(org, sent)
            want = expected_figures(sent)  # tras leer los turnos: de ellos salen las sustituciones y la verificación
            problems = state_problems(sent)
            check(not problems, "\n".join(problems))
            problems = mode_problems(row, want)
            check(not problems, "\n".join(problems))
            anon = row["anonymizer"]
        by_mode = " · ".join(f"{m} {want['turns_by_mode'][m]}" for m in MODES)
        return (
            f"{len(sent)} turnos nuevos en «{org.slug}» ({by_mode}) = turns_by_mode del informe; anonymizer.turns = "
            f"requests = {len(sent)}; tokens {want['tokens_in']}/{want['tokens_out']}; L1: replaced_in "
            f"{anon['replaced_in']} = {want['replaced_in']}, restored_out {anon['restored_out']} = "
            f"{want['restored_out']}; "
            f"verificación {want['verified']}"
        )

    # --- 16.2 disco ------------------------------------------------------------------------------------
    def settle(
        self, org: e2e_uso.UsoOrg, written: datetime, what: str, *, above: int = -1
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Espera a una foto posterior a `written` que ya cuente lo escrito (la skill y el área, y más de `above` bytes
        facturables: una foto cortada antes de las escrituras no cuenta) y comprueba la fila del informe frente al
        `latest` de la organización. Una foto nueva entre las dos lecturas se vuelve a leer; una diferencia de valores
        de la misma foto, no."""
        got: dict[str, Any] = {}

        def compare() -> list[str]:
            latest = self.uso.report_json("storage", org.slug, self.org_admin, self.uso.today(org)).get("latest")
            taken = e2e_uso.parse_instant((latest or {}).get("taken_at"))
            if taken is None or taken <= written:
                return [f"sin foto posterior a las escrituras (latest.taken_at = {(latest or {}).get('taken_at')})"]
            row = self.row(org.slug)  # la de plataforma se lee después: puede ir por delante, no por detrás
            problems = billable_problems(row, latest)
            if problems and not (len(problems) == 1 and NEWER_SNAPSHOT in problems[0]):
                raise StepError(what + ":\n" + "\n".join(problems))
            problems += published_problems(row, skills=1, areas=1)
            if not problems and row["disk"]["last"] <= above:
                problems.append(f"disk.last = {row['disk']['last']} no supera los {above} B de la foto anterior")
            if not problems:
                got.update(latest=latest, row=row)
            return problems

        self.uso.wait(compare, self.a.usage_timeout, what, every=2)
        return got["latest"], got["row"]

    def disk(self) -> str:
        skill, area = f"disco-{self.nonce}", f"area-{self.nonce}"
        pad = "x" * 4000
        with self.uso.org("disco", {self.org_admin: "ORG_ADMIN"}) as org:
            self.uso.write_skill(
                org,
                skill,
                {"scripts/proceso.py": f"# {pad}\nprint(1)\n", "esquema.json": json.dumps({"relleno": pad}) + "\n"},
            )
            self.uso.write_doc(org, f"knowledge/{area}/doc.md", pad * 3)
            latest, row = self.settle(
                org, datetime.now(timezone.utc), "la foto de disco no llega o no cuadra con la de W15"
            )
            first = row["disk"]["last"]
            # el disco crece: otra foto, más grande. La media recoge la anterior y el máximo es la última
            self.uso.write_doc(org, f"knowledge/{area}/mas.md", pad * 8)
            what = "la foto de disco tras crecer no llega o no cuadra con la de W15"
            latest2, row2 = self.settle(org, datetime.now(timezone.utc), what, above=first)
            problems = growth_problems(row2, first=first)
            check(not problems, "\n".join(problems))
        disk = row2["disk"]
        return (
            f"foto {latest['taken_at']}: facturable {first} B + índice {latest['bytes']['index']} B (aparte) = "
            "total con "
            f"índice {latest['total_bytes']} B; skills 1, áreas 1 (sin tope). Tras crecer ({latest2['taken_at']}): "
            f"facturable {disk['last']} B = máximo, media {disk['avg']} B, {disk['snapshots']} fotos, índice "
            f"{disk['index_last']} B"
        )

    # --- 16.3 CSV = JSON -------------------------------------------------------------------------------
    def csv(self) -> str:
        name = f'=E2E consumo, "{self.nonce}"'  # empieza por «=» y lleva coma y comillas
        with self.uso.org("csv", {self.org_admin: "ORG_ADMIN", self.user: "USER"}, name=name) as org:
            for i in range(2):
                self.uso.send(org, self.user, "messages", f"csv {self.nonce} {i}. Hola.")
            self.uso.wait_requests(org, 2)
            details = []
            for params in (utc_range(), {"month": current_month()}):
                found: dict[str, Any] = {}

                def compare(params: dict[str, Any] = params, found: dict[str, Any] = found) -> list[str]:
                    data = self.report(params)
                    res = self.platform(self.admin, {**params, "format": "csv"})
                    if res.status != 200:
                        raise StepError(f"GET {PLATFORM}?format=csv → {e2e_uso.describe(res)}")
                    header = {k.lower(): v for k, v in res.headers}
                    text = res.body.decode("utf-8")
                    problems = []
                    if not str(header.get("content-type", "")).startswith("text/csv"):
                        problems.append(f"Content-Type = {header.get('content-type')!r} (se esperaba text/csv)")
                    problems += disposition_problems(header.get("content-disposition"), csv_filename(data["period"]))
                    problems += csv_problems(text, data["orgs"])
                    mine = next((o for o in data["orgs"] if o.get("slug") == org.slug), None)
                    if mine is None or mine.get("requests") != 2:
                        problems.append(
                            f"«{org.slug}» no sale con 2 peticiones en el JSON: {mine and mine.get('requests')}"
                        )
                    table = list(csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline="")))
                    ours = next(
                        (dict(zip(table[0], r, strict=False)) for r in table[1:] if r and r[0] == org.slug), None
                    )
                    if ours is None or ours.get("name") != "'" + name:
                        problems.append(
                            f"el nombre «{name}» no sale con apóstrofo en el CSV: {ours and ours.get('name')!r}"
                        )
                    found.update(rows=len(data["orgs"]), name=ours and ours.get("name"))
                    return problems

                self.uso.wait(compare, CSV_SETTLE, f"el CSV no es el JSON ({params})", every=1)
                period = f"month={params['month']}" if "month" in params else f"from={params['from']}&to={params['to']}"
                details.append(f"{period}: {found['rows']} fila(s)")
        return (
            f"CSV = JSON (46 columnas, BOM, \\r\\n, una fila por organización y sin totales) con "
            f"{' y '.join(details)}; "
            f"el nombre {name!r} sale con apóstrofo ({found['name']!r})"
        )

    # --- 16.4 permisos ---------------------------------------------------------------------------------
    def permissions(self) -> str:
        other = self.other_user
        month = current_month()
        dates = utc_range()
        invalid_range = {"from": "2026-12-31", "to": "2026-01-01"}
        problems: list[str] = []
        for kind in (None, *GLOBAL_KINDS):
            route = f"{PLATFORM}/{kind}" if kind else PLATFORM
            base = (
                {"month": month} if kind is None else dates
            )  # el informe de consumo es por mes; los globales, por rango
            invalid = {"month": "2026-13"} if kind is None else {"group_by": "hour"}  # `hour` no es de los globales
            for label, params in (
                ("JSON", base),
                ("CSV", {**base, "format": "csv"}),
                ("parámetro no válido", invalid),
                ("from y to no válidos", invalid_range),
            ):
                for who, want in ((self.org_admin, 403), (self.user, 403), (other, 403), (None, 401)):
                    res = self.platform(who, params, kind)
                    found = denied_problems(res.status, res.json(), want)
                    problems += [f"{route} {label} ({who or 'sin sesión'}): {p}" for p in found]
            control = self.platform(self.admin, base, kind)
            if control.status != 200:
                problems.append(f"control: {route} como PLATFORM_ADMIN → {e2e_uso.describe(control)} (se esperaba 200)")
            wrong = self.platform(self.admin, invalid, kind)
            field = (wrong.json() or {}).get("field") if isinstance(wrong.json(), dict) else None
            want_field = "month" if kind is None else "group_by"
            if (wrong.status, field) != (422, want_field):
                problems.append(
                    f"control: {route} con {urlencode(invalid)} como PLATFORM_ADMIN → {e2e_uso.describe(wrong)} "
                    f"(se esperaba 422 con field {want_field})"
                )
        check(not problems, "\n".join(problems))
        return (
            f"GET {PLATFORM} y …/{{{','.join(GLOBAL_KINDS)}}}: ORG_ADMIN ({self.org_admin}), USER ({self.user}) y USER "
            f"de otra organización ({other}) → 403 FORBIDDEN y sin sesión → 401, en JSON, en CSV y con parámetros no "
            "válidos (los permisos van antes que la validación: no se filtra ni un dato); el PLATFORM_ADMIN → 200 y "
            "422 con un valor no válido"
        )

    # --- organizaciones de zona UTC ---------------------------------------------------------------------
    @contextlib.contextmanager
    def utc_org(self, label: str, members: dict[str, str]) -> Iterator[e2e_uso.UsoOrg]:
        """Una organización del paso con zona `UTC` (se cambia nada más crearla, antes de enviar nada): el informe de
        plataforma habla en UTC y el de la organización en su zona, así que solo con `UTC` hablan de los mismos días
        (criterio 2 del diseño, contrato §11.2)."""
        with self.uso.org(label, members) as org:
            res = self.e.admin_post(f"/api/v1/orgs/{org.slug}", {"timezone": "UTC"}, method="PATCH")
            check(res.status == 200, f"PATCH /api/v1/orgs/{org.slug} {{timezone: UTC}} → {e2e_uso.describe(res)}")
            zone = ((res.json() or {}).get("organization") or {}).get("timezone")
            check(zone == "UTC", f"«{org.slug}» sigue en la zona {zone!r} tras PATCH {{timezone: UTC}}")
            org.tz = ZoneInfo("UTC")
            yield org

    # --- 16.5 el tráfico global es la suma de las organizaciones -----------------------------------------
    def send_many(self, org: e2e_uso.UsoOrg, who: str, count: int, label: str) -> None:
        """`count` turnos de `who` por el proxy, repartidos entre las APIs de `--apis`."""
        cases = self.cases()
        for i in range(count):
            api, stream = cases[i % len(cases)]
            name = f"consumo-{self.nonce}-{label}-{who.split('@')[0]}-{i}"
            self.uso.send(org, who, api, f"{label} {self.nonce} {who} {i}. Hola.", stream=stream, model=name)

    def global_sum(self) -> str:
        # (etiqueta, miembros, quién lee su informe, quién envía cuántos): cifras distintas, dos y una personas activas
        plan = (
            ("alfa", {self.org_admin: "ORG_ADMIN", self.user: "USER"}, self.org_admin,
             ((self.org_admin, 1), (self.user, 2))),
            ("beta", {self.other_user: "ORG_ADMIN"}, self.other_user, ((self.other_user, 5),)),
        )  # fmt: skip
        with contextlib.ExitStack() as stack:
            orgs = [stack.enter_context(self.utc_org(label, members)) for label, members, _r, _s in plan]
            expected = {}
            for org, (label, _members, _reader, senders) in zip(orgs, plan, strict=True):
                for who, count in senders:
                    self.send_many(org, who, count, label)
                expected[org.slug] = sum(count for _who, count in senders)
            readers = {org.slug: reader for org, (_l, _m, reader, _s) in zip(orgs, plan, strict=True)}
            found: dict[str, Any] = {}

            def compare() -> list[str]:
                dates = utc_range()
                groups = ("org", "day", "org_day")
                reports = {g: self.global_report("traffic", {**dates, "group_by": g}) for g in groups}
                rows = {s.get("key"): s for s in reports["org"]["series"] if isinstance(s, dict)}
                platform = {o.get("slug"): o for o in self.report(dates)["orgs"] if isinstance(o, dict)}
                problems: list[str] = []
                own_requests: dict[str, int] = {}
                for org in orgs:
                    today = self.uso.today(org)
                    own = self.uso.report_json("traffic", org.slug, readers[org.slug], today).get("totals") or {}
                    users = self.uso.report_json("traffic", org.slug, readers[org.slug], {**today, "group_by": "user"})
                    own_requests[org.slug] = int(own.get("requests") or 0)
                    if own_requests[org.slug] != expected[org.slug]:
                        problems.append(
                            f"«{org.slug}»: {own_requests[org.slug]} peticiones en su informe "
                            f"(se esperaban {expected[org.slug]}: trazas sin consumir)"
                        )
                        continue
                    problems += traffic_row_problems(org.slug, rows.get(org.slug), own, users.get("series") or [])
                    if isinstance(rows.get(org.slug), dict):
                        problems += platform_row_problems(org.slug, platform.get(org.slug), rows[org.slug])
                if problems:
                    return problems
                count = reports["org"]["totals"].get("orgs")
                problems += traffic_sum_problems(reports["org"])
                problems += traffic_sum_problems(reports["day"], orgs=count)
                problems += traffic_sum_problems(reports["org_day"], orgs=count)
                problems += days_problems(reports["day"], dates["from"], dates["to"])
                problems += same_totals_problems(reports)
                problems += org_day_problems(reports["org_day"], own_requests)
                found["totals"] = reports["org"]["totals"]
                return problems

            what = "el tráfico global no es la suma de los informes de cada organización"
            self.uso.wait(compare, self.a.trace_timeout, what, every=0.5)
        totals, (first, second) = found["totals"], orgs
        return (
            f"«{first.slug}» y «{second.slug}» (zona UTC) con {expected[first.slug]} y {expected[second.slug]} "
            f"peticiones: cada fila de {PLATFORM}/traffic?group_by=org es el informe de tráfico de su organización "
            f"(diez cifras y personas activas) y su fila de {PLATFORM}; totals = suma de las {totals['orgs']} "
            f"organizaciones de la pila ({totals['requests']} peticiones); los totales de group_by=org, day y org_day "
            "coinciden, day trae cada día del rango y org_day suma lo de cada una de las nuestras que está en su top"
        )

    # --- 16.6 el anonimizador de la organización es el de plataforma ---------------------------------------
    def anonymizer(self) -> str:
        members = {self.other_user: "ORG_ADMIN", self.user: "USER"}  # `org_admin` es el ORG_ADMIN de otra
        with self.utc_org("anon", members) as org:
            plan = (("BYPASS", 1), ("OBSERVATION", 2), ("ANONYMIZED", 3))
            sent = self.send_plan(org, plan)
            row = self.wait_row(org, len(sent))
            self.read_turns(org, sent, self.other_user)
            problems = state_problems(sent)
            check(not problems, "\n".join(problems))
            want = expected_figures(sent)["turns_by_mode"]
            dates = utc_range()
            path = f"/api/v1/orgs/{org.slug}/usage/anonymizer"
            own: dict[str, Any] = {}
            problems = []
            for label, params in (("day", {**dates, "group_by": "day"}), ("hour", {**dates, "group_by": "hour"})):
                res = self.e.webapp("GET", f"{path}?{urlencode(params)}", self.admin)
                data = res.json()
                check(res.status == 200 and isinstance(data, dict) and isinstance(data.get("series"), list),
                      f"GET {path}?{urlencode(params)} ({self.admin}) → {e2e_uso.describe(res)}")  # fmt: skip
                own[label] = data
            first = datetime.fromisoformat(dates["from"]).date()
            days = [(first + timedelta(days=i)).isoformat() for i in range(2)]
            problems += anonymizer_series_problems(own["day"], days=days)
            problems += anonymizer_series_problems(own["hour"], count=48)
            by_org = self.global_report("anonymizer", {**dates, "group_by": "org"})
            glob = next((s for s in by_org["series"] if isinstance(s, dict) and s.get("key") == org.slug), None)
            row = self.row(org.slug)  # tras leer los turnos: la fila ya no cambia
            problems += anonymizer_equal_problems(org.slug, own["day"].get("totals"), row.get("anonymizer"), glob)
            modes = (own["day"].get("totals") or {}).get("turns_by_mode")
            if modes != want:
                problems.append(f"turns_by_mode del anonimizador de la organización = {modes!r} (se esperaba {want!r})")
            check(not problems, "\n".join(problems))
            problems = []
            for who, status in ((self.admin, 200), (self.other_user, 200), (self.user, 403), (self.org_admin, 404),
                                (None, 401)):  # fmt: skip
                for fmt in ("json", "csv"):
                    query = urlencode({**dates, "group_by": "day", "format": fmt})
                    res = (self.e.webapp("GET", f"{path}?{query}", who) if who
                           else h3.http("GET", f"{self.a.app_url}{path}?{query}", timeout=self.a.timeout))  # fmt: skip
                    who_name = who or "sin sesión"
                    if status == 200:
                        ctype = dict((k.lower(), v) for k, v in res.headers).get("content-type", "")
                        if res.status != 200 or (fmt == "csv") != ctype.startswith("text/csv"):
                            problems.append(f"{fmt} ({who_name}): {e2e_uso.describe(res)} {ctype!r} (se esperaba 200)")
                    else:
                        problems += [
                            f"{fmt} ({who_name}): {p}" for p in denied_problems(res.status, res.json(), status)
                        ]
            check(not problems, "\n".join(problems))
        by_mode = " · ".join(f"{m} {want[m]}" for m in MODES)
        return (
            f"{len(sent)} turnos en «{org.slug}» (zona UTC; {by_mode}): GET /orgs/{{slug}}/usage/anonymizer (totals) = "
            f"el bloque anonymizer de su fila de {PLATFORM} = su elemento de {PLATFORM}/anonymizer?group_by=org en "
            "las 19 columnas y los tres modos; sus 2 días y sus 48 horas suman el total; JSON y CSV: PLATFORM_ADMIN y "
            f"ORG_ADMIN de la organización ({self.other_user}) → 200, USER → 403, ORG_ADMIN de otra "
            f"({self.org_admin}) → 404, sin sesión → 401"
        )

    # --- 16.7 las consultas globales no auditan --------------------------------------------------------
    def audit_viewed(self) -> list[dict[str, Any]]:
        """Todas las filas `usage.viewed` de la auditoría (W10, como PLATFORM_ADMIN; las 200 más recientes)."""
        query = urlencode({"action": VIEWED, "limit": 200})
        res = self.e.webapp("GET", f"/api/v1/audit?{query}", self.admin)
        check(res.status == 200, f"GET /api/v1/audit?{query} ({self.admin}) → {e2e_uso.describe(res)}")
        return [i for i in (res.json() or {}).get("items") or [] if isinstance(i, dict)]

    def not_viewed(self) -> str:
        members = {self.org_admin: "ORG_ADMIN", self.admin: "USER"}  # el PLATFORM_ADMIN solo es USER en ella
        with self.utc_org("vista", members) as org:
            self.uso.send(org, self.org_admin, "messages", f"vista {self.nonce}. Hola.")
            self.uso.wait_requests(org, 1)
            before = viewed_snapshot(self.audit_viewed())
            dates, month = utc_range(), current_month()
            calls = [(PLATFORM, {"month": month}), (PLATFORM, {"month": month, "format": "csv"})]
            for kind in GLOBAL_KINDS:
                calls += [(f"{PLATFORM}/{kind}", {**dates, "group_by": g}) for g in ("org", "day", "org_day")]
                calls.append((f"{PLATFORM}/{kind}", {**dates, "format": "csv"}))
            path = f"/api/v1/orgs/{org.slug}/usage/anonymizer"
            calls += [(path, {**dates, "group_by": "day"}), (path, {**dates, "group_by": "hour"}),
                      (path, {**dates, "format": "csv"})]  # fmt: skip
            problems = []
            for route, params in calls:
                res = self.e.webapp("GET", f"{route}?{urlencode(params)}", self.admin)
                if res.status != 200:
                    problems.append(f"GET {route}?{urlencode(params)} ({self.admin}) → {e2e_uso.describe(res)}")
            check(not problems, "no se pudo consultar (sin consulta, que no haya filas no probaría nada):\n"
                  + "\n".join(problems))  # fmt: skip
            problems = viewed_problems(before, viewed_snapshot(self.audit_viewed()))
            problems += org_viewed_problems(org.slug, self.uso.viewed(org, self.admin))
            check(not problems, "\n".join(problems))
            # control positivo: el detalle por persona de un PLATFORM_ADMIN que no es ORG_ADMIN de la organización sí se
            # audita (12.6), así que la auditoría muestra la fila si existiera
            self.uso.report_json("traffic", org.slug, self.admin, {**self.uso.today(org), "group_by": "user"})
            problems = control_problems(org.slug, self.uso.viewed(org, self.admin))
            check(not problems, "\n".join(problems))
        return (
            f"{len(calls)} consultas del PLATFORM_ADMIN ({PLATFORM} en JSON y CSV, las cuatro rutas globales con org, "
            f"day, org_day y CSV, y el anonimizador de «{org.slug}», donde solo es USER, por día, por hora y en CSV) → "
            f"200 y ninguna fila {VIEWED} nueva en la auditoría de la plataforma ({len(before)} antes) ni en la de la "
            "organización; control: su detalle por persona (traffic, group_by=user) sí deja una"
        )

    # --- 16.8 L2 con el NER --------------------------------------------------------------------------------
    def l2(self) -> str:
        with self.utc_org("l2", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            sent = self.send_plan(org, (("ANONYMIZED", 2), ("OBSERVATION", 1)), text=L2_TEXT)
            row = self.wait_row(org, len(sent))
            self.read_turns(org, sent)
            if not has_l2(sent):
                check(not self.a.ner, ner_declared_message())  # con el NER declarado, que falte `l2` es un fallo
                return no_ner_message()
            problems = state_problems(sent)
            check(not problems, "\n".join(problems))
            want = l2_expected(sent)
            dates = utc_range()
            res = self.e.webapp("GET", f"/api/v1/orgs/{org.slug}/usage/anonymizer?{urlencode(dates)}", self.admin)
            own = (res.json() or {}).get("totals") if res.status == 200 else None
            check(isinstance(own, dict), f"GET …/{org.slug}/usage/anonymizer ({self.admin}) → {e2e_uso.describe(res)}")
            by_org = self.global_report("anonymizer", {**dates, "group_by": "org"})
            glob = next((s for s in by_org["series"] if isinstance(s, dict) and s.get("key") == org.slug), None)
            problems = []
            readings = (
                ("el informe de plataforma", row.get("anonymizer")),
                ("el anonimizador global", glob),
                ("el anonimizador de la organización", own),
            )
            for label, reading in readings:
                problems += [f"{label}: {p}" for p in l2_problems(reading or {}, want)]
            check(not problems, "\n".join(problems))
        by_mode = " · ".join(f"{m} {sum(1 for t in sent if t.mode == m)}" for m in MODES)
        return (
            f"{len(sent)} turnos con NER en «{org.slug}» ({by_mode}): las columnas l2_* del informe de plataforma "
            f"({PLATFORM}), del anonimizador global (…/anonymizer) y del anonimizador de la organización suman lo que "
            f"dice cada turno: l2_turns "
            f"{want['l2_turns']}, l2_enabled {want['l2_enabled']}, l2_applied {want['l2_applied']}, l2_observed_turns "
            f"{want['l2_observed_turns']}, l2_degraded {want['l2_degraded']}; bytes analizados "
            f"{want['l2_analyzed_bytes']}, de caché {want['l2_cached_bytes']}, sin analizar "
            f"{want['l2_unanalyzed_bytes']}, detecciones {want['l2_observed']}"
        )

    # --- el recorrido ----------------------------------------------------------------------------------
    def register(self, runner: Runner, base: list[str]) -> None:
        step = runner.step
        ready = "16.0 la pila tiene el consumo (informe de plataforma con period y datos hasta por tubería)"
        step(ready, self.ready, needs=base)
        base = [*base, ready]
        step("16.1 turnos por modo: bypass, observación y anonimizado cuadran con lo enviado", self.modes, needs=base)
        step("16.2 disco: media, máximo y última; el índice aparte y no suma", self.disk, needs=base)
        step("16.3 el CSV es el JSON (una fila por organización, sin totales)", self.csv, needs=base)
        step("16.4 permisos: 403 al ORG_ADMIN y al USER, 401 sin sesión", self.permissions, needs=base)
        step(
            "16.5 tráfico global: cada organización es su informe y los totales son la suma",
            self.global_sum,
            needs=base,
        )
        step("16.6 anonimizador de la organización = el de plataforma, con sus permisos", self.anonymizer, needs=base)
        step("16.7 las consultas globales y del anonimizador no dejan usage.viewed", self.not_viewed, needs=base)
        step("16.8 con el NER, las columnas de L2 se llenan (se salta sin NER, salvo con --ner)", self.l2, needs=base)
