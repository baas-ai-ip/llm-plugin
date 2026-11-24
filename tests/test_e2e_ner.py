"""Grupo «ner» (15.x) de la prueba de punta a punta de H4 (`dev/e2e_ner.py` y el inspector de `dev/e2e_h4.py`).

Los pasos 15.1–15.7 se ejecutan contra la pila real (la pila simulada de `dev/fake_h4_stack.py` no tiene NER); aquí se
prueban las utilidades puras con las que deciden y las consultas que el inspector hace a los contenedores (con un
motor de contenedores falso que anota sus argumentos). Con `unittest` (se ejecutan con `pytest` en la CI).
"""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))

import e2e_h4
import e2e_ner

ORG_ID = "5963754d-a8c6-4822-9b1b-17edadd88c99"
CONV_ID = "562fcbf4-4d2d-45df-87fa-cc2b4730fc53"
FIRST = "Hola, soy [[PERSON_001]], trabajo en [[ORG_001]] y vivo en [[LOCATION_001]]."


def segments(*parts: str) -> list[dict[str, str]]:
    """Segmentos `{text}` / `{token}` como los devuelve la webapp (`request_sent`): lo entre «[[» y «]]» es un token."""
    out: list[dict[str, str]] = []
    for part in parts:
        out.append({"token": part} if part.startswith("[[") else {"text": part})
    return out


def first_body(api: str, text: str = FIRST) -> dict[str, object]:
    return second_body(api, [], text)


def second_body(api: str, tail: list[dict[str, object]], text: str) -> dict[str, object]:
    """Lo que recibe el proveedor: el primer turno y, si `tail` no está vacío, sus mensajes siguientes."""
    if api == "responses":
        user = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
        return {"model": "m", "instructions": "s", "input": [user, *tail]}
    system = [{"role": "system", "content": "s"}] if api == "chat" else []
    return {"model": "m", "messages": [*system, {"role": "user", "content": text}, *tail]}


def follow_up(api: str, reply: str, new: str) -> list[dict[str, object]]:
    if api == "responses":
        return [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": new}]},
        ]
    return [{"role": "assistant", "content": reply}, {"role": "user", "content": new}]


class TokensTest(unittest.TestCase):
    def test_tokens_by_type_from_request_sent(self) -> None:
        sent = segments(
            "[system] Eres un asistente.\n\nHola, soy ", "[[PERSON_001]]", ", trabajo en ", "[[ORG_001]]",
            " y vivo en ", "[[LOCATION_001]]", ". Otra vez ", "[[PERSON_001]]", " y ", "[[PERSON_002]]",
        )  # fmt: skip
        self.assertEqual(
            e2e_ner.tokens_by_type_from_request_sent(sent),
            {"PERSON": ["[[PERSON_001]]", "[[PERSON_002]]"], "ORG": ["[[ORG_001]]"], "LOCATION": ["[[LOCATION_001]]"]},
        )

    def test_a_text_is_read_like_its_segments(self) -> None:
        parts = segments("Hola, soy ", "[[PERSON_001]]", ", trabajo en ", "[[ORG_001]]", " y vivo en ")
        parts += segments("[[LOCATION_001]]", ".")
        self.assertEqual(
            e2e_ner.tokens_by_type_from_request_sent(FIRST), e2e_ner.tokens_by_type_from_request_sent(parts)
        )
        self.assertEqual(e2e_ner.tokens_by_type_from_request_sent(None), {})
        self.assertEqual(e2e_ner.tokens_by_type_from_request_sent([{"text": "sin tokens"}]), {})

    def test_tokenization_is_good_with_the_three_tokens_and_no_value(self) -> None:
        self.assertEqual(e2e_ner.tokenization_problems(FIRST, e2e_ner.VALUES, e2e_ner.EXPECTED_1), [])

    def test_tokenization_reports_a_leaked_value_and_a_missing_or_wrong_token(self) -> None:
        text = "Hola, soy Ana Gómez, trabajo en [[ORG_001]] y vivo en [[LOCATION_002]]."
        problems = "\n".join(e2e_ner.tokenization_problems(text, e2e_ner.VALUES, e2e_ner.EXPECTED_1))
        self.assertIn("Ana Gómez", problems)
        self.assertIn("PERSON", problems)
        self.assertIn("LOCATION_002", problems)
        self.assertNotIn("ORG", problems.replace("ORG_001", ""))


