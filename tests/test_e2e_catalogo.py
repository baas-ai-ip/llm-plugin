"""Grupo «catalogo» (17.x) de la prueba de punta a punta de H4 (`dev/e2e_catalogo.py`).

Los pasos 17.0–17.10 se ejecutan contra la pila real; aquí se prueban las utilidades puras con las que deciden, para que
un paso no pase por error (que no vea un cuerpo, que dé por buena una respuesta `200` de `/internal`). Con `unittest`.
"""

from __future__ import annotations

import json
import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))

import e2e_catalogo
import e2e_h4
from e2e_h2 import Http

SENTINELS = {"SKILL.md": "CUERPO-SKILL-1", "documento": "CUERPO-DOC-1"}


def reply(status: int, body: object) -> Http:
    return Http(status, [], json.dumps(body).encode() if not isinstance(body, bytes) else body)


class BodyTest(unittest.TestCase):
    def test_metadata_only_is_clean(self) -> None:
        raw = json.dumps({"skills": [{"name": "base", "roles": ["ventas"], "warnings": []}]}).encode()
        self.assertEqual(e2e_catalogo.body_problems("x", raw, SENTINELS), [])

    def test_a_sentinel_is_found_plain_and_escaped(self) -> None:
        plain = b'{"title": "CUERPO-SKILL-1"}'
        escaped = json.dumps({"t": "a\nCUERPO-DOC-1"}).encode()  # el centinela tras un salto de línea escapado
        self.assertEqual(len(e2e_catalogo.body_problems("x", plain, SENTINELS)), 1)
        self.assertEqual(len(e2e_catalogo.body_problems("x", escaped, SENTINELS)), 1)

    def test_a_content_key_is_found_at_any_depth(self) -> None:
        raw = json.dumps({"file_list": [{"path": "a", "content": ""}]}).encode()
        self.assertIn("«content»", e2e_catalogo.body_problems("x", raw, SENTINELS)[0])

    def test_not_json_is_scanned_as_text(self) -> None:
        self.assertEqual(len(e2e_catalogo.body_problems("x", b"<html>CUERPO-SKILL-1</html>", SENTINELS)), 1)


FILE_OK = json.dumps({"skill": "base", "content": "CUERPO-SKILL-1"}).encode()
FILE_ERR = json.dumps({"error": "CONTENT_TOO_LARGE", "message": "CUERPO-DOC-1", "size": 5, "max_bytes": 4}).encode()
DOC_OK = json.dumps({"path": "knowledge/ventas/guia.md", "content": "CUERPO-DOC-1"}).encode()
SKILL_FILE = "skills/base/files/SKILL.md"
DOC_FILE = "knowledge-content/ventas/guia.md"
BIG_FILE = "skills/base/files/scripts/grande.txt"
BIG_DOC = "knowledge-content/ventas/grande.txt"


