"""Configuration via variables d'environnement."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "oui"}


@dataclass
class Settings:
    dish_address: str = field(default_factory=lambda: os.environ.get("STARLINK_DISH_ADDRESS", "192.168.100.1:9200"))
    mock: bool = field(default_factory=lambda: _bool("STARLINK_MOCK", False))
    poll_interval_s: float = field(default_factory=lambda: float(os.environ.get("STARLINK_POLL_INTERVAL", "2")))
    db_path: str = field(default_factory=lambda: os.environ.get("STARLINK_DB_PATH", "data/starlinkleo.db"))
    retention_days: int = field(default_factory=lambda: int(os.environ.get("STARLINK_RETENTION_DAYS", "30")))
    enable_controls: bool = field(default_factory=lambda: _bool("STARLINK_ENABLE_CONTROLS", True))
    # Cible pour la mesure de latence (connexion TCP) pendant le test bufferbloat.
    latency_target: str = field(default_factory=lambda: os.environ.get("STARLINK_LATENCY_TARGET", "1.1.1.1:443"))
    speedtest_base_url: str = field(default_factory=lambda: os.environ.get("STARLINK_SPEEDTEST_URL", "https://speed.cloudflare.com"))
    # Interface WAN du routeur OpenWrt (utilisée dans les configs générées).
    openwrt_wan_if: str = field(default_factory=lambda: os.environ.get("OPENWRT_WAN_IF", "wan"))
    openwrt_wan_dev: str = field(default_factory=lambda: os.environ.get("OPENWRT_WAN_DEV", "eth1"))

    # Telegram (bot en long polling : aucune ouverture de port nécessaire)
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_ids: list[int] = field(
        default_factory=lambda: [
            int(x) for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x
        ]
    )
    telegram_api: str = field(default_factory=lambda: os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org"))

    # Relais cloud (tableau de bord Vercel) : l'agent pousse son état et récupère les commandes.
    cloud_url: str = field(default_factory=lambda: os.environ.get("CLOUD_URL", "").rstrip("/"))
    cloud_token: str = field(default_factory=lambda: os.environ.get("CLOUD_INGEST_TOKEN", ""))
    cloud_interval_s: float = field(default_factory=lambda: float(os.environ.get("CLOUD_INTERVAL", "20")))

    # Alertes
    alert_outage_s: float = field(default_factory=lambda: float(os.environ.get("ALERT_OUTAGE_SECONDS", "20")))


settings = Settings()
