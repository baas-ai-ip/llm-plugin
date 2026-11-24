"""Grupo «ner» (pasos 15.1–15.8) de `dev/e2e_h4.py`: L2 (NER) de la Fase 2 contra la pila real con el proveedor de eco.

Un paso por criterio de aceptación del diseño (`fase-2-ner-diseno.md`, §8) y del contrato (`contratos-fase-1.md`, §5.2,
§6 y §7.2) que se pueden probar de punta a punta:

  15.1  una organización nueva tiene el NER encendido y en `OBSERVE`; `PUT /ner` pide motivo, cambia lo que lleva y
        queda en la auditoría (`org.ner.changed`); con el anonimizador apagado (o AI-IP apagado) da `409` (criterio 13).
  15.2  con `ner_mode: ACTIVE`, un nombre, una empresa y una dirección salen al proveedor como `[[PERSON_001]]`,
        `[[ORG_001]]` y `[[LOCATION_001]]` y vuelven restaurados, en las tres APIs con y sin *streaming* (criterio 1).
  15.3  en el segundo turno el NER solo analiza lo nuevo (`l2.analyzed_bytes`) y el historial reenviado es idéntico al
        del turno anterior (criterio 2).
  15.4  tras vaciar la caché del NER y la copia caliente del vault en la efímera, el siguiente turno sale con el
        historial idéntico y sin esperar al NER por lo ya visto (criterio 4).
  15.5  con el servicio `ner` parado: `DEGRADE` da un turno `DEGRADED` y `WARNING` (y con el disyuntor abierto no
        espera); `REJECT` da `503`, `turn.blocked` `NER_UNAVAILABLE` y nada llega al proveedor. Al final, `ner` vuelve a
        arrancar y se espera a que el disyuntor del proxy se cierre (criterio 5).
  15.6  observación (NER apagado, NER encendido en `OBSERVE` y anonimizador apagado): el cuerpo sale sin cambios,
        `observed_detections` llega en `turn.completed` y el estado del turno no cambia (criterio 8).
  15.7  la prueba en vivo devuelve coincidencias de NER con su capa y `ALLOWLISTED` con un término de la *allowlist*
        (criterio 9).
  15.8  W8 (#71): un valor que el NER observó se añade a la *allowlist* de la organización (el mismo `POST` que el
        botón «Añadir a la allowlist» del detalle del turno) y el turno siguiente, en otra conversación, ya no lo
        observa; los demás valores sí.

Cada paso crea su organización (`ner-<ejecución>-<paso>`, con un usuario de la semilla de desarrollo como miembro) y la
elimina al terminar, así que el grupo no depende de los datos de la semilla. Los pasos 15.4 y 15.5 miran o paran
contenedores (`--inspect`). El 15.4 borra de la efímera solo la caché del NER de su organización
(`baas:ner:<org_id>:*`) y la copia caliente del vault de su conversación (`baas:conv:<id>:vault`), no toda la
instancia: lo demás (trazas aún sin consumir, cachés de otras organizaciones) no se toca.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import e2e_h3 as h3
from e2e_h2 import Http, StepError, check

if TYPE_CHECKING:
    from e2e_h2 import Runner
    from e2e_h4 import E2EH4

REASON = "Prueba de punta a punta del grupo ner (F2, L2)"
# Nombres inventados que el modelo detecta de forma fiable (se comprueban con la prueba en vivo antes de cada ejecución)
VALUES = {"PERSON": "Ana Gómez", "ORG": "Telefónica", "LOCATION": "Valencia"}
TEXT_1 = "Hola, soy Ana Gómez, trabajo en Telefónica y vivo en Valencia."
EXPECTED_1 = {"PERSON": ["[[PERSON_001]]"], "ORG": ["[[ORG_001]]"], "LOCATION": ["[[LOCATION_001]]"]}
OBSERVED_1 = [("PERSON", "Ana Gómez"), ("ORG", "Telefónica"), ("LOCATION", "Valencia")]
# Segundo y tercer mensaje de una conversación: valores nuevos, así que los tokens siguen la numeración
TEXT_2 = "Mi compañero Luis Fernández vive en Sevilla."
VALUES_2 = {"PERSON": "Luis Fernández", "LOCATION": "Sevilla"}
EXPECTED_2 = {"PERSON": ["[[PERSON_002]]"], "LOCATION": ["[[LOCATION_002]]"]}
TEXT_3 = "Pedro Martínez trabaja en Repsol, en Bilbao."
VALUES_3 = {"PERSON": "Pedro Martínez", "ORG": "Repsol", "LOCATION": "Bilbao"}
ECHO = "Eco: "  # el proveedor falso contesta con el eco del último mensaje del usuario
TOKEN_RE = re.compile(r"\[\[([A-Z_]+)_[0-9]{3,}\]\]")
DEGRADED_CAUSES = ("TIMEOUT", "UNAVAILABLE", "OVERLOADED", "BREAKER_OPEN")
PROXY = "llm-interceptor-proxy"
# 15.4: lo que puede tardar el turno cuyo historial perdió todas las cachés: una llamada interactiva al NER (el tiempo
# máximo es de 2 s y una llamada normal tarda decenas de ms) con margen; esperar al NER por lo visto la pasaría
LOST_CACHE_BOUND = 1.5
# APIs cuyo texto de sistema no es un mensaje de la conversación: con la caché perdida se espera al NER por él (diseño
# §5.3, «posiblemente nuevo»), en la misma llamada que lo nuevo; en Chat es el primer mensaje y cuenta como ya visto
SYSTEM_OUTSIDE_MESSAGES = ("messages", "responses")
SPEEDY = 1.0  # segundos: un turno con el disyuntor abierto no espera al NER (el tiempo máximo del NER es de 2 s)


# --- utilidades puras ----------------------------------------------------------------------------


def sent_text(sent: Any) -> str:
    """El texto de lo enviado al proveedor: una cadena, o los segmentos `{text}` / `{token}` de la webapp."""
    if isinstance(sent, str):
        return sent
    if isinstance(sent, list):
        return "".join(str(s.get("text") or s.get("token") or "") for s in sent if isinstance(s, dict))
    return ""


def tokens_by_type_from_request_sent(sent: Any) -> dict[str, list[str]]:
    """Los tokens que salieron al proveedor, por tipo y por orden de aparición, sin repetir."""
    out: dict[str, list[str]] = {}
    for match in TOKEN_RE.finditer(sent_text(sent)):
        tokens = out.setdefault(match.group(1), [])
        if match.group(0) not in tokens:
            tokens.append(match.group(0))
    return out


def tokenization_problems(sent: Any, values: Mapping[str, str], expected: Mapping[str, Sequence[str]]) -> list[str]:
    """Lo que sobra o falta en lo enviado al proveedor: los valores originales no salen y cada tipo lleva sus tokens."""
    text = sent_text(sent)
    problems = [f"{kind}: el valor original «{value}» sale en claro" for kind, value in values.items() if value in text]
    got = tokens_by_type_from_request_sent(text)
    for kind, want in expected.items():
        if got.get(kind, []) != list(want):
            problems.append(f"{kind}: tokens {got.get(kind, [])} (se esperaba {list(want)})")
    problems += [f"{kind}: tokens {tokens} que nadie esperaba" for kind, tokens in got.items() if kind not in expected]
    return problems


def restored_problems(text: str, values: Mapping[str, str]) -> list[str]:
    """Lo que le falta a lo que recibe el cliente para estar restaurado: los valores y ningún `[[`."""
    problems = [f"falta {kind} «{value}»" for kind, value in values.items() if value not in text]
    if "[[" in text:
        problems.append(f"quedan tokens sin restaurar: {sorted(set(TOKEN_RE.findall(text)))} ({text[:80]!r})")
    return problems


def conversation_items(api: str, body: Any) -> list[Any]:
    """Los mensajes (Messages y Chat) o entradas (Responses) de una petición."""
    key = "input" if api == "responses" else "messages"
    items = body.get(key) if isinstance(body, dict) else None
    return list(items) if isinstance(items, list) else []


def item_text(item: Any) -> str:
    content = item.get("content") if isinstance(item, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return ""


def turn_pair(api: str, text: str, reply: str) -> list[dict[str, Any]]:
    """El mensaje del usuario y la respuesta del modelo, tal como el cliente los reenvía en el turno siguiente."""
    if api == "responses":
        return [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply}]},
        ]
    return [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]


def new_user_text(api: str, body: Any) -> str:
    """El texto del último mensaje de una petición (lo nuevo de un segundo turno)."""
    items = conversation_items(api, body)
    return item_text(items[-1]) if items else ""


def raw_item(item: Any) -> str:
    """Un mensaje serializado con el orden de claves con que llegó al proveedor. El proveedor falso guarda el cuerpo ya
    interpretado (no los bytes), así que esto es lo más cerca de «byte a byte»: un cambio de valor, de orden o de
    nombre de campo se nota; un cambio de espacios o de escapes del JSON, no."""
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def history_problems(api: str, first: Any, second: Any, reply_prefix: str = ECHO) -> list[str]:
    """15.3 y 15.4: lo que recibió el proveedor en el segundo turno empieza por lo que recibió en el primero (mensaje a
    mensaje, idéntico), sigue con la respuesta del modelo tal como la dio (eco del primer mensaje, con sus tokens) y
    termina con lo nuevo del usuario."""
    before, after = conversation_items(api, first), conversation_items(api, second)
    if len(after) < len(before) + 2:
        return [f"el segundo turno trae {len(after)} mensaje(s): faltan la respuesta del modelo y lo nuevo "
                f"(el primero trae {len(before)})"]  # fmt: skip
    problems = []
    for i, (was, now) in enumerate(zip(before, after, strict=False)):
        if raw_item(was) != raw_item(now):
            problems.append(f"el mensaje {i} del historial no es idéntico al del primer turno: "
                            f"{h3.first_difference(was, now) or 'mismos valores, otro orden de claves'}")  # fmt: skip
    answer = after[len(before)]
    want = reply_prefix + item_text(before[-1])
    if not isinstance(answer, dict) or answer.get("role") != "assistant":
        problems.append(f"tras el historial no viene el mensaje del asistente: {str(answer)[:80]}")
    elif item_text(answer) != want:
        problems.append(f"el mensaje del asistente del historial no es lo que contestó el proveedor: "
                        f"{item_text(answer)[:80]!r} (se esperaba {want[:80]!r})")  # fmt: skip
    return problems


def _bytes(l2: Mapping[str, Any], name: str) -> int:
    value = l2.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def new_only_problems(
    first: Any, second: Any, new_bytes: int, *, cache_lost: bool = False, awaited_extra: int = 0
) -> list[str]:
    """15.3 y 15.4: `verification.l2` del primer y del segundo turno. Solo lo nuevo se analiza (`analyzed_bytes`); todo
    lo ya visto (`analyzed + cached + unanalyzed` del primero) sale de la caché (`cached_bytes`) o, con la caché
    perdida, se cuenta como no analizado (`unanalyzed_bytes`) sin esperar al NER. `awaited_extra`: bytes de texto de
    origen cliente que **no** es un mensaje ya visto y por eso sí se espera con la caché perdida (diseño §5.3:
    «posiblemente nuevo», p. ej. el `system`, que no es un mensaje de la correlación)."""
    if not isinstance(first, dict):
        return ["turno 1: sin verification.l2"]
    if not isinstance(second, dict):
        return ["turno 2: sin verification.l2"]
    problems = []
    for name, l2 in (("turno 1", first), ("turno 2", second)):
        if l2.get("status") != "APPLIED":
            problems.append(f"{name}: l2.status {l2.get('status')!r} (se esperaba APPLIED; causa {l2.get('cause')!r})")
    seen = sum(max(_bytes(first, n), 0) for n in ("analyzed_bytes", "cached_bytes", "unanalyzed_bytes"))
    analyzed, cached, unanalyzed = (_bytes(second, n) for n in ("analyzed_bytes", "cached_bytes", "unanalyzed_bytes"))
    expected_analyzed = new_bytes + (awaited_extra if cache_lost else 0)
    if analyzed != expected_analyzed:
        problems.append(f"turno 2: l2.analyzed_bytes {analyzed} (solo lo nuevo: {expected_analyzed} bytes)")
    if cache_lost:
        # ninguna capa tiene lo visto (ni Redis ni el LRU del proxy): se cuenta como no analizado y no se espera
        if cached != 0:
            problems.append(f"turno 2: l2.cached_bytes {cached} (con todas las cachés perdidas, 0)")
        if unanalyzed != seen - awaited_extra:
            problems.append(
                f"turno 2: l2.unanalyzed_bytes {unanalyzed} (los mensajes ya vistos, sin esperar al NER: "
                f"{seen - awaited_extra})"
            )
    else:
        if cached != seen:
            problems.append(f"turno 2: l2.cached_bytes {cached} (lo ya visto, de la caché: {seen})")
        if unanalyzed != 0:
            problems.append(f"turno 2: l2.unanalyzed_bytes {unanalyzed} (se esperaba 0)")
    return problems


def observed_problems(observed: Any, expected: Sequence[tuple[str, str]]) -> list[str]:
    """15.6: `observed_detections` de `turn.completed` (tipo y valor de lo que L2 habría sustituido, valores distintos
    por orden de aparición, con su regla y su número de apariciones)."""
    if not isinstance(observed, list) or not observed:
        return [f"observed_detections ausente o vacío ({observed!r}); se esperaban {list(expected)}"]
    items = [d for d in observed if isinstance(d, dict)]
    got = [(d.get("token_type"), d.get("original_value")) for d in items]
    problems = [f"falta {kind} «{value}»" for kind, value in expected if (kind, value) not in got]
    problems += [f"sobra {kind} «{value}»" for kind, value in got if (kind, value) not in expected]
    if not problems and got != list(expected):
        problems.append(f"orden distinto al de aparición: {got} (se esperaba {list(expected)})")
    for d in items:
        label = f"{d.get('token_type')} «{d.get('original_value')}»"
        if not isinstance(d.get("rule_id"), str) or not d["rule_id"]:
            problems.append(f"{label}: sin rule_id")
        count = d.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            problems.append(f"{label}: count {count!r} (se esperaba un entero ≥ 1)")
    return problems


def allowlisted_problems(observed: Any, term: str) -> list[str]:
    """15.8: `term`, ya en la *allowlist*, no puede estar entre lo observado (sin distinguir mayúsculas)."""
    if not isinstance(observed, list):
        return []
    wanted = term.casefold()
    return [
        f"«{d.get('original_value')}» ({d.get('token_type')}) está en la allowlist y se sigue observando"
        for d in observed
        if isinstance(d, dict) and str(d.get("original_value", "")).casefold() == wanted
    ]


def hidden_problems(observed: Any) -> list[str]:
    """La vista de verificación del PLATFORM_ADMIN no lleva los valores originales de lo observado."""
    if not isinstance(observed, list):
        return []
    return [
        f"{d.get('token_type')}: lleva original_value"
        for d in observed
        if isinstance(d, dict) and "original_value" in d
    ]


def live_test_problems(text: str, body: Any, expected: Sequence[tuple[str, str, str]]) -> list[str]:
    """15.7: la respuesta de `rules/test` con NER. `expected`: (tipo, valor, `result`). Cada coincidencia de NER lleva
    su capa, su `score`, desplazamientos en puntos de código y un token si se sustituye (`ALLOWLISTED` no)."""
    if not isinstance(body, dict):
        return [f"la respuesta no es un objeto: {str(body)[:100]}"]
    problems = []
    ner = body.get("ner")
    if not isinstance(ner, dict) or ner.get("status") != "OK":
        problems.append(f"ner {ner!r} (se esperaba status OK)")
    matches = [m for m in body.get("matches") or [] if isinstance(m, dict)]
    for kind, value, result in expected:
        found = [m for m in matches if m.get("token_type") == kind and m.get("value") == value]
        if not found:
            problems.append(
                f"{kind} «{value}»: sin coincidencia ({[(m.get('token_type'), m.get('value')) for m in matches]})"
            )
            continue
        m = found[0]
        if m.get("layer") != "NER":
            problems.append(f"{kind} «{value}»: layer {m.get('layer')!r} (se esperaba NER)")
        if m.get("result") != result:
            problems.append(f"{kind} «{value}»: result {m.get('result')!r} (se esperaba {result})")
        token = m.get("token")
        if result == "ALLOWLISTED" and token is not None:
            problems.append(f"{kind} «{value}»: ALLOWLISTED con token {token!r}")
        if result == "REPLACED" and not (isinstance(token, str) and token.startswith(f"[[{kind}_")):
            problems.append(f"{kind} «{value}»: token {token!r}")
        start, end = m.get("start"), m.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and text[start:end] == value):
            problems.append(f"{kind} «{value}»: desplazamientos {start}-{end} (puntos de código) no apuntan al valor")
        score = m.get("score")
        if not isinstance(score, int | float) or isinstance(score, bool):
            problems.append(f"{kind} «{value}»: sin score")
    return problems


def case_text(base: str, label: str) -> str:
    """El texto de un caso: el mismo con un sufijo propio. Un mismo primer mensaje del mismo usuario es **una**
    conversación (también entre Chat y Responses, que comparten proveedor) y la caché del NER es de la organización:
    sin el sufijo, un caso reutilizaría la conversación, el vault y la caché del anterior."""
    return f"{base} (caso {label})"


def provider_body(entry: Mapping[str, Any]) -> Any:
    """El cuerpo que guardó el proveedor falso de una petición, como JSON."""
    body = entry.get("body")
    return json.loads(body) if isinstance(body, str) else body


def describe(res: Http) -> str:
    data = res.json()
    error = data.get("error") if isinstance(data, dict) else None
    return f"HTTP {res.status}{' ' + str(error) if isinstance(error, str) else ''}" + (
        "" if res.status < 300 else f": {h3.snippet(res.body, 200)}"
    )


# --- la prueba ------------------------------------------------------------------------------------


@dataclass
class NerOrg:
    """Una organización de un paso: su slug, su id y el handshake de su usuario (con AI-IP activo)."""

    slug: str
    org_id: str
    binding: dict[str, Any]


class NerGroup:
    def __init__(self, e2e: E2EH4) -> None:
        self.e = e2e
        self.a = e2e.args
        self.nonce = e2e.nonce

    # --- quién -------------------------------------------------------------------------------------
    @property
    def admin(self) -> str:
        return str(self.a.admin_email)

    @property
    def user(self) -> str:
        return str(self.a.bypass_email)

    def logins(self) -> list[str]:
        return [self.admin, self.user]

    def inspector(self) -> Any:
        assert self.e.inspector is not None  # los pasos que lo usan solo se registran con inspector
        return self.e.inspector

    def apis(self) -> list[str]:
        return list(self.a.apis)

    def main_api(self) -> str:
        return "messages" if "messages" in self.a.apis else str(self.a.apis[0])

    # --- organizaciones de cada paso ---------------------------------------------------------------
    def put(self, slug: str, tail: str, body: dict[str, Any], who: str | None = None) -> Http:
        return self.e.webapp("PUT", f"/api/v1/orgs/{slug}/{tail}", who or self.admin, body)

    def create_org(self, label: str, *, ai_ip: bool = True, mode: str | None = None) -> NerOrg:
        slug = f"ner-{self.nonce}-{label}"
        res = self.e.admin_post("/api/v1/orgs", {"slug": slug, "name": f"E2E ner {label} {self.nonce}"})
        check(res.status == 201, f"POST /api/v1/orgs ({slug}) → {describe(res)}")
        data = (res.json() or {}).get("organization") or {}
        try:
            check(data.get("id"), f"POST /api/v1/orgs no devolvió el id de «{slug}»")
            res = self.e.admin_post(f"/api/v1/orgs/{slug}/members", {"email": self.user, "governance_role": "USER"})
            check(res.status == 201, f"añadir a {self.user} a «{slug}» → {describe(res)}")
            if ai_ip:
                self.enable_ai_ip(slug)
            if mode:
                self.set_ner(slug, mode=mode)
            binding = self.e.handshake(self.user, slug, ai_ip=True) if ai_ip else {}
            if ai_ip or mode:
                self.settle()
        except BaseException:
            with contextlib.suppress(Exception):  # no dejar una organización a medias
                self.drop_org(slug)
            raise
        return NerOrg(slug, str(data["id"]), binding)

    def enable_ai_ip(self, slug: str) -> None:
        res = self.put(slug, "ai-ip", {"enabled": True, "reason": REASON})
        check(res.status == 200, f"PUT …/{slug}/ai-ip → {describe(res)}")

    def set_ner(self, slug: str, **fields: Any) -> dict[str, Any]:
        res = self.put(slug, "ner", {**fields, "reason": REASON})
        check(res.status == 200, f"PUT …/{slug}/ner {fields} → {describe(res)}")
        return org_of(res)

    def org_state(self, slug: str) -> dict[str, Any]:
        res = self.e.webapp("GET", f"/api/v1/orgs/{slug}", self.admin)
        check(res.status == 200, f"GET /api/v1/orgs/{slug} → {describe(res)}")
        return org_of(res)

    def drop_org(self, slug: str) -> None:
        """Bloquea y elimina la organización (sin desactivar a nadie: el miembro es de la semilla)."""
        res = self.e.admin_post(f"/api/v1/orgs/{slug}/block", {"blocked": True, "reason": REASON}, method="PUT")
        if res.status == 404:
            return
        check(res.status == 200, f"bloquear «{slug}» → {describe(res)}")
        body = self.e.delete_body(reason=REASON, confirm_slug=slug, disable_orphans=False)
        res = self.e.admin_post(f"/api/v1/orgs/{slug}/delete", body)
        check(res.status == 200, f"eliminar «{slug}» → {describe(res)}")

    @contextlib.contextmanager
    def org(self, label: str, **options: Any) -> Iterator[NerOrg]:
        org = self.create_org(label, **options)
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

    # --- turnos --------------------------------------------------------------------------------------
    def model(self, label: str) -> str:
        return f"ner-{self.nonce}-{label}"

    def send(
        self, org: NerOrg, api: str, model: str, text: str, *, stream: bool = False, history: Sequence[Any] = ()
    ) -> tuple[bytes, Http, float]:
        payload = h3.request_body(api, model, text, stream=stream, history=history)
        start = time.monotonic()
        raw, res = self.e.send(org.binding, self.user, api, payload)
        return raw, res, time.monotonic() - start

    def sent_ok(self, org: NerOrg, api: str, model: str, text: str, **kw: Any) -> tuple[bytes, Http]:
        raw, res, _ = self.send(org, api, model, text, **kw)
        check(res.status == 200, f"{h3.API_NAMES[api]} en «{org.slug}» → HTTP {res.status}: {h3.snippet(res.body)}")
        return raw, res

    def reply_text(self, api: str, res: Http, *, stream: bool = False) -> str:
        reply = h3.extract_reply(api, res.body, stream=stream)
        check(reply.texts, f"{h3.API_NAMES[api]}: la respuesta no trae texto: {h3.snippet(res.body)}")
        return reply.texts[0][1]

    def provider_requests(self, model: str, api: str, *, count: int | None = 1) -> list[dict[str, Any]]:
        entries = self.e.provider_entries(model)
        if count is not None:
            check(
                len(entries) == count,
                f"el proveedor recibió {len(entries)} petición(es) de «{model}» (se esperaban {count})",
            )
        problems = self.e.path_problems(entries, api)
        check(not problems, "; ".join(problems))
        return entries

    def turn(self, org: NerOrg, model: str, index: int, who: str | None = None) -> dict[str, Any]:
        """El turno `index` de la conversación de `model` (como `who`; por omisión, quien envió). Espera la traza."""
        who = who or self.user
        conv = self.e.wait_conversation(self.user, model, org=org.slug, turns=index)
        last = ""

        def problems() -> list[str]:
            nonlocal last
            res = self.e.turn(who, str(conv["id"]), index)
            last = describe(res)
            return [] if res.status == 200 else [last]

        left = self.e.poll(problems)
        check(not left, f"el turno {index} de «{model}» no se puede leer ({who}): {last}")
        res = self.e.turn(who, str(conv["id"]), index)
        data = res.json()
        check(isinstance(data, dict), f"el turno {index} de «{model}» no es un objeto")
        return {**data, "_conversation_id": conv["id"]}

    @staticmethod
    def l2_of(turn: Mapping[str, Any]) -> dict[str, Any]:
        verification = turn.get("verification")
        l2 = verification.get("l2") if isinstance(verification, dict) else None
        check(isinstance(l2, dict), f"el turno no lleva verification.l2: {json.dumps(verification)[:200]}")
        return l2  # type: ignore[return-value]

    # --- 15.1 interruptores ---------------------------------------------------------------------------
    def settings(self) -> str:
        with self.org("ajustes", ai_ip=False) as org:
            slug = org.slug
            state = self.org_state(slug)
            got = (state.get("ner_enabled"), state.get("ner_mode"), state.get("ner_on_failure"))
            check(got == (True, "OBSERVE", "DEGRADE"),
                  f"una organización nueva tiene (ner_enabled, ner_mode, ner_on_failure) = {got} "
                  "(se esperaba (True, 'OBSERVE', 'DEGRADE'))")  # fmt: skip
            res = self.put(slug, "ner", {"mode": "ACTIVE", "reason": REASON})
            check(res.status == 409 and (res.json() or {}).get("error") == "AI_IP_DISABLED",
                  f"PUT /ner con AI-IP apagado → {describe(res)} (se esperaba 409 AI_IP_DISABLED)")  # fmt: skip
            self.enable_ai_ip(slug)
            res = self.put(slug, "ner", {"mode": "ACTIVE"})
            check(res.status == 422 and (res.json() or {}).get("error") == "REASON_REQUIRED",
                  f"PUT /ner sin motivo → {describe(res)} (se esperaba 422 REASON_REQUIRED)")  # fmt: skip
            for body in (
                {"reason": REASON},
                {"mode": "AUTO", "reason": REASON},
                {"on_failure": "IGNORE", "reason": REASON},
            ):
                res = self.put(slug, "ner", body)
                check(res.status == 422, f"PUT /ner {body} → {describe(res)} (se esperaba 422)")
            res = self.put(slug, "ner", {"mode": "ACTIVE", "reason": REASON}, who=self.user)
            check(res.status == 403, f"PUT /ner de un USER → {describe(res)} (se esperaba 403)")
            check(self.org_state(slug).get("ner_mode") == "OBSERVE", "los intentos rechazados cambiaron ner_mode")
            changed = self.set_ner(slug, mode="ACTIVE", on_failure="REJECT")
            check((changed.get("ner_enabled"), changed.get("ner_mode"), changed.get("ner_on_failure"))
                  == (True, "ACTIVE", "REJECT"), f"PUT /ner no devolvió lo cambiado: {changed}")  # fmt: skip
            res = self.e.webapp("GET", f"/api/v1/audit?org={slug}&action=org.ner.changed", self.admin)
            check(res.status == 200, f"GET /api/v1/audit → {describe(res)}")
            rows = [r for r in (res.json() or {}).get("items") or [] if r.get("action") == "org.ner.changed"]
            check(rows, f"la auditoría no tiene org.ner.changed de «{slug}»")
            row = rows[0]
            meta = row.get("metadata") or {}
            check(row.get("reason") == REASON and (meta.get("before") or {}).get("ner_mode") == "OBSERVE"
                  and (meta.get("after") or {}).get("ner_mode") == "ACTIVE"
                  and (meta.get("after") or {}).get("ner_on_failure") == "REJECT",
                  f"org.ner.changed sin motivo, antes y después: {json.dumps(row)[:300]}")  # fmt: skip
            res = self.put(slug, "anonymizer", {"enabled": False, "reason": REASON})
            check(res.status == 200, f"apagar el anonimizador → {describe(res)}")
            res = self.put(slug, "ner", {"enabled": False, "reason": REASON})
            check(res.status == 409 and (res.json() or {}).get("error") == "ANONYMIZER_DISABLED",
                  f"PUT /ner sin anonimizador → {describe(res)} (se esperaba 409 ANONYMIZER_DISABLED)")  # fmt: skip
            check(self.org_state(slug).get("ner_enabled") is True, "el 409 cambió ner_enabled")
            res = self.put(slug, "anonymizer", {"enabled": True, "reason": REASON})
            check(res.status == 200, f"encender el anonimizador → {describe(res)}")
            self.set_ner(slug, enabled=False)
            off = self.org_state(slug)
            self.set_ner(slug, enabled=True)
            on = self.org_state(slug)
            check((off.get("ner_enabled"), on.get("ner_enabled"), on.get("ner_mode")) == (False, True, "ACTIVE"),
                  f"apagar y encender el NER debe conservar el modo: apagado {off.get('ner_enabled')}, "
                  f"encendido {on.get('ner_enabled')}, modo {on.get('ner_mode')}")  # fmt: skip
        return (
            "nueva: NER encendido, OBSERVE, DEGRADE · 409 AI_IP_DISABLED y 409 ANONYMIZER_DISABLED · "
            "422 sin motivo o con valores no válidos · 403 a un USER · org.ner.changed con motivo, antes y después · "
            "apagar y encender conserva el modo"
        )

    # --- 15.2 tokens y restauración ---------------------------------------------------------------------
    def tokens(self) -> str:
        cases = [(api, stream) for api in self.apis() for stream in (False, True)]
        with self.org("tokens", mode="ACTIVE") as org:
            for api, stream in cases:
                where = f"{h3.API_NAMES[api]} · {h3.mode_name(stream)}"
                mode = "stream" if stream else "json"
                model = self.model(f"tok-{api}-{mode}")
                _, res = self.sent_ok(org, api, model, case_text(TEXT_1, f"{api} {mode}"), stream=stream)
                problems: list[str] = []
                reply = h3.extract_reply(api, res.body, stream=stream)
                if not reply.texts:
                    problems.append("la respuesta no trae texto")
                for place, text in reply.texts:
                    problems += [f"{place}: {p}" for p in restored_problems(text, VALUES)]
                [entry] = self.provider_requests(model, api)
                problems += [f"proveedor: {p}" for p in tokenization_problems(h3.debug_text(entry), VALUES, EXPECTED_1)]
                turn = self.turn(org, model, 1)
                problems += [f"turno de la webapp: {p}"
                             for p in tokenization_problems(turn.get("request_sent"), VALUES, EXPECTED_1)]  # fmt: skip
                l2 = self.l2_of(turn)
                if l2.get("status") != "APPLIED":
                    problems.append(f"l2.status {l2.get('status')!r} (se esperaba APPLIED)")
                by_type = (turn.get("verification") or {}).get("request_replacements_by_type")
                if by_type != {"PERSON": 1, "ORG": 1, "LOCATION": 1}:
                    problems.append(f"request_replacements_by_type {by_type}")
                check(not problems, f"{where}:\n" + "\n".join(problems))
        tokens = ", ".join(t[0] for t in EXPECTED_1.values())
        return (f"{len(cases)} casos ({', '.join(self.apis())}, con y sin streaming): «{VALUES['PERSON']}», "
                f"«{VALUES['ORG']}» y «{VALUES['LOCATION']}» salen como {tokens}, "
                "vuelven restaurados y el turno sale APPLIED")  # fmt: skip

    # --- 15.3 solo lo nuevo ------------------------------------------------------------------------------
    def two_turns(self, org: NerOrg, api: str, model: str, first: str, second: str) -> tuple[Any, Any]:
        """Primer turno (`first`) y segundo (con el historial y `second`): lo que recibió el proveedor en cada uno."""
        _, res = self.sent_ok(org, api, model, first)
        self.sent_ok(org, api, model, second, history=turn_pair(api, first, self.reply_text(api, res)))
        one, two = self.provider_requests(model, api, count=2)
        return provider_body(one), provider_body(two)

    def only_new(self) -> str:
        done = []
        with self.org("nuevo", mode="ACTIVE") as org:
            for api in self.apis():
                model = self.model(f"new-{api}")
                text_2 = case_text(TEXT_2, api)
                first, second = self.two_turns(org, api, model, case_text(TEXT_1, api), text_2)
                problems = history_problems(api, first, second)
                new = tokenization_problems(new_user_text(api, second), VALUES_2, EXPECTED_2)
                problems += [f"proveedor, lo nuevo: {p}" for p in new]
                l2_1, l2_2 = (self.l2_of(self.turn(org, model, i)) for i in (1, 2))
                problems += new_only_problems(l2_1, l2_2, len(text_2.encode()))
                check(not problems, f"{h3.API_NAMES[api]}:\n" + "\n".join(problems))
                done.append(f"{h3.API_NAMES[api]} {l2_2['analyzed_bytes']} B nuevos, {l2_2['cached_bytes']} B de caché")
        return "segundo turno: " + " · ".join(done) + " · historial idéntico y respuesta del modelo tal cual"

    # --- 15.4 caché perdida --------------------------------------------------------------------------------
    def wait_healthy(self, read: Callable[[], str], name: str) -> None:
        """Espera a que el contenedor esté `healthy` (`read` falla con un mensaje claro si no define healthcheck)."""
        deadline = time.monotonic() + self.a.ner_timeout
        while (health := read()) != "healthy":
            if time.monotonic() > deadline:
                raise StepError(f"el contenedor {name} no está sano tras {self.a.ner_timeout:g} s ({health})")
            time.sleep(1)

    def restart_proxy(self, org: NerOrg) -> None:
        """Para y arranca el proxy: su caché local (LRU en memoria) se pierde. Espera a que esté sano y a que Caddy lo
        sirva otra vez (una petición sin clave da `401`, no un error de conexión ni un `502`)."""
        insp = self.inspector()
        try:
            insp.service_control(PROXY, "stop")
        finally:  # `start` es idempotente: si `stop` agota el tiempo tras parar de verdad, vuelve a arrancar
            insp.service_control(PROXY, "start")
        self.wait_healthy(lambda: insp.service_health(PROXY), PROXY)
        url = h3.api_url(org.binding, "messages")
        deadline = time.monotonic() + self.a.ner_timeout
        while True:
            try:
                status = h3.http("POST", url, payload={}, timeout=5).status
            except OSError:
                status = 0
            if status == 401:
                return
            if time.monotonic() > deadline:
                raise StepError(f"el proxy no responde por Caddy tras {self.a.ner_timeout:g} s (HTTP {status})")
            time.sleep(1)

    def lost_cache(self) -> str:
        insp = self.inspector()
        api = self.main_api()
        with self.org("cache", mode="ACTIVE") as org:
            model = self.model("cache")
            _, res = self.sent_ok(org, api, model, TEXT_1)
            history = turn_pair(api, TEXT_1, self.reply_text(api, res))
            _, res = self.sent_ok(org, api, model, TEXT_2, history=history)
            history += turn_pair(api, TEXT_2, self.reply_text(api, res))
            second = self.turn(org, model, 2)
            cache = insp.ephemeral_scan(f"baas:ner:{org.org_id}:*")
            check(cache, f"la caché del NER de «{org.slug}» está vacía antes de vaciarla (baas:ner:{org.org_id}:*)")
            vault = insp.ephemeral_scan(f"baas:conv:{second['_conversation_id']}:vault")
            deleted = insp.ephemeral_delete([*cache, *vault])
            left = insp.ephemeral_scan(f"baas:ner:{org.org_id}:*") + insp.ephemeral_scan(
                f"baas:conv:{second['_conversation_id']}:vault"
            )
            check(not left, f"tras vaciar la efímera quedan {len(left)} clave(s) de la organización")
            # el LRU del proxy sobrevive al borrado de Redis: sin reiniciarlo, lo visto saldría de él y el paso no
            # probaría nada sobre esperar al NER
            self.restart_proxy(org)
            _, res, elapsed = self.send(org, api, model, TEXT_3, history=history)
            check(res.status == 200, f"el turno tras vaciar la efímera → HTTP {res.status}: {h3.snippet(res.body)}")
            _, req2, req3 = (provider_body(e) for e in self.provider_requests(model, api, count=3))
            expected_3 = {"PERSON": ["[[PERSON_003]]"], "ORG": ["[[ORG_002]]"], "LOCATION": ["[[LOCATION_003]]"]}
            problems = history_problems(api, req2, req3)
            problems += [f"proveedor, lo nuevo: {p}"
                         for p in tokenization_problems(new_user_text(api, req3), VALUES_3, expected_3)]  # fmt: skip
            third = self.turn(org, model, 3)
            problems += new_only_problems(
                self.l2_of(second), self.l2_of(third), len(TEXT_3.encode()), cache_lost=True,
                awaited_extra=len(h3.SYSTEM.encode()) if api in SYSTEM_OUTSIDE_MESSAGES else 0,
            )  # fmt: skip
            if elapsed > LOST_CACHE_BOUND:
                problems.append(
                    f"el turno tarda {elapsed:.2f} s (más de {LOST_CACHE_BOUND} s: ¿espera al NER por lo visto?)"
                )
            check(not problems, "\n".join(problems))
            again = self.e.poll(
                lambda: [] if insp.ephemeral_scan(f"baas:ner:{org.org_id}:*") else ["sin claves baas:ner:*"]
            )
            check(not again, "tras el turno, el NER no volvió a escribir su caché en la efímera")
            l2 = self.l2_of(third)
        return (
            f"{len(cache)} clave(s) de la caché del NER y {len(vault)} de la copia caliente del vault borradas "
            f"({deleted} DEL, solo las de la organización; no FLUSHALL) y el proxy reiniciado (sin su caché local) · "
            f"turno 3 en {elapsed * 1000:.0f} ms (límite {LOST_CACHE_BOUND * 1000:.0f} ms): "
            f"{l2['analyzed_bytes']} B nuevos, {l2['cached_bytes']} B de caché, "
            f"{l2['unanalyzed_bytes']} B de mensajes ya vistos sin esperar al NER · "
            "historial idéntico y tokens que siguen la numeración (el vault se recargó de PostgreSQL)"
        )

    # --- 15.5 NER caído ------------------------------------------------------------------------------------
    def degraded_turn(self, org: NerOrg, api: str, label: str) -> tuple[float, dict[str, Any], dict[str, Any]]:
        """Un turno con el NER parado y `DEGRADE`: sale (el proveedor lo recibe) y su `l2` lo dice."""
        model = self.model(f"down-{label}")
        _, res, elapsed = self.send(org, api, model, f"{TEXT_1} ({label})")
        check(res.status == 200, f"con DEGRADE y el NER parado → HTTP {res.status}: {h3.snippet(res.body)}")
        self.provider_requests(model, api)
        turn = self.turn(org, model, 1)
        l2 = self.l2_of(turn)
        verification = turn.get("verification") or {}
        problems = []
        if l2.get("status") != "DEGRADED" or l2.get("cause") not in DEGRADED_CAUSES:
            problems.append(
                f"l2 {l2.get('status')}/{l2.get('cause')} (se esperaba DEGRADED y una causa de {DEGRADED_CAUSES})"
            )
        if verification.get("status") != "WARNING" or turn.get("verification_status") != "WARNING":
            problems.append(f"el turno sale {verification.get('status')} (se esperaba WARNING)")
        check(not problems, f"turno «{label}»: " + "; ".join(problems))
        return elapsed, l2, turn

    def settle(self) -> None:
        """Una política nueva llega al proxy por Pub/Sub: se le da un momento antes del turno siguiente."""
        time.sleep(self.a.propagation_wait)

    def restore_ner(self, org: NerOrg, api: str) -> str:
        """Arranca `ner`, espera a que esté sano y a que el proxy vuelva a sustituir (con `REJECT`, un `200` es que el
        disyuntor se cerró y el NER respondió). Si no vuelve, falla: los grupos siguientes no pueden contar con él."""
        insp = self.inspector()
        insp.ner_control("start")
        start = time.monotonic()
        deadline = start + self.a.ner_timeout
        self.wait_healthy(lambda: insp.ner_health(), "ner")
        self.set_ner(org.slug, on_failure="REJECT")
        self.settle()
        tries = 0
        while True:
            tries += 1
            model = self.model(f"up-{tries}")
            _, res, _ = self.send(org, api, model, f"{TEXT_1} (sonda {tries})")
            if res.status == 200:
                [entry] = self.provider_requests(model, api)
                problems = tokenization_problems(h3.debug_text(entry), VALUES, EXPECTED_1)
                check(not problems, "con el NER de vuelta el turno no se sustituye: " + "; ".join(problems))
                return (
                    f"tras {time.monotonic() - start:.0f} s ({tries} sonda(s)) el proxy vuelve a sustituir con el NER"
                )
            if time.monotonic() > deadline:
                raise StepError(f"el NER no vuelve a servir tras {self.a.ner_timeout:g} s: HTTP {res.status}")
            time.sleep(2)

    def ner_down(self) -> str:
        insp = self.inspector()
        api = self.main_api()
        notes: list[str] = []
        with self.org("caido", mode="ACTIVE") as org:
            stopped = failed = False
            try:
                stopped = (
                    True  # antes de parar: si `podman stop` agota el tiempo tras parar de verdad, se reinicia igual
                )
                insp.ner_control("stop")
                elapsed, l2, _ = self.degraded_turn(org, api, "d0")
                notes.append(f"DEGRADE: DEGRADED/{l2['cause']} y WARNING ({elapsed:.1f} s)")
                for i in range(1, 9):  # cinco fallos abren el disyuntor (BAAS_NER_BREAKER_FAILURES)
                    elapsed, l2, _ = self.degraded_turn(org, api, f"d{i}")
                    if l2.get("cause") == "BREAKER_OPEN":
                        check(elapsed < SPEEDY, f"con el disyuntor abierto el turno espera {elapsed:.2f} s al NER")
                        notes.append(
                            f"disyuntor abierto tras {i + 1} turnos: el siguiente sale en {elapsed * 1000:.0f} ms"
                        )
                        break
                else:
                    raise StepError("el disyuntor del NER no se abrió tras 9 turnos con el servicio parado")
                self.set_ner(org.slug, on_failure="REJECT")
                self.settle()
                model = self.model("down-reject")
                _, res, elapsed = self.send(org, api, model, f"{TEXT_1} (rechazo)")
                check(res.status == 503, f"con REJECT y el NER parado → HTTP {res.status} (se esperaba 503): "
                                         f"{h3.snippet(res.body)}")  # fmt: skip
                self.provider_requests(model, api, count=0)
                turn = self.turn(org, model, 1)
                blocked = (turn.get("status"), turn.get("block_reason"), turn.get("http_status"))
                check(blocked == ("BLOCKED", "NER_UNAVAILABLE", 503),
                      f"el turno rechazado sale {blocked} (se esperaba BLOCKED, NER_UNAVAILABLE, 503)")  # fmt: skip
                notes.append(
                    f"REJECT: 503 en {elapsed * 1000:.0f} ms, turn.blocked NER_UNAVAILABLE y nada en el proveedor"
                )
            except BaseException:
                failed = True
                raise
            finally:
                if stopped:
                    try:
                        notes.append(self.restore_ner(org, api))
                    except Exception as exc:
                        if not failed:
                            raise
                        print(f"          (el NER no se pudo restablecer: {exc})")
        return " · ".join(notes)

    # --- 15.6 observación ----------------------------------------------------------------------------------
    def observe(self) -> str:
        cases: list[tuple[str, str, Callable[[NerOrg], None] | None, bool]] = [
            ("NER apagado", "apagado", lambda org: self.set_ner(org.slug, enabled=False), False),
            ("NER encendido en OBSERVE", "observe", None, True),
            ("anonimizador apagado", "anon", lambda org: self.switch_anonymizer(org, False), True),
        ]
        done = []
        for title, label, setup, enabled in cases:
            with self.org(f"obs-{label}") as org:
                if setup:
                    setup(org)
                    self.settle()
                for api in self.apis():
                    model = self.model(f"obs-{label}-{api}")
                    text = case_text(TEXT_1, api)
                    raw, res = self.sent_ok(org, api, model, text)
                    problems = []
                    if self.reply_text(api, res) != ECHO + text:
                        problems.append("el cliente no recibe lo mismo que envió")
                    [entry] = self.provider_requests(model, api)
                    same, _ = h3.same_payload(raw, entry.get("body"))
                    if not same:
                        problems.append("el cuerpo que recibió el proveedor no es idéntico al enviado")
                    turn = self.turn(org, model, 1)
                    l2 = self.l2_of(turn)
                    verification = turn.get("verification") or {}
                    got_l2 = (l2.get("status"), l2.get("enabled"), l2.get("observed"))
                    if got_l2 != ("OBSERVED", enabled, 3):
                        problems.append(f"l2 (status, enabled, observed) {got_l2} (se esperaba OBSERVED, {enabled}, 3)")
                    if (turn.get("status"), verification.get("status"), turn.get("verification_status")) != (
                        "OK",
                        "CLEAN",
                        "CLEAN",
                    ):
                        problems.append(f"lo observado cambió el estado del turno: {turn.get('status')}/"
                                        f"{verification.get('status')}")  # fmt: skip
                    if verification.get("request_replacements_by_type") or verification.get("residual_matches"):
                        problems.append(
                            f"el turno sustituyó o contó coincidencias residuales: {json.dumps(verification)[:200]}"
                        )
                    problems += observed_problems(turn.get("observed_detections"), OBSERVED_1)
                    admin = self.turn(org, model, 1, who=self.admin)
                    seen_by_admin = admin.get("observed_detections")
                    problems += [f"vista del PLATFORM_ADMIN: {p}" for p in hidden_problems(seen_by_admin)]
                    kinds = [d.get("token_type") for d in seen_by_admin or [] if isinstance(d, dict)]
                    if kinds != [kind for kind, _ in OBSERVED_1]:
                        problems.append(f"vista del PLATFORM_ADMIN: observed_detections {kinds} (con los tipos)")
                    check(not problems, f"{title} · {h3.API_NAMES[api]}:\n" + "\n".join(problems))
                done.append(title)
        return (
            f"{len(self.apis())} API(s) en tres casos ({', '.join(done)}): el cuerpo sale sin cambios, l2 OBSERVED, "
            "observed_detections con nombre, empresa y dirección (sin valores para el PLATFORM_ADMIN) y el turno CLEAN"
        )

    def switch_anonymizer(self, org: NerOrg, enabled: bool) -> None:
        res = self.put(org.slug, "anonymizer", {"enabled": enabled, "reason": REASON})
        check(res.status == 200, f"PUT …/{org.slug}/anonymizer → {describe(res)}")

    # --- 15.7 la prueba en vivo ---------------------------------------------------------------------------
    def live(self, org: NerOrg) -> dict[str, Any]:
        res = self.e.admin_post(f"/api/v1/orgs/{org.slug}/ai-ip/rules/test", {"text": TEXT_1, "draft": None})
        check(res.status == 200, f"POST …/{org.slug}/ai-ip/rules/test → {describe(res)}")
        body = res.json()
        check(isinstance(body, dict), f"la prueba en vivo no devuelve un objeto: {h3.snippet(res.body)}")
        return body

    def live_test(self) -> str:
        replaced = [(kind, value, "REPLACED") for kind, value in OBSERVED_1]
        with self.org("vivo", ai_ip=False) as org:
            body = self.live(org)
            problems = live_test_problems(TEXT_1, body, replaced)
            if body.get("anonymized") != TEXT_1.replace("Ana Gómez", "[[PERSON_001]]").replace(
                "Telefónica", "[[ORG_001]]"
            ).replace("Valencia", "[[LOCATION_001]]"):
                problems.append(f"anonymized: {body.get('anonymized')!r}")
            check(not problems, "sin allowlist:\n" + "\n".join(problems))
            res = self.e.admin_post(f"/api/v1/orgs/{org.slug}/ai-ip/allowlist", {"term": VALUES["ORG"]})
            check(res.status == 201, f"añadir «{VALUES['ORG']}» a la allowlist → {describe(res)}")
            term_id = ((res.json() or {}).get("term") or {}).get("id")
            check(term_id, "la allowlist no devolvió el id del término")
            body = self.live(org)
            allowed = [(k, v, "ALLOWLISTED" if k == "ORG" else r) for k, v, r in replaced]
            problems = live_test_problems(TEXT_1, body, allowed)
            if VALUES["ORG"] not in str(body.get("anonymized")):
                problems.append(f"«{VALUES['ORG']}» está en la allowlist y se sustituyó: {body.get('anonymized')!r}")
            check(not problems, "con «Telefónica» en la allowlist:\n" + "\n".join(problems))
            res = self.e.webapp("DELETE", f"/api/v1/orgs/{org.slug}/ai-ip/allowlist/{term_id}", self.admin)
            check(res.status == 200, f"quitar el término de la allowlist → {describe(res)}")
            problems = live_test_problems(TEXT_1, self.live(org), replaced)
            check(not problems, "tras quitar el término:\n" + "\n".join(problems))
        return (
            "rules/test: PERSON, ORG y LOCATION con layer NER, score y desplazamientos · con «Telefónica» en "
            "la allowlist, ALLOWLISTED sin token y el resto sigue REPLACED · al quitarlo, vuelve a REPLACED"
        )

    # --- 15.8 añadir desde W8 a la allowlist ----------------------------------------------------------------
    def observed_allowlist(self) -> str:
        term = VALUES["ORG"]
        api = self.main_api()
        with self.org("w8") as org:
            model = self.model(f"w8-antes-{api}")
            self.sent_ok(org, api, model, case_text(TEXT_1, f"w8-antes-{api}"))
            problems = observed_problems(self.turn(org, model, 1).get("observed_detections"), OBSERVED_1)
            check(not problems, "antes de añadir el término:\n" + "\n".join(problems))
            res = self.e.admin_post(f"/api/v1/orgs/{org.slug}/ai-ip/allowlist", {"term": term})
            check(res.status == 201, f"añadir «{term}» a la allowlist → {describe(res)}")
            check((res.json() or {}).get("propagated") is True, f"el término no se propagó al proxy: {describe(res)}")
            self.settle()
            # Otro primer mensaje: una conversación nueva (el mismo sería el turno 2 de la anterior). La caché del NER
            # guarda la salida sin filtrar (D101): la allowlist se aplica después, haya acierto de caché o no.
            model = self.model(f"w8-despues-{api}")
            self.sent_ok(org, api, model, case_text(TEXT_1, f"w8-despues-{api}"))
            observed = self.turn(org, model, 1).get("observed_detections")
            problems = allowlisted_problems(observed, term)
            problems += observed_problems(observed, [o for o in OBSERVED_1 if o[1] != term])
            check(not problems, f"con «{term}» en la allowlist:\n" + "\n".join(problems))
            # El término se va con la organización, que se elimina al salir
        return (
            f"{h3.API_NAMES[api]}: «{term}» observado → añadido a la allowlist de la organización (propagado) → el "
            "turno siguiente, en otra conversación, observa solo la persona y la dirección"
        )

    # --- el recorrido ----------------------------------------------------------------------------------
    def register(self, runner: Runner, base: list[str]) -> None:
        step = runner.step
        step("15.1 organización nueva: NER encendido en OBSERVE; PUT /ner con motivo y auditoría; 409 sin anonimizador",
             self.settings, needs=base)  # fmt: skip
        step("15.2 nombre, empresa y dirección salen como tokens y vuelven restaurados (3 APIs, con y sin streaming)",
             self.tokens, needs=base)  # fmt: skip
        step("15.3 segundo turno: l2.analyzed_bytes es solo lo nuevo y el historial enviado es idéntico",
             self.only_new, needs=base)  # fmt: skip
        if self.e.inspector is not None:
            step("15.4 vaciada la caché en la efímera, el historial sale idéntico y no se espera al NER",
                 self.lost_cache, needs=base)  # fmt: skip
            step("15.5 con ner parado: DEGRADE da DEGRADED y WARNING; REJECT da 503 y turn.blocked NER_UNAVAILABLE",
                 self.ner_down, needs=base)  # fmt: skip
        step("15.6 observación (NER apagado, en OBSERVE y anonimizador apagado): observed_detections y mismo estado",
             self.observe, needs=base)  # fmt: skip
        step("15.7 la prueba en vivo devuelve coincidencias de NER y ALLOWLISTED", self.live_test, needs=base)
        step("15.8 añadir desde W8 a la allowlist un valor observado: el turno siguiente ya no lo observa",
             self.observed_allowlist, needs=base)  # fmt: skip


def org_of(res: Http) -> dict[str, Any]:
    data = res.json()
    org = data.get("organization") if isinstance(data, dict) else None
    return org if isinstance(org, dict) else (data if isinstance(data, dict) else {})