class HistoryTest(unittest.TestCase):
    REPLY = "Eco: " + FIRST

    def second(self, api: str, **changes: str) -> dict[str, object]:
        reply = changes.get("reply", self.REPLY)
        text = changes.get("first", FIRST)
        return second_body(api, follow_up(api, reply, "Mi compañero [[PERSON_002]] vive en [[LOCATION_002]]."), text)

    def test_history_identical_between_turns(self) -> None:
        for api in ("messages", "chat", "responses"):
            self.assertEqual(e2e_ner.history_problems(api, first_body(api), self.second(api)), [], api)

    def test_a_changed_message_of_the_history_is_reported_with_where(self) -> None:
        changed = FIRST.replace("[[PERSON_001]]", "[[PERSON_009]]")
        for api in ("messages", "chat", "responses"):
            problems = e2e_ner.history_problems(api, first_body(api), self.second(api, first=changed))
            self.assertEqual(len(problems), 1, api)
            self.assertIn("no es idéntico", problems[0])

    def test_the_assistant_message_must_be_what_the_provider_answered(self) -> None:
        for api in ("messages", "chat", "responses"):
            restored = self.second(api, reply="Eco: " + FIRST.replace("[[PERSON_001]]", "Ana Gómez"))
            self.assertIn("asistente", "\n".join(e2e_ner.history_problems(api, first_body(api), restored)), api)

    def test_the_history_is_compared_with_its_key_order(self) -> None:
        """El proveedor guarda el cuerpo ya interpretado: lo más cerca de «byte a byte» es la serialización con el orden
        de claves de la petición (un diccionario con otro orden es igual para `==`)."""
        for api in ("messages", "chat", "responses"):
            second = self.second(api)
            items = second["input" if api == "responses" else "messages"]
            items[0] = dict(reversed(list(items[0].items())))  # type: ignore[index,arg-type]
            problems = e2e_ner.history_problems(api, first_body(api), second)
            self.assertEqual(len(problems), 1, api)
            self.assertIn("orden", problems[0])

    def test_a_second_turn_without_a_new_message_is_reported(self) -> None:
        for api in ("messages", "chat", "responses"):
            problems = e2e_ner.history_problems(api, first_body(api), first_body(api))
            self.assertTrue(problems, api)

    def test_the_new_message_is_the_last_one(self) -> None:
        body = self.second("messages")
        self.assertEqual(
            e2e_ner.new_user_text("messages", body), "Mi compañero [[PERSON_002]] vive en [[LOCATION_002]]."
        )
        self.assertEqual(e2e_ner.new_user_text("responses", self.second("responses")),
                         "Mi compañero [[PERSON_002]] vive en [[LOCATION_002]].")  # fmt: skip


