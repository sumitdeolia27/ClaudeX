"""Turn an accepted blueprint into a resumable product workspace.

Models never receive permission to write files or run commands. They return
strict JSON file bundles; ClaudeX validates every relative path and performs
the writes inside ``runs/<slug>/product``. Verification commands are accepted
only from the human invoking the CLI and always run without a shell.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path, PurePosixPath

from .providers import ProviderError
from .util import extract_json, now_iso, truncate, write_text

MAX_TASKS = 60
MAX_FILES_PER_TASK = 40
MAX_FILE_CHARS = 1_000_000
MAX_CONTEXT_CHARS = 120_000


def safe_product_path(workspace: Path, raw: str) -> Path:
    """Resolve one model-proposed path beneath the product workspace."""
    value = str(raw or "").strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or "\x00" in value
    ):
        raise ProviderError(f"Model proposed an unsafe product path: {raw!r}")
    target = workspace.joinpath(*path.parts)
    root = workspace.resolve()
    resolved = target.resolve()
    if root != resolved and root not in resolved.parents:
        raise ProviderError(f"Model path escapes the product workspace: {raw!r}")
    return target


def _json_call(pool, run, registry, assignment, system, user, role) -> dict:
    completion = pool.complete(
        vendor=assignment.vendor,
        tier=assignment.tier,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=8000,
        temperature=0.2,
    )
    run.record(0, role, assignment, completion, registry)
    payload = extract_json(completion.text)
    if not isinstance(payload, dict):
        raise ProviderError(
            f"{assignment.label} returned invalid JSON for {role}. "
            "The product run is saved and can be resumed."
        )
    return payload


def _clean_plan(payload: dict) -> list[dict]:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ProviderError("The implementation plan did not contain any tasks.")
    tasks: list[dict] = []
    known: set[str] = set()
    for index, raw in enumerate(raw_tasks[:MAX_TASKS], 1):
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("id") or f"T{index:03d}").strip()[:32]
        if not task_id or task_id in known:
            task_id = f"T{index:03d}"
        known.add(task_id)
        acceptance = raw.get("acceptance")
        if not isinstance(acceptance, list):
            acceptance = []
        depends = raw.get("depends_on")
        if not isinstance(depends, list):
            depends = []
        tasks.append({
            "id": task_id,
            "title": str(raw.get("title") or task_id).strip()[:160],
            "objective": str(raw.get("objective") or "").strip()[:2000],
            "acceptance": [str(x).strip()[:500] for x in acceptance[:20] if str(x).strip()],
            "depends_on": [str(x).strip()[:32] for x in depends if str(x).strip()],
        })
    if not tasks:
        raise ProviderError("The implementation plan contained no usable tasks.")
    ids = {task["id"] for task in tasks}
    for task in tasks:
        task["depends_on"] = [d for d in task["depends_on"] if d in ids and d != task["id"]]
    return tasks


def _task_order(tasks: list[dict]) -> list[dict]:
    remaining = list(tasks)
    ordered: list[dict] = []
    done: set[str] = set()
    while remaining:
        ready = [task for task in remaining if all(d in done for d in task["depends_on"])]
        if not ready:
            cycle = ", ".join(task["id"] for task in remaining)
            raise ProviderError(f"Implementation task dependency cycle: {cycle}")
        for task in ready:
            ordered.append(task)
            done.add(task["id"])
            remaining.remove(task)
    return ordered


def _clean_bundle(payload: dict, workspace: Path) -> list[dict]:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ProviderError("The implementation response did not contain files.")
    files: list[dict] = []
    seen: set[str] = set()
    for raw in raw_files[:MAX_FILES_PER_TASK]:
        if not isinstance(raw, dict):
            continue
        rel = str(raw.get("path") or "").strip().replace("\\", "/")
        safe_product_path(workspace, rel)
        content = raw.get("content")
        if not isinstance(content, str):
            continue
        if len(content) > MAX_FILE_CHARS:
            raise ProviderError(f"Model file is too large: {rel}")
        if rel in seen:
            raise ProviderError(f"Model returned the same file twice: {rel}")
        seen.add(rel)
        files.append({"path": rel, "content": content})
    if not files:
        raise ProviderError("The implementation response contained no usable files.")
    return files


def _workspace_snapshot(workspace: Path) -> str:
    parts: list[str] = []
    used = 0
    if not workspace.exists():
        return "(empty workspace)"
    for path in sorted(p for p in workspace.rglob("*") if p.is_file()):
        if path.is_symlink():
            continue
        rel = path.relative_to(workspace).as_posix()
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = f"\n--- {rel} ---\n{body}\n"
        if used + len(block) > MAX_CONTEXT_CHARS:
            parts.append("\n...[workspace snapshot truncated]\n")
            break
        parts.append(block)
        used += len(block)
    return "".join(parts) or "(empty workspace)"


def _write_bundle(workspace: Path, files: list[dict]) -> list[str]:
    written: list[str] = []
    for item in files:
        target = safe_product_path(workspace, item["path"])
        write_text(target, item["content"])
        written.append(item["path"])
    return written


def _verify(workspace: Path, command: list[str] | None, expected: list[str]) -> dict:
    missing = [rel for rel in expected if not safe_product_path(workspace, rel).is_file()]
    empty = [
        rel for rel in expected
        if safe_product_path(workspace, rel).is_file()
        and safe_product_path(workspace, rel).stat().st_size == 0
    ]
    if missing or empty:
        return {"ok": False, "kind": "structural", "missing": missing, "empty": empty}
    if not command:
        return {"ok": True, "kind": "structural", "detail": "files exist and are non-empty"}
    try:
        proc = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "kind": "command", "command": command, "output": str(exc)[:4000]}
    output = truncate(f"{proc.stdout}\n{proc.stderr}".strip(), 12_000)
    return {
        "ok": proc.returncode == 0,
        "kind": "command",
        "command": command,
        "exit_code": proc.returncode,
        "output": output,
    }


class ProductEngine:
    def __init__(self, run, router, pool, registry, console, verify_command: str = ""):
        self.run = run
        self.router = router
        self.pool = pool
        self.registry = registry
        self.console = console
        self.workspace = run.dir / "product"
        self.command = shlex.split(verify_command, posix=False) if verify_command else None
        self.product = run.data.setdefault("product", {})

    def _blueprint(self) -> str:
        path = self.run.dir / "blueprint.md"
        if not path.exists():
            raise SystemExit(
                f"No blueprint exists for '{self.run.slug}'. Complete `claudex run` first."
            )
        return path.read_text(encoding="utf-8", errors="replace")

    def plan(self, force: bool = False) -> list[dict]:
        if self.product.get("plan") and not force:
            return self.product["plan"]
        assignment = self.router.assign(
            "propose", self.router.author_vendor(1), "deep",
            why="turning the accepted blueprint into an executable task graph",
        )
        system = """CLAUDEX-ROLE: product-plan
