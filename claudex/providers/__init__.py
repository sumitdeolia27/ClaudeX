"""Provider factory with an on-disk response cache."""

from __future__ import annotations

import time
from pathlib import Path

from ..util import read_json, retry, sha256_of, write_json
from .anthropic_api import AnthropicProvider
from .base import Completion, Provider, ProviderError
from .cli_api import CLIProvider, QuotaExhausted
from .mock import MockProvider
from .openai_api import OpenAIProvider

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "cli": CLIProvider,
    "mock": MockProvider,
}

__all__ = [
    "Completion", "Provider", "ProviderError", "ProviderPool", "PROVIDERS",
    "QuotaExceeded", "QuotaExhausted",
]


class QuotaExceeded(RuntimeError):
    """The subscription-call cap for this run was reached."""


class ProviderPool:
    """Resolves vendor -> provider instance, adds caching, retry and throttling.

    In offline mode every vendor resolves to MockProvider, so role alternation
    and cross-vendor critique still happen - just without network calls.

    On the CLI transport, calls cost no money but consume subscription quota,
    so they are spaced out and capped per run.
    """

    def __init__(self, registry, settings, offline: bool = False, console=None):
        self.registry = registry
        self.settings = settings
        self.offline = offline
        self.console = console
        self._instances: dict[str, Provider] = {}

        cli = registry.cli_settings
        self.is_cli = (registry.transport == "cli") and not offline
        self.delay = float(cli.get("delay_between_calls", 0.0)) if self.is_cli else 0.0
        self.max_calls = int(cli.get("max_calls_per_run", 0)) if self.is_cli else 0
        self.warn_after = int(cli.get("warn_after_calls", 0)) if self.is_cli else 0
        self.live_calls = 0
        self._last_call = 0.0

    def get(self, vendor: str) -> Provider:
        if vendor not in self._instances:
            kind = "mock" if self.offline else self.registry.provider_kind(vendor)
            self._instances[vendor] = PROVIDERS[kind](
                vendor, self.registry, timeout=self.settings.timeout
            )
        return self._instances[vendor]

    def preflight(self, vendors: list[str]) -> dict[str, str]:
        """Verify each vendor is reachable before a run starts spending."""
        results = {}
        for vendor in vendors:
            try:
                provider = self.get(vendor)
                results[vendor] = (
                    provider.check() if hasattr(provider, "check") else "ready"
                )
            except Exception as exc:
                results[vendor] = f"FAILED: {str(exc)[:200]}"
        return results

    def _throttle(self) -> None:
        """Space out subscription calls and stop before the plan does."""
        if not self.is_cli:
            return
        if self.max_calls and self.live_calls >= self.max_calls:
            raise QuotaExceeded(
                f"Reached the {self.max_calls}-call cap for this run "
                f"(config/models.json -> cli.max_calls_per_run). "
                f"Completed phases are saved; rerun to continue."
            )
        if self.warn_after and self.live_calls == self.warn_after and self.console:
            self.console.warn(
                f"{self.live_calls} subscription calls made - you may be close to "
                "your plan's usage window limit."
            )
        wait = self.delay - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    # -- cache ------------------------------------------------------------

    def _cache_path(self, key: str) -> Path:
        return self.settings.cache_dir / key[:2] / f"{key}.json"

    def _cached(self, key: str) -> Completion | None:
        if not self.settings.use_cache:
            return None
        data = read_json(self._cache_path(key))
        if not data:
            return None
        return Completion(**{**data, "cached": True})

    def _store(self, key: str, completion: Completion) -> None:
        if not self.settings.use_cache:
            return
        payload = {
            "text": completion.text, "vendor": completion.vendor,
            "tier": completion.tier, "model": completion.model,
            "tokens_in": completion.tokens_in, "tokens_out": completion.tokens_out,
            "latency": completion.latency, "raw": completion.raw,
        }
        write_json(self._cache_path(key), payload)

    # -- main entry point -------------------------------------------------

    def complete(
        self,
        vendor: str,
        tier: str,
        system: str,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        provider = self.get(vendor)
        key = sha256_of(
            provider.name, vendor, tier,
            self.registry.model_id(vendor, tier) if not self.offline else "mock",
            system, messages, max_tokens, temperature,
        )
        if hit := self._cached(key):
            return hit

        def _on_retry(attempt, total, delay, exc):
            if self.console:
                self.console.dim(
                    f"retry {attempt}/{total} in {delay:.0f}s ({str(exc)[:90]})"
                )

        def _call():
            self._throttle()
            try:
                return provider.complete(
                    system=system, messages=messages, tier=tier,
                    max_tokens=max_tokens, temperature=temperature,
                )
            finally:
                # Count attempts, not successes: a retried CLI call still
                # consumed subscription quota.
                self.live_calls += 1
                self._last_call = time.time()

        completion = retry(_call, attempts=4, on_retry=_on_retry)
        if not completion.text.strip():
            raise ProviderError(
                f"{vendor}:{tier} returned an empty response "
                f"(stop reason: {completion.raw})"
            )
        self._store(key, completion)
        return completion
