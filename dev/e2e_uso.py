"""Grupo «uso» (pasos 12.0–12.10) de `dev/e2e_h4.py`: la observabilidad de uso de la Fase 4 contra la pila real.

Un paso por criterio de aceptación del diseño (§8) y del contrato (`contrato-fase-4-uso.md`, §11):

  12.1  tráfico: N peticiones por el proxy (Messages, Chat y Responses) aparecen en `usage/traffic` con sus tokens,
        sus bytes exactos y la misma cifra en la franja horaria local, por hora, por día de la semana, por usuario y
        por modelo.
  12.2  `/<org>:<skill>` (envuelto en `<command-name>` como lo envía Claude Code) y la herramienta `Skill` (disparador
        del proveedor falso) cuentan `INVOKED` una vez cada una; otra organización u otro plugin no cuentan.
  12.3  una sincronización de paquete (como el plugin: listado, manifiesto y ficheros) cuenta `SYNCED`, no `INVOKED`.
  12.4  una lectura de conocimiento suma a su área y ningún dato (evento de `baas:usage`, filas, API ni logs) lleva la
        ruta ni el nombre del fichero; `baas:usage` no existe en la Redis efímera y sí en la persistente.
  12.5  la foto de disco coincide con el volumen del MCP (las seis categorías del contrato §4) con margen del 1 %.
  12.6  permisos (401, 403 del USER, 404 del ORG_ADMIN ajeno), aislamiento entre organizaciones, el PLATFORM_ADMIN lo
        ve todo y su consulta por persona queda en la auditoría (`usage.viewed`).
  12.7  con el consumidor de la webapp y el MCP parados el proxy responde igual y sin tardar más; «datos hasta» se queda
        atrás y, al volver, se pone al día sin contar dos veces.
  12.8  retención: una métrica más antigua que la retención se borra en la purga; una conversación borrada sigue
        contando; eliminar la organización borra sus métricas en el acto.
  12.9  un evento repetido (`content.usage`, `storage.snapshot` y uno de traza) no se cuenta dos veces.
  12.10 el sobrecoste del proxy (frente al proveedor falso directo) no empeora.

Cada paso crea su organización (`uso-<ejecución>-<paso>`, con usuarios de la semilla de desarrollo como miembros) y la
elimina al terminar. Los pasos 12.4, 12.5, 12.7, 12.8 y 12.9 miran o tocan los contenedores (`--inspect`).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import secrets
import statistics
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import e2e_h3 as h3
from e2e_h2 import Http, StepError, check, dev_api_key

from baas_plugin.mcp_client import McpClient

if TYPE_CHECKING:
    from e2e_h2 import Runner
    from e2e_h4 import E2EH4

SOURCES = ("INVOKED", "SYNCED", "SKILL_READ", "SCRIPT_DOWNLOADED", "KNOWLEDGE_READ", "KNOWLEDGE_SEARCH")
CATEGORIES = ("skills", "scripts", "schemas", "knowledge", "versions", "index")
SCHEMA_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".sql", ".xsd"})
DISK_MARGIN = 0.01  # contrato §4: ≤ 1 % en el e2e (el disco puede cambiar mientras se mide)
OVERHEAD_BODY = 2000  # bytes del texto de cada petición de 12.10 (~2 KB, como un cuerpo corriente de Claude Code)
USAGE_STREAM, TRACES_STREAM = "baas:usage", "baas:traces"
PERSISTENT, EPHEMERAL = "redis", "redis-ephemeral"
CONSUMER, MCP = "webapp-api", "mcp-server"
REASON = "Prueba de punta a punta del grupo uso (F4)"
OTHER_ADMIN = "orgadmin@privada.test"  # ORG_ADMIN de una organización distinta (12.6)
OTHER_USER = "analista@privada.test"
# 12.8: la retención se pone en el máximo (60 meses) y no por debajo de la vigente: la purga es global y así no borra
# métricas de otras organizaciones de la pila que la retención normal (13 meses) conserva
TEST_RETENTION = 60
LATENCY_FACTOR, LATENCY_P50_MS, LATENCY_P95_MS = 1.5, 5.0, 10.0  # «sin degradarse»: margen relativo y absoluto
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENTRY_ID = re.compile(r"^[0-9]+-[0-9]+$")


# --- utilidades puras ----------------------------------------------------------------------------


def disk_category(path: str) -> str | None:
    """Categoría de un fichero regular de `orgs/<slug>/` (ruta relativa con «/») según el contrato §4, o `None` si
    queda fuera de la medición (`.uploads/`, `.lock`, `.tmp/` y cualquier otra entrada de la raíz)."""
    parts = path.split("/")
    if len(parts) == 1:
        name = parts[0]
        return "index" if name == "index.sqlite" or name.startswith("index.sqlite-") else None
    top = parts[0]
    if top == ".versions":
        return "versions"
    if top not in ("skills", "knowledge"):
        return None
    if top == "skills" and len(parts) >= 4 and parts[2] == "scripts":
        return "scripts"
    if os.path.splitext(parts[-1])[1].lower() in SCHEMA_EXTENSIONS:
        return "schemas"
    return top


def disk_expected(files: Iterable[tuple[str, int]]) -> dict[str, int]:
    """Lo que la foto debería decir de esos ficheros: cuántos cuentan y los bytes de cada categoría."""
    usage = {"files": 0, **dict.fromkeys(CATEGORIES, 0)}
    for path, size in files:
        category = disk_category(path)
        if category is not None:
            usage["files"] += 1
            usage[category] += size
    return usage


def disk_total(usage: dict[str, int]) -> int:
    return sum(usage.get(c, 0) for c in CATEGORIES)


def snapshot_usage(latest: Any) -> dict[str, int]:
    """`latest` de `usage/storage` en la forma de `disk_expected` (vacío si no hay foto)."""
    if not isinstance(latest, dict):
        return {}
    data = latest.get("bytes") if isinstance(latest.get("bytes"), dict) else {}
    return {"files": latest.get("files"), **{c: data.get(c) for c in CATEGORIES}}  # type: ignore[dict-item]


def disk_problems(expected: dict[str, int], got: dict[str, Any], margin: float = DISK_MARGIN) -> list[str]:
    """Diferencias de más de `margin` (relativo) en el total y en cada categoría; el número de ficheros, exacto."""
    problems = []
    for key in ("files", *CATEGORIES):
        value = got.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"{key}: la foto no trae un entero ({value!r})")
            continue
        want = expected[key]
        allowed = 0 if key == "files" else margin * want
        if abs(value - want) > allowed:
            problems.append(f"{key}: la foto dice {value}, el volumen {want}")
    total = disk_total({c: got[c] for c in CATEGORIES if isinstance(got.get(c), int)})
    if abs(total - disk_total(expected)) > margin * disk_total(expected):
        problems.append(f"total: la foto dice {total}, el volumen {disk_total(expected)}")
    return problems


def hour_window(first: int, last: int) -> tuple[str, str]:
    """`hours` (`H1-H2`, contrato §8.1) que cubre de la hora local `first` a la `last` (ambas incluidas; si `last` es
    menor, cruza la medianoche) y la franja complementaria."""
    inside = f"{first}-{last + 1}"
    outside = f"{(last + 1) % 24}-{first or 24}"
    return inside, outside


def hour_key(at: datetime) -> str:
    """Clave de `group_by=hour`: la hora local con su desplazamiento (`2026-11-18T23:00:00+01:00`)."""
    return at.replace(minute=0, second=0, microsecond=0).isoformat()


def weekday_hour_key(at: datetime) -> str:
    """Clave de `group_by=weekday_hour`: `<día ISO>-<HH>` (`1-09` = lunes a las 9)."""
    return f"{at.isoweekday()}-{at.hour:02d}"


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sse_data(body: bytes) -> list[Any]:
    events = []
    for _, data in h3.parse_sse(body.decode(errors="replace")):
        with contextlib.suppress(ValueError):
            events.append(json.loads(data))
    return events


def reply_tokens(api: str, body: bytes, *, stream: bool) -> tuple[int, int] | None:
    """Los tokens (entrada, salida) que el proveedor declaró en su respuesta, o `None` si no los declaró."""
    if not stream:
        try:
            usage = (json.loads(body) or {}).get("usage")
        except (ValueError, AttributeError):
            return None
        if not isinstance(usage, dict):
            return None
        names = ("prompt_tokens", "completion_tokens") if api == "chat" else ("input_tokens", "output_tokens")
        got = (_int(usage.get(names[0])), _int(usage.get(names[1])))
        return (got[0], got[1]) if got[0] is not None and got[1] is not None else None
    tokens_in = tokens_out = None
    for event in _sse_data(body):
        if not isinstance(event, dict):
            continue
        if api == "messages":
            if event.get("type") == "message_start":
                tokens_in = _int(((event.get("message") or {}).get("usage") or {}).get("input_tokens"))
            elif event.get("type") == "message_delta":
                tokens_out = _int((event.get("usage") or {}).get("output_tokens"))
        elif api == "chat" and isinstance(event.get("usage"), dict):
            tokens_in, tokens_out = (
                _int(event["usage"].get("prompt_tokens")),
                _int(event["usage"].get("completion_tokens")),
            )
        elif api == "responses" and event.get("type") == "response.completed":
            usage = (event.get("response") or {}).get("usage") or {}
            tokens_in, tokens_out = _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
    return (tokens_in, tokens_out) if tokens_in is not None and tokens_out is not None else None


def quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def overhead(direct: Sequence[float], proxied: Sequence[float]) -> dict[str, float]:
    """Sobrecoste del proxy: la diferencia de las medianas y de los p95 (ms) entre ir por el proxy y directo."""
    got = {
        "direct_p50": median(direct),
        "direct_p95": quantile(direct, 0.95),
        "proxy_p50": median(proxied),
        "proxy_p95": quantile(proxied, 0.95),
    }
    return {**got, "p50": got["proxy_p50"] - got["direct_p50"], "p95": got["proxy_p95"] - got["direct_p95"]}


def degradation(before: Sequence[float], during: Sequence[float]) -> list[str]:
    """Si `during` tarda más que `before` más allá del margen (×1,5 y +5 ms en la mediana, +10 ms en el p95)."""
    problems = []
    for name, fn, extra in (("p50", median, LATENCY_P50_MS), ("p95", lambda v: quantile(v, 0.95), LATENCY_P95_MS)):
        was, now = fn(before), fn(during)
        if now > was * LATENCY_FACTOR + extra:
            problems.append(f"{name}: {now:.1f} ms con las piezas paradas frente a {was:.1f} ms antes")
    return problems


def stream_entries(raw: str) -> list[tuple[str, str]]:
    """Entradas (`id`, `data`) de la salida de `redis-cli --raw XRANGE` (un campo `data` por entrada, en una línea)."""
    lines = raw.replace("\r", "").split("\n")
    entries = []
    for i in range(len(lines) - 2):
        if _ENTRY_ID.fullmatch(lines[i]) and lines[i + 1] == "data":
            entries.append((lines[i], lines[i + 2]))
    return entries


def events_of(entries: Iterable[tuple[str, str]], kind: str, org_slug: str) -> list[tuple[str, dict[str, Any], str]]:
    """Los eventos `kind` de `org_slug` de un stream: (id de la entrada, el sobre y su texto tal cual)."""
    found = []
    for entry_id, data in entries:
        with contextlib.suppress(ValueError):
            event = json.loads(data)
            if isinstance(event, dict) and event.get("type") == kind and event.get("org_slug") == org_slug:
                found.append((entry_id, event, data))
    return found


def group_info(raw: str, group: str) -> dict[str, str] | None:
    """El grupo `group` de la salida de `redis-cli --raw XINFO GROUPS` (pares nombre/valor), o `None`."""
    lines = [line for line in raw.replace("\r", "").split("\n") if line != ""]
    current: dict[str, str] = {}
    groups = []
    for name, value in zip(lines[::2], lines[1::2], strict=False):
        if name == "name":
            current = {}
            groups.append((value, current))
        else:
            current[name] = value
    return next((info for name, info in groups if name == group), None)


def new_event_id() -> str:
    """Un ULID (26 caracteres de Crockford): 48 bits de milisegundos y 80 aleatorios."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def follow_up(first: Any, response: bytes, next_content: Any) -> list[dict[str, Any]]:
    """Mensajes del segundo turno de una conversación de Messages, como los envía Claude Code: el primero del
    usuario, la respuesta del modelo **tal cual** (con su `tool_use`, para que el proxy correlacione el turno) y el
    contenido nuevo del usuario (texto o `tool_result`)."""
    try:
        content = json.loads(response).get("content")
    except (ValueError, AttributeError):
        content = None
    if not isinstance(content, list) or not content:
        raise StepError(f"la respuesta del primer turno no trae «content»: {h3.snippet(response)}")
    return [
        {"role": "user", "content": first},
        {"role": "assistant", "content": content},
        {"role": "user", "content": next_content},
    ]


