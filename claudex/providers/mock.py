"""Offline provider that emits schema-correct output.

This exists so the whole pipeline - prompt assembly, JSON parsing, the judge
loop, escalation, assembly, costing - can be exercised and tested without API
keys or spend. It is deterministic: the same run produces the same artifacts.
Content is structural filler, never a substitute for a real model.
"""

from __future__ import annotations

import hashlib
import re

from .base import Completion, Provider
from ..util import estimate_tokens

ROLE_RE = re.compile(r"^CLAUDEX-(ROLE|PHASE|ROUND|TITLE):\s*(.+)$", re.M)

# Mock replies are a fraction of the length of real ones, so reporting their
# true size would make the offline cost forecast useless. Output tokens are
# reported at the typical size of a real reply for that role instead. Input
# tokens are not simulated - the prompts are the real ones.
TYPICAL_OUTPUT_TOKENS = {
    "propose": 2200, "revise": 2400, "critique": 1100,
    "judge": 260, "summarize": 200, "assemble": 1200,
    "product-plan": 900, "product-implement": 1600,
    "product-review": 500, "product-revise": 1700, "product-repair": 1700,
}


def _meta(system: str) -> dict:
    return {k.lower(): v.strip() for k, v in ROLE_RE.findall(system)}


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


class MockProvider(Provider):
    name = "mock"

    def complete(
        self,
        system: str,
        messages: list[dict],
        tier: str,
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        meta = _meta(system)
        role = meta.get("role", "propose")
        phase = meta.get("phase", "0")
        title = meta.get("title", "Section")
        rnd = int(meta.get("round", "1") or 1)
        builder = {
            "propose": self._propose,
            "revise": self._revise,
            "critique": self._critique,
            "judge": self._judge,
            "summarize": self._summarize,
            "assemble": self._assemble,
            "product-plan": self._product_plan,
            "product-implement": self._product_implement,
            "product-review": self._product_review,
            "product-revise": self._product_implement,
            "product-repair": self._product_implement,
        }.get(role, self._propose)
        text = builder(phase, title, rnd)
        prompt_chars = len(system) + sum(len(m.get("content", "")) for m in messages)
        return Completion(
            text=text,
            vendor=self.vendor,
            tier=tier,
            model=f"mock-{self.vendor}-{tier}",
            tokens_in=estimate_tokens("x" * prompt_chars),
            tokens_out=TYPICAL_OUTPUT_TOKENS.get(role, 1000),
            latency=0.01,
            raw={"mock": True, "real_tokens_out": estimate_tokens(text)},
        )

    # -- role builders ----------------------------------------------------

    def _propose(self, phase: str, title: str, rnd: int) -> str:
        return f"""## {title}

### Summary
Mock draft for phase {phase} produced by `{self.vendor}` in offline mode. Real
providers replace this with an actual proposal; the structure below is what the
pipeline parses and assembles.

### Decisions
- **D{phase}.1** - Placeholder decision for {title.lower()}.
- **D{phase}.2** - Second placeholder decision, dependent on D{phase}.1.

### Details
Every checklist item for this phase would be addressed here, each with a
concrete choice rather than a list of options.

### Risks
- Offline mode cannot evaluate real trade-offs.

### Open questions
- Which constraint matters most for this project?

```json
{{
  "decisions": [
    {{"id": "D{phase}.1", "choice": "placeholder", "why": "offline mode"}},
    {{"id": "D{phase}.2", "choice": "placeholder", "why": "offline mode"}}
  ],
  "covered": ["all-checklist-items"],
  "not_covered": [],
  "open_questions": ["Which constraint matters most?"]
}}
```"""

    def _revise(self, phase: str, title: str, rnd: int) -> str:
        base = self._propose(phase, title, rnd)
        return base.replace(
            "### Risks",
            f"### Changes made in round {rnd}\n"
            f"- Accepted the critic's blocker and tightened D{phase}.1.\n"
            f"- Rejected one minor note as out of scope.\n\n### Risks",
        )

    def _critique(self, phase: str, title: str, rnd: int) -> str:
        severity = "blocker" if rnd == 1 else "minor"
        verdict = "revise" if rnd == 1 else "acceptable"
        return f"""Cross-examination of phase {phase} ({title}).

```json
{{
  "issues": [
    {{"severity": "{severity}", "item": "Specificity",
     "problem": "Decision D{phase}.1 states a choice without a measurable target.",
     "fix": "Attach a number and a verification method to D{phase}.1."}},
    {{"severity": "minor", "item": "Consistency",
     "problem": "Terminology drifts from the phase 1 glossary.",
     "fix": "Reuse the phase 1 terms verbatim."}}
  ],
  "missing_checklist_items": [],
  "verdict": "{verdict}"
}}
```"""

    def _judge(self, phase: str, title: str, rnd: int) -> str:
        # Score climbs with each round so the accept/iterate path is exercised.
        base = 58 + _seed(phase, title) % 8
        score = min(96, base + rnd * 14)
        verdict = "ACCEPT" if score >= 75 else "ITERATE"
        gaps = '["Targets are still qualitative."]' if verdict == "ITERATE" else "[]"
        return f"""```json
{{
  "scores": {{"coverage": {min(100, score + 2)}, "specificity": {score},
             "consistency": {min(100, score + 4)}, "feasibility": {score},
             "risk_handling": {max(40, score - 6)}}},
  "total": {score},
  "verdict": "{verdict}",
  "blocking_gaps": {gaps},
  "note": "Mock judge for phase {phase}, round {rnd}."
}}
```"""

    def _summarize(self, phase: str, title: str, rnd: int) -> str:
        return f"Condensed context for phase {phase} ({title}): decisions D{phase}.1 and D{phase}.2 stand."

    def _assemble(self, phase: str, title: str, rnd: int) -> str:
        return (
            "## Executive summary\n\nMock executive summary. With real keys this "
            "is written from the accepted sections of every phase."
        )

    def _product_plan(self, phase: str, title: str, rnd: int) -> str:
        return """```json
{"tasks":[{"id":"T001","title":"Create the runnable MVP",
"objective":"Create a minimal product entry point that proves the build pipeline.",
"acceptance":["README exists and identifies offline mock output"],
"depends_on":[]}]}
```"""

    def _product_implement(self, phase: str, title: str, rnd: int) -> str:
        return """```json
{"files":[{"path":"README.md","content":"# Offline mock product\\n\\nStructural output only. Run ClaudeX with real providers to build the product.\\n"}],
"notes":"Created the deterministic offline product fixture."}
```"""

    def _product_review(self, phase: str, title: str, rnd: int) -> str:
        return """```json
{"approved":true,"issues":[]}
```"""
