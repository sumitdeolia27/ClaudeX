"""Anthropic Messages API client (stdlib only)."""

from __future__ import annotations

import os

from .base import Completion, Provider, ProviderError, get_json, post_json, timed


class AnthropicProvider(Provider):
    name = "anthropic"

    def _headers(self) -> dict:
        spec = self.registry.vendor(self.vendor)
        key = os.environ.get(spec.get("api_key_env", "ANTHROPIC_API_KEY"), "").strip()
        if not key:
            raise ProviderError(
                f"{spec.get('api_key_env')} is not set. Add it to .env or the environment."
            )
        return {
            "x-api-key": key,
            "anthropic-version": spec.get("api_version", "2023-06-01"),
        }

    def complete(
        self,
        system: str,
        messages: list[dict],
        tier: str,
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        spec = self.registry.tier_spec(self.vendor, tier)
        url = f"{self.registry.base_url(self.vendor)}/v1/messages"
        payload = {
            "model": spec["model"],
            "max_tokens": max_tokens or spec.get("max_tokens", 4096),
            "system": system,
            "messages": messages,
        }
        # Extended thinking requires temperature=1 and a budget under max_tokens.
        budget = spec.get("thinking_budget")
        if spec.get("thinking"):
            budget = int(budget or max(1024, payload["max_tokens"] // 2))
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["temperature"] = 1
        else:
            payload["temperature"] = temperature

        data, elapsed = timed(
            lambda: post_json(url, self._headers(), payload, self.timeout)
        )

        # Content is a list of blocks; thinking blocks are skipped, text is joined.
        parts = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        usage = data.get("usage", {})
        return Completion(
            text="\n".join(p for p in parts if p).strip(),
            vendor=self.vendor,
            tier=tier,
            model=spec["model"],
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            latency=elapsed,
            raw={"stop_reason": data.get("stop_reason")},
        )

    def list_models(self) -> list[str]:
        url = f"{self.registry.base_url(self.vendor)}/v1/models?limit=100"
        data = get_json(url, self._headers(), self.timeout)
        return [m.get("id", "") for m in data.get("data", [])]
