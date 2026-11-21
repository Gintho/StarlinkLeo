"""Cœur applicatif partagé par l'API web, le bot Telegram et le relais cloud."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from . import bufferbloat, optimizer
from .collector import Collector
from .config import Settings
from .dish import Dish, DishError, make_dish
from .storage import Storage

log = logging.getLogger(__name__)

ACTIONS = {"reboot", "stow", "unstow"}
ACTION_LABELS = {"reboot": "Redémarrage", "stow": "Rangement (stow)", "unstow": "Déploiement (unstow)"}


class ControlError(RuntimeError):
    pass


class Core:
    def __init__(self, cfg: Settings, dish: Dish | None = None):
        self.cfg = cfg
        self.dish = dish or make_dish(cfg.dish_address, cfg.mock)
        self.storage = Storage(cfg.db_path)
        self.collector = Collector(self.dish, self.storage, cfg.poll_interval_s, cfg.retention_days)
        self.test: dict[str, Any] | None = None
        self._test_listeners: list[Callable[[dict[str, Any]], Awaitable[None]]] = []

    # ------------------------------------------------------------------ lecture
    async def diagnostics(self) -> list[dict[str, Any]]:
        s, now = self.storage, time.time()
        tests = await asyncio.to_thread(s.tests, "bufferbloat", 1)
        return optimizer.diagnose(
            self.collector.latest,
            await asyncio.to_thread(s.window_stats, now - 3600),
            await asyncio.to_thread(s.hourly_profile, now - 7 * 86400),
            tests[0] if tests else None,
            self.collector.last_error,
        )

    async def stats(self) -> dict[str, Any]:
        s, now = self.storage, time.time()
        return {
            "1h": await asyncio.to_thread(s.window_stats, now - 3600),
            "24h": await asyncio.to_thread(s.window_stats, now - 86400),
            "hourly_7d": await asyncio.to_thread(s.hourly_profile, now - 7 * 86400),
        }

    async def rates(self) -> optimizer.Rates:
        tests = await asyncio.to_thread(self.storage.tests, "bufferbloat", 30)
        w = await asyncio.to_thread(self.storage.window_stats, time.time() - 7 * 86400)
        return optimizer.recommend_rates(tests, w.get("dl_max"), w.get("ul_max"))

    async def snapshot(self) -> dict[str, Any]:
        """État compact envoyé au tableau de bord cloud."""
        c = self.collector
        s = dict(c.latest or {})
        # La carte par secteur est utile, le reste des tableaux bruts non.
        s.pop("raw_wedges_fraction_obstructed", None)
        tests = await asyncio.to_thread(self.storage.tests, "bufferbloat", 10)
        series = await asyncio.to_thread(self.storage.series, time.time() - 86400, 144)
        return {
            "ts": c.latest_ts,
            "error": c.last_error,
            "mock": self.cfg.mock,
            "controls_enabled": self.cfg.enable_controls,
            "status": s or None,
            "stats": await self.stats(),
            "findings": await self.diagnostics(),
            "bufferbloat": tests,
            "series_24h": series,
            "test": self.test,
        }

    # ------------------------------------------------------------------ actions
    async def control(self, action: str) -> None:
        if not self.cfg.enable_controls:
            raise ControlError("Contrôles désactivés (STARLINK_ENABLE_CONTROLS=0)")
        if action not in ACTIONS:
            raise ControlError(f"Action inconnue : {action}")
        fn = {
            "reboot": self.dish.reboot,
            "stow": lambda: self.dish.stow(unstow=False),
            "unstow": lambda: self.dish.stow(unstow=True),
        }[action]
        try:
            await asyncio.to_thread(fn)
        except DishError as e:
            raise ControlError(str(e)) from e
        log.warning("Action exécutée sur la parabole : %s", action)

    def on_test_done(self, cb: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._test_listeners.append(cb)

    def start_bufferbloat(self, shaped: bool = False) -> dict[str, Any]:
        if self.test and self.test["status"] == "running":
            raise ControlError("Un test est déjà en cours")
        self.test = {"status": "running", "phase": "idle", "started": time.time(), "result": None}
        asyncio.create_task(self._run_test(shaped))
        return self.test

    async def _run_test(self, shaped: bool) -> None:
        test = self.test
        assert test is not None

        async def progress(phase: str):
            test["phase"] = phase

        try:
            if self.cfg.mock:
                for p in ("idle", "download", "upload"):
                    await progress(p)
                    await asyncio.sleep(1)
                result = bufferbloat.simulated_result(shaped=shaped)
            else:
                result = await bufferbloat.run_test(
                    self.cfg.latency_target, self.cfg.speedtest_base_url, progress=progress
                )
            if not result.get("valid", True):
                test.update(status="error", error="Test invalide : " + (result.get("error") or "?"), result=result)
            else:
                await asyncio.to_thread(self.storage.add_test, "bufferbloat", result)
                test.update(status="done", result=result)
        except Exception as e:  # noqa: BLE001
            log.exception("Test bufferbloat échoué")
            test.update(status="error", error=str(e))
        for cb in self._test_listeners:
            try:
                await cb(test)
            except Exception:  # noqa: BLE001
                log.exception("Listener de fin de test en échec")

    # ------------------------------------------------------------------ cycle de vie
    def start(self) -> None:
        self.collector.start()

    async def stop(self) -> None:
        await self.collector.stop()
        self.dish.close()
