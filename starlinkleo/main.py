"""API FastAPI + interface web locale (+ bot Telegram et relais cloud optionnels)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import optimizer
from .config import Settings, settings as default_settings
from .core import ControlError, Core
from .dish import DishError

log = logging.getLogger("starlinkleo")
STATIC = Path(__file__).parent / "static"
RANGES = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        core = Core(cfg)
        app.state.core = core
        services = []
        if cfg.telegram_token:
            from .telegram_bot import TelegramBot

            if not cfg.telegram_chat_ids:
                log.warning("TELEGRAM_ALLOWED_CHAT_IDS vide : le bot répondra seulement /start avec votre chat ID")
            services.append(TelegramBot(core, cfg.telegram_token, cfg.telegram_chat_ids, cfg.telegram_api))
        if cfg.cloud_url and cfg.cloud_token:
            from .cloud_sync import CloudSync

            services.append(CloudSync(core, cfg.cloud_url, cfg.cloud_token, cfg.cloud_interval_s))
        app.state.services = services
        core.start()
        for s in services:
            s.start()
        log.info(
            "StarlinkLeo démarré (%s)%s%s",
            "simulation" if cfg.mock else cfg.dish_address,
            " + Telegram" if cfg.telegram_token else "",
            " + relais cloud" if cfg.cloud_url else "",
        )
        yield
        for s in services:
            await s.stop()
        await core.stop()

    app = FastAPI(title="StarlinkLeo", lifespan=lifespan)

    def core() -> Core:
        return app.state.core

    # ------------------------------------------------------------------ monitoring
    @app.get("/api/status")
    async def status():
        c = core().collector
        if c.latest is None:
            await c.poll_once()
        return {
            "mock": cfg.mock,
            "controls_enabled": cfg.enable_controls,
            "ts": c.latest_ts,
            "age_s": None if c.latest_ts is None else time.time() - c.latest_ts,
            "error": c.last_error,
            "status": c.latest,
        }

    @app.get("/api/history")
    async def history(range: str = "1h"):
        seconds = RANGES.get(range)
        if seconds is None:
            raise HTTPException(400, f"range doit être parmi {list(RANGES)}")
        series = await asyncio.to_thread(core().storage.series, time.time() - seconds)
        return {"range": range, "series": series}

    @app.get("/api/history/live")
    async def history_live(samples: int = 900):
        """Historique seconde par seconde directement depuis la parabole."""
        try:
            return await asyncio.to_thread(core().dish.history, max(1, min(samples, 43200)))
        except DishError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/obstruction-map")
    async def obstruction_map():
        try:
            rows = await asyncio.to_thread(core().dish.obstruction_map)
        except DishError as e:
            raise HTTPException(502, str(e)) from e
        return {"rows": rows}

    @app.get("/api/location")
    async def location():
        try:
            return await asyncio.to_thread(core().dish.location)
        except DishError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/stats")
    async def stats():
        return await core().stats()

    @app.get("/api/diagnostics")
    async def diagnostics():
        return {"findings": await core().diagnostics()}

    @app.get("/api/integrations")
    async def integrations():
        out = {"telegram": bool(cfg.telegram_token), "cloud": None}
        for s in app.state.services:
            if s.__class__.__name__ == "CloudSync":
                out["cloud"] = {"url": cfg.cloud_url, "last_ok": s.last_ok, "error": s.last_error}
        return out

    # ------------------------------------------------------------------ bufferbloat
    @app.post("/api/bufferbloat")
    async def start_bufferbloat(shaped: bool = False):
        try:
            return core().start_bufferbloat(shaped)
        except ControlError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/bufferbloat")
    async def bufferbloat_status():
        return {"current": core().test, "history": await asyncio.to_thread(core().storage.tests, "bufferbloat", 30)}

    # ------------------------------------------------------------------ optimisation
    def _files(rates: optimizer.Rates, dev: str) -> dict[str, str]:
        return {
            "sqm": optimizer.render_sqm_config(rates, dev, autorate=True),
            "sqm_static": optimizer.render_sqm_config(rates, dev, autorate=False),
            "cake_autorate": optimizer.render_cake_autorate_config(rates, dev),
            "install": optimizer.render_install_script(rates, dev),
        }

    @app.get("/api/optimize")
    async def optimize(wan_dev: str | None = None):
        dev = wan_dev or cfg.openwrt_wan_dev
        rates = await core().rates()
        return {"wan_dev": dev, "rates": optimizer.rates_dict(rates), "files": _files(rates, dev)}

    @app.get("/api/optimize/download/{name}", response_class=PlainTextResponse)
    async def optimize_download(name: str, wan_dev: str | None = None):
        files = _files(await core().rates(), wan_dev or cfg.openwrt_wan_dev)
        mapping = {"sqm": "sqm", "config.primary.sh": "cake_autorate", "install_openwrt.sh": "install"}
        if name not in mapping:
            raise HTTPException(404)
        return PlainTextResponse(
            files[mapping[name]], headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    # ------------------------------------------------------------------ contrôle
    @app.post("/api/control/{action}")
    async def control(action: str):
        try:
            await core().control(action)
        except ControlError as e:
            msg = str(e)
            code = 403 if "désactivés" in msg else 404 if "inconnue" in msg else 502
            raise HTTPException(code, msg) from e
        return {"ok": True, "action": action}

    # ------------------------------------------------------------------ front
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app


def run() -> None:  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":  # pragma: no cover
    run()
