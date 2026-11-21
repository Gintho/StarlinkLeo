// POST /api/ingest — appelé par l'agent local (jeton INGEST_TOKEN).
// Enregistre l'instantané, et renvoie (puis efface) les commandes en attente.
import { handler, readBody, redis, requireAgent, HIST_MAX } from "./_lib.js";

export default handler(["POST"], async (req) => {
  requireAgent(req);
  const snap = await readBody(req);
  const now = Date.now() / 1000;
  const s = snap.status || {};
  const point = {
    ts: snap.ts || now,
    dl: s.downlink_throughput_bps ?? null,
    ul: s.uplink_throughput_bps ?? null,
    lat: s.pop_ping_latency_ms ?? null,
    drop: s.pop_ping_drop_rate ?? null,
    obs: s.currently_obstructed ? 1 : 0,
    up: snap.status ? 1 : 0,
  };
  const results = Array.isArray(snap.command_results) ? snap.command_results.slice(0, 20) : [];
  delete snap.command_results;

  const cmds = [
    ["SET", "state", JSON.stringify(snap)],
    ["SET", "agent_seen", String(now)],
    ["LPUSH", "hist", JSON.stringify(point)],
    ["LTRIM", "hist", 0, HIST_MAX - 1],
  ];
  for (const r of results) cmds.push(["LPUSH", "results", JSON.stringify(r)]);
  if (results.length) cmds.push(["LTRIM", "results", 0, 49]);
  await redis(cmds);

  // Récupère et vide la file de commandes de façon atomique.
  const [pending] = await redis([["LRANGE", "cmds", 0, -1], ["DEL", "cmds"]], { atomic: true });
  return { ok: true, commands: (pending || []).map((c) => JSON.parse(c)) };
});
