"""Test de bufferbloat : latence au repos vs latence sous charge (download / upload).

La latence est mesurée par le temps d'établissement d'une connexion TCP (pas besoin
de droits root pour l'ICMP). La charge est générée avec plusieurs flux parallèles
vers le serveur de test de débit Cloudflare.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

# Barème inspiré du test Waveform : augmentation de latence sous charge (ms)
GRADES = [(5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")]


def grade(increase_ms: float | None) -> str:
    if increase_ms is None:
        return "?"
    for limit, g in GRADES:
        if increase_ms < limit:
            return g
    return "F"


@dataclass
class Phase:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    bytes: int = 0
    duration_s: float = 0.0
    error: str | None = None

    @property
    def mbps(self) -> float | None:
        if not self.duration_s or not self.bytes:
            return None
        return self.bytes * 8 / self.duration_s / 1e6

    def summary(self) -> dict[str, Any]:
        lats = sorted(self.latencies_ms)
        return {
            "name": self.name,
            "samples": len(lats),
            "median_ms": statistics.median(lats) if lats else None,
            "p90_ms": lats[int(0.9 * (len(lats) - 1))] if lats else None,
            "max_ms": lats[-1] if lats else None,
            "mbps": self.mbps,
            "error": self.error,
        }


async def tcp_rtt(host: str, port: int, timeout: float = 2.0) -> float | None:
    start = time.perf_counter()
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    rtt = (time.perf_counter() - start) * 1000
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return rtt


async def _measure_latency(phase: Phase, host: str, port: int, stop: asyncio.Event, interval: float = 0.25):
    while not stop.is_set():
        rtt = await tcp_rtt(host, port)
        if rtt is not None:
            phase.latencies_ms.append(rtt)
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:
            pass


async def _download_worker(client, url: str, phase: Phase, stop: asyncio.Event):
    while not stop.is_set():
        try:
            async with client.stream("GET", url) as resp:
                async for chunk in resp.aiter_bytes(65536):
                    phase.bytes += len(chunk)
                    if stop.is_set():
                        return
        except Exception as e:  # noqa: BLE001
            phase.error = f"{type(e).__name__}: {e}"[:200]
            await asyncio.sleep(0.2)


async def _upload_worker(client, url: str, phase: Phase, stop: asyncio.Event):
    block = os.urandom(65536)

    async def body():
        while not stop.is_set():
            phase.bytes += len(block)
            yield block

    while not stop.is_set():
        try:
            await client.post(url, content=body(), headers={"Content-Type": "application/octet-stream"})
        except Exception as e:  # noqa: BLE001
            phase.error = f"{type(e).__name__}: {e}"[:200]
            await asyncio.sleep(0.2)


async def _run_phase(name: str, duration: float, host: str, port: int, load=None) -> Phase:
    phase = Phase(name)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(_measure_latency(phase, host, port, stop))]
    if load:
        tasks += load(phase, stop)
    start = time.perf_counter()
    await asyncio.sleep(duration)
    stop.set()
    phase.duration_s = time.perf_counter() - start
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return phase


async def run_test(
    target: str,
    speedtest_url: str,
    idle_s: float = 5,
    load_s: float = 12,
    streams: int = 6,
    progress: Callable[[str], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    import httpx

    host, _, port_s = target.rpartition(":")
    port = int(port_s or 443)
    base = speedtest_url.rstrip("/")

    async def notify(msg: str):
        if progress:
            r = progress(msg)
            if asyncio.iscoroutine(r):
                await r

    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, http2=False) as client:
        await notify("idle")
        idle = await _run_phase("idle", idle_s, host, port)
        await notify("download")
        down = await _run_phase(
            "download",
            load_s,
            host,
            port,
            lambda ph, st: [
                asyncio.create_task(_download_worker(client, f"{base}/__down?bytes=250000000", ph, st))
                for _ in range(streams)
            ],
        )
        await notify("upload")
        up = await _run_phase(
            "upload",
            load_s,
            host,
            port,
            lambda ph, st: [
                asyncio.create_task(_upload_worker(client, f"{base}/__up", ph, st)) for _ in range(max(2, streams // 2))
            ],
        )
    return summarize(idle, down, up)


def summarize(idle: Phase, down: Phase, up: Phase) -> dict[str, Any]:
    i, d, u = idle.summary(), down.summary(), up.summary()
    base = i["median_ms"]

    def inc(p):
        return None if base is None or p["median_ms"] is None else max(0.0, p["median_ms"] - base)

    # Un test sans charge effective (serveur injoignable, proxy...) ne mesure rien :
    # on le marque invalide plutôt que d'afficher une fausse bonne note.
    problems = []
    if base is None:
        problems.append("aucune mesure de latence au repos")
    for p in (d, u):
        if not p["mbps"] or p["mbps"] < 1:
            problems.append(f"pas de charge en {p['name']}" + (f" ({p['error']})" if p.get("error") else ""))
    valid = not problems
    d_inc, u_inc = inc(d), inc(u)
    return {
        "valid": valid,
        "error": "; ".join(problems) or None,
        "idle": i,
        "download": {**d, "increase_ms": d_inc, "grade": grade(d_inc) if valid else "?"},
        "upload": {**u, "increase_ms": u_inc, "grade": grade(u_inc) if valid else "?"},
        "grade": grade(max([x for x in (d_inc, u_inc) if x is not None], default=None)) if valid else "?",
    }


def simulated_result(shaped: bool = False) -> dict[str, Any]:
    """Résultat synthétique pour le mode mock."""
    import random

    rng = random.Random()
    idle = Phase("idle", [rng.gauss(32, 4) for _ in range(20)], 0, 5)
    extra_d, extra_u = (3, 6) if shaped else (140, 260)
    down = Phase("download", [rng.gauss(32 + extra_d, 15) for _ in range(48)], int(165e6 / 8 * 12), 12)
    up = Phase("upload", [rng.gauss(32 + extra_u, 30) for _ in range(48)], int(18e6 / 8 * 12), 12)
    return summarize(idle, down, up)


__all__ = ["run_test", "summarize", "grade", "simulated_result", "Phase", "asdict"]
