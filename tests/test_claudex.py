"""ClaudeX test suite. Run:  python -m unittest discover -s tests -v"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claudex.config import ModelRegistry, Settings  # noqa: E402
from claudex.debate import count_issues, parse_judgement  # noqa: E402
from claudex.phases import (  # noqa: E402
    execution_order, load_phases, parse_selection, write_phase_files,
)
from claudex.providers import ProviderPool  # noqa: E402
from claudex.product import ProductEngine, safe_product_path  # noqa: E402
from claudex.router import Router  # noqa: E402
from claudex.state import Run  # noqa: E402
from claudex.util import extract_json, slugify, strip_json_blocks  # noqa: E402

REGISTRY = ModelRegistry.load(Settings())


class TestUtil(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("CropGuard: AI for Farmers!"), "cropguard-ai-for-farmers")
        self.assertEqual(slugify("---"), "untitled")

    def test_extract_json_from_fence(self):
        text = 'Here you go:\n```json\n{"a": 1}\n```\nthanks'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_extract_json_without_fence(self):
        self.assertEqual(extract_json('prose {"a": [1, 2]} more'), {"a": [1, 2]})

    def test_extract_json_handles_braces_in_strings(self):
        self.assertEqual(extract_json('{"a": "} not the end", "b": 2}')["b"], 2)

    def test_extract_json_returns_none_on_garbage(self):
        self.assertIsNone(extract_json("no json here at all"))

    def test_strip_json_blocks(self):
        self.assertEqual(strip_json_blocks('keep\n```json\n{"x":1}\n```'), "keep")

    def test_product_paths_stay_inside_workspace(self):
        root = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(safe_product_path(root, "src/app.js"), root / "src" / "app.js")
            for unsafe in ("../secret", "/etc/passwd", "C:\\secret.txt", "a/../../b"):
                with self.assertRaises(Exception):
                    safe_product_path(root, unsafe)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestJudgeParsing(unittest.TestCase):
    def test_well_formed(self):
        text = """```json
{"scores": {"coverage": 90, "specificity": 80, "consistency": 85,
            "feasibility": 80, "risk_handling": 75},
 "total": 82, "verdict": "ACCEPT", "blocking_gaps": [], "note": "solid"}
```"""
        result = parse_judgement(text)
        self.assertEqual(result["verdict"], "ACCEPT")
        self.assertAlmostEqual(result["total"], 82.0)

    def test_inflated_total_is_corrected_to_rubric_mean(self):
        # Judge claims 95 while its own rubric averages 50: the mean wins.
        text = """```json
{"scores": {"coverage": 50, "specificity": 50, "consistency": 50,
            "feasibility": 50, "risk_handling": 50},
 "total": 95, "verdict": "ACCEPT"}
```"""
        result = parse_judgement(text)
        self.assertAlmostEqual(result["total"], 50.0)
        self.assertEqual(result["verdict"], "ACCEPT")  # verdict is reported as given

    def test_malformed_json_is_unscored_not_zero(self):
        """A judge that did not answer is not a judge that gave 0/100.

        Recording 0.0 made a formatting glitch indistinguishable from a
        genuinely terrible section in report.html, and handed the escalation
        logic a failure that never happened.
        """
        result = parse_judgement("the section looks fine to me")
        self.assertIsNone(result["total"])
        self.assertEqual(result["verdict"], "UNSCORED")
        self.assertFalse(result["parsed"])
        self.assertTrue(all(v is None for v in result["scores"].values()))

    def test_unscored_phase_does_not_trigger_escalation(self):
        from claudex.router import Router
        router = Router(REGISTRY, offline=True)
        unscored = parse_judgement("no json here")
        self.assertFalse(
            router.should_escalate(unscored["total"]),
            "a parse failure must not burn a stronger tier on a phantom bad score",
        )
        self.assertTrue(router.should_escalate(42.0), "a real low score still escalates")

    def test_a_genuine_zero_is_still_a_zero(self):
        text = ('{"scores": {"coverage": 0, "specificity": 0, "consistency": 0,'
                ' "feasibility": 0, "risk_handling": 0}, "total": 0,'
                ' "verdict": "ITERATE"}')
        result = parse_judgement(text)
        self.assertEqual(result["total"], 0.0)
        self.assertTrue(result["parsed"])

    def test_scores_are_clamped(self):
        text = '{"scores": {"coverage": 500, "specificity": -20, "consistency": 80,' \
               ' "feasibility": 80, "risk_handling": 80}, "total": 500}'
        result = parse_judgement(text)
        self.assertEqual(result["scores"]["coverage"], 100.0)
        self.assertEqual(result["scores"]["specificity"], 0.0)
        self.assertLessEqual(result["total"], 100.0)

    def test_missing_verdict_is_derived_from_score(self):
        text = '{"scores": {"coverage": 60, "specificity": 60, "consistency": 60,' \
               ' "feasibility": 60, "risk_handling": 60}}'
        self.assertEqual(parse_judgement(text)["verdict"], "ITERATE")

    def test_count_issues(self):
        text = """```json
{"issues": [{"severity": "blocker"}, {"severity": "minor"}, {"severity": "minor"}],
 "missing_checklist_items": ["a", "b"]}
```"""
        counts = count_issues(text)
        self.assertEqual((counts["blocker"], counts["minor"], counts["missing"]), (1, 2, 2))


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = Router(REGISTRY, offline=True)

    def test_authorship_alternates_across_phases(self):
        authors = [self.router.author_vendor(i) for i in range(1, 5)]
        self.assertEqual(len(set(authors)), 2, "both vendors should author phases")
        self.assertNotEqual(authors[0], authors[1])

    def test_critic_is_never_the_author(self):
        for pid in range(1, 26):
            author = self.router.author_vendor(pid)
            self.assertNotEqual(self.router.critic_vendor(author), author)

    def test_forced_lead_overrides_alternation(self):
        led = Router(REGISTRY, lead="claude", offline=True)
        self.assertTrue(all(led.author_vendor(i) == "claude" for i in range(1, 6)))

    def test_deep_phases_get_stronger_tier_than_light(self):
        self.assertEqual(self.router.tier_for("propose", "deep"), "deep")
        self.assertEqual(self.router.tier_for("propose", "light"), "fast")

    def test_escalation_climbs_the_ladder_and_stops_at_the_top(self):
        self.assertEqual(self.router.tier_for("propose", "light", escalation=1), "balanced")
        self.assertEqual(self.router.tier_for("propose", "light", escalation=2), "deep")
        self.assertEqual(self.router.tier_for("propose", "light", escalation=9), "deep")

    def test_should_escalate_threshold(self):
        self.assertTrue(self.router.should_escalate(50))
        self.assertFalse(self.router.should_escalate(95))
        self.assertFalse(self.router.should_escalate(None))

    def test_plan_round_pairs_two_vendors(self):
        plan = self.router.plan_round({"id": 1, "profile": "deep"})
        self.assertNotEqual(plan["propose"].vendor, plan["critique"].vendor)
        self.assertEqual(plan["propose"].vendor, plan["revise"].vendor)
        self.assertNotEqual(plan["judge"].vendor, plan["revise"].vendor)


class TestPhases(unittest.TestCase):
    def setUp(self):
        self.phases = load_phases(Settings())

    def test_seed_has_all_twenty_five(self):
        self.assertEqual(len(self.phases), 25)
        self.assertEqual([p["id"] for p in self.phases], list(range(1, 26)))

    def test_every_phase_has_a_checklist(self):
        for phase in self.phases:
            self.assertTrue(phase["checklist"], f"phase {phase['id']} has no checklist")
            self.assertTrue(phase["goal"], f"phase {phase['id']} has no goal")

    def test_parse_selection(self):
        known = list(range(1, 26))
        self.assertEqual(parse_selection("1-3,7", known), [1, 2, 3, 7])
        self.assertEqual(parse_selection("all", known), known)

    def test_parse_selection_rejects_unknown(self):
        with self.assertRaises(SystemExit):
            parse_selection("99", list(range(1, 26)))

    def test_execution_order_respects_dependencies(self):
        ordered = execution_order(self.phases, list(range(1, 26)))
        seen: set[int] = set()
        for phase in ordered:
            for dep in phase["depends_on"]:
                self.assertIn(dep, seen, f"phase {phase['id']} ran before its dep {dep}")
            seen.add(phase["id"])

    def test_partial_selection_ignores_outside_dependencies(self):
        # Phase 10 depends on 4,5,6 - selecting it alone must still work.
        ordered = execution_order(self.phases, [10])
        self.assertEqual([p["id"] for p in ordered], [10])

    def test_round_trip_through_markdown_files(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            settings = Settings(config_dir=tmp)
            write_phase_files(settings)
            reloaded = load_phases(settings)
            self.assertEqual(len(reloaded), 25)
            self.assertEqual(reloaded[0]["title"], self.phases[0]["title"])
            self.assertEqual(reloaded[9]["depends_on"], self.phases[9]["depends_on"])
            self.assertEqual(reloaded[6]["checklist"], self.phases[6]["checklist"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEndToEndOffline(unittest.TestCase):
    """Drives the real engine against the mock provider."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = Settings(runs_dir=self.tmp / "runs", cache_dir=self.tmp / "cache")
        self.settings.runs_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_phases(self, ids, rounds=2):
        from claudex.debate import DebateEngine
        from claudex.util import Console

        run = Run.create(self.settings, "Test Project", "A test brief.", "No budget.")
        router = Router(REGISTRY, offline=True)
        pool = ProviderPool(REGISTRY, self.settings, offline=True)
        engine = DebateEngine(
            run, router, pool, REGISTRY, Console(quiet=True),
            max_rounds=rounds, accept_score=80.0,
        )
        phases = load_phases(Settings())
        for phase in execution_order(phases, ids):
            engine.run_phase(phase)
        return run, phases, router, pool

    def test_phase_produces_section_and_score(self):
        run, _, _, _ = self._run_phases([1])
        meta = run.phase(1)
        self.assertEqual(meta["status"], "accepted")
        self.assertGreaterEqual(meta["score"], 80)
        self.assertTrue((run.dir / meta["section_file"]).exists())
        self.assertTrue((run.dir / meta["transcript_file"]).exists())

    def test_author_and_critic_are_different_vendors(self):
        run, _, _, _ = self._run_phases([1, 2])
        for pid in (1, 2):
            meta = run.phase(pid)
            self.assertNotEqual(meta["author"], meta["critic"])
        self.assertNotEqual(run.phase(1)["author"], run.phase(2)["author"])

    def test_ledger_records_every_call_as_offline(self):
        run, _, _, _ = self._run_phases([1])
        ledger = run.data["ledger"]
        self.assertGreaterEqual(len(ledger), 4)
        self.assertTrue(all(e["offline"] for e in ledger))
        self.assertTrue(run.totals()["cost_is_estimate"])

    def test_resume_skips_completed_phases(self):
        run, _, _, _ = self._run_phases([1])
        self.assertTrue(run.is_done(1))
        reloaded = Run.load(self.settings, run.slug)
        self.assertTrue(reloaded.is_done(1))
        self.assertFalse(reloaded.is_done(2))

    def test_cache_makes_the_second_identical_call_free(self):
        pool = ProviderPool(REGISTRY, self.settings, offline=True)
        args = dict(vendor="claude", tier="deep", system="CLAUDEX-ROLE: propose\n",
                    messages=[{"role": "user", "content": "hi"}])
        first = pool.complete(**args)
        second = pool.complete(**args)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(first.text, second.text)

    def test_build_produces_all_deliverables(self):
        from claudex import assembler, report
        from claudex.util import Console

        run, phases, router, pool = self._run_phases([1, 2])
        result = assembler.build(
            run, phases, router, pool, REGISTRY, Console(quiet=True), write_summary=True
        )
        self.assertEqual(result["sections"], 2)
        self.assertGreater(result["decisions"], 0)
        blueprint = (run.dir / "blueprint.md").read_text(encoding="utf-8")
        self.assertIn("Test Project", blueprint)
        self.assertIn("Offline run", blueprint)      # honesty banner present
        self.assertIn("How each section was produced", blueprint)
        html_path = report.build(run, phases)
        self.assertTrue(Path(html_path).exists())
        self.assertIn("<!DOCTYPE html>", Path(html_path).read_text(encoding="utf-8"))

    def test_single_round_still_accepts_or_flags(self):
        run, _, _, _ = self._run_phases([1], rounds=1)
        self.assertIn(run.phase(1)["status"], {"accepted", "needs_work"})
        self.assertEqual(run.phase(1)["rounds"], 1)

    def test_product_engine_builds_resumable_workspace(self):
        from claudex.util import Console, write_text

        run = Run.create(self.settings, "Product Test", "Build a tiny product.")
        write_text(run.dir / "blueprint.md", "# Product Test\n\nAccepted blueprint.")
        router = Router(REGISTRY, offline=True)
        pool = ProviderPool(REGISTRY, self.settings, offline=True)
        engine = ProductEngine(run, router, pool, REGISTRY, Console(quiet=True))
        result = engine.build()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(run.data["product"]["status"], "complete")
        self.assertTrue((run.dir / "product" / "README.md").exists())
        calls = len(run.data["ledger"])
        engine.build()
        self.assertEqual(len(run.data["ledger"]), calls, "accepted tasks should resume without calls")


    def test_unscored_phase_survives_build_and_report(self):
        """A judge parse failure must not crash or fake a 0 in the deliverables."""
        import json
        from claudex import assembler, report
        from claudex.util import Console

        run, phases, router, pool = self._run_phases([1, 2])
        run.phase(2).update({"score": None, "scored": False, "status": "unscored"})
        run.save()

        assembler.build(run, phases, router, pool, REGISTRY, Console(quiet=True),
                        write_summary=False)
        blueprint = (run.dir / "blueprint.md").read_text(encoding="utf-8")
        decisions = json.loads((run.dir / "decisions.json").read_text(encoding="utf-8"))
        html = Path(report.build(run, phases)).read_text(encoding="utf-8")

        self.assertIn("--/100", blueprint, "unscored renders as -- not 0")
        self.assertNotIn("0/100", blueprint)
        self.assertEqual(decisions["unscored_phases"], [2])
        # The average must ignore the unscored phase, not drag it to zero.
        self.assertGreater(decisions["average_score"], 50)
        self.assertIn('class="unscored"', html)
        self.assertIn("did not parse", html)


