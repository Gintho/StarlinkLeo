"""Détection d'événements à notifier (coupures, alertes matérielles, obstructions...)."""
from __future__ import annotations

import time
from typing import Any

from .dish import ALERT_LABELS

# Alertes de la parabole qui méritent une notification
NOTIFY_ALERTS = {
    "alert_motors_stuck", "alert_thermal_shutdown", "alert_thermal_throttle",
    "alert_power_supply_thermal_throttle", "alert_mast_not_near_vertical", "alert_slow_ethernet_speeds",
    "alert_slow_ethernet_speeds_100", "alert_dish_water_detected", "alert_router_water_detected",
    "alert_no_ethernet_link", "alert_unexpected_location", "alert_lower_signal_than_predicted",
}


def _dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


class AlertMonitor:
    """Machine à états : transforme la suite des statuts en messages (début / fin d'incident)."""

    def __init__(self, outage_threshold_s: float = 20.0, obstruction_threshold: float = 0.02):
        self.threshold = outage_threshold_s
        self.obstruction_threshold = obstruction_threshold
        self._down_since: dict[str, float] = {}  # incident -> début
        self._notified: set[str] = set()
        self._alerts: set[str] = set()
        self._flags: set[str] = set()
        self._first = True

    def _track(self, key: str, active: bool, now: float, start_msg: str, end_msg: str) -> list[str]:
        out: list[str] = []
        if active:
            since = self._down_since.setdefault(key, now)
            if key not in self._notified and now - since >= self.threshold:
                self._notified.add(key)
                out.append(start_msg)
        else:
            since = self._down_since.pop(key, None)
            if key in self._notified:
                self._notified.discard(key)
                out.append(end_msg.format(duree=_dur(now - since if since else 0)))
        return out

    def update(self, status: dict[str, Any] | None, error: str | None, now: float | None = None) -> list[str]:
        now = now or time.time()
        out: list[str] = []
        out += self._track(
            "unreachable", status is None, now,
            f"🔴 <b>Parabole injoignable</b>\n{error or ''}".strip(),
            "🟢 <b>Parabole de nouveau joignable</b> (après {duree})",
        )
        if status is None:
            return out

        state = status.get("state")
        out += self._track(
            "state", bool(state) and state != "CONNECTED", now,
            f"🟠 <b>Parabole non connectée</b> — état : {state}",
            "🟢 <b>Parabole reconnectée</b> (après {duree})",
        )
        out += self._track(
            "outage", (status.get("pop_ping_drop_rate") or 0) >= 1.0 and state == "CONNECTED", now,
            "🔴 <b>Coupure Internet</b> — 100 % de perte vers le PoP Starlink",
            "🟢 <b>Internet rétabli</b> (coupure de {duree})",
        )

        alerts = {a for a in (status.get("active_alerts") or []) if a in NOTIFY_ALERTS}
        if not self._first:
            for a in sorted(alerts - self._alerts):
                out.append(f"⚠️ <b>Alerte parabole</b> : {ALERT_LABELS.get(a, a)}")
            for a in sorted(self._alerts - alerts):
                out.append(f"✅ Alerte terminée : {ALERT_LABELS.get(a, a)}")
        self._alerts = alerts

        frac = status.get("fraction_obstructed") or 0.0
        if frac >= self.obstruction_threshold and "obstruction" not in self._flags:
            self._flags.add("obstruction")
            out.append(f"🌳 <b>Obstructions élevées</b> : {frac:.1%} du temps. Voir la carte d'obstruction.")
        elif frac < self.obstruction_threshold * 0.5:
            self._flags.discard("obstruction")

        if status.get("software_update_reboot_ready") and "update" not in self._flags:
            self._flags.add("update")
            out.append("🔄 <b>Mise à jour firmware prête</b> — redémarrage nécessaire (/reboot).")
        elif not status.get("software_update_reboot_ready"):
            self._flags.discard("update")

        self._first = False
        return out