def tool_result_for(response: bytes, text: str) -> list[dict[str, Any]]:
    """El `tool_result` que contesta al `tool_use` de la respuesta (lo que Claude Code devuelve tras lanzar la
    skill: «Launching skill: <org>:<skill>»)."""
    try:
        blocks = json.loads(response).get("content") or []
    except (ValueError, AttributeError):
        blocks = []
    uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not uses:
        raise StepError(f"la respuesta no trae ningún tool_use: {h3.snippet(response)}")
    return [{"type": "tool_result", "tool_use_id": uses[0].get("id"), "content": text}]


def second_turn_problems(org_slug: str, skill: str, request_original: Any) -> list[str]:
    """12.2: lo que le falta al `request_original` del segundo turno de la herramienta (texto o segmentos de la vista
    del turno) para que «no hay doble recuento» pruebe algo: empezar por el `[tool_use: Skill]` de la respuesta anterior
    y traer el `tool_result` «Launching skill: <org>:<skill>»."""
    if isinstance(request_original, list):
        text = "".join(str(s.get("text") or s.get("token") or "") for s in request_original if isinstance(s, dict))
    else:
        text = request_original if isinstance(request_original, str) else ""
    name = f"{org_slug}:{skill}"
    problems = []
    if f'[tool_use: Skill] {{"skill":"{name}"}}' not in text:
        problems.append(f'el request_original del segundo turno no trae «[tool_use: Skill] {{"skill":"{name}"}}»')
    if f"Launching skill: {name}" not in text:
        problems.append(f"el request_original del segundo turno no trae «Launching skill: {name}»")
    return problems


def counts_by_key(series: Any) -> dict[str, dict[str, int]]:
    """`{key: {source: n}}` de las series de `usage/content`, solo con los recuentos distintos de cero."""
    out: dict[str, dict[str, int]] = {}
    for item in series or []:
        if isinstance(item, dict):
            counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
            out[str(item.get("key"))] = {k: v for k, v in counts.items() if v}
    return out


def leaks(raw: str | bytes, needles: Iterable[str]) -> list[str]:
    """Qué `needles` aparecen en `raw`, tal cual o en las cadenas de su JSON (con escapes)."""
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    decoded = ""
    with contextlib.suppress(ValueError):
        decoded = "\n".join(h3.strings_in(json.loads(text)))
    return [n for n in needles if n in text or n in decoded]


def local_now(tz: ZoneInfo) -> datetime:
    """La hora local de la organización (aparte, para poder simular el reloj en los tests)."""
    return datetime.now(tz)


def phase4_problem(status: int) -> str | None:
    """12.0: qué decir si `GET …/usage/traffic` (como PLATFORM_ADMIN, de una organización que existe) no da 200."""
    if status == 200:
        return None
    if status == 404:
        return (
            "pila sin la Fase 4 (observabilidad de uso): reconstruye con los repos de la Fase 4 (webapp, mcp-server, "
            "llm-interceptor-proxy y deploy); GET …/usage/traffic → 404"
        )
    return f"GET …/usage/traffic → HTTP {status} (se esperaba 200): ¿la pila tiene la Fase 4?"


def retention_warning(previous: int) -> str:
    """12.8: si la retención ya era la de la prueba, seguramente la dejó así una ejecución anterior cortada (SIGKILL)
    sin restaurarla: se avisa en el detalle del paso para que alguien la ponga a mano en su valor (13 por defecto)."""
    if previous != TEST_RETENTION:
        return ""
    return (
        f". AVISO: la retención ya estaba en {TEST_RETENTION} meses al empezar (¿una ejecución anterior cortada?): "
        "ponla a mano en su valor (13 por defecto) en Plataforma → Uso"
    )