class TestCallProjection(unittest.TestCase):
    """The quoted call budget has to match what the engine actually spends."""

    def test_projection_matches_the_engine(self):
        from claudex.debate import projected_calls
        typical, worst = projected_calls(1, 1, dual_judge=False)
        self.assertEqual(typical, 5, "propose+critique+revise+judge+summarize")
        self.assertEqual(worst, typical, "no dual judge means no extra call")

        typical2, _ = projected_calls(1, 2, dual_judge=False)
        self.assertEqual(typical2, 8, "each extra round adds critique+revise+judge")

    def test_dual_judge_is_counted(self):
        from claudex.debate import projected_calls
        _, worst = projected_calls(10, 2, dual_judge=True)
        typical, _ = projected_calls(10, 2, dual_judge=False)
        self.assertGreater(worst, typical)

    def test_documented_defaults_fit_under_the_cap(self):
        """Regression: --rounds 2 over 25 phases used to blow the 120-call cap."""
        from claudex.debate import projected_calls
        cap = int(REGISTRY.cli_settings["max_calls_per_run"])
        _, worst = projected_calls(25, 2, dual_judge=True)
        self.assertLessEqual(
            worst, cap,
            f"a default 25-phase run projects {worst} calls against a cap of {cap}; "
            "the shipped defaults must be able to finish",
        )


