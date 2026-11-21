"""Grupo «catalogo» (pasos 15.0–15.8) de `dev/e2e_h4.py`: el catálogo de administración de skills y conocimiento.

Un paso por criterio de aceptación del contrato (`contrato-catalogo-admin.md`, §9 y §10) contra la pila real:

  15.0  la pila tiene la API del catálogo (`GET /api/v1/orgs/<org>/catalog/skills`); si no, falla con un mensaje
        claro y se **saltan** los demás pasos.
  15.1  un ORG_ADMIN lista skills y conocimiento y los ve todos, aunque no tenga esos roles.
  15.2  ficha de una skill: roles y su origen, dependencias (con existencia, estado y roles), «usada por» e
        invocaciones; ficha de un documento con «usada por».
  15.3  un USER recibe `403` en todas las rutas; sin sesión `401`; el ORG_ADMIN de otra organización, `404`.
  15.4  `PUT` de roles de una skill (sin `baas.yaml` previo) y de un documento: cambia quién lee con `baas_get_skill`
        y `baas_read_document`; el cuerpo del documento queda intacto; roles inexistentes → `422 INVALID_ROLES`.
  15.5  edición concurrente: dos `PUT` con el mismo `expected_sha256` → el segundo da `409` y no pisa nada.
  15.6  `GET <mcp>/internal/v1/orgs/<org>/skills` por Caddy → `404`, también con el token interno y con variantes
        codificadas (`%2e%2e`, barras, mayúsculas): nunca `200` ni datos.
  15.7  ninguna respuesta (de la API del catálogo ni de Caddy) contiene el cuerpo de un fichero.
  15.8  desactivar una skill: el USER recibe `NOT_FOUND` y no la ve en el listado, el administrador sí («deprecated»),
        el `sync` del plugin la retira del disco, y al reactivarla vuelve (con sus roles).

Cada paso crea su organización (`cat-<ejecución>-<paso>`, con usuarios de la semilla de desarrollo como miembros, un
rol funcional `ventas` para el USER y otro, `soporte`, para quitárselo) y la elimina al terminar. Ningún paso mira
dentro de los contenedores: no necesita `--inspect`.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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
    """15.0: qué decir si `GET …/catalog/skills` (como PLATFORM_ADMIN, de una organización que existe) no da 200."""
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
    """Rutas con las que alguien intentaría llegar a `/internal/v1` del MCP por el sitio público (15.6): la buena y
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
    """15.6: lo que no puede contestar Caddy a una ruta interna: `200`, datos del catálogo o ficheros del equipo."""
    how = "con el token interno" if with_token else "sin token"
    text = res.body.decode(errors="replace")
    problems = []
    if res.status < 400:
        problems.append(f"{path} ({how}) → HTTP {res.status} (se esperaba un error)")
    if '"skills"' in text or "root:" in text or '"documents"' in text:
        problems.append(f"{path} ({how}) devuelve datos: {h3.snippet(res.body, 120)}")
    return problems


# --- la prueba ------------------------------------------------------------------------------------