class L2Test(unittest.TestCase):
    @staticmethod
    def l2(analyzed: int, cached: int = 0, unanalyzed: int = 0, status: str = "APPLIED") -> dict[str, object]:
        return {"status": status, "enabled": True, "cause": None, "analyzed_bytes": analyzed, "cached_bytes": cached,
                "unanalyzed_bytes": unanalyzed, "observed": 0}  # fmt: skip

    def test_the_second_turn_analyzes_only_what_is_new(self) -> None:
        self.assertEqual(e2e_ner.new_only_problems(self.l2(121), self.l2(46, cached=121), 46), [])

    def test_a_second_turn_that_analyzes_the_history_again_is_reported(self) -> None:
        problems = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(167), 46))
        self.assertIn("analyzed_bytes", problems)
        self.assertIn("167", problems)

    def test_seen_text_must_be_accounted_for_as_cached(self) -> None:
        problems = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(46, cached=100), 46))
        self.assertIn("cached_bytes", problems)

    def test_with_every_cache_layer_lost_seen_text_is_all_unanalyzed_and_not_awaited(self) -> None:
        lost = self.l2(46, cached=0, unanalyzed=121)
        self.assertEqual(e2e_ner.new_only_problems(self.l2(121), lost, 46, cache_lost=True), [])
        self.assertTrue(e2e_ner.new_only_problems(self.l2(121), lost, 46), "sin pérdida de caché no debería faltar")

    def test_the_system_text_is_awaited_with_the_cache_lost_and_the_messages_are_not(self) -> None:
        """Contra la pila real (15.4): el `system` (57 B) no es un mensaje de la correlación, así que se espera al NER
        por él en la misma llamada que lo nuevo (`analyzed_bytes` 102); los mensajes ya vistos (110 B) no se esperan."""
        got = self.l2(102, cached=0, unanalyzed=110)
        self.assertEqual(
            e2e_ner.new_only_problems(self.l2(46, cached=121), got, 45, cache_lost=True, awaited_extra=57), []
        )
        problems = "\n".join(e2e_ner.new_only_problems(self.l2(46, cached=121), got, 45, cache_lost=True))
        self.assertIn("analyzed_bytes 102", problems)
        late = self.l2(167, cached=0, unanalyzed=0)  # esperó por los mensajes ya vistos
        problems = "\n".join(
            e2e_ner.new_only_problems(self.l2(46, cached=121), late, 45, cache_lost=True, awaited_extra=57)
        )
        self.assertIn("analyzed_bytes 167", problems)
        self.assertIn("unanalyzed_bytes 0", problems)

    def test_with_the_cache_lost_anything_still_cached_or_awaited_is_reported(self) -> None:
        # el LRU del proxy sobrevivió: lo visto sale de la caché, así que el paso no prueba nada
        survived = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(46, cached=121), 46, cache_lost=True))
        self.assertIn("cached_bytes 121", survived)
        # se esperó al NER por lo ya visto: lo analizó de nuevo
        awaited = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(167), 46, cache_lost=True))
        self.assertIn("analyzed_bytes 167", awaited)
        partly = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(46, 0, 100), 46, cache_lost=True))
        self.assertIn("unanalyzed_bytes 100", partly)

    def test_a_turn_that_is_not_applied_is_reported(self) -> None:
        problems = "\n".join(e2e_ner.new_only_problems(self.l2(121), self.l2(46, 121, status="DEGRADED"), 46))
        self.assertIn("DEGRADED", problems)
        self.assertEqual(e2e_ner.new_only_problems(None, self.l2(46, 121), 46)[0].split(":")[0], "turno 1")


class ObservedTest(unittest.TestCase):
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("PERSON", "Ana Gómez"), ("ORG", "Telefónica"), ("LOCATION", "Valencia"),
    ]  # fmt: skip

    @staticmethod
    def observed(*items: tuple[str, str, int]) -> list[dict[str, object]]:
        return [{"token_type": t, "rule_id": f"0b1f6a52-8f0e-5c3a-9a41-6e2d7b3c1a0{i}", "original_value": v, "count": n}
                for i, (t, v, n) in enumerate(items, 1)]  # fmt: skip

    def test_observed_detections_match_expected_values(self) -> None:
        got = self.observed(("PERSON", "Ana Gómez", 1), ("ORG", "Telefónica", 1), ("LOCATION", "Valencia", 2))
        self.assertEqual(e2e_ner.observed_problems(got, self.EXPECTED), [])

    def test_missing_extra_misordered_and_wrong_values_are_reported(self) -> None:
        missing = self.observed(("PERSON", "Ana Gómez", 1), ("LOCATION", "Valencia", 1))
        self.assertIn("ORG", "\n".join(e2e_ner.observed_problems(missing, self.EXPECTED)))
        extra = self.observed(*[(t, v, 1) for t, v in self.EXPECTED], ("ORG", "Iberdrola", 1))
        self.assertIn("Iberdrola", "\n".join(e2e_ner.observed_problems(extra, self.EXPECTED)))
        swapped = self.observed(("ORG", "Telefónica", 1), ("PERSON", "Ana Gómez", 1), ("LOCATION", "Valencia", 1))
        self.assertTrue(e2e_ner.observed_problems(swapped, self.EXPECTED))
        wrong = self.observed(("PERSON", "Ana Gomez", 1), ("ORG", "Telefónica", 1), ("LOCATION", "Valencia", 1))
        self.assertIn("Ana Gomez", "\n".join(e2e_ner.observed_problems(wrong, self.EXPECTED)))

    def test_a_missing_list_or_a_bad_entry_is_reported(self) -> None:
        self.assertIn("observed_detections", e2e_ner.observed_problems(None, self.EXPECTED)[0])
        bad = self.observed(*[(t, v, 1) for t, v in self.EXPECTED])
        bad[0]["rule_id"] = ""
        bad[1]["count"] = 0
        self.assertEqual(len(e2e_ner.observed_problems(bad, self.EXPECTED)), 2)

    def test_the_verification_view_of_the_platform_admin_has_no_original_values(self) -> None:
        self.assertEqual(
            e2e_ner.hidden_problems(self.observed(("PERSON", "Ana Gómez", 1))), ["PERSON: lleva original_value"]
        )
        hidden = [{"token_type": "PERSON", "rule_id": "x", "count": 1}]
        self.assertEqual(e2e_ner.hidden_problems(hidden), [])
        self.assertEqual(e2e_ner.hidden_problems(None), [])


