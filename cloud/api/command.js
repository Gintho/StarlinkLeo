// POST /api/command {action} — met une commande en file pour l'agent local (mot de passe).
import { ACTIONS, HttpError, handler, newId, readBody, redis, requireDashboard } from "./_lib.js";

export default handler(["POST"], async (req) => {
  await requireDashboard(req);
  const { action } = await readBody(req);
  if (!ACTIONS.includes(action)) throw new HttpError(400, `Action invalide (attendu : ${ACTIONS.join(", ")})`);
  const cmd = { id: newId(), action, ts: Date.now() / 1000 };
  const [len] = await redis([["RPUSH", "cmds", JSON.stringify(cmd)]]);
  if (len > 10) await redis([["LTRIM", "cmds", -10, -1]]);
  return { ok: true, command: cmd };
});