class FilesRouteTest(unittest.TestCase):
    """17.7: lo único que puede llevar un cuerpo es la 200 de las dos rutas de contenido (ficheros de una skill y
    documento de conocimiento)."""

    def test_only_the_200_of_the_two_routes_is_exempt(self) -> None:
        exempt = e2e_catalogo.is_file_content
        for path in (
            SKILL_FILE,
            "skills/base/files/scripts/hola.py",
            DOC_FILE,
            "knowledge-content/knowledge/ventas/guia.md",
        ):
            self.assertTrue(exempt("GET", path, 200), path)
            for status in (201, 204, 206, 301, 304, 401, 403, 404, 413, 415, 422, 503):
                self.assertFalse(exempt("GET", path, status), f"{path} {status}")

    def test_nothing_else_is_exempt(self) -> None:
        exempt = e2e_catalogo.is_file_content
        for path in (
            "skills",
            "skills/base",
            "skills/base/files",
            "skills/base/files/",
            "skills/base/roles",
            "knowledge",
            "knowledge/ventas/guia.md",
            "knowledge/ventas/guia.md/roles",
            "knowledge/a/files/b",  # lo que parece la ruta de skills, pero de conocimiento
            "knowledge-content",  # sin ruta (la contesta el catch-all con un 404)
            "knowledge-content/",
            "knowledge/knowledge-content/a",
            "knowledge-contents/a",
            "x/knowledge-content/a",
            "skills/files/SKILL.md",
            "skills//files/SKILL.md",
            "skills/base/files-extra/SKILL.md",
            "skills/base/x/files/SKILL.md",
            "skills/a/b/files/SKILL.md",
        ):
            self.assertFalse(exempt("GET", path, 200), path)

    def test_only_get_is_exempt(self) -> None:
        for method in ("PUT", "POST", "DELETE", "HEAD", "PATCH", "get"):
            for path in (SKILL_FILE, DOC_FILE):
                self.assertFalse(e2e_catalogo.is_file_content(method, path, 200), f"{method} {path}")

    def test_the_route_is_matched_whole_not_by_a_substring_or_a_query(self) -> None:
        exempt = e2e_catalogo.is_file_content
        for path in (
            "../skills/base/files/SKILL.md",
            "../knowledge-content/ventas/guia.md",
            "/knowledge-content/ventas/guia.md",
            " knowledge-content/ventas/guia.md",
            "knowledge-content/ventas/guia.md?x=1",
            "knowledge-content/ventas/guia.md#x",
            "knowledge-content/ventas/guia.md\n",
            "skills/base/files/SKILL.md?x=1",
            "skills/base/files/SKILL.md#x",
            "skills/base?/files/SKILL.md",
            "skills/base#/files/SKILL.md",
            "skills?/base/files/SKILL.md",
            "knowledge?/knowledge-content/a",
            "knowledge/a?knowledge-content/b",
            "knowledge/a#knowledge-content/b",
            "skills/base/roles?files/x",
        ):
            self.assertFalse(exempt("GET", path, 200), repr(path))

    def test_a_body_in_any_other_response_still_fails(self) -> None:
        for path in (
            "skills",
            "skills/base",
            "knowledge",
            "knowledge/ventas/guia.md",
            "skills/base/roles",
            "knowledge/ventas/guia.md/roles",
            "knowledge-content",
            "knowledge/a/files/b",
        ):
            for body, canary in ((FILE_OK, "CUERPO-SKILL-1"), (DOC_OK, "CUERPO-DOC-1")):
                seen = [e2e_catalogo.Seen(f"GET {path}", body, e2e_catalogo.is_file_content("GET", path, 200))]
                got = e2e_catalogo.scan_seen(seen, SENTINELS)
                self.assertTrue(any(canary in p for p in got), path)
                self.assertTrue(any("«content»" in p for p in got), path)

    def test_a_body_in_a_put_of_a_content_route_still_fails(self) -> None:
        for path, body in ((SKILL_FILE, FILE_OK), (DOC_FILE, DOC_OK)):
            flag = e2e_catalogo.is_file_content("PUT", path, 200)
            self.assertEqual(len(e2e_catalogo.scan_seen([e2e_catalogo.Seen("PUT", body, flag)], SENTINELS)), 2)

    def test_the_200_of_either_route_does_not_fail(self) -> None:
        for path, body in ((SKILL_FILE, FILE_OK), (DOC_FILE, DOC_OK)):
            seen = [e2e_catalogo.Seen(f"GET {path}", body, e2e_catalogo.is_file_content("GET", path, 200))]
            self.assertEqual(e2e_catalogo.scan_seen(seen, SENTINELS), [], path)

    def test_the_errors_of_both_routes_are_scanned(self) -> None:
        for path in (BIG_FILE, BIG_DOC, SKILL_FILE, DOC_FILE):
            for status in (401, 403, 404, 413, 415, 422, 503):
                flag = e2e_catalogo.is_file_content("GET", path, status)
                got = e2e_catalogo.scan_seen([e2e_catalogo.Seen(f"GET {path} {status}", FILE_ERR, flag)], SENTINELS)
                self.assertEqual(len(got), 1, f"{path} {status}")  # el centinela en el mensaje; sin claves de contenido

    def test_a_body_in_an_error_of_a_content_route_is_still_found_with_content_keys(self) -> None:
        flag = e2e_catalogo.is_file_content("GET", DOC_FILE, 404)
        got = e2e_catalogo.scan_seen([e2e_catalogo.Seen("GET 404", DOC_OK, flag)], SENTINELS)
        self.assertEqual(len(got), 2)

    def test_one_leak_among_clean_responses_is_reported_once(self) -> None:
        clean = json.dumps({"skills": []}).encode()
        seen = [
            e2e_catalogo.Seen("a", clean),
            e2e_catalogo.Seen("exenta", FILE_OK, True),
            e2e_catalogo.Seen("exenta del conocimiento", DOC_OK, True),
            e2e_catalogo.Seen("b", b'{"title": "CUERPO-DOC-1"}'),
        ]
        got = e2e_catalogo.scan_seen(seen, SENTINELS)
        self.assertEqual(len(got), 1)
        self.assertIn("b:", got[0])

    def test_the_audit_scan_sees_a_canary_and_passes_clean_rows(self) -> None:
        row = {
            "action": "knowledge.file_read",
            "target_id": "knowledge/ventas/guia.md",
            "metadata": {"sha256": "0" * 64, "size": 3},
        }
        clean = json.dumps({"items": [row]}).encode()
        self.assertEqual(e2e_catalogo.body_problems("auditoría", clean, SENTINELS), [])
        tainted = json.dumps({"items": [{**row, "metadata": {**row["metadata"], "note": "a\nCUERPO-DOC-1"}}]}).encode()
        self.assertEqual(len(e2e_catalogo.body_problems("auditoría", tainted, SENTINELS)), 1)
        keyed = json.dumps({"items": [{**row, "metadata": {**row["metadata"], "content": ""}}]}).encode()
        self.assertIn("«content»", e2e_catalogo.body_problems("auditoría", keyed, SENTINELS)[0])


