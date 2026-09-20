# ClaudeX

**Two AI models argue about your project. You get the blueprint.**

You give ClaudeX a one-line idea. It walks the project through 25 engineering
phases — problem definition, dataset, model, security, testing, deployment,
viva prep — and in every phase:

1. **One model writes** the section
2. **A model from a different vendor attacks it** — adversarially, by design
3. **The author revises**, fixing blockers and rejecting bad notes with reasons
4. **A judge that didn't write it scores** the result against a fixed rubric
5. **Below the threshold?** Another round, on a stronger model

The output is `blueprint.md` — a full project specification where every section
has survived cross-examination — plus an HTML report showing which model wrote
what, how it scored, and what it cost.

No dependencies. Python 3.10+. Pure standard library.

---

## Table of contents

- [Quick start](#quick-start)
- [Why two vendors](#why-two-vendors)
- [Works for any project type](#works-for-any-project-type)
- [Two ways to pay](#two-ways-to-pay)
- [Automatic model selection](#automatic-model-selection)
- [Commands](#commands)
- [What you get](#what-you-get)
- [The rubric — and what the scores are not](#the-rubric--and-what-the-scores-are-not)
- [Customising the phases](#customising-the-phases)
- [Cost control](#cost-control)
- [Troubleshooting](#troubleshooting)
- [How it's built](#how-its-built)
- [Limits](#limits)
- [Security](#security)

---

## Quick start

### 1. Try it free, right now

No keys, no setup, no spend. The full pipeline runs against a built-in mock:

```bash
python -m claudex init "A web app that predicts crop disease from leaf photos"
```

```bash
python -m claudex run --offline
```

You'll watch the debate happen and get a complete `blueprint.md`. The *content*
is placeholder text — but every moving part is real, so you can see exactly
what a paid run would produce and what it would cost.

### 2. Check what's configured

```bash
python -m claudex setup
```

This tells you what works, what's missing, and the exact command to fix it.

### 3. Run it for real

```bash
python -m claudex run
```

> **Windows / PowerShell note:** PowerShell 5.1 doesn't support `&&`. Use `;`
> to chain commands, or just run them one at a time.

---

## Why two vendors

A model reviewing its own draft agrees with itself. That's the core problem
with single-model planning: it produces confident, well-formatted prose that
decides nothing and has never been contradicted.

ClaudeX forces disagreement **structurally**, not by asking nicely:

| Mechanism | What it does |
|---|---|
| **Alternating authorship** | Phase 1 written by Claude, phase 2 by GPT, phase 3 by Claude… Neither model's habits shape the whole document. |
| **Cross-vendor critique** | The critic is always the *other* vendor, prompted adversarially. Waving a draft through counts as a failed review. |
| **Independent judging** | The judge didn't write the final text. It scores against a rubric instead of rewriting. |
| **Dual judge on close calls** | Borderline scores get a second opinion from the opposite vendor, averaged. That's exactly where one model's bias matters most. |

**With one key or one CLI**, ClaudeX still runs — it tells you it's degraded to
self-critique. **With neither**, it runs fully offline against a deterministic
mock, so you can exercise everything for free before spending anything.

---

## Works for any project type

The 25 phases assume a data-driven web app. Most projects aren't that — so
before writing anything, ClaudeX **profiles your brief and reshapes the
blueprint**: it drops phases that don't apply, adds domain phases that are
missing, and annotates the ones that stay with how to read them.

These are real results from four runs:

| Project | Phases dropped | Phases added |
|---|---|---|
| Offline Unity puzzle game | dataset, model, recommender, backend, database, API | **Game Design & Core Loop**, **Mobile Platform Constraints** |
| ESP32 soil sensor firmware | dataset, model, recommender | **Hardware & Power Budget** |
| Sleep research paper | dataset, model, recommender, scalability, devops, monitoring | **Research Methodology & Reproducibility** |
| Log-rotation CLI tool | dataset, model, recommender, UI/UX, frontend | — |

The game blueprint came out as **21 phases, not 25**, with game design inserted
right after feature planning.

Dropping a phase beats letting a model pad it with filler to look thorough.

**Two guardrails** stop a bad profile from wrecking the blueprint: phase 1 can
never be dropped, and at least five phases always survive.

```bash
python -m claudex run --no-profile
```

```bash
python -m claudex run --reprofile
```

With keys, a model does the profiling. Offline, a keyword heuristic does it
less precisely — every row in that table came from the heuristic.

---

## Two ways to pay

|  | `--via api` | `--via cli` |
|---|---|---|
| **Needs** | API keys in `.env` | `claude` / `codex` signed in |
| **Cost** | ~$7 per full run | nothing — uses your subscription |
| **Speed** | seconds per call | minutes per call |
| **Limit** | your credit balance | your plan's rolling usage window |

`--via auto` (the default) prefers API keys and falls back to the CLIs.

### A subscription is not an API key

This trips up almost everyone: **Claude Pro and ChatGPT Plus/Pro do not include
API access.** The API is billed separately, pay-as-you-go. A valid API key with
no credits returns:

```
Your credit balance is too low to access the Anthropic API
You have no credits remaining
```

The `cli` transport exists precisely so a subscription *can* be used instead,
by shelling out to the official CLIs in non-interactive mode.

### Setting up CLI mode

```bash
claude login
```

```bash
npm install -g @openai/codex
```

```bash
codex login
```

Choose **"Sign in with ChatGPT"** — not the API-key option, or you're back to
needing credits.

```bash
python -m claudex setup
```

### The catch: quota

A full run is ~200 calls, and subscription plans meter usage in rolling
windows, so a full run **will** exhaust your quota. ClaudeX handles this
deliberately:

- throttles calls (`cli.delay_between_calls`)
- caps them per run (`cli.max_calls_per_run`, default 120)
- warns as you approach the cap
- saves every completed phase so you resume in the next window
- detects a spent quota and **stops instead of retrying** — it reads the reset
  time out of the error and tells you when to come back

For a subscription run, trim it:

```bash
python -m claudex run --via cli --phases 1,2,5,6,7,8,10 --rounds 1
```

That's ~28 calls instead of ~200.

---

## Automatic model selection

Two independent decisions, neither hardcoded anywhere in the code.

**Which vendor speaks** — turn-taking, as described above.

**How hard it thinks** — each phase carries a `profile`, each role maps that
profile to a tier, and `config/models.json` maps tiers to actual models:

| Profile | Phases | propose | critique | judge |
|---|---|---|---|---|
| `deep` | problem definition, backend, database, dataset, model, security, research, evaluation | deep | deep | balanced |
| `standard` | UI/UX, frontend, testing, API, DevOps, diagrams, docs | balanced | balanced | balanced |
| `light` | monitoring, analytics, code quality | fast | balanced | fast |

Security gets the strongest model available. Analytics doesn't. Judging runs
one tier below authoring, because scoring against a rubric is a much easier job
than designing — which keeps the bill down without weakening the debate.

**Escalation:** a phase scoring below 70 gets bumped one tier up the ladder
(`fast → balanced → deep`) for the next round, instead of retrying the same
model that already failed.

Every routing decision is recorded with its reason in `state.json`.

### Model IDs

`config/models.json` is the single source of truth. Model names change, and a
wrong name is a 404 at call time — so check before a real run:

```bash
python -m claudex models --probe
```

That lists each configured model and tells you whether your account can
actually see it. Edit the JSON; no code changes needed.

---

## Commands

| Command | What it does |
|---|---|
| `setup` | what works, what's missing, how to fix it |
| `init "<idea>"` | start a new project run |
| `run` | the debate |
| `build` | re-assemble outputs without re-debating |
| `status` | per-phase progress and scores |
| `cost` | token and spend ledger |
| `models` | model registry and key/CLI check |
| `runs` | list all runs |

```bash
python -m claudex init "<idea>" --name Short --constraints "team, deadline, budget"
```

### `run` options

| Flag | Meaning |
|---|---|
| `--via auto\|api\|cli` | transport: keys, subscription CLIs, or auto |
| `--phases 1-9,12` | run only these phases |
| `--rounds 2` | max debate rounds per phase |
| `--accept-score 80` | rubric score needed to accept a section |
| `--lead claude\|gpt` | force an author vendor instead of alternating |
| `--offline` | deterministic mock, no calls, no spend |
| `--no-profile` | skip profiling, run all 25 phases as-is |
| `--reprofile` | redo the profiling analysis |
| `--force` | redo phases already accepted |
| `--no-dual-judge` | skip second opinions on borderline scores |
| `--no-cache` | ignore the response cache |
| `--no-build` | don't assemble afterwards |
| `-y`, `--yes` | skip the cost confirmation |

### A typical session

Start the project:

```bash
python -m claudex init "Campus lost-and-found with image matching" --name FindIt --constraints "2 students, 8 weeks, free hosting, must work on 2G"
```

Free dry run first — see the whole pipeline, spend nothing:

```bash
python -m claudex run --offline
```

Real run on the expensive phases, three rounds each:

```bash
python -m claudex run --phases 1,2,5,6,7,8,10 --rounds 3
```

Cheap phases at lower effort:

```bash
python -m claudex run --phases 17,18,23 --rounds 1
```

Assemble the deliverables:

```bash
python -m claudex build
```

Runs are **resumable**. Interrupt at any point; completed phases are saved and
rerunning skips them. Every response is cached by prompt hash, so a rerun after
a crash replays for free.

---

## What you get

```
runs/<project>/
├── blueprint.md          ← the deliverable: all sections, provenance, open questions
├── report.html           ← scores, model attribution, spend
├── decisions.json        ← every decision ID, choice, rationale, author
├── open-questions.md     ← what the models couldn't settle without you
├── summary.json          ← machine-readable run stats
├── sections/NN-*.md      ← accepted section per phase
├── transcript/NN-*.json  ← full debate: draft, critique, revision, scores
└── state.json            ← resumable state + routing ledger
```

**The transcripts are the interesting part.** Every critique is preserved, so
you can read exactly what one model thought was wrong with the other's design —
and that argument is usually more useful than the polished section it produced.

`blueprint.md` opens with a provenance table:

| # | Phase | Author | Critic | Rounds | Score |
|---|---|---|---|---|---|
| 1 | Problem Definition & Requirements | claude | gpt | 2 | 91 |
| 2 | Feature Planning | gpt | claude | 2 | 93 |
| 3 | UI/UX Design | claude | gpt | 2 | 87 |

---

## The rubric — and what the scores are not

Judges score five dimensions 0–100:

| Dimension | Question it answers |
|---|---|
| **coverage** | Is the checklist genuinely addressed, or just name-checked? |
| **specificity** | Are choices concrete and testable, or hedged? |
| **consistency** | Does it agree with earlier locked decisions? |
| **feasibility** | Could the stated team actually build this? |
| **risk_handling** | Are real failure modes identified with mitigations? |

Default accept threshold is 80. A self-reported total that disagrees with its
own rubric mean by more than 5 points is discarded in favour of the mean —
models inflate summary scores more readily than individual ones.

### What the scores are not

They measure **how completely and concretely the plan is specified.** They are
*not* evidence that the plan is correct, that the tech choices suit your
situation, or that the timeline is realistic.

Two models agreeing is weak evidence — they share training data and failure
modes. Treat ClaudeX output as a strong first draft that a human must verify,
especially:

- **any number, benchmark, or citation** — prompts require a `[VERIFY]` tag on
  unverified claims, so grep for it
- **dataset licensing and availability**
- **anything security-related**, before it touches real user data

---

## Customising the phases

```bash
python -m claudex init "..." --write-phases
```

That writes all 25 to `config/phases/NN-slug.md` as editable markdown:

```markdown
---
id: 7
slug: dataset
title: Dataset Requirements
profile: deep
depends_on: 1, 2
---

## Goal
Define where data comes from and every quality gate it must pass.

## Checklist
- Dataset source
- Class imbalance
```

Files win over the built-in seed. Add phases, delete them, rewrite checklists,
change dependencies — `depends_on` drives both execution order and what context
each phase receives. Cycles and unknown IDs are caught at load time.

---

## Cost control

| Technique | Effect |
|---|---|
| `--offline` | costs nothing, exercises the full pipeline |
| `--via cli` | uses your subscription instead of credits |
| `--phases` | spend deeply only where it matters |
| `--rounds 1` | roughly halves the calls |
| `--no-dual-judge` | skips second opinions |
| disk cache | reruns are free |

`run` prints a worst-case estimate and asks before spending. `claudex cost`
breaks spend down per model.

Offline runs price mock traffic at real rates using typical response sizes, so
the figure is a usable forecast — and it's labelled as an estimate everywhere
it appears.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `The token '&&' is not a valid statement separator` | PowerShell 5.1 | Use `;`, or run commands separately |
| `Your credit balance is too low` | Valid key, no credits | Add credits, or use `--via cli` |
| `You have no credits remaining` | Same, OpenAI side | Same |
| `OAuth session expired` | CLI not signed in | `claude login` |
| `You've hit your usage limit` | Subscription quota spent | Wait for the reset time ClaudeX prints, or `--via api` |
| `NOT in the account's model list` | Wrong model ID | Edit `config/models.json` |
| `'claude' is not on PATH` | CLI not installed | Install it, or use `--via api` |
| Blueprint full of "Placeholder decision" | Ran offline | Configure a transport — run `claudex setup` |

---

## How it's built

```
claudex/
├── cli.py            # commands
├── router.py         # which model, which tier, and why
├── debate.py         # propose → critique → revise → judge
├── prompts.py        # role prompts — edit these to change debate quality
├── profiler.py       # reshapes the blueprint to the project's domain
├── phases.py         # load / write / order phases
├── phases_seed.py    # the 25 phases
├── assembler.py      # sections → blueprint.md
├── report.py         # HTML report
├── state.py          # resumable state + cost ledger
├── util.py           # JSON extraction, retry, console
└── providers/
    ├── anthropic_api.py   # Anthropic Messages API
    ├── openai_api.py      # OpenAI Chat Completions
    ├── cli_api.py         # subscription transport (claude -p, codex exec)
    └── mock.py            # deterministic offline provider
```

`prompts.py` is where output quality actually lives. If sections come out
vague, tighten the house rules there before touching anything else.

### Tests

```bash
python -m unittest discover -s tests -v
```

74 tests covering JSON extraction from messy model output, judge-score
sanitising, routing and escalation, phase dependency ordering, markdown
round-tripping, caching, resume, a full offline end-to-end run, both
transports, CLI auth and quota error handling, Windows shim resolution, call
capping, and profiler reshaping across game, firmware, research and CLI
projects.

---

## Limits

- **Phases run sequentially.** The dependency graph would allow parallelism;
  it isn't implemented.
- **A full 25-phase run at 2 rounds is ~200 model calls.** Estimate first.
- **Two vendors only.** A third would make the judge genuinely neutral rather
  than merely not-the-author.
- **The judge sees the section, not the debate.** It can't tell whether a
  critique was answered or dodged.
- **No web access**, so the research phase relies on training data. That's why
  it's prompted to tag claims `[VERIFY]` rather than cite.
- **The CLI transport reports no token usage**, so its ledger figures are
  estimates and its cost column is always zero.
- **The offline profiler is keyword-based** and will misread an unusual brief.
  Check what it dropped before a paid run; `--no-profile` disables it.

---

## Security

`.env` is gitignored and holds your keys. Never commit it, and never paste an
API key into a chat or an issue — a leaked key is billable by whoever finds it.
If one leaks, revoke it at
[console.anthropic.com](https://console.anthropic.com) or
[platform.openai.com/api-keys](https://platform.openai.com/api-keys).

On the CLI transport, ClaudeX **strips `ANTHROPIC_API_KEY` and
`OPENAI_API_KEY` from the subprocess environment** — otherwise the CLI silently
bills your API instead of the subscription, defeating the point. Codex also
runs with `--sandbox read-only`, so it can't modify files while writing your
blueprint.
