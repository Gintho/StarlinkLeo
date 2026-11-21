"""Accès à la parabole Starlink : client gRPC réel (starlink-grpc-core) ou simulateur."""
from __future__ import annotations

import math
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

# Alertes connues -> libellé français
ALERT_LABELS = {
    "alert_motors_stuck": "Moteurs bloqués",
    "alert_thermal_shutdown": "Arrêt thermique",
    "alert_thermal_throttle": "Bridage thermique",
    "alert_unexpected_location": "Position inattendue",
    "alert_mast_not_near_vertical": "Mât non vertical",
    "alert_slow_ethernet_speeds": "Lien Ethernet lent (<1 Gb/s)",
    "alert_slow_ethernet_speeds_100": "Lien Ethernet lent (100 Mb/s)",
    "alert_roaming": "Itinérance",
    "alert_install_pending": "Mise à jour en attente",
    "alert_is_heating": "Chauffage (fonte neige) actif",
    "alert_power_supply_thermal_throttle": "Bridage thermique alimentation",
    "alert_is_power_save_idle": "Mode économie d'énergie",
    "alert_moving_while_not_mobile": "En mouvement (abonnement non mobile)",
    "alert_moving_fast_while_not_aviation": "Vitesse élevée (non aviation)",
    "alert_dbf_telem_stale": "Télémétrie DBF périmée",
    "alert_moving_too_fast_for_policy": "Trop rapide pour l'abonnement",
    "alert_low_motor_current": "Courant moteur faible",
    "alert_lower_signal_than_predicted": "Signal plus faible que prévu",
    "alert_obstruction_map_reset": "Carte d'obstruction réinitialisée",
    "alert_dish_water_detected": "Eau détectée (parabole)",
    "alert_router_water_detected": "Eau détectée (routeur)",
    "alert_upsu_router_port_slow": "Port routeur lent",
    "alert_no_ethernet_link": "Pas de lien Ethernet",
}


class DishError(RuntimeError):
    pass


class Dish(ABC):
    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Statut courant, aplati (statut + obstructions + alertes)."""

    @abstractmethod
    def history(self, samples: int = 900) -> dict[str, list]:
        """Historique seconde par seconde (latence, perte, débits, puissance...)."""

    @abstractmethod
    def obstruction_map(self) -> list[list[float]]:
        """Carte d'obstruction : lignes x colonnes, SNR 0..1, -1 = pas de donnée."""

    @abstractmethod
    def location(self) -> dict[str, Any]: ...

    @abstractmethod
    def reboot(self) -> None: ...

    @abstractmethod
    def stow(self, unstow: bool = False) -> None: ...

    def close(self) -> None:
        pass


def _flatten_status(status: dict, obstruction: dict, alerts: dict) -> dict[str, Any]:
    out = dict(status)
    out["wedges_fraction_obstructed"] = obstruction.get("wedges_fraction_obstructed[]") or obstruction.get(
        "wedges_fraction_obstructed"
    )
    out["raw_wedges_fraction_obstructed"] = obstruction.get("raw_wedges_fraction_obstructed[]") or obstruction.get(
        "raw_wedges_fraction_obstructed"
    )
    out["valid_s"] = obstruction.get("valid_s")
    active = [k for k, v in alerts.items() if v]
    out["active_alerts"] = active
    out["active_alert_labels"] = [ALERT_LABELS.get(a, a.removeprefix("alert_")) for a in active]
    return out


class GrpcDish(Dish):
    """Client réel, basé sur le module starlink_grpc de sparky8512/starlink-grpc-tools."""

    def __init__(self, address: str):
        try:
            import starlink_grpc  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise DishError("Le paquet starlink-grpc-core n'est pas installé") from e
        self._sg = starlink_grpc
        self._ctx = starlink_grpc.ChannelContext(target=address)
        self._lock = threading.Lock()

    def _call(self, fn, *args, **kwargs):
        with self._lock:
            try:
                return fn(*args, context=self._ctx, **kwargs)
            except self._sg.GrpcError as e:
                # Force la reconnexion au prochain appel
                self._ctx.close()
                raise DishError(str(e)) from e

    def status(self) -> dict[str, Any]:
        data = self._call(self._sg.status_data)
        status, obstruction, alerts = data[0], data[1], data[2]
        return _flatten_status(status, obstruction, alerts)

    def history(self, samples: int = 900) -> dict[str, list]:
        _general, bulk = self._call(self._sg.history_bulk_data, samples)
        return {k: list(v) for k, v in bulk.items()}

    def obstruction_map(self) -> list[list[float]]:
        return [list(r) for r in self._call(self._sg.obstruction_map)]

    def location(self) -> dict[str, Any]:
        return self._call(self._sg.location_data)

    def reboot(self) -> None:
        self._call(self._sg.reboot)

    def stow(self, unstow: bool = False) -> None:
        self._call(self._sg.set_stow_state, unstow=unstow)

    def close(self) -> None:
        self._ctx.close()