class CatalogoGroup(e2e_uso.UsoGroup):
    """Reutiliza de `uso` la creación de organizaciones de cada paso, las sesiones y los clientes del MCP."""

    def __init__(self, e2e: E2EH4) -> None:
        super().__init__(e2e)
        self.sentinels = {
            "SKILL.md": f"CATALOGO-CUERPO-SKILL-{self.nonce}",
            "documento": f"CATALOGO-CUERPO-DOC-{self.nonce}",
            "script": f"CATALOGO-CUERPO-SCRIPT-{self.nonce}",
        }
        self.seen: list[tuple[str, bytes]] = []  # (qué se pidió, cuerpo) de cada respuesta de los pasos
        self.mcp_urls: dict[str, str] = {}
        self.internal_token = getattr(e2e.args, "internal_token", INTERNAL_TOKEN)

    # --- organizaciones y escenario -----------------------------------------------------------------
    def create_org(self, label: str, members: dict[str, str]) -> e2e_uso.UsoOrg:  # type: ignore[override]
        """Como la de `uso`, pero con el prefijo `cat-` y los roles funcionales `ventas` (del USER) y `soporte`."""
        slug = f"cat-{self.nonce}-{label}"
        res = self.e.admin_post("/api/v1/orgs", {"slug": slug, "name": f"E2E catalogo {label} {self.nonce}"})
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
        s = self.sentinels
        with self.org(label, {self.org_admin: "ORG_ADMIN", self.user: "USER"}) as org:

            def skill_md(name: str) -> str:
                return (
                    f"---\ntitle: {name.title()}\ntype: skill\nroles: [{ROLE}]\nstatus: active\n"
                    f"description: Prueba {self.nonce}.\n---\n# {name}\n{s['SKILL.md']}\n"
                )

            manifest = f"version: 1\nroles: [{ROLE}]\ndepends_on:\n  skills: [{BASE}]\n  knowledge:\n    - {DOC}\n    - {MISSING_DOC}\n"
            files = [
                {"path": f"skills/{BASE}/SKILL.md", "content": skill_md(BASE)},
                {"path": f"skills/{BASE}/scripts/hola.py", "content": f"print('{s['script']}')\n"},
                {"path": f"skills/{REPORT}/SKILL.md", "content": skill_md(REPORT)},
                {"path": f"skills/{REPORT}/baas.yaml", "content": manifest},
            ]
            with self.mcp(org, self.org_admin) as client:
                self.call(client, "baas_write_files", {"files": files})
            self.write_doc_roles(org, DOC, f"{s['documento']}\nCuerpo del documento.")
            yield org

    def write_doc_roles(self, org: e2e_uso.UsoOrg, path: str, text: str) -> None:
        content = f"---\ntitle: Guia {self.nonce}\ntype: knowledge\nroles: [{ROLE}]\n---\n{text}\n"
        with self.mcp(org, self.org_admin) as client:
            self.call(client, "baas_write_document", {"path": path, "content": content})

    # --- la API del catálogo -------------------------------------------------------------------------
    def api(self, method: str, org: e2e_uso.UsoOrg | str, path: str, who: str, payload: Any = None) -> Http:
        slug = org if isinstance(org, str) else org.slug
        url = f"/api/v1/orgs/{slug}/catalog/{path}"
        res = self.e.webapp(method, url, who, payload)
        self.seen.append((f"{method} {url} ({who})", res.body))
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

    # --- 15.0 la pila tiene la API --------------------------------------------------------------------
    def stack_has_api(self) -> str:
        res = self.e.webapp("GET", f"/api/v1/orgs/{META_ORG}/catalog/skills", self.admin)
        why = catalog_api_problem(res.status, res.json())
        check(why is None, why or "")
        count = len((res.json() or {}).get("skills") or [])
        return (
            f"GET /api/v1/orgs/{META_ORG}/catalog/skills → 200 ({count} skills de la organización meta; la API existe)"
        )

    # --- 15.1 listados ----------------------------------------------------------------------------------
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

    # --- 15.2 fichas ------------------------------------------------------------------------------------
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

    # --- 15.3 permisos ------------------------------------------------------------------------------------
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

    # --- 15.4 cambiar roles ---------------------------------------------------------------------------------
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

    # --- 15.5 edición concurrente ----------------------------------------------------------------------------
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

    # --- 15.6 Caddy bloquea /internal ------------------------------------------------------------------------------
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
                self.seen.append((f"GET {origin}{paths[0]} (token={with_token})", res.body))
                if res.status != 404:
                    problems.append(
                        f"{paths[0]} ({'con' if with_token else 'sin'} token) → HTTP {res.status} (se esperaba 404): {h3.snippet(res.body, 120)}"
                    )
            for path in paths:
                for with_token in (False, True):
                    headers = {"X-Baas-Internal-Token": self.internal_token} if with_token else {}
                    res = h3.http("GET", f"{origin}{path}", headers=headers, timeout=self.a.timeout)
                    self.seen.append((f"GET {origin}{path} (token={with_token})", res.body))
                    problems += leak_problems(path, res, with_token)
            # y la API interna sí responde dentro de la red: lo prueba la webapp (15.1); aquí solo se mira desde fuera
            check(not problems, f"/internal/* de {origin} no está bloqueado:\n" + "\n".join(problems))
        return (
            f"GET {origin}/internal/v1/orgs/<org>/skills → 404 (sin token y con el token interno); además {len(paths)} variantes "
            "(mayúsculas, %69, %2F, %2e%2e, //, /./, /mcp/../, ficheros del equipo), con y sin token: ninguna 200 ni con datos"
        )

    # --- 15.7 ninguna respuesta lleva cuerpos ------------------------------------------------------------------------
    def no_bodies(self) -> str:
        with self.scenario("cu") as org:
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
            problems: list[str] = []
            for label, raw in self.seen:
                problems += body_problems(label, raw, self.sentinels)
            check(not problems, "respuestas con cuerpos de ficheros:\n" + "\n".join(problems[:10]))
            # control positivo: los centinelas están de verdad en los ficheros (el escáner no se queda ciego)
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
            total = len(self.seen)
        return (
            f"{total} respuestas de la prueba (API del catálogo y Caddy; listas, fichas, PUT, errores) sin los centinelas de SKILL.md, "
            f"script ni documento y sin claves de contenido ({', '.join(sorted(BODY_KEYS))}); control positivo: la respuesta "
            "de baas_get_skill del MCP sí lo lleva"
        )

    # --- 15.8 desactivar y reactivar -----------------------------------------------------------------------------------
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
        ready = "15.0 la pila tiene la API del catálogo"
        step(ready, self.stack_has_api, needs=base)
        base = [*base, ready]
        step(
            "15.1 ORG_ADMIN lista skills y conocimiento (ve todo, aunque no tenga esos roles)", self.listing, needs=base
        )
        step("15.2 ficha de una skill con dependencias y «usada por»", self.details, needs=base)
        step("15.3 USER recibe 403 (sin sesión 401, otra organización 404)", self.permissions, needs=base)
        step("15.4 PUT de roles cambia quién lee con baas_get_skill (skill y documento)", self.change_roles, needs=base)
        step("15.5 409 en edición concurrente (dos PUT con el mismo expected_sha256)", self.conflict, needs=base)
        step("15.6 GET mcp.<dominio>/internal/v1/orgs/<org>/skills → 404 por Caddy", self.caddy_blocks, needs=base)
        step("15.7 ninguna respuesta contiene cuerpos de ficheros", self.no_bodies, needs=base)
        step(
            "15.8 desactivar una skill: NOT_FOUND al USER, el administrador la ve, sync la retira; reactivar la devuelve",
            self.disable,
            needs=base,
        )
