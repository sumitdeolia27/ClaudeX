"""Run directories, resumable state, and the token/cost ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .util import now_iso, read_json, slugify, write_json, write_text


@dataclass
class Run:
    settings: object
    slug: str
    data: dict = field(default_factory=dict)

    # -- lifecycle --------------------------------------------------------

    @property
    def dir(self) -> Path:
        return self.settings.runs_dir / self.slug

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @classmethod
    def create(cls, settings, name: str, brief: str, constraints: str = "") -> "Run":
        slug = slugify(name)
        run = cls(settings=settings, slug=slug)
        if run.state_path.exists():
            run.data = read_json(run.state_path, {})
            run.data["brief"] = brief or run.data.get("brief", "")
            if constraints:
                run.data["constraints"] = constraints
        else:
            run.data = {
                "name": name,
                "slug": slug,
                "brief": brief,
                "constraints": constraints,
                "created": now_iso(),
                "updated": now_iso(),
                "phases": {},
                "ledger": [],
            }
        write_text(run.dir / "brief.md", f"# {name}\n\n{brief}\n")
        run.save()
        return run

    @classmethod
    def load(cls, settings, slug: str | None = None) -> "Run":
        runs_dir = settings.runs_dir
        if slug:
            target = runs_dir / slug
            if not (target / "state.json").exists():
                raise SystemExit(f"No run named '{slug}' in {runs_dir}")
        else:
            candidates = sorted(
                (p for p in runs_dir.glob("*/state.json")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                raise SystemExit(
                    "No runs yet. Start one with:  python -m claudex init \"Your project idea\""
                )
            target = candidates[0].parent
        run = cls(settings=settings, slug=target.name)
        run.data = read_json(run.state_path, {})
        return run

    @staticmethod
    def list_all(settings) -> list[dict]:
        out = []
        for path in sorted(settings.runs_dir.glob("*/state.json")):
            data = read_json(path, {}) or {}
            out.append(
                {
                    "slug": path.parent.name,
                    "name": data.get("name", path.parent.name),
                    "created": data.get("created", "?"),
                    "phases_done": sum(
                        1 for p in data.get("phases", {}).values()
                        if p.get("status") == "accepted"
                    ),
                    "cost": sum(e.get("cost", 0.0) for e in data.get("ledger", [])),
                }
            )
        return out

    def save(self) -> None:
        self.data["updated"] = now_iso()
        write_json(self.state_path, self.data)

    # -- accessors --------------------------------------------------------

    @property
    def brief(self) -> str:
        return self.data.get("brief", "")

    @property
    def constraints(self) -> str:
        return self.data.get("constraints", "")

    @property
    def name(self) -> str:
        return self.data.get("name", self.slug)

    def phase(self, pid: int) -> dict:
        return self.data.setdefault("phases", {}).setdefault(str(pid), {})

    def is_done(self, pid: int) -> bool:
        return self.phase(pid).get("status") == "accepted"

    def section_path(self, phase: dict) -> Path:
        return self.dir / "sections" / f"{phase['id']:02d}-{phase['slug']}.md"

    def transcript_path(self, phase: dict) -> Path:
        return self.dir / "transcript" / f"{phase['id']:02d}-{phase['slug']}.json"

    def section_text(self, pid: int) -> str:
        rel = self.phase(pid).get("section_file")
        if not rel:
            return ""
        path = self.dir / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def summary_text(self, pid: int) -> str:
        """Condensed context for downstream phases; falls back to the section."""
        return self.phase(pid).get("summary") or self.section_text(pid)

    # -- ledger -----------------------------------------------------------

    def record(self, phase_id: int, role: str, assignment, completion, registry) -> float:
        cost = (
            0.0
            if completion.cached
            else registry.price(
                completion.vendor, completion.tier,
                completion.tokens_in, completion.tokens_out,
            )
        )
        self.data.setdefault("ledger", []).append(
            {
                "at": now_iso(),
                "phase": phase_id,
                "role": role,
                "vendor": completion.vendor,
                "tier": completion.tier,
                "model": completion.model,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "cost": round(cost, 6),
                "latency": round(completion.latency, 2),
                "cached": completion.cached,
                "offline": bool(completion.raw.get("mock")),
                "reason": assignment.reason,
            }
        )
        return cost

    def totals(self) -> dict:
        ledger = self.data.get("ledger", [])
        by_model: dict[str, dict] = {}
        for entry in ledger:
            key = f"{entry['vendor']}:{entry['tier']}"
            bucket = by_model.setdefault(
                key, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                      "cached": 0, "model": entry.get("model", "")}
            )
            bucket["calls"] += 1
            bucket["tokens_in"] += entry.get("tokens_in", 0)
            bucket["tokens_out"] += entry.get("tokens_out", 0)
            bucket["cost"] += entry.get("cost", 0.0)
            bucket["cached"] += 1 if entry.get("cached") else 0
        offline_calls = sum(1 for e in ledger if e.get("offline"))
        return {
            "calls": len(ledger),
            "cost": round(sum(e.get("cost", 0.0) for e in ledger), 4),
            "tokens_in": sum(e.get("tokens_in", 0) for e in ledger),
            "tokens_out": sum(e.get("tokens_out", 0) for e in ledger),
            "cached_calls": sum(1 for e in ledger if e.get("cached")),
            "offline_calls": offline_calls,
            # Offline runs price mock traffic at real rates, which makes the
            # figure a forecast of a real run - never money actually spent.
            "cost_is_estimate": offline_calls > 0,
            "by_model": by_model,
        }