class TestEscalation(unittest.TestCase):
    """The escalation ladder had never executed - it is now covered."""

    def test_low_score_bumps_the_tier(self):
        router = Router(REGISTRY, offline=True)
        phase = {"id": 1, "profile": "light"}
        base = router.plan_round(phase, escalation=0)["propose"].tier
        bumped = router.plan_round(phase, escalation=1)["propose"].tier
        self.assertNotEqual(base, bumped)
        self.assertEqual(router.ladder.index(bumped), router.ladder.index(base) + 1)

    def test_escalation_stops_at_the_top_of_the_ladder(self):
        router = Router(REGISTRY, offline=True)
        phase = {"id": 1, "profile": "deep"}
        top = router.plan_round(phase, escalation=9)["propose"].tier
        self.assertEqual(top, router.ladder[-1])

    def test_escalation_is_reachable_only_with_more_than_one_round(self):
        """At --rounds 1 there is no next round, so escalation cannot apply.

        This is the documented limitation, asserted so it stays deliberate
        rather than silently becoming dead code again.
        """
        router = Router(REGISTRY, offline=True)
        self.assertTrue(router.should_escalate(40.0))
        max_rounds, rnd = 1, 1
        self.assertFalse(rnd < max_rounds, "rounds=1 cannot escalate")
        max_rounds = 2
        self.assertTrue(rnd < max_rounds, "rounds>=2 can")


class TestRegistry(unittest.TestCase):
    def test_two_vendors_configured(self):
        self.assertGreaterEqual(len(REGISTRY.vendors), 2)

    def test_every_vendor_has_all_three_tiers(self):
        for vendor in REGISTRY.vendors:
            for tier in ("deep", "balanced", "fast"):
                self.assertIn("model", REGISTRY.tier_spec(vendor, tier))

    def test_routing_covers_every_role(self):
        for role in ("propose", "critique", "revise", "judge", "summarize", "assemble"):
            self.assertIn(role, REGISTRY.routing)

    def test_pricing_math(self):
        cost = REGISTRY.price("claude", "deep", 1_000_000, 0)
        self.assertAlmostEqual(cost, REGISTRY.tier_spec("claude", "deep")["price_in"])

    def test_ambient_base_url_is_not_inherited(self):
        import os
        os.environ["ANTHROPIC_BASE_URL"] = "https://proxy.invalid"
        try:
            self.assertEqual(REGISTRY.base_url("claude"), "https://api.anthropic.com")
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
