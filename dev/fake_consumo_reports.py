"""Los informes de consumo de la pila H4 simulada (`dev/fake_h4_stack.py`): lo que la webapp de la Fase 2 calcula con
los turnos que el proxy simulado ha servido, para probar el grupo «consumo» de `dev/e2e_h4.py` sin la pila real.

Contrato de la Fase 2 (`contrato-fase-2-consumo.md`): §7.3 el informe de consumo, §7.4 y §7.7 el tráfico y el
anonimizador globales (los de contenido y disco, con la forma pero sin datos), §7.8 el anonimizador de una organización
y los informes de tráfico de una organización de la Fase 4. Son funciones puras: reciben los turnos de cada organización
(`Entry` = quién y el turno de la pila simulada) y devuelven el JSON; los permisos, la auditoría y los fallos que
cambian lo que se calcula (`faults`) los pone quien las llama. La pila simulada no reparte los turnos por fechas: cada
turno lleva el instante en que se sirvió (`at`, UTC) y los días y las horas salen de él. Las zonas de las
organizaciones no se simulan: todo es UTC.

Solo biblioteca estándar. No es una implementación de referencia: es un doble de pruebas.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

MODES = ("ANONYMIZED", "OBSERVATION", "BYPASS")
SOURCES = ("INVOKED", "SYNCED", "SKILL_READ", "SCRIPT_DOWNLOADED", "KNOWLEDGE_READ", "KNOWLEDGE_SEARCH")
BYTE_KINDS = ("skills", "scripts", "schemas", "knowledge", "versions", "index")
L2_COLUMNS = ("l2_turns", "l2_enabled", "l2_applied", "l2_degraded", "l2_observed_turns", "l2_known_values_only",
              "l2_analyzed_bytes", "l2_cached_bytes", "l2_unanalyzed_bytes", "l2_observed")  # fmt: skip
L2_STATUS = {"APPLIED": "l2_applied", "DEGRADED": "l2_degraded", "OBSERVED": "l2_observed_turns",
             "KNOWN_VALUES_ONLY": "l2_known_values_only"}  # fmt: skip
L2_BYTES = {"l2_analyzed_bytes": "analyzed_bytes", "l2_cached_bytes": "cached_bytes",
            "l2_unanalyzed_bytes": "unanalyzed_bytes", "l2_observed": "observed"}  # fmt: skip
SUMS = ("requests", "blocked", "errors", "tokens_in", "tokens_out", "bytes_in", "bytes_out")
TOP = 10
MAX_DAYS = 400
ANONYMIZER_CSV = ("turns", "anonymized_turns", "observation_turns", "bypass_turns", "tokens_in", "tokens_out",
                  "bytes_in", "replaced_in", "restored_out", "verified_clean", "verified_warning", "verified_error",
                  *L2_COLUMNS)  # fmt: skip

Entry = tuple[str, dict[str, Any]]  # (email de quien envió, turno de la pila simulada)
Result = tuple[int, dict[str, Any]]
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def error(field: str, message: str) -> Result:
    return 422, {"error": "VALIDATION_ERROR", "message": message, "field": field}


def parse_range(
    query: Mapping[str, Sequence[str]], *, groups: Sequence[str], default: str
) -> Result | tuple[date, date, str]:
    """`from`, `to` (por omisión, los últimos 30 días hasta hoy UTC) y `group_by` de un informe (contrato §7.1);
    `422 VALIDATION_ERROR` con el `field` si algo no vale."""
    today = datetime.now(timezone.utc).date()
    values: dict[str, date] = {}
    for name, fallback in (("to", today), ("from", None)):
        raw = (query.get(name) or [None])[0]
        if raw is None:
            values[name] = fallback or values["to"] - timedelta(days=29)
        elif not _DAY.fullmatch(raw):
            return error(name, f"{name} must be YYYY-MM-DD")
        else:
            try:
                values[name] = date.fromisoformat(raw)
            except ValueError:
                return error(name, f"{name} must be a valid date")
    first, last = values["from"], values["to"]
    if first > last:
        return error("from", "from must not be after to")
    if (last - first).days + 1 > MAX_DAYS:
        return error("from", f"the range must not exceed {MAX_DAYS} days")
    group_by = (query.get("group_by") or [default])[0]
    if group_by not in groups:
        return error("group_by", f"group_by must be one of {', '.join(groups)}")
    if (query.get("format") or ["json"])[0] not in ("json", "csv"):
        return error("format", "format must be json or csv")
    return first, last, group_by


def days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def in_range(entries: Sequence[Entry], first: date, last: date) -> list[Entry]:
    return [(who, t) for who, t in entries if first <= t["at"].astimezone(timezone.utc).date() <= last]


def _day(turn: dict[str, Any]) -> date:
    return turn["at"].astimezone(timezone.utc).date()


def _hour(turn: dict[str, Any]) -> datetime:
    return turn["at"].astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


# --- tráfico ---------------------------------------------------------------------------------------------


def traffic(entries: Sequence[Entry]) -> dict[str, Any]:
    """Las diez cifras de tráfico de la Fase 4 (los percentiles no se simulan: `null`). Los tokens son los que declara
    el proveedor falso (12 y 9 por petición)."""
    turns = [t for _, t in entries]
    return {
        "requests": len(turns), "blocked": 0, "errors": 0, "tokens_in": 12 * len(turns), "tokens_out": 9 * len(turns),
        "bytes_in": sum(len(str(t["original"]).encode()) for t in turns),
        "bytes_out": sum(len(str(t["response_received"]).encode()) for t in turns),
        "latency_p50_ms": None, "latency_p95_ms": None, "ttft_avg_ms": None,
    }  # fmt: skip


def active(entries: Sequence[Entry]) -> int:
    return len({who for who, _ in entries})


def _total(items: Sequence[Mapping[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        **{name: sum(item[name] for item in items) for name in SUMS},
        "latency_p50_ms": None, "latency_p95_ms": None, "ttft_avg_ms": None, **extra,
    }  # fmt: skip


def org_item(slug: str, name: str) -> dict[str, Any]:
    return {"key": slug, "label": name, "org": {"slug": slug, "name": name}}


def _envelope(kind: str, first: date, last: date, group_by: str, totals: dict[str, Any], series: list[Any],
              top: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    data: dict[str, Any] = {
        "tz": "UTC", "from": first.isoformat(), "to": last.isoformat(), "group_by": group_by, "data_until": None,
        "period": {"month": None, "from": first.isoformat(), "to": last.isoformat(), "open": True},
        "totals": totals, "series": series,
    }  # fmt: skip
    if top is not None:
        data["top"] = top
    return data


def _pairs(
    orgs: Mapping[str, tuple[str, Sequence[Entry]]],
    first: date,
    last: date,
    size: Callable[[Sequence[Entry]], Any],
    measure: Callable[[Sequence[Entry]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """`org_day` (contrato §7.4): las parejas (organización, día) con datos de las 10 mayores por `size`, y por día una
    fila «(otras)» con las demás sumadas (solo si alguna fuera de las 10 tiene datos ese día)."""
    ranked = sorted(((slug, name, es) for slug, (name, es) in orgs.items() if es),
                    key=lambda r: (-size(r[2]), r[0]))  # fmt: skip
    top = ranked[:TOP]
    others = [e for _slug, _name, es in ranked[TOP:] for e in es]
    series: list[dict[str, Any]] = []
    for day in days(first, last):
        rows = []
        for slug, name, es in top:
            mine = [e for e in es if _day(e[1]) == day]
            if mine:
                rows.append((size(mine), {**org_item(slug, name), "key": f"{slug}:{day}", "day": day.isoformat(),
                                           **measure(mine)}))  # fmt: skip
        rows.sort(key=lambda r: -r[0])
        series += [row for _size, row in rows]
        rest = [e for e in others if _day(e[1]) == day]
        if rest:
            series.append({"key": f"(otras):{day}", "label": "(otras)", "day": day.isoformat(), "org": None,
                           **measure(rest)})  # fmt: skip
    return series, [slug for slug, _name, _es in top]


def global_traffic(orgs: Mapping[str, tuple[str, Sequence[Entry]]], first: date, last: date, group_by: str,
                   *, faults: Collection[str] = ()) -> dict[str, Any]:  # fmt: skip
    """`GET /platform/usage/traffic` (contrato §7.4)."""
    scoped = {slug: (name, in_range(es, first, last)) for slug, (name, es) in orgs.items()}
    if group_by == "org":
        rows = [{**org_item(slug, name), **traffic(es), "active_users": active(es)} for slug, (name, es) in
                scoped.items()]  # fmt: skip
        if "consumo-global-wrong" in faults:  # una petición de más en cada organización con tráfico
            rows = [{**r, "requests": r["requests"] + (r["requests"] > 0)} for r in rows]
        rows.sort(key=lambda r: (-r["requests"], r["key"]))
        totals = {"orgs": len(rows), **_total(rows, active_users=sum(r["active_users"] for r in rows))}
        return _envelope("traffic", first, last, group_by, totals, rows)
    everything = [e for _name, es in scoped.values() for e in es]
    if group_by == "day":
        rows = [{"key": d.isoformat(), "label": d.isoformat(), **traffic([e for e in everything if _day(e[1]) == d])}
                for d in days(first, last)]  # fmt: skip
        return _envelope("traffic", first, last, group_by, {"orgs": len(scoped), **_total(rows)}, rows)
    rows, top = _pairs(scoped, first, last, len, traffic)
    return _envelope("traffic", first, last, group_by, {"orgs": len(scoped), **_total(rows)}, rows, top)


def org_traffic(
    slug: str, name: str, entries: Sequence[Entry], first: date, last: date, group_by: str
) -> dict[str, Any]:
    """`GET /orgs/{slug}/usage/traffic` (Fase 4 §8.3), por día o por persona (con datos)."""
    es = in_range(entries, first, last)
    if group_by == "user":
        who = sorted({w for w, _ in es})
        series = [{"key": w, "label": w, "user": {"id": w, "email": w}, **traffic([e for e in es if e[0] == w])}
                  for w in who]  # fmt: skip
    else:
        series = [{"key": d.isoformat(), "label": d.isoformat(), **traffic([e for e in es if _day(e[1]) == d])}
                  for d in days(first, last)]  # fmt: skip
    return {
        "org": {"slug": slug, "name": name, "timezone": "UTC"}, "tz": "UTC", "from": first.isoformat(),
        "to": last.isoformat(), "hours": None, "users": None, "model": None, "group_by": group_by,
        "data_until": None, "totals": _total(series), "series": series,
    }  # fmt: skip


# --- anonimizador ----------------------------------------------------------------------------------------


def _mode(turn: dict[str, Any], *, observed_as_anonymized: bool) -> str:
    if not turn["ai_ip"]:
        return "BYPASS"
    return "ANONYMIZED" if turn["anonymizer"] or observed_as_anonymized else "OBSERVATION"


def anonymizer(entries: Sequence[Entry], *, faults: Collection[str] = ()) -> dict[str, Any]:
    """El bloque `M` del contrato §6.5 de esos turnos. Con el NER, los `l2_*` salen de `verification.l2` de cada turno
    (§5.2), salvo con el fallo `consumo-l2-empty` (el consumidor no los cuenta)."""
    turns = [t for _, t in entries]
    wrong = "consumo-mode-wrong" in faults
    verifications = [t["verification"] for t in turns if isinstance(t["verification"], dict)]

    def replaced(name: str) -> int:
        return sum(sum(v.get(name, {}).values()) for v in verifications)

    def verified(status: str) -> int:
        return sum(1 for v in verifications if v.get("status") == status)

    l2 = dict.fromkeys(L2_COLUMNS, 0)
    for verification in verifications:
        block = verification.get("l2")
        if not isinstance(block, dict) or "consumo-l2-empty" in faults:
            continue
        l2["l2_turns"] += 1
        l2["l2_enabled"] += block.get("enabled") is True
        if block.get("status") in L2_STATUS:
            l2[L2_STATUS[block["status"]]] += 1
        for column, name in L2_BYTES.items():
            value = block.get(name)
            l2[column] += value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return {
        "turns": len(turns),
        "turns_by_mode": {m: sum(1 for t in turns if _mode(t, observed_as_anonymized=wrong) == m) for m in MODES},
        "tokens_in": 12 * len(turns), "tokens_out": 9 * len(turns),
        "bytes_in": sum(len(str(t["original"]).encode()) for t in turns),
        "replaced_in": replaced("request_replacements_by_type"),
        "restored_out": replaced("response_replacements_by_type"), "verified_clean": verified("CLEAN"),
        "verified_warning": verified("WARNING"), "verified_error": verified("ERROR"), **l2,
    }  # fmt: skip


def _sum_blocks(blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = [k for k in anonymizer([]) if k != "turns_by_mode"]
    return {
        **{name: sum(b[name] for b in blocks) for name in names},
        "turns_by_mode": {m: sum(b["turns_by_mode"][m] for b in blocks) for m in MODES},
    }


def global_anonymizer(orgs: Mapping[str, tuple[str, Sequence[Entry]]], first: date, last: date, group_by: str,
                      *, faults: Collection[str] = ()) -> dict[str, Any]:  # fmt: skip
    """`GET /platform/usage/anonymizer` (contrato §7.7)."""
    scoped = {slug: (name, in_range(es, first, last)) for slug, (name, es) in orgs.items()}

    def block(es: Sequence[Entry]) -> dict[str, Any]:
        return anonymizer(es, faults=faults)

    if group_by == "org":
        rows = [{**org_item(slug, name), **block(es)} for slug, (name, es) in scoped.items()]
        rows.sort(key=lambda r: (-r["turns"], r["key"]))
        return _envelope("anonymizer", first, last, group_by, {"orgs": len(rows), **_sum_blocks(rows)}, rows)
    everything = [e for _name, es in scoped.values() for e in es]
    if group_by == "day":
        rows = [{"key": d.isoformat(), "label": d.isoformat(), **block([e for e in everything if _day(e[1]) == d])}
                for d in days(first, last)]  # fmt: skip
        return _envelope("anonymizer", first, last, group_by, {"orgs": len(scoped), **_sum_blocks(rows)}, rows)
    rows, top = _pairs(scoped, first, last, len, block)
    return _envelope("anonymizer", first, last, group_by, {"orgs": len(scoped), **_sum_blocks(rows)}, rows, top)


def org_anonymizer(slug: str, name: str, entries: Sequence[Entry], first: date, last: date, group_by: str,
                   *, faults: Collection[str] = ()) -> dict[str, Any]:  # fmt: skip
    """`GET /orgs/{slug}/usage/anonymizer` (contrato §7.8): todos los días (o las horas) del rango, con ceros."""
    es = in_range(entries, first, last)
    if group_by == "hour":
        midnight = [datetime.combine(d, datetime.min.time(), timezone.utc) for d in days(first, last)]
        slots = [(m + timedelta(hours=h), lambda t, m=m, h=h: _hour(t) == m + timedelta(hours=h))
                 for m in midnight for h in range(24)]  # fmt: skip
        series = [
            {"key": at.isoformat(), "label": at.isoformat(), **anonymizer([e for e in es if mine(e[1])], faults=faults)}
            for at, mine in slots
        ]
    else:
        series = [
            {"key": d.isoformat(), "label": d.isoformat(),
             **anonymizer([e for e in es if _day(e[1]) == d], faults=faults)}
            for d in days(first, last)
        ]  # fmt: skip
    if "consumo-anonymizer-wrong" in faults:  # pierde los turnos en observación (no cuadra con el de plataforma)
        for item in series:
            item["turns_by_mode"] = {**item["turns_by_mode"], "OBSERVATION": 0}
    return {
        "org": {"slug": slug, "name": name, "timezone": "UTC"}, "tz": "UTC", "from": first.isoformat(),
        "to": last.isoformat(), "group_by": group_by, "data_until": None,
        "period": {"from": first.isoformat(), "to": last.isoformat(), "open": True},
        "totals": _sum_blocks(series), "series": series,
    }  # fmt: skip


def anonymizer_csv(series: Sequence[Mapping[str, Any]]) -> str:
    """El CSV del anonimizador de una organización (§7.9): BOM, `\\r\\n`, `key`, `label` y `turns`…`l2_observed`."""
    by_mode = {"anonymized_turns": "ANONYMIZED", "observation_turns": "OBSERVATION", "bypass_turns": "BYPASS"}
    lines = [",".join(("key", "label", *ANONYMIZER_CSV))]
    for item in series:
        cells = [item["key"], item["label"]]
        for column in ANONYMIZER_CSV:
            cells.append(item["turns_by_mode"][by_mode[column]] if column in by_mode else item[column])
        lines.append(",".join(str(c) for c in cells))
    return "﻿" + "\r\n".join(lines) + "\r\n"


# --- contenido y disco: la forma, sin datos ------------------------------------------------------------------


def global_empty(kind: str, orgs: Mapping[str, tuple[str, Sequence[Entry]]], first: date, last: date,
                 group_by: str) -> dict[str, Any]:  # fmt: skip
    """`GET /platform/usage/content` y `/storage` (§7.5 y §7.6) sin datos: la forma, con ceros (los pasos que los
    consultan no miran lo que traen). Las parejas (organización, día) quedan vacías."""
    zero_counts = dict.fromkeys(SOURCES, 0)
    if kind == "content":
        item = {"counts": zero_counts, "skills": None, "skills_capped": False, "areas": None, "areas_capped": False}
        totals: dict[str, Any] = {"orgs": len(orgs), "counts": zero_counts, "skills": 0, "skills_capped": False,
                                  "areas": 0, "areas_capped": False}  # fmt: skip
    else:
        item = {"taken_at": None, "files": None, "bytes": None, "billable_bytes": None,
                "disk": {"avg": None, "max": None, "last": None, "index_last": None, "snapshots": 0,
                         "first_taken_at": None, "taken_at": None}}  # fmt: skip
        totals = {"orgs": len(orgs), "files": 0, "bytes": dict.fromkeys(BYTE_KINDS, 0), "billable_bytes": 0,
                  "disk": {"avg": 0, "last": 0, "index_last": 0}}  # fmt: skip
    if group_by == "org":
        series: list[dict[str, Any]] = [{**org_item(slug, name), **item} for slug, (name, _es) in sorted(orgs.items())]
    elif group_by == "day" and kind == "content":
        series = [{"key": d.isoformat(), "label": d.isoformat(), "counts": zero_counts} for d in days(first, last)]
    else:
        series = []
    return _envelope(kind, first, last, group_by, totals, series, [] if group_by == "org_day" else None)


# --- el informe de consumo ----------------------------------------------------------------------------------


def consumption_row(slug: str, name: str, blocked: bool, entries: Sequence[Entry], *,
                    faults: Collection[str] = ()) -> dict[str, Any]:  # fmt: skip
    """Una fila de `GET /platform/usage` (§7.3) con tráfico y anonimizador. No simula el disco ni el contenido."""
    t = traffic(entries)
    return {
        "slug": slug, "name": name, "timezone": "Europe/Madrid", "blocked": blocked, "paused": False,
        "requests": t["requests"], "blocked_requests": t["blocked"], "errors": t["errors"],
        "tokens_in": t["tokens_in"], "tokens_out": t["tokens_out"], "bytes_in": t["bytes_in"],
        "bytes_out": t["bytes_out"], "active_users": active(entries), "invocations": 0, "storage_bytes": None,
        "storage_taken_at": None, "anonymizer": anonymizer(entries, faults=faults),
    }  # fmt: skip
