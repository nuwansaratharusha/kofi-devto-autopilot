"""Zero-dependency runtime configuration loader (.env + environment variables)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


def parse_dotenv_file(env_path: Path) -> dict[str, str]:
    """Parse a `.env` file safely without executing shell syntax."""
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    db_path: Path
    site_dir: Path
    exports_dir: Path
    dist_dir: Path
    devto_api_key: str
    publish_mode: str  # "draft" | "live"
    kofi_profile_url: str
    primary_store_url: str
    mirror_store_url: str
    canonical_base_url: str
    author_name: str
    timezone: str

    @property
    def free_checkout_url(self) -> str:
        return self.primary_store_url or self.mirror_store_url or self.kofi_profile_url

    @property
    def paid_checkout_url(self) -> str:
        return self.mirror_store_url or self.primary_store_url or self.kofi_profile_url

    @classmethod
    def load(
        cls,
        project_root: Optional[Path] = None,
        *,
        environ: Optional[Mapping[str, str]] = None,
        db_path: Optional[Path] = None,
    ) -> "AppConfig":
        root = (project_root or Path(__file__).resolve().parent.parent).resolve()
        file_vals = parse_dotenv_file(root / ".env")
        env_map = dict(file_vals)
        source_env = os.environ if environ is None else environ
        for k, v in source_env.items():
            if v is not None:
                env_map[k] = v

        raw_mode = env_map.get("PUBLISH_MODE", "draft").strip().lower()
        publish_mode = "live" if raw_mode == "live" else "draft"

        kofi_profile = (
            env_map.get("KOFI_PROFILE_URL", "https://ko-fi.com/tharushanuwansara").strip().rstrip("/")
            or "https://ko-fi.com/tharushanuwansara"
        )
        primary_store = (
            env_map.get("PRIMARY_STORE_URL", f"{kofi_profile}/shop").strip().rstrip("/")
            or f"{kofi_profile}/shop"
        )
        mirror_store = env_map.get("MIRROR_STORE_URL", "").strip().rstrip("/")
        canonical_base = (
            env_map.get(
                "CANONICAL_BASE_URL",
                "https://tharushanuwansara.github.io/kofi-devto-autopilot",
            )
            .strip()
            .rstrip("/")
        )

        return cls(
            project_root=root,
            db_path=db_path or (root / "state" / "pipeline.db"),
            site_dir=root / "site",
            exports_dir=root / "exports",
            dist_dir=root / "products" / "dist",
            devto_api_key=env_map.get("DEVTO_API_KEY", "").strip(),
            publish_mode=publish_mode,
            kofi_profile_url=kofi_profile,
            primary_store_url=primary_store,
            mirror_store_url=mirror_store,
            canonical_base_url=canonical_base,
            author_name=env_map.get("AUTHOR_NAME", "Tharusha Nuwansara").strip() or "Tharusha Nuwansara",
            timezone=env_map.get("TIMEZONE", "Asia/Colombo").strip() or "Asia/Colombo",
        )
