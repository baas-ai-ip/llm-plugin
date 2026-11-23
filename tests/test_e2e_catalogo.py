"""Grupo «catalogo» (15.x) de la prueba de punta a punta de H4 (`dev/e2e_catalogo.py`).

Los pasos 15.0–15.9 se ejecutan contra la pila real; aquí se prueban las utilidades puras con las que deciden, para que
un paso no pase por error (que no vea un cuerpo, que dé por buena una respuesta `200` de `/internal`). Con `unittest`.
"""

from __future__ import annotations

import json
import sys
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


class FilesRouteTest(unittest.TestCase):
    """15.7: la única respuesta que puede llevar un cuerpo es la 200 de la ruta de ficheros de una skill."""

    def test_only_the_200_of_the_files_route_is_exempt(self) -> None:
        exempt = e2e_catalogo.is_file_content
        self.assertTrue(exempt("GET", "skills/base/files/SKILL.md", 200))
        self.assertTrue(exempt("GET", "skills/base/files/scripts/hola.py", 200))
        for status in (401, 403, 404, 413, 415, 422, 503):
            self.assertFalse(exempt("GET", "skills/base/files/SKILL.md", status), status)

    def test_nothing_else_is_exempt(self) -> None:
        exempt = e2e_catalogo.is_file_content
        for path in (
            "skills",
            "skills/base",
            "skills/base/files",
            "skills/base/roles",
            "knowledge",
            "knowledge/a/files/b",
        ):
            self.assertFalse(exempt("GET", path, 200), path)
        self.assertFalse(exempt("PUT", "skills/base/files/SKILL.md", 200))
        self.assertFalse(exempt("GET", "../skills/base/files/SKILL.md", 200))

    def test_a_body_in_any_other_response_still_fails(self) -> None:
        for path in ("skills", "skills/base", "knowledge/ventas/guia.md", "skills/base/roles"):
            seen = [e2e_catalogo.Seen(f"GET {path}", FILE_OK, e2e_catalogo.is_file_content("GET", path, 200))]
            got = e2e_catalogo.scan_seen(seen, SENTINELS)
            self.assertTrue(any("CUERPO-SKILL-1" in p for p in got), path)
            self.assertTrue(any("«content»" in p for p in got), path)

    def test_the_200_of_the_files_route_does_not_fail(self) -> None:
        seen = [
            e2e_catalogo.Seen(
                "GET files", FILE_OK, e2e_catalogo.is_file_content("GET", "skills/base/files/SKILL.md", 200)
            )
        ]
        self.assertEqual(e2e_catalogo.scan_seen(seen, SENTINELS), [])

    def test_the_errors_of_the_files_route_are_scanned(self) -> None:
        for status in (403, 404, 413, 415, 422):
            flag = e2e_catalogo.is_file_content("GET", "skills/base/files/scripts/grande.txt", status)
            got = e2e_catalogo.scan_seen([e2e_catalogo.Seen(f"GET files {status}", FILE_ERR, flag)], SENTINELS)
            self.assertEqual(len(got), 1, status)  # el centinela en el mensaje; sin claves de contenido en un error

    def test_one_leak_among_clean_responses_is_reported_once(self) -> None:
        clean = json.dumps({"skills": []}).encode()
        seen = [
            e2e_catalogo.Seen("a", clean),
            e2e_catalogo.Seen("exenta", FILE_OK, True),
            e2e_catalogo.Seen("b", b'{"title": "CUERPO-DOC-1"}'),
        ]
        got = e2e_catalogo.scan_seen(seen, SENTINELS)
        self.assertEqual(len(got), 1)
        self.assertIn("b:", got[0])


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
        self.assertEqual([n.split(" ")[0] for n in names], [f"15.{i}" for i in range(10)])


if __name__ == "__main__":
    unittest.main()