class FileViewTest(unittest.TestCase):
    def test_no_store_headers(self) -> None:
        good = Http(200, [("cache-control", "no-store"), ("PRAGMA", "no-cache")], b"{}")
        self.assertEqual(e2e_catalogo.no_store_problems("x", good), [])
        self.assertEqual(
            e2e_catalogo.no_store_problems(
                "x", Http(200, [("Cache-Control", "no-store, max-age=0"), ("Pragma", "no-cache")], b"")
            ),
            [],
        )

    def test_missing_or_weak_cache_headers_fail(self) -> None:
        self.assertEqual(len(e2e_catalogo.no_store_problems("x", Http(200, [], b"{}"))), 2)
        weak = Http(200, [("Cache-Control", "private, max-age=0"), ("Pragma", "no-cache")], b"{}")
        self.assertEqual(len(e2e_catalogo.no_store_problems("x", weak)), 1)
        self.assertEqual(len(e2e_catalogo.no_store_problems("x", Http(200, [("Cache-Control", "no-store")], b""))), 1)

    def test_header_lookup_ignores_case(self) -> None:
        res = Http(200, [("Content-Type", "a"), ("cache-control", "no-store")], b"")
        self.assertEqual(e2e_catalogo.header_value(res, "Cache-Control"), "no-store")
        self.assertIsNone(e2e_catalogo.header_value(res, "Pragma"))

    def test_sized_text_is_exact_ascii_and_starts_with_the_head(self) -> None:
        for size in (262144, 262145, 100, 81):
            raw = e2e_catalogo.sized_text("CABEZA-1", size)
            self.assertEqual(len(raw), size)
            self.assertTrue(raw.startswith(b"CABEZA-1\n"))
            raw.decode("ascii")

    def test_the_files_added_to_the_skill_cover_each_error(self) -> None:
        class E2E:
            args = type("A", (), {"internal_token": "t"})()
            nonce = "n"

        files = e2e_catalogo.CatalogoGroup(E2E()).view_files()  # type: ignore[arg-type]
        self.assertEqual(len(files[e2e_catalogo.BIG]), e2e_catalogo.MAX_FILE_VIEW_BYTES + 1)
        self.assertEqual(len(files[e2e_catalogo.LIMIT]), e2e_catalogo.MAX_FILE_VIEW_BYTES)
        with self.assertRaises(UnicodeDecodeError):
            files[e2e_catalogo.BINARY].decode()
        self.assertIn(b"\x00", files[e2e_catalogo.BINARY])
        self.assertTrue(files[e2e_catalogo.NESTED_ACL].endswith(b"\n"))
        self.assertIn(e2e_catalogo.NESTED_ACL.rsplit("/", 1)[-1], "_acl.yaml")


