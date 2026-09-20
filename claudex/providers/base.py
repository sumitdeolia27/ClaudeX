"""Provider interface plus a stdlib-only JSON-over-HTTPS helper."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..util import RetryableError

# Status codes worth a retry: rate limit, overloaded, and transient server errors.
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


@dataclass
class Completion:
    text: str
    vendor: str
    tier: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency: float = 0.0
    cached: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.vendor}:{self.tier}"


class ProviderError(RuntimeError):
    """Non-retryable API failure (bad key, bad model name, malformed request)."""


class Provider:
    """Every provider turns (system, messages) into a Completion."""

    name = "base"

    def __init__(self, vendor: str, registry, timeout: int = 300):
        self.vendor = vendor
        self.registry = registry
        self.timeout = timeout

    def complete(
        self,
        system: str,
        messages: list[dict],
        tier: str,
        max_tokens: int | None = None,
        temperature: float = 0.6,
    ) -> Completion:
        raise NotImplementedError


def post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    """POST JSON and return parsed JSON, mapping transient failures to RetryableError."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:800]
        except Exception:
            pass
        message = f"HTTP {exc.code} from {url}: {detail}"
        if exc.code in RETRY_STATUS:
            raise RetryableError(message) from exc
        raise ProviderError(message) from exc
    except urllib.error.URLError as exc:
        raise RetryableError(f"network error calling {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RetryableError(f"timeout after {timeout}s calling {url}") from exc


def get_json(url: str, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"network error calling {url}: {exc.reason}") from exc


def timed(fn):
    start = time.time()
    result = fn()
    return result, time.time() - start
