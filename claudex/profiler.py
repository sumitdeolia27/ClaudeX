"""Reshape the blueprint to fit the project.

The 25 default phases assume a data-driven web application. Most projects are
not that. Before any section is written, a model reads the brief and decides
which phases apply, which to drop, which domain-specific phases to add, and how
to reinterpret the ones that stay.

Offline, or when the model reply cannot be parsed, a keyword heuristic does the
same job less precisely. It is deliberately conservative: it only drops phases
whose irrelevance is unambiguous from the brief.
"""

from __future__ import annotations

import re

from . import prompts
from .util import extract_json, now_iso

# Phases whose relevance depends on the kind of project.
ML_PHASES = {7, 8}           # dataset, model
RECO_PHASE = 9
UI_PHASES = {3, 4}           # UI/UX, frontend
BACKEND_PHASES = {5, 6, 15}  # backend, database, API
DEPLOY_PHASES = {14, 16, 17} # scalability, devops, monitoring

# Keyword -> domain signal. Deliberately blunt; the LLM path is the good one.
SIGNALS = {
    "ml": r"\b(ml|machine learning|deep learning|neural|predict|classif|"
          r"detect|recogni[sz]|recommend|nlp|llm|model|train|dataset|"
          r"forecast|sentiment|vision)\b",
    "reco": r"\b(recommend|suggestion|personali[sz]|feed|discovery)\b",
    "game": r"\b(game|gameplay|player|level design|multiplayer|fps|rpg|"
            r"platformer|unity|unreal|godot)\b",
    "hardware": r"\b(firmware|embedded|arduino|raspberry|esp32|stm32|sensor|"
                r"microcontroller|pcb|robot|iot device|drone)\b",
    "research": r"\b(research paper|thesis|dissertation|literature review|"
                r"study|hypothesis|survey paper|reproduc)\b",
    "cli": r"\b(cli|command.line|terminal|shell script|devtool)\b",
    "mobile": r"\b(android|ios|mobile app|flutter|react native|kotlin|swift)\b",
    "web": r"\b(web app|website|dashboard|portal|saas|browser)\b",
    "data": r"\b(pipeline|etl|data warehouse|analytics platform|scraper|"
            r"ingestion|streaming)\b",
    "offline": r"\b(offline|single.player|standalone|local.only|no server)\b",
}

# Extra phases worth adding per domain, keyed by signal.
DOMAIN_PHASES = {
    "game": [dict(
        title="Game Design & Core Loop", profile="deep", after=2,
        goal="Define the moment-to-moment loop, progression, and difficulty curve.",
        checklist=[
            "Core gameplay loop", "Win and lose conditions", "Progression system",
            "Difficulty curve and pacing", "Level or content structure",
            "Player controls and game feel", "Economy or scoring balance",
            "Replayability", "Tutorial and onboarding", "Art and audio direction",
        ],
    )],
    "hardware": [dict(
        title="Hardware & Power Budget", profile="deep", after=2,
        goal="Specify components, interfaces, power draw, and physical constraints.",
        checklist=[
            "Component selection with part numbers", "Microcontroller or SoC choice",
            "Sensors and actuators", "Communication interfaces (I2C, SPI, UART, BLE)",
            "Power source and budget", "Battery life target", "Thermal constraints",
            "Enclosure and form factor", "Bill of materials and unit cost",
            "Firmware update mechanism", "Failure and safety modes",
        ],
    )],
    "research": [dict(
        title="Research Methodology & Reproducibility", profile="deep", after=1,
        goal="State the hypothesis, method, and what would falsify the conclusion.",
        checklist=[
            "Research question and hypothesis", "Prior work positioning",
            "Experimental design", "Variables and controls", "Sample and sampling method",
            "Statistical tests and power", "Threats to validity",
            "What result would disprove the hypothesis", "Reproducibility package",
            "Ethics approval if human subjects",
        ],
    )],
    "data": [dict(
        title="Data Pipeline Architecture", profile="deep", after=5,
        goal="Define ingestion, transformation, scheduling, and data quality gates.",
        checklist=[
            "Source systems and access", "Batch or streaming", "Ingestion mechanism",
            "Transformation layer", "Orchestration and scheduling", "Idempotency and replay",
            "Schema evolution", "Data quality checks", "Late and out-of-order data",
            "Partitioning and retention", "Lineage and observability", "Backfill strategy",
        ],
    )],
    "mobile": [dict(
        title="Mobile Platform Constraints", profile="standard", after=4,
        goal="Cover the constraints that only apply on a phone.",
        checklist=[
            "Target OS versions and device range", "Offline behaviour and sync",
            "Battery and data usage", "App size budget", "Permissions and privacy prompts",
            "Push notifications", "Background execution limits",
            "App store review requirements", "Crash reporting", "Update and migration path",
        ],
    )],
}


