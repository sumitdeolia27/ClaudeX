"""Paths, .env loading, and the model registry."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .util import read_json

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read a .env file into os.environ without overwriting real env vars."""
    path = path or (PROJECT_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


@dataclass
class Settings:
    root: Path = PROJECT_ROOT
    config_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "config")
    runs_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runs")
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".cache")
    use_cache: bool = True
    timeout: int = 300

    @property
    def models_path(self) -> Path:
        return self.config_dir / "models.json"

    @property
    def phases_dir(self) -> Path:
        return self.config_dir / "phases"


class ModelRegistry:
    """Wraps config/models.json so no model ID is ever hardcoded in code.

    A vendor exposes the same tiers over two transports:

      api - pay-as-you-go API key, billed per token
      cli - the vendor's local CLI (`claude`, `codex`) signed in to your
            subscription. No money per call, but metered by your plan.

    `transport` is set once per run and applies to every vendor.
    """

    def __init__(self, data: dict, transport: str = "api"):
        self.data = data
        self.vendors: dict = data.get("vendors", {})
        self.routing: dict = data.get("routing", {})
        self.escalation: dict = data.get("escalation", {})
        self.cli_settings: dict = data.get("cli", {})
        self.transport = transport

    @classmethod
    def load(cls, settings: Settings, transport: str = "api") -> "ModelRegistry":
        data = read_json(settings.models_path)
        if data is None:
            raise SystemExit(
                f"Model registry not found at {settings.models_path}.\n"
                "Restore config/models.json from the repo."
            )
        return cls(data, transport=transport)

    # -- lookups ----------------------------------------------------------

    def vendor(self, name: str) -> dict:
        if name not in self.vendors:
            raise KeyError(f"Unknown vendor '{name}'. Known: {', '.join(self.vendors)}")
        return self.vendors[name]

    def transport_spec(self, vendor: str, transport: str | None = None) -> dict:
        """Transport config, tolerating the flat pre-transport layout."""
        spec = self.vendor(vendor)
        transports = spec.get("transports")
        if not transports:
            return spec  # legacy flat config: provider/base_url on the vendor
        name = transport or self.transport
        if name not in transports:
            raise SystemExit(
                f"Vendor '{vendor}' has no '{name}' transport. "
                f"Available: {', '.join(transports)}"
            )
        return transports[name]

    def provider_kind(self, vendor: str, transport: str | None = None) -> str:
        return self.transport_spec(vendor, transport).get("provider", "mock")

    def tier_spec(self, vendor: str, tier: str) -> dict:
        tiers = self.vendor(vendor).get("tiers", {})
        if tier not in tiers:
            raise KeyError(
                f"Vendor '{vendor}' has no tier '{tier}'. Known: {', '.join(tiers)}"
            )
        return tiers[tier]

    def model_id(self, vendor: str, tier: str, transport: str | None = None) -> str:
        """CLI transports may need a different model name than the API."""
        spec = self.tier_spec(vendor, tier)
        if (transport or self.transport) == "cli":
            return spec.get("cli_model") or spec["model"]
        return spec["model"]

    def has_key(self, vendor: str) -> bool:
        env_name = self.vendor(vendor).get("api_key_env", "")
        return bool(os.environ.get(env_name, "").strip())

    def cli_command(self, vendor: str) -> list[str]:
        return list(self.transport_spec(vendor, "cli").get("command", []))

    def cli_available(self, vendor: str) -> bool:
        """Is the vendor's CLI executable actually on PATH?"""
        cmd = self.cli_command(vendor)
        return bool(cmd) and shutil.which(cmd[0]) is not None

    def live_vendors(self, transport: str | None = None) -> list[str]:
        """Vendors usable over the active transport."""
        name = transport or self.transport
        if name == "cli":
            return [v for v in self.vendors if self.cli_available(v)]
        return [v for v in self.vendors if self.has_key(v)]

    def base_url(self, vendor: str) -> str:
        """Explicit override wins; otherwise the registry value.

        Note: ambient ANTHROPIC_BASE_URL / OPENAI_BASE_URL are deliberately NOT
        inherited. Host tools set those to internal proxies that reject plain
        API keys, which produces very confusing 401s.
        """
        override = os.environ.get(f"CLAUDEX_{vendor.upper()}_BASE_URL", "").strip()
        registry_url = self.transport_spec(vendor, "api").get("base_url", "")
        return (override or registry_url).rstrip("/")

    def price(self, vendor: str, tier: str, tokens_in: int, tokens_out: int) -> float:
        # Subscription calls cost no money; the ledger counts them as quota.
        if self.transport == "cli":
            return 0.0
        spec = self.tier_spec(vendor, tier)
        return (
            tokens_in / 1_000_000 * float(spec.get("price_in", 0.0))
            + tokens_out / 1_000_000 * float(spec.get("price_out", 0.0))
        )