class LiveTestTest(unittest.TestCase):
    TEXT = "Hola, soy Ana Gómez, trabajo en Telefónica y vivo en Valencia."

    @staticmethod
    def match(text: str, kind: str, value: str, result: str, token: str | None) -> dict[str, object]:
        start = text.index(value)
        return {"rule_index": 0, "rule_id": "r", "layer": "NER", "token_type": kind, "start": start,
                "end": start + len(value), "value": value, "token": token, "score": 0.99, "result": result}  # fmt: skip

    def body(self, allow: bool = False) -> dict[str, object]:
        orgs = ("ALLOWLISTED", None) if allow else ("REPLACED", "[[ORG_001]]")
        return {
            "anonymized": "x",
            "matches": [
                self.match(self.TEXT, "PERSON", "Ana Gómez", "REPLACED", "[[PERSON_001]]"),
                self.match(self.TEXT, "ORG", "Telefónica", *orgs),
                self.match(self.TEXT, "LOCATION", "Valencia", "REPLACED", "[[LOCATION_001]]"),
            ],
            "ner": {"status": "OK", "model": "m"},
        }

    def test_ner_matches_are_replaced_with_offsets_in_code_points(self) -> None:
        want = [("PERSON", "Ana Gómez", "REPLACED"), ("ORG", "Telefónica", "REPLACED"),
                ("LOCATION", "Valencia", "REPLACED")]  # fmt: skip
        self.assertEqual(e2e_ner.live_test_problems(self.TEXT, self.body(), want), [])

    def test_allowlisted_has_no_token(self) -> None:
        want = [("PERSON", "Ana Gómez", "REPLACED"), ("ORG", "Telefónica", "ALLOWLISTED"),
                ("LOCATION", "Valencia", "REPLACED")]  # fmt: skip
        self.assertEqual(e2e_ner.live_test_problems(self.TEXT, self.body(allow=True), want), [])
        problems = "\n".join(e2e_ner.live_test_problems(self.TEXT, self.body(), want))
        self.assertIn("ALLOWLISTED", problems)

    def test_an_integer_score_is_a_score_but_a_boolean_is_not(self) -> None:
        want = [("PERSON", "Ana Gómez", "REPLACED")]
        body = self.body()
        body["matches"][0]["score"] = 1  # type: ignore[index]
        self.assertEqual(e2e_ner.live_test_problems(self.TEXT, body, want), [])
        body["matches"][0]["score"] = True  # type: ignore[index]
        self.assertIn("score", "\n".join(e2e_ner.live_test_problems(self.TEXT, body, want)))

    def test_unavailable_service_wrong_layer_and_bad_offsets_are_reported(self) -> None:
        want = [("PERSON", "Ana Gómez", "REPLACED")]
        body = self.body()
        body["ner"] = {"status": "UNAVAILABLE", "model": None}
        self.assertIn("UNAVAILABLE", "\n".join(e2e_ner.live_test_problems(self.TEXT, body, want[:1])))
        body = self.body()
        body["matches"][0]["layer"] = "L1"  # type: ignore[index]
        self.assertIn("layer", "\n".join(e2e_ner.live_test_problems(self.TEXT, body, want[:1])))
        body = self.body()
        body["matches"][0]["start"] += 1  # type: ignore[index]
        self.assertIn("desplazamientos", "\n".join(e2e_ner.live_test_problems(self.TEXT, body, want[:1])))


