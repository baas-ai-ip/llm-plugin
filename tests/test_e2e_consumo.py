"""Grupo «consumo» (16.x) de la prueba de punta a punta de H4 (`dev/e2e_consumo.py`).

Los pasos 16.0–16.8 se ejecutan contra la pila real (la simulada de `dev/fake_h4_stack.py` sirve los informes de
turnos por modo, los globales y el del anonimizador, no el disco de 16.2 ni el CSV de 16.3); aquí se prueban las
utilidades puras con las que deciden y el recorrido del grupo (orden, dependencias y qué se salta si la pila no trae el
informe). Con `unittest` (se ejecutan con `pytest` en la CI). Los pasos contra la pila simulada (16.0, 16.1 y 16.4 a
16.8) están en `tests/test_e2e_h4.py`.
"""

from __future__ import annotations

import contextlib
import copy
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))

import e2e_consumo
import e2e_h4

# El informe de una organización como lo define el contrato de la Fase 2 (§7.3)
ZERO_ANON = {
    "turns": 0, "turns_by_mode": {"ANONYMIZED": 0, "OBSERVATION": 0, "BYPASS": 0},
    "tokens_in": 0, "tokens_out": 0, "bytes_in": 0, "replaced_in": 0, "restored_out": 0,
    "verified_clean": 0, "verified_warning": 0, "verified_error": 0,
    "l2_turns": 0, "l2_enabled": 0, "l2_applied": 0, "l2_degraded": 0, "l2_observed_turns": 0,
    "l2_known_values_only": 0, "l2_analyzed_bytes": 0, "l2_cached_bytes": 0, "l2_unanalyzed_bytes": 0,
    "l2_observed": 0,
}  # fmt: skip


def org_row(**overrides: object) -> dict:
    row = {
        "slug": "consumo-abc-modos", "name": "Consumo", "timezone": "Europe/Madrid", "blocked": False, "paused": False,
        "requests": 6, "blocked_requests": 0, "errors": 0, "tokens_in": 72, "tokens_out": 54, "bytes_in": 9000,
        "bytes_out": 5000, "active_users": 1, "invocations": 0, "storage_bytes": None, "storage_taken_at": None,
        "content": {"invoked": 0, "synced": 0, "skill_read": 0, "script_downloaded": 0, "knowledge_read": 0,
                    "knowledge_search": 0, "skills": None, "skills_capped": False, "areas": None,
                    "areas_capped": False},
        "disk": {"avg": None, "max": None, "last": None, "index_last": None, "snapshots": 0,
                 "first_taken_at": None, "taken_at": None},
        "anonymizer": {**ZERO_ANON, "turns": 6, "tokens_in": 72, "tokens_out": 54, "bytes_in": 9000,
                       "turns_by_mode": {"ANONYMIZED": 3, "OBSERVATION": 2, "BYPASS": 1},
                       "replaced_in": 9, "restored_out": 9, "verified_clean": 3, "verified_warning": 2},
    }  # fmt: skip
    row.update(overrides)
    return row


def sent_turns() -> list[e2e_consumo.Sent]:
    """Un turno en bypass, dos en observación y tres anonimizados (con los tres valores sustituidos)."""
    clean = {"status": "CLEAN", "request_replacements_by_type": {"DNI": 1, "EMAIL": 1, "IBAN": 1},
             "response_replacements_by_type": {"DNI": 1, "EMAIL": 1, "IBAN": 1}}  # fmt: skip
    warning = {"status": "WARNING", "request_replacements_by_type": {}, "response_replacements_by_type": {}}
    turns = [e2e_consumo.Sent("BYPASS", 12, 9, "BYPASS", None)]
    turns += [e2e_consumo.Sent("OBSERVATION", 12, 9, "OBSERVATION", warning) for _ in range(2)]
    turns += [e2e_consumo.Sent("ANONYMIZED", 12, 9, "ANONYMIZED", clean) for _ in range(3)]
    return turns