class MockDish(Dish):
    """Simulateur réaliste pour développer sans parabole.

    Reproduit : variation journalière du débit, pics de latence aux changements
    de satellite (secondes 12/27/42/57 de chaque minute), obstructions au nord-est.
    """

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._stowed = False
        self._boot = time.time()

    def _sample(self, t: float) -> dict[str, Any]:
        rng = self._rng
        hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
        # Congestion en soirée (19h-23h)
        congestion = 0.55 if 19 <= hour <= 23 else (0.8 if 17 <= hour < 19 else 1.0)
        sec = int(t) % 60
        switch = min(abs(sec - s) for s in (12, 27, 42, 57)) <= 0
        obstructed = rng.random() < 0.003
        lat = 28 + 8 * rng.random() + (35 * rng.random() if switch else 0)
        drop = 1.0 if obstructed else (0.05 * rng.random() if switch else 0.0)
        load = 0.5 + 0.5 * math.sin(t / 37)
        dl = max(0.0, (40e6 + 150e6 * load) * congestion * (0.8 + 0.4 * rng.random()))
        ul = max(0.0, (3e6 + 12e6 * load) * congestion * (0.8 + 0.4 * rng.random()))
        if self._stowed:
            dl = ul = 0.0
            drop = 1.0
        return {
            "latency": lat,
            "drop": drop,
            "dl": dl * (1 - drop),
            "ul": ul * (1 - drop),
            "power": 55 + 25 * load + rng.random() * 5,
            "obstructed": obstructed,
        }

    def status(self) -> dict[str, Any]:
        s = self._sample(time.time())
        wedges = [0.0] * 12
        wedges[1] = 0.031
        wedges[2] = 0.012
        alerts = {k: False for k in ALERT_LABELS}
        return _flatten_status(
            {
                "id": "ut01000000-00000000-mock0001",
                "hardware_version": "rev4_prod2",
                "software_version": "mock-2026.11.0",
                "state": "STOWED" if self._stowed else "CONNECTED",
                "uptime": int(time.time() - self._boot) + 86400,
                "pop_ping_drop_rate": s["drop"],
                "pop_ping_latency_ms": s["latency"],
                "downlink_throughput_bps": s["dl"],
                "uplink_throughput_bps": s["ul"],
                "fraction_obstructed": 0.0043,
                "currently_obstructed": s["obstructed"],
                "obstruction_duration": 1.2,
                "obstruction_interval": 600.0,
                "direction_azimuth": 12.4,
                "direction_elevation": 64.8,
                "is_snr_above_noise_floor": True,
                "gps_ready": True,
                "gps_sats": 14,
                "software_update_state": "IDLE",
                "seconds_to_first_nonempty_slot": 0.0,
                "power_w": s["power"],
            },
            {"wedges_fraction_obstructed[]": wedges, "valid_s": 86400.0},
            alerts,
        )

    def history(self, samples: int = 900) -> dict[str, list]:
        now = time.time()
        rows = [self._sample(now - samples + i) for i in range(samples)]
        return {
            "pop_ping_latency_ms": [r["latency"] for r in rows],
            "pop_ping_drop_rate": [r["drop"] for r in rows],
            "downlink_throughput_bps": [r["dl"] for r in rows],
            "uplink_throughput_bps": [r["ul"] for r in rows],
            "power_w": [r["power"] for r in rows],
            "obstructed": [r["obstructed"] for r in rows],
        }

    def obstruction_map(self) -> list[list[float]]:
        size = 123
        c = size / 2
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                dx, dy = x - c, c - y
                r = math.hypot(dx, dy)
                if r > c:
                    row.append(-1.0)
                    continue
                az = (math.degrees(math.atan2(dx, dy)) + 360) % 360
                # Données présentes sur des "traces" de satellites
                if self._rng.random() > 0.35:
                    row.append(-1.0)
                    continue
                # Obstruction (arbre) au NE, près de l'horizon
                if 30 <= az <= 70 and r > c * 0.7:
                    row.append(0.0)
                else:
                    row.append(1.0)
            rows.append(row)
        return rows

    def location(self) -> dict[str, Any]:
        return {"latitude": 47.2184, "longitude": -1.5536, "altitude": 12.0}

    def reboot(self) -> None:
        self._boot = time.time()

    def stow(self, unstow: bool = False) -> None:
        self._stowed = not unstow


def make_dish(address: str, mock: bool) -> Dish:
    return MockDish() if mock else GrpcDish(address)
