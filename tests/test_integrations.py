import asyncio
import json
import time

import httpx
import pytest

from starlinkleo.alerts import AlertMonitor
from starlinkleo.cloud_sync import CloudSync
from starlinkleo.config import Settings
from starlinkleo.core import Core
from starlinkleo.dish import MockDish
from starlinkleo.telegram_bot import TelegramBot, format_status


def ok_status(**kw):
    s = MockDish(seed=3).status()
    s.update({"pop_ping_drop_rate": 0.0, "fraction_obstructed": 0.001, "active_alerts": [], **kw})
    return s


def test_alert_unreachable_then_recovery():
    m = AlertMonitor(outage_threshold_s=20)
    assert m.update(ok_status(), None, now=0) == []
    assert m.update(None, "timeout", now=10) == []          # sous le seuil
    msgs = m.update(None, "timeout", now=31)
    assert len(msgs) == 1 and "injoignable" in msgs[0]
    assert m.update(None, "timeout", now=40) == []          # pas de doublon
    back = m.update(ok_status(), None, now=100)
    assert len(back) == 1 and "joignable" in back[0] and "1 min" in back[0]


def test_alert_outage_and_hw_alerts():
    m = AlertMonitor(outage_threshold_s=5)
    m.update(ok_status(), None, now=0)
    m.update(ok_status(pop_ping_drop_rate=1.0), None, now=1)
    assert any("Coupure" in x for x in m.update(ok_status(pop_ping_drop_rate=1.0), None, now=7))
    assert any("rétabli" in x for x in m.update(ok_status(), None, now=9))
    msgs = m.update(ok_status(active_alerts=["alert_thermal_throttle"]), None, now=10)
    assert any("Bridage thermique" in x for x in msgs)
    assert any("terminée" in x for x in m.update(ok_status(), None, now=11))
    assert any("Obstructions" in x for x in m.update(ok_status(fraction_obstructed=0.05), None, now=12))


@pytest.fixture
def core(tmp_path):
    return Core(Settings(mock=True, db_path=str(tmp_path / "c.db")))


class FakeBot(TelegramBot):
    def __init__(self, core, chat_ids):
        super().__init__(core, "TOKEN", chat_ids)
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        return True


def test_telegram_auth_and_commands(core):
    async def scenario():
        await core.collector.poll_once()
        bot = FakeBot(core, [42])
        # Inconnu : seul /start répond, avec son chat id
        await bot.handle_update({"update_id": 1, "message": {"chat": {"id": 7}, "text": "/start"}})
        assert "<code>7</code>" in bot.calls[-1][1]["text"]
        n = len(bot.calls)
        await bot.handle_update({"update_id": 2, "message": {"chat": {"id": 7}, "text": "/reboot"}})
        assert len(bot.calls) == n  # ignoré

        await bot.handle_update({"update_id": 3, "message": {"chat": {"id": 42}, "text": "/etat"}})
        assert "CONNECTED" in bot.calls[-1][1]["text"]
        await bot.handle_update({"update_id": 4, "message": {"chat": {"id": 42}, "text": "/stow"}})
        kb = bot.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        assert kb.startswith("ok:stow:")
        # Confirmation par un autre chat : refusée
        await bot.handle_update({"update_id": 5, "callback_query": {"id": "x", "data": kb, "message": {"chat": {"id": 7}, "message_id": 1}}})
        assert core.collector.dish.status()["state"] == "CONNECTED"
        # Confirmation expirée
        old = f"ok:stow:{int(time.time()) - 999}"
        await bot.handle_update({"update_id": 6, "callback_query": {"id": "y", "data": old, "message": {"chat": {"id": 42}, "message_id": 1}}})
        assert "expirée" in bot.calls[-1][1]["text"]
        assert core.collector.dish.status()["state"] == "CONNECTED"
        # Confirmation valide
        await bot.handle_update({"update_id": 7, "callback_query": {"id": "z", "data": kb, "message": {"chat": {"id": 42}, "message_id": 1}}})
        assert core.collector.dish.status()["state"] == "STOWED"
        await bot.handle_update({"update_id": 8, "message": {"chat": {"id": 42}, "text": "/diag"}})
        assert "Diagnostic" in bot.calls[-1][1]["text"]
        await bot.client.aclose()

    asyncio.run(scenario())


def test_format_status_unreachable(core):
    core.collector.last_error = "timeout <x>"
    assert "&lt;x&gt;" in format_status(core)


def test_cloud_sync_roundtrip(core):
    seen = {}
    queue = [[{"id": "a", "action": "stow", "ts": time.time()},
              {"id": "b", "action": "reboot", "ts": time.time() - 3600}], []]

    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer tok"
        body = json.loads(request.content)
        seen.setdefault("bodies", []).append(body)
        return httpx.Response(200, json={"ok": True, "commands": queue.pop(0)})

    async def scenario():
        await core.collector.poll_once()
        sync = CloudSync(core, "https://x.vercel.app", "tok")
        sync.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await sync.push_once()
        await sync.push_once()
        await sync.client.aclose()

    asyncio.run(scenario())
    first, second = seen["bodies"]
    assert first["status"]["state"] == "CONNECTED" and first["findings"]
    res = {r["id"]: r for r in second["command_results"]}
    assert res["a"]["ok"] is True
    assert res["b"]["ok"] is False and "expirée" in res["b"]["error"]
    assert core.dish.status()["state"] == "STOWED"