class KnowledgeViewDocsTest(unittest.TestCase):
    def group(self) -> e2e_catalogo.CatalogoGroup:
        class E2E:
            args = type("A", (), {"internal_token": "t"})()
            nonce = "n"

        return e2e_catalogo.CatalogoGroup(E2E())  # type: ignore[arg-type]

    def test_the_documents_added_to_the_store_cover_each_case(self) -> None:
        group = self.group()
        docs = group.view_docs()
        self.assertEqual(len(docs[e2e_catalogo.K_BIG]), e2e_catalogo.MAX_FILE_VIEW_BYTES + 1)
        self.assertEqual(len(docs[e2e_catalogo.K_LIMIT]), e2e_catalogo.MAX_FILE_VIEW_BYTES)
        with self.assertRaises(UnicodeDecodeError):
            docs[e2e_catalogo.K_BINARY].decode()
        self.assertIn(b"\x00", docs[e2e_catalogo.K_BINARY])
        self.assertTrue(
            e2e_catalogo.K_SQL.endswith(".sql") and group.sentinels["doc-sql"].encode() in docs[e2e_catalogo.K_SQL]
        )
        self.assertEqual(e2e_catalogo.K_ACL.rsplit("/", 1)[-1], "_acl.yaml")
        for path in docs:
            self.assertTrue(path.startswith("knowledge/ventas/"), path)
        for name in ("doc-sql", "doc-acento", "doc-grande", "doc-limite", "doc-binario"):
            self.assertIn(name, group.sentinels)
            self.assertIn(
                group.sentinels[name].encode(),
                docs[
                    {
                        "doc-sql": e2e_catalogo.K_SQL,
                        "doc-acento": e2e_catalogo.K_ACCENT,
                        "doc-grande": e2e_catalogo.K_BIG,
                        "doc-limite": e2e_catalogo.K_LIMIT,
                        "doc-binario": e2e_catalogo.K_BINARY,
                    }[name]
                ],
            )

    def test_the_accented_name_is_nfc_and_not_ascii(self) -> None:
        name = e2e_catalogo.K_ACCENT
        self.assertEqual(unicodedata.normalize("NFC", name), name)
        self.assertNotEqual(unicodedata.normalize("NFD", name), name)
        self.assertFalse(name.isascii())

    def test_the_markdown_document_is_what_the_scenario_writes(self) -> None:
        group = self.group()
        text = group.doc_text(group.doc_body())
        self.assertTrue(text.startswith("---\ntitle: Guia n\ntype: knowledge\nroles: [ventas]\n---\n"))
        self.assertIn(group.sentinels["documento"], text)
        self.assertTrue(text.endswith("Cuerpo del documento.\n"))
        self.assertEqual(group.short(e2e_catalogo.DOC), "ventas/guia.md")


