"""Subscription transport: shell out to the vendor's local CLI.

`claude -p` and `codex exec` run against the subscription you are already
signed in to, so these calls cost no money. They are metered by your plan
instead, which is why the pool throttles them and caps how many a run may make.

Trade-offs versus the API transport, all of them real:
  - much slower: each call spawns a process and runs an agent loop
  - no token accounting, so cost/usage figures are estimates
  - subject to your plan's rolling usage window, not a balance
  - the CLI may add conversational framing the API would not
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import Completion, Provider, ProviderError
from ..util import RetryableError, estimate_tokens

# The plan's usage window is spent. Retrying cannot help - the reset can be
# days away - so this must be checked BEFORE the transient patterns, which it
# would otherwise match on "try again at ...".
QUOTA_EXHAUSTED = (
    "usage limit", "quota", "no credits remaining", "credit balance",
    "purchase more credits", "upgrade to pro", "out of credits",
)

# CLI stderr text that means "try again in a moment" rather than "this is broken".
TRANSIENT = (
    "rate limit", "429", "overloaded", "timeout", "timed out",
    "temporarily", "try again", "connection", "econnreset", "socket hang up",
)

# Text that means the user must act - retrying will never help.
AUTH_FAILURE = (
    "oauth", "not authenticated", "unauthenticated", "log in", "login",
    "session expired", "invalid api key", "sign in", "credential",
)


class QuotaExhausted(ProviderError):
    """The subscription's usage window is spent. Retrying will not help."""


_RESET_RE = re.compile(
    r"try again at ([^.\n]{4,60})|resets? (?:at|on) ([^.\n]{4,60})", re.I
)


def _reset_hint(detail: str) -> str:
    match = _RESET_RE.search(detail)
    if match:
        when = (match.group(1) or match.group(2)).strip()
        return f"Resets around: {when}"
    return "Check your plan's usage page for when the window resets."


