# ClaudeX

**Two AI models from different vendors argue through 25 engineering phases until the surviving text becomes one project blueprint — and then build it.**

You give it one line. Claude drafts a phase, GPT attacks the draft, Claude revises, GPT scores it against a rubric. Authorship alternates every phase, and the critic is always the *other* vendor, because a model reviewing its own output agrees with itself.

Zero third-party dependencies. Python 3.10+ standard library only. Runs on Windows and POSIX, offline against a mock, over subscription CLIs, or over paid APIs.

---

## Table of contents

- [Requirements](#requirements)
- [Setup](#setup)
- [How to make a project](#how-to-make-a-project)
- [How to run it](#how-to-run-it)
- [Command reference](#command-reference)
- [Transports: cli vs api vs offline](#transports-cli-vs-api-vs-offline)
- [Quota, cost and time](#quota-cost-and-time)
- [Statistics from real runs](#statistics-from-real-runs)
- [What you get out](#what-you-get-out)
- [Troubleshooting](#troubleshooting)
- [Known issues](#known-issues)

---

## Requirements

| | |
|---|---|
| Python | 3.10 or newer |
| Dependencies | none — standard library only |
| Platform | Windows, macOS, Linux |
| For `--via cli` | `claude` and `codex` on PATH, both signed in |
| For `--via api` | `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env` |
| For `--offline` | nothing |

You need **both** vendors for the tool to do what it exists to do. With one, it still runs — it tells you it has degraded to single-vendor and that the critique is self-critique.

---

## Setup

### 1. Check what you have

```
python -m claudex setup
```

This prints exactly which transports are ready and what is missing. Read it before anything else — it is the fastest way to find out you are about to run single-vendor.

Healthy output looks like:

```
CLI transport  (your subscription)
  OK claude: claude present (2.1.278 (Claude Code))
  OK gpt: codex present (0.155.1)

Verdict
  OK both CLIs ready - cross-vendor debate available
```

### 2. Sign in to both CLIs

Both open a browser. They cannot be automated.

```
claude login
codex login          # choose "Sign in with ChatGPT"
```

If `claude` is already running as a TUI, the command inside it is `/login` **with the slash** — typing `login` sends it to the model as a prompt.

### 3. Verify the real call paths

Do **not** trust `claudex setup` alone here. It checks that the binaries exist, not that they answer. Test the actual calls:

```
claude -p "Reply with exactly: AUTH_OK"
codex exec --skip-git-repo-check -s read-only --ephemeral "Reply with exactly: AUTH_OK"
```

Both must print `AUTH_OK`. If codex says you hit your usage limit, the window has not rolled over yet.

### 4. Optional — API keys

Only needed for `--via api`. Copy `.env.example` to `.env` and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

A subscription is **not** an API key. Being signed in to Claude or ChatGPT gives you no key; the `cli` transport exists precisely so a subscription can be used instead.

---

## How to make a project

Two routes. Pick by how much control you want.

### Route A — one command, start to finish

```
python -m claudex create "a browser-based 2D arcade space shooter" ^
  --constraints "solo dev; HTML/CSS/JS; offline-capable; no paid services" ^
  --via cli --rounds 1 --yes
```

`create` chains everything: profile the idea, debate every surviving phase, assemble the blueprint, then build a runnable product from it. Good when you trust it to go.

### Route B — step by step (recommended the first time)

```
python -m claudex init "a browser-based 2D arcade space shooter" ^
  --name "Space Attack" ^
  --constraints "solo dev; HTML/CSS/JS; offline-capable; no paid services"
```

`init` creates `runs/<slug>/` with a brief and a phase plan. Nothing is spent yet. Then run the debate, assemble, and build — see below.

### The phase plan

ClaudeX profiles your idea and drops phases that do not apply. It does not run all 25 blindly.

```
game  ->  23 phases   drops 7, 8 (no ML), 9 (no recommender), adds 26 "Game Design & Core Loop"
cli   ->  17 phases   drops 3, 4 (no GUI), 5, 6 (no server), 7, 8, 9, 15 (no API)
```

Use `--no-profile` to force all 25, or `--reprofile` to redo the decision.

---

## How to run it

### Debate the phases

```
python -m claudex run --run space-attack --via cli --rounds 1 --yes
```

Watch the header. It must say **cross-vendor**:

```
mode:       cross-vendor - authorship alternates across claude, gpt
transport:  cli
phases:     23
quota:      ~92 subscription calls, cap 120
```

If it says `single-vendor`, one CLI is missing and the critique is worthless as a quality signal. Stop and fix that first.

Each phase then prints who is doing what:

```
Phase 1: Problem Definition & Requirements  [deep]
  -> round 1/1 - claude writes, gpt attacks
     propose   claude:deep      claude-opus-5
     critique  gpt:deep         gpt-6-astra
     revise    claude:deep      claude-opus-5
     judge     gpt:balanced     gpt-5.6-terra
  -> score 89/100 -> ACCEPT
```

### Resume after it stops

It **will** stop — subscription windows are the normal failure. Nothing is lost. Completed phases are saved to `state.json` and the response cache keeps finished calls, so the same command picks up where it left off:

```
python -m claudex run --run space-attack --via cli --rounds 1 --yes
```

Re-running an accepted phase does nothing unless you pass `--force`.

### Assemble the document

```
python -m claudex build --run space-attack
```

Writes `blueprint.md`, `open-questions.md` and `report.html`. Add `--no-summary` to skip the model-written executive summary — useful when you are out of quota, since that summary costs one more call.

### Build the actual product

```
python -m claudex make --run space-attack --via cli --yes
```

Turns an accepted blueprint into a runnable product. Pass `--verify-command` to have it run a check you approve (run without a shell, in the product workspace). `--allow-incomplete` builds from a partial blueprint and is not recommended.

### Check where you are

```
python -m claudex status --run space-attack
python -m claudex cost   --run space-attack
python -m claudex runs
```

---

## Command reference

| Command | What it does |
|---|---|
| `init <idea>` | create a run, profile it, write the brief. Spends nothing. |
| `create <idea>` | init + run + build + make, in one go |
| `run` | debate the phases |
| `build` | assemble `blueprint.md` and `report.html` |
| `make` | build a runnable product from an accepted blueprint |
| `status` | phase progress and scores |
| `cost` | token and spend ledger |
| `models` | model registry and key/CLI check |
| `runs` | list all runs |
| `setup` | what is configured and what is missing |

**Flags that matter most**

| Flag | Applies to | Effect |
|---|---|---|
| `--run <slug>` | most | which run. **Must come after the subcommand.** |
| `--via auto\|api\|cli` | run, build, make, create | transport. `auto` prefers keys, falls back to CLIs |
| `--phases 1-9,12,20` | run | subset instead of all |
| `--rounds N` | run, create | max debate rounds per phase (default 2) |
| `--accept-score N` | run, create | rubric score needed to accept (default 80) |
| `--force` | run | redo phases already accepted |
| `--offline` | run, build, make, create | deterministic mock, no calls, no spend |
| `--project-dir <path>` | run, make, create | directory the CLI agents read while planning. Defaults to the run's folder, **not** the ClaudeX repo |
| `--lead claude\|gpt` | run, make, create | force one author instead of alternating |
| `--solo claude\|gpt` | make | one vendor writes *and* reviews, instead of cross-vendor review. Unblocks a build when the other vendor's window is spent — scores become self-assessed |
| `--no-dual-judge` | run | skip the second judge on borderline scores |
| `--yes` / `-y` | run, make, create | skip the confirmation prompt |

`--run` is a subcommand-level argument. `claudex --run x run` fails with `invalid choice`; `claudex run --run x` is correct.

---

## Transports: cli vs api vs offline

| | `--via cli` | `--via api` | `--offline` |
|---|---|---|---|
| Uses | your Claude / ChatGPT subscription | paid API keys | nothing |
| Money | none | ~$7 per full run | none |
| Limited by | rolling usage windows | credit balance | — |
| Speed | slow — each call spawns an agent loop | fast | instant |
| Token accounting | **estimated only** | exact | n/a |
| Repo awareness | yes — the CLI reads your files | no | no |

One property of `cli` that is easy to miss: `claude -p` and `codex exec` are **agent loops, not single completions**. They read their working directory. That is why a CLI-transport blueprint can cite real files and line numbers, and an API-transport blueprint writes a spec from scratch.

Which directory they read is therefore a real decision, and it is yours:

```
python -m claudex run --run tetris --via cli --project-dir ../tetris-game
```

`--project-dir` defaults to the run's own folder (`runs/<slug>/`), which contains the brief and the accepted sections so far. Point it at an existing codebase when you want the models to design *against that code*.

> Before this was a flag, the working directory silently fell back to wherever `claudex` was launched from — in practice the ClaudeX repo itself. Every blueprint for any other project was built while the agents read ClaudeX's source tree. If you have runs from before this change, that is why their file citations look wrong.

On the `cli` transport ClaudeX strips `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` from the subprocess environment. Otherwise the CLI silently bills your API instead of your subscription, defeating the point.

---

## Quota, cost and time

A full run is roughly **5 calls per phase** — propose, critique, revise, judge, summarize — plus a second judge on borderline scores. That is 5× what a single-model tool would spend, by design.

Context is re-sent every step: the critique carries the draft, the revise carries the draft *and* the critique, the judge carries the finished section. Inputs are therefore 3–4× the distinct content.

ClaudeX defends against this in `config/models.json`:

```json
"cli": {
  "timeout": 600,
  "delay_between_calls": 2.0,
  "max_calls_per_run": 260,
  "warn_after_calls": 40
}
```

`max_calls_per_run` is a runaway-loop safety net, not a budget. It was previously 120, which a 25-phase run at the default `--rounds 2` (projecting ~200 typical / 250 worst-case calls) could not fit inside — so the documented defaults stopped around phase 15 and every example in this README quietly used `--rounds 1` instead. `claudex run` now prints the projection before spending anything:

```
quota:    ~200 calls typical, 250 worst case, cap 260 (config/models.json)
```

**To spend less**, change `routing` in the same file — move `propose`, `critique` and `revise` from `deep` to `balanced`. That takes the heavy calls off the top-tier model and roughly cuts time and quota by half, at some quality cost.

**When one vendor's window is already spent**, `claudex make --solo claude` (or `--solo gpt`) builds with that vendor alone rather than waiting for the other to reset. The same model then writes, critiques and scores, so the rubric is self-assessed — which is why `run`, `create` and `build` do not offer the flag.

Two roles are deliberately *not* cheap, and moving them down costs more than it saves:

- **`summarize`** — its output is the only view later phases ever get of an accepted section. Sections compress to 3–12% of their size before any downstream phase reads them, so whichever model runs this role decides what survives. It used to run on `fast` for every profile, which meant the weakest model was the highest-leverage component in the pipeline.
- **`judge`** — a judge routed below the author's tier cannot meaningfully reject its work. On `deep` phases the author is the top tier, so the judge now is too.

> **`cost` reports $0.00 on the cli transport, and that is not the same as free.**
> The CLIs report no token usage, so the ledger estimates from the prompt string and the final answer. Everything the agent loop does in between — reading your files, reasoning, tool calls — is counted nowhere. Treat the ledger as a floor, not a measurement.

---

## Statistics from real runs

> **Read the ledger, not the folder.** Four runs exist under `runs/`. Only two
> made real model calls: `claudex` (single-vendor) and `space-attack` (cross-vendor,
> 1 phase). `tetris` and `findit` are complete-looking runs whose ledgers are 100%
> `offline: true` — mock output, not evidence. Check `offline` in `state.json`
> before citing any run below.

### Run A — ClaudeX profiling itself, 17 phases, single-vendor

Every score below came from Claude grading Claude, so read them as a measure of the pipeline, not of quality.

```
17/17 accepted   91 calls   $0.0000
405,861 tokens in / 231,216 out
2h 01m of phase time
```

| Phase | Tier | Score | Time |
|---|---|---|---|
| 1. Problem Definition | deep | 89 | 12m 01s |
| 2. Feature Planning | deep | 93 | 1m 12s *(cached)* |
| 10. Security | deep | 90 | 13m 05s |
| 11. Testing | standard | 94 | 5m 51s |
| 12. Edge Cases | standard | 93 | 6m 09s |
| 13. Performance | standard | 90 | 4m 51s |
| 14. Scalability | standard | 92 | 5m 08s |
| 16. DevOps | standard | 93 | 4m 59s |
| 17. Monitoring | light | 91 *(re-run; first attempt hit the parse-failure bug)* | 6m 52s |
| 18. Analytics | light | 83 | 7m 50s |
| 19. Documentation | standard | 91 | 4m 36s |
| 20. Diagrams | standard | 88 | 6m 14s |
| 21. Research | deep | 91 | 9m 42s |
| 22. Legal & Privacy | standard | 93 | 5m 51s |
| 23. Code Quality | light | 90 | 5m 54s |
| 24. Final Evaluation | deep | 93 | 14m 30s |
| 25. Presentation | standard | 88 | 5m 55s |

**By model**

| Model | Calls | In | Out |
|---|---|---|---|
| `claude-opus-5` (deep) | 17 | 97,423 | 116,612 |
| `claude-sonnet-5` (balanced) | 44 | 209,142 | 88,894 |
| `claude-haiku-4-5` (fast) | 25 | 99,296 | 25,710 |

**By tier — plan your time with these**

| Tier | Phases | Average |
|---|---|---|
| deep | 4 | **12m 20s** |
| standard | 9 | **5m 30s** |
| light | 3 | **6m 52s** |

Note the anomaly: **`light` phases were slower than `standard`**. The light profile routes `propose` to the fast model but `critique` to balanced, and the cheap model's longer, looser drafts cost more time downstream than they save. The tier names describe cost, not speed.

### Run B — Space Attack, cross-vendor

The configuration that actually demonstrates the idea:

```
propose    claude:deep      claude-opus-5
critique   gpt:deep         gpt-6-astra
revise     claude:deep      claude-opus-5
judge      gpt:balanced     gpt-5.6-terra
score 89/100   author=claude  critic=gpt
coverage 94 | specificity 93 | consistency 84 | feasibility 84 | risk_handling 91
```

### Output size

A 17-phase blueprint came to **347 KB** across 17 sections, with a 17-file JSON transcript of every exchange and 14 logged open questions. Individual sections ran 20–57 KB.

---

## What you get out

```
runs/<slug>/
  brief.md              your idea and constraints
  state.json            phase status, scores, ledger — the resume point
  sections/NN-slug.md   one accepted section per phase
  transcript/NN-slug.json   every propose/critique/revise/judge exchange
  blueprint.md          all sections assembled
  open-questions.md     what the debate could not settle
  report.html           scores and model attribution, open in a browser
  decisions.json        every numbered decision, extracted
  summary.json          run-level totals
```

`open-questions.md` is the most underrated file. It is where the critic flagged something the author could not resolve — read it before you start building.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `invalid choice: 'x'` on `--run` | `--run` placed before the subcommand | `claudex run --run x`, not `claudex --run x run` |
| `Login expired · Please run /login` | TUI session expired | type `/login` **with the slash** |
| `You've hit your session limit` | subscription window spent | wait for the printed reset time, or `--via api` |
| `'codex' is not on PATH` | CLI not installed | `npm install -g @openai/codex`, then `codex login` |
| `single-vendor` in the header | one CLI missing or signed out | fix it — the scores mean nothing otherwise |
| `Your credit balance is too low` | valid key, no credits | add credits, or `--via cli` |
| Phase scores `0/100` | judge reply did not parse | check `parsed` in the transcript; rerun with `--force` |
| Blocked network in a sandbox | egress policy | the CLIs need `api.anthropic.com` and `api.openai.com` |

---

## Known issues

**1. A successful answer can be misread as an exhausted quota.** *(fixed)*

`providers/cli_api.py` scanned the model's own answer for `QUOTA_EXHAUSTED` tokens, which include the bare word `"quota"`. Any section discussing subscription tooling, or a browser game's `QuotaExceededError` from `localStorage`, triggered a non-retryable `QuotaExhausted` and killed the run on a call that had actually succeeded.

Fixed by gating body-text scanning on *shape* rather than length. Length was not a safe signal either: a judge's JSON verdict sits well under `ERROR_TEXT_MAX` and routinely discusses quota, login or timeout behaviour as ordinary project content. `_looks_like_cli_error()` now requires a diagnostic-shaped prefix (`error:`, `usage limit reached`, `not authenticated`, …) before an exit-zero answer may be read as a failure; on a non-zero exit the whole output is still scanned. The same gate guards the `AUTH_FAILURE` check, which previously read `answer[:300]` unconditionally.

A related trap on the same path: `codex exec` writes its agent transcript — including the user's full prompt — to stderr even on success, so a successful call's stderr must never be keyword-scanned.

**2. A judge parse failure was recorded as a score of 0.** *(fixed)*

When the judge's reply did not match the rubric format, every dimension defaulted to `0.0` and the phase was kept with `verdict: ITERATE`. The transcript recorded `parsed: False`, but `report.html` showed `0/100` — indistinguishable from a genuinely bad section. It also fed the escalation ladder a failure that never happened, burning a stronger tier on a formatting glitch.

`parse_judgement` now returns `total: None` and `verdict: UNSCORED`. Unscored phases render as `--` in `report.html` and `blueprint.md`, are excluded from averages, and never trigger escalation. A borderline-or-unparsed primary judgement now also triggers the second judge, which often rescues the phase for one cheap call.

**3. `"session limit"` is not in `QUOTA_EXHAUSTED`.**

The table matches `"usage limit"` but not `"session limit"`, so that genuine quota stop surfaces as a generic `failed (exit 1)` and skips the reset-time guidance.

**4. The `cli` ledger cannot measure what it reports.** See [Quota, cost and time](#quota-cost-and-time).

Output tokens were additionally estimated from `stdout` rather than the answer — wrong for `codex exec`, where `-o` writes the answer to a file and stdout is the agent's reasoning transcript. Fixed to measure the answer. The figure remains an estimate.

**5. The cross-vendor claim is under-evidenced.**

The thing this tool exists to do has been validated end to end on **one phase**. `runs/claudex` is a real 17-phase run but was single-vendor (Claude critiquing Claude). `runs/tetris` and `runs/findit` look like real runs and are not — their ledgers are 100% `offline: true`, i.e. mock. `runs/space-attack` is the only genuine cross-vendor evidence and it completed 1 of 23 phases.

There is also no measured comparison of cross-vendor output against single-vendor output, so the 5× call cost is unjustified rather than merely unmeasured. Treat every score in this repo as a model grading a model with no external baseline.

**6. `make` has never completed against a real transport.**

No `runs/*/product` directory exists. Product generation is exercised only by the offline mock test.