class ApiProblemTest(unittest.TestCase):
    def test_ok(self) -> None:
        self.assertIsNone(e2e_catalogo.catalog_api_problem(200, {"skills": []}))

    def test_missing_route_names_the_cause(self) -> None:
        self.assertIn("sin el catálogo", e2e_catalogo.catalog_api_problem(404, {"detail": "Not Found"}) or "")

    def test_unreachable_mcp_names_the_variable(self) -> None:
        got = e2e_catalogo.catalog_api_problem(503, {"error": "CATALOG_UNAVAILABLE"}) or ""
        self.assertIn("BAAS_MCP_ADMIN_URL", got)
        self.assertIn("CATALOG_UNAVAILABLE", got)

    def test_anything_else_fails(self) -> None:
        self.assertIsNotNone(e2e_catalogo.catalog_api_problem(500, None))


class LeakTest(unittest.TestCase):
    def test_404_is_fine(self) -> None:
        self.assertEqual(e2e_catalogo.leak_problems("/internal/v1", reply(404, b""), with_token=False), [])
        self.assertEqual(
            e2e_catalogo.leak_problems("/internal/v1", reply(401, {"error": "UNAUTHENTICATED"}), with_token=True), []
        )

    def test_200_and_data_are_leaks(self) -> None:
        got = e2e_catalogo.leak_problems("/internal/v1/orgs/o/skills", reply(200, {"skills": []}), with_token=True)
        self.assertEqual(len(got), 2)  # el 200 y los datos
        self.assertEqual(len(e2e_catalogo.leak_problems("/x", reply(404, b"root:x:0:0"), with_token=False)), 1)

    def test_traversal_variants_cover_the_encoded_forms(self) -> None:
        paths = e2e_catalogo.internal_paths("org")
        self.assertEqual(paths[0], "/internal/v1/orgs/org/skills")
        self.assertTrue(any("%2e%2e" in p for p in paths))
        self.assertTrue(any(p.startswith("//") for p in paths))
        self.assertTrue(any(p.startswith("/INTERNAL") for p in paths))
        self.assertEqual(len(paths), len(set(paths)))


class OrgContextTest(unittest.TestCase):
    """`UsoGroup.org()` llama a `create_org(label, members, name)`: la del catálogo tiene que aceptar los tres."""

    def test_org_context_uses_the_catalogo_prefix_and_drops_the_org(self) -> None:
        posts: list[tuple[str, object]] = []

        class E2E:
            args = type("A", (), {"internal_token": "t"})()
            nonce = "n"

            def admin_post(self, path: str, body: object, method: str = "POST") -> Http:
                posts.append((path, body))
                return reply(201, {"organization": {"id": "org-1", "timezone": "Europe/Madrid"}})

        group = e2e_catalogo.CatalogoGroup(E2E())  # type: ignore[arg-type]
        dropped: list[str] = []
        group.add_members = lambda org, members: None  # type: ignore[method-assign]
        group.drop_org = dropped.append  # type: ignore[method-assign]
        with group.org("paso", {}) as org:
            self.assertEqual(org.slug, "cat-n-paso")
        self.assertEqual(dropped, ["cat-n-paso"])
        self.assertEqual(posts[0][0], "/api/v1/orgs")
        self.assertEqual(posts[0][1], {"slug": "cat-n-paso", "name": "E2E catalogo paso n"})
        self.assertEqual([p for p, _ in posts[1:]], ["/api/v1/orgs/cat-n-paso/roles"] * 2)


class WiringTest(unittest.TestCase):
    def test_group_is_selectable(self) -> None:
        self.assertIn("catalogo", e2e_h4.GROUPS)
        self.assertEqual(e2e_h4.parse_args(["--only", "catalogo"]).only, ["catalogo"])

    def test_step_names_follow_the_contract(self) -> None:
        names: list[str] = []

        class Fake:
            def step(self, name: str, fn: object, *, needs: object = ()) -> None:
                names.append(name)

        class Args:
            internal_token = "t"

        class E2E:
            args = Args()
            nonce = "n"

        e2e_catalogo.CatalogoGroup(E2E()).register(Fake(), ["0"])  # type: ignore[arg-type]
        self.assertEqual([n.split(" ")[0] for n in names], [f"17.{i}" for i in range(11)])


if __name__ == "__main__":
    unittest.main()
