"""Grupo «catalogo» (pasos 17.0–17.10) de `dev/e2e_h4.py`: el catálogo de administración de skills y conocimiento.

Un paso por criterio de aceptación del contrato (`contrato-catalogo-admin.md`, §9 y §10) contra la pila real:

  17.0  la pila tiene la API del catálogo (`GET /api/v1/orgs/<org>/catalog/skills`); si no, falla con un mensaje
        claro y se **saltan** los demás pasos.
  17.1  un ORG_ADMIN lista skills y conocimiento y los ve todos, aunque no tenga esos roles.
  17.2  ficha de una skill: roles y su origen, dependencias (con existencia, estado y roles), «usada por» e
        invocaciones; ficha de un documento con «usada por».
  17.3  un USER recibe `403` en todas las rutas; sin sesión `401`; el ORG_ADMIN de otra organización, `404`.
  17.4  `PUT` de roles de una skill (sin `baas.yaml` previo) y de un documento: cambia quién lee con `baas_get_skill`
        y `baas_read_document`; el cuerpo del documento queda intacto; roles inexistentes → `422 INVALID_ROLES`.
  17.5  edición concurrente: dos `PUT` con el mismo `expected_sha256` → el segundo da `409` y no pisa nada.
  17.6  `GET <mcp>/internal/v1/orgs/<org>/skills` por Caddy → `404`, también con el token interno y con variantes
        codificadas (`%2e%2e`, barras, mayúsculas): nunca `200` ni datos.
  17.7  ninguna respuesta (de la API del catálogo ni de Caddy) contiene el cuerpo de un fichero, salvo las `200` de
        las dos rutas de contenido, que lo llevan a propósito: la de ficheros de una skill
        (`…/skills/<nombre>/files/<ruta>`, §3.4) y la de un documento de conocimiento
        (`…/knowledge-content/<ruta>`, §3.5); los errores de ambas (`403`, `404`, `413`, `415`, `422`) sí se escanean.
  17.8  desactivar una skill: el USER recibe `NOT_FOUND` y no la ve en el listado, el administrador sí («deprecated»),
        el `sync` del plugin la retira del disco, y al reactivarla vuelve (con sus roles).
  17.9  leer el contenido de un fichero de una skill (§3.4): el ORG_ADMIN abre `SKILL.md` y un script y recibe lo que
        se escribió (con `size` y `sha256` del disco y sin caché); USER `403`, sin sesión `401`, otra organización
        `404`, traversal y `_acl.yaml` `422`, más de 256 KiB `413`, binario `415`; una fila `skill.file_read` por
        lectura correcta (visible para el ORG_ADMIN) y ninguna por los fallos, sin el contenido.
  17.10 leer el contenido de un documento de conocimiento (§3.5): el ORG_ADMIN abre un `.md` y un `.sql` (en forma
        corta y completa: la misma ruta canónica) y recibe lo que se escribió (con `size` y `sha256` del disco y sin
        caché); USER `403`, sin sesión `401`, otra organización `404`, traversal, la raíz y `_acl.yaml` `422`, más de
        256 KiB `413`, binario `415`, un nombre con tildes (NFC) se abre; una fila `knowledge.file_read` por lectura
        correcta (visible para el ORG_ADMIN) y ninguna por los fallos, sin el contenido.

Orden: 17.7 → 17.9 → 17.10. 17.9 y 17.10 guardan en `self.seen` sus `200` con contenido (exentas del escaneo, como las
de 17.7); por eso la comprobación de 17.7 «exactamente dos respuestas exentas» cuenta solo las que él mismo provoca
(desde el principio del paso) y no las de todo `self.seen`: si no, ejecutar antes 17.9 o 17.10 la haría fallar.

Cada paso crea su organización (`cat-<ejecución>-<paso>`, con usuarios de la semilla de desarrollo como miembros, un
rol funcional `ventas` para el USER y otro, `soporte`, para quitárselo) y la elimina al terminar. Ningún paso mira
dentro de los contenedores: no necesita `--inspect`.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import tempfile
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import quote, urlsplit

import e2e_h3 as h3
import e2e_uso
from e2e_h2 import Http, StepError, check

from baas_plugin.envfile import BaasPaths
from baas_plugin.errors import McpToolError
from baas_plugin.mcp_client import McpClient
from baas_plugin.sync import ClaudePaths, SyncOptions, sync_organization

if TYPE_CHECKING:
    from e2e_h2 import Runner
    from e2e_h4 import E2EH4

META_ORG = "baas-ai-ip"
ROLE, OTHER_ROLE = "ventas", "soporte"
BASE, REPORT = "base", "informe"  # skills del escenario: `informe` depende de `base` y de dos documentos
DOC = "knowledge/ventas/guia.md"
MISSING_DOC = "knowledge/no/existe.md"
INTERNAL_TOKEN = "baas-dev-internal-token"  # BAAS_INTERNAL_TOKEN de env/dev/webapp-api.env y mcp-server.env
# Claves que nunca pueden venir en una respuesta de la API del catálogo (el cuerpo de un fichero)
BODY_KEYS = frozenset({"content", "body", "text", "excerpt", "data", "chunks"})
MAX_FILE_VIEW_BYTES = 262144  # tope de la lectura de un fichero de skill (contrato §3.4)
# Las dos rutas que devuelven un cuerpo (relativas a `…/catalog/`): la de ficheros de una skill (§3.4) y la del contenido
# de un documento de conocimiento (§3.5). Se comparan enteras (`fullmatch`) y sin `?`, `#` ni espacios (nada de cadenas de
# consulta ni de fragmentos: la ruta es la que se pide, no una que lleve esos textos dentro).
FILES_ROUTE = re.compile(r"skills/[^/?#\s]+/files/[^?#\s]+")
KNOWLEDGE_ROUTE = re.compile(r"knowledge-content/[^?#\s]+")
# Ficheros que 17.7 y 17.9 añaden a `base` (ruta dentro de la skill)
BIG, BINARY, LIMIT, NESTED_ACL = "scripts/grande.txt", "scripts/binario.bin", "scripts/limite.txt", "scripts/_acl.yaml"
# Documentos que 17.7 y 17.10 añaden a `knowledge/ventas/` (ruta completa)
K_SQL = "knowledge/ventas/consulta.sql"
K_ACCENT = unicodedata.normalize("NFC", "knowledge/ventas/guía-ñandú.md")
K_BIG, K_LIMIT, K_BINARY = "knowledge/ventas/grande.txt", "knowledge/ventas/limite.txt", "knowledge/ventas/binario.bin"
K_ACL = "knowledge/ventas/_acl.yaml"


# --- utilidades puras ----------------------------------------------------------------------------


def keys_in(value: Any) -> set[str]:
    """Todas las claves de objeto que aparecen en un JSON, a cualquier profundidad."""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(str(k))
            found |= keys_in(v)
    elif isinstance(value, list):
        for v in value:
            found |= keys_in(v)
    return found


def body_problems(label: str, raw: bytes, sentinels: dict[str, str]) -> list[str]:
    """Qué le sobra a una respuesta para que sea solo metadatos: un centinela del cuerpo de un fichero (tal cual o con
    los escapes de su JSON) o una clave que lleve contenido."""
    text = raw.decode(errors="replace")
    decoded = ""
    data: Any = None
    with contextlib.suppress(ValueError):
        data = json.loads(text)
        decoded = "\n".join(h3.strings_in(data))
    problems = [
        f"{label}: contiene el centinela {name} ({value})"
        for name, value in sentinels.items()
        if value in text or value in decoded
    ]
    problems += [f"{label}: lleva la clave de contenido «{k}»" for k in sorted(keys_in(data) & BODY_KEYS)]
    return problems


def catalog_api_problem(status: int, body: Any) -> str | None:
    """17.0: qué decir si `GET …/catalog/skills` (como PLATFORM_ADMIN, de una organización que existe) no da 200."""
    if status == 200:
        return None
    if status == 404:
        return (
            "pila sin el catálogo de administración: reconstruye con los repos del catálogo (webapp y mcp-server con "
            "feat/catalogo-*); GET …/catalog/skills → 404"
        )
    error = body.get("error") if isinstance(body, dict) else None
    if status == 503:
        return (
            f"el MCP de la pila no ofrece /internal/v1 o la webapp no llega a él ({error or 'sin código'}): ¿mcp-server "
            "con el catálogo y BAAS_MCP_ADMIN_URL en webapp-api.env?; GET …/catalog/skills → 503"
        )
    return f"GET …/catalog/skills → HTTP {status}{' ' + str(error) if error else ''} (se esperaba 200)"


def internal_paths(org: str) -> list[str]:
    """Rutas con las que alguien intentaría llegar a `/internal/v1` del MCP por el sitio público (17.6): la buena y
    variantes de mayúsculas, barras, puntos y codificación."""
    skills = f"v1/orgs/{org}/skills"
    return [
        f"/internal/{skills}",
        f"/internal/{skills}/",
        f"/INTERNAL/{skills}",
        f"/Internal/{skills}",
        f"/%69nternal/{skills}",
        f"/internal%2Fv1/orgs/{org}/skills",
        f"/internal/%2e%2e/internal/{skills}",
        f"/%2e%2e/internal/{skills}",
        f"/mcp/%2e%2e/internal/{skills}",
        f"/mcp/../internal/{skills}",
        f"/./internal/{skills}",
        f"//internal/{skills}",
        f"/internal/v1/orgs/{org}/knowledge/..%2f..%2f..%2f..%2fetc%2fpasswd",
        f"/internal/v1/orgs/{org}/knowledge/%2e%2e/%2e%2e/etc/passwd",
        "/internal/v1/orgs/%2e%2e/%2e%2e/etc/passwd",
        "/internal",
        "/internal/",
        "/internal/v1",
    ]


def leak_problems(path: str, res: Http, with_token: bool) -> list[str]:
    """17.6: lo que no puede contestar Caddy a una ruta interna: `200`, datos del catálogo o ficheros del equipo."""
    how = "con el token interno" if with_token else "sin token"
    text = res.body.decode(errors="replace")
    problems = []
    if res.status < 400:
        problems.append(f"{path} ({how}) → HTTP {res.status} (se esperaba un error)")
    if '"skills"' in text or "root:" in text or '"documents"' in text:
        problems.append(f"{path} ({how}) devuelve datos: {h3.snippet(res.body, 120)}")
    return problems


class Seen(NamedTuple):
    """Una respuesta de la prueba: qué se pidió, su cuerpo y si lleva el cuerpo de un fichero a propósito (la `200` de
    la ruta de ficheros de una skill)."""

    label: str
    body: bytes
    content_expected: bool = False


def is_file_content(method: str, path: str, status: int) -> bool:
    """17.7: ¿es la respuesta una `200` de una de las dos rutas de contenido (`GET skills/<nombre>/files/<ruta>` o
    `GET knowledge-content/<ruta>`, ambas relativas a `…/catalog/`)? Es lo único que puede llevar un cuerpo; un error de
    esas mismas rutas, no."""
    if method != "GET" or status != 200:
        return False
    return FILES_ROUTE.fullmatch(path) is not None or KNOWLEDGE_ROUTE.fullmatch(path) is not None


def scan_seen(seen: list[Seen], sentinels: dict[str, str]) -> list[str]:
    """17.7: los problemas de todas las respuestas recogidas, salvo las que llevan un cuerpo a propósito."""
    problems: list[str] = []
    for item in seen:
        if not item.content_expected:
            problems += body_problems(item.label, item.body, sentinels)
    return problems


def header_value(res: Http, name: str) -> str | None:
    """El valor de una cabecera de la respuesta (sin distinguir mayúsculas), o `None`."""
    for key, value in res.headers:
        if key.lower() == name.lower():
            return value
    return None


def no_store_problems(label: str, res: Http) -> list[str]:
    """17.9: la `200` con el contenido de un fichero no se guarda en caché (`Cache-Control: no-store`, `Pragma: no-cache`)."""
    problems = []
    cache = header_value(res, "Cache-Control") or ""
    if "no-store" not in [d.strip().lower() for d in cache.split(",")]:
        problems.append(f"{label}: Cache-Control {cache!r} (se esperaba no-store)")
    pragma = header_value(res, "Pragma") or ""
    if pragma.strip().lower() != "no-cache":
        problems.append(f"{label}: Pragma {pragma!r} (se esperaba no-cache)")
    return problems


def sized_text(head: str, size: int) -> bytes:
    """Un texto UTF-8 ASCII de exactamente `size` bytes que empieza por `head` (para 413: 262145 bytes)."""
    text = (head + "\n").encode()
    line = b"x" * 79 + b"\n"
    body = text + line * ((size - len(text)) // len(line))
    body += b"y" * (size - len(body))
    return body


# --- la prueba ------------------------------------------------------------------------------------


class CatalogoGroup(e2e_uso.UsoGroup):
    """Reutiliza de `uso` la creación de organizaciones de cada paso, las sesiones y los clientes del MCP."""

    slug_prefix = "cat"  # `<prefijo>-<ejecución>-<paso>`, como lo usa `UsoGroup.create_org` (y `consumo`, con el suyo)

    def __init__(self, e2e: E2EH4) -> None:
        super().__init__(e2e)
        self.sentinels = {
            "SKILL.md": f"CATALOGO-CUERPO-SKILL-{self.nonce}",
            "documento": f"CATALOGO-CUERPO-DOC-{self.nonce}",
            "script": f"CATALOGO-CUERPO-SCRIPT-{self.nonce}",
            "grande": f"CATALOGO-CUERPO-GRANDE-{self.nonce}",
            "binario": f"CATALOGO-CUERPO-BINARIO-{self.nonce}",
            "limite": f"CATALOGO-CUERPO-LIMITE-{self.nonce}",
            # los documentos de conocimiento que añaden 17.7 y 17.10 (el `.md` de siempre lleva «documento»)
            "doc-sql": f"CATALOGO-CUERPO-DOC-SQL-{self.nonce}",
            "doc-acento": f"CATALOGO-CUERPO-DOC-ACENTO-{self.nonce}",
            "doc-grande": f"CATALOGO-CUERPO-DOC-GRANDE-{self.nonce}",
            "doc-limite": f"CATALOGO-CUERPO-DOC-LIMITE-{self.nonce}",
            "doc-binario": f"CATALOGO-CUERPO-DOC-BINARIO-{self.nonce}",
        }
        self.seen: list[Seen] = []  # cada respuesta de los pasos: qué se pidió, su cuerpo y si lleva uno a propósito
        self.mcp_urls: dict[str, str] = {}
        self.internal_token = getattr(e2e.args, "internal_token", INTERNAL_TOKEN)

    # --- organizaciones y escenario -----------------------------------------------------------------
    def create_org(self, label: str, members: dict[str, str], name: str | None = None) -> e2e_uso.UsoOrg:
        """Como la de `uso`, pero con el prefijo `cat-` y los roles funcionales `ventas` (del USER) y `soporte`."""
        slug = f"{self.slug_prefix}-{self.nonce}-{label}"
        res = self.e.admin_post("/api/v1/orgs", {"slug": slug, "name": name or f"E2E catalogo {label} {self.nonce}"})
        check(res.status == 201, f"POST /api/v1/orgs ({slug}) → {e2e_uso.describe(res)}")
        data = (res.json() or {}).get("organization") or {}
        org = e2e_uso.UsoOrg(
            slug, str(data.get("id") or ""), e2e_uso.ZoneInfo(str(data.get("timezone") or "Europe/Madrid"))
        )
        try:
            check(org.org_id, f"POST /api/v1/orgs no devolvió el id de «{slug}»")
            for role in (ROLE, OTHER_ROLE):
                made = self.e.admin_post(
                    f"/api/v1/orgs/{slug}/roles", {"slug": role, "name": role.title(), "description": role}
                )
                check(made.status == 201, f"crear el rol {role} en «{slug}» → {e2e_uso.describe(made)}")
            self.add_members(org, members)
        except BaseException:
            with contextlib.suppress(Exception):  # no dejar una organización a medias
                self.drop_org(slug)
            raise
        return org

    def add_members(self, org: e2e_uso.UsoOrg, members: dict[str, str]) -> None:
        slug = org.slug
        for email, role in members.items():
            payload: dict[str, Any] = {"email": email, "governance_role": role}
            if role == "USER":
                payload["functional_roles"] = [ROLE]
            res = self.e.admin_post(f"/api/v1/orgs/{slug}/members", payload)
            check(res.status == 201, f"añadir a {email} ({role}) a «{slug}» → {e2e_uso.describe(res)}")
            member = (res.json() or {}).get("member") or {}
            if member.get("user_id"):
                self.user_ids[email] = str(member["user_id"])
            self.key(email)
            org.bindings[email] = self.binding(email, slug)

    def binding(self, email: str, slug: str) -> dict[str, Any]:
        """La vinculación de `email` con la organización `slug` (la URL del MCP es `<origen>/org/<slug>/mcp`). Un solo
        handshake por persona y ejecución: el de la webapp admite 10 por clave y minuto, y cada paso crea su organización."""
        if email not in self.mcp_urls:
            first = self.e.handshake(email, self.a.bypass_org, ai_ip=False)
            self.mcp_urls[email] = str(first["mcp_url"]).replace(f"/org/{self.a.bypass_org}/", "/org/{slug}/")
            check("{slug}" in self.mcp_urls[email], f"la URL del MCP no lleva /org/<slug>/: {first['mcp_url']}")
        return {"slug": slug, "org": slug, "mcp_url": self.mcp_urls[email].replace("{slug}", slug)}

    @contextlib.contextmanager
    def scenario(self, label: str) -> Iterator[e2e_uso.UsoOrg]:
        """Una organización con un ORG_ADMIN y un USER (rol `ventas`) y este contenido, escrito por el ORG_ADMIN:

        - `skills/base` (sin `baas.yaml`: roles de la cabecera de `SKILL.md`, `[ventas]`) con un script;
        - `skills/informe` (con `baas.yaml`: roles `[ventas]`, depende de `base`, del documento y de uno que no existe);
        - `knowledge/ventas/guia.md` (roles `[ventas]`)."""
        with self.org(label, {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:
            skill_md = self.skill_md
            manifest = f"version: 1\nroles: [{ROLE}]\ndepends_on:\n  skills: [{BASE}]\n  knowledge:\n    - {DOC}\n    - {MISSING_DOC}\n"
            files = [
                {"path": f"skills/{BASE}/SKILL.md", "content": skill_md(BASE)},
                {"path": f"skills/{BASE}/scripts/hola.py", "content": self.script_text()},
                {"path": f"skills/{REPORT}/SKILL.md", "content": skill_md(REPORT)},
                {"path": f"skills/{REPORT}/baas.yaml", "content": manifest},
            ]
            with self.mcp(org, self.org_admin) as client:
                self.call(client, "baas_write_files", {"files": files})
            self.write_doc_roles(org, DOC, self.doc_body())
            yield org

    def skill_md(self, name: str) -> str:
        """El `SKILL.md` que el escenario escribe (con el centinela del cuerpo)."""
        return (
            f"---\ntitle: {name.title()}\ntype: skill\nroles: [{ROLE}]\nstatus: active\n"
            f"description: Prueba {self.nonce}.\n---\n# {name}\n{self.sentinels['SKILL.md']}\n"
        )

    def script_text(self) -> str:
        return f"print('{self.sentinels['script']}')\n"

    def view_files(self) -> dict[str, bytes]:
        """Lo que 17.7 y 17.9 añaden a `base` (ruta dentro de la skill → bytes): un fichero que pasa del tope por un byte,
        otro que lo alcanza justo, un binario (con NUL y bytes que no son UTF-8, y un centinela por si se filtra) y un
        `_acl.yaml` de subcarpeta (forma parte del paquete, pero no se abre)."""
        s = self.sentinels
        return {
            BIG: sized_text(s["grande"], MAX_FILE_VIEW_BYTES + 1),
            LIMIT: sized_text(s["limite"], MAX_FILE_VIEW_BYTES),
            BINARY: b"\x00\xff\xfe" + s["binario"].encode() + bytes(range(256)),
            NESTED_ACL: f"roles: [{ROLE}]\n".encode(),
        }

    def write_view_files(self, org: e2e_uso.UsoOrg) -> None:
        """Escribe `view_files` en `skills/base` como ORG_ADMIN con `baas_write_files` (`encoding: base64`: son bytes)."""
        files = [
            {"path": f"skills/{BASE}/{path}", "content": base64.b64encode(raw).decode(), "encoding": "base64"}
            for path, raw in self.view_files().items()
        ]
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_files", {"files": files})

    def doc_text(self, text: str) -> str:
        """Un documento de conocimiento `.md` completo (cabecera con el rol `ventas` y `text` de cuerpo)."""
        return f"---\ntitle: Guia {self.nonce}\ntype: knowledge\nroles: [{ROLE}]\n---\n{text}\n"

    def doc_body(self) -> str:
        """El cuerpo de `DOC` (con el centinela)."""
        return f"{self.sentinels['documento']}\nCuerpo del documento."

    def write_doc_roles(self, org: e2e_uso.UsoOrg, path: str, text: str) -> None:
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_document", {"path": path, "content": self.doc_text(text)})

    def view_docs(self) -> dict[str, bytes]:
        """Lo que 17.7 y 17.10 añaden a `knowledge/ventas/` (ruta completa → bytes): un `.sql`, un `.md` con tildes en el
        nombre (NFC), un documento que pasa del tope por un byte, otro que lo alcanza justo, un binario (con NUL y bytes
        que no son UTF-8, y un centinela por si se filtra) y un `_acl.yaml` de subcarpeta (no se abre)."""
        s = self.sentinels
        return {
            K_SQL: f"-- {s['doc-sql']}\nSELECT 1 AS uno;\n".encode(),
            K_ACCENT: self.doc_text(f"{s['doc-acento']}\nÁrbol, canción y ñandú: acentos en el nombre.").encode(),
            K_BIG: sized_text(s["doc-grande"], MAX_FILE_VIEW_BYTES + 1),
            K_LIMIT: sized_text(s["doc-limite"], MAX_FILE_VIEW_BYTES),
            K_BINARY: b"\x00\xff\xfe" + s["doc-binario"].encode() + bytes(range(256)),
            K_ACL: f"roles: [{ROLE}]\n".encode(),
        }

    def write_view_docs(self, org: e2e_uso.UsoOrg) -> None:
        """Escribe `view_docs` como ORG_ADMIN con `baas_write_files` (`encoding: base64`: son bytes; los documentos de
        conocimiento admiten cualquier extensión y tamaño dentro del tope del almacén)."""
        files = [
            {"path": path, "content": base64.b64encode(raw).decode(), "encoding": "base64"}
            for path, raw in self.view_docs().items()
        ]
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_files", {"files": files})

    # --- la API del catálogo -------------------------------------------------------------------------
    def api(self, method: str, org: e2e_uso.UsoOrg | str, path: str, who: str | None, payload: Any = None) -> Http:
        """Una petición a la API del catálogo como `who` (`None`: sin sesión); se guarda su respuesta para 17.7."""
        slug = org if isinstance(org, str) else org.slug
        url = f"/api/v1/orgs/{slug}/catalog/{path}"
        if who is None:
            headers = {"X-Requested-With": "baas-webapp"} if method != "GET" else {}
            res = h3.http(method, f"{self.a.app_url}{url}", payload=payload, headers=headers, timeout=self.a.timeout)
        else:
            res = self.e.webapp(method, url, who, payload)
        self.seen.append(
            Seen(f"{method} {url} ({who or 'sin sesión'})", res.body, is_file_content(method, path, res.status))
        )
        return res

    def api_json(
        self, method: str, org: e2e_uso.UsoOrg | str, path: str, who: str, payload: Any = None, status: int = 200
    ) -> Any:
        res = self.api(method, org, path, who, payload)
        data = res.json()
        check(
            res.status == status and isinstance(data, dict),
            f"{method} …/catalog/{path} ({who}) → {e2e_uso.describe(res)}",
        )
        return data

    def put_roles(
        self, org: e2e_uso.UsoOrg, kind: str, name: str, roles: list[str], expected: str | None, who: str | None = None
    ) -> Http:
        return self.api(
            "PUT", org, f"{kind}/{name}/roles", who or self.org_admin, {"roles": roles, "expected_sha256": expected}
        )

    def put_status(self, org: e2e_uso.UsoOrg, name: str, status: str, expected: str | None) -> Http:
        return self.api(
            "PUT", org, f"skills/{name}/status", self.org_admin, {"status": status, "expected_sha256": expected}
        )

    def status_audit_rows(self, org: e2e_uso.UsoOrg) -> int:
        """Cuántas filas `skill.status_changed` sobre `BASE` tiene la auditoría de `org` (filtrada por su slug: las filas
        de organizaciones eliminadas sobreviven y llenarían las últimas 200) (la ve el PLATFORM_ADMIN)."""
        res = self.e.webapp("GET", f"/api/v1/audit?action=skill.status_changed&org={org.slug}&limit=200", self.admin)
        check(res.status == 200, f"GET /api/v1/audit → {e2e_uso.describe(res)}")
        items = (res.json() or {}).get("items") or []
        return sum(1 for i in items if isinstance(i, dict) and i.get("target_id") == BASE)

    def skill_detail(self, org: e2e_uso.UsoOrg, name: str) -> dict[str, Any]:
        return self.api_json("GET", org, f"skills/{name}", self.org_admin)

    def skill_names(self, org: e2e_uso.UsoOrg) -> dict[str, dict[str, Any]]:
        data = self.api_json("GET", org, "skills", self.org_admin)
        return {str(s.get("name")): s for s in data.get("skills") or [] if isinstance(s, dict)}

    @staticmethod
    def short(path: str) -> str:
        """La ruta de un documento en forma corta (sin `knowledge/`), como la usa la API de la webapp."""
        return path.removeprefix("knowledge/")

    def mcp_error(self, client: McpClient, tool: str, args: dict[str, Any]) -> str:
        """El código de error de una tool que se espera que falle (y falla el paso si no falla)."""
        try:
            client.call_tool(tool, args)
        except McpToolError as exc:
            return str(exc.code)
        except Exception as exc:
            raise StepError(f"{tool}: {type(exc).__name__}: {exc}") from None
        raise StepError(f"{tool} {args} funcionó y debía fallar")

    # --- 17.0 la pila tiene la API --------------------------------------------------------------------
    def stack_has_api(self) -> str:
        res = self.e.webapp("GET", f"/api/v1/orgs/{META_ORG}/catalog/skills", self.admin)
        why = catalog_api_problem(res.status, res.json())
        check(why is None, why or "")
        count = len((res.json() or {}).get("skills") or [])
        return (
            f"GET /api/v1/orgs/{META_ORG}/catalog/skills → 200 ({count} skills de la organización meta; la API existe)"
        )

    # --- 17.1 listados ----------------------------------------------------------------------------------
    def listing(self) -> str:
        with self.scenario("li") as org:
            skills = self.skill_names(org)
            check(set(skills) == {BASE, REPORT}, f"skills listadas: {sorted(skills)} (se esperaba {[BASE, REPORT]})")
            for name, item in skills.items():
                check(item.get("roles") == [ROLE], f"«{name}»: roles {item.get('roles')} (se esperaba [{ROLE}])")
                check(item.get("status") == "active", f"«{name}»: estado {item.get('status')}")
                check(
                    isinstance(item.get("package_sha256"), str) and item["package_sha256"],
                    f"«{name}» sin package_sha256",
                )
            check(
                skills[BASE]["roles_source"] == "skill_md", f"roles_source de «{BASE}»: {skills[BASE]['roles_source']}"
            )
            check(
                skills[REPORT]["roles_source"] == "baas.yaml",
                f"roles_source de «{REPORT}»: {skills[REPORT]['roles_source']}",
            )
            data = self.api_json("GET", org, "knowledge", self.org_admin)
            docs = {str(d.get("path")): d for d in data.get("documents") or [] if isinstance(d, dict)}
            check(DOC in docs, f"el documento {DOC} no está en el listado: {sorted(docs)}")
            check(docs[DOC]["roles"] == [ROLE], f"roles de {DOC}: {docs[DOC]['roles']}")
            check(docs[DOC].get("sha256") and docs[DOC].get("size"), f"{DOC} sin huella o tamaño: {docs[DOC]}")
            who = self.api_json("GET", org, "skills", self.admin)  # el PLATFORM_ADMIN también lo ve
            check(
                {s.get("name") for s in who.get("skills") or []} == {BASE, REPORT},
                "el PLATFORM_ADMIN no ve las mismas skills",
            )
        return (
            f"ORG_ADMIN (sin el rol «{ROLE}») ve {len(skills)} skills ({BASE}: skill_md, {REPORT}: baas.yaml) y "
            f"{len(docs)} documento con sus roles; el PLATFORM_ADMIN lo mismo"
        )

    # --- 17.2 fichas ------------------------------------------------------------------------------------
    def details(self) -> str:
        with self.scenario("de") as org:
            detail = self.skill_detail(org, REPORT)
            check(
                detail["roles"] == [ROLE] and detail["roles_source"] == "baas.yaml",
                f"roles de la ficha: {detail['roles']} / {detail['roles_source']}",
            )
            check(
                isinstance(detail.get("roles_sha256"), str),
                f"roles_sha256 de «{REPORT}»: {detail.get('roles_sha256')!r}",
            )
            paths = {f["path"] for f in detail["file_list"]}
            check(paths == {f"skills/{REPORT}/SKILL.md", f"skills/{REPORT}/baas.yaml"}, f"file_list: {sorted(paths)}")
            check(
                all(f.get("sha256") and f.get("encoding") == "utf8" for f in detail["file_list"]),
                "file_list sin huella o codificación",
            )
            deps = detail["depends_on"]
            check(
                deps["skills"] == [{"name": BASE, "exists": True, "status": "active", "roles": [ROLE]}],
                f"depends_on.skills: {deps['skills']}",
            )
            found, missing = deps["knowledge"]
            check(
                found["path"] == DOC and found["exists"] is True and found["roles"] == [ROLE] and found["title"],
                f"dependencia de conocimiento: {found}",
            )
            check(
                missing == {"path": MISSING_DOC, "exists": False, "title": None, "roles": []},
                f"dependencia inexistente: {missing}",
            )
            usage = detail.get("usage")
            check(
                isinstance(usage, dict) and usage.get("invoked_30d") == 0 and usage.get("last_invoked_at") is None,
                f"usage: {usage}",
            )
            base = self.skill_detail(org, BASE)
            check([u["name"] for u in base["used_by"]] == [REPORT], f"used_by de «{BASE}»: {base['used_by']}")
            check(
                base["used_by"][0]["roles"] == [ROLE] and base["used_by"][0]["title"],
                f"used_by sin título o roles: {base['used_by']}",
            )
            check(base["roles_sha256"] is None, f"«{BASE}» no tiene baas.yaml: roles_sha256 {base['roles_sha256']!r}")
            doc = self.api_json("GET", org, f"knowledge/{self.short(DOC)}", self.org_admin)
            check([u["name"] for u in doc["used_by"]] == [REPORT], f"used_by del documento: {doc['used_by']}")
            check(doc["path"] == DOC and doc["sha256"], f"ficha del documento: {doc}")
            summary = self.skill_names(org)
            check(summary[BASE]["used_by"] == [REPORT], f"used_by del resumen: {summary[BASE]['used_by']}")
            check(
                summary[REPORT]["depends_on"] == {"skills": [BASE], "knowledge": [DOC, MISSING_DOC]},
                f"depends_on del resumen: {summary[REPORT]['depends_on']}",
            )
            absent = self.api("GET", org, "skills/no-existe", self.org_admin)
            check(
                absent.status == 404 and (absent.json() or {}).get("error") == "NOT_FOUND",
                f"skill inexistente → {e2e_uso.describe(absent)}",
            )
        return (
            f"«{REPORT}»: roles baas.yaml, 2 ficheros con huella, depende de «{BASE}» (active), de {DOC} (existe) y de otro "
            f"que no existe; «{BASE}» y {DOC} dicen «usada por» {REPORT}; usage 0 invocaciones; skill inexistente → 404"
        )

    # --- 17.3 permisos ------------------------------------------------------------------------------------
    def permissions(self) -> str:
        with self.scenario("pe") as org:
            routes = [
                ("GET", "skills", None),
                ("GET", f"skills/{BASE}", None),
                ("GET", "knowledge", None),
                ("GET", f"knowledge/{self.short(DOC)}", None),
                ("PUT", f"skills/{BASE}/roles", {"roles": [OTHER_ROLE], "expected_sha256": None}),
                ("PUT", f"skills/{BASE}/status", {"status": "deprecated", "expected_sha256": None}),
                ("PUT", f"knowledge/{self.short(DOC)}/roles", {"roles": [OTHER_ROLE], "expected_sha256": None}),
            ]
            for method, path, payload in routes:
                res = self.api(method, org, path, self.user, payload)
                check(
                    res.status == 403 and (res.json() or {}).get("error") == "FORBIDDEN",
                    f"{method} …/{path} como USER → {e2e_uso.describe(res)} (se esperaba 403 FORBIDDEN)",
                )
                res = h3.http(
                    method,
                    f"{self.a.app_url}/api/v1/orgs/{org.slug}/catalog/{path}",
                    payload=payload,
                    headers={"X-Requested-With": "baas-webapp"},
                    timeout=self.a.timeout,
                )
                check(res.status == 401, f"{method} …/{path} sin sesión → HTTP {res.status} (se esperaba 401)")
                res = self.api(method, org, path, e2e_uso.OTHER_ADMIN, payload)
                check(
                    res.status == 404,
                    f"{method} …/{path} como ORG_ADMIN de otra organización → {e2e_uso.describe(res)} (se esperaba 404)",
                )
            ghost = self.api("GET", f"cat-{self.nonce}-fantasma", "skills", self.admin)
            check(ghost.status == 404, f"organización inexistente → {e2e_uso.describe(ghost)} (se esperaba 404)")
            # lo que el USER no pudo cambiar sigue igual
            check(
                self.skill_detail(org, BASE)["roles"] == [ROLE],
                "los roles de «base» cambiaron por una petición rechazada",
            )
        return (
            f"{len(routes)} rutas: USER → 403 FORBIDDEN, sin sesión → 401, ORG_ADMIN de otra organización → 404, "
            "organización inexistente → 404; nada cambió"
        )

    # --- 17.4 cambiar roles ---------------------------------------------------------------------------------
    def change_roles(self) -> str:
        with self.scenario("ro") as org:
            s = self.sentinels
            with self.mcp(org, self.user) as user:
                got = self.call(user, "baas_get_skill", {"skill": BASE})
                check(got, f"el USER con «{ROLE}» no lee «{BASE}»")
            before = self.skill_detail(org, BASE)
            check(
                before["roles_source"] == "skill_md" and before["roles_sha256"] is None,
                f"antes: {before['roles_source']} / {before['roles_sha256']!r}",
            )
            # skill sin baas.yaml: expected_sha256 null (se crea el fichero)
            res = self.put_roles(org, "skills", BASE, [OTHER_ROLE], None)
            data = res.json() or {}
            check(
                res.status == 200 and data.get("roles") == [OTHER_ROLE] and data.get("roles_sha256"),
                f"PUT roles de «{BASE}» → {e2e_uso.describe(res)}: {data}",
            )
            created = str(data["roles_sha256"])
            after = self.skill_detail(org, BASE)
            check(
                after["roles"] == [OTHER_ROLE]
                and after["roles_source"] == "baas.yaml"
                and after["roles_sha256"] == created,
                f"después: {after['roles']} / {after['roles_source']}",
            )
            with self.mcp(org, self.user) as user:
                code = self.mcp_error(user, "baas_get_skill", {"skill": BASE})
                check(
                    code == "AUTHORIZATION_DENIED",
                    f"el USER (rol «{ROLE}») tras quitarle «{ROLE}» a «{BASE}»: {code} (se esperaba AUTHORIZATION_DENIED)",
                )
                listed = {
                    str(x.get("name") or x.get("skill"))
                    for x in self.call(user, "baas_list_skills", {}).get("skills") or []
                }
                check(BASE not in listed, f"baas_list_skills del USER todavía lista «{BASE}»: {sorted(listed)}")
            with self.mcp(org, self.org_admin) as admin:
                check(
                    self.call(admin, "baas_get_skill", {"skill": BASE}),
                    "el ORG_ADMIN no lee «base» (sin filtro de roles)",
                )
            # y a la inversa: devolver el rol
            res = self.put_roles(org, "skills", BASE, [ROLE], created)
            check(
                res.status == 200 and (res.json() or {}).get("roles") == [ROLE],
                f"PUT roles (devolver «{ROLE}») → {e2e_uso.describe(res)}",
            )
            with self.mcp(org, self.user) as user:
                check(self.call(user, "baas_get_skill", {"skill": BASE}), f"el USER no vuelve a leer «{BASE}»")
            # roles inexistentes o «*» con otros: 422 con el campo
            current = str((res.json() or {}).get("roles_sha256"))
            for roles, kind in (([f"no-existe-{self.nonce}"], "inexistente"), (["*", ROLE], "«*» con otros")):
                bad = self.put_roles(org, "skills", BASE, roles, current)
                bd = bad.json() or {}
                check(
                    bad.status == 422 and bd.get("error") == "INVALID_ROLES" and bd.get("field") == "roles",
                    f"rol {kind} → {e2e_uso.describe(bad)}: {bd}",
                )
            check(self.skill_detail(org, BASE)["roles"] == [ROLE], "un PUT rechazado cambió los roles")
            # el mismo cambio con un documento: la cabecera cambia y el cuerpo queda intacto
            doc = self.api_json("GET", org, f"knowledge/{self.short(DOC)}", self.org_admin)
            with self.mcp(org, self.org_admin) as admin:
                original = str(self.call(admin, "baas_read_document", {"path": DOC}).get("content") or "")
            res = self.put_roles(org, "knowledge", self.short(DOC), [OTHER_ROLE], doc["sha256"])
            dd = res.json() or {}
            check(
                res.status == 200 and dd.get("roles") == [OTHER_ROLE] and dd.get("sha256"),
                f"PUT roles del documento → {e2e_uso.describe(res)}: {dd}",
            )
            with self.mcp(org, self.user) as user:
                code = self.mcp_error(user, "baas_read_document", {"path": DOC})
                check(code == "AUTHORIZATION_DENIED", f"el USER lee {DOC} tras quitarle «{ROLE}»: {code}")
            with self.mcp(org, self.org_admin) as admin:
                changed = str(self.call(admin, "baas_read_document", {"path": DOC}).get("content") or "")
            body = lambda text: text.split("---\n", 2)[-1]  # noqa: E731 (lo que sigue a la cabecera)
            check(
                s["documento"] in changed and body(changed) == body(original),
                "el cuerpo del documento cambió al editar sus roles",
            )
            check(
                f"roles: [{OTHER_ROLE}]" in changed.replace('"', "") or OTHER_ROLE in changed.split("---")[1],
                f"la cabecera no lleva el rol nuevo:\n{changed[:200]}",
            )
            res = self.put_roles(org, "knowledge", self.short(DOC), [ROLE], str(dd["sha256"]))
            check(res.status == 200, f"PUT roles del documento (devolver «{ROLE}») → {e2e_uso.describe(res)}")
            with self.mcp(org, self.user) as user:
                check(self.call(user, "baas_read_document", {"path": DOC}), f"el USER no vuelve a leer {DOC}")
        return (
            f"«{BASE}» (sin baas.yaml, expected null): [{ROLE}] → [{OTHER_ROLE}] crea baas.yaml y el USER pasa de leer a "
            f"AUTHORIZATION_DENIED y desaparece de baas_list_skills; de vuelta a [{ROLE}] lee; rol inexistente y «*» con "
            f"otros → 422 INVALID_ROLES (field roles); {DOC} igual con el cuerpo byte a byte intacto"
        )

    # --- 17.5 edición concurrente ----------------------------------------------------------------------------
    def conflict(self) -> str:
        with self.scenario("co") as org:
            detail = self.skill_detail(org, REPORT)
            expected = detail["roles_sha256"]
            check(isinstance(expected, str), f"«{REPORT}» sin roles_sha256: {expected!r}")
            first = self.put_roles(org, "skills", REPORT, [OTHER_ROLE], expected)
            check(first.status == 200, f"primer PUT → {e2e_uso.describe(first)}")
            winner = str((first.json() or {}).get("roles_sha256"))
            check(winner != expected, "el primer PUT no cambió la huella")
            second = self.put_roles(org, "skills", REPORT, [ROLE, OTHER_ROLE], expected)
            check(
                second.status == 409 and (second.json() or {}).get("error") == "CONFLICT",
                f"segundo PUT con la misma huella → {e2e_uso.describe(second)} (se esperaba 409 CONFLICT)",
            )
            after = self.skill_detail(org, REPORT)
            check(
                after["roles"] == [OTHER_ROLE] and after["roles_sha256"] == winner,
                f"el segundo PUT pisó el primero: {after['roles']}",
            )
            # sin baas.yaml no se puede pisar con una huella cualquiera, y con un baas.yaml que existe tampoco con null
            stale = self.put_roles(org, "skills", REPORT, [ROLE], None)
            check(
                stale.status == 409,
                f"PUT con expected null sobre un baas.yaml existente → {e2e_uso.describe(stale)} (se esperaba 409)",
            )
            doc = self.api_json("GET", org, f"knowledge/{self.short(DOC)}", self.org_admin)
            one = self.put_roles(org, "knowledge", self.short(DOC), [OTHER_ROLE], doc["sha256"])
            two = self.put_roles(org, "knowledge", self.short(DOC), [ROLE, OTHER_ROLE], doc["sha256"])
            check(
                one.status == 200 and two.status == 409,
                f"documento: PUT {one.status} y PUT con la misma huella {two.status} (se esperaba 200 y 409)",
            )
            gone = self.put_status(org, REPORT, "deprecated", expected)
            check(gone.status == 409, f"PUT de estado con la huella vieja → {e2e_uso.describe(gone)} (se esperaba 409)")
            check(self.skill_detail(org, REPORT)["status"] == "active", "el PUT de estado rechazado desactivó la skill")
        return (
            f"dos PUT con la misma huella: 200 y 409 CONFLICT, los roles siguen [{OTHER_ROLE}]; null sobre un baas.yaml existente → 409; "
            "documento y estado con huella vieja → 409; nada pisado"
        )

    # --- 17.6 Caddy bloquea /internal ------------------------------------------------------------------------------
    def caddy_blocks(self) -> str:
        with self.scenario("ca") as org:
            parts = urlsplit(str(org.bindings[self.org_admin]["mcp_url"]))
            origin = f"{parts.scheme}://{parts.netloc}"
            paths = internal_paths(org.slug)
            # la ruta de verdad: 404 exacto (sin token y con el token interno: no es el token lo que la protege)
            problems: list[str] = []
            for with_token in (False, True):
                headers = {"X-Baas-Internal-Token": self.internal_token} if with_token else {}
                res = h3.http("GET", f"{origin}{paths[0]}", headers=headers, timeout=self.a.timeout)
                self.seen.append(Seen(f"GET {origin}{paths[0]} (token={with_token})", res.body))
                if res.status != 404:
                    problems.append(
                        f"{paths[0]} ({'con' if with_token else 'sin'} token) → HTTP {res.status} (se esperaba 404): {h3.snippet(res.body, 120)}"
                    )
            for path in paths:
                for with_token in (False, True):
                    headers = {"X-Baas-Internal-Token": self.internal_token} if with_token else {}
                    res = h3.http("GET", f"{origin}{path}", headers=headers, timeout=self.a.timeout)
                    self.seen.append(Seen(f"GET {origin}{path} (token={with_token})", res.body))
                    problems += leak_problems(path, res, with_token)
            # y la API interna sí responde dentro de la red: lo prueba la webapp (17.1); aquí solo se mira desde fuera
            check(not problems, f"/internal/* de {origin} no está bloqueado:\n" + "\n".join(problems))
        return (
            f"GET {origin}/internal/v1/orgs/<org>/skills → 404 (sin token y con el token interno); además {len(paths)} variantes "
            "(mayúsculas, %69, %2F, %2e%2e, //, /./, /mcp/../, ficheros del equipo), con y sin token: ninguna 200 ni con datos"
        )

    # --- 17.7 ninguna respuesta lleva cuerpos ------------------------------------------------------------------------
    def no_bodies(self) -> str:
        with self.scenario("cu") as org:
            start = len(self.seen)  # 17.9 y 17.10 también guardan 200 exentas: aquí solo se cuentan las de este paso
            requests = [("skills", None)] + [(f"skills/{n}", None) for n in (BASE, REPORT)]
            requests += [("knowledge", None), (f"knowledge/{self.short(DOC)}", None)]
            for path, _ in requests:
                self.api("GET", org, path, self.org_admin)
            # también las respuestas de las escrituras: cada una debe ser un 200 (si no, no se escanea el éxito)
            written = self.put_roles(org, "skills", BASE, [ROLE], None)
            check(written.status == 200, f"PUT roles de «{BASE}» → {e2e_uso.describe(written)} (se esperaba 200)")
            deprecated = self.put_status(org, BASE, "deprecated", (written.json() or {}).get("roles_sha256"))
            check(deprecated.status == 200, f"PUT status deprecated → {e2e_uso.describe(deprecated)} (se esperaba 200)")
            active = self.put_status(org, BASE, "active", (deprecated.json() or {}).get("roles_sha256"))
            check(active.status == 200, f"PUT status active → {e2e_uso.describe(active)} (se esperaba 200)")
            self.api("GET", org, f"skills/{BASE}", self.user)  # y las rechazadas
            self.api(
                "PUT",
                org,
                f"skills/{BASE}/roles",
                self.org_admin,
                {"roles": [f"no-{self.nonce}"], "expected_sha256": None},
            )
            # las dos rutas de contenido: su 200 lleva el cuerpo a propósito (únicas excepciones) pero todos sus errores
            # se escanean igual que el resto. La de ficheros de una skill:
            self.write_view_files(org)
            ok = self.files_get(org, "SKILL.md", self.org_admin)
            okd = ok.json() or {}
            check(
                ok.status == 200 and self.sentinels["SKILL.md"] in str(okd.get("content")),
                f"control de la excepción: GET …/files/SKILL.md → {e2e_uso.describe(ok)} (se esperaba 200 con el contenido)",
            )
            rejected = [
                ("SKILL.md", self.user, 403),
                ("scripts/no-existe.py", self.org_admin, 404),
                ("../informe/SKILL.md", self.org_admin, 422),
                ("scripts/_acl.yaml", self.org_admin, 422),
                (BIG, self.org_admin, 413),
                (BINARY, self.org_admin, 415),
            ]
            for path, who, status in rejected:
                res = self.files_get(org, path, who)
                check(
                    res.status == status,
                    f"GET …/files/{path} ({who}) → {e2e_uso.describe(res)} (se esperaba {status}: se escanea su error)",
                )
            # y la del contenido de un documento de conocimiento
            self.write_view_docs(org)
            kok = self.knowledge_get(org, self.short(DOC), self.org_admin)
            check(
                kok.status == 200 and self.sentinels["documento"] in str((kok.json() or {}).get("content")),
                f"control de la excepción: GET …/knowledge-content/{self.short(DOC)} → {e2e_uso.describe(kok)} "
                "(se esperaba 200 con el contenido)",
            )
            k_rejected = [
                (self.short(DOC), self.user, 403),
                ("ventas/no-existe.md", self.org_admin, 404),
                ("../ventas/guia.md", self.org_admin, 422),
                ("ventas/_acl.yaml", self.org_admin, 422),
                (self.short(K_BIG), self.org_admin, 413),
                (self.short(K_BINARY), self.org_admin, 415),
            ]
            for path, who, status in k_rejected:
                res = self.knowledge_get(org, path, who)
                check(
                    res.status == status,
                    f"GET …/knowledge-content/{path} ({who}) → {e2e_uso.describe(res)} (se esperaba {status}: se escanea su error)",
                )
            problems = scan_seen(self.seen, self.sentinels)
            check(not problems, "respuestas con cuerpos de ficheros:\n" + "\n".join(problems[:10]))
            # controles positivos: los centinelas están de verdad en los ficheros (el escáner no se queda ciego) y la
            # excepción exime solo a las 200 de las dos rutas de contenido
            with self.mcp(org, self.org_admin) as admin:
                skill = self.call(admin, "baas_get_skill", {"skill": BASE})
                doc = self.call(admin, "baas_read_document", {"path": DOC})
            seen = body_problems("baas_get_skill", json.dumps(skill).encode(), {"SKILL.md": self.sentinels["SKILL.md"]})
            check(
                seen, "control positivo: el escáner no ve el centinela en la respuesta del MCP (que sí lleva el cuerpo)"
            )
            check(
                self.sentinels["documento"] in str(doc.get("content")),
                "control positivo: el documento no lleva su centinela",
            )
            exempt = [i for i in self.seen[start:] if i.content_expected]
            check(
                len(exempt) == 2 and sorted(i.body for i in exempt) == sorted([ok.body, kok.body]),
                f"la excepción de las rutas de contenido exime {len(exempt)} respuestas "
                f"({[i.label for i in exempt]}; se esperaban 2: la 200 de skills/{BASE}/files/SKILL.md y la de "
                f"knowledge-content/{self.short(DOC)})",
            )
            for name, res in (("ficheros de una skill", ok), ("contenido de un documento", kok)):
                check(
                    body_problems(f"200 de la ruta de {name}", res.body, self.sentinels),
                    f"control positivo: el escáner no ve el contenido en la 200 de la ruta de {name}",
                )
            total = len(self.seen)
        return (
            f"{total} respuestas de la prueba (API del catálogo y Caddy; listas, fichas, PUT, errores) sin los centinelas de SKILL.md, "
            f"script ni documento y sin claves de contenido ({', '.join(sorted(BODY_KEYS))}); las únicas exentas son 2 200: "
            f"…/skills/{BASE}/files/SKILL.md y …/knowledge-content/{self.short(DOC)} (llevan su contenido), y los errores "
            "de ambas rutas (403, 404, 422, 413, 415) se escanean igual; control positivo: la respuesta de baas_get_skill "
            "del MCP y las dos 200 sí llevan el centinela"
        )

    # --- 17.9 contenido de un fichero de una skill ---------------------------------------------------------------------
    def files_get(self, org: e2e_uso.UsoOrg | str, path: str, who: str | None, skill: str = BASE) -> Http:
        """`GET …/catalog/skills/<skill>/files/<path>` (la ruta tal cual: no se codifica ni normaliza)."""
        return self.api("GET", org, f"skills/{skill}/files/{path}", who)

    def knowledge_get(self, org: e2e_uso.UsoOrg | str, path: str, who: str | None) -> Http:
        """`GET …/catalog/knowledge-content/<path>` (la ruta tal cual, corta o completa: no se codifica ni normaliza)."""
        return self.api("GET", org, f"knowledge-content/{path}", who)

    def file_read_rows(self, org: e2e_uso.UsoOrg, who: str) -> tuple[list[dict[str, Any]], bytes]:
        """Las filas `skill.file_read` de `BASE` de la auditoría de `org` que ve `who` (y la respuesta cruda, para escanearla)."""
        res = self.e.webapp("GET", f"/api/v1/audit?action=skill.file_read&org={org.slug}&limit=200", who)
        check(res.status == 200, f"GET /api/v1/audit (skill.file_read, {who}) → {e2e_uso.describe(res)}")
        items = (res.json() or {}).get("items") or []
        return [i for i in items if isinstance(i, dict) and i.get("target_id") == BASE], res.body

    def file_read(self) -> str:
        with self.scenario("fi") as org:
            s = self.sentinels
            self.write_view_files(org)
            start = len(self.seen)
            detail = self.skill_detail(org, BASE)
            listed = {f["path"]: f for f in detail["file_list"]}
            for path in (BIG, LIMIT, BINARY, NESTED_ACL, "SKILL.md", "scripts/hola.py"):
                check(f"skills/{BASE}/{path}" in listed, f"«{path}» no está en file_list de «{BASE}»: {sorted(listed)}")
            before, _ = self.file_read_rows(org, self.admin)
            check(not before, f"la organización recién creada ya tiene {len(before)} filas skill.file_read")
            # 1. el ORG_ADMIN abre SKILL.md y un script: lo que se escribió, con tamaño y huella del disco y sin caché
            expected = {
                "SKILL.md": (self.skill_md(BASE).encode(), "markdown"),
                "scripts/hola.py": (self.script_text().encode(), "python"),
                LIMIT: (self.view_files()[LIMIT], "text"),
            }
            reads: list[str] = []
            for path, (raw, language) in expected.items():
                res = self.files_get(org, path, self.org_admin)
                data = res.json() or {}
                check(res.status == 200 and isinstance(data, dict), f"GET …/files/{path} → {e2e_uso.describe(res)}")
                problems = no_store_problems(f"GET …/files/{path}", res)
                check(not problems, "\n".join(problems))
                check(
                    set(data) == {"skill", "path", "full_path", "size", "sha256", "encoding", "language", "content"},
                    f"claves de la respuesta de {path}: {sorted(data)}",
                )
                check(
                    data["content"].encode() == raw,
                    f"el contenido de {path} no es el que se escribió: {h3.snippet(res.body, 160)}",
                )
                check(
                    data["size"] == len(raw) and data["sha256"] == hashlib.sha256(raw).hexdigest(),
                    f"{path}: size {data['size']} sha256 {data['sha256']} (se esperaba {len(raw)} / {hashlib.sha256(raw).hexdigest()})",
                )
                check(
                    (data["skill"], data["path"], data["full_path"], data["encoding"], data["language"])
                    == (BASE, path, f"skills/{BASE}/{path}", "utf8", language),
                    f"metadatos de {path}: { {k: v for k, v in data.items() if k != 'content'} }",
                )
                check(
                    listed[f"skills/{BASE}/{path}"]["sha256"] == data["sha256"],
                    f"la huella de {path} no coincide con la de file_list ({listed[f'skills/{BASE}/{path}']['sha256']})",
                )
                reads.append(path)
            # el mismo fichero dos veces: dos filas (una por lectura, no agregadas)
            again = self.files_get(org, "SKILL.md", self.org_admin)
            check(again.status == 200, f"segunda lectura de SKILL.md → {e2e_uso.describe(again)}")
            reads.append("SKILL.md")
            # 2. permisos: USER 403, sin sesión 401, otra organización 404
            denied = {
                "USER": (self.files_get(org, "SKILL.md", self.user), 403),
                "sin sesión": (self.files_get(org, "SKILL.md", None), 401),
                "ORG_ADMIN de otra organización": (self.files_get(org, "SKILL.md", e2e_uso.OTHER_ADMIN), 404),
                "organización inexistente": (self.files_get(f"cat-{self.nonce}-fantasma", "SKILL.md", self.admin), 404),
            }
            for who, (res, status) in denied.items():
                check(
                    res.status == status, f"…/files/SKILL.md ({who}) → {e2e_uso.describe(res)} (se esperaba {status})"
                )
            check((denied["USER"][0].json() or {}).get("error") == "FORBIDDEN", "el USER no recibe FORBIDDEN")
            # 3. traversal y _acl.yaml: 422 INVALID_ARGUMENT, que valida la webapp antes de llamar al MCP
            invalid = [
                "../informe/SKILL.md",
                "%2e%2e/informe/SKILL.md",
                "scripts/%2e%2e/%2e%2e/informe/SKILL.md",
                "scripts/..%2f..%2finforme/SKILL.md",
                "..%2f..%2f..%2f..%2fetc%2fpasswd",
                "/SKILL.md",
                "scripts//hola.py",
                "scripts/./hola.py",
                "_acl.yaml",
                "scripts/_acl.yaml",
                "scripts/%5Facl.yaml",
            ]
            for path in dict.fromkeys(invalid):
                res = self.files_get(org, path, self.org_admin)
                bd = res.json() or {}
                check(
                    res.status == 422 and bd.get("error") == "INVALID_ARGUMENT" and bd.get("field") == "path",
                    f"GET …/files/{path} → {e2e_uso.describe(res)}: {bd} (se esperaba 422 INVALID_ARGUMENT, field path)",
                )
            # 4. no existe → 404; más de 256 KiB → 413 con size y max_bytes; binario → 415
            missing = self.files_get(org, "scripts/no-existe.py", self.org_admin)
            check(missing.status == 404, f"fichero inexistente → {e2e_uso.describe(missing)} (se esperaba 404)")
            missing_skill = self.files_get(org, "SKILL.md", self.org_admin, skill="no-existe")
            check(
                missing_skill.status == 404, f"skill inexistente → {e2e_uso.describe(missing_skill)} (se esperaba 404)"
            )
            big = self.files_get(org, BIG, self.org_admin)
            bd = big.json() or {}
            check(
                big.status == 413
                and bd.get("error") == "CONTENT_TOO_LARGE"
                and bd.get("size") == MAX_FILE_VIEW_BYTES + 1
                and bd.get("max_bytes") == MAX_FILE_VIEW_BYTES,
                f"fichero de {MAX_FILE_VIEW_BYTES + 1} bytes → {e2e_uso.describe(big)}: {bd} "
                f"(se esperaba 413 CONTENT_TOO_LARGE con size {MAX_FILE_VIEW_BYTES + 1} y max_bytes {MAX_FILE_VIEW_BYTES})",
            )
            binary = self.files_get(org, BINARY, self.org_admin)
            check(
                binary.status == 415 and (binary.json() or {}).get("error") == "CONTENT_NOT_TEXT",
                f"fichero binario → {e2e_uso.describe(binary)} (se esperaba 415 CONTENT_NOT_TEXT)",
            )
            # 5. auditoría: una fila por lectura correcta, ninguna por los fallos, y nunca el contenido
            failures = len(denied) + len(dict.fromkeys(invalid)) + 4
            self.wait(
                lambda: (
                    [f"skill.file_read: {n} filas nuevas (se esperaban {len(reads)})"]
                    if (n := len(self.file_read_rows(org, self.admin)[0]) - len(before)) != len(reads)
                    else []
                ),
                10,
                "la auditoría de skill.file_read no cuadra",
            )
            time.sleep(1.5)  # y los fallos no la hacen crecer después
            rows, raw_platform = self.file_read_rows(org, self.admin)
            check(
                len(rows) == len(reads),
                f"{failures} lecturas fallidas dejaron {len(rows)} filas (se esperaban {len(reads)})",
            )
            observed = sorted((r["metadata"]["path"], r["metadata"]["sha256"], r["metadata"]["size"]) for r in rows)
            wanted = sorted((p, hashlib.sha256(expected[p][0]).hexdigest(), len(expected[p][0])) for p in reads)
            check(observed == wanted, f"filas de auditoría {observed} ≠ lecturas correctas {wanted}")
            for row in rows:
                who = (row.get("actor") or {}).get("email")
                check(
                    who == self.org_admin
                    and row.get("target_type") == "skill"
                    and row.get("org_slug") == org.slug
                    and row.get("count") == 1,
                    f"fila de auditoría: actor {who}, {row.get('target_type')}, {row.get('org_slug')}, count {row.get('count')}",
                )
                check(
                    not (set(row["metadata"]) & BODY_KEYS),
                    f"la fila lleva claves de contenido: {sorted(row['metadata'])}",
                )
            # el ORG_ADMIN de la organización ve las mismas filas (no es una acción solo de plataforma)
            seen_by_admin, raw_org = self.file_read_rows(org, self.org_admin)
            check(
                {r["id"] for r in seen_by_admin} == {r["id"] for r in rows},
                f"el ORG_ADMIN ve {len(seen_by_admin)} filas skill.file_read y la plataforma {len(rows)}",
            )
            leaks = body_problems("auditoría (PLATFORM_ADMIN)", raw_platform, s)
            leaks += body_problems("auditoría (ORG_ADMIN)", raw_org, s)
            check(not leaks, "el contenido aparece en la auditoría:\n" + "\n".join(leaks[:10]))
            # los errores de la ruta de ficheros no llevan cuerpos (17.7 los escanea igual)
            problems = scan_seen(self.seen[start:], s)
            check(not problems, "respuestas de errores con cuerpos de ficheros:\n" + "\n".join(problems[:10]))
        return (
            f"ORG_ADMIN abre SKILL.md (dos veces), scripts/hola.py y un fichero de exactamente {MAX_FILE_VIEW_BYTES} bytes: "
            "200 con el contenido escrito, size y sha256 del disco (los de file_list), Cache-Control no-store y Pragma "
            f"no-cache; USER 403, sin sesión 401, otra organización 404; {len(dict.fromkeys(invalid))} rutas con traversal o "
            f"_acl.yaml (también el de subcarpeta, que sí está en file_list) → 422; {MAX_FILE_VIEW_BYTES + 1} bytes → 413 "
            "(size, max_bytes); binario → 415; inexistente → 404; auditoría: "
            f"{len(rows)} filas skill.file_read (una por lectura correcta, ninguna por las {failures} fallidas, con path, "
            "sha256 y size, sin contenido), visibles para el ORG_ADMIN"
        )

    # --- 17.10 contenido de un documento de conocimiento -----------------------------------------------------------------
    def knowledge_read_rows(self, org: e2e_uso.UsoOrg, who: str) -> tuple[list[dict[str, Any]], bytes]:
        """TODAS las filas `knowledge.file_read` de la auditoría de `org` que ve `who` (sea cual sea su `target_id`; y la
        respuesta cruda, para escanearla)."""
        res = self.e.webapp("GET", f"/api/v1/audit?action=knowledge.file_read&org={org.slug}&limit=200", who)
        check(res.status == 200, f"GET /api/v1/audit (knowledge.file_read, {who}) → {e2e_uso.describe(res)}")
        items = (res.json() or {}).get("items") or []
        return [i for i in items if isinstance(i, dict)], res.body

    def knowledge_read(self) -> str:
        with self.scenario("ko") as org:
            s = self.sentinels
            self.write_view_docs(org)
            start = len(self.seen)
            docs = {
                str(d.get("path")): d
                for d in self.api_json("GET", org, "knowledge", self.org_admin).get("documents") or []
                if isinstance(d, dict)
            }
            # lo que el escenario puso en el almacén (fuente independiente de lo que contesta la API) y su `language`
            expected: dict[str, tuple[bytes, str]] = {
                DOC: (self.doc_text(self.doc_body()).encode(), "markdown"),
                K_SQL: (self.view_docs()[K_SQL], "sql"),
                K_LIMIT: (self.view_docs()[K_LIMIT], "text"),
                K_ACCENT: (self.view_docs()[K_ACCENT], "markdown"),
            }
            for path in (*expected, K_BIG, K_BINARY):
                check(path in docs, f"{path} no está en el listado de conocimiento: {sorted(docs)}")
            before, _ = self.knowledge_read_rows(org, self.admin)
            check(not before, f"la organización recién creada ya tiene {len(before)} filas knowledge.file_read")
            # 1. el ORG_ADMIN abre el .md (corta, completa y otra vez la corta), el .sql, el de exactamente 256 KiB y el de
            # nombre con tildes (NFC): contenido escrito, tamaño y huella del disco, ruta canónica y sin caché
            plan = [
                ("corta", self.short(DOC), DOC),
                ("completa", DOC, DOC),
                ("corta, otra vez", self.short(DOC), DOC),
                ("sql", self.short(K_SQL), K_SQL),
                ("límite", self.short(K_LIMIT), K_LIMIT),
                ("con tildes", quote(self.short(K_ACCENT)), K_ACCENT),
            ]
            first_200: Http | None = None
            for label, asked, full in plan:
                res = self.knowledge_get(org, asked, self.org_admin)
                data = res.json() or {}
                what = f"GET …/knowledge-content/{asked} ({label})"
                check(res.status == 200 and isinstance(data, dict), f"{what} → {e2e_uso.describe(res)}")
                first_200 = first_200 or res
                problems = no_store_problems(what, res)
                check(not problems, "\n".join(problems))
                check(
                    set(data) == {"path", "size", "sha256", "encoding", "language", "content"},
                    f"claves de la respuesta de {asked}: {sorted(data)}",
                )
                raw, language = expected[full]
                check(
                    data["content"].encode() == raw,
                    f"el contenido de {asked} no es el que se escribió: {h3.snippet(res.body, 160)}",
                )
                digest = hashlib.sha256(raw).hexdigest()
                check(
                    data["size"] == len(raw) and data["sha256"] == digest,
                    f"{asked}: size {data['size']} sha256 {data['sha256']} (se esperaba {len(raw)} / {digest})",
                )
                check(
                    (data["path"], data["encoding"], data["language"]) == (full, "utf8", language),
                    f"metadatos de {asked}: { {k: v for k, v in data.items() if k != 'content'} }",
                )
                check(
                    docs[full].get("sha256") == data["sha256"],
                    f"la huella de {full} no coincide con la del listado ({docs[full].get('sha256')})",
                )
            # 2. permisos: USER 403, sin sesión 401, otra organización 404, organización inexistente 404
            denied = {
                "USER": (self.knowledge_get(org, self.short(DOC), self.user), 403),
                "sin sesión": (self.knowledge_get(org, self.short(DOC), None), 401),
                "ORG_ADMIN de otra organización": (self.knowledge_get(org, self.short(DOC), e2e_uso.OTHER_ADMIN), 404),
                "organización inexistente": (
                    self.knowledge_get(f"cat-{self.nonce}-fantasma", self.short(DOC), self.admin),
                    404,
                ),
            }
            for who, (res, status) in denied.items():
                check(
                    res.status == status,
                    f"…/knowledge-content/{self.short(DOC)} ({who}) → {e2e_uso.describe(res)} (se esperaba {status})",
                )
            check((denied["USER"][0].json() or {}).get("error") == "FORBIDDEN", "el USER no recibe FORBIDDEN")
            # 3. lo que cuenta hasta aquí: una fila por lectura correcta (las seis), ni una más tras los fallos
            ok_rows = len(plan)
            self.wait(
                lambda: (
                    [f"knowledge.file_read: {n} filas (se esperaban {ok_rows})"]
                    if (n := len(self.knowledge_read_rows(org, self.admin)[0])) != ok_rows
                    else []
                ),
                10,
                "la auditoría de knowledge.file_read no cuadra",
            )
            # 4. traversal, la raíz y _acl.yaml (de cualquier nivel): 422 INVALID_ARGUMENT, que valida la webapp
            nfd = quote(unicodedata.normalize("NFD", self.short(K_ACCENT)))
            invalid = [
                "../ventas/guia.md",
                "%2e%2e/ventas/guia.md",
                "ventas/%2e%2e/%2e%2e/ventas/guia.md",
                "ventas/..%2f..%2fventas/guia.md",
                "..%2f..%2f..%2f..%2fetc%2fpasswd",
                "/ventas/guia.md",
                "ventas//guia.md",
                "ventas/./guia.md",
                "ventas%5Cguia.md",
                "ventas\\guia.md",
                "knowledge",
                "knowledge/",
                "knowledge/.",
                "ventas",
                "knowledge/ventas",
                "_acl.yaml",
                "ventas/_acl.yaml",
                "knowledge/_acl.yaml",
                "knowledge/ventas/_acl.yaml",
                "ventas/%5Facl.yaml",
                nfd,  # la misma ruta con tildes, pero en NFD (no canónica): no resuelve al documento en NFC
            ]
            for path in dict.fromkeys(invalid):
                res = self.knowledge_get(org, path, self.org_admin)
                bd = res.json() or {}
                check(
                    res.status == 422 and bd.get("error") == "INVALID_ARGUMENT" and bd.get("field") == "path",
                    f"GET …/knowledge-content/{path} → {e2e_uso.describe(res)}: {bd} (se esperaba 422 INVALID_ARGUMENT, field path)",
                )
            # 5. no existe → 404 (también `skills/…`, que se lee como knowledge/skills/…); más de 256 KiB → 413 con size
            # y max_bytes; binario → 415
            for path in ("ventas/no-existe.md", f"skills/{BASE}/SKILL.md"):
                missing = self.knowledge_get(org, path, self.org_admin)
                check(
                    missing.status == 404, f"…/knowledge-content/{path} → {e2e_uso.describe(missing)} (se esperaba 404)"
                )
            big = self.knowledge_get(org, self.short(K_BIG), self.org_admin)
            bd = big.json() or {}
            check(
                big.status == 413
                and bd.get("error") == "CONTENT_TOO_LARGE"
                and bd.get("size") == MAX_FILE_VIEW_BYTES + 1
                and bd.get("max_bytes") == MAX_FILE_VIEW_BYTES,
                f"documento de {MAX_FILE_VIEW_BYTES + 1} bytes → {e2e_uso.describe(big)}: {bd} "
                f"(se esperaba 413 CONTENT_TOO_LARGE con size {MAX_FILE_VIEW_BYTES + 1} y max_bytes {MAX_FILE_VIEW_BYTES})",
            )
            binary = self.knowledge_get(org, self.short(K_BINARY), self.org_admin)
            check(
                binary.status == 415 and (binary.json() or {}).get("error") == "CONTENT_NOT_TEXT",
                f"documento binario → {e2e_uso.describe(binary)} (se esperaba 415 CONTENT_NOT_TEXT)",
            )
            # 6. auditoría: ni una fila más por los fallos; las de las lecturas correctas, por documento (y SOLO de ellos)
            failures = len(denied) + len(dict.fromkeys(invalid)) + 4
            time.sleep(1.5)
            rows, raw_platform = self.knowledge_read_rows(org, self.admin)
            check(
                len(rows) == ok_rows,
                f"{failures} lecturas fallidas dejaron {len(rows)} filas knowledge.file_read (se esperaban {ok_rows})",
            )
            wanted = sorted(
                (full, hashlib.sha256(expected[full][0]).hexdigest(), len(expected[full][0])) for _, _, full in plan
            )
            observed = sorted(
                (r.get("target_id"), (r.get("metadata") or {}).get("sha256"), (r.get("metadata") or {}).get("size"))
                for r in rows
            )
            check(observed == wanted, f"filas de auditoría {observed} ≠ lecturas correctas {wanted}")
            by_doc = {full: sum(1 for t, _, _ in observed if t == full) for full in expected}
            check(
                by_doc == {DOC: 3, K_SQL: 1, K_LIMIT: 1, K_ACCENT: 1},
                f"filas por documento {by_doc} (el .md se leyó tres veces, en forma corta y completa: el mismo target_id)",
            )
            for row in rows:
                who = (row.get("actor") or {}).get("email")
                check(
                    who == self.org_admin
                    and row.get("target_type") == "knowledge"
                    and row.get("org_slug") == org.slug
                    and row.get("count") == 1,
                    f"fila de auditoría: actor {who}, {row.get('target_type')}, {row.get('org_slug')}, count {row.get('count')}",
                )
                check(
                    set(row["metadata"]) == {"sha256", "size"},
                    f"metadatos de la fila: {sorted(row['metadata'])} (se esperaban sha256 y size)",
                )
            # el ORG_ADMIN de la organización ve las mismas filas (no es una acción solo de plataforma)
            seen_by_admin, raw_org = self.knowledge_read_rows(org, self.org_admin)
            check(
                {r["id"] for r in seen_by_admin} == {r["id"] for r in rows},
                f"el ORG_ADMIN ve {len(seen_by_admin)} filas knowledge.file_read y la plataforma {len(rows)}",
            )
            # nunca el contenido: ningún centinela ni clave de contenido en lo que devuelve la auditoría (a ambos). Control
            # positivo: el mismo escáner sí ve el centinela en la respuesta con el contenido (y en una auditoría que lo llevase)
            leaks = body_problems("auditoría (PLATFORM_ADMIN)", raw_platform, s)
            leaks += body_problems("auditoría (ORG_ADMIN)", raw_org, s)
            check(not leaks, "el contenido aparece en la auditoría:\n" + "\n".join(leaks[:10]))
            check(
                first_200 is not None and body_problems("200 de la ruta de contenido", first_200.body, s),
                "control positivo: el escáner no ve el contenido en la 200 de la ruta de contenido",
            )
            tainted = json.dumps({"items": [{"metadata": {"sha256": "0" * 64, "size": 1, "note": s["documento"]}}]})
            check(
                body_problems("auditoría simulada", tainted.encode(), s),
                "control positivo: el escáner no ve el centinela en una auditoría que lo llevase",
            )
            # los errores de la ruta no llevan cuerpos (17.7 los escanea igual)
            problems = scan_seen(self.seen[start:], s)
            check(not problems, "respuestas de errores con cuerpos de documentos:\n" + "\n".join(problems[:10]))
        return (
            f"ORG_ADMIN abre {self.short(DOC)} (en forma corta, completa y otra vez corta), un .sql, uno de exactamente "
            f"{MAX_FILE_VIEW_BYTES} bytes y uno con tildes (NFC): 200 con el contenido escrito, size y sha256 del disco (los "
            "del listado), path = ruta completa canónica, Cache-Control no-store y Pragma no-cache; USER 403, sin sesión "
            f"401, otra organización 404; {len(dict.fromkeys(invalid))} rutas con traversal, la raíz, _acl.yaml (también el "
            f"de subcarpeta) o en NFD → 422; inexistente y skills/… → 404; {MAX_FILE_VIEW_BYTES + 1} bytes → 413 (size, "
            f"max_bytes); binario → 415; auditoría: {len(rows)} filas knowledge.file_read (una por lectura correcta: el .md "
            f"tres, con el mismo target_id; ninguna de otro documento ni por las {failures} fallidas; metadata sha256 y "
            "size, sin contenido), visibles para el ORG_ADMIN"
        )

    # --- 17.8 desactivar y reactivar -----------------------------------------------------------------------------------
    def disable(self) -> str:
        with self.scenario("de2") as org, tempfile.TemporaryDirectory(prefix="e2e-catalogo-") as tmp:
            root = Path(tmp)
            plugin = root / "claude" / "skills" / org.slug / "skills" / BASE
            claude = ClaudePaths(home=root / "claude", skills_root=root / "claude" / "skills")
            baas = BaasPaths(root / "baas")
            project = root / "proyecto"
            project.mkdir()

            def sync() -> Any:
                opts = SyncOptions(quiet=True, timeout=30.0, transfer_timeout=60.0, trigger="e2e")
                binding = org.bindings[
                    self.user
                ]  # la vinculación del proyecto (`org`, `mcp_url`), como la escribe /baas-init
                return sync_organization(binding, self.key(self.user), project, claude, baas, opts)

            def user_sees() -> bool:
                with self.mcp(org, self.user) as user:
                    listed = {
                        str(x.get("name") or x.get("skill"))
                        for x in self.call(user, "baas_list_skills", {}).get("skills") or []
                    }
                return BASE in listed

            first = sync()
            check(
                not first.failed and BASE in first.added, f"primer sync: añadidas {first.added}, fallos {first.failed}"
            )
            check(
                (plugin / "SKILL.md").is_file() and (plugin / "scripts" / "hola.py").is_file(),
                f"el sync no escribió «{BASE}» en {plugin}",
            )
            check(user_sees(), f"el USER no ve «{BASE}» antes de desactivarla")
            # sin baas.yaml, el estado que ya tiene (`active`) es idempotente: 200, huella null, nada se escribe ni se audita
            audit0 = self.status_audit_rows(org)
            noop = self.put_status(org, BASE, "active", None)
            nd = noop.json() or {}
            check(
                noop.status == 200
                and nd.get("status") == "active"
                and "roles_sha256" in nd
                and nd["roles_sha256"] is None,
                f"PUT status active sobre «{BASE}» sin baas.yaml → {e2e_uso.describe(noop)}: {nd} "
                "(se esperaba 200 con roles_sha256 null)",
            )
            untouched = self.skill_detail(org, BASE)
            check(
                untouched["roles_sha256"] is None and untouched["roles_source"] == "skill_md",
                f"el PUT idempotente escribió un baas.yaml: {untouched['roles_source']} / {untouched['roles_sha256']!r}",
            )
            # desactivar: la skill no tiene baas.yaml, se crea con los roles heredados de SKILL.md
            res = self.put_status(org, BASE, "deprecated", None)
            data = res.json() or {}
            check(
                res.status == 200 and data.get("status") == "deprecated" and data.get("roles_sha256"),
                f"PUT status deprecated → {e2e_uso.describe(res)}: {data}",
            )
            check(
                isinstance(data["roles_sha256"], str), f"el cambio real devolvió roles_sha256 {data['roles_sha256']!r}"
            )
            created = str(data["roles_sha256"])
            again = self.put_status(org, BASE, "deprecated", created)
            check(
                again.status == 200 and (again.json() or {}).get("roles_sha256") == created,
                f"PUT idempotente → {e2e_uso.describe(again)} (la huella no debe cambiar)",
            )
            with self.mcp(org, self.user) as user:
                code = self.mcp_error(user, "baas_get_skill", {"skill": BASE})
            check(
                code == "NOT_FOUND", f"baas_get_skill de la skill desactivada como USER: {code} (se esperaba NOT_FOUND)"
            )
            check(not user_sees(), "baas_list_skills del USER todavía lista la skill desactivada")
            skills = self.skill_names(org)  # el administrador sí la ve, como «deprecated» y con sus roles
            check(
                skills.get(BASE, {}).get("status") == "deprecated" and skills[BASE]["roles"] == [ROLE],
                f"el administrador ve {skills.get(BASE)}",
            )
            dep = self.skill_detail(org, REPORT)["depends_on"]["skills"]
            check(
                dep == [{"name": BASE, "exists": True, "status": "deprecated", "roles": [ROLE]}],
                f"«{REPORT}» ve su dependencia como {dep}",
            )
            with self.mcp(org, self.org_admin) as admin:
                check(
                    self.call(admin, "baas_get_skill", {"skill": BASE}),
                    "el ORG_ADMIN ya no lee la skill desactivada por el MCP",
                )
            # el siguiente sync la retira del disco
            second = sync()
            check(
                BASE in second.removed and not second.failed,
                f"segundo sync: retiradas {second.removed}, fallos {second.failed}",
            )
            check(not plugin.exists(), f"la skill desactivada sigue en el disco: {plugin}")
            # reactivar: vuelve con sus roles
            res = self.put_status(org, BASE, "active", created)
            check(
                res.status == 200 and (res.json() or {}).get("status") == "active",
                f"PUT status active → {e2e_uso.describe(res)}",
            )
            check(user_sees(), f"el USER no recupera «{BASE}» al reactivarla")
            check(self.skill_names(org)[BASE]["status"] == "active", "el administrador no la ve «active»")
            third = sync()
            check(
                BASE in third.added and (plugin / "SKILL.md").is_file(),
                f"tercer sync: añadidas {third.added}; ¿está en {plugin}?",
            )
            # una huella vieja es un conflicto, también para el estado
            stale = self.put_status(org, BASE, "deprecated", "0" * 64)
            check(
                stale.status == 409,
                f"PUT de estado con una huella cualquiera → {e2e_uso.describe(stale)} (se esperaba 409)",
            )
            # auditoría: solo los dos cambios reales (deprecated y active); los PUT idempotentes no auditan
            self.wait(
                lambda: (
                    [f"skill.status_changed: {n} filas nuevas (se esperaban 2)"]
                    if (n := self.status_audit_rows(org) - audit0) != 2
                    else []
                ),
                10,
                "la auditoría de skill.status_changed no cuadra",
            )
        return (
            f"«{BASE}»: desactivada (idempotente) → USER NOT_FOUND y sin verla en baas_list_skills, administrador la ve «deprecated» "
            f"({REPORT} la ve igual); sync 2 la retira del disco (sync 1 la había traído); reactivada → vuelve (sync 3 la restaura)"
        )

    # --- el recorrido ------------------------------------------------------------------------------------------------------
    def register(self, runner: Runner, base: list[str]) -> None:
        step = runner.step
        ready = "17.0 la pila tiene la API del catálogo"
        step(ready, self.stack_has_api, needs=base)
        base = [*base, ready]
        step(
            "17.1 ORG_ADMIN lista skills y conocimiento (ve todo, aunque no tenga esos roles)", self.listing, needs=base
        )
        step("17.2 ficha de una skill con dependencias y «usada por»", self.details, needs=base)
        step("17.3 USER recibe 403 (sin sesión 401, otra organización 404)", self.permissions, needs=base)
        step("17.4 PUT de roles cambia quién lee con baas_get_skill (skill y documento)", self.change_roles, needs=base)
        step("17.5 409 en edición concurrente (dos PUT con el mismo expected_sha256)", self.conflict, needs=base)
        step("17.6 GET mcp.<dominio>/internal/v1/orgs/<org>/skills → 404 por Caddy", self.caddy_blocks, needs=base)
        step(
            "17.7 ninguna respuesta contiene cuerpos de ficheros (salvo la 200 de las rutas de contenido de una skill y de un documento)",
            self.no_bodies,
            needs=base,
        )
        step(
            "17.8 desactivar una skill: NOT_FOUND al USER, el administrador la ve, sync la retira; reactivar la devuelve",
            self.disable,
            needs=base,
        )
        step(
            "17.9 contenido de un fichero de una skill: ORG_ADMIN lo lee (auditado), USER 403, traversal 422, grande 413, binario 415",
            self.file_read,
            needs=base,
        )
        step(
            "17.10 contenido de un documento de conocimiento: ORG_ADMIN lo lee (auditado), USER 403, traversal 422, grande 413, binario 415",
            self.knowledge_read,
            needs=base,
        )
