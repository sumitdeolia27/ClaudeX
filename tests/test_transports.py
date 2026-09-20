"""Tests for the two transports (api / cli) and the project profiler."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claudex import profiler  # noqa: E402
from claudex.config import ModelRegistry, Settings  # noqa: E402
from claudex.phases import execution_order, load_phases  # noqa: E402
from claudex.providers import ProviderPool, QuotaExceeded  # noqa: E402
from claudex.providers.base import ProviderError  # noqa: E402
from claudex.util import estimate_tokens  # noqa: E402

SETTINGS = Settings()
PHASES = load_phases(SETTINGS)


class TestTransportRegistry(unittest.TestCase):
    def test_api_transport_selects_http_providers(self):
        r = ModelRegistry.load(SETTINGS, transport="api")
        self.assertEqual(r.provider_kind("claude"), "anthropic")
        self.assertEqual(r.provider_kind("gpt"), "openai")

    def test_cli_transport_selects_cli_provider(self):
        r = ModelRegistry.load(SETTINGS, transport="cli")
        self.assertEqual(r.provider_kind("claude"), "cli")
        self.assertEqual(r.provider_kind("gpt"), "cli")

    def test_cli_transport_can_use_a_different_model_name(self):
        r = ModelRegistry.load(SETTINGS, transport="cli")
        api = ModelRegistry.load(SETTINGS, transport="api")
        self.assertNotEqual(r.model_id("gpt", "deep"), api.model_id("gpt", "deep"))
        # Claude has no cli_model override, so it falls back to the API name.
        self.assertEqual(r.model_id("claude", "deep"), api.model_id("claude", "deep"))

    def test_cli_calls_are_free(self):
        cli = ModelRegistry.load(SETTINGS, transport="cli")
        api = ModelRegistry.load(SETTINGS, transport="api")
        self.assertEqual(cli.price("claude", "deep", 1_000_000, 1_000_000), 0.0)
        self.assertGreater(api.price("claude", "deep", 1_000_000, 1_000_000), 0.0)

    def test_unknown_transport_is_rejected(self):
        r = ModelRegistry.load(SETTINGS, transport="carrier-pigeon")
        with self.assertRaises(SystemExit):
            r.provider_kind("claude")

    def test_cli_command_is_configured_for_both_vendors(self):
        r = ModelRegistry.load(SETTINGS, transport="cli")
        self.assertEqual(r.cli_command("claude")[0], "claude")
        self.assertEqual(r.cli_command("gpt")[0], "codex")


class TestTransportResolution(unittest.TestCase):
    """resolve_transport picks api > cli > offline, and honours a forced choice."""

    def setUp(self):
        from claudex.util import Console
        self.console = Console(quiet=True)
        self.registry = ModelRegistry.load(SETTINGS)

    def _resolve(self, requested, keys, clis):
        from claudex.cli import resolve_transport
        with mock.patch.object(
            ModelRegistry, "live_vendors",
            lambda self, t=None: (keys if (t or self.transport) == "api" else clis),
        ):
            return resolve_transport(requested, self.registry, self.console)

    def test_auto_prefers_api_when_keys_exist(self):
        self.assertEqual(self._resolve("auto", ["claude"], ["claude"]), ("api", False))

    def test_auto_falls_back_to_cli_without_keys(self):
        self.assertEqual(self._resolve("auto", [], ["claude"]), ("cli", False))

    def test_auto_falls_back_to_offline_with_neither(self):
        self.assertEqual(self._resolve("auto", [], []), ("api", True))

    def test_forced_cli_without_clis_goes_offline(self):
        self.assertEqual(self._resolve("cli", ["claude"], []), ("cli", True))

    def test_forced_api_without_keys_goes_offline(self):
        self.assertEqual(self._resolve("api", [], ["claude"]), ("api", True))


class TestCLIProvider(unittest.TestCase):
    def setUp(self):
        self.registry = ModelRegistry.load(SETTINGS, transport="cli")
        # The CLI agents READ their working directory, so it has to be set.
        self.project_dir = Path(tempfile.mkdtemp())
        self.registry.set_cli_cwd(self.project_dir)

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _provider(self):
        from claudex.providers.cli_api import CLIProvider
        with mock.patch("shutil.which", return_value="/usr/bin/claude"):
            return CLIProvider("claude", self.registry)

    def test_windows_shim_is_resolved_to_a_full_path(self):
        # subprocess cannot launch a bare `claude.cmd` on Windows by name.
        from claudex.providers.cli_api import CLIProvider
        shim = "C:\\npm\\claude.cmd"
        with mock.patch("shutil.which", return_value=shim):
            provider = CLIProvider("claude", self.registry)
        self.assertEqual(provider.command[0], shim)
        self.assertEqual(provider.exe_name, "claude", "errors show the friendly name")

    def test_output_file_is_preferred_over_stdout_transcript(self):
        # `codex exec` prints an agent transcript to stdout; the real answer is
        # the file it writes via -o.
        from claudex.providers.cli_api import CLIProvider
        with mock.patch("shutil.which", return_value="/usr/bin/codex"):
            provider = CLIProvider("gpt", self.registry)
        self.assertEqual(provider.output_file_flag, "-o")

        seen = {}

        def fake_run(argv, **kwargs):
            path = argv[argv.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("FINAL ANSWER")
            seen["argv"] = argv
            return mock.Mock(returncode=0, stdout="noisy agent log", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            out = provider.complete("s", [{"role": "user", "content": "q"}], "fast")
        self.assertEqual(out.text, "FINAL ANSWER")
        self.assertIn("--skip-git-repo-check", seen["argv"])
        self.assertIn("read-only", seen["argv"], "codex must not be able to edit files")

    def test_agent_runs_in_the_project_dir_not_the_claudex_repo(self):
        """Regression: cwd used to fall back to os.getcwd(), i.e. ClaudeX itself.

        `claude -p` and `codex exec` read their working directory, so that
        fallback made every blueprint cite ClaudeX's own source tree instead of
        the project being planned.
        """
        provider = self._provider()
        seen = {}

        def fake_run(argv, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return mock.Mock(returncode=0, stdout="answer text", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            provider.complete("s", [{"role": "user", "content": "q"}], "fast")

        self.assertEqual(Path(seen["cwd"]), self.project_dir.resolve())
        self.assertNotEqual(
            Path(seen["cwd"]), Path(os.getcwd()).resolve(),
            "the agent must not silently read whatever directory claudex was run from",
        )

    def test_unset_project_dir_is_refused_rather_than_guessed(self):
        provider = self._provider()
        self.registry.set_cli_cwd(None)
        with mock.patch("subprocess.run") as runner:
            with self.assertRaises(ProviderError) as ctx:
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")
        runner.assert_not_called()
        self.assertIn("project directory", str(ctx.exception).lower())

    def test_output_tokens_are_estimated_from_the_answer_not_the_transcript(self):
        """Regression: tokens_out measured stdout, but with -o the answer is a file."""
        from claudex.providers.cli_api import CLIProvider
        with mock.patch("shutil.which", return_value="/usr/bin/codex"):
            provider = CLIProvider("gpt", self.registry)

        answer = "REAL ANSWER " * 50
        transcript = "noisy agent reasoning log " * 500

        def fake_run(argv, **kwargs):
            with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as handle:
                handle.write(answer)
            return mock.Mock(returncode=0, stdout=transcript, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            out = provider.complete("s", [{"role": "user", "content": "q"}], "fast")

        # the provider strips the file it reads, so compare like for like
        self.assertEqual(out.tokens_out, estimate_tokens(answer.strip()))
        self.assertLess(
            out.tokens_out, estimate_tokens(transcript),
            "the ledger must not bill the agent transcript as output",
        )

    def test_missing_executable_is_a_clear_error(self):
        from claudex.providers.cli_api import CLIProvider
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ProviderError) as ctx:
                CLIProvider("claude", self.registry)
        self.assertIn("not on PATH", str(ctx.exception))

    def test_prompt_is_flattened_to_a_single_string(self):
        provider = self._provider()
        text = provider._flatten("SYS", [{"role": "user", "content": "hello"}])
        self.assertIn("SYS", text)
        self.assertIn("hello", text)

    def test_api_keys_are_stripped_so_the_subscription_is_used(self):
        provider = self._provider()
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertNotIn("ANTHROPIC_API_KEY", provider._env())

    def test_successful_call_returns_stdout(self):
        provider = self._provider()
        fake = mock.Mock(returncode=0, stdout="the answer", stderr="")
        with mock.patch("subprocess.run", return_value=fake):
            out = provider.complete("sys", [{"role": "user", "content": "q"}], "fast")
        self.assertEqual(out.text, "the answer")
        self.assertEqual(out.raw["transport"], "cli")
        self.assertGreater(out.tokens_out, 0)

    def test_auth_failure_tells_the_user_how_to_fix_it(self):
        provider = self._provider()
        fake = mock.Mock(
            returncode=1, stdout="",
            stderr="Failed to authenticate: OAuth session expired",
        )
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(ProviderError) as ctx:
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")
        self.assertIn("claude login", str(ctx.exception))

    def test_auth_failure_on_stdout_with_exit_zero_is_still_caught(self):
        # The real CLI does exactly this, which is why it is handled separately.
        provider = self._provider()
        fake = mock.Mock(
            returncode=0, stdout="Failed to authenticate: OAuth session expired",
            stderr="",
        )
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(ProviderError):
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")

    def test_usage_limit_is_not_retried(self):
        """The real codex quota message ends in 'try again at <date>', which the
        transient patterns match. Retrying a two-day reset is pure waste."""
        from claudex.providers.cli_api import QuotaExhausted
        provider = self._provider()
        real = (
            "ERROR: You've hit your usage limit. Upgrade to Pro "
            "(https://chatgpt.com/explore/pro), visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits "
            "or try again at Sep 20th, 2026 12:11 PM."
        )
        fake = mock.Mock(returncode=0, stdout=real, stderr="")
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(QuotaExhausted) as ctx:
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")
        message = str(ctx.exception)
        self.assertIn("Sep 20th, 2026 12:11 PM", message, "reset time is surfaced")
        self.assertIn("--via api", message, "offers the paid escape hatch")

    def test_quota_exhausted_is_not_mistaken_for_a_login_problem(self):
        # "Upgrade to Pro" contains no auth words, but credit errors elsewhere
        # mention 'sign in' - quota must win over the auth branch.
        from claudex.providers.cli_api import QuotaExhausted
        provider = self._provider()
        fake = mock.Mock(
            returncode=1, stdout="",
            stderr="You have no credits remaining. Sign in to billing to add more.",
        )
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(QuotaExhausted):
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")

    def test_rate_limit_is_retryable_not_fatal(self):
        from claudex.util import RetryableError
        provider = self._provider()
        fake = mock.Mock(returncode=1, stdout="", stderr="429 rate limit exceeded")
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(RetryableError):
                provider.complete("s", [{"role": "user", "content": "q"}], "fast")


class TestQuotaGuard(unittest.TestCase):
    def test_cli_pool_caps_calls_per_run(self):
        registry = ModelRegistry.load(SETTINGS, transport="cli")
        registry.cli_settings = {"max_calls_per_run": 3, "delay_between_calls": 0}
        pool = ProviderPool(registry, SETTINGS, offline=False)
        pool.live_calls = 3
        with self.assertRaises(QuotaExceeded):
            pool._throttle()

    def test_api_pool_has_no_cap(self):
        registry = ModelRegistry.load(SETTINGS, transport="api")
        pool = ProviderPool(registry, SETTINGS, offline=False)
        pool.live_calls = 10_000
        pool._throttle()  # must not raise


class TestProfiler(unittest.TestCase):
    """The blueprint must reshape itself to non-web projects."""

    def _plan(self, brief, constraints=""):
        return profiler._sanitise(profiler._heuristic(brief, constraints, PHASES), PHASES)

    def test_offline_game_drops_recommendation_and_adds_game_design(self):
        plan = self._plan("A single-player offline puzzle game for Android in Unity")
        dropped = {d["id"] for d in plan["drop"]}
        self.assertIn(9, dropped, "single-player game has nothing to recommend")
        self.assertIn("Game Design & Core Loop", [p["title"] for p in plan["add"]])

    def test_non_ml_project_drops_dataset_and_model_phases(self):
        plan = self._plan("A todo list web app with user accounts")
        dropped = {d["id"] for d in plan["drop"]}
        self.assertTrue({7, 8} <= dropped, "no ML means no dataset or model phase")

    def test_ml_project_keeps_dataset_and_model_phases(self):
        plan = self._plan("Predict crop disease from leaf photos using a CNN")
        dropped = {d["id"] for d in plan["drop"]}
        self.assertNotIn(7, dropped)
        self.assertNotIn(8, dropped)

    def test_hardware_project_adds_power_budget(self):
        plan = self._plan("ESP32 firmware for a soil moisture sensor with BLE")
        self.assertIn("Hardware & Power Budget", [p["title"] for p in plan["add"]])

    def test_research_project_adds_methodology(self):
        plan = self._plan("A research paper studying the hypothesis that X causes Y")
        self.assertIn(
            "Research Methodology & Reproducibility", [p["title"] for p in plan["add"]]
        )

    def test_phase_one_is_never_dropped(self):
        plan = profiler._sanitise(
            {"drop": [{"id": 1, "reason": "nonsense"}], "add": [], "notes": []}, PHASES
        )
        self.assertEqual(plan["drop"], [])

    def test_blueprint_is_never_gutted_entirely(self):
        plan = profiler._sanitise(
            {"drop": [{"id": i, "reason": "x"} for i in range(2, 26)],
             "add": [], "notes": []},
            PHASES,
        )
        self.assertGreaterEqual(len(PHASES) - len(plan["drop"]), 5)

    def test_added_phases_get_unique_ids_beyond_the_defaults(self):
        plan = profiler._sanitise(
            {"drop": [], "notes": [],
             "add": [{"title": "Extra A", "checklist": ["x"], "after": 2},
                     {"title": "Extra B", "checklist": ["y"], "after": 3}]},
            PHASES,
        )
        ids = [p["id"] for p in plan["add"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(i > 25 for i in ids))

    def test_add_without_checklist_is_rejected(self):
        plan = profiler._sanitise(
            {"drop": [], "notes": [], "add": [{"title": "Empty"}]}, PHASES
        )
        self.assertEqual(plan["add"], [])

    def test_apply_plan_removes_dropped_and_appends_added(self):
        plan = self._plan("A single-player offline puzzle game for Android in Unity")
        tailored = profiler.apply_plan(PHASES, plan)
        ids = {p["id"] for p in tailored}
        self.assertNotIn(9, ids)
        self.assertTrue(any(p.get("added_by_profiler") for p in tailored))

    def test_tailored_phases_still_order_without_deadlock(self):
        plan = self._plan("A single-player offline puzzle game for Android in Unity")
        tailored = profiler.apply_plan(PHASES, plan)
        ordered = execution_order(tailored, [p["id"] for p in tailored])
        self.assertEqual(len(ordered), len(tailored))
        seen: set[int] = set()
        for phase in ordered:
            for dep in phase["depends_on"]:
                self.assertIn(dep, seen)
            seen.add(phase["id"])

    def test_domain_notes_reach_the_prompt(self):
        from claudex import prompts
        plan = profiler._sanitise(
            {"drop": [], "add": [],
             "notes": [{"id": 6, "note": "read database as local save-file format"}]},
            PHASES,
        )
        tailored = profiler.apply_plan(PHASES, plan)
        phase6 = next(p for p in tailored if p["id"] == 6)
        _, messages = prompts.propose(phase6, "brief", "")
        self.assertIn("local save-file format", messages[0]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