class CLIProvider(Provider):
    """Drives a vendor CLI in non-interactive mode."""

    name = "cli"

    def __init__(self, vendor: str, registry, timeout: int = 600):
        super().__init__(vendor, registry, timeout)
        spec = registry.transport_spec(vendor, "cli")
        self.command: list[str] = list(spec.get("command", []))
        self.model_flag: str = spec.get("model_flag", "--model")
        self.extra_args: list[str] = list(spec.get("extra_args", []))
        self.login_hint: str = spec.get("login_hint", f"{self.command[:1]} login")
        self.timeout = int(registry.cli_settings.get("timeout", timeout))
        # Agent CLIs print reasoning and tool logs to stdout. When the CLI can
        # write just its final message to a file, use that instead - otherwise
        # the "response" is a transcript with the answer buried in it.
        self.output_file_flag: str = spec.get("output_file_flag", "")

        if not self.command:
            raise ProviderError(f"No CLI command configured for vendor '{vendor}'.")
        # On Windows these CLIs are .cmd/.ps1 shims. subprocess cannot launch
        # them by bare name without a shell, so resolve to the real path once.
        resolved = shutil.which(self.command[0])
        if resolved is None:
            raise ProviderError(
                f"'{self.command[0]}' is not on PATH. Install it, or use --via api."
            )
        self.exe_name = self.command[0]
        self.command[0] = resolved

    # -- prompt shaping ---------------------------------------------------

    @staticmethod
    def _flatten(system: str, messages: list[dict]) -> str:
        """These CLIs take one prompt string, not a role array."""
        parts = [system.strip()] if system.strip() else []
        for msg in messages:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg.get("role") == "assistant":
                parts.append(f"[previous assistant turn]\n{content}")
            else:
                parts.append(content)
        return "\n\n".join(parts)

    def _env(self) -> dict:
        """Run the CLI on subscription auth, not a stray API key.

        An ANTHROPIC_API_KEY in the environment makes the Claude CLI bill the
        API instead of the subscription - which is the opposite of why anyone
        picks this transport. The same applies to base-URL overrides pointing
        at proxies.
        """
        env = dict(os.environ)
        for var in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
        ):
            env.pop(var, None)
        return env

    # -- main entry point -------------------------------------------------

    def complete(
        self,
        system: str,
        messages: list[dict],
        tier: str,
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        model = self.registry.model_id(self.vendor, tier, transport="cli")
        prompt = self._flatten(system, messages)
        argv = [*self.command, *self.extra_args]
        if self.model_flag:
            argv += [self.model_flag, model]

        out_file: Path | None = None
        if self.output_file_flag:
            handle, path = tempfile.mkstemp(prefix="claudex-", suffix=".txt")
            os.close(handle)
            out_file = Path(path)
            argv += [self.output_file_flag, str(out_file)]

        started = time.time()
        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=self._env(),
                # Windows resolves .cmd shims only through the shell path lookup
                # that subprocess already does; cwd is pinned so the CLI does not
                # pick up whatever project happens to be the current directory.
                cwd=str(self.registry.data.get("_cli_cwd") or os.getcwd()),
            )
        except subprocess.TimeoutExpired as exc:
            raise RetryableError(
                f"{self.exe_name} timed out after {self.timeout}s"
            ) from exc
        except FileNotFoundError as exc:
            raise ProviderError(f"'{self.exe_name}' not found on PATH.") from exc

        elapsed = time.time() - started
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        # Prefer the CLI's final-message file over its stdout transcript.
        answer = stdout
        if out_file is not None:
            try:
                written = out_file.read_text(encoding="utf-8", errors="replace").strip()
                if written:
                    answer = written
            except OSError:
                pass
            finally:
                out_file.unlink(missing_ok=True)

        # These CLIs report failure on stdout with exit code 0 often enough that
        # the text has to be inspected either way.
        combined = f"{stderr}\n{stdout}\n{answer}".lower()
        quota_hit = any(token in combined for token in QUOTA_EXHAUSTED)
        if proc.returncode != 0 or not answer or quota_hit:
            detail = stderr or stdout or answer or "(no output)"
            if any(token in combined for token in AUTH_FAILURE) and not quota_hit:
                raise ProviderError(
                    f"{self.exe_name} is not signed in: {detail[:200]}\n"
                    f"Fix it by running:  {self.login_hint}"
                )
            if quota_hit:
                raise QuotaExhausted(
                    f"{self.exe_name} has used up its subscription quota.\n"
                    f"  {_reset_hint(detail)}\n"
                    f"  Completed phases are saved - rerun when the window resets, "
                    f"or switch with --via api."
                )
            if any(token in combined for token in TRANSIENT):
                raise RetryableError(f"{self.exe_name} transient failure: {detail[:200]}")
            raise ProviderError(
                f"{self.exe_name} failed (exit {proc.returncode}): {detail[:400]}"
            )

        if any(token in answer.lower()[:300] for token in AUTH_FAILURE):
            raise ProviderError(
                f"{self.exe_name} is not signed in: {answer[:200]}\n"
                f"Fix it by running:  {self.login_hint}"
            )

        return Completion(
            text=answer,
            vendor=self.vendor,
            tier=tier,
            model=model,
            # No usage reporting from the CLIs, so these are estimates and the
            # ledger prices them at zero anyway.
            tokens_in=estimate_tokens(prompt),
            tokens_out=estimate_tokens(stdout),
            latency=elapsed,
            raw={"transport": "cli", "exit": proc.returncode, "estimated_tokens": True},
        )

    def list_models(self) -> list[str]:
        """CLIs do not expose a model list, so the registry is taken on trust."""
        return [
            self.registry.model_id(self.vendor, tier, transport="cli")
            for tier in self.registry.vendor(self.vendor).get("tiers", {})
        ]

    def check(self) -> str:
        """Return the CLI version string, or raise with a fix-it message."""
        spec = self.registry.transport_spec(self.vendor, "cli")
        argv = list(spec.get("check") or [self.exe_name, "--version"])
        argv[0] = shutil.which(argv[0]) or argv[0]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=60, env=self._env()
            )
        except Exception as exc:
            raise ProviderError(f"{argv[0]} check failed: {exc}") from exc
        return (proc.stdout or proc.stderr or "").strip().splitlines()[0][:80]
