"""`--solo` on `make`: one vendor on purpose, and only where it is honest.

`--lead claude` decides who writes first and leaves the other vendor as critic.
`--solo claude` removes the second vendor entirely, so the same model writes,
critiques and scores. That makes the rubric self-assessed, which is why these
tests also assert the flag is NOT offered on the debate commands.
"""

import unittest

from claudex.cli import build_parser
from claudex.config import ModelRegistry, Settings
from claudex.router import Router

PHASE = {"id": 1, "profile": "standard"}


def _registry(live):
    """A real registry whose reachable-vendor list is pinned for the test."""
    registry = ModelRegistry.load(Settings(), transport="cli")
    registry.live_vendors = lambda transport=None, _v=list(live): list(_v)
    return registry


def _flags(parser, command):
    subparsers = parser._subparsers._group_actions[0]
    return {
        option
        for action in subparsers.choices[command]._actions
        for option in action.option_strings
    }


class TestSoloFlagSurface(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_make_offers_solo(self):
        self.assertIn("--solo", _flags(self.parser, "make"))

    def test_debate_commands_do_not_offer_solo(self):
        # Allowing it here would let a self-assessed blueprint pass for a
        # cross-examined one.
        for command in ("run", "create", "build"):
            with self.subTest(command=command):
                self.assertNotIn("--solo", _flags(self.parser, command))

    def test_make_defaults_to_no_solo(self):
        args = self.parser.parse_args(["make", "--run", "demo"])
        self.assertEqual(args.solo, "")


class TestSoloNarrowsThePool(unittest.TestCase):
    def setUp(self):
        self.registry = _registry(["claude", "gpt"])

    def test_live_is_reduced_to_the_chosen_vendor(self):
        router = Router(self.registry, solo="claude")
        self.assertEqual(router.live, ["claude"])
        self.assertEqual(router.mode, "solo")

    def test_author_and_critic_are_the_same_vendor(self):
        router = Router(self.registry, solo="claude")
        self.assertEqual(router.author_vendor(1), "claude")
        self.assertEqual(router.author_vendor(2), "claude")
        self.assertEqual(router.critic_vendor("claude"), "claude")

    def test_every_role_in_a_round_goes_to_that_vendor(self):
        plan = Router(self.registry, solo="claude").plan_round(PHASE)
        self.assertEqual({a.vendor for a in plan.values()}, {"claude"})

    def test_description_admits_the_scores_are_self_assessed(self):
        self.assertIn("self-assessed", Router(self.registry, solo="claude").describe())

    def test_reason_string_names_the_flag(self):
        plan = Router(self.registry, solo="claude").plan_round(PHASE)
        self.assertIn("--solo", plan["critique"].reason)

    def test_gpt_can_be_the_solo_vendor_too(self):
        router = Router(self.registry, solo="gpt")
        self.assertEqual(router.live, ["gpt"])
        self.assertEqual(router.critic_vendor("gpt"), "gpt")

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(Router(self.registry, solo="  claude ").live, ["claude"])


class TestSoloRejections(unittest.TestCase):
    def test_unknown_vendor_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            Router(_registry(["claude", "gpt"]), solo="bogus")
        self.assertIn("Unknown vendor", str(caught.exception))

    def test_unreachable_vendor_is_rejected_rather_than_silently_ignored(self):
        with self.assertRaises(SystemExit) as caught:
            Router(_registry(["claude"]), solo="gpt")
        self.assertIn("not reachable", str(caught.exception))

    def test_offline_skips_the_reachability_check(self):
        # Offline resolves every vendor to the mock, so nothing has to be live.
        router = Router(_registry([]), offline=True, solo="claude")
        self.assertEqual(router.live, ["claude"])
        self.assertEqual(router.mode, "offline")


class TestDefaultBehaviourUnchanged(unittest.TestCase):
    """The flag must be inert when it is not passed."""

    def test_two_live_vendors_still_debate(self):
        router = Router(_registry(["claude", "gpt"]))
        self.assertEqual(router.mode, "cross-vendor")
        self.assertNotEqual(router.author_vendor(1), router.author_vendor(2))
        self.assertEqual(router.critic_vendor("claude"), "gpt")

    def test_cross_vendor_reason_string_is_untouched(self):
        plan = Router(_registry(["claude", "gpt"])).plan_round(PHASE)
        self.assertIn("cross-examined", plan["critique"].reason)

    def test_accidental_single_vendor_keeps_its_own_message(self):
        # One vendor missing is a different situation from one vendor chosen,
        # and the operator should be able to tell them apart.
        router = Router(_registry(["claude"]))
        self.assertEqual(router.mode, "single-vendor")
        plan = router.plan_round(PHASE)
        self.assertIn("has a key", plan["critique"].reason)
        self.assertNotIn("--solo", plan["critique"].reason)


if __name__ == "__main__":
    unittest.main()
