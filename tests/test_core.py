import time

import pytest
from fastapi.testclient import TestClient

from starlinkleo import bufferbloat, optimizer
from starlinkleo.config import Settings
from starlinkleo.dish import MockDish
from starlinkleo.main import create_app
from starlinkleo.storage import Storage


def test_grade_thresholds():
    assert bufferbloat.grade(2) == "A+"
    assert bufferbloat.grade(20) == "A"
    assert bufferbloat.grade(45) == "B"
    assert bufferbloat.grade(150) == "C"
    assert bufferbloat.grade(300) == "D"
    assert bufferbloat.grade(900) == "F"
    assert bufferbloat.grade(None) == "?"


def test_summarize_increase():
    idle = bufferbloat.Phase("idle", [30, 31, 32], 0, 5)
    down = bufferbloat.Phase("download", [80, 90, 100], 150_000_000 // 8 * 10, 10)
    up = bufferbloat.Phase("upload", [31, 33, 32], 15_000_000 // 8 * 10, 10)
    r = bufferbloat.summarize(idle, down, up)
    assert r["download"]["increase_ms"] == pytest.approx(59)
    assert r["download"]["grade"] == "B"
    assert r["upload"]["grade"] == "A+"
    assert r["grade"] == "B"
    assert r["download"]["mbps"] == pytest.approx(150, rel=0.01)


def test_recommend_rates_from_tests():
    tests = [bufferbloat.simulated_result() for _ in range(5)]
    r = optimizer.recommend_rates(tests)
    assert r.source == "mesures"
    assert r.min_dl <= r.base_dl <= r.max_dl
    assert r.min_ul <= r.base_ul <= r.max_ul
    assert r.min_dl >= 5000 and r.min_ul >= 2000


def test_recommend_rates_default():
    r = optimizer.recommend_rates([])
    assert r.source == "défaut"
    assert r.max_dl > r.base_dl > r.min_dl


def test_configs_render():
    r = optimizer.recommend_rates([])
    car = optimizer.render_cake_autorate_config(r, "eth1")
    assert "dl_if=ifb4eth1" in car and "ul_if=eth1" in car
    assert f"max_dl_shaper_rate_kbps={r.max_dl}" in car
    sqm = optimizer.render_sqm_config(r, "eth1")
    assert "option interface 'eth1'" in sqm and f"option download '{r.base_dl}'" in sqm
    script = optimizer.render_install_script(r, "eth1")
    assert script.startswith("#!/bin/sh")
    assert "service cake-autorate enable" in script


def test_diagnose_unreachable():
    f = optimizer.diagnose(None, None, dish_error="timeout")
    assert f[0]["severity"] == "critical"


def test_diagnose_obstruction_and_bufferbloat():
    status = MockDish(seed=1).status()
    status["fraction_obstructed"] = 0.05
    status["active_alerts"] = ["alert_thermal_throttle"]
    bb = bufferbloat.simulated_result(shaped=False)
    f = optimizer.diagnose(status, {"n": 100, "drop_avg": 0.0, "lat_p95": 40}, [], bb)
    cats = {x["category"] for x in f if x["severity"] in {"critical", "warning"}}
    assert {"obstruction", "matériel", "bufferbloat"} <= cats
    assert f[0]["severity"] == "critical"


def test_storage_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    dish = MockDish(seed=2)
    now = time.time()
    for i in range(30):
        s.add_sample(now - 30 + i, dish.status())
    series = s.series(now - 60, buckets=10)
    assert series["ts"] and len(series["dl_bps"]) <= 11
    stats = s.window_stats(now - 60)
    assert stats["n"] == 30 and stats["lat_p50"] is not None
    tid = s.add_test("bufferbloat", {"grade": "A"})
    assert s.tests("bufferbloat")[0]["id"] == tid


@pytest.fixture
def client(tmp_path):
    cfg = Settings(mock=True, db_path=str(tmp_path / "app.db"), poll_interval_s=0.2)
    with TestClient(create_app(cfg)) as c:
        yield c


def test_api_flow(client):
    r = client.get("/api/status").json()
    assert r["mock"] is True and r["status"]["state"] == "CONNECTED"
    assert client.get("/api/history?range=1h").status_code == 200
    assert client.get("/api/history?range=bad").status_code == 400
    assert len(client.get("/api/obstruction-map").json()["rows"]) == 123
    assert client.get("/api/diagnostics").json()["findings"]
    opt = client.get("/api/optimize?wan_dev=wan").json()
    assert "dl_if=ifb4wan" in opt["files"]["cake_autorate"]
    d = client.get("/api/optimize/download/install_openwrt.sh")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    assert client.get("/").status_code == 200


def test_api_bufferbloat_mock(client):
    assert client.post("/api/bufferbloat").json()["status"] == "running"
    for _ in range(50):
        cur = client.get("/api/bufferbloat").json()
        if cur["current"]["status"] != "running":
            break
        time.sleep(0.1)
    assert cur["current"]["status"] == "done"
    assert cur["history"][0]["grade"] in {"C", "D", "F"}
    rates = client.get("/api/optimize").json()["rates"]
    assert rates["source"] == "mesures"


def test_api_controls(client):
    assert client.post("/api/control/stow").json()["ok"]
    assert client.get("/api/status").json()["status"] is not None
    assert client.post("/api/control/unstow").json()["ok"]
    assert client.post("/api/control/explode").status_code == 404


def test_summarize_invalid_when_no_load():
    idle = bufferbloat.Phase("idle", [1, 1, 1], 0, 2)
    down = bufferbloat.Phase("download", [1, 1], 0, 4, error="ConnectError: 403")
    up = bufferbloat.Phase("upload", [1, 1], 0, 4)
    r = bufferbloat.summarize(idle, down, up)
    assert r["valid"] is False and r["grade"] == "?"
    assert "403" in r["error"]


def test_diagnose_ignores_drop_with_few_samples():
    status = MockDish(seed=1).status()
    few = optimizer.diagnose(status, {"n": 10, "drop_avg": 0.2, "lat_p95": 40})
    many = optimizer.diagnose(status, {"n": 600, "drop_avg": 0.2, "lat_p95": 40})
    assert not any(x["category"] == "qualité" for x in few)
    assert any(x["category"] == "qualité" and x["severity"] == "critical" for x in many)
