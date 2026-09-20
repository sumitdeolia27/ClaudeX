"""The debate engine: propose -> critique -> revise -> judge, until accepted.

One phase at a time. Each round the author (one vendor) writes, the critic (the
other vendor) attacks, the author revises, and a judge that did not write the
final text scores it against a rubric. A low score escalates the phase to a
stronger model tier for the next round rather than simply retrying.
"""

from __future__ import annotations

import time

from . import prompts
from .util import (
    extract_json, human_duration, now_iso, score_text, strip_json_blocks,
    truncate, write_json, write_text,
)

RUBRIC_KEYS = ("coverage", "specificity", "consistency", "feasibility", "risk_handling")
DEP_CONTEXT_BUDGET = 18000
SUMMARIZE_OVER = 2800
BORDERLINE = 8


def projected_calls(n_phases: int, rounds: int, dual_judge: bool = True) -> tuple[int, int]:
    """(typical, worst-case) model calls for a run. Must match run_phase().

    Per phase, round 1 costs propose + critique + revise + judge. Every extra
    round reuses the previous revision as the draft, so it costs 3. One
    summarize call condenses an accepted section for downstream phases, and a
    borderline score can add a second judge in any round.

    This lives next to the engine on purpose: when the call pattern changes,
    the estimate the user is quoted has to change with it.
    """
    rounds = max(1, rounds)
    per_phase = 4 + 3 * (rounds - 1) + 1
    typical = per_phase * n_phases
    worst = (per_phase + (rounds if dual_judge else 0)) * n_phases
    return typical, worst


# ---------------------------------------------------------- scoring ------


def parse_judgement(text: str) -> dict:
    """Turn a judge reply into a score dict, defending against malformed JSON.

    A reply that does not parse yields total=None, NOT 0.0. Those are different
    facts: 0/100 means the judge read the section and hated it, None means no
    judgement exists. Collapsing them made a formatting glitch indistinguishable
    from a terrible section in report.html, and fed the escalation logic a
    failure that never happened.
    """
    data = extract_json(text) or {}
    if not isinstance(data, dict):
        data = {}
    if not data:
        return {
            "scores": {key: None for key in RUBRIC_KEYS},
            "total": None,
            "verdict": "UNSCORED",
            "blocking_gaps": [],
            "note": "judge reply did not parse as JSON",
            "parsed": False,
        }
    raw_scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    scores = {}
    for key in RUBRIC_KEYS:
        try:
            scores[key] = max(0.0, min(100.0, float(raw_scores.get(key, 0))))
        except (TypeError, ValueError):
            scores[key] = 0.0

    total = data.get("total")
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = 0.0
    # Trust the rubric mean over a self-reported total that disagrees with it.
    mean = sum(scores.values()) / len(RUBRIC_KEYS) if any(scores.values()) else 0.0
    if mean and abs(total - mean) > 5:
        total = mean
    total = max(0.0, min(100.0, total or mean))

    verdict = str(data.get("verdict", "")).upper()
    if verdict not in {"ACCEPT", "ITERATE"}:
        verdict = "ACCEPT" if total >= 75 else "ITERATE"

    gaps = data.get("blocking_gaps") or []
    if not isinstance(gaps, list):
        gaps = [str(gaps)]
    return {
        "scores": scores,
        "total": round(total, 1),
        "verdict": verdict,
        "blocking_gaps": [str(g) for g in gaps][:8],
        "note": str(data.get("note", ""))[:400],
        "parsed": bool(data),
    }


def count_issues(critique_text: str) -> dict:
    data = extract_json(critique_text) or {}
    issues = data.get("issues") if isinstance(data, dict) else None
    issues = issues if isinstance(issues, list) else []
    counts = {"blocker": 0, "major": 0, "minor": 0}
    for issue in issues:
        sev = str(issue.get("severity", "minor")).lower() if isinstance(issue, dict) else "minor"
        if sev in counts:
            counts[sev] += 1
    missing = data.get("missing_checklist_items") if isinstance(data, dict) else []
    counts["missing"] = len(missing) if isinstance(missing, list) else 0
    counts["total"] = len(issues)
    return counts


# --------------------------------------------------------- context -------


def dependency_context(phase: dict, run) -> str:
    chunks = []
    for dep_id in phase.get("depends_on", []):
        text = run.summary_text(dep_id).strip()
        if not text:
            continue
        meta = run.phase(dep_id)
        title = meta.get("title", f"Phase {dep_id}")
        chunks.append(f"### Phase {dep_id}: {title}\n{text}")
    if not chunks:
        return ""
    joined = "\n\n".join(chunks)
    return truncate(joined, DEP_CONTEXT_BUDGET, "\n\n...[earlier context truncated]")


