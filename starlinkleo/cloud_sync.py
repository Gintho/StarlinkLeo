"""Relais vers le tableau de bord cloud (Vercel).

L'agent local pousse un instantané de l'état toutes les CLOUD_INTERVAL secondes
vers POST {CLOUD_URL}/api/ingest. La réponse contient les commandes en attente
saisies sur le tableau de bord web ; l'agent les exécute et renvoie leur résultat
au push suivant. Aucune connexion entrante vers le réseau local n'est nécessaire.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .core import ACTIONS, ControlError, Core

log = logging.getLogger(__name__)
COMMAND_MAX_AGE_S = 300  # une commande plus vieille est ignorée (agent hors ligne entre-temps)


class CloudSync:
    def __init__(self, core: Core, url: str, token: str, interval_s: float = 20):
        self.core = core
        self.url = url.rstrip("/") + "/api/ingest"
        self.token = token
        self.interval_s = interval_s
        self.client = httpx.AsyncClient(timeout=20)
        self.results: list[dict[str, Any]] = []
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    async def execute(self, cmd: dict[str, Any]) -> dict[str, Any]:
        action, cid = cmd.get("action"), cmd.get("id")
        res = {"id": cid, "action": action, "ts": time.time()}
        if time.time() - float(cmd.get("ts") or 0) > COMMAND_MAX_AGE_S:  # ts en secondes
            return {**res, "ok": False, "error": "commande expirée"}
        try:
            if action == "bufferbloat":
                self.core.start_bufferbloat()
            elif action in ACTIONS:
                await self.core.control(action)
            else:
                raise ControlError(f"action inconnue : {action}")
            return {**res, "ok": True}
        except ControlError as e:
            return {**res, "ok": False, "error": str(e)}

    async def push_once(self) -> list[dict[str, Any]]:
        payload = await self.core.snapshot()
        payload["command_results"] = self.results
        r = await self.client.post(self.url, json=payload, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        self.results = []
        commands = r.json().get("commands") or []
        for cmd in commands:
            log.warning("Commande reçue du tableau de bord cloud : %s", cmd.get("action"))
            self.results.append(await self.execute(cmd))
        self.last_ok, self.last_error = time.time(), None
        return commands

    async def run(self) -> None:
        while True:
            try:
                cmds = await self.push_once()
                # Si une commande vient d'arriver, on renvoie vite son résultat.
                await asyncio.sleep(2 if cmds else self.interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.warning("Synchro cloud impossible : %s", e)
                await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.client.aclose()