class InspectorTest(unittest.TestCase):
    """`ContainerInspector` (métodos de la Fase 2) con un motor falso que anota sus argumentos y responde lo dado."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.log = root / "calls.jsonl"
        self.replies = root / "replies.json"
        self.replies.write_text("{}")
        engine = root / "engine"
        engine.write_text(
            f"""#!{sys.executable}
import json, sys
argv = sys.argv[1:]
open({str(self.log)!r}, "a").write(json.dumps(argv) + "\\n")
if argv[0] == "ps":
    print("cid-" + [a for a in argv if a.startswith("label=com.docker.compose.service=")][0].split("=")[-1])
    sys.exit(0)
replies = json.load(open({str(self.replies)!r}))
for needle, (out, err, rc) in replies.items():
    if needle in " ".join(argv):
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(rc)
"""
        )
        engine.chmod(engine.stat().st_mode | stat.S_IXUSR)
        self.inspector = e2e_h4.ContainerInspector(str(engine), "f2-ner")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def reply(self, **by_needle: tuple[str, str, int]) -> None:
        self.replies.write_text(json.dumps({k.replace("_", " "): v for k, v in by_needle.items()}))

    def runs(self) -> list[list[str]]:
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c for c in calls if c[0] != "ps"]

    def test_scan_looks_only_in_the_ephemeral_instance_and_only_for_the_caches_of_an_organization(self) -> None:
        key = f"baas:ner:{ORG_ID}:modelo:abcd1234:" + "0" * 64
        self.reply(**{"--scan": (f"{key}\n", "", 0)})
        self.assertEqual(self.inspector.ephemeral_scan(f"baas:ner:{ORG_ID}:*"), [key])
        self.assertEqual(self.runs()[0][:2], ["exec", "cid-redis-ephemeral"])
        self.assertEqual(self.runs()[0][-3:], ["--scan", "--pattern", f"baas:ner:{ORG_ID}:*"])
        self.inspector.ephemeral_scan(f"baas:conv:{CONV_ID}:vault")

    def test_other_patterns_are_refused_before_any_command(self) -> None:
        for pattern in ("*", "baas:*", "baas:traces", "baas:ner:*", f"baas:ner:{ORG_ID}", f"baas:conv:{CONV_ID}:events",
                        f"baas:policy:org:{ORG_ID}", f"baas:ner:{ORG_ID}:*; FLUSHALL"):  # fmt: skip
            with self.assertRaises(e2e_h4.StepError, msg=pattern):
                self.inspector.ephemeral_scan(pattern)
        self.assertEqual(self.runs(), [])

    def test_delete_removes_only_cache_and_hot_vault_keys_in_batches(self) -> None:
        keys = [f"baas:ner:{ORG_ID}:m:a:{i:064d}" for i in range(450)] + [f"baas:conv:{CONV_ID}:vault"]
        self.reply(DEL=("100\n", "", 0))
        self.assertEqual(self.inspector.ephemeral_delete(keys), 100 * 5, "una ráfaga por cada 100 claves")
        dels = [r for r in self.runs() if "DEL" in r]
        self.assertEqual(len(dels), 5)
        self.assertTrue(all(r[1] == "cid-redis-ephemeral" for r in dels))
        self.assertEqual(self.inspector.ephemeral_delete([]), 0)
        before = len(self.runs())
        for bad in ("baas:traces", f"baas:policy:org:{ORG_ID}", f"baas:conv:{CONV_ID}:tombstone", "baas:ner:x y"):
            with self.assertRaises(e2e_h4.StepError, msg=bad):
                self.inspector.ephemeral_delete([f"baas:conv:{CONV_ID}:vault", bad])
        self.assertEqual(len(self.runs()), before, "nada se borra si alguna clave no vale")

    def test_ner_can_be_stopped_and_started_and_nothing_else(self) -> None:
        self.inspector.ner_control("stop")
        self.inspector.ner_control("start")
        self.assertEqual(self.runs()[0], ["stop", "cid-ner"])
        self.assertEqual(self.runs()[1], ["start", "cid-ner"])
        for action in ("restart", "rm", "pause", ""):
            with self.assertRaises(e2e_h4.StepError, msg=action):
                self.inspector.ner_control(action)

    def test_ner_health(self) -> None:
        self.reply(inspect=("healthy\n", "", 0))
        self.assertEqual(self.inspector.ner_health(), "healthy")
        self.assertEqual(self.runs()[0], ["inspect", "-f", "{{.State.Health.Status}}", "cid-ner"])

    def test_ner_health_without_a_healthcheck_says_so(self) -> None:
        for out in ("", "<no value>\n"):
            self.reply(inspect=(out, "", 0))
            with self.assertRaises(e2e_h4.StepError) as ctx:
                self.inspector.ner_health()
            self.assertIn("healthcheck", str(ctx.exception))
        self.reply(inspect=("", "map has no entry", 1))
        with self.assertRaises(e2e_h4.StepError) as ctx:
            self.inspector.ner_health()
        self.assertIn("healthcheck", str(ctx.exception))

    def test_the_proxy_can_be_stopped_started_and_its_health_read(self) -> None:
        self.inspector.service_control("llm-interceptor-proxy", "stop")
        self.inspector.service_control("llm-interceptor-proxy", "start")
        self.reply(inspect=("healthy\n", "", 0))
        self.assertEqual(self.inspector.service_health("llm-interceptor-proxy"), "healthy")
        self.assertEqual(self.runs()[0], ["stop", "cid-llm-interceptor-proxy"])
        self.assertEqual(self.runs()[1], ["start", "cid-llm-interceptor-proxy"])
        for service in ("postgres", "redis", "ner"):
            with self.assertRaises(e2e_h4.StepError, msg=service):
                self.inspector.service_health(service)

    def test_missing_inspector_says_how_to_continue(self) -> None:
        missing = e2e_h4.MissingInspector("podman no está instalado")
        for name in ("ephemeral_scan", "ephemeral_delete", "ner_control", "ner_health", "service_health"):
            with self.assertRaises(e2e_h4.StepError, msg=name):
                getattr(missing, name)("x")


class GroupWiringTest(unittest.TestCase):
    def test_ner_goes_after_limite_and_is_in_the_default_list(self) -> None:
        groups = e2e_h4.GROUPS
        self.assertEqual(groups.index("ner"), groups.index("limite") + 1, "los pasos 15.x van después de los 14.x")
        self.assertIn("ner", e2e_h4.parse_args([]).only)
        self.assertEqual(e2e_h4.parse_args(["--only", "ner"]).only, ["ner"])

    def test_the_steps_without_the_inspector_leave_out_15_4_and_15_5(self) -> None:
        names: list[str] = []

        class Recorder:
            def step(self, name: str, fn: object, *, needs: object = ()) -> None:
                names.append(name)

        ns = e2e_h4.parse_args(["--only", "ner", "--inspect", "none"])
        e2e_h4.E2EH4(ns, inspector=None).run(Recorder())  # type: ignore[arg-type]
        self.assertEqual([n.split(" ")[0] for n in names if n.startswith("15.")],
                         ["15.1", "15.2", "15.3", "15.6", "15.7"])  # fmt: skip
        names.clear()
        e2e_h4.E2EH4(ns, inspector=e2e_h4.MissingInspector("prueba")).run(Recorder())  # type: ignore[arg-type]
        self.assertEqual([n.split(" ")[0] for n in names if n.startswith("15.")], [f"15.{i}" for i in range(1, 8)])


if __name__ == "__main__":
    unittest.main()