def _heuristic(brief: str, constraints: str, phases: list[dict]) -> dict:
    text = f"{brief} {constraints}".lower()
    hits = {name: bool(re.search(pat, text)) for name, pat in SIGNALS.items()}

    drop: list[dict] = []
    known = {p["id"] for p in phases}

    if not hits["ml"]:
        for pid in sorted(ML_PHASES & known):
            drop.append({"id": pid, "reason": "no ML/data component detected in the brief"})
    if not hits["reco"] or hits["offline"]:
        if RECO_PHASE in known:
            drop.append({"id": RECO_PHASE, "reason": "no recommendation feature detected"})
    if hits["research"] and not hits["web"]:
        for pid in sorted(DEPLOY_PHASES & known):
            drop.append({"id": pid, "reason": "research output, not a deployed service"})
    if hits["cli"] and not hits["web"] and not hits["mobile"]:
        for pid in sorted(UI_PHASES & known):
            drop.append({"id": pid, "reason": "command-line tool, no graphical UI"})
    if hits["offline"] and not hits["web"]:
        for pid in sorted(BACKEND_PHASES & known):
            drop.append({"id": pid, "reason": "runs locally, no server component detected"})

    add = []
    for signal, extra in DOMAIN_PHASES.items():
        if hits.get(signal):
            add.extend(extra)

    kind = next(
        (k for k in ("game", "hardware", "research", "data", "mobile", "cli", "web")
         if hits.get(k)),
        "software project",
    )
    return {
        "project_type": kind,
        "domain_tags": [k for k, v in hits.items() if v],
        "characteristics": {
            "has_ml": hits["ml"], "has_ui": not hits["cli"],
            "has_backend": not (hits["offline"] and not hits["web"]),
            "has_dataset": hits["ml"], "is_research": hits["research"],
            "is_hardware": hits["hardware"], "user_facing": not hits["cli"],
        },
        "drop": drop,
        "notes": [],
        "add": add[:5],
        "summary": f"Keyword heuristic: treated as a {kind} project.",
        "source": "heuristic",
    }


