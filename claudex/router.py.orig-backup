"""Automatic model selection.

Two independent decisions:

  vendor  - *who* speaks. Authorship alternates between Claude and GPT so
            neither model's habits dominate the blueprint, and the critic is
            always the other vendor. That cross-vendor pairing is the whole
            point: a model reviewing its own output agrees with itself.

  tier    - *how hard* to think. Driven by the phase profile and the role, via
            the routing table in config/models.json, with automatic escalation
            when a phase keeps scoring badly.

Nothing here hardcodes a model ID. Change config/models.json and the routing
changes with it.
"""

from __future__ import annotations

from dataclasses import dataclass

LADDER_DEFAULT = ["fast", "balanced", "deep"]


@dataclass
class Assignment:
    vendor: str
    tier: str
    role: str
    model: str
    reason: str

    @property
    def label(self) -> str:
        return f"{self.vendor}:{self.tier}"


class Router:
    def __init__(self, registry, lead: str = "alternate", offline: bool = False):
        self.registry = registry
        self.lead = lead
        self.offline = offline
        self.ladder = registry.escalation.get("ladder", LADDER_DEFAULT)
        self.escalate_below = int(registry.escalation.get("escalate_below", 70))

        all_vendors = list(registry.vendors)
        self.live = all_vendors if offline else registry.live_vendors()
        self.mode = (
            "offline" if offline
            else "cross-vendor" if len(self.live) >= 2
            else "single-vendor" if len(self.live) == 1
            else "no-keys"
        )

    # -- vendor selection -------------------------------------------------

    def author_vendor(self, phase_id: int) -> str:
        """Alternate authorship across phases unless a lead is forced."""
        pool = self.live or list(self.registry.vendors)
        if self.lead in pool:
            return self.lead
        return pool[(phase_id - 1) % len(pool)]

    def critic_vendor(self, author: str) -> str:
        """The critic is always a different vendor when one is available."""
        pool = self.live or list(self.registry.vendors)
        others = [v for v in pool if v != author]
        return others[0] if others else author

    # -- tier selection ---------------------------------------------------

    def _bump(self, tier: str, steps: int) -> str:
        if steps <= 0 or tier not in self.ladder:
            return tier
        idx = min(len(self.ladder) - 1, self.ladder.index(tier) + steps)
        return self.ladder[idx]

    def base_tier(self, role: str, profile: str) -> str:
        table = self.registry.routing.get(role, {})
        return table.get(profile) or table.get("standard") or "balanced"

    def tier_for(self, role: str, profile: str, escalation: int = 0) -> str:
        return self._bump(self.base_tier(role, profile), escalation)

    def should_escalate(self, score: float | None) -> bool:
        return score is not None and score < self.escalate_below

    # -- full assignments -------------------------------------------------

    def assign(
        self, role: str, vendor: str, profile: str, escalation: int = 0, why: str = ""
    ) -> Assignment:
        tier = self.tier_for(role, profile, escalation)
        base = self.base_tier(role, profile)
        reason = why or f"{role} on a '{profile}' phase -> {base} tier"
        if tier != base:
            reason += f", escalated to {tier} after a low score"
        return Assignment(
            vendor=vendor,
            tier=tier,
            role=role,
            model="mock" if self.offline else self.registry.model_id(vendor, tier),
            reason=reason,
        )

    def plan_round(self, phase: dict, escalation: int = 0) -> dict[str, Assignment]:
        """Everyone who speaks in one debate round, and why they were chosen."""
        profile = phase.get("profile", "standard")
        author = self.author_vendor(phase["id"])
        critic = self.critic_vendor(author)
        single = author == critic

        plan = {
            "propose": self.assign(
                "propose", author, profile, escalation,
                why=f"phase {phase['id']} is odd/even -> {author} authors this one",
            ),
            "critique": self.assign(
                "critique", critic, profile, escalation,
                why=(
                    f"cross-examined by {critic} (different vendor from the author)"
                    if not single
                    else f"only {critic} has a key - self-critique, weaker by design"
                ),
            ),
            "revise": self.assign("revise", author, profile, escalation),
            "judge": self.assign(
                "judge", critic, profile, escalation,
                why=f"scored by {critic}, which did not write the final text",
            ),
        }
        return plan

    def tiebreak_judge(self, phase: dict, author: str, escalation: int = 0) -> Assignment:
        """Second opinion from the author's vendor when the score is borderline."""
        return self.assign(
            "judge", author, phase.get("profile", "standard"), escalation,
            why="borderline score - second judge from the other vendor, scores averaged",
        )

    # -- reporting --------------------------------------------------------

    def describe(self) -> str:
        if self.mode == "offline":
            return "offline - deterministic mock provider, no API calls, no spend"
        if self.mode == "cross-vendor":
            return f"cross-vendor - authorship alternates across {', '.join(self.live)}"
        if self.mode == "single-vendor":
            return (
                f"single-vendor - only {self.live[0]} has an API key, so it both "
                "writes and critiques. Add the second key for real debate."
            )
        return "no API keys found - run with --offline or set keys in .env"