class PeriodTest(unittest.TestCase):
    def test_current_month_is_the_utc_one(self) -> None:
        self.assertEqual(e2e_consumo.current_month(datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)), "2026-11")
        self.assertEqual(e2e_consumo.current_month(datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)), "2026-12")

    def test_range_covers_yesterday_and_today_in_utc(self) -> None:
        now = datetime(2026, 12, 1, 0, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(e2e_consumo.utc_range(now), {"from": "2026-11-30", "to": "2026-12-01"})


class ModeTest(unittest.TestCase):
    def test_mode_of_the_turn_state(self) -> None:
        self.assertEqual(e2e_consumo.mode_of(False, False), "BYPASS")
        self.assertEqual(e2e_consumo.mode_of(False, True), "BYPASS", "con AI-IP apagado el anonimizador no cuenta")
        self.assertEqual(e2e_consumo.mode_of(True, False), "OBSERVATION")
        self.assertEqual(e2e_consumo.mode_of(True, True), "ANONYMIZED")

    def test_replacements_are_the_sum_of_the_values(self) -> None:
        self.assertEqual(e2e_consumo.replacements_total({"DNI": 1, "EMAIL": 2}), 3)
        for bad in (None, [], "x", {"DNI": "1"}, {"DNI": True}):
            self.assertEqual(e2e_consumo.replacements_total(bad), 0, repr(bad))


class Step160Test(unittest.TestCase):
    def test_a_report_with_period_and_sources_is_enough(self) -> None:
        data = {"period": {"month": "2026-11", "open": True}, "data_until_by_source": {"traffic": None}}
        self.assertIsNone(e2e_consumo.consumo_problem(200, data))

    def test_a_stack_without_the_report_is_named(self) -> None:
        phase4 = {"tz": "UTC", "from": "2026-11-01", "to": "2026-11-30", "totals": {}, "orgs": []}
        message = e2e_consumo.consumo_problem(200, phase4) or ""
        self.assertIn("sin el informe de consumo", message)
        self.assertIn("period", message)
        self.assertIn("T2", message)
        self.assertIn("reconstruye", message)
        self.assertIn("period", e2e_consumo.consumo_problem(200, {"period": {}, "orgs": []}) or "")

    def test_other_statuses(self) -> None:
        self.assertIn("404", e2e_consumo.consumo_problem(404, {"error": "NOT_FOUND"}) or "")
        self.assertIn("500", e2e_consumo.consumo_problem(500, None) or "")
        self.assertIn("403", e2e_consumo.consumo_problem(403, {"error": "FORBIDDEN"}) or "")


class Step161Test(unittest.TestCase):
    def test_what_was_sent_is_what_the_report_must_say(self) -> None:
        want = e2e_consumo.expected_figures(sent_turns())
        self.assertEqual(want["requests"], 6)
        self.assertEqual(want["turns_by_mode"], {"ANONYMIZED": 3, "OBSERVATION": 2, "BYPASS": 1})
        self.assertEqual((want["tokens_in"], want["tokens_out"]), (72, 54))
        self.assertEqual((want["replaced_in"], want["restored_out"]), (9, 9))
        self.assertEqual(want["verified"], {"clean": 3, "warning": 2, "error": 0})

    def test_a_matching_report_has_no_problems(self) -> None:
        self.assertEqual(e2e_consumo.mode_problems(org_row(), e2e_consumo.expected_figures(sent_turns())), [])

    def test_the_turns_must_have_left_in_the_mode_that_was_asked(self) -> None:
        turns = sent_turns()
        self.assertEqual(e2e_consumo.state_problems(turns), [])
        turns[1].state = "ANONYMIZED"  # el interruptor no había llegado al proxy
        turns[3].state = None  # el turno no dice con qué estado salió
        problems = e2e_consumo.state_problems(turns)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("OBSERVATION", problems[0])
        self.assertIn("ANONYMIZED", problems[0])

    def test_a_mode_mix_up_is_found(self) -> None:
        row = org_row()
        row["anonymizer"]["turns_by_mode"] = {"ANONYMIZED": 5, "OBSERVATION": 0, "BYPASS": 1}
        problems = e2e_consumo.mode_problems(row, e2e_consumo.expected_figures(sent_turns()))
        self.assertTrue(any("turns_by_mode" in p and "OBSERVATION" in p for p in problems), problems)

    def test_each_figure_is_compared_exactly(self) -> None:
        want = e2e_consumo.expected_figures(sent_turns())
        cases = {
            "requests": lambda r: r.update(requests=7),
            "tokens_in": lambda r: r.update(tokens_in=71),
            "tokens_out": lambda r: r.update(tokens_out=55),
            "replaced_in": lambda r: r["anonymizer"].update(replaced_in=8),
            "restored_out": lambda r: r["anonymizer"].update(restored_out=0),
            "verified_clean": lambda r: r["anonymizer"].update(verified_clean=2),
            "verified_warning": lambda r: r["anonymizer"].update(verified_warning=3),
            "verified_error": lambda r: r["anonymizer"].update(verified_error=1),
        }
        for name, mutate in cases.items():
            row = org_row()
            mutate(row)
            problems = e2e_consumo.mode_problems(row, want)
            self.assertTrue(any(name in p for p in problems), f"{name}: {problems}")

    def test_the_contract_invariants(self) -> None:
        want = e2e_consumo.expected_figures(sent_turns())
        row = org_row()
        row["anonymizer"]["turns"] = 5  # ≠ Σ turns_by_mode y ≠ requests
        problems = " ".join(e2e_consumo.mode_problems(row, want))
        self.assertIn("Σ turns_by_mode", problems)
        self.assertIn("requests", problems)
        row = org_row()
        row["anonymizer"]["bytes_in"] = 1  # el anonimizador cuelga del tráfico: los mismos bytes
        self.assertIn("bytes_in", " ".join(e2e_consumo.mode_problems(row, want)))
        row = org_row()
        del row["anonymizer"]["turns_by_mode"]["BYPASS"]  # «siempre las tres claves»
        self.assertIn("BYPASS", " ".join(e2e_consumo.mode_problems(row, want)))
        row = org_row()
        row["anonymizer"].update(verified_clean=5, verified_warning=5)  # más verificados que turnos
        self.assertIn("≤ turns", " ".join(e2e_consumo.mode_problems(row, want)))


def latest(**overrides: object) -> dict:
    """`latest` del informe de disco de una organización (Fase 4 §8.5): las seis categorías y su total."""
    bytes_ = {"skills": 4000, "scripts": 1000, "schemas": 500, "knowledge": 12000, "versions": 2500, "index": 20480}
    data = {"taken_at": "2026-11-20T19:38:17.920Z", "files": 9, "bytes": bytes_, "total_bytes": sum(bytes_.values()),
            "items": []}  # fmt: skip
    data.update(overrides)
    return data


def disk_row(**disk: object) -> dict:
    block = {"avg": 19000, "max": 20000, "last": 20000, "index_last": 20480, "snapshots": 3,
             "first_taken_at": "2026-11-20T19:36:44.894Z", "taken_at": "2026-11-20T19:38:17.920Z"}  # fmt: skip
    block.update(disk)
    return org_row(storage_bytes=block["last"], storage_taken_at=block["taken_at"], disk=block,
                   content={**org_row()["content"], "skills": 1, "areas": 1})  # fmt: skip


class Step162Test(unittest.TestCase):
    def test_billable_is_the_total_minus_the_index(self) -> None:
        self.assertEqual(e2e_consumo.BILLABLE, ("skills", "scripts", "schemas", "knowledge", "versions"))
        self.assertEqual(e2e_consumo.billable_problems(disk_row(), latest()), [])

    def test_the_figures_of_the_same_snapshot_differ_exactly_in_the_index(self) -> None:
        problems = e2e_consumo.billable_problems(disk_row(last=20480, max=20480, avg=20480), latest())
        self.assertTrue(any("disk.last" in p for p in problems), problems)
        problems = e2e_consumo.billable_problems(disk_row(index_last=0), latest())
        self.assertTrue(any("index_last" in p for p in problems), problems)
        self.assertTrue(any("total_bytes" in p for p in problems), problems)

    def test_the_index_is_not_counted_in_storage_bytes(self) -> None:
        row = disk_row()
        row["storage_bytes"] = 20000 + 20480  # la Fase 4: sumaba el índice
        problems = e2e_consumo.billable_problems(row, latest())
        self.assertTrue(any("storage_bytes" in p for p in problems), problems)

    def test_average_and_last_never_exceed_the_maximum(self) -> None:
        problems = e2e_consumo.billable_problems(disk_row(avg=20001), latest())
        self.assertTrue(any("avg" in p and "max" in p for p in problems), problems)
        problems = e2e_consumo.billable_problems(disk_row(max=19999), latest())
        self.assertTrue(any("last" in p and "max" in p for p in problems), problems)

    def test_a_newer_snapshot_is_told_apart_from_a_wrong_figure(self) -> None:
        problems = e2e_consumo.billable_problems(disk_row(taken_at="2026-11-20T19:38:47.920Z"), latest())
        self.assertTrue(any(e2e_consumo.NEWER_SNAPSHOT in p for p in problems), problems)

    def test_at_least_one_snapshot_and_integers(self) -> None:
        self.assertTrue(e2e_consumo.billable_problems(disk_row(snapshots=0), latest()))
        self.assertTrue(e2e_consumo.billable_problems(disk_row(avg=19000.5), latest()))
        self.assertTrue(e2e_consumo.billable_problems(disk_row(avg=True), latest()))

    def test_published_skills_and_areas(self) -> None:
        self.assertEqual(e2e_consumo.published_problems(disk_row(), skills=1, areas=1), [])
        row = disk_row()
        row["content"]["skills_capped"] = True
        problems = e2e_consumo.published_problems(row, skills=1, areas=1)
        self.assertTrue(any("skills_capped" in p for p in problems), problems)
        row = disk_row()
        row["content"]["areas"] = 2
        self.assertTrue(e2e_consumo.published_problems(row, skills=1, areas=1))

    def test_growth_after_a_second_snapshot(self) -> None:
        grown = disk_row(avg=15000, max=20000, last=20000, snapshots=3)
        self.assertEqual(e2e_consumo.growth_problems(grown, first=10000), [])
        # sin la foto de antes de crecer, la media sería la última y el máximo no dejaría rastro de las otras
        flat = disk_row(avg=20000, max=20000, last=20000, snapshots=1)
        self.assertTrue(e2e_consumo.growth_problems(flat, first=10000))
        # el máximo es la última si solo crece
        self.assertTrue(e2e_consumo.growth_problems(disk_row(avg=15000, max=21000, last=20000), first=10000))


def csv_text(orgs: list[dict], *, bom: bool = True, extra: str = "") -> str:
    """El CSV del contrato §7.9 a partir del JSON: lo que el servidor debería devolver."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(e2e_consumo.PLATFORM_CSV_COLUMNS)
    for org in orgs:
        writer.writerow(e2e_consumo.csv_expected(org))
    return ("﻿" if bom else "") + out.getvalue() + extra


class Step163Test(unittest.TestCase):
    def orgs(self) -> list[dict]:
        formula = org_row(slug="consumo-abc-formula", name='=SUMA(1;1) "x", y', requests=2)
        plain = org_row(slug="demo", name="Demo Clínica", requests=0, anonymizer={**ZERO_ANON})
        return [org_row(), formula, plain]

    def test_the_columns_are_the_ones_of_the_contract(self) -> None:
        columns = e2e_consumo.PLATFORM_CSV_COLUMNS
        self.assertEqual(len(columns), 46)
        self.assertEqual(columns[:4], ("slug", "name", "timezone", "requests"))
        self.assertEqual(columns[24:32], ("disk_max_bytes", "disk_index_last_bytes", "disk_snapshots",
                                          "disk_first_taken_at", "anonymized_turns", "observation_turns",
                                          "bypass_turns", "replaced_in"))  # fmt: skip
        self.assertEqual(columns[-1], "l2_observed")
        for repeated in ("turns", "disk_last_bytes", "invoked"):
            self.assertNotIn(repeated, columns, "el informe no repite lo que ya está en el tráfico")
        self.assertEqual(len(set(columns)), 46)

    def test_a_cell_is_the_json_value(self) -> None:
        row = org_row()
        values = dict(zip(e2e_consumo.PLATFORM_CSV_COLUMNS, e2e_consumo.csv_expected(row), strict=True))
        self.assertEqual((values["slug"], values["requests"], values["anonymized_turns"]), (row["slug"], "6", "3"))
        self.assertEqual((values["disk_avg_bytes"], values["storage_taken_at"], values["skills"]), ("", "", ""))
        self.assertEqual((values["skills_capped"], values["areas_capped"]), ("false", "false"))
        self.assertEqual(values["invocations"], "0")

    def test_formulas_are_neutralized_with_an_apostrophe(self) -> None:
        for name in ("=1+1", "+1", "-1", "@SUM", "\tx", "\rx"):
            values = dict(zip(e2e_consumo.PLATFORM_CSV_COLUMNS,
                              e2e_consumo.csv_expected(org_row(name=name)), strict=True))  # fmt: skip
            self.assertEqual(values["name"], "'" + name, repr(name))
        values = dict(zip(e2e_consumo.PLATFORM_CSV_COLUMNS, e2e_consumo.csv_expected(org_row(name="A=B")), strict=True))
        self.assertEqual(values["name"], "A=B")

    def test_the_csv_of_the_json_has_no_problems(self) -> None:
        orgs = self.orgs()
        self.assertEqual(e2e_consumo.csv_problems(csv_text(orgs), orgs), [])

    def test_each_kind_of_difference_is_reported(self) -> None:
        orgs = self.orgs()
        cases = {
            "BOM": csv_text(orgs, bom=False),
            "totales": csv_text(orgs, extra="TOTAL," + "0," * 45 + "0\r\n"),
            "fila": csv_text(orgs[:2]),
            "orden": csv_text(list(reversed(orgs))),
            "lf": csv_text(orgs).replace("\r\n", "\n"),
        }
        for name, text in cases.items():
            self.assertTrue(e2e_consumo.csv_problems(text, orgs), name)
        wrong = copy.deepcopy(orgs)
        wrong[0]["requests"] = 99
        problems = e2e_consumo.csv_problems(csv_text(wrong), orgs)
        self.assertTrue(any("requests" in p and "99" in p for p in problems), problems)
        formula = copy.deepcopy(orgs)
        formula[1]["name"] = orgs[1]["name"]  # sin el apóstrofo: el CSV lleva la fórmula tal cual
        raw = csv_text(orgs).replace("'=SUMA", "=SUMA")
        problems = e2e_consumo.csv_problems(raw, orgs)
        self.assertTrue(any("name" in p for p in problems), problems)
        header = csv_text(orgs).replace("slug,name,", "name,slug,", 1)
        self.assertTrue(any("cabecera" in p for p in e2e_consumo.csv_problems(header, orgs)))

    def test_the_file_name_follows_the_period(self) -> None:
        self.assertEqual(
            e2e_consumo.csv_filename({"from": "2026-11-19", "to": "2026-11-20"}),
            "consumo-plataforma-2026-11-19-2026-11-20.csv",
        )
        disposition = 'attachment; filename="consumo-plataforma-2026-11-19-2026-11-20.csv"'
        self.assertEqual(
            e2e_consumo.disposition_problems(disposition, "consumo-plataforma-2026-11-19-2026-11-20.csv"), []
        )
        self.assertTrue(e2e_consumo.disposition_problems("inline", "x.csv"))


class Step164Test(unittest.TestCase):
    def test_a_forbidden_reply_carries_no_data(self) -> None:
        self.assertEqual(e2e_consumo.denied_problems(403, {"error": "FORBIDDEN", "message": "Forbidden"}, 403), [])
        problems = e2e_consumo.denied_problems(403, {"error": "FORBIDDEN", "orgs": []}, 403)
        self.assertTrue(any("datos" in p for p in problems), problems)
        self.assertTrue(e2e_consumo.denied_problems(200, {"orgs": []}, 403))
        self.assertTrue(e2e_consumo.denied_problems(404, {"error": "NOT_FOUND"}, 403))
        self.assertTrue(e2e_consumo.denied_problems(403, {"error": "NOT_FOUND"}, 403))
        self.assertEqual(e2e_consumo.denied_problems(401, {"error": "UNAUTHENTICATED"}, 401), [])

    def test_a_denied_reply_of_a_global_report_carries_no_series_either(self) -> None:
        problems = e2e_consumo.denied_problems(403, {"error": "FORBIDDEN", "series": [{"key": "demo"}]}, 403)
        self.assertTrue(any("series" in p for p in problems), problems)
        problems = e2e_consumo.denied_problems(403, {"error": "FORBIDDEN", "orgs": [], "totals": {}, "period": {}}, 403)
        self.assertTrue(any("orgs" in p and "totals" in p and "period" in p for p in problems), problems)

    def test_not_found_is_the_third_reply_the_matrix_knows(self) -> None:
        self.assertEqual(e2e_consumo.denied_problems(404, {"error": "NOT_FOUND", "message": "x"}, 404), [])
        self.assertTrue(e2e_consumo.denied_problems(403, {"error": "FORBIDDEN"}, 404))
        self.assertTrue(e2e_consumo.denied_problems(404, {"error": "FORBIDDEN"}, 404))
        self.assertTrue(e2e_consumo.denied_problems(404, {"error": "NOT_FOUND", "totals": {}}, 404))

    def test_the_five_platform_routes_are_the_report_and_the_four_global_ones(self) -> None:
        self.assertEqual(e2e_consumo.GLOBAL_KINDS, ("traffic", "content", "storage", "anonymizer"))
        self.assertEqual(
            e2e_consumo.platform_routes(),
            [
                "/api/v1/platform/usage",
                "/api/v1/platform/usage/traffic",
                "/api/v1/platform/usage/content",
                "/api/v1/platform/usage/storage",
                "/api/v1/platform/usage/anonymizer",
            ],
        )


def traffic_item(slug: str = "consumo-abc-a", **overrides: object) -> dict:
    """Un elemento de `series` de `GET /platform/usage/traffic?group_by=org` (contrato §7.4)."""
    item = {"key": slug, "label": "Org", "org": {"slug": slug, "name": "Org"}, "requests": 3, "blocked": 1, "errors": 2,
            "tokens_in": 36, "tokens_out": 27, "bytes_in": 900, "bytes_out": 500, "latency_p50_ms": 1000,
            "latency_p95_ms": 2000, "ttft_avg_ms": 800, "active_users": 2}  # fmt: skip
    item.update(overrides)
    return item


def own_totals(**overrides: object) -> dict:
    """`totals` del informe de tráfico de la propia organización (Fase 4 §8.3): lo mismo, sin `active_users`."""
    totals = {k: v for k, v in traffic_item().items() if k not in ("key", "label", "org", "active_users")}
    totals.update(overrides)
    return totals


def own_users(*requests: int) -> list[dict]:
    """`series` de `group_by=user` de la propia organización: una fila por persona con datos."""
    return [{"key": f"u{i}", "label": f"u{i}", "user": {"id": f"u{i}"}, "requests": n} for i, n in enumerate(requests)]


def traffic_report(group_by: str, series: list[dict], **totals: object) -> dict:
    base = {"orgs": len(series), **{k: sum(s.get(k) or 0 for s in series) for k in e2e_consumo.TRAFFIC_ADDED}}
    return {"group_by": group_by, "period": {"month": None, "from": "2026-11-19", "to": "2026-11-20", "open": True},
            "totals": {**base, **totals}, "series": series}  # fmt: skip


class Step165Test(unittest.TestCase):
    def test_the_figures_that_are_compared_one_by_one(self) -> None:
        self.assertEqual(
            e2e_consumo.TRAFFIC_COMPARED,
            ("requests", "blocked", "errors", "tokens_in", "tokens_out", "bytes_in", "bytes_out", "latency_p50_ms",
             "latency_p95_ms", "ttft_avg_ms"),
        )  # fmt: skip
        self.assertEqual(e2e_consumo.TRAFFIC_ADDED, (*e2e_consumo.TRAFFIC_COMPARED[:7], "active_users"))

    def test_the_global_row_is_the_report_of_the_organisation(self) -> None:
        self.assertEqual(e2e_consumo.traffic_row_problems("a", traffic_item(), own_totals(), own_users(2, 1)), [])

    def test_each_compared_figure_is_reported(self) -> None:
        for name in e2e_consumo.TRAFFIC_COMPARED:
            with self.subTest(name=name):
                row = traffic_item(**{name: (traffic_item()[name] or 0) + 1})
                problems = e2e_consumo.traffic_row_problems("a", row, own_totals(), own_users(2, 1))
                self.assertTrue(any(name in p for p in problems), problems)

    def test_percentiles_that_are_null_on_both_sides_agree(self) -> None:
        row = traffic_item(latency_p50_ms=None, latency_p95_ms=None, ttft_avg_ms=None)
        own = own_totals(latency_p50_ms=None, latency_p95_ms=None, ttft_avg_ms=None)
        self.assertEqual(e2e_consumo.traffic_row_problems("a", row, own, own_users(2, 1)), [])
        problems = e2e_consumo.traffic_row_problems("a", row, own_totals(), own_users(2, 1))
        self.assertTrue(any("latency_p50_ms" in p for p in problems), problems)

    def test_active_users_are_the_people_with_requests_in_the_report_of_the_organisation(self) -> None:
        problems = e2e_consumo.traffic_row_problems("a", traffic_item(), own_totals(), own_users(3))
        self.assertTrue(any("active_users" in p and "2" in p and "1" in p for p in problems), problems)
        # una persona sin peticiones no es activa
        self.assertEqual(e2e_consumo.traffic_row_problems("a", traffic_item(), own_totals(), own_users(2, 1, 0)), [])

    def test_an_organisation_missing_from_the_global_report_is_named(self) -> None:
        problems = e2e_consumo.traffic_row_problems("consumo-abc-a", None, own_totals(), own_users(2, 1))
        self.assertTrue(any("consumo-abc-a" in p and "no sale" in p for p in problems), problems)

    def test_a_type_mix_up_is_a_difference(self) -> None:
        problems = e2e_consumo.traffic_row_problems("a", traffic_item(requests=3.0), own_totals(), own_users(2, 1))
        self.assertTrue(any("requests" in p for p in problems), problems)
        problems = e2e_consumo.traffic_row_problems(
            "a", traffic_item(requests=True), own_totals(requests=1), own_users(2)
        )
        self.assertTrue(any("requests" in p for p in problems), problems)

    def test_totals_are_the_sum_of_the_series(self) -> None:
        series = [traffic_item("a"), traffic_item("b", requests=5, active_users=1)]
        self.assertEqual(e2e_consumo.traffic_sum_problems(traffic_report("org", series)), [])
        report = traffic_report("org", series)
        report["totals"]["requests"] += 1
        report["totals"]["orgs"] = 3
        report["totals"]["active_users"] = 9
        problems = " ".join(e2e_consumo.traffic_sum_problems(report))
        for fragment in ("requests", "orgs", "active_users"):
            self.assertIn(fragment, problems)

    def test_days_and_pairs_have_no_active_users_to_add(self) -> None:
        series = [{"key": "2026-11-19", **{k: 1 for k in e2e_consumo.TRAFFIC_ADDED[:7]}},
                  {"key": "2026-11-20", **{k: 2 for k in e2e_consumo.TRAFFIC_ADDED[:7]}}]  # fmt: skip
        report = {"group_by": "day", "totals": {"orgs": 4, **{k: 3 for k in e2e_consumo.TRAFFIC_ADDED[:7]}},
                  "series": series}  # fmt: skip
        self.assertEqual(e2e_consumo.traffic_sum_problems(report, orgs=4), [])
        report["totals"]["tokens_in"] = 4
        self.assertTrue(any("tokens_in" in p for p in e2e_consumo.traffic_sum_problems(report, orgs=4)))

    def test_the_series_is_every_day_of_the_range(self) -> None:
        days = {"series": [{"key": "2026-11-19"}, {"key": "2026-11-20"}]}
        self.assertEqual(e2e_consumo.days_problems(days, "2026-11-19", "2026-11-20"), [])
        self.assertTrue(e2e_consumo.days_problems({"series": [{"key": "2026-11-20"}]}, "2026-11-19", "2026-11-20"))
        self.assertTrue(e2e_consumo.days_problems(days, "2026-11-18", "2026-11-20"))

    def test_the_three_groupings_say_the_same_totals(self) -> None:
        by_org = traffic_report("org", [traffic_item("a")])
        by_day = {"totals": {k: by_org["totals"][k] for k in e2e_consumo.TRAFFIC_ADDED[:7]}}
        by_pair = {"totals": dict(by_day["totals"])}
        self.assertEqual(e2e_consumo.same_totals_problems({"org": by_org, "day": by_day, "org_day": by_pair}), [])
        by_pair["totals"]["requests"] += 1
        problems = e2e_consumo.same_totals_problems({"org": by_org, "day": by_day, "org_day": by_pair})
        self.assertTrue(any("org_day" in p and "requests" in p for p in problems), problems)

    def test_org_day_adds_up_to_each_organisation_it_names(self) -> None:
        pairs = {"top": ["a", "b"], "series": [
            {"key": "a:2026-11-19", "org": {"slug": "a"}, "requests": 1},
            {"key": "a:2026-11-20", "org": {"slug": "a"}, "requests": 2},
            {"key": "(otras):2026-11-20", "org": None, "requests": 9},
        ]}  # fmt: skip
        self.assertEqual(e2e_consumo.org_day_problems(pairs, {"a": 3, "b": 0}), [])
        problems = e2e_consumo.org_day_problems(pairs, {"a": 4})
        self.assertTrue(any("a" in p and "3" in p and "4" in p for p in problems), problems)
        # una organización fuera de las 10 mayores no se comprueba (va en «(otras)»)
        self.assertEqual(e2e_consumo.org_day_problems(pairs, {"z": 7}), [])
        too_many = {"top": [f"o{i}" for i in range(11)], "series": []}
        self.assertTrue(any("10" in p for p in e2e_consumo.org_day_problems(too_many, {})))

    def test_the_consumption_report_agrees_with_the_global_traffic(self) -> None:
        row = {"slug": "a", "requests": 3, "blocked_requests": 1, "errors": 2, "tokens_in": 36, "tokens_out": 27,
               "bytes_in": 900, "bytes_out": 500, "active_users": 2}  # fmt: skip
        self.assertEqual(e2e_consumo.platform_row_problems("a", row, traffic_item()), [])
        problems = e2e_consumo.platform_row_problems("a", {**row, "blocked_requests": 0}, traffic_item())
        self.assertTrue(any("blocked" in p for p in problems), problems)
        self.assertTrue(e2e_consumo.platform_row_problems("a", None, traffic_item()))


def anonymizer_block(**overrides: object) -> dict:
    """El bloque `M` de una organización (contrato §6.5): tres modos distintos y una cifra por columna."""
    block = {**ZERO_ANON, "turns": 6, "turns_by_mode": {"ANONYMIZED": 3, "OBSERVATION": 2, "BYPASS": 1},
             "tokens_in": 72, "tokens_out": 54, "bytes_in": 9000, "replaced_in": 9, "restored_out": 9,
             "verified_clean": 3, "verified_warning": 2}  # fmt: skip
    block.update(overrides)
    return block


def anonymizer_series(*days: dict) -> dict:
    """El informe de organización (`group_by=day`) cuyos días suman `totals`."""
    series = [{"key": f"2026-11-{19 + i}", "label": f"2026-11-{19 + i}", **block} for i, block in enumerate(days)]
    columns = [c for c in e2e_consumo.ANONYMIZER_COLUMNS]
    totals = {c: sum(b[c] for b in days) for c in columns}
    totals["turns_by_mode"] = {m: sum(b["turns_by_mode"][m] for b in days) for m in e2e_consumo.MODES}
    return {"group_by": "day", "totals": totals, "series": series}


class Step166Test(unittest.TestCase):
    def test_the_columns_are_the_anonymizer_block_of_the_contract(self) -> None:
        self.assertEqual(len(e2e_consumo.ANONYMIZER_COLUMNS), 19)
        self.assertEqual(set(e2e_consumo.ANONYMIZER_COLUMNS) | {"turns_by_mode"}, set(anonymizer_block()))
        self.assertEqual(len(e2e_consumo.L2_COLUMNS), 10)
        self.assertTrue(all(c.startswith("l2_") for c in e2e_consumo.L2_COLUMNS))

    def test_the_three_readings_of_one_organisation_agree(self) -> None:
        block = anonymizer_block()
        own = {**block}
        globl = {"key": "a", "label": "A", "org": {"slug": "a", "name": "A"}, **block}
        self.assertEqual(e2e_consumo.anonymizer_equal_problems("a", own, block, globl), [])

    def test_each_column_is_compared_in_the_three_readings(self) -> None:
        block = anonymizer_block()
        for column in e2e_consumo.ANONYMIZER_COLUMNS:
            for where in ("org", "platform", "global"):
                with self.subTest(column=column, where=where):
                    own, platform, globl = {**block}, {**block}, {"key": "a", **block}
                    {"org": own, "platform": platform, "global": globl}[where][column] += 1
                    problems = e2e_consumo.anonymizer_equal_problems("a", own, platform, globl)
                    self.assertTrue(any(column in p for p in problems), problems)

    def test_the_turns_by_mode_are_compared_by_mode(self) -> None:
        block = anonymizer_block()
        own = {**block, "turns_by_mode": {"ANONYMIZED": 2, "OBSERVATION": 3, "BYPASS": 1}}
        problems = e2e_consumo.anonymizer_equal_problems("a", own, block, {"key": "a", **block})
        self.assertTrue(any("turns_by_mode" in p and "ANONYMIZED" in p for p in problems), problems)

    def test_a_missing_reading_is_named(self) -> None:
        block = anonymizer_block()
        problems = e2e_consumo.anonymizer_equal_problems("a", block, block, None)
        self.assertTrue(any("plataforma" in p and "a" in p for p in problems), problems)

    def test_the_days_add_up_to_the_totals(self) -> None:
        zero = anonymizer_block(**{c: 0 for c in ("turns", "tokens_in", "tokens_out", "bytes_in", "replaced_in",
                                                  "restored_out", "verified_clean", "verified_warning")},
                                turns_by_mode={"ANONYMIZED": 0, "OBSERVATION": 0, "BYPASS": 0})  # fmt: skip
        report = anonymizer_series(zero, anonymizer_block())
        self.assertEqual(e2e_consumo.anonymizer_series_problems(report, days=["2026-11-19", "2026-11-20"]), [])
        report["totals"]["replaced_in"] += 1
        report["totals"]["turns_by_mode"]["BYPASS"] += 1
        problems = " ".join(e2e_consumo.anonymizer_series_problems(report, days=["2026-11-19", "2026-11-20"]))
        self.assertIn("replaced_in", problems)
        self.assertIn("BYPASS", problems)

    def test_every_day_of_the_range_is_there_with_zeros(self) -> None:
        report = anonymizer_series(anonymizer_block())
        problems = e2e_consumo.anonymizer_series_problems(report, days=["2026-11-19", "2026-11-20"])
        self.assertTrue(any("2026-11-20" in p for p in problems), problems)

    def test_hours_are_checked_by_count(self) -> None:
        zero = anonymizer_block(**{c: 0 for c in ("turns", "tokens_in", "tokens_out", "bytes_in", "replaced_in",
                                                  "restored_out", "verified_clean", "verified_warning")},
                                turns_by_mode={"ANONYMIZED": 0, "OBSERVATION": 0, "BYPASS": 0})  # fmt: skip
        hours = [{"key": f"2026-11-19T{h:02d}:00:00+00:00", **zero} for h in range(24)]
        hours[3] = {**hours[3], **anonymizer_block()}
        report = {"group_by": "hour", "series": hours,
                  "totals": anonymizer_series(anonymizer_block())["totals"]}  # fmt: skip
        self.assertEqual(e2e_consumo.anonymizer_series_problems(report, count=24), [])
        self.assertTrue(e2e_consumo.anonymizer_series_problems(report, count=48))


def audit_row(row_id: str, count: int = 1, **overrides: object) -> dict:
    meta = {"group_by": "user", "format": "json", "users": 0}
    row = {"id": row_id, "action": "usage.viewed", "org_slug": "consumo-abc-vista", "target_type": "usage_report",
           "target_id": "traffic", "count": count, "metadata": meta}  # fmt: skip
    row.update(overrides)
    return row


class Step167Test(unittest.TestCase):
    def test_the_snapshot_is_the_rows_with_their_counts(self) -> None:
        rows = [audit_row("a", 2), audit_row("b")]
        self.assertEqual(e2e_consumo.viewed_snapshot(rows), {"a": 2, "b": 1})

    def test_no_new_row_and_no_new_count_is_clean(self) -> None:
        before = {"a": 2, "b": 1}
        self.assertEqual(e2e_consumo.viewed_problems(before, dict(before)), [])

    def test_a_new_row_is_a_problem(self) -> None:
        problems = e2e_consumo.viewed_problems({"a": 2}, {"a": 2, "b": 1})
        self.assertTrue(any("usage.viewed" in p and "b" in p for p in problems), problems)

    def test_an_aggregated_row_that_grows_is_a_problem(self) -> None:
        problems = e2e_consumo.viewed_problems({"a": 2}, {"a": 3})
        self.assertTrue(any("a" in p and "2" in p and "3" in p for p in problems), problems)

    def test_a_row_that_disappears_is_not_ours_to_judge(self) -> None:
        self.assertEqual(e2e_consumo.viewed_problems({"a": 2, "b": 1}, {"a": 2}), [])

    def test_the_organisation_audit_must_be_empty(self) -> None:
        self.assertEqual(e2e_consumo.org_viewed_problems("consumo-abc-vista", []), [])
        problems = e2e_consumo.org_viewed_problems("consumo-abc-vista", [audit_row("a", target_id="anonymizer")])
        self.assertTrue(any("consumo-abc-vista" in p and "anonymizer" in p for p in problems), problems)

    def test_the_control_is_one_row_of_the_detail_by_person(self) -> None:
        self.assertEqual(e2e_consumo.control_problems("consumo-abc-vista", [audit_row("a")]), [])
        self.assertTrue(
            e2e_consumo.control_problems("consumo-abc-vista", []), "sin fila, la comprobación no probaría nada"
        )
        self.assertTrue(e2e_consumo.control_problems("consumo-abc-vista", [audit_row("a"), audit_row("b")]))
        wrong = audit_row("a", metadata={"group_by": "day"})
        self.assertTrue(e2e_consumo.control_problems("consumo-abc-vista", [wrong]))
        self.assertTrue(e2e_consumo.control_problems("consumo-abc-vista", [audit_row("a", target_id="content")]))


def l2_turn(status: str = "OBSERVED", **l2: object) -> dict:
    block = {"status": status, "enabled": True, "cause": None, "analyzed_bytes": 100, "cached_bytes": 0,
             "unanalyzed_bytes": 0, "observed": 3}  # fmt: skip
    block.update(l2)
    return {"status": "CLEAN", "request_replacements_by_type": {}, "response_replacements_by_type": {}, "l2": block}


def l2_sent() -> list[e2e_consumo.Sent]:
    """Dos turnos anonimizados y uno en observación con el NER, y uno en bypass (sin verificación)."""
    turns = [e2e_consumo.Sent("ANONYMIZED", 12, 9, "ANONYMIZED", l2_turn(analyzed_bytes=100, observed=3)),
             e2e_consumo.Sent("ANONYMIZED", 12, 9, "ANONYMIZED",
                              l2_turn("APPLIED", analyzed_bytes=60, cached_bytes=40)),
             e2e_consumo.Sent("OBSERVATION", 12, 9, "OBSERVATION", l2_turn(enabled=False, unanalyzed_bytes=7)),
             e2e_consumo.Sent("BYPASS", 12, 9, "BYPASS", None)]  # fmt: skip
    return turns


class Step168Test(unittest.TestCase):
    def test_the_ner_is_there_when_a_turn_carries_l2(self) -> None:
        self.assertTrue(e2e_consumo.has_l2(l2_sent()))
        self.assertFalse(e2e_consumo.has_l2(sent_turns()), "sin NER, verification no lleva l2")
        self.assertFalse(e2e_consumo.has_l2([e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", {"l2": None})]))
        self.assertFalse(e2e_consumo.has_l2([e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", {"l2": "x"})]))

    def test_the_columns_are_what_each_turn_says_of_its_l2(self) -> None:
        want = e2e_consumo.l2_expected(l2_sent())
        self.assertEqual(want, {
            "l2_turns": 3, "l2_enabled": 2, "l2_applied": 1, "l2_degraded": 0, "l2_observed_turns": 2,
            "l2_known_values_only": 0, "l2_analyzed_bytes": 260, "l2_cached_bytes": 40, "l2_unanalyzed_bytes": 7,
            "l2_observed": 9,
        })  # fmt: skip

    def test_only_a_boolean_true_enables_and_only_an_object_counts(self) -> None:
        odd = [e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", l2_turn(enabled="true", status="UNKNOWN")),
               e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", {"status": "CLEAN", "l2": None})]  # fmt: skip
        want = e2e_consumo.l2_expected(odd)
        self.assertEqual((want["l2_turns"], want["l2_enabled"]), (1, 0))
        self.assertEqual((want["l2_applied"], want["l2_degraded"], want["l2_observed_turns"]), (0, 0, 0))

    def test_negative_or_non_integer_bytes_count_zero(self) -> None:
        odd = l2_turn(analyzed_bytes=-5, cached_bytes=1.5, unanalyzed_bytes=True, observed="2")
        bad = [e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", odd)]
        want = e2e_consumo.l2_expected(bad)
        self.assertEqual([want[c] for c in ("l2_analyzed_bytes", "l2_cached_bytes", "l2_unanalyzed_bytes",
                                            "l2_observed")], [0, 0, 0, 0])  # fmt: skip

    def test_a_filled_block_matches(self) -> None:
        want = e2e_consumo.l2_expected(l2_sent())
        self.assertEqual(e2e_consumo.l2_problems(anonymizer_block(**want), want), [])

    def test_a_block_left_at_zero_is_named(self) -> None:
        want = e2e_consumo.l2_expected(l2_sent())
        problems = e2e_consumo.l2_problems(anonymizer_block(), want)
        self.assertTrue(any("l2_turns" in p for p in problems), problems)
        self.assertTrue(any("se llenan" in p for p in problems), problems)

    def test_each_column_is_compared_exactly(self) -> None:
        want = e2e_consumo.l2_expected(l2_sent())
        for column in e2e_consumo.L2_COLUMNS:
            with self.subTest(column=column):
                problems = e2e_consumo.l2_problems(anonymizer_block(**{**want, column: want[column] + 1}), want)
                self.assertTrue(any(column in p for p in problems), problems)

    def test_observing_or_applying_must_show(self) -> None:
        degraded = [e2e_consumo.Sent("ANONYMIZED", 1, 1, "ANONYMIZED", l2_turn("DEGRADED", cause="TIMEOUT"))]
        want = e2e_consumo.l2_expected(degraded)
        problems = e2e_consumo.l2_problems(anonymizer_block(**want), want)
        self.assertTrue(any("l2_observed_turns" in p and "l2_applied" in p for p in problems), problems)

    def test_the_skip_says_why(self) -> None:
        text = e2e_consumo.no_ner_message()
        for fragment in ("SALTADO", "NER", "verification.l2"):
            self.assertIn(fragment, text)
        for fragment in ("--ner", "BAAS_E2E_NER"):
            self.assertIn(fragment, text, "dice cómo se declara el NER para que el paso no se salte")

    def test_the_failure_with_the_ner_declared_says_what_it_can_be(self) -> None:
        text = e2e_consumo.ner_declared_message()
        for fragment in ("--ner", "BAAS_E2E_NER", "verification.l2", "NER", "regresión", "l2_*"):
            self.assertIn(fragment, text)
        self.assertNotIn("SALTADO", text, "un fallo no se presenta como un salto")

    def test_the_ner_is_declared_by_flag_or_by_environment_and_never_by_default(self) -> None:
        for env in ({}, {"BAAS_E2E_NER": ""}, {"BAAS_E2E_NER": "0"}, {"BAAS_E2E_NER": "no"}):
            with mock.patch.dict(os.environ, env, clear=False):
                if "BAAS_E2E_NER" not in env:
                    os.environ.pop("BAAS_E2E_NER", None)
                self.assertFalse(e2e_h4.parse_args(["--only", "consumo"]).ner, env)
        self.assertTrue(e2e_h4.parse_args(["--only", "consumo", "--ner"]).ner)
        for value in ("1", "true", "TRUE", "yes"):
            with mock.patch.dict(os.environ, {"BAAS_E2E_NER": value}):
                self.assertTrue(e2e_h4.parse_args(["--only", "consumo"]).ner, value)

    def test_the_help_of_the_group_explains_the_ner_option(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            e2e_h4.parse_args(["--help"])
        text = " ".join(out.getvalue().split())
        for fragment in ("--ner", "BAAS_E2E_NER", "16.8", "verification.l2"):
            self.assertIn(fragment, text)


class GroupTest(unittest.TestCase):
    def register(self) -> list[tuple[str, tuple[str, ...]]]:
        ns = e2e_h4.parse_args(["--only", "consumo", "--inspect", "none"])
        steps: list[tuple[str, tuple[str, ...]]] = []

        class Recorder:
            def step(self, name: str, fn: object, *, needs: object = ()) -> None:
                steps.append((name, tuple(needs)))  # type: ignore[call-overload]

        e2e_h4.E2EH4(ns, inspector=None).run(Recorder())  # type: ignore[arg-type]
        return [(n, needs) for n, needs in steps if n.startswith("16.")]

    def test_the_steps_are_registered_in_order_without_an_inspector(self) -> None:
        steps = self.register()
        self.assertEqual(
            [n.split(" ")[0] for n, _ in steps],
            ["16.0", "16.1", "16.2", "16.3", "16.4", "16.5", "16.6", "16.7", "16.8"],
        )

    def test_every_step_needs_16_0_so_a_stack_without_the_report_skips_them(self) -> None:
        steps = self.register()
        ready = steps[0][0]
        for name, needs in steps[1:]:
            self.assertIn(ready, needs, name)
        self.assertNotIn(ready, steps[0][1])

    def test_the_group_logs_in_the_people_it_uses(self) -> None:
        ns = e2e_h4.parse_args(["--only", "consumo", "--inspect", "none"])
        group = e2e_consumo.ConsumoGroup(e2e_h4.E2EH4(ns, inspector=None))
        self.assertEqual(group.uso.slug_prefix, "consumo", "sus organizaciones se llaman consumo-<ejecución>-…")
        logins = group.logins()
        for email in (ns.admin_email, ns.orgadmin_email, ns.bypass_email, ns.anon_email):
            self.assertIn(email, logins)


if __name__ == "__main__":
    unittest.main()