def rfc3339(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_instant(text: Any) -> datetime | None:
    """Un instante de la API (`…Z`) o de PostgreSQL (`2026-11-18 22:19:20.549+00`)."""
    if not isinstance(text, str) or not text:
        return None
    value = text.strip().replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", value):
        value += ":00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- la prueba ------------------------------------------------------------------------------------


@dataclass
class UsoOrg:
    """Una organización de un paso: su slug, su id, su zona y el handshake de cada miembro."""

    slug: str
    org_id: str
    tz: ZoneInfo
    bindings: dict[str, dict[str, Any]] = field(default_factory=dict)


class Timed:
    """Una conexión persistente (http.client) para medir latencias de cliente."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float) -> None:
        parts = urlsplit(url)
        self.path = parts.path
        self.conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=timeout)
        self.headers = {"Content-Type": "application/json", **headers}

    def post(self, payload: dict[str, Any]) -> tuple[float, int, bytes]:
        body = h3.encode(payload)
        start = time.perf_counter()
        self.conn.request("POST", self.path, body=body, headers=self.headers)
        response = self.conn.getresponse()
        data = response.read()
        return (time.perf_counter() - start) * 1000, response.status, data

    def close(self) -> None:
        self.conn.close()


class UsoGroup:
    slug_prefix = "uso"  # `<prefijo>-<ejecución>-<paso>`: otros grupos (consumo) reutilizan los ayudantes con el suyo

    def __init__(self, e2e: E2EH4) -> None:
        self.e = e2e
        self.a = e2e.args
        self.nonce = e2e.nonce
        self.user_ids: dict[str, str] = {}
        self.figures: dict[str, Any] = {}  # cifras del paso 12.10 (y de 12.7) para el informe

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

    def logins(self) -> list[str]:
        return [self.admin, self.org_admin, self.user, OTHER_ADMIN]

    def inspector(self) -> Any:
        assert self.e.inspector is not None  # los pasos que lo usan solo se registran con inspector
        return self.e.inspector

    def key(self, email: str) -> str:
        if email not in self.e.keys:
            self.e.keys[email] = dev_api_key(email)
        return self.e.keys[email]

    def user_id(self, email: str) -> str:
        if email not in self.user_ids:
            res = self.e.webapp("GET", "/api/v1/me", email)
            check(res.status == 200, f"GET /api/v1/me ({email}) → {describe(res)}")
            self.user_ids[email] = str((res.json() or {}).get("id") or "")
            check(self.user_ids[email], f"GET /api/v1/me ({email}) no trae el id")
        return self.user_ids[email]

    def wait(self, fn: Callable[[], list[str]], timeout: float, what: str, every: float = 1.0) -> None:
        """Repite `fn` (devuelve problemas) cada `every` s hasta que no haya ninguno; si no, falla con los últimos."""
        deadline = time.monotonic() + timeout
        while True:
            problems = fn()
            if not problems:
                return
            if time.monotonic() > deadline:
                raise StepError(f"{what} tras {timeout:g} s:\n" + "\n".join(problems))
            time.sleep(every)

    # --- organizaciones de cada paso ----------------------------------------------------------------
    def create_org(self, label: str, members: dict[str, str], name: str | None = None) -> UsoOrg:
        slug = f"{self.slug_prefix}-{self.nonce}-{label}"
        res = self.e.admin_post("/api/v1/orgs", {"slug": slug, "name": name or f"E2E uso {label} {self.nonce}"})
        check(res.status == 201, f"POST /api/v1/orgs ({slug}) → {describe(res)}")
        data = (res.json() or {}).get("organization") or {}
        org = UsoOrg(slug, str(data.get("id") or ""), ZoneInfo(str(data.get("timezone") or "Europe/Madrid")))
        try:
            check(org.org_id, f"POST /api/v1/orgs no devolvió el id de «{slug}»")
            self.add_members(org, members)
        except BaseException:
            with contextlib.suppress(Exception):  # no dejar una organización a medias
                self.drop_org(slug)
            raise
        return org

    def add_members(self, org: UsoOrg, members: dict[str, str]) -> None:
        slug = org.slug
        for email, role in members.items():
            res = self.e.admin_post(f"/api/v1/orgs/{slug}/members", {"email": email, "governance_role": role})
            check(res.status == 201, f"añadir a {email} ({role}) a «{slug}» → {describe(res)}")
            member = (res.json() or {}).get("member") or {}
            if member.get("user_id"):
                self.user_ids[email] = str(member["user_id"])
            self.key(email)
            org.bindings[email] = self.e.handshake(email, slug, ai_ip=False)

    def drop_org(self, slug: str) -> None:
        """Bloquea y elimina la organización (sin desactivar a nadie: los miembros son de la semilla)."""
        res = self.e.admin_post(f"/api/v1/orgs/{slug}/block", {"blocked": True, "reason": REASON}, method="PUT")
        if res.status == 404:
            return
        check(res.status == 200, f"bloquear «{slug}» → {describe(res)}")
        body = self.e.delete_body(reason=REASON, confirm_slug=slug, disable_orphans=False)
        res = self.e.admin_post(f"/api/v1/orgs/{slug}/delete", body)
        check(res.status == 200, f"eliminar «{slug}» → {describe(res)}")

    @contextlib.contextmanager
    def org(self, label: str, members: dict[str, str], name: str | None = None) -> Iterator[UsoOrg]:
        org = self.create_org(label, members, name)
        failed = False
        try:
            yield org
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self.drop_org(org.slug)
            except Exception as exc:
                if not failed:
                    raise StepError(f"no se pudo eliminar «{org.slug}» al terminar: {exc}") from None
                print(f"          (no se pudo eliminar «{org.slug}»: {exc})")

    # --- la API de uso -----------------------------------------------------------------------------
    def report(self, kind: str, slug: str, who: str | None, params: dict[str, Any] | None = None) -> Http:
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        path = f"/api/v1/orgs/{slug}/usage/{kind}" + (f"?{query}" if query else "")
        if who is None:
            return h3.http("GET", f"{self.a.app_url}{path}", timeout=self.a.timeout)
        return self.e.webapp("GET", path, who)

    def report_json(self, kind: str, slug: str, who: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        res = self.report(kind, slug, who, params)
        data = res.json()
        check(
            res.status == 200 and isinstance(data, dict),
            f"GET …/{slug}/usage/{kind}?{urlencode(params or {})} ({who}) → {describe(res)}",
        )
        return data

    def wait_report(
        self,
        kind: str,
        slug: str,
        who: str,
        params: dict[str, Any],
        problems: Callable[[dict[str, Any]], list[str]],
        what: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        got: dict[str, Any] = {}

        def check_once() -> list[str]:
            got["data"] = self.report_json(kind, slug, who, params)
            return problems(got["data"])

        self.wait(check_once, self.a.trace_timeout if timeout is None else timeout, what)
        return got["data"]

    def today(self, org: UsoOrg, start: datetime | None = None) -> dict[str, str]:
        """`from`/`to` locales que cubren desde `start` hasta ahora; sin `start`, desde **ayer**: una petición enviada a
        las 23:59:5x y consultada a las 00:00:0x sigue dentro (cada paso crea su organización, sin histórico)."""
        now = local_now(org.tz)
        first = (start or now - timedelta(days=1)).astimezone(org.tz)
        return {"from": first.date().isoformat(), "to": now.date().isoformat()}

    def totals(self, org: UsoOrg, who: str | None = None, **params: Any) -> dict[str, Any]:
        data = self.report_json("traffic", org.slug, who or self.org_admin, {**self.today(org), **params})
        return data.get("totals") or {}

    def wait_requests(self, org: UsoOrg, n: int, who: str | None = None) -> dict[str, Any]:
        """Espera a que el informe de tráfico tenga exactamente `n` peticiones (todas las trazas consumidas)."""
        params = self.today(org)
        return self.wait_report(
            "traffic",
            org.slug,
            who or self.org_admin,
            params,
            lambda d: (
                []
                if (d.get("totals") or {}).get("requests") == n
                else [f"requests = {(d.get('totals') or {}).get('requests')!r} (se esperaba {n})"]
            ),
            f"el informe de tráfico de «{org.slug}» no llega a {n} petición(es)",
        )

    def content(self, org: UsoOrg, group_by: str, who: str | None = None) -> dict[str, Any]:
        return self.report_json("content", org.slug, who or self.org_admin, {**self.today(org), "group_by": group_by})

    def wait_sources(self, org: UsoOrg, want: dict[str, int], timeout: float | None = None) -> dict[str, Any]:
        """Espera a que los totales de `usage/content` sean exactamente `want` (las demás `source`, cero)."""
        expected = {s: want.get(s, 0) for s in SOURCES}
        return self.wait_report(
            "content",
            org.slug,
            self.org_admin,
            {**self.today(org), "group_by": "source"},
            lambda d: (
                []
                if {s: (d.get("totals") or {}).get(s) for s in SOURCES} == expected
                else [f"totales {d.get('totals')} (se esperaba {expected})"]
            ),
            f"el informe de contenido de «{org.slug}» no cuadra",
            self.a.usage_timeout if timeout is None else timeout,
        )

    # --- el proxy y el MCP -------------------------------------------------------------------------
    def send(
        self, org: UsoOrg, who: str, api: str, text: str, *, stream: bool = False, model: str = ""
    ) -> tuple[bytes, Http]:
        model = model or f"uso-{self.nonce}-{api}-{'stream' if stream else 'json'}"
        payload = h3.request_body(api, model, text, stream=stream)
        raw, res = self.e.send(org.bindings[who], who, api, payload)
        check(res.status == 200, f"{h3.API_NAMES[api]} en «{org.slug}» → HTTP {res.status}: {h3.snippet(res.body)}")
        return raw, res

    @contextlib.contextmanager
    def mcp(self, org: UsoOrg, who: str) -> Iterator[McpClient]:
        with McpClient(str(org.bindings[who]["mcp_url"]), self.key(who), timeout=self.a.timeout) as client:
            yield client

    def call(self, client: McpClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return client.call_tool(tool, args)
        except Exception as exc:
            raise StepError(f"{tool}: {type(exc).__name__}: {exc}") from None

    def write_skill(self, org: UsoOrg, skill: str, extra: dict[str, str] | None = None) -> None:
        """Publica una skill (como ORG_ADMIN) con `baas_write_files`: SKILL.md y `extra` (ruta relativa → texto)."""
        body = f"---\ntitle: {skill}\ntype: skill\nroles: []\nstatus: active\ndescription: Prueba {self.nonce}.\n---\n"
        files = [{"path": f"skills/{skill}/SKILL.md", "content": body + f"# {skill}\n"}]
        files += [{"path": f"skills/{skill}/{p}", "content": c} for p, c in (extra or {}).items()]
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_files", {"files": files})

    def write_doc(self, org: UsoOrg, path: str, text: str) -> None:
        content = f"---\ntitle: Prueba {self.nonce}\ntype: knowledge\nroles: []\n---\n{text}\n"
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_document", {"path": path, "content": content})

    # --- contenedores --------------------------------------------------------------------------------
    def wait_webapp(self) -> None:
        def problems() -> list[str]:
            try:
                res = h3.http("GET", f"{self.a.app_url}/healthz", timeout=5)
                me = self.e.webapp("GET", "/api/v1/me", self.admin) if res.status == 200 else None
            except OSError as exc:
                return [f"{type(exc).__name__}: {exc}"]
            if res.status != 200 or me is None or me.status != 200:
                return [f"/healthz → HTTP {res.status}" + (f", /api/v1/me → HTTP {me.status}" if me else "")]
            return []

        self.wait(problems, self.a.usage_timeout, "la webapp no vuelve a responder")

    def wait_mcp(self, org: UsoOrg) -> None:
        def problems() -> list[str]:
            try:
                with self.mcp(org, self.org_admin) as client:
                    client.call_tool("baas_whoami", {})
            except Exception as exc:
                return [f"{type(exc).__name__}: {exc}"]
            return []

        self.wait(problems, self.a.usage_timeout, "el MCP no vuelve a responder")

    @contextlib.contextmanager
    def paused_consumer(self) -> Iterator[None]:
        """Congela la webapp (`pause`): lo que llega a los streams se queda en ellos hasta reanudarla."""
        insp = self.inspector()
        insp.service_control(CONSUMER, "pause")
        try:
            yield
        finally:
            insp.service_control(CONSUMER, "unpause")

    def capture(
        self, service: str, stream: str, kind: str, slug: str, enough: Callable[[list[Any]], bool], what: str
    ) -> list[tuple[str, dict[str, Any], str]]:
        """Espera (con la webapp congelada) a que `stream` tenga los eventos `kind` de `slug` que pide `enough`."""
        got: dict[str, Any] = {}

        def problems() -> list[str]:
            got["events"] = events_of(self.inspector().stream_entries(service, stream), kind, slug)
            return [] if enough(got["events"]) else [f"{len(got['events'])} evento(s) {kind} de «{slug}» en {stream}"]

        self.wait(problems, self.a.usage_timeout, what, every=0.5)
        return list(got["events"])

    # --- 12.0 la pila tiene la Fase 4 -----------------------------------------------------------------
    def phase4(self) -> str:
        res = self.report("traffic", "baas-ai-ip", self.admin)  # la organización meta existe siempre
        problem = phase4_problem(res.status)
        check(problem is None, f"{problem}: {h3.snippet(res.body, 200)}")
        return "GET /api/v1/orgs/baas-ai-ip/usage/traffic → 200 (la API de uso existe)"

    # --- 12.1 tráfico -------------------------------------------------------------------------------
    def traffic(self) -> str:
        cases = [
            (api, stream)
            for api, stream in (("messages", False), ("messages", True), ("chat", False), ("responses", False))
            if api in self.a.apis
        ]
        check(cases, "--apis no deja ninguna API que probar")
        with self.org("tr", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            start = datetime.now(org.tz)
            sent = received = tokens_in = tokens_out = 0
            models: dict[str, str] = {}
            for i, (api, stream) in enumerate(cases):
                raw, res = self.send(org, self.user, api, f"trafico {self.nonce} {i}. Hola.", stream=stream)
                tokens = reply_tokens(api, res.body, stream=stream)
                check(tokens, f"{h3.API_NAMES[api]}: la respuesta del proveedor no declara los tokens")
                sent, received = sent + len(raw), received + len(res.body)
                tokens_in, tokens_out = tokens_in + tokens[0], tokens_out + tokens[1]  # type: ignore[index]
                models[f"uso-{self.nonce}-{api}-{'stream' if stream else 'json'}"] = (
                    "ANTHROPIC" if api == "messages" else "OPENAI"
                )
            end = datetime.now(org.tz)
            n = len(cases)
            data = self.wait_requests(org, n)
            totals = data.get("totals") or {}
            want = {
                "requests": n,
                "blocked": 0,
                "errors": 0,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "bytes_in": sent,
                "bytes_out": received,
            }
            problems = [f"{k} = {totals.get(k)!r} (se esperaba {v})" for k, v in want.items() if totals.get(k) != v]
            if not isinstance(totals.get("latency_p50_ms"), int):
                problems.append(f"latency_p50_ms = {totals.get('latency_p50_ms')!r} (se esperaba un entero)")
            dates = self.today(org, start)
            inside, outside = hour_window(start.hour, end.hour)
            got = self.totals(org, **dates, hours=inside).get("requests")
            if got != n:
                problems.append(f"en la franja local {inside} ({org.tz.key}): requests = {got!r} (se esperaba {n})")
            got = self.totals(org, **dates, hours=outside).get("requests")
            if got != 0:
                problems.append(f"fuera de la franja ({outside}): requests = {got!r} (se esperaba 0)")
            hourly = self.report_json("traffic", org.slug, self.org_admin, {**dates, "group_by": "hour"})
            by_hour = {s.get("key"): s.get("requests") for s in hourly.get("series") or []}
            keys = {hour_key(start), hour_key(end)}
            if sum(by_hour.get(k) or 0 for k in keys) != n:
                problems.append(f"group_by=hour: {sorted(keys)} suman {[by_hour.get(k) for k in sorted(keys)]}")
            week = self.report_json("traffic", org.slug, self.org_admin, {**dates, "group_by": "weekday_hour"})
            cells = {s.get("key"): s.get("requests") or 0 for s in week.get("series") or []}
            if (
                len(cells) != 168
                or sum(cells[k] for k in {weekday_hour_key(start), weekday_hour_key(end)} if k in cells) != n
            ):
                problems.append(
                    f"group_by=weekday_hour: {len(cells)} celdas; la de {weekday_hour_key(start)} = "
                    f"{cells.get(weekday_hour_key(start))!r} (se esperaba {n})"
                )
            users = self.report_json("traffic", org.slug, self.org_admin, {**dates, "group_by": "user"})
            per_user = {s.get("key"): s.get("requests") for s in users.get("series") or []}
            if per_user != {self.user_id(self.user): n}:
                problems.append(f"group_by=user: {per_user} (se esperaba {{{self.user}: {n}}})")
            per_model = self.report_json("traffic", org.slug, self.org_admin, {**dates, "group_by": "model"})
            got_models = {s.get("key"): (s.get("requests"), s.get("provider")) for s in per_model.get("series") or []}
            if got_models != {m: (1, p) for m, p in models.items()}:
                problems.append(f"group_by=model: {got_models}")
            check(not problems, "\n".join(problems))
        apis = ", ".join(f"{h3.API_NAMES[a]}{' (stream)' if s else ''}" for a, s in cases)
        return (
            f"{n} peticiones ({apis}) → requests {n}, tokens {tokens_in}/{tokens_out}, bytes {sent}/{received} "
            f"(exactos); franja local {inside} = {n} y {outside} = 0; por hora, día×hora, usuario y modelo"
        )

    # --- 12.2 invocaciones ---------------------------------------------------------------------------
    def messages_turn(self, org: UsoOrg, model: str, messages: list[dict[str, Any]]) -> Http:
        payload = {**h3.request_body("messages", model, "x"), "messages": messages}
        _, res = self.e.send(org.bindings[self.user], self.user, "messages", payload)
        check(res.status == 200, f"Messages en «{org.slug}» → HTTP {res.status}: {h3.snippet(res.body)}")
        return res

    def conversations(self, org: UsoOrg, who: str | None = None) -> dict[str, dict[str, Any]]:
        """Modelo → conversación de la organización (lo que ve su ORG_ADMIN, o `who` si no es el de la semilla)."""
        res = self.e.webapp("GET", f"/api/v1/conversations?org={org.slug}&limit=200", who or self.org_admin)
        check(res.status == 200, f"GET /api/v1/conversations?org={org.slug} → {describe(res)}")
        items = [i for i in (res.json() or {}).get("items") or [] if isinstance(i, dict)]
        return {str(i.get("model")): i for i in items}

    def last_request_original(self, conversation_id: str) -> Any:
        """El `request_original` del último turno, como lo ve el dueño (el USER que lo envió)."""
        indexes = self.e.turn_indexes(self.user, conversation_id)
        res = self.e.turn(self.user, conversation_id, indexes[-1])
        check(res.status == 200, f"GET …/{conversation_id}/turns/{indexes[-1]} ({self.user}) → {describe(res)}")
        return (res.json() or {}).get("request_original")

    def invocations(self) -> str:
        """12.2. Cada invocación es una conversación de **dos turnos**, como en Claude Code. El `request_original` de un
        turno es solo lo nuevo, pero empieza por la respuesta anterior del modelo (contrato §7): en el segundo turno de
        la herramienta se repite el `[tool_use: Skill] …` y llega el `[tool_result] Launching skill: …` (se comprueba
        leyendo el turno antes de dar por buena la ausencia de doble recuento). En el de `/<org>:facturacion`, el
        `<command-name>` va en el **historial del cuerpo** que envía el cliente, no en el `request_original` del
        segundo turno (que es la respuesta anterior y la pregunta nueva). Un extractor que contara la herramienta
        también en `request_original`, o el comando en todo el historial, contaría dos veces.

        Usa la organización `demo` de la semilla de desarrollo: se invoca `demo:cosa-<ejecución>` desde la organización
        de la prueba y se comprueba que el informe de `demo` (como PLATFORM_ADMIN, agregado) no la cuenta."""
        other = f"cosa-{self.nonce}"  # skill de «demo» que no debe aparecer en «demo»
        with self.org("sk", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            slug = org.slug
            # /<org>:facturacion: el envoltorio de Claude Code en un bloque y la petición del usuario en otro (el eco
            # del proveedor falso repite solo el último bloque, como un modelo que no repite el comando); el segundo
            # turno lleva el comando en el historial del cuerpo
            command = (
                f"<command-message>{slug}:facturacion</command-message>\n"
                f"<command-name>/{slug}:facturacion</command-name>\n<command-args>importe 100</command-args>"
            )
            first = [{"type": "text", "text": command}, {"type": "text", "text": f"Prepara la factura {self.nonce}."}]
            slash_model = f"uso-{self.nonce}-sk-comando"
            reply = self.messages_turn(org, slash_model, [{"role": "user", "content": first}])
            self.messages_turn(org, slash_model, follow_up(first, reply.body, f"Y el IVA {self.nonce}?"))
            # la herramienta Skill: tool_use del modelo y, en el turno siguiente, su tool_result
            ask = f"usa la herramienta skill {slug}:informes"
            tool_model = f"uso-{self.nonce}-sk-herramienta"
            reply = self.messages_turn(org, tool_model, [{"role": "user", "content": ask}])
            check(b'"Skill"' in reply.body, f"el proveedor falso no respondió con la herramienta Skill: {ask}")
            launched = tool_result_for(reply.body, f"Launching skill: {slug}:informes")
            self.messages_turn(org, tool_model, follow_up(ask, reply.body, launched))
            ignored = [
                f"<command-name>/otro:cosa</command-name> {self.nonce} 1",  # otro plugin
                f"usa la herramienta skill otro:cosa {self.nonce}",
                f"<command-name>/{slug}2:cosa</command-name> {self.nonce} 2",  # un slug que empieza igual
                f"<command-name>/demo:{other}</command-name> {self.nonce} 3",  # otra organización
                f"usa la herramienta skill demo:{other}",
                f"- {slug}:listado: una skill del listado, sin invocarla ({self.nonce})",
                f"/{slug}:suelta sin envoltorio ({self.nonce})",
            ]
            for i, text in enumerate(ignored):
                self.send(org, self.user, "messages", text, model=f"uso-{self.nonce}-sk-{i}")
            total = 4 + len(ignored)
            self.wait_requests(org, total)  # todas las trazas consumidas, también los segundos turnos
            problems = []
            convs = self.conversations(org)
            for model in (slash_model, tool_model):
                turns = (convs.get(model) or {}).get("turn_count")
                if turns != 2:
                    problems.append(
                        f"el segundo turno no se correlacionó: la conversación {model} tiene {turns!r} "
                        "turno(s) (se esperaba 2; sin correlación el paso no prueba el doble recuento)"
                    )
            check(not problems, "\n".join(problems))
            # precondición: el texto que un extractor equivocado contaría dos veces está de verdad en el 2.º turno
            original = self.last_request_original(str(convs[tool_model]["id"]))
            missing = second_turn_problems(slug, "informes", original)
            check(not missing, "\n".join(missing) + " (sin eso, que INVOKED siga en 1 no prueba nada)")
            by_skill = counts_by_key(self.content(org, "skill").get("series"))
            by_source = self.content(org, "source").get("totals") or {}
            want = {"facturacion": {"INVOKED": 1}, "informes": {"INVOKED": 1}}
            if by_skill != want:
                problems.append(f"group_by=skill: {by_skill} (se esperaba {want}: una vez cada una)")
            if {s: by_source.get(s) for s in SOURCES} != {s: (2 if s == "INVOKED" else 0) for s in SOURCES}:
                problems.append(f"group_by=source: {by_source} (se esperaba INVOKED 2 y lo demás 0)")
            demo = counts_by_key(self.report_json("content", "demo", self.admin, {"group_by": "skill"}).get("series"))
            if other in demo:
                problems.append(f"«demo» cuenta {demo[other]} de «{other}», invocada desde «{slug}»")
            check(not problems, "\n".join(problems))
        return (
            f"/{slug}:facturacion (<command-name>) e Skill {slug}:informes (tool_use del proveedor falso), cada una en "
            "una conversación de dos turnos correlacionados (el segundo repite el comando en el historial o trae "
            f"«[tool_use: Skill]» y «Launching skill:») → INVOKED 1 cada una ({total} peticiones); otro plugin, otra "
            f"organización (y «demo» no gana «{other}»), «{slug}2:», el listado y el texto suelto ({len(ignored)}) → 0"
        )

    # --- 12.3 sincronización ------------------------------------------------------------------------
    def sync(self) -> str:
        skill = f"sincro-{self.nonce}"
        with self.org("sy", {self.org_admin: "ORG_ADMIN"}) as org:
            self.write_skill(org, skill, {"scripts/calculo.py": "print('hola')\n"})
            with self.mcp(org, self.org_admin) as client:  # como `baas sync`: listado, manifiesto y ficheros
                listing = self.call(client, "baas_list_skills", {})
                names = {
                    str(s.get("name") or s.get("skill")) for s in listing.get("skills") or [] if isinstance(s, dict)
                }
                check(skill in names, f"baas_list_skills no lista «{skill}»: {sorted(names)}")
                manifest = self.call(client, "baas_get_skill_package", {"skill": skill})
                check(manifest.get("package_sha256"), f"el manifiesto de «{skill}» no trae package_sha256")
                files = self.call(client, "baas_get_skill_files", {"skill": skill})
                check(files.get("files"), f"baas_get_skill_files de «{skill}» no devolvió ficheros")
            self.wait_sources(org, {"SYNCED": 1})
            # una ventana del MCP más (alineada al reloj) y 1 s: si trajera algo más, tendría que seguir igual
            time.sleep(self.a.usage_window + 1)
            data = self.wait_sources(org, {"SYNCED": 1}, timeout=0)
            by_skill = counts_by_key(self.content(org, "skill").get("series"))
            check(by_skill == {skill: {"SYNCED": 1}}, f"group_by=skill: {by_skill} (se esperaba {{{skill}: SYNCED 1}})")
        return (
            f"listado + manifiesto + ficheros de «{skill}» → SYNCED 1, INVOKED 0, SKILL_READ 0 "
            f"(totales {data.get('totals')})"
        )

    # --- 12.4 conocimiento y valores centinela ------------------------------------------------------
    def knowledge(self) -> str:
        insp = self.inspector()
        area = f"area-{self.nonce}"
        sentinels = [
            f"subcarpeta-{self.nonce}",
            f"secreto-{self.nonce}",
            f"raiz-{self.nonce}",
            f"consulta-{self.nonce}",
        ]
        deep = f"knowledge/{area}/{sentinels[0]}/{sentinels[1]}.md"
        root = f"knowledge/{sentinels[2]}.md"
        since = rfc3339(datetime.now(timezone.utc) - timedelta(seconds=2))
        with self.org("kn", {self.org_admin: "ORG_ADMIN"}) as org:
            self.write_doc(org, deep, f"Documento del área {area}.")
            self.write_doc(org, root, "Documento en la raíz.")
            with self.paused_consumer():  # el evento de uso se queda en baas:usage para leerlo tal cual
                with self.mcp(org, self.org_admin) as client:
                    self.call(client, "baas_read_document", {"path": deep})
                    self.call(client, "baas_read_document", {"path": root})
                    self.call(client, "baas_search_knowledge", {"query": sentinels[3]})
                events = self.capture(
                    PERSISTENT,
                    USAGE_STREAM,
                    "content.usage",
                    org.slug,
                    lambda found: (
                        sum(i.get("count", 0) for _, e, _ in found for i in (e.get("payload") or {}).get("items") or [])
                        >= 3
                    ),
                    "el MCP no publicó el uso en baas:usage (persistente)",
                )
            problems = [
                f"el evento {e.get('event_id')} de baas:usage contiene «{n}»"
                for _, e, raw in events
                for n in leaks(raw, sentinels)
            ]
            items = sorted(
                (i.get("source"), i.get("skill"), i.get("area"))
                for _, e, _ in events
                for i in (e.get("payload") or {}).get("items") or []
            )
            want_items = [("KNOWLEDGE_READ", "", ""), ("KNOWLEDGE_READ", "", area), ("KNOWLEDGE_SEARCH", "", "")]
            if items != want_items:
                problems.append(f"elementos de content.usage: {items} (se esperaba {want_items})")
            self.wait_sources(org, {"KNOWLEDGE_READ": 2, "KNOWLEDGE_SEARCH": 1})
            by_area = counts_by_key(self.content(org, "area").get("series"))
            want_area = {area: {"KNOWLEDGE_READ": 1}, "": {"KNOWLEDGE_READ": 1, "KNOWLEDGE_SEARCH": 1}}
            if by_area != want_area:
                problems.append(f"group_by=area: {by_area} (se esperaba {want_area})")
            problems += self.sentinel_problems(org, sentinels, since)
            if insp.key_exists(EPHEMERAL, USAGE_STREAM):
                problems.append(f"EXISTS {USAGE_STREAM} en «{EPHEMERAL}» = 1 (el uso nunca va a la efímera)")
            if not insp.key_exists(PERSISTENT, USAGE_STREAM):
                problems.append(f"EXISTS {USAGE_STREAM} en «{PERSISTENT}» = 0 (la webapp lo crea con MKSTREAM)")
            group = insp.stream_group(PERSISTENT, USAGE_STREAM, "webapp")
            if group is None:
                problems.append(f"{USAGE_STREAM} no tiene el grupo de consumo «webapp» en «{PERSISTENT}»")
            check(not problems, "\n".join(problems))
        return (
            f"«{area}» lee 1 y la raíz 1 (+1 búsqueda); ni el evento de baas:usage, ni las filas, "
            "ni la API (JSON y CSV), "
            f"ni los logs de {CONSUMER} y {MCP} llevan {', '.join(sentinels)}; EXISTS {USAGE_STREAM}: "
            f"{EPHEMERAL} 0, {PERSISTENT} 1 (grupo webapp, pendientes {group.get('pending') if group else '?'})"
        )

    def sentinel_problems(self, org: UsoOrg, sentinels: list[str], since: str) -> list[str]:
        insp = self.inspector()
        problems = [f"las tablas de uso contienen «{n}»" for n in leaks(insp.usage_dump(), sentinels)]
        remaining = "\n".join(data for _, data in insp.stream_entries(PERSISTENT, USAGE_STREAM))
        problems += [f"{USAGE_STREAM} (persistente) contiene «{n}»" for n in leaks(remaining, sentinels)]
        dates = self.today(org)
        requests: list[tuple[str, str, str | None, dict[str, Any]]] = []
        for group_by in ("day", "hour", "user", "model", "weekday_hour"):
            requests.append(("traffic", org.slug, self.org_admin, {**dates, "group_by": group_by}))
        for group_by in ("skill", "area", "user", "source", "day", "user_skill"):
            requests.append(("content", org.slug, self.org_admin, {**dates, "group_by": group_by}))
            requests.append(("content", org.slug, self.org_admin, {**dates, "group_by": group_by, "format": "csv"}))
        requests.append(("traffic", org.slug, self.org_admin, {**dates, "format": "csv"}))
        requests.append(("storage", org.slug, self.org_admin, dates))
        requests.append(("storage", org.slug, self.org_admin, {**dates, "format": "csv"}))
        for kind, slug, who, params in requests:
            res = self.report(kind, slug, who, params)
            if res.status != 200:
                problems.append(f"GET …/usage/{kind}?{urlencode(params)} → {describe(res)}")
            problems += [f"…/usage/{kind}?{urlencode(params)} contiene «{n}»" for n in leaks(res.body, sentinels)]
        for fmt in ("json", "csv"):
            res = self.e.webapp("GET", f"/api/v1/platform/usage?format={fmt}", self.admin)
            if res.status != 200:
                problems.append(f"GET /api/v1/platform/usage?format={fmt} → {describe(res)}")
            problems += [f"/api/v1/platform/usage ({fmt}) contiene «{n}»" for n in leaks(res.body, sentinels)]
        for service in (CONSUMER, MCP):
            problems += [
                f"los logs de {service} contienen «{n}»" for n in leaks(insp.service_logs(service, since), sentinels)
            ]
        return problems

    # --- 12.5 foto de disco ---------------------------------------------------------------------------
    def storage(self) -> str:
        insp = self.inspector()
        skill, area = f"disco-{self.nonce}", f"area-{self.nonce}"
        with self.org("dk", {self.org_admin: "ORG_ADMIN"}) as org:
            pad = "x" * 4000
            self.write_skill(
                org,
                skill,
                {"scripts/proceso.py": f"# {pad}\nprint(1)\n", "esquema.json": json.dumps({"relleno": pad * 2}) + "\n"},
            )
            self.write_doc(org, f"knowledge/{area}/doc.md", pad * 3)
            written = datetime.now(timezone.utc)
            attempts: list[str] = []
            result: dict[str, Any] = {}

            def compare() -> list[str]:
                latest = self.report_json("storage", org.slug, self.org_admin, self.today(org)).get("latest")
                taken = parse_instant((latest or {}).get("taken_at"))
                if taken is None or taken <= written:
                    return [f"sin foto posterior a las escrituras (latest.taken_at = {(latest or {}).get('taken_at')})"]
                expected = disk_expected(insp.mcp_files(org.slug))
                got = snapshot_usage(latest)
                problems = disk_problems(expected, got)
                kinds = {(i.get("kind"), i.get("name")) for i in (latest or {}).get("items") or []}
                problems += [f"items sin {k}" for k in (("SKILL", skill), ("AREA", area)) if k not in kinds]
                if problems:
                    attempts.append(f"{latest.get('taken_at')}: {'; '.join(problems)}")  # type: ignore[union-attr]
                    return problems
                result.update(expected=expected, got=got, taken=latest.get("taken_at"))  # type: ignore[union-attr]
                return []

            self.wait(compare, self.a.usage_timeout, "la foto de disco no coincide con el volumen del MCP", every=2)
        exp = result["expected"]
        cats = ", ".join(f"{c} {exp[c]}" for c in CATEGORIES)
        note = f" ({len(attempts)} foto(s) anterior(es) no cuadraban: {attempts[0]})" if attempts else ""
        return (
            f"foto {result['taken']}: {exp['files']} ficheros, total {disk_total(exp)} B ({cats}) = volumen del MCP "
            f"con margen ≤ 1 %; items SKILL {skill} y AREA {area}{note}"
        )

    # --- 12.6 permisos, aislamiento y auditoría --------------------------------------------------------
    def permissions(self) -> str:
        a_members = {OTHER_ADMIN: "ORG_ADMIN", self.user: "USER"}
        b_members = {self.org_admin: "ORG_ADMIN", OTHER_USER: "USER"}
        with self.org("pa", a_members) as org_a, self.org("pb", b_members) as org_b:
            self.send(org_a, self.user, "messages", f"permisos {self.nonce} a")
            for i in range(2):
                self.send(org_b, OTHER_USER, "messages", f"permisos {self.nonce} b {i}")
            self.wait_requests(org_a, 1, OTHER_ADMIN)
            self.wait_requests(org_b, 2, self.org_admin)
            problems: list[str] = []
            dates = self.today(org_a)
            for kind in ("traffic", "content", "storage"):
                for who, slug, status in (
                    (None, org_a.slug, 401),
                    (self.user, org_a.slug, 403),
                    (self.org_admin, org_a.slug, 404),
                    (OTHER_ADMIN, org_b.slug, 404),
                ):
                    res = self.report(kind, slug, who, dates)
                    if res.status != status:
                        problems.append(
                            f"{kind} de «{slug}» ({who or 'sin sesión'}) → {describe(res)} (se esperaba {status})"
                        )
                    elif "totals" in (res.json() or {}) or "latest" in (res.json() or {}):
                        problems.append(
                            f"la respuesta {status} de {kind} ({who}) lleva datos: {h3.snippet(res.body, 120)}"
                        )
            for who in (OTHER_ADMIN, self.user):
                res = self.e.webapp("GET", "/api/v1/platform/usage", who)
                if res.status != 403:
                    problems.append(f"/api/v1/platform/usage ({who}) → {describe(res)} (se esperaba 403)")
            own = self.totals(org_a, OTHER_ADMIN).get("requests")
            other_user = self.totals(org_a, OTHER_ADMIN, users=self.user_id(OTHER_USER)).get("requests")
            if (own, other_user) != (1, 0):
                problems.append(
                    f"ORG_ADMIN de «{org_a.slug}»: requests {own} y {other_user} con el usuario de la otra "
                    "(se esperaba 1 y 0)"
                )
            seen = (self.totals(org_a, self.admin).get("requests"), self.totals(org_b, self.admin).get("requests"))
            if seen != (1, 2):
                problems.append(f"el PLATFORM_ADMIN ve {seen} peticiones en las dos (se esperaba (1, 2))")
            # /platform/usage interpreta las fechas en UTC (contrato §8.6), no en la zona de la organización
            utc_today = datetime.now(timezone.utc).date()
            utc_dates = {"from": (utc_today - timedelta(days=1)).isoformat(), "to": utc_today.isoformat()}
            res = self.e.webapp("GET", f"/api/v1/platform/usage?{urlencode(utc_dates)}", self.admin)
            ranking = {
                o.get("slug"): o.get("requests") for o in (res.json() or {}).get("orgs") or [] if isinstance(o, dict)
            }
            if res.status != 200 or (ranking.get(org_a.slug), ranking.get(org_b.slug)) != (1, 2):
                problems.append(
                    f"/api/v1/platform/usage → {describe(res)}: {org_a.slug} {ranking.get(org_a.slug)!r}, "
                    f"{org_b.slug} {ranking.get(org_b.slug)!r} (se esperaba 1 y 2)"
                )
            # usage.viewed: solo el detalle por persona de un PLATFORM_ADMIN que no es ORG_ADMIN de la organización
            self.report_json("traffic", org_a.slug, self.admin, {**dates, "group_by": "day"})
            self.report_json("content", org_a.slug, self.admin, dates)
            self.report_json("traffic", org_a.slug, OTHER_ADMIN, {**dates, "group_by": "user"})
            if self.viewed(org_a, OTHER_ADMIN):
                problems.append("se auditó usage.viewed sin detalle por persona o de su propio ORG_ADMIN")
            self.report_json("traffic", org_a.slug, self.admin, {**dates, "group_by": "user"})
            rows = self.viewed(org_a, OTHER_ADMIN)  # el ORG_ADMIN de la organización la ve en W10
            if [(r.get("target_id"), (r.get("metadata") or {}).get("group_by")) for r in rows] != [("traffic", "user")]:
                problems.append(f"usage.viewed de «{org_a.slug}» (W10 del ORG_ADMIN): {rows}")
            elif not self.viewed(org_a, self.admin):
                problems.append("el PLATFORM_ADMIN no ve su usage.viewed en W10")
            check(not problems, "\n".join(problems))
        return (
            "sin sesión 401, USER 403, ORG_ADMIN ajeno 404 (tráfico, contenido y disco) y 403 en plataforma; cada "
            "ORG_ADMIN ve solo lo suyo (1 y 2); el PLATFORM_ADMIN ve las dos y su consulta por persona deja una fila "
            "usage.viewed (traffic, group_by=user) que el ORG_ADMIN ve en W10; los informes agregados no se auditan"
        )

    def viewed(self, org: UsoOrg, who: str) -> list[dict[str, Any]]:
        res = self.e.webapp("GET", f"/api/v1/audit?{urlencode({'org': org.slug, 'action': 'usage.viewed'})}", who)
        check(res.status == 200, f"GET /api/v1/audit?org={org.slug}&action=usage.viewed ({who}) → {describe(res)}")
        return [i for i in (res.json() or {}).get("items") or [] if isinstance(i, dict)]

    # --- 12.7 el proxy no depende del consumidor ni del MCP ---------------------------------------------
    def latencies(self, org: UsoOrg, label: str, count: int) -> list[float]:
        binding = org.bindings[self.user]
        client = Timed(
            h3.api_url(binding, "messages"),
            {
                "X-Baas-Api-Key": self.key(self.user),
                "x-api-key": h3.FAKE_ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            self.a.timeout,
        )
        values = []
        try:
            for i in range(count):
                text = f"{label} {self.nonce} {i}. Hola."
                payload = h3.request_body("messages", f"uso-{self.nonce}-pr-{label}-{i}", text)
                elapsed, status, body = client.post(payload)
                check(status == 200, f"{label} {i}: el proxy respondió HTTP {status}: {h3.snippet(body)}")
                reply = h3.extract_reply("messages", body, stream=False).texts
                check(reply and reply[0][1] == f"Eco: {text}", f"{label} {i}: respuesta distinta: {h3.snippet(body)}")
                values.append(elapsed)
        finally:
            client.close()
        return values

    def restart(self, org: UsoOrg, services: list[str], failed: bool) -> None:
        """Vuelve a arrancar `services` (cada uno por su lado: si uno falla, se intenta el otro) y espera a que
        respondan antes de que el contexto elimine la organización (la webapp, al arrancar, vuelve a ejecutar la
        migración y la semilla). Si el paso ya falló, no tapa su error: solo lo avisa."""
        insp = self.inspector()
        errors = []
        for service in reversed(services):
            try:
                insp.service_control(service, "start")
            except Exception as exc:
                errors.append(f"no se pudo arrancar {service}: {exc}")
        waits = [(CONSUMER, self.wait_webapp), (MCP, lambda: self.wait_mcp(org))]
        for service, wait in waits:
            if service in services:
                try:
                    wait()
                except Exception as exc:
                    errors.append(f"{service} no vuelve: {exc}")
        if errors and not failed:
            raise StepError("\n".join(errors))
        for error in errors:
            print(f"          ({error})")

    def outage(self) -> str:
        """12.7. «Se queda atrás» se lee en la base (`usage_source_status`) con el consumidor parado, porque la API
        también lo está; lo que vale es la puesta al día exacta al volver (`data_until` y los recuentos sin
        duplicados). La tolerancia de latencia (×1,5 y +5/+10 ms) detecta un bloqueo o una espera por un tiempo de
        espera agotado, no una degradación fina (para eso, 12.10 y las pruebas de sobrecoste del proxy)."""
        insp = self.inspector()
        count = 30
        with self.org("pr", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            self.latencies(org, "calentamiento", 5)
            before = self.latencies(org, "antes", count)
            self.wait_requests(org, 5 + count)
            mark = parse_instant(insp.source_status().get("TRAFFIC"))
            check(mark, "usage_source_status no tiene TRAFFIC tras consumir el tráfico")
            with self.mcp(org, self.org_admin) as client:  # el MCP ya tiene en caché la clave y la membresía
                self.call(client, "baas_whoami", {})
            stopped: list[str] = []
            failed = False
            try:
                stopped.append(CONSUMER)  # antes de pararlo: si `stop` falla a medias, también se rearranca
                insp.service_control(CONSUMER, "stop")
                with self.mcp(org, self.org_admin) as client:
                    self.call(client, "baas_search_knowledge", {"query": f"parada {self.nonce}"})
                self.capture(
                    PERSISTENT,
                    USAGE_STREAM,
                    "content.usage",
                    org.slug,
                    bool,
                    "el uso del MCP no llegó a baas:usage con la webapp parada",
                )
                stopped.append(MCP)
                insp.service_control(MCP, "stop")
                during = self.latencies(org, "durante", count)
                last = datetime.now(timezone.utc)
                held = parse_instant(insp.source_status().get("TRAFFIC"))
            except BaseException:
                failed = True
                raise
            finally:
                self.restart(org, stopped, failed)
            self.wait_webapp()
            self.wait_mcp(org)
            problems = degradation(before, during)
            if held != mark:
                problems.append(f"«datos hasta» (TRAFFIC) avanzó con el consumidor parado: {mark} → {held}")
            data = self.wait_requests(org, 5 + 2 * count)
            until = parse_instant(data.get("data_until"))
            if until is None or until < last - timedelta(seconds=5) or until <= mark:  # type: ignore[operator]
                problems.append(
                    f"data_until = {data.get('data_until')} tras volver (parada desde {rfc3339(mark)}, "
                    f"última petición {rfc3339(last)})"
                )  # type: ignore[arg-type]
            self.wait_sources(org, {"KNOWLEDGE_SEARCH": 1})
            time.sleep(2)
            again = self.totals(org).get("requests")
            if again != 5 + 2 * count:
                problems.append(f"requests = {again} poco después (se esperaba {5 + 2 * count}: ¿duplicados?)")
            check(not problems, "\n".join(problems))
        self.figures["12.7"] = {
            "before": (median(before), quantile(before, 0.95)),
            "during": (median(during), quantile(during, 0.95)),
        }
        return (
            f"{count} peticiones con {CONSUMER} y {MCP} parados: 200 y la misma respuesta; latencia p50/p95 "
            f"{median(before):.1f}/{quantile(before, 0.95):.1f} ms antes y {median(during):.1f}/"
            f"{quantile(during, 0.95):.1f} ms durante; «datos hasta» se quedó en {rfc3339(mark)} durante la parada y "
            f"al volver llegó a {data.get('data_until')} con {5 + 2 * count} peticiones y 1 búsqueda (sin duplicados)"
        )

    # --- 12.8 retención y borrado ---------------------------------------------------------------------
    def retention(self) -> str:
        insp = self.inspector()
        res = self.e.webapp("GET", "/api/v1/platform/usage", self.admin)
        check(res.status == 200, f"GET /api/v1/platform/usage → {describe(res)}")
        previous = (res.json() or {}).get("metrics_retention_months")
        check(isinstance(previous, int), f"/api/v1/platform/usage no trae metrics_retention_months: {previous!r}")
        old, recent = f"v{self.nonce}", f"r{self.nonce}"
        org = self.create_org("rt", {self.org_admin: "ORG_ADMIN", self.user: "USER"})
        problems: list[str] = []
        try:
            self.send(org, self.user, "messages", f"retencion {self.nonce}. Hola.")
            with self.mcp(org, self.org_admin) as client:
                self.call(client, "baas_search_knowledge", {"query": f"retencion {self.nonce}"})
            self.wait_requests(org, 1)
            self.wait_sources(org, {"KNOWLEDGE_SEARCH": 1})
            insp.insert_old_usage(org.org_id, self.user_id(self.user), months=TEST_RETENTION + 1, tag=old)
            insp.insert_old_usage(org.org_id, self.user_id(self.user), months=TEST_RETENTION - 1, tag=recent)
            for tag in (old, recent):
                rows = insp.tagged_rows(tag)
                check(rows == {"traffic": 1, "content": 1, "snapshots": 1}, f"filas insertadas ({tag}): {rows}")
            # la conversación se borra (como la purga por su retención): las métricas no se tocan
            listing = self.e.webapp("GET", f"/api/v1/conversations?org={org.slug}&limit=50", self.org_admin)
            items = [i for i in (listing.json() or {}).get("items") or [] if isinstance(i, dict)]
            check(listing.status == 200 and len(items) == 1, f"conversaciones de «{org.slug}»: {describe(listing)}")
            cid = str(items[0]["id"])
            res = self.e.webapp("POST", f"/api/v1/conversations/{cid}/delete", self.org_admin, {"reason": REASON})
            check(res.status == 200, f"borrar la conversación {cid} → {describe(res)}")
            gone = self.e.webapp("GET", f"/api/v1/conversations/{cid}", self.org_admin)
            check(gone.status == 404, f"la conversación borrada → HTTP {gone.status} (se esperaba 404)")
            problems += self.purge_with_retention(
                TEST_RETENTION,
                previous,
                lambda: self.wait(
                    lambda: (
                        []
                        if insp.tagged_rows(old) == {"traffic": 0, "content": 0, "snapshots": 0}
                        else [f"filas de hace {TEST_RETENTION + 1} meses: {insp.tagged_rows(old)}"]
                    ),
                    self.a.usage_timeout,
                    "la purga no borró las métricas más antiguas que la retención",
                ),
            )
            kept = insp.tagged_rows(recent)
            if kept != {"traffic": 1, "content": 1, "snapshots": 1}:
                problems.append(f"la purga borró también las de hace {TEST_RETENTION - 1} meses: {kept}")
            requests = self.totals(org).get("requests")
            if requests != 1:
                problems.append(f"con la conversación borrada, requests = {requests!r} (se esperaba 1: sigue contando)")
            before = insp.usage_rows(org.org_id)
            if not (before["traffic"] and before["content"]):
                problems.append(f"antes de eliminar la organización no tenía métricas: {before}")
            self.drop_org(org.slug)
            after = insp.usage_rows(org.org_id)
            if any(after.values()):
                problems.append(f"tras eliminar «{org.slug}» quedan métricas suyas: {after}")
        finally:
            with contextlib.suppress(Exception):
                self.drop_org(org.slug)
        check(not problems, "\n".join(problems))
        return (
            f"retención {TEST_RETENTION} meses y purga (al arrancar {CONSUMER}): las filas de hace "
            f"{TEST_RETENTION + 1} meses (tráfico, contenido y foto) se borran y las de hace {TEST_RETENTION - 1} no; "
            "la conversación borrada sigue contando (1 petición); eliminar "
            f"«{org.slug}» deja 0 filas suyas ({before} antes); retención restaurada a {previous}"
            + retention_warning(previous)
        )

    def purge_with_retention(self, months: int, previous: int, purged: Callable[[], None]) -> list[str]:
        """Pone la retención de métricas en `months`, vuelve a arrancar la webapp (`purge_metrics` se ejecuta al
        arrancar; `restart` no vale con podman-compose: las dependencias ya terminadas, migrate y seed, cuentan como «no
        arrancadas») y espera con `purged`. En el `finally` (también con Ctrl-C): si la webapp quedó parada, se intenta
        arrancar otra vez, y la retención vuelve siempre a `previous`. Si algo de eso falla, lo dice el error del paso
        o, si el paso fue bien, los problemas que devuelve."""
        insp = self.inspector()
        notes: list[str] = []
        changed = stopped = started = False
        try:
            changed = True  # antes del PUT: si se aplica y la respuesta se pierde, también se restaura (inocuo)
            res = self.e.webapp(
                "PUT", "/api/v1/platform/settings/metrics-retention", self.admin, {"months": months, "reason": REASON}
            )
            check(res.status == 200, f"PUT …/metrics-retention {{months: {months}}} → {describe(res)}")
            stopped = True  # antes de `stop`: si falla a medias, también se intenta arrancar
            insp.service_control(CONSUMER, "stop")
            insp.service_control(CONSUMER, "start")
            started = True
            self.wait_webapp()
            purged()
        except Exception as exc:
            raise StepError(f"{exc}{self._purge_cleanup(stopped, started, changed, previous, notes)}") from None
        except BaseException:
            extra = self._purge_cleanup(stopped, started, changed, previous, notes)
            if extra:
                print(f"          ({extra.strip()})")
            raise
        self._purge_cleanup(stopped, started, changed, previous, notes)
        return notes

    def _purge_cleanup(self, stopped: bool, started: bool, changed: bool, previous: int, notes: list[str]) -> str:
        insp = self.inspector()
        if stopped and not started:
            try:
                insp.service_control(CONSUMER, "start")
                notes.append(f"{CONSUMER} no arrancó a la primera tras pararlo; se volvió a arrancar")
            except Exception as exc:
                notes.append(f"{CONSUMER} sigue parado: tampoco arrancó al reintentarlo ({exc}); arráncalo a mano")
        if changed:
            restored = self.restore_retention(previous)
            if restored:
                notes.append(restored)
        return "".join(f"\nademás, {n}" for n in notes)

    def restore_retention(self, months: int) -> str | None:
        """Vuelve a poner la retención de métricas (esperando a la webapp si se está arrancando); `None` si se pudo."""
        with contextlib.suppress(Exception):
            self.wait_webapp()
        try:
            res = self.e.webapp(
                "PUT", "/api/v1/platform/settings/metrics-retention", self.admin, {"months": months, "reason": REASON}
            )
        except Exception as exc:
            return f"no se pudo restaurar la retención de métricas a {months}: {type(exc).__name__}: {exc}"
        if res.status != 200:
            return f"no se pudo restaurar la retención de métricas a {months}: {describe(res)}"
        return None

    # --- 12.9 un evento repetido no cuenta dos veces --------------------------------------------------------
    def replay(self) -> str:
        insp = self.inspector()
        with self.org("dp", {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            self.send(org, self.user, "messages", f"repetido {self.nonce} 0. Hola.")
            with self.mcp(org, self.org_admin) as client:
                self.call(client, "baas_whoami", {})
            self.wait_requests(org, 1)
            with self.paused_consumer():
                with self.mcp(org, self.org_admin) as client:
                    self.call(client, "baas_search_knowledge", {"query": f"repetido {self.nonce}"})
                self.send(org, self.user, "messages", f"repetido {self.nonce} 1. Hola.")
                usage = self.capture(
                    PERSISTENT, USAGE_STREAM, "content.usage", org.slug, bool, "el uso del MCP no llegó a baas:usage"
                )
                trace = self.capture(
                    EPHEMERAL, TRACES_STREAM, "turn.completed", org.slug, bool, "el turno no llegó a baas:traces"
                )
            totals = self.wait_requests(org, 2).get("totals") or {}
            self.wait_sources(org, {"KNOWLEDGE_SEARCH": 1})
            snapshot = {
                "event_id": new_event_id(),
                "type": "storage.snapshot",
                "ts": rfc3339(datetime.now(timezone.utc)),
                "org_slug": org.slug,
                "payload": {
                    "files": 1,
                    "bytes": {**dict.fromkeys(CATEGORIES, 0), "knowledge": 7},
                    "items": [{"kind": "AREA", "name": "", "files": 1, "bytes": 7}],
                },
            }
            raw_snapshot = json.dumps(snapshot, separators=(",", ":"))
            self.consume(PERSISTENT, USAGE_STREAM, raw_snapshot)
            check(insp.snapshot_count(snapshot["event_id"]) == 1, "la foto inyectada no se guardó (¿evento venenoso?)")
            for service, stream, raw in (
                (PERSISTENT, USAGE_STREAM, usage[-1][2]),
                (EPHEMERAL, TRACES_STREAM, trace[-1][2]),
                (PERSISTENT, USAGE_STREAM, raw_snapshot),
            ):
                self.consume(service, stream, raw)
            problems = []
            after = self.totals(org)
            for key in ("requests", "tokens_in", "tokens_out", "bytes_in", "bytes_out"):
                if after.get(key) != totals.get(key):
                    problems.append(f"{key}: {totals.get(key)} → {after.get(key)} tras repetir el turno")
            sources = self.content(org, "source").get("totals") or {}
            if sources.get("KNOWLEDGE_SEARCH") != 1:
                problems.append(f"KNOWLEDGE_SEARCH = {sources.get('KNOWLEDGE_SEARCH')} tras repetir content.usage")
            if insp.snapshot_count(snapshot["event_id"]) != 1:
                problems.append(f"la foto repetida se guardó {insp.snapshot_count(snapshot['event_id'])} veces")
            check(not problems, "\n".join(problems))
        return (
            f"content.usage {usage[-1][1].get('event_id')}, turn.completed {trace[-1][1].get('event_id')} y "
            f"storage.snapshot {snapshot['event_id']} reinyectados con XADD (mismo event_id): se consumen y no "
            f"cambian nada (requests {totals.get('requests')}, KNOWLEDGE_SEARCH 1, 1 foto)"
        )

    def consume(self, service: str, stream: str, raw: str) -> None:
        """XADD de `raw` y espera a que la webapp lo consuma (lo confirma y lo borra del stream)."""
        insp = self.inspector()
        entry = insp.stream_add(service, stream, raw)
        self.wait(
            lambda: [f"la entrada {entry} sigue en {stream}"] if insp.stream_has(service, stream, entry) else [],
            self.a.usage_timeout,
            f"la webapp no consumió la entrada reinyectada en {stream}",
            every=0.5,
        )
        time.sleep(0.5)  # el COMMIT va antes del XDEL; margen para el informe

    # --- 12.10 sobrecoste del proxy ---------------------------------------------------------------------
    def proxy_overhead(self) -> str:
        n = int(self.a.overhead_n)
        limit = float(self.a.overhead_p95_ms)  # 10 ms: objetivo de fase-1-h4-mediciones §1 (bypass, ~2 KB)
        with self.org("lt", {self.user: "USER"}) as org:
            binding = org.bindings[self.user]
            anthropic = {"x-api-key": h3.FAKE_ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
            direct = Timed(f"{self.a.fake_url}/v1/messages", anthropic, self.a.timeout)
            proxied = Timed(
                h3.api_url(binding, "messages"), {**anthropic, "X-Baas-Api-Key": self.key(self.user)}, self.a.timeout
            )
            times: dict[str, list[float]] = {"direct": [], "proxy": []}
            pad = "x" * OVERHEAD_BODY
            try:
                for i in range(20 + n):
                    pair = (("direct", direct), ("proxy", proxied))
                    for name, client in pair if i % 2 else pair[::-1]:
                        payload = h3.request_body("messages", f"uso-{self.nonce}-lt", f"{i} {name} {pad}")
                        elapsed, status, body = client.post(payload)
                        check(status == 200, f"{name} {i}: HTTP {status}: {h3.snippet(body)}")
                        if i >= 20:  # las 20 primeras calientan las conexiones y las cachés
                            times[name].append(elapsed)
            finally:
                direct.close()
                proxied.close()
        got = overhead(times["direct"], times["proxy"])
        self.figures["12.10"] = got
        detail = (
            f"{n} pares intercalados (~2 KB, Messages, bypass): directo p50/p95 {got['direct_p50']:.2f}/"
            f"{got['direct_p95']:.2f} ms, por el proxy {got['proxy_p50']:.2f}/{got['proxy_p95']:.2f} ms → sobrecoste "
            f"p50 {got['p50']:.2f} ms y p95 {got['p95']:.2f} ms (umbral p95 < {limit:g} ms, --overhead-p95-ms; "
            "fase-1-h4-mediciones). Control grueso: que no empeora respecto a la referencia lo dicen las pruebas "
            "de sobrecoste en proceso del proxy (TestBypassOverheadP95, TestAIIPOverheadP95, TestObserveOverheadP95), "
            "que no forman parte de este e2e"
        )
        check(got["p95"] < limit, f"el sobrecoste empeora: {detail}")
        return detail

    # --- el recorrido ----------------------------------------------------------------------------------
    def register(self, runner: Runner, base: list[str]) -> None:
        step = runner.step
        has_inspector = self.e.inspector is not None
        ready = "12.0 la pila tiene la Fase 4 (API de uso)"
        step(ready, self.phase4, needs=base)
        base = [*base, ready]
        step("12.1 tráfico: peticiones, tokens, bytes y franja local", self.traffic, needs=base)
        step(
            "12.2 /org:skill y la herramienta Skill cuentan INVOKED; otra organización no", self.invocations, needs=base
        )
        step("12.3 una sincronización cuenta SYNCED, no INVOKED", self.sync, needs=base)
        if has_inspector:
            step(
                "12.4 una lectura suma a su área; ningún dato lleva la ruta; baas:usage solo en la persistente",
                self.knowledge,
                needs=base,
            )
            step("12.5 la foto de disco coincide con el volumen del MCP (margen del 1 %)", self.storage, needs=base)
        step("12.6 permisos (401, 403, 404), aislamiento y usage.viewed", self.permissions, needs=base)
        if has_inspector:
            step(
                "12.7 con el consumidor y el MCP parados el proxy responde igual; «datos hasta» se pone al día",
                self.outage,
                needs=base,
            )
            step(
                "12.8 retención: purga de lo antiguo, conversación borrada que cuenta y organización eliminada",
                self.retention,
                needs=base,
            )
            step("12.9 un evento repetido no cuenta dos veces", self.replay, needs=base)
        step("12.10 el sobrecoste del proxy no empeora", self.proxy_overhead, needs=base)


def describe(res: Http) -> str:
    data = res.json()
    error = data.get("error") if isinstance(data, dict) else None
    return f"HTTP {res.status}{' ' + str(error) if error else ''}" + (
        "" if res.status < 300 else f": {h3.snippet(res.body, 200)}"
    )