# ----------------------------------------------------------- engine ------


class DebateEngine:
    def __init__(self, run, router, pool, registry, console,
                 max_rounds: int = 2, accept_score: float = 80.0,
                 dual_judge: bool = True):
        self.run = run
        self.router = router
        self.pool = pool
        self.registry = registry
        self.console = console
        self.max_rounds = max_rounds
        self.accept_score = accept_score
        self.dual_judge = dual_judge

    def _call(self, assignment, built, phase_id: int, max_tokens=None, temperature=0.6):
        system, messages = built
        completion = self.pool.complete(
            vendor=assignment.vendor, tier=assignment.tier,
            system=system, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
        )
        cost = self.run.record(phase_id, assignment.role, assignment, completion, self.registry)
        tag = " (cached)" if completion.cached else f" {completion.tokens_out}tok ${cost:.4f}"
        self.console.dim(f"{assignment.role:<9} {assignment.label:<16} {completion.model}{tag}")
        return completion

    def run_phase(self, phase: dict) -> dict:
        started = time.time()
        pid = phase["id"]
        brief = self.run.brief
        constraints = self.run.constraints
        deps = dependency_context(phase, self.run)

        self.console.head(f"Phase {pid}: {phase['title']}  [{phase['profile']}]")

        rounds: list[dict] = []
        escalation = 0
        section = ""
        judgement = {"total": None, "verdict": "UNSCORED", "blocking_gaps": [],
                     "scores": {}}
        plan = self.router.plan_round(phase, escalation)

        for rnd in range(1, self.max_rounds + 1):
            plan = self.router.plan_round(phase, escalation)
            self.console.step(
                f"round {rnd}/{self.max_rounds} - "
                f"{plan['propose'].vendor} writes, {plan['critique'].vendor} attacks"
            )

            if rnd == 1:
                draft = self._call(
                    plan["propose"],
                    prompts.propose(phase, brief, deps, constraints),
                    pid, temperature=0.65,
                ).text
            else:
                draft = section  # the previous round's revision gets attacked next

            critique_text = self._call(
                plan["critique"],
                prompts.critique(phase, brief, deps, draft, rnd),
                pid, temperature=0.4,
            ).text

            section = self._call(
                plan["revise"],
                prompts.revise(phase, brief, deps, draft, critique_text, rnd),
                pid, temperature=0.55,
            ).text

            judgement = self._judge(phase, brief, deps, section, rnd, escalation, plan)
            issues = count_issues(critique_text)

            self.console.step(
                f"score {score_text(judgement['total'])}/100 -> {judgement['verdict']}"
                f"   (issues: {issues['blocker']}B/{issues['major']}M/{issues['minor']}m)"
            )
            if judgement["total"] is None:
                self.console.warn(
                    "the judge's reply did not parse - this phase is UNSCORED, "
                    "not bad. Check `parsed` in the transcript."
                )
            for gap in judgement["blocking_gaps"][:2]:
                self.console.dim(f"gap: {gap[:110]}")

            rounds.append({
                "round": rnd,
                "author": plan["propose"].vendor,
                "critic": plan["critique"].vendor,
                "tiers": {k: v.tier for k, v in plan.items()},
                "escalation": escalation,
                "draft": draft,
                "critique": critique_text,
                "revision": section,
                "judgement": judgement,
                "issues": issues,
            })

            if judgement["total"] is not None and judgement["total"] >= self.accept_score:
                break
            if rnd < self.max_rounds and self.router.should_escalate(judgement["total"]):
                escalation += 1
                self.console.dim("score below escalation threshold - stronger tier next round")

        accepted = (
            judgement["total"] is not None and judgement["total"] >= self.accept_score
        )
        self._persist(phase, section, rounds, judgement, accepted, plan, started)
        return {"accepted": accepted, "score": judgement["total"], "rounds": len(rounds)}

    # -- judging ----------------------------------------------------------

    def _judge(self, phase, brief, deps, section, rnd, escalation, plan) -> dict:
        primary = self._call(
            plan["judge"],
            prompts.judge(phase, brief, deps, section, rnd),
            phase["id"], temperature=0.2,
        )
        judgement = parse_judgement(primary.text)
        judgement["judges"] = [{"vendor": plan["judge"].vendor, "total": judgement["total"]}]

        # A borderline score decides whether a phase gets another expensive
        # round, so it is worth a second opinion from the other vendor.
        # An unparsed primary is worth a second opinion too - that is exactly
        # the case where one more cheap call can rescue the phase.
        borderline = (
            judgement["total"] is None
            or abs(judgement["total"] - self.accept_score) <= BORDERLINE
        )
        if self.dual_judge and borderline and len(self.router.live) > 1:
            second_assignment = self.router.tiebreak_judge(
                phase, plan["propose"].vendor, escalation
            )
            if second_assignment.vendor != plan["judge"].vendor:
                second = self._call(
                    second_assignment,
                    prompts.judge(phase, brief, deps, section, rnd),
                    phase["id"], temperature=0.2,
                )
                other = parse_judgement(second.text)
                judgement["judges"].append(
                    {"vendor": second_assignment.vendor, "total": other["total"]}
                )
                totals = [t for t in (judgement["total"], other["total"]) if t is not None]
                averaged = round(sum(totals) / len(totals), 1) if totals else None
                self.console.dim(
                    f"borderline: {score_text(judgement['total'])} vs "
                    f"{score_text(other['total'])} -> {score_text(averaged)}"
                )
                judgement["total"] = averaged
                judgement["verdict"] = (
                    "UNSCORED" if averaged is None
                    else "ACCEPT" if averaged >= self.accept_score else "ITERATE"
                )
                judgement["parsed"] = judgement.get("parsed") or other.get("parsed", False)
                judgement["blocking_gaps"] = (
                    judgement["blocking_gaps"] + other["blocking_gaps"]
                )[:8]
        return judgement

    # -- persistence ------------------------------------------------------

    def _persist(self, phase, section, rounds, judgement, accepted, plan, started):
        pid = phase["id"]
        section_path = self.run.section_path(phase)
        header = (
            f"<!-- phase {pid} | score {score_text(judgement['total'])}/100 | "
            f"{len(rounds)} round(s) | author {rounds[-1]['author']} | "
            f"critic {rounds[-1]['critic']} | {now_iso()} -->\n\n"
        )
        write_text(section_path, header + section.strip() + "\n")
        write_json(self.run.transcript_path(phase), {
            "phase": {k: phase[k] for k in ("id", "slug", "title", "profile", "goal")},
            "rounds": rounds,
        })

        summary = self._summarize(phase, section, plan)
        elapsed = time.time() - started

        self.run.phase(pid).update({
            "id": pid,
            "title": phase["title"],
            "slug": phase["slug"],
            "status": (
                "accepted" if accepted
                else "unscored" if judgement["total"] is None
                else "needs_work"
            ),
            "score": judgement["total"],
            "scored": judgement["total"] is not None,
            "scores": judgement["scores"],
            "rounds": len(rounds),
            "author": rounds[-1]["author"],
            "critic": rounds[-1]["critic"],
            "blocking_gaps": judgement["blocking_gaps"],
            "summary": summary,
            "section_file": str(section_path.relative_to(self.run.dir)).replace("\\", "/"),
            "transcript_file": str(
                self.run.transcript_path(phase).relative_to(self.run.dir)
            ).replace("\\", "/"),
            "seconds": round(elapsed, 1),
            "updated": now_iso(),
        })
        self.run.save()

        verdict = (
            "accepted" if accepted
            else "kept UNSCORED (judge reply unparsable)" if judgement["total"] is None
            else "kept with open gaps"
        )
        line = (f"phase {pid} {verdict} at {score_text(judgement['total'])}/100 "
                f"in {human_duration(elapsed)}")
        (self.console.ok if accepted else self.console.warn)(line)

    def _summarize(self, phase, section, plan) -> str:
        """Condense long sections so later phases get signal, not volume."""
        body = strip_json_blocks(section)
        if len(body) <= SUMMARIZE_OVER:
            return body.strip()
        assignment = self.router.assign(
            "summarize", plan["propose"].vendor, phase.get("profile", "standard"),
            why="condensing an accepted section for downstream phases",
        )
        try:
            return self._call(
                assignment, prompts.summarize(phase, body), phase["id"],
                # The summary now carries a reason per decision, so it needs
                # room. Starving it here is what produced telegraphic output
                # that later phases could not actually reason about.
                max_tokens=1100, temperature=0.2,
            ).text.strip()
        except Exception as exc:  # summarisation is an optimisation, never fatal
            self.console.warn(f"summary failed ({str(exc)[:80]}), using truncated section")
            return truncate(body, SUMMARIZE_OVER)
