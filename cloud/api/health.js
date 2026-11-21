// GET /api/health — vérifie la configuration (sans rien divulguer).
import { handler } from "./_lib.js";

export default handler(["GET"], async () => ({
  ok: true,
  redis: Boolean(process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL),
  password: Boolean(process.env.DASHBOARD_PASSWORD),
  ingest_token: Boolean(process.env.INGEST_TOKEN),
}));
