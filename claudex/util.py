"""Small helpers shared across ClaudeX. No third-party imports anywhere."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- text ----

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "untitled"


def sha256_of(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8", "replace"))
    return h.hexdigest()


def estimate_tokens(text: str) -> int:
    """Rough but stable estimate: ~4 characters per token for English prose."""
    return max(1, len(text) // 4)


def truncate(text: str, max_chars: int, note: str = "\n\n...[truncated]") -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(note)] + note


def extract_json(text: str) -> dict | list | None:
    """Pull the first JSON object/array out of a model reply.

    Models wrap JSON in prose or fences often enough that trying `json.loads`
    on the whole reply is not good enough.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text.strip())
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            pass
        # Fall back to brace matching from the first opener.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            if start == -1:
                continue
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cand[start : i + 1])
                        except Exception:
                            break
    return None


def strip_json_blocks(text: str) -> str:
    """Remove fenced json blocks so prose can be rendered without them."""
    return re.sub(r"```json\s*.+?```", "", text, flags=re.S).strip()



def score_text(value, width: int = 0) -> str:
    """Render a rubric score, distinguishing "unscored" from "scored zero"."""
    text = "--" if value is None else f"{float(value):.0f}"
    return text.rjust(width) if width else text


def mean_score(values) -> float | None:
    """Average only the phases that actually have a score."""
    real = [float(v) for v in values if v is not None]
    return sum(real) / len(real) if real else None


# ---------------------------------------------------------------- time ----


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


# ------------------------------------------------------------------ io ----


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------- retry ----


class RetryableError(Exception):
    """Transient failure worth retrying (429, 5xx, timeouts)."""


def retry(fn, attempts: int = 4, base_delay: float = 1.5, on_retry=None):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except RetryableError as exc:
            last = exc
            if i == attempts - 1:
                break
            delay = base_delay * (2**i)
            if on_retry:
                on_retry(i + 1, attempts, delay, exc)
            time.sleep(delay)
    raise last if last else RuntimeError("retry called with zero attempts")


# ------------------------------------------------------------- console ----


class Console:
    """Terminal output with colour when the terminal supports it."""

    COLORS = {
        "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
        "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
    }

    def __init__(self, quiet: bool = False, color: bool | None = None):
        self.quiet = quiet
        if color is None:
            color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        self.color = bool(color)

    def _c(self, text: str, name: str) -> str:
        if not self.color or name not in self.COLORS:
            return text
        return f"{self.COLORS[name]}{text}{self.COLORS['reset']}"

    def say(self, text: str = "") -> None:
        if not self.quiet:
            print(text, flush=True)

    def step(self, text: str) -> None:
        self.say(f"  {self._c('->', 'cyan')} {text}")

    def head(self, text: str) -> None:
        self.say()
        self.say(self._c(text, "bold"))

    def ok(self, text: str) -> None:
        self.say(f"  {self._c('OK', 'green')} {text}")

    def warn(self, text: str) -> None:
        if not self.quiet:
            print(f"  {self._c('!!', 'yellow')} {text}", flush=True)

    def err(self, text: str) -> None:
        print(f"  {self._c('XX', 'red')} {text}", file=sys.stderr, flush=True)

    def dim(self, text: str) -> None:
        self.say(f"     {self._c(text, 'dim')}")
