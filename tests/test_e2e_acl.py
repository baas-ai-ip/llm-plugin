"""Grupo «acl» (13.x) de la prueba de punta a punta de H4 (`dev/e2e_acl.py` y el inspector de `dev/e2e_h4.py`).

Los pasos 13.1–13.5 se ejecutan contra la pila real con la ACL de Redis activa; aquí se prueban las utilidades puras,
los pasos con un inspector falso y las órdenes que el inspector real manda al motor de contenedores (falso).
Con `unittest` (se ejecutan con `pytest` en la CI).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))

import e2e_acl
import e2e_h4
from e2e_h2 import Runner

ATTACKS = (
    "HSET baas:policy:org:demo blocked 0",
    "HSET baas:key:e2e-acl user_id x",
    "HSET baas:member:e2e-acl:demo governance_role ORG_ADMIN",
    "SET baas:platform:status x",
    "PUBLISH baas:control x",
)
NOPERM = "NOPERM User {user} has no permissions to run the command"

CLIENT_LIST = """\
id=3 addr=10.89.0.5:40000 laddr=10.89.0.2:6379 name= user=webapp cmd=get
id=4 addr=10.89.0.6:40001 laddr=10.89.0.2:6379 name= user=proxy cmd=hgetall
id=5 addr=10.89.0.7:40002 laddr=10.89.0.2:6379 name= user=webapp cmd=xreadgroup
id=6 addr=127.0.0.1:50000 laddr=127.0.0.1:6379 name= user=default cmd=ping
id=7 addr=[::1]:50001 laddr=[::1]:6379 name= user=default cmd=ping
id=8 addr=127.0.0.1:50002 laddr=127.0.0.1:6379 name= user=default cmd=client|list
"""


class PureTest(unittest.TestCase):
    def test_is_noperm(self) -> None:
        self.assertTrue(e2e_acl.is_noperm("NOPERM User mcp has no permissions to run the 'hset' command"))
        for reply in ("OK", "(integer) 1", "", "WRONGPASS invalid username-password pair"):
            self.assertFalse(e2e_acl.is_noperm(reply), reply)

    def test_client_users_ignores_loopback_connections(self) -> None:
        self.assertEqual(e2e_acl.client_users(CLIENT_LIST), {"webapp", "proxy"})
        self.assertEqual(e2e_acl.client_users("id=1 addr=::1:5000 user=default\n"), set())
        self.assertEqual(e2e_acl.client_users("id=1 addr=10.0.0.9:5000 user=default\n"), {"default"})
        self.assertEqual(e2e_acl.client_users(""), set())


class FakeInspector:
    """Responde como Redis con la ACL: `NOPERM` salvo lo que `allowed` permite (usuario, comando) o `reply` cambia."""

    def __init__(self, allowed: dict[tuple[str, str], str] | None = None) -> None:
        self.allowed = allowed or {}
        self.calls: list[tuple[str, str, str]] = []
        self.clients = {
            "redis": CLIENT_LIST + "id=9 addr=10.89.0.8:40003 user=mcp cmd=get\n",
            "redis-ephemeral": CLIENT_LIST,
        }
        self.ephemeral_mcp = ""

    def redis_as(self, service: str, user: str, command: str) -> str:
        self.calls.append((service, user, command))
        return self.allowed.get((user, command), NOPERM.format(user=user))

    def redis_admin(self, service: str, *args: str) -> str:
        args = tuple(a for a in args if a != "--raw")
        if args[:2] == ("CLIENT", "LIST"):
            return self.clients[service]
        if args[:3] == ("ACL", "GETUSER", "mcp"):
            return self.ephemeral_mcp
        raise AssertionError(args)


def run_group(inspector: FakeInspector) -> dict[str, tuple[str, str]]:
    e2e = e2e_h4.E2EH4(e2e_h4.parse_args(["--inspect", "none"]), inspector=inspector)  # type: ignore[arg-type]
    runner = Runner(echo=False)
    e2e_acl.register(runner, e2e, [])
    return {r.name.split(" ")[0]: (r.status, r.detail) for r in runner.results}


class GroupStepsTest(unittest.TestCase):
    def test_group_passes_when_every_attempt_is_denied(self) -> None:
        inspector = FakeInspector()
        got = run_group(inspector)
        self.assertEqual(list(got), ["13.1", "13.2", "13.3", "13.4", "13.5"])
        self.assertEqual({s for s, _ in got.values()}, {"OK"}, got)
        for user in ("mcp", "proxy"):
            self.assertEqual([c for _, u, c in inspector.calls if u == user], list(ATTACKS))
        self.assertIn(("redis-ephemeral", "webapp", "XADD baas:traces * data x"), inspector.calls)
        self.assertEqual(
            [(s, c) for s, u, c in inspector.calls if u == "monitor"],
            [("redis", "GET baas:platform:status"), ("redis", "HGETALL baas:policy:org:demo")],
        )

    def test_group_fails_when_a_write_is_accepted(self) -> None:
        inspector = FakeInspector({("mcp", "HSET baas:policy:org:demo blocked 0"): "OK"})
        got = run_group(inspector)
        status, detail = got["13.2"]
        self.assertEqual(status, "FALLO")
        self.assertIn("HSET baas:policy:org:demo blocked 0", detail)
        self.assertEqual(got["13.3"][0], "OK")

    def test_a_service_that_enters_as_default_or_is_missing_fails_13_1(self) -> None:
        inspector = FakeInspector()
        inspector.clients["redis"] += "id=10 addr=10.89.0.9:40004 user=default cmd=get\n"
        status, detail = run_group(inspector)["13.1"]
        self.assertEqual(status, "FALLO")
        self.assertIn("default", detail)
        inspector = FakeInspector()
        inspector.clients["redis-ephemeral"] = "id=3 addr=10.89.0.5:40000 user=webapp cmd=get\n"
        status, detail = run_group(inspector)["13.1"]
        self.assertEqual(status, "FALLO")
        self.assertIn("proxy", detail)

    def test_13_4_fails_if_the_ephemeral_has_an_mcp_user(self) -> None:
        inspector = FakeInspector()
        inspector.ephemeral_mcp = "flags\non\n"
        self.assertEqual(run_group(inspector)["13.4"][0], "FALLO")

    def test_13_5_fails_if_the_monitor_can_read(self) -> None:
        inspector = FakeInspector({("monitor", "GET baas:platform:status"): "ok"})
        status, detail = run_group(inspector)["13.5"]
        self.assertEqual(status, "FALLO")
        self.assertIn("GET baas:platform:status", detail)


class UnusableAttemptTest(unittest.TestCase):
    """Un intento que no se pudo hacer (auth, SETNAME, conexión) no es un rechazo de la ACL: el paso falla."""

    def test_a_non_noperm_reply_fails_the_step(self) -> None:
        for reply in ("WRONGPASS invalid username-password pair", "Could not connect to Redis", ""):
            inspector = FakeInspector({("mcp", c): reply for c in ATTACKS})
            status, detail = run_group(inspector)["13.2"]
            self.assertEqual(status, "FALLO", reply)
            self.assertIn(ATTACKS[0], detail)

    def test_a_step_error_from_redis_as_is_a_failed_step_not_a_crash(self) -> None:
        class Broken(FakeInspector):
            def redis_as(self, service: str, user: str, command: str) -> str:
                raise e2e_h4.StepError(f"no se pudo nombrar la conexión de {user} en {service}")

        got = run_group(Broken())
        self.assertEqual(got["13.2"][0], "FALLO")
        self.assertIn("no se pudo nombrar", got["13.2"][1])
        self.assertEqual(got["13.1"][0], "OK", "el resto de pasos sigue")


class RedisAsTest(unittest.TestCase):
    def inspector(self) -> e2e_h4.ContainerInspector:
        return e2e_h4.ContainerInspector("fake", "acl-int")

    def test_redis_as_rejects_unsafe_command(self) -> None:
        for user, command in (("mcp", "GET x; FLUSHALL"), ("mcp", "GET $(id)"), ("mcp", ""), ("mcp", "x" * 201),
                              ("default", "GET x"), ("root", "GET x")):  # fmt: skip
            with self.assertRaises(e2e_h4.StepError, msg=(user, command)):
                self.inspector().redis_as("redis", user, command)

    def test_redis_as_names_the_client_and_returns_the_reply_of_the_command(self) -> None:
        insp = self.inspector()
        seen: list[tuple[str, ...]] = []

        def exec_(service: str, *argv: str, check_rc: bool = True) -> str:
            seen.append((service, *argv))
            return "OK\nNOPERM User mcp has no permissions\n"

        with mock.patch.object(insp, "_exec", exec_):
            reply = insp.redis_as("redis", "mcp", "SET baas:platform:status x")
        self.assertEqual(reply, "NOPERM User mcp has no permissions")
        service, *argv = seen[0]
        self.assertEqual(service, "redis")
        script = argv[2]
        self.assertIn("BAAS_REDIS_MCP_PASSWORD", script)
        self.assertIn("CLIENT SETNAME e2e-acl", script)
        self.assertIn("--user mcp", script)
        self.assertEqual(argv[-1], "SET baas:platform:status x", "la orden llega como argumento, no en el guion")

    def run_as(self, out: str) -> str:
        insp = self.inspector()
        with mock.patch.object(insp, "_exec", return_value=out):
            return insp.redis_as("redis", "mcp", "GET x")

    def test_redis_as_fails_unless_setname_replied_ok(self) -> None:
        for out in (
            "AUTH failed: WRONGPASS invalid username-password pair\nNOAUTH Authentication required.\n",
            "NOPERM User mcp has no permissions to run the 'client|setname' command\nNOPERM x\n",
            "",
            "OKAY\nNOPERM x\n",
        ):
            with self.assertRaises(e2e_h4.StepError, msg=out) as ctx:
                self.run_as(out)
            self.assertIn("mcp", str(ctx.exception))
            self.assertIn("redis", str(ctx.exception))
        self.assertEqual(self.run_as("OK\nvalor\n"), "valor")
        self.assertEqual(self.run_as("OK\n"), "")

    def test_redis_admin_is_the_default_cli(self) -> None:
        insp = self.inspector()
        with mock.patch.object(insp, "_redis_cli", return_value="x") as cli:
            self.assertEqual(insp.redis_admin("redis", "CLIENT", "LIST"), "x")
        cli.assert_called_once_with("redis", "CLIENT", "LIST")


class GroupWiringTest(unittest.TestCase):
    def test_acl_goes_before_limite_and_needs_the_inspector(self) -> None:
        groups = e2e_h4.GROUPS
        self.assertEqual(groups.index("limite"), groups.index("acl") + 1, "los pasos 13.x y después los 14.x")
        self.assertIn("acl", e2e_h4.parse_args([]).only)
        self.assertEqual(e2e_acl.CLIENT_NAME, "e2e-acl")
        ns = e2e_h4.parse_args(["--only", "acl", "--inspect", "none"])
        names: list[str] = []

        class Recorder:
            def step(self, name: str, fn: object, *, needs: object = ()) -> None:
                names.append(name)

        e2e_h4.E2EH4(ns, inspector=None).run(Recorder())  # type: ignore[arg-type]
        self.assertFalse([n for n in names if n.startswith("13.")])
        names.clear()
        e2e_h4.E2EH4(ns, inspector=e2e_h4.MissingInspector("prueba")).run(Recorder())  # type: ignore[arg-type]
        self.assertEqual([n.split(" ")[0] for n in names if n.startswith("13.")], [f"13.{i}" for i in range(1, 6)])


if __name__ == "__main__":
    unittest.main()
