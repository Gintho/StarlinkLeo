// GET /api/state — lecture de l'état pour le tableau de bord (mot de passe).
import { handler, redis, requireDashboard } from "./_lib.js";

export default handler(["GET"], async (req) => {
  await requireDashboard(req);
  const points = Math.min(Number(req.query?.points) || 1440, 4320);
  const [state, seen, hist, results, pending] = await redis([
    ["GET", "state"],
    ["GET", "agent_seen"],
    ["LRANGE", "hist", 0, points - 1],
    ["LRANGE", "results", 0, 19],
    ["LRANGE", "cmds", 0, -1],
  ]);
  return {
    now: Date.now() / 1000,
    agent_seen: seen ? Number(seen) : null,
    state: state ? JSON.parse(state) : null,
    hist: (hist || []).map((x) => JSON.parse(x)).reverse(),
    results: (results || []).map((x) => JSON.parse(x)),
    pending: (pending || []).map((x) => JSON.parse(x)),
  };
});
