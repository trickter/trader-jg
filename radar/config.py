from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    rules_path: Path
    host: str
    port: int
    okx_api_key: str | None
    okx_secret_key: str | None
    okx_passphrase: str | None
    okx_project_id: str | None
    gmgn_api_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        def path(name: str, default: str) -> Path:
            value = Path(os.getenv(name, default))
            return value if value.is_absolute() else ROOT / value

        return cls(
            db_path=path("RADAR_DB_PATH", "data/radar.db"),
            rules_path=path("RADAR_RULES_PATH", "config/rules.yaml"),
            host=os.getenv("RADAR_HOST", "127.0.0.1"),
            port=int(os.getenv("RADAR_PORT", "8000")),
            okx_api_key=os.getenv("OKX_API_KEY") or None,
            okx_secret_key=os.getenv("OKX_SECRET_KEY") or None,
            okx_passphrase=os.getenv("OKX_PASSPHRASE") or os.getenv("OKX_API_PASSPHRASE") or None,
            okx_project_id=os.getenv("OKX_PROJECT_ID") or None,
            gmgn_api_key=os.getenv("GMGN_API_KEY") or "gmgn_solbscbaseethmonadtron",
        )

    @property
    def okx_configured(self) -> bool:
        return all((self.okx_api_key, self.okx_secret_key, self.okx_passphrase))


def load_rules(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return data, hashlib.sha256(canonical.encode()).hexdigest()

