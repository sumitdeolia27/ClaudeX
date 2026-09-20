"""Self-contained HTML run report: scores, model attribution, spend."""

from __future__ import annotations

import html
import json

from .util import now_iso, write_text

CSS = """
:root{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#6b6b66;--line:#e3e3df;--card:#fff;
--good:#1a7f4b;--warn:#b26a00;--bad:#b3261e;--a:#5b4bd6;--b:#0d8a72;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15151a;
--fg:#ececea;--muted:#9a9a95;--line:#2c2c33;--card:#1d1d23;--good:#4ec38a;
--warn:#e0a44a;--bad:#f2837a;--a:#a396ff;--b:#4fd1b4;}}
:root[data-theme=dark]{--bg:#15151a;--fg:#ececea;--muted:#9a9a95;--line:#2c2c33;
--card:#1d1d23;--good:#4ec38a;--warn:#e0a44a;--bad:#f2837a;--a:#a396ff;--b:#4fd1b4;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 ui-sans-serif,
-apple-system,"Segoe UI",Roboto,sans-serif;padding:32px 16px}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:28px;margin:0 0 4px} h2{font-size:18px;margin:34px 0 12px}
.sub{color:var(--muted);margin-bottom:24px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:24px;font-weight:600;margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:none}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:70px}
.bar>i{display:block;height:100%;border-radius:3px}
.v-claude{color:var(--a);font-weight:600}.v-gpt{color:var(--b);font-weight:600}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.note{color:var(--muted);font-size:13px;margin-top:8px}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:13px}
@media(max-width:620px){body{padding:20px 12px}table{font-size:13px}
th,td{padding:7px 8px}.hide-s{display:none}}
"""


def _score_class(score: float) -> str:
    return "good" if score >= 80 else "warn" if score >= 65 else "bad"


def _vendor_span(name: str) -> str:
    cls = "v-claude" if "claude" in (name or "").lower() else "v-gpt"
    return f'<span class="{cls}">{html.escape(name or "-")}</span>'


def build(run, phases: list[dict]) -> str:
    done = [p for p in phases if run.phase(p["id"]).get("section_file")]
    totals = run.totals()
    scores = [run.phase(p["id"]).get("score", 0) for p in done]
    avg = sum(scores) / len(scores) if scores else 0
    accepted = sum(1 for p in done if run.phase(p["id"]).get("status") == "accepted")

    rows = []
    for phase in done:
        meta = run.phase(phase["id"])
        score = meta.get("score", 0)
        rows.append(f"""<tr>
<td>{phase['id']}</td>
<td>{html.escape(phase['title'])}</td>
<td class="hide-s">{_vendor_span(meta.get('author'))}</td>
<td class="hide-s">{_vendor_span(meta.get('critic'))}</td>
<td>{meta.get('rounds', 0)}</td>
<td><div class="bar"><i style="width:{max(2, min(100, score)):.0f}%;
background:currentColor" class="{_score_class(score)}"></i></div></td>
<td class="{_score_class(score)}">{score:.0f}</td>
</tr>""")

    model_rows = []
    for label, stats in sorted(totals["by_model"].items()):
        model_rows.append(f"""<tr>
<td>{_vendor_span(label.split(':')[0])} <code>{html.escape(stats.get('model',''))}</code></td>
<td>{stats['calls']}</td>
<td class="hide-s">{stats['tokens_in']:,}</td>
<td class="hide-s">{stats['tokens_out']:,}</td>
<td>${stats['cost']:.4f}</td>
</tr>""")

    gaps = []
    for phase in done:
        meta = run.phase(phase["id"])
        for gap in meta.get("blocking_gaps", [])[:3]:
            gaps.append(
                f"<li><strong>Phase {phase['id']}</strong> "
                f"({html.escape(phase['title'])}): {html.escape(gap)}</li>"
            )

    gaps_html = (
        f"<h2>Unresolved gaps</h2><ul>{''.join(gaps)}</ul>"
        if gaps else "<h2>Unresolved gaps</h2><p class='note'>None recorded.</p>"
    )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClaudeX Run Report</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>{html.escape(run.name)}</h1>
<div class="sub">ClaudeX run <code>{html.escape(run.slug)}</code> &middot;
generated {now_iso()[:16].replace('T', ' ')} UTC</div>

<div class="cards">
<div class="card"><div class="k">Sections</div><div class="v">{len(done)}</div></div>
<div class="card"><div class="k">Accepted</div><div class="v">{accepted}/{len(done)}</div></div>
<div class="card"><div class="k">Avg score</div>
<div class="v {_score_class(avg)}">{avg:.0f}</div></div>
<div class="card"><div class="k">Model calls</div><div class="v">{totals['calls']}</div></div>
<div class="card"><div class="k">{'Est. spend' if totals['cost_is_estimate'] else 'Spend'}</div>
<div class="v">${totals['cost']:.2f}</div></div>
</div>
{'<p class="note warn">Offline run &mdash; no API calls were made and nothing was spent. The figure above is what this same run would cost with real keys.</p>' if totals['cost_is_estimate'] else ''}

<h2>Phases</h2>
<table><thead><tr><th>#</th><th>Phase</th><th class="hide-s">Author</th>
<th class="hide-s">Critic</th><th>Rounds</th><th>Score</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="note">Scores are the models' rubric judgement of their own plan. They
measure how completely and concretely the plan is specified &mdash; not whether
it is correct.</p>

<h2>Model usage</h2>
<table><thead><tr><th>Model</th><th>Calls</th><th class="hide-s">Tokens in</th>
<th class="hide-s">Tokens out</th><th>Cost</th></tr></thead>
<tbody>{''.join(model_rows) or '<tr><td colspan="5">No calls recorded.</td></tr>'}</tbody></table>
<p class="note">{totals['cached_calls']} of {totals['calls']} calls were served
from cache at no cost.</p>

{gaps_html}
</div>
<script>
// Persist a manual theme toggle if one is ever added; harmless when absent.
try{{const t=localStorage.getItem('claudex-theme');
if(t)document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}
</script>
</body></html>"""

    path = run.dir / "report.html"
    write_text(path, doc)
    return str(path)


def write_summary_json(run, phases: list[dict]) -> str:
    done = [p for p in phases if run.phase(p["id"]).get("section_file")]
    payload = {
        "project": run.name,
        "slug": run.slug,
        "generated": now_iso(),
        "totals": run.totals(),
        "phases": [
            {
                "id": p["id"], "title": p["title"],
                **{
                    k: run.phase(p["id"]).get(k)
                    for k in ("status", "score", "rounds", "author", "critic", "seconds")
                },
            }
            for p in done
        ],
    }
    path = run.dir / "summary.json"
    write_text(path, json.dumps(payload, indent=2))
    return str(path)
