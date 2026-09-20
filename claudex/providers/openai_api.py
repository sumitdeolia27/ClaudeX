"""OpenAI Chat Completions client (stdlib only).

Reasoning-model parameter rules have shifted across generations, so this client
is deliberately tolerant: if the API rejects a parameter as unsupported, it is
dropped and the call is retried once. That keeps the registry editable without
code changes when model names or parameter support move on.
"""

from __future__ import annotations

import os
import re

from .base import Completion, Provider, ProviderError, get_json, post_json, timed

DROPPABLE = (
    "temperature",
    "reasoning_effort",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
)

_UNSUPPORTED_RE = re.compile(
    r"unsupported[_ ](?:parameter|value)|is not supported with this model"
    r"|unrecognized request argument|use '([a-z_]+)' instead",
    re.I,
)


class OpenAIProvider(Provider):
    name = "openai"

    def _headers(self) -> dict:
        spec = self.registry.vendor(self.vendor)
        key = os.environ.get(spec.get("api_key_env", "OPENAI_API_KEY"), "").strip()
        if not key:
            raise ProviderError(
                f"{spec.get('api_key_env')} is not set. Add it to .env or the environment."
            )
        headers = {"Authorization": f"Bearer {key}"}
        if org := os.environ.get("OPENAI_ORG_ID", "").strip():
            headers["OpenAI-Organization"] = org
        return headers

    def complete(
        self,
        system: str,
        messages: list[dict],
        tier: str,
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        spec = self.registry.tier_spec(self.vendor, tier)
        url = f"{self.registry.base_url(self.vendor)}/v1/chat/completions"
        limit = max_tokens or spec.get("max_tokens", 4096)

        payload: dict = {
            "model": spec["model"],
            "messages": [{"role": "system", "content": system}, *messages],
            "max_completion_tokens": limit,
            "temperature": temperature,
        }
        if effort := spec.get("reasoning_effort"):
            payload["reasoning_effort"] = effort

        data, elapsed = timed(lambda: self._post_tolerant(url, payload))

        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage", {})
        return Completion(
            text=(choice.get("message", {}).get("content") or "").strip(),
            vendor=self.vendor,
            tier=tier,
            model=spec["model"],
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency=elapsed,
            raw={"finish_reason": choice.get("finish_reason")},
        )

    def _post_tolerant(self, url: str, payload: dict) -> dict:
        """Post, and on an 'unsupported parameter' 400 drop that param and retry."""
        attempt = dict(payload)
        for _ in range(len(DROPPABLE) + 1):
            try:
                return post_json(url, self._headers(), attempt, self.timeout)
            except ProviderError as exc:
                text = str(exc)
                if "HTTP 400" not in text or not _UNSUPPORTED_RE.search(text):
                    raise
                offender = next(
                    (p for p in DROPPABLE if f"'{p}'" in text or f'"{p}"' in text),
                    None,
                )
                if offender is None or offender not in attempt:
                    raise
                swap = re.search(r"use '([a-z_]+)' instead", text, re.I)
                value = attempt.pop(offender)
                if swap and swap.group(1) not in attempt:
                    attempt[swap.group(1)] = value
        raise ProviderError("gave up dropping unsupported OpenAI parameters")

    def list_models(self) -> list[str]:
        url = f"{self.registry.base_url(self.vendor)}/v1/models"
        data = get_json(url, self._headers(), self.timeout)
        return sorted(m.get("id", "") for m in data.get("data", []))