You are the lead engineer converting an accepted project blueprint into a small,
complete implementation plan. Return JSON only. Tasks must be independently
reviewable, dependency ordered, and collectively produce a runnable MVP. Do not
include deployment, prose-only, or project-management tasks.
Schema: {"tasks":[{"id":"T001","title":"...","objective":"...",
"acceptance":["observable check"],"depends_on":[]}]}"""
        user = f"## Brief\n{self.run.brief}\n\n## Constraints\n{self.run.constraints}\n\n## Blueprint\n{truncate(self._blueprint(), 100_000)}"
        payload = _json_call(
            self.pool, self.run, self.registry, assignment, system, user, "product_plan"
        )
        tasks = _task_order(_clean_plan(payload))
        self.product.update({
            "status": "planned", "workspace": "product", "plan": tasks,
            "tasks": {}, "planned_at": now_iso(),
        })
        self.run.save()
        return tasks

    def _implement(self, task: dict, index: int) -> tuple[list[dict], str, str]:
        author = self.router.author_vendor(index)
        critic = self.router.critic_vendor(author)
        author_assignment = self.router.assign(
            "propose", author, "standard", why=f"implementing product task {task['id']}"
        )
        snapshot = _workspace_snapshot(self.workspace)
        system = """CLAUDEX-ROLE: product-implement
You are a senior implementation engineer. Return JSON only. Produce complete
file contents, never diffs, ellipses, or instructions for the user. Change only
files required by this task. Paths must be relative and portable.
Schema: {"files":[{"path":"relative/path","content":"complete contents"}],
"notes":"short implementation note"}"""
        user = (
            f"## Product\n{self.run.brief}\n\n## Task\n{json.dumps(task, indent=2)}"
            f"\n\n## Current workspace\n{snapshot}"
        )
        candidate = _json_call(
            self.pool, self.run, self.registry, author_assignment,
            system, user, "product_implement",
        )
        files = _clean_bundle(candidate, self.workspace)

        critic_assignment = self.router.assign(
            "critique", critic, "standard", why=f"reviewing product task {task['id']}"
        )
        review_system = """CLAUDEX-ROLE: product-review
