"""Collecte périodique du statut de la parabole."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .dish import Dish, DishError
from .storage import Storage

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, dish: Dish, storage: Storage, interval_s: float, retention_days: int):
        self.dish = dish
        self.storage = storage
        self.interval_s = interval_s
        self.retention_days = retention_days
        self.latest: dict[str, Any] | None = None
        self.latest_ts: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._last_prune = 0.0
        # Appelés après chaque collecte : cb(status | None, erreur | None)
        self.listeners: list = []

    async def _notify(self, status, error) -> None:
        for cb in self.listeners:
            try:
                await cb(status, error)
            except Exception:  # noqa: BLE001
                log.exception("Listener de collecte en échec")

    async def poll_once(self) -> None:
        try:
            status = await asyncio.to_thread(self.dish.status)
        except DishError as e:
            self.last_error = str(e)
            log.warning("Parabole injoignable : %s", e)
            await self._notify(None, self.last_error)
            return
        ts = time.time()
        if status.get("power_w") is None:
            # La puissance n'est disponible que dans l'historique : on prend le dernier échantillon.
            try:
                hist = await asyncio.to_thread(self.dish.history, 1)
                if hist.get("power_w"):
                    status["power_w"] = hist["power_w"][-1]
            except DishError:
                pass
        self.latest, self.latest_ts, self.last_error = status, ts, None
        await asyncio.to_thread(self.storage.add_sample, ts, status)
        if ts - self._last_prune > 3600:
            self._last_prune = ts
            await asyncio.to_thread(self.storage.prune, ts - self.retention_days * 86400)
        await self._notify(status, None)

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
                log.exception("Erreur de collecte")
            await asyncio.sleep(max(0.1, self.interval_s - (time.monotonic() - started)))

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
