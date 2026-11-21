"""Stockage SQLite des échantillons et des résultats de tests."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    state TEXT,
    latency_ms REAL,
    drop_rate REAL,
    dl_bps REAL,
    ul_bps REAL,
    power_w REAL,
    obstructed INTEGER,
    fraction_obstructed REAL,
    azimuth REAL,
    elevation REAL,
    alerts TEXT
);
CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tests_kind_ts ON tests(kind, ts);
"""

SAMPLE_FIELDS = ("latency_ms", "drop_rate", "dl_bps", "ul_bps", "power_w", "obstructed")


class Storage:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")

    def add_sample(self, ts: float, status: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    status.get("state"),
                    status.get("pop_ping_latency_ms"),
                    status.get("pop_ping_drop_rate"),
                    status.get("downlink_throughput_bps"),
                    status.get("uplink_throughput_bps"),
                    status.get("power_w"),
                    1 if status.get("currently_obstructed") else 0,
                    status.get("fraction_obstructed"),
                    status.get("direction_azimuth"),
                    status.get("direction_elevation"),
                    json.dumps(status.get("active_alerts") or []),
                ),
            )
            self._conn.commit()

    def update_power(self, ts: float, power_w: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE samples SET power_w=? WHERE ts=?", (power_w, ts))
            self._conn.commit()

    def series(self, since: float, buckets: int = 300) -> dict[str, list]:
        """Séries agrégées (moyenne par tranche, max pour la latence)."""
        now = time.time()
        width = max(1.0, (now - since) / buckets)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT CAST((ts - ?) / ? AS INTEGER) AS b,
                       MIN(ts) AS ts,
                       AVG(latency_ms) AS latency_ms,
                       MAX(latency_ms) AS latency_max_ms,
                       AVG(drop_rate) AS drop_rate,
                       AVG(dl_bps) AS dl_bps,
                       MAX(dl_bps) AS dl_max_bps,
                       AVG(ul_bps) AS ul_bps,
                       MAX(ul_bps) AS ul_max_bps,
                       AVG(power_w) AS power_w,
                       AVG(obstructed) AS obstructed
                FROM samples WHERE ts >= ? GROUP BY b ORDER BY b
                """,
                (since, width, since),
            ).fetchall()
        keys = rows[0].keys() if rows else []
        return {k: [r[k] for r in rows] for k in keys if k != "b"}

    def window_stats(self, since: float) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) n, AVG(latency_ms) lat_avg, AVG(drop_rate) drop_avg,
                       AVG(obstructed) obstructed_avg, MAX(dl_bps) dl_max, MAX(ul_bps) ul_max,
                       AVG(power_w) power_avg,
                       SUM(CASE WHEN drop_rate >= 1 THEN 1 ELSE 0 END) outage_samples
                FROM samples WHERE ts >= ?
                """,
                (since,),
            ).fetchone()
            lats = [
                r[0]
                for r in self._conn.execute(
                    "SELECT latency_ms FROM samples WHERE ts >= ? AND latency_ms IS NOT NULL ORDER BY latency_ms",
                    (since,),
                )
            ]
        out = dict(row)
        out["lat_p50"] = _pct(lats, 0.5)
        out["lat_p95"] = _pct(lats, 0.95)
        return out

    def hourly_profile(self, since: float) -> list[dict[str, Any]]:
        """Profil par heure locale : débit et latence moyens (pour repérer la congestion)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h,
                       AVG(latency_ms) lat, MAX(dl_bps) dl_max, MAX(ul_bps) ul_max, AVG(drop_rate) drop_rate
                FROM samples WHERE ts >= ? GROUP BY h ORDER BY h
                """,
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, older_than: float) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (older_than,))
            self._conn.commit()

    def add_test(self, kind: str, result: dict[str, Any], ts: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tests(ts, kind, result) VALUES (?,?,?)", (ts or time.time(), kind, json.dumps(result))
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def tests(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, result FROM tests WHERE kind=? ORDER BY ts DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], **json.loads(r["result"])} for r in rows]


def _pct(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]
