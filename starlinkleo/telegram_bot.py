"""Bot Telegram : alertes poussées + commandes de contrôle.

Fonctionne en long polling (getUpdates) : uniquement des connexions sortantes,
aucun port à ouvrir. Seuls les chat IDs listés dans TELEGRAM_ALLOWED_CHAT_IDS
peuvent lire l'état ou piloter la parabole.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any

import httpx

from .alerts import AlertMonitor
from .core import ACTION_LABELS, ACTIONS, ControlError, Core

log = logging.getLogger(__name__)
CONFIRM_TTL_S = 120

HELP = (
    "🛰️ <b>StarlinkLeo</b>\n"
    "/etat — état de la parabole\n"
    "/diag — diagnostic et recommandations\n"
    "/test — test de latence sous charge (bufferbloat)\n"
    "/optim — débits recommandés SQM / cake-autorate\n"
    "/reboot — redémarrer la parabole\n"
    "/stow — ranger la parabole\n"
    "/unstow — déployer la parabole\n"
    "/aide — cette aide"
)


def _f(v: Any, d: int = 1, unit: str = "") -> str:
    if v is None:
        return "–"
    return f"{v:.{d}f}{unit}"


def format_status(core: Core) -> str:
    c = core.collector
    s = c.latest
    if not s:
        return f"🔴 <b>Parabole injoignable</b>\n{html.escape(c.last_error or 'aucune donnée')}"
    age = time.time() - (c.latest_ts or 0)
    up = s.get("uptime") or 0
    alerts = ", ".join(s.get("active_alert_labels") or []) or "aucune"
    icon = "🟢" if s.get("state") == "CONNECTED" and (s.get("pop_ping_drop_rate") or 0) < 0.05 else "🟠"
    return (
        f"{icon} <b>{html.escape(str(s.get('state')))}</b>{' (simulation)' if core.cfg.mock else ''}\n"
        f"⬇️ {_f((s.get('downlink_throughput_bps') or 0) / 1e6)} Mb/s   ⬆️ {_f((s.get('uplink_throughput_bps') or 0) / 1e6)} Mb/s\n"
        f"⏱️ Latence {_f(s.get('pop_ping_latency_ms'), 0)} ms · perte {_f((s.get('pop_ping_drop_rate') or 0) * 100)} %\n"
        f"🌳 Obstruction {_f((s.get('fraction_obstructed') or 0) * 100, 2)} %\n"
        f"⚡ {_f(s.get('power_w'), 0)} W · uptime {up // 86400} j {(up % 86400) // 3600} h\n"
        f"🧭 Az {_f(s.get('direction_azimuth'))}° · él {_f(s.get('direction_elevation'))}°\n"
        f"⚠️ Alertes : {html.escape(alerts)}\n"
        f"<i>mis à jour il y a {age:.0f} s</i>"
    )


def format_findings(findings: list[dict[str, Any]]) -> str:
    icons = {"critical": "🔴", "warning": "🟠", "info": "🔵", "ok": "🟢"}
    lines = ["🩺 <b>Diagnostic</b>"]
    for f in findings:
        lines.append(f"{icons.get(f['severity'], '•')} <b>{html.escape(f['title'])}</b>")
        if f.get("action") and f["severity"] != "ok":
            lines.append(f"   → {html.escape(f['action'])}")
    return "\n".join(lines)


def format_test(test: dict[str, Any]) -> str:
    if test.get("status") == "error":
        return f"❌ Test échoué : {html.escape(str(test.get('error')))}"
    r = test["result"]
    return (
        f"📶 <b>Bufferbloat : note {r['grade']}</b>\n"
        f"Repos : {_f(r['idle']['median_ms'], 0)} ms\n"
        f"Download : {_f(r['download']['mbps'])} Mb/s, +{_f(r['download']['increase_ms'], 0)} ms ({r['download']['grade']})\n"
        f"Upload : {_f(r['upload']['mbps'])} Mb/s, +{_f(r['upload']['increase_ms'], 0)} ms ({r['upload']['grade']})"
    )


class TelegramBot:
    def __init__(self, core: Core, token: str, chat_ids: list[int], api: str = "https://api.telegram.org"):
        self.core = core
        self.chat_ids = set(chat_ids)
        self.base = f"{api.rstrip('/')}/bot{token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))
        self.monitor = AlertMonitor(core.cfg.alert_outage_s)
        self._offset = 0
        self._task: asyncio.Task | None = None
        core.collector.listeners.append(self._on_poll)
        core.on_test_done(self._on_test_done)

    # ------------------------------------------------------------------ API Telegram
    async def call(self, method: str, **params) -> Any:
        r = await self.client.post(f"{self.base}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str, **kw) -> None:
        try:
            await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                            disable_web_page_preview=True, **kw)
        except Exception as e:  # noqa: BLE001
            log.warning("Envoi Telegram impossible : %s", e)

    async def broadcast(self, text: str) -> None:
        for cid in self.chat_ids:
            await self.send(cid, text)

    # ------------------------------------------------------------------ événements
    async def _on_poll(self, status, error) -> None:
        for msg in self.monitor.update(status, error):
            await self.broadcast(msg)

    async def _on_test_done(self, test: dict[str, Any]) -> None:
        await self.broadcast(format_test(test))

    # ------------------------------------------------------------------ commandes
    async def handle_update(self, upd: dict[str, Any]) -> None:
        if "callback_query" in upd:
            await self._handle_callback(upd["callback_query"])
            return
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if not text or chat_id is None:
            return
        if chat_id not in self.chat_ids:
            if text.startswith("/start"):
                await self.send(chat_id, f"⛔ Non autorisé. Votre chat ID est <code>{chat_id}</code> : "
                                         "ajoutez-le à TELEGRAM_ALLOWED_CHAT_IDS.")
            log.warning("Message Telegram refusé de chat %s", chat_id)
            return
        cmd = text.split()[0].split("@")[0].lower()
        await self.handle_command(chat_id, cmd)

    async def handle_command(self, chat_id: int, cmd: str) -> None:
        if cmd in {"/start", "/aide", "/help"}:
            await self.send(chat_id, HELP)
        elif cmd in {"/etat", "/status"}:
            await self.send(chat_id, format_status(self.core))
        elif cmd == "/diag":
            await self.send(chat_id, format_findings(await self.core.diagnostics()))
        elif cmd == "/test":
            try:
                self.core.start_bufferbloat()
                await self.send(chat_id, "⏳ Test lancé (~30 s). Le résultat arrivera ici.")
            except ControlError as e:
                await self.send(chat_id, f"❌ {html.escape(str(e))}")
        elif cmd == "/optim":
            r = await self.core.rates()
            await self.send(
                chat_id,
                "⚙️ <b>Shaper recommandé</b> (" + r.source + ")\n"
                f"Download : {r.min_dl // 1000}–{r.max_dl // 1000} Mb/s (base {r.base_dl // 1000})\n"
                f"Upload : {r.min_ul // 1000}–{r.max_ul // 1000} Mb/s (base {r.base_ul // 1000})\n"
                f"SQM fixe : {r.static_dl // 1000} / {r.static_ul // 1000} Mb/s\n"
                "Configs complètes dans l'onglet Optimisation de l'interface web.",
            )
        elif cmd.lstrip("/") in ACTIONS:
            action = cmd.lstrip("/")
            if not self.core.cfg.enable_controls:
                await self.send(chat_id, "⛔ Contrôles désactivés sur l'agent.")
                return
            stamp = int(time.time())
            await self.send(
                chat_id,
                f"Confirmer : <b>{ACTION_LABELS[action]}</b> de la parabole ?",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Confirmer", "callback_data": f"ok:{action}:{stamp}"},
                    {"text": "Annuler", "callback_data": "cancel"},
                ]]},
            )
        else:
            await self.send(chat_id, "Commande inconnue.\n\n" + HELP)

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        msg_id = (cq.get("message") or {}).get("message_id")
        data = cq.get("data") or ""
        try:
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:  # noqa: BLE001
            pass
        if chat_id not in self.chat_ids:
            return

        async def edit(text: str):
            try:
                await self.call("editMessageText", chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")
            except Exception:  # noqa: BLE001
                await self.send(chat_id, text)

        if data == "cancel":
            await edit("Annulé.")
            return
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "ok" or parts[1] not in ACTIONS:
            return
        action, stamp = parts[1], int(parts[2])
        if time.time() - stamp > CONFIRM_TTL_S:
            await edit("⌛ Confirmation expirée, relancez la commande.")
            return
        try:
            await self.core.control(action)
            await edit(f"✅ {ACTION_LABELS[action]} envoyé à la parabole.")
        except ControlError as e:
            await edit(f"❌ Échec : {html.escape(str(e))}")

    # ------------------------------------------------------------------ boucle
    async def run(self) -> None:
        try:
            await self.call("setMyCommands", commands=[
                {"command": c, "description": d} for c, d in [
                    ("etat", "État de la parabole"), ("diag", "Diagnostic"), ("test", "Test bufferbloat"),
                    ("optim", "Débits recommandés"), ("reboot", "Redémarrer"), ("stow", "Ranger"),
                    ("unstow", "Déployer"), ("aide", "Aide"),
                ]
            ])
        except Exception as e:  # noqa: BLE001
            log.warning("setMyCommands : %s", e)
        await self.broadcast("🛰️ StarlinkLeo démarré. /aide pour les commandes.")
        while True:
            try:
                updates = await self.call("getUpdates", offset=self._offset, timeout=50,
                                          allowed_updates=["message", "callback_query"])
                for u in updates:
                    self._offset = u["update_id"] + 1
                    await self.handle_update(u)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram getUpdates : %s", e)
                await asyncio.sleep(5)

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
