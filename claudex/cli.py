"""ClaudeX command line: init, run, build, status, cost, models, runs."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import assembler, profiler, report
from .config import ModelRegistry, Settings, load_dotenv
from .debate import DebateEngine, projected_calls
from .phases import execution_order, load_phases, parse_selection, write_phase_files
from .providers import ProviderError, ProviderPool, QuotaExceeded
from .product import ProductEngine
from .router import Router
from .state import Run
from .util import Console, human_duration, score_text

BANNER = "ClaudeX - two models argue, you get the blueprint"

# Rough per-call token assumptions for the pre-run cost estimate.
EST_IN, EST_OUT = 6000, 2200


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", dest="run_slug", help="run slug (default: most recent)")
    parser.add_argument("--quiet", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claudex", description=BANNER)
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="start a new project run")
    init.add_argument("idea", nargs="+", help="one-line description of the project")
    init.add_argument("--name", help="short project name (default: derived from the idea)")
    init.add_argument("--constraints", default="",
                      help="hard limits: team size, deadline, budget, required stack")
    init.add_argument("--write-phases", action="store_true",
                      help="write the 25 phases to config/phases/ so you can edit them")
    init.add_argument("--quiet", action="store_true")

    create = sub.add_parser(
        "create", help="one prompt -> accepted blueprint -> runnable product"
    )
    create.add_argument("idea", nargs="+", help="one-line description of the product")
    create.add_argument("--name", help="short project name (default: derived from the idea)")
    create.add_argument("--constraints", default="", help="hard product constraints")
    create.add_argument("--rounds", type=int, default=2)
    create.add_argument("--accept-score", type=float, default=80.0)
    create.add_argument("--lead", default="alternate")
    create.add_argument("--via", choices=["auto", "api", "cli"], default="auto")
    create.add_argument("--project-dir", default="",
                      help="directory the CLI transport's agent reads while planning. Point it at the codebase you are designing for. Defaults to the run's own directory, NOT the ClaudeX repo.")
    create.add_argument("--offline", action="store_true")
    create.add_argument("--verify-command", default="",
                        help="human-approved command run without a shell in the product workspace")
    create.add_argument("--yes", "-y", action="store_true")
    create.add_argument("--quiet", action="store_true")

    run = sub.add_parser("run", help="debate the phases")
    _common(run)
    run.add_argument("--phases", default="all", help="e.g. 1-9,12,20 (default: all)")
    run.add_argument("--rounds", type=int, default=2, help="max debate rounds per phase")
    run.add_argument("--accept-score", type=float, default=80.0,
                     help="rubric score needed to accept a section (default 80)")
    run.add_argument("--lead", default="alternate",
                     help="force an author vendor (claude|gpt) instead of alternating")
    run.add_argument("--via", choices=["auto", "api", "cli"], default="auto",
                     help="api = paid API keys; cli = your Claude/Codex "
                          "subscription via the local CLIs; auto picks (default)")
    run.add_argument("--offline", action="store_true",
                     help="deterministic mock provider - no API calls, no spend")
    run.add_argument("--no-profile", action="store_true",
                     help="skip project profiling; run all 25 default phases as-is")
    run.add_argument("--reprofile", action="store_true",
                     help="re-run project profiling even if a plan already exists")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--project-dir", default="",
                     help="directory the CLI transport's agent reads while planning. Point it at the codebase you are designing for. Defaults to the run's own directory, NOT the ClaudeX repo.")
    run.add_argument("--no-dual-judge", action="store_true",
                     help="skip the second judge on borderline scores")
    run.add_argument("--force", action="store_true", help="redo phases already accepted")
    run.add_argument("--yes", "-y", action="store_true", help="skip the cost confirmation")
    run.add_argument("--no-build", action="store_true", help="do not assemble afterwards")

    build = sub.add_parser("build", help="assemble blueprint.md and report.html")
    _common(build)
    build.add_argument("--offline", action="store_true")
    build.add_argument("--via", choices=["auto", "api", "cli"], default="auto")
    build.add_argument("--no-summary", action="store_true",
                       help="skip the model-written executive summary")
    build.add_argument("--project-dir", default="",
                       help="directory the CLI transport's agent reads (the "
                            "executive summary is a model call). Defaults to "
                            "the run's own directory.")

    make = sub.add_parser("make", help="build a runnable product from an accepted blueprint")
    _common(make)
    make.add_argument("--via", choices=["auto", "api", "cli"], default="auto")
    make.add_argument("--project-dir", default="",
                      help="directory the CLI transport's agent reads while planning. Point it at the codebase you are designing for. Defaults to the run's own directory, NOT the ClaudeX repo.")
    make.add_argument("--offline", action="store_true")
    make.add_argument("--lead", default="alternate")
    make.add_argument("--verify-command", default="",
                      help="human-approved command run without a shell in the product workspace")
    make.add_argument("--allow-incomplete", action="store_true",
                      help="build from a partial blueprint (not recommended)")
    make.add_argument("--force-plan", action="store_true")
    make.add_argument("--force-tasks", action="store_true")
    make.add_argument("--no-cache", action="store_true")
    make.add_argument("--yes", "-y", action="store_true")

    status = sub.add_parser("status", help="show phase progress")
    _common(status)

    cost = sub.add_parser("cost", help="show the token and spend ledger")
    _common(cost)

    models = sub.add_parser("models", help="show the model registry and key status")
    models.add_argument("--probe", action="store_true",
                        help="call each provider to verify the configured model IDs exist")
    models.add_argument("--via", choices=["auto", "api", "cli"], default="api")
    models.add_argument("--quiet", action="store_true")

    sub.add_parser("runs", help="list all runs").add_argument(
        "--quiet", action="store_true"
    )
    sub.add_parser("setup", help="check what is configured and what is missing").add_argument(
        "--quiet", action="store_true"
    )
    return p


# ------------------------------------------------------------ commands ----


def cmd_init(args, settings, console) -> int:
    idea = " ".join(args.idea).strip()
    name = args.name or " ".join(idea.split()[:7])
    run = Run.create(settings, name=name, brief=idea, constraints=args.constraints)
    console.head(f"Run '{run.slug}' ready")
    console.ok(f"brief saved to runs/{run.slug}/brief.md")
    if args.write_phases:
        written = write_phase_files(settings)
        console.ok(f"{written} phase file(s) written to config/phases/ - edit freely")
    console.say()
    console.say("Next:")
    console.say(f"  python -m claudex run --offline      # free dry run, no keys needed")
    console.say(f"  python -m claudex run                # real debate, needs API keys")
    return 0


def cmd_create(args, settings, console) -> int:
    """Execute the complete one-prompt planning and implementation workflow."""
    idea = " ".join(args.idea).strip()
    name = args.name or " ".join(idea.split()[:7])
    run = Run.create(settings, name=name, brief=idea, constraints=args.constraints)
    console.head(f"Creating {run.name}")
    console.ok(f"brief saved to runs/{run.slug}/brief.md")
    run_args = argparse.Namespace(
        run_slug=run.slug, quiet=args.quiet, phases="all", rounds=args.rounds,
        accept_score=args.accept_score, lead=args.lead, via=args.via,
        offline=args.offline, no_profile=False, reprofile=False, no_cache=False,
        no_dual_judge=False, force=False, yes=args.yes, no_build=False,
        project_dir=args.project_dir,
    )
    result = cmd_run(run_args, settings, console)
    if result:
        return result
    make_args = argparse.Namespace(
        run_slug=run.slug, quiet=args.quiet, via=args.via, offline=args.offline,
        lead=args.lead, verify_command=args.verify_command,
        allow_incomplete=False, force_plan=False, force_tasks=False,
        no_cache=False, yes=args.yes, project_dir=args.project_dir,
    )
    return cmd_make(make_args, settings, console)


def _estimate(registry, router, phases, rounds) -> float:
    total = 0.0
    for phase in phases:
        profile = phase.get("profile", "standard")
        author = router.author_vendor(phase["id"])
        critic = router.critic_vendor(author)
        for rnd in range(rounds):
            for role, vendor in (
                ("propose", author), ("critique", critic),
                ("revise", author), ("judge", critic),
            ):
                if role == "propose" and rnd > 0:
                    continue
                tier = router.tier_for(role, profile)
                total += registry.price(vendor, tier, EST_IN, EST_OUT)
    return total


def pin_project_dir(registry, run, requested: str, transport: str, offline: bool,
                    console) -> None:
    """Decide which directory the vendor CLIs run in, and say it out loud.

    `claude -p` and `codex exec` read their working directory, so this choice
    decides which source tree ends up quoted in the blueprint. Defaulting to
    os.getcwd() silently pointed every run at the ClaudeX repo itself, so the
    default is now the run's own directory and anything else is explicit.
    """
    if requested:
        target = Path(requested).expanduser().resolve()
        if not target.is_dir():
            raise SystemExit(f"--project-dir does not exist: {target}")
    else:
        target = run.dir.resolve()
    registry.set_cli_cwd(target)
    if transport == "cli" and not offline:
        console.say(f"  reading:  {target}")
        if not requested:
            console.dim("pass --project-dir to point the agents at an existing codebase")


def resolve_transport(requested: str, registry, console) -> tuple[str, bool]:
    """Decide between API keys and subscription CLIs. Returns (transport, offline)."""
    has_keys = bool(registry.live_vendors("api"))
    has_clis = bool(registry.live_vendors("cli"))

    if requested == "api":
        if not has_keys:
            console.warn("--via api but no API keys found - falling back to offline mock.")
            return "api", True
        return "api", False

    if requested == "cli":
        if not has_clis:
            console.warn("--via cli but neither `claude` nor `codex` is on PATH.")
            console.warn("Install them, or use --via api with keys in .env.")
            return "cli", True
        return "cli", False

    # auto: prefer paid API (faster, no quota), fall back to subscriptions.
    if has_keys:
        return "api", False
    if has_clis:
        console.say("  No API keys - using your subscription CLIs (slower, quota-metered).")
        return "cli", False
    console.warn("No API keys and no CLIs found - falling back to offline mock mode.")
    console.warn("Output will be structural placeholder text, not a real design.")
    console.warn("Fix with:  keys in .env (--via api)  or  claude login (--via cli)")
    return "api", True


def cmd_run(args, settings, console) -> int:
    settings.use_cache = not args.no_cache
    probe = ModelRegistry.load(settings)
    transport, offline = resolve_transport(args.via, probe, console)
    if args.offline:
        offline = True
    registry = ModelRegistry.load(settings, transport=transport)

    run = Run.load(settings, args.run_slug)
    pin_project_dir(registry, run, args.project_dir, transport, offline, console)
    router = Router(registry, lead=args.lead, offline=offline)
    pool = ProviderPool(registry, settings, offline=offline, console=console)

    all_phases = load_phases(settings)

    # Reshape the blueprint to the project before selecting anything.
    plan = run.data.get("plan")
    if args.no_profile:
        plan = None
    elif plan is None or args.reprofile:
        console.head("Profiling the project")
        plan = profiler.profile(
            run, all_phases, router, pool, registry, console, offline=offline
        )
    tailored = profiler.apply_plan(all_phases, plan)

    selected = parse_selection(args.phases, [p["id"] for p in tailored])
    ordered = execution_order(tailored, selected)
    if not args.force:
        pending = [p for p in ordered if not run.is_done(p["id"])]
        skipped = len(ordered) - len(pending)
        if skipped:
            console.say(f"Skipping {skipped} already-accepted phase(s). Use --force to redo.")
        ordered = pending
    if not ordered:
        console.ok("Every selected phase is already accepted. Nothing to do.")
        return 0

    console.head(f"{run.name}")
    console.say(f"  mode:     {router.describe()}")
    console.say(f"  transport:{'  offline mock' if offline else '  ' + transport}")
    console.say(f"  phases:   {len(ordered)} ({', '.join(str(p['id']) for p in ordered)})")
    console.say(f"  rounds:   up to {args.rounds}, accept at {args.accept_score:.0f}/100")

    if not offline and transport == "cli":
        typical, worst = projected_calls(
            len(ordered), args.rounds, dual_judge=not args.no_dual_judge
        )
        console.say(f"  quota:    ~{typical} calls typical, {worst} worst case, "
                    f"cap {pool.max_calls} (config/models.json)")
        if worst > pool.max_calls:
            per_phase = max(1, worst // max(1, len(ordered)))
            fits = pool.max_calls // per_phase
            console.warn(
                f"This run can exceed the {pool.max_calls}-call cap and would stop "
                f"around phase {fits} of {len(ordered)}."
            )
            console.warn(
                f"Either raise cli.max_calls_per_run in config/models.json, or run "
                f"--rounds 1, or --phases 1-{max(1, fits)} and resume afterwards."
            )
        console.warn("Subscription plans meter usage in rolling windows. Finished "
                     "phases are saved, so a stop mid-run costs you nothing.")
        checks = pool.preflight(router.live)
        for vendor, status in checks.items():
            (console.err if status.startswith("FAILED") else console.dim)(
                f"{vendor} cli: {status}"
            )
        if any(s.startswith("FAILED") for s in checks.values()):
            console.err("Fix the CLI sign-in above, then rerun.")
            return 2

    if not offline and transport == "api":
        estimate = _estimate(registry, router, ordered, args.rounds)
        console.say(f"  estimate: ~${estimate:.2f} worst case (cache hits cost nothing)")

    if not offline and not args.yes and sys.stdin.isatty():
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            console.say("Cancelled.")
            return 1

    engine = DebateEngine(
        run=run, router=router, pool=pool, registry=registry, console=console,
        max_rounds=max(1, args.rounds), accept_score=args.accept_score,
        dual_judge=not args.no_dual_judge,
    )

    started = time.time()
    completed = 0
    try:
        for phase in ordered:
            engine.run_phase(phase)
            completed += 1
    except KeyboardInterrupt:
        console.warn("\nInterrupted. Finished phases are saved - rerun to continue.")
        return 130
    except QuotaExceeded as exc:
        console.warn(str(exc))
        completed = completed  # phases already saved by the engine
    except ProviderError as exc:
        console.err(str(exc))
        console.warn("Finished phases are saved. Fix the error and rerun to continue.")
        return 2

    totals = run.totals()
    console.head(
        f"Done: {completed} phase(s) in {human_duration(time.time() - started)}, "
        f"{totals['calls']} model calls, ${totals['cost']:.4f}"
    )

    if not args.no_build:
        _do_build(run, tailored, router, pool, registry, console, write_summary=True)
    return 0


def _do_build(run, phases, router, pool, registry, console, write_summary: bool):
    result = assembler.build(
        run, phases, router, pool, registry, console, write_summary=write_summary
    )
    html_path = report.build(run, phases)
    report.write_summary_json(run, phases)
    console.ok(f"report.html - open it in a browser")
    console.say()
    console.say(f"  blueprint: {result['path']}")
    console.say(f"  report:    {html_path}")
    return result


def cmd_build(args, settings, console) -> int:
    probe = ModelRegistry.load(settings)
    transport, offline = resolve_transport(args.via, probe, console)
    offline = offline or args.offline
    registry = ModelRegistry.load(settings, transport=transport)
    run = Run.load(settings, args.run_slug)
    pin_project_dir(registry, run, getattr(args, "project_dir", ""), transport,
                    offline, console)
    router = Router(registry, offline=offline)
    pool = ProviderPool(registry, settings, offline=offline, console=console)
    _do_build(
        run, profiler.apply_plan(load_phases(settings), run.data.get("plan")),
        router, pool, registry, console,
        write_summary=not args.no_summary,
    )
    return 0


def cmd_make(args, settings, console) -> int:
    """Build files from an accepted blueprint with adversarial review."""
    settings.use_cache = not args.no_cache
    probe = ModelRegistry.load(settings)
    transport, offline = resolve_transport(args.via, probe, console)
    offline = offline or args.offline
    registry = ModelRegistry.load(settings, transport=transport)
    run = Run.load(settings, args.run_slug)
    phases = profiler.apply_plan(load_phases(settings), run.data.get("plan"))
    pending = [phase["id"] for phase in phases if not run.is_done(phase["id"])]
    if pending and not args.allow_incomplete:
        console.err(
            f"Blueprint is incomplete: {len(pending)} phase(s) remain "
            f"({', '.join(str(pid) for pid in pending)}). Resume `claudex run` first."
        )
        return 2
    pin_project_dir(registry, run, args.project_dir, transport, offline, console)
    router = Router(registry, lead=args.lead, offline=offline)
    pool = ProviderPool(registry, settings, offline=offline, console=console)
    console.head(f"Building product for {run.name}")
    console.say(f"  mode:      {router.describe()}")
    console.say(f"  workspace: {run.dir / 'product'}")
    if not offline and transport == "cli":
        checks = pool.preflight(router.live)
        for vendor, status in checks.items():
            (console.err if status.startswith("FAILED") else console.dim)(
                f"{vendor} cli: {status}"
            )
        if any(status.startswith("FAILED") for status in checks.values()):
            return 2
    if not offline and transport == "api" and not args.yes and sys.stdin.isatty():
        answer = input("\nProduct generation uses paid API tokens. Proceed? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            console.say("Cancelled.")
            return 1
    engine = ProductEngine(
        run, router, pool, registry, console, verify_command=args.verify_command
    )
    result = engine.build(force_plan=args.force_plan, force_tasks=args.force_tasks)
    console.ok(f"product complete - {result['tasks']} task(s)")
    console.say(f"  product: {result['workspace']}")
    console.say(f"  verification: {result['verification']}")
    return 0


def cmd_status(args, settings, console) -> int:
    run = Run.load(settings, args.run_slug)
    phases = profiler.apply_plan(load_phases(settings), run.data.get("plan"))
    console.head(f"{run.name}  ({run.slug})")
    done = 0
    for phase in phases:
        meta = run.phase(phase["id"])
        status = meta.get("status")
        if status == "accepted":
            mark, done = "OK  ", done + 1
        elif status == "needs_work":
            mark = "GAP "
        else:
            mark = "--  "
        detail = ""
        if status:
            detail = (
                f"{score_text(meta.get('score'), 3)}/100  "
                f"{meta.get('rounds', 0)}r  "
                f"{meta.get('author', '?')} vs {meta.get('critic', '?')}"
            )
        console.say(f"  {mark}{phase['id']:>2}. {phase['title']:<38} {detail}")
    totals = run.totals()
    console.say()
    console.say(
        f"  {done}/{len(phases)} accepted   "
        f"{totals['calls']} calls   ${totals['cost']:.4f}"
    )
    product = run.data.get("product") or {}
    if product:
        tasks = product.get("tasks") or {}
        accepted_tasks = sum(1 for task in tasks.values() if task.get("status") == "accepted")
        console.say(
            f"  product: {product.get('status', 'unknown')}   "
            f"{accepted_tasks}/{len(product.get('plan') or [])} tasks accepted"
        )
    return 0


def cmd_cost(args, settings, console) -> int:
    run = Run.load(settings, args.run_slug)
    totals = run.totals()
    console.head(f"Spend for {run.name}")
    if not totals["by_model"]:
        console.say("  No model calls recorded yet.")
        return 0
    console.say(f"  {'model':<34}{'calls':>7}{'in':>11}{'out':>10}{'cost':>10}")
    for label, stats in sorted(totals["by_model"].items()):
        name = f"{label} {stats['model']}"[:33]
        console.say(
            f"  {name:<34}{stats['calls']:>7}{stats['tokens_in']:>11,}"
            f"{stats['tokens_out']:>10,}{stats['cost']:>10.4f}"
        )
    console.say()
    console.say(
        f"  total {totals['calls']} calls, {totals['tokens_in']:,} in / "
        f"{totals['tokens_out']:,} out, ${totals['cost']:.4f} "
        f"({totals['cached_calls']} cached)"
    )
    if totals["cost_is_estimate"]:
        console.warn(
            f"{totals['offline_calls']} of {totals['calls']} calls ran offline. "
            "Nothing was spent - that figure is a forecast for the same run with keys."
        )
    return 0


def cmd_models(args, settings, console) -> int:
    transport = "api" if args.via == "auto" else args.via
    registry = ModelRegistry.load(settings, transport=transport)
    console.head(f"Model registry - {transport} transport  (edit config/models.json)")
    for vendor in registry.vendors:
        if transport == "cli":
            cmd = " ".join(registry.cli_command(vendor))
            state = "on PATH" if registry.cli_available(vendor) else "NOT INSTALLED"
            console.say(f"\n  {vendor}  [{state}]  `{cmd}`")
        else:
            key = "key set" if registry.has_key(vendor) else "NO KEY"
            console.say(f"\n  {vendor}  [{key}]  {registry.base_url(vendor)}")
        for tier in registry.vendor(vendor).get("tiers", {}):
            spec = registry.tier_spec(vendor, tier)
            price = (
                "subscription - no per-call charge" if transport == "cli"
                else f"${spec.get('price_in', 0):.2f}/"
                     f"${spec.get('price_out', 0):.2f} per 1M"
            )
            console.say(f"    {tier:<9} {registry.model_id(vendor, tier):<30} {price}")
    console.say("\n  Routing (role -> tier by phase profile):")
    for role, table in registry.routing.items():
        if role.startswith("_"):
            continue
        console.say(f"    {role:<11}" + "  ".join(f"{k}={v}" for k, v in table.items()))

    if args.probe:
        console.head("Probing providers")
        pool = ProviderPool(registry, settings, offline=False, console=console)
        for vendor in registry.vendors:
            if transport == "api" and not registry.has_key(vendor):
                console.warn(f"{vendor}: no API key, skipped")
                continue
            if transport == "cli" and not registry.cli_available(vendor):
                hint = registry.transport_spec(vendor, "cli").get("login_hint", "")
                console.warn(f"{vendor}: CLI not installed. {hint}")
                continue
            try:
                provider = pool.get(vendor)
                if transport == "cli":
                    console.ok(f"{vendor} cli: {provider.check()}")
                    continue
                available = set(provider.list_models())
                for tier in registry.vendor(vendor).get("tiers", {}):
                    model = registry.model_id(vendor, tier)
                    if model in available:
                        console.ok(f"{vendor}:{tier} -> {model}")
                    else:
                        console.err(
                            f"{vendor}:{tier} -> {model} NOT in the account's "
                            f"model list. Edit config/models.json."
                        )
            except Exception as exc:
                console.err(f"{vendor}: probe failed - {str(exc)[:160]}")
    return 0


def cmd_runs(args, settings, console) -> int:
    runs = Run.list_all(settings)
    if not runs:
        console.say('No runs yet. Try: python -m claudex init "Your project idea"')
        return 0
    console.head(f"{len(runs)} run(s)")
    for item in runs:
        console.say(
            f"  {item['slug']:<34} {item['phases_done']:>2} accepted  "
            f"${item['cost']:.3f}  {item['created'][:10]}"
        )
    return 0


def cmd_setup(args, settings, console) -> int:
    """One place that answers: can I run this, and if not, what do I type?"""
    registry = ModelRegistry.load(settings)
    todo: list[str] = []

    console.head("API transport  (pay per token)")
    for vendor in registry.vendors:
        env = registry.vendor(vendor).get("api_key_env", "")
        if registry.has_key(vendor):
            console.ok(f"{vendor}: {env} is set")
        else:
            console.warn(f"{vendor}: {env} not set")
            todo.append(f"add {env} to .env  (needs paid credits, not a subscription)")

    console.head("CLI transport  (your subscription)")
    cli_ready = []
    for vendor in registry.vendors:
        spec = registry.transport_spec(vendor, "cli")
        exe = registry.cli_command(vendor)[0]
        if not registry.cli_available(vendor):
            console.err(f"{vendor}: `{exe}` not on PATH")
            todo.append(spec.get("install_hint") or f"install the {exe} CLI")
            continue
        try:
            pool = ProviderPool(
                ModelRegistry.load(settings, transport="cli"), settings,
                offline=False, console=console,
            )
            version = pool.get(vendor).check()
            console.ok(f"{vendor}: {exe} present ({version})")
            cli_ready.append(vendor)
        except Exception as exc:
            console.err(f"{vendor}: {str(exc)[:120]}")
            todo.append(spec.get("login_hint", f"{exe} login"))

    console.head("Verdict")
    if registry.live_vendors("api"):
        console.ok("API transport is configured - `python -m claudex run --via api`")
    if len(cli_ready) >= 2:
        console.ok("Both CLIs present - `python -m claudex run --via cli`")
    elif len(cli_ready) == 1:
        console.warn(
            f"Only {cli_ready[0]} CLI is ready. It will run single-vendor "
            "(self-critique, weaker). Sign in to the other for real debate."
        )
    if not registry.live_vendors("api") and not cli_ready:
        console.warn("Nothing is configured - runs will fall back to offline mock output.")

    if todo:
        console.say()
        console.say("  Do this:")
        for item in dict.fromkeys(todo):
            console.say(f"    - {item}")
    console.say()
    console.say("  Sign-in happens in your browser, so run those yourself.")
    return 0


COMMANDS = {
    "setup": cmd_setup,
    "init": cmd_init, "create": cmd_create, "run": cmd_run,
    "build": cmd_build, "make": cmd_make, "status": cmd_status,
    "cost": cmd_cost, "models": cmd_models, "runs": cmd_runs,
}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    console = Console(quiet=getattr(args, "quiet", False))
    settings = Settings()
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    try:
        return COMMANDS[args.command](args, settings, console)
    except KeyboardInterrupt:
        console.warn("\nInterrupted.")
        return 130
    except ProviderError as exc:
        console.err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