def _sanitise(plan: dict, phases: list[dict]) -> dict:
    """Never let a model's plan produce an unusable blueprint."""
    known = {p["id"] for p in phases}
    plan.setdefault("drop", [])
    plan.setdefault("notes", [])
    plan.setdefault("add", [])

    drop = []
    for item in plan["drop"] if isinstance(plan["drop"], list) else []:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        # Phase 1 defines the problem; everything depends on it.
        if pid in known and pid != 1:
            drop.append({"id": pid, "reason": str(item.get("reason", ""))[:200]})

    # Refuse to gut the blueprint entirely.
    if len(drop) > len(known) - 5:
        drop = drop[: max(0, len(known) - 5)]

    notes = [
        {"id": int(n["id"]), "note": str(n.get("note", ""))[:300]}
        for n in plan["notes"] if isinstance(n, dict) and str(n.get("id", "")).isdigit()
        and int(n["id"]) in known
    ]

    add = []
    next_id = (max(known) if known else 0) + 1
    for item in plan["add"][:5] if isinstance(plan["add"], list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        checklist = [str(c) for c in (item.get("checklist") or []) if str(c).strip()]
        if not checklist:
            continue
        after = item.get("after")
        add.append({
            "id": next_id,
            "slug": re.sub(r"[^a-z0-9]+", "-", str(item["title"]).lower()).strip("-")[:40]
                    or f"extra-{next_id}",
            "title": str(item["title"])[:80],
            "profile": item.get("profile") if item.get("profile") in
                       {"deep", "standard", "light"} else "standard",
            "goal": str(item.get("goal", ""))[:300] or "Domain-specific phase.",
            "checklist": checklist[:25],
            "depends_on": [1] if not str(after).isdigit() else [1, int(after)],
            "added_by_profiler": True,
            "after": int(after) if str(after).isdigit() else 1,
        })
        next_id += 1

    plan["drop"], plan["notes"], plan["add"] = drop, notes, add
    plan.setdefault("project_type", "software project")
    plan.setdefault("summary", "")
    return plan


def profile(run, phases, router, pool, registry, console, offline: bool = False) -> dict:
    """Produce and persist the tailored blueprint plan for this run."""
    plan = None
    if not offline:
        assignment = router.assign(
            "profile", router.author_vendor(1), "standard",
            why="one cheap call to decide which phases this project actually needs",
        )
        system, messages = prompts.profile_project(run.brief, run.constraints, phases)
        try:
            completion = pool.complete(
                vendor=assignment.vendor, tier=assignment.tier,
                system=system, messages=messages, max_tokens=2000, temperature=0.3,
            )
            run.record(0, "profile", assignment, completion, registry)
            parsed = extract_json(completion.text)
            if isinstance(parsed, dict) and parsed:
                plan = parsed
                plan["source"] = f"{assignment.vendor}:{assignment.tier}"
        except Exception as exc:
            console.warn(f"profiling call failed ({str(exc)[:90]}), using keyword heuristic")

    if plan is None:
        plan = _heuristic(run.brief, run.constraints, phases)

    plan = _sanitise(plan, phases)
    plan["generated"] = now_iso()
    run.data["plan"] = plan
    run.save()

    dropped = len(plan["drop"])
    added = len(plan["add"])
    console.step(f"project type: {plan['project_type']}  (via {plan.get('source', '?')})")
    if plan.get("summary"):
        console.dim(plan["summary"][:140])
    if dropped:
        console.dim(
            "dropping: "
            + ", ".join(f"{d['id']} ({d['reason'][:40]})" for d in plan["drop"][:6])
        )
    if added:
        console.dim("adding: " + ", ".join(p["title"] for p in plan["add"]))
    if not dropped and not added:
        console.dim("default 25 phases fit this project unchanged")
    return plan


def apply_plan(phases: list[dict], plan: dict | None) -> list[dict]:
    """Return the phase list this run should actually execute."""
    if not plan:
        return phases
    dropped = {d["id"] for d in plan.get("drop", [])}
    notes = {n["id"]: n["note"] for n in plan.get("notes", [])}

    kept = []
    for phase in phases:
        if phase["id"] in dropped:
            continue
        phase = dict(phase)
        # A dependency on a dropped phase can never be satisfied, so remove it
        # rather than leaving the ordering to silently ignore it later.
        phase["depends_on"] = [d for d in phase.get("depends_on", []) if d not in dropped]
        if note := notes.get(phase["id"]):
            phase["domain_note"] = note
        kept.append(phase)

    for extra in plan.get("add", []):
        phase = dict(extra)
        phase["depends_on"] = [d for d in phase.get("depends_on", []) if d not in dropped]
        kept.append(phase)

    kept.sort(key=lambda p: (p.get("after", p["id"]), p["id"]))
    return kept
