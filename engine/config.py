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
    lemonsqueezy_store_url: str = ""
    lemonsqueezy_api_key: str = ""
    lemonsqueezy_store_id: str = ""
    gumroad_store_url: str = ""
    gumroad_access_token: str = ""
    blogger_blog_id: str = ""
    blogger_client_id: str = ""
    blogger_client_secret: str = ""
    blogger_refresh_token: str = ""
    blogger_publish_mode: str = "draft"  # "draft" | "live"
    pinterest_access_token: str = ""
    pinterest_board_id: str = ""
    patreon_url: str = ""
    patreon_creator_access_token: str = ""
    patreon_campaign_id: str = ""
    patreon_client_id: str = ""
    patreon_client_secret: str = ""
    patreon_creator_refresh_token: str = ""
    nasio_store_url: str = ""
    nasio_webhook_secret: str = ""
    experiment_start_date: str = "2026-11-18"
    experiment_end_date: str = "2026-12-18"

    @property
    def free_checkout_url(self) -> str:
        return (
            self.nasio_store_url
            or self.gumroad_store_url
            or self.patreon_url
            or self.lemonsqueezy_store_url
            or self.primary_store_url
            or self.mirror_store_url
            or self.kofi_profile_url
        )

    @property
    def paid_checkout_url(self) -> str:
        return (
            self.nasio_store_url
            or self.patreon_url
            or self.lemonsqueezy_store_url
            or self.gumroad_store_url
            or self.mirror_store_url
            or self.primary_store_url
            or self.kofi_profile_url
        )

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

        raw_blogger_mode = env_map.get("BLOGGER_PUBLISH_MODE", "draft").strip().lower()
        blogger_publish_mode = "live" if raw_blogger_mode == "live" else "draft"

        canonical_base = (
            env_map.get(
                "CANONICAL_BASE_URL",
                "https://nuwansaratharusha.github.io/kofi-devto-autopilot",
            )
            .strip()
            .rstrip("/")
            or "https://nuwansaratharusha.github.io/kofi-devto-autopilot"
        )
        nasio_store = env_map.get("NASIO_STORE_URL", "").strip().rstrip("/")
        patreon_store = env_map.get("PATREON_URL", "").strip().rstrip("/")
        kofi_profile = (
            env_map.get("KOFI_PROFILE_URL", "").strip().rstrip("/")
            or patreon_store
            or nasio_store
            or canonical_base
        )
        primary_store = (
            env_map.get("PRIMARY_STORE_URL", "").strip().rstrip("/")
            or nasio_store
            or env_map.get("LEMONSQUEEZY_STORE_URL", "").strip().rstrip("/")
            or patreon_store
            or env_map.get("GUMROAD_STORE_URL", "").strip().rstrip("/")
            or canonical_base
        )
        mirror_store = env_map.get("MIRROR_STORE_URL", "").strip().rstrip("/")
        lemonsqueezy_store = env_map.get("LEMONSQUEEZY_STORE_URL", "").strip().rstrip("/")
        gumroad_store = env_map.get("GUMROAD_STORE_URL", "").strip().rstrip("/")

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
            lemonsqueezy_store_url=lemonsqueezy_store,
            lemonsqueezy_api_key=env_map.get("LEMONSQUEEZY_API_KEY", "").strip(),
            lemonsqueezy_store_id=env_map.get("LEMONSQUEEZY_STORE_ID", "").strip(),
            gumroad_store_url=gumroad_store,
            gumroad_access_token=env_map.get("GUMROAD_ACCESS_TOKEN", "").strip(),
            blogger_blog_id=env_map.get("BLOGGER_BLOG_ID", "").strip(),
            blogger_client_id=env_map.get("BLOGGER_CLIENT_ID", "").strip(),
            blogger_client_secret=env_map.get("BLOGGER_CLIENT_SECRET", "").strip(),
            blogger_refresh_token=env_map.get("BLOGGER_REFRESH_TOKEN", "").strip(),
            blogger_publish_mode=blogger_publish_mode,
            pinterest_access_token=env_map.get("PINTEREST_ACCESS_TOKEN", "").strip(),
            pinterest_board_id=env_map.get("PINTEREST_BOARD_ID", "").strip(),
            patreon_url=patreon_store,
            patreon_creator_access_token=env_map.get("PATREON_CREATOR_ACCESS_TOKEN", "").strip(),
            patreon_campaign_id=env_map.get("PATREON_CAMPAIGN_ID", "").strip(),
            patreon_client_id=env_map.get("PATREON_CLIENT_ID", "").strip(),
            patreon_client_secret=env_map.get("PATREON_CLIENT_SECRET", "").strip(),
            patreon_creator_refresh_token=env_map.get("PATREON_CREATOR_REFRESH_TOKEN", "").strip(),
            nasio_store_url=nasio_store,
            nasio_webhook_secret=env_map.get("NASIO_WEBHOOK_SECRET", "").strip(),
            experiment_start_date=env_map.get("EXPERIMENT_START_DATE", "2026-11-18").strip() or "2026-11-18",
            experiment_end_date=env_map.get("EXPERIMENT_END_DATE", "2026-12-18").strip() or "2026-12-18",
        )