Review the proposed files against the task and existing workspace. Find real
correctness, integration, security, and acceptance failures. Return JSON only:
{"approved":true,"issues":[{"severity":"blocker|major|minor",
"problem":"...","fix":"..."}]}"""
        review_user = (
            f"## Task\n{json.dumps(task, indent=2)}\n\n## Existing workspace\n{snapshot}"
            f"\n\n## Proposed files\n{truncate(json.dumps(candidate, ensure_ascii=False), 100_000)}"
        )
        review = _json_call(
            self.pool, self.run, self.registry, critic_assignment,
            review_system, review_user, "product_review",
        )
        issues = review.get("issues") if isinstance(review.get("issues"), list) else []
        approved = bool(review.get("approved")) and not any(
            str(issue.get("severity", "")).lower() in {"blocker", "major"}
            for issue in issues if isinstance(issue, dict)
        )
        if not approved:
            revise_assignment = self.router.assign(
                "revise", author, "standard", why=f"fixing review findings for {task['id']}"
            )
            revise_system = """CLAUDEX-ROLE: product-revise
Fix every valid review issue. Return JSON only with complete replacement file
contents: {"files":[{"path":"relative/path","content":"complete contents"}],
"notes":"what changed"}. Preserve correct behavior in the candidate."""
            revise_user = (
                f"## Task\n{json.dumps(task, indent=2)}\n\n## Candidate\n"
                f"{truncate(json.dumps(candidate, ensure_ascii=False), 90_000)}"
                f"\n\n## Review\n{json.dumps(review, ensure_ascii=False)}"
            )
            candidate = _json_call(
                self.pool, self.run, self.registry, revise_assignment,
                revise_system, revise_user, "product_revise",
            )
            files = _clean_bundle(candidate, self.workspace)
        return files, author, critic

    def _repair(self, task: dict, author: str, verification: dict) -> list[dict]:
        assignment = self.router.assign(
            "revise", author, "standard", why=f"repairing failed verification for {task['id']}"
        )
        system = """CLAUDEX-ROLE: product-repair
The product failed its human-approved verification command. Diagnose the
failure and return JSON only with complete contents for every file that must be
created or replaced. Never propose a different command and never weaken tests.
Schema: {"files":[{"path":"relative/path","content":"complete contents"}],
"notes":"root cause and fix"}"""
        user = (
            f"## Task\n{json.dumps(task, indent=2)}\n\n## Verification failure\n"
            f"{json.dumps(verification, ensure_ascii=False)}\n\n## Current workspace\n"
            f"{_workspace_snapshot(self.workspace)}"
        )
        payload = _json_call(
            self.pool, self.run, self.registry, assignment,
            system, user, "product_repair",
        )
        return _clean_bundle(payload, self.workspace)

    def build(self, force_plan: bool = False, force_tasks: bool = False) -> dict:
        tasks = self.plan(force=force_plan)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.product["status"] = "building"
        self.run.save()
        for index, task in enumerate(tasks, 1):
            saved = self.product.setdefault("tasks", {}).get(task["id"], {})
            if saved.get("status") == "accepted" and not force_tasks:
                self.console.dim(f"{task['id']} already accepted - skipped")
                continue
            self.console.step(f"{task['id']} {task['title']}")
            files, author, critic = self._implement(task, index)
            written = _write_bundle(self.workspace, files)
            verification = _verify(self.workspace, self.command, written)
            if not verification["ok"]:
                self.console.warn(f"{task['id']} verification failed - one repair attempt")
                repair = self._repair(task, author, verification)
                repaired = _write_bundle(self.workspace, repair)
                written = list(dict.fromkeys([*written, *repaired]))
                verification = _verify(self.workspace, self.command, written)
            status = "accepted" if verification["ok"] else "needs_work"
            self.product["tasks"][task["id"]] = {
                "status": status, "title": task["title"], "files": written,
                "author": author, "critic": critic,
                "verification": verification, "updated": now_iso(),
            }
            self.run.save()
            if not verification["ok"]:
                self.product["status"] = "needs_work"
                self.run.save()
                raise ProviderError(
                    f"Verification failed after {task['id']}: "
                    f"{verification.get('output') or verification}"
                )
            self.console.ok(f"{task['id']} accepted - {len(written)} file(s)")
        self.product["status"] = "complete"
        self.product["completed_at"] = now_iso()
        self.run.save()
        return {
            "status": "complete", "tasks": len(tasks),
            "workspace": str(self.workspace),
            "verification": "command" if self.command else "structural",
        }
