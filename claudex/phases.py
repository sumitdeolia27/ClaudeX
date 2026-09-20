"""Loading, writing, selecting and ordering phases.

Phases live in code as a seed (phases_seed.py) and on disk as editable markdown
(config/phases/NN-slug.md). Files win when present, so a project can reshape the
blueprint without touching Python.
"""

from __future__ import annotations

import re
from pathlib import Path

from .phases_seed import PHASES as SEED
from .util import write_text

VALID_PROFILES = {"deep", "standard", "light"}


# ------------------------------------------------------------ writing ----


def phase_to_markdown(phase: dict) -> str:
    deps = ", ".join(str(d) for d in phase.get("depends_on", []))
    checklist = "\n".join(f"- {c}" for c in phase["checklist"])
    return f"""---
id: {phase['id']}
slug: {phase['slug']}
title: {phase['title']}
profile: {phase['profile']}
depends_on: {deps}
---

## Goal
{phase['goal']}

## Checklist
{checklist}
"""


def write_phase_files(settings, overwrite: bool = False) -> int:
    written = 0
    for phase in SEED:
        path = settings.phases_dir / f"{phase['id']:02d}-{phase['slug']}.md"
        if path.exists() and not overwrite:
            continue
        write_text(path, phase_to_markdown(phase))
        written += 1
    return written


# ------------------------------------------------------------ reading ----

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def parse_phase_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = _FM_RE.match(text)
    if not match:
        raise SystemExit(f"{path.name}: missing the --- frontmatter block")
    meta: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    body = match.group(2)
    goal = _section(body, "Goal")
    checklist = [
        line.lstrip("-* ").strip()
        for line in _section(body, "Checklist").splitlines()
        if line.strip().startswith(("-", "*"))
    ]

    try:
        pid = int(meta["id"])
    except (KeyError, ValueError):
        raise SystemExit(f"{path.name}: frontmatter needs a numeric `id`")

    profile = meta.get("profile", "standard")
    if profile not in VALID_PROFILES:
        raise SystemExit(
            f"{path.name}: profile '{profile}' is not one of {sorted(VALID_PROFILES)}"
        )

    deps_raw = meta.get("depends_on", "").replace(",", " ").split()
    return dict(
        id=pid,
        slug=meta.get("slug", path.stem),
        title=meta.get("title", path.stem),
        profile=profile,
        depends_on=[int(d) for d in deps_raw if d.isdigit()],
        goal=goal.strip(),
        checklist=checklist,
    )


def _section(body: str, name: str) -> str:
    match = re.search(rf"^##\s+{re.escape(name)}\s*\n(.*?)(?=^##\s|\Z)", body, re.S | re.M)
    return match.group(1) if match else ""


def load_phases(settings) -> list[dict]:
    files = sorted(settings.phases_dir.glob("*.md")) if settings.phases_dir.exists() else []
    phases = [parse_phase_file(p) for p in files] if files else [dict(p) for p in SEED]
    phases.sort(key=lambda p: p["id"])
    seen: set[int] = set()
    for p in phases:
        if p["id"] in seen:
            raise SystemExit(f"Duplicate phase id {p['id']} in config/phases/")
        seen.add(p["id"])
    for p in phases:
        unknown = [d for d in p["depends_on"] if d not in seen]
        if unknown:
            raise SystemExit(
                f"Phase {p['id']} depends on unknown phase(s) {unknown}"
            )
    return phases


# ---------------------------------------------------------- selecting ----


def parse_selection(spec: str, known: list[int]) -> list[int]:
    """Parse '1-9,12,20' into a sorted id list. Empty spec means everything."""
    if not spec or spec.strip() in {"all", "*"}:
        return sorted(known)
    chosen: set[int] = set()
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            if not (lo.isdigit() and hi.isdigit()):
                raise SystemExit(f"Bad phase range '{chunk}'. Use e.g. 1-9,12")
            chosen.update(range(int(lo), int(hi) + 1))
        elif chunk.isdigit():
            chosen.add(int(chunk))
        else:
            raise SystemExit(f"Bad phase selector '{chunk}'. Use e.g. 1-9,12")
    unknown = sorted(chosen - set(known))
    if unknown:
        raise SystemExit(f"No such phase(s): {unknown}. Known: {min(known)}-{max(known)}")
    return sorted(chosen)


def execution_order(phases: list[dict], selected: list[int]) -> list[dict]:
    """Topological order over the selection.

    Dependencies outside the selection are not pulled in - they are assumed
    already done in an earlier run, which is what makes `--phases` resumable.
    """
    by_id = {p["id"]: p for p in phases}
    chosen = [by_id[i] for i in selected]
    inside = set(selected)
    ordered: list[dict] = []
    done: set[int] = set()
    remaining = list(chosen)

    while remaining:
        ready = [p for p in remaining if all(d in done or d not in inside for d in p["depends_on"])]
        if not ready:
            cycle = ", ".join(str(p["id"]) for p in remaining)
            raise SystemExit(f"Circular dependency among phases: {cycle}")
        ready.sort(key=lambda p: p["id"])
        for p in ready:
            ordered.append(p)
            done.add(p["id"])
            remaining.remove(p)
    return ordered
