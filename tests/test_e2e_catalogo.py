"""Grupo «catalogo» (13.x) de la prueba de punta a punta de H4 (`dev/e2e_catalogo.py`).

Los pasos 13.0–13.8 se ejecutan contra la pila real; aquí se prueban las utilidades puras con las que deciden, para que
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
        self.assertEqual([n.split(" ")[0] for n in names], [f"13.{i}" for i in range(9)])


if __name__ == "__main__":
    unittest.main()
