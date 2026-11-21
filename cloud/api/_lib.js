// Fonctions partagées (le préfixe "_" empêche Vercel d'en faire une route).
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";

export const ACTIONS = ["reboot", "stow", "unstow", "bufferbloat"];
export const HIST_MAX = 4320; // ~24 h à un push toutes les 20 s

// Upstash Redis (intégration Vercel Marketplace) : l'une ou l'autre paire de variables.
function redisConfig() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) throw new HttpError(500, "Stockage Redis non configuré (KV_REST_API_URL / KV_REST_API_TOKEN)");
  return { url: url.replace(/\/$/, ""), token };
}

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function redisFetch(path, body) {
  const { url, token } = redisConfig();
  const r = await fetch(url + path, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new HttpError(502, `Redis ${r.status}`);
  return r.json();
}

/** Exécute plusieurs commandes Redis ; atomic=true → transaction MULTI/EXEC. */
export async function redis(commands, { atomic = false } = {}) {
  const data = await redisFetch(atomic ? "/multi-exec" : "/pipeline", commands);
  return data.map((x) => {
    if (x.error) throw new HttpError(502, `Redis: ${x.error}`);
    return x.result;
  });
}

// Comparaison en temps constant (via empreintes de même longueur).
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) return false;
  const ha = createHash("sha256").update(a).digest();
  const hb = createHash("sha256").update(b).digest();
  return timingSafeEqual(ha, hb);
}

export function bearer(req) {
  const h = req.headers.authorization || req.headers.Authorization || "";
  return h.startsWith("Bearer ") ? h.slice(7).trim() : "";
}

function clientIp(req) {
  return String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "inconnu";
}

/** Authentifie l'utilisateur du tableau de bord, avec limitation des essais ratés par IP. */
export async function requireDashboard(req) {
  const expected = process.env.DASHBOARD_PASSWORD;
  if (!expected) throw new HttpError(500, "DASHBOARD_PASSWORD non défini");
  const key = `fail:${clientIp(req)}`;
  const [fails] = await redis([["GET", key]]);
  if (Number(fails || 0) >= 10) throw new HttpError(429, "Trop d'essais, réessayez dans 15 minutes");
  if (!safeEqual(bearer(req), expected)) {
    await redis([["INCR", key], ["EXPIRE", key, 900]]);
    throw new HttpError(401, "Mot de passe incorrect");
  }
}

export function requireAgent(req) {
  const expected = process.env.INGEST_TOKEN;
  if (!expected) throw new HttpError(500, "INGEST_TOKEN non défini");
  if (!safeEqual(bearer(req), expected)) throw new HttpError(401, "Jeton agent invalide");
}

export async function readBody(req) {
  if (req.body !== undefined) return typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body;
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

export function handler(methods, fn) {
  return async (req, res) => {
    try {
      if (!methods.includes(req.method)) throw new HttpError(405, "Méthode non autorisée");
      const out = await fn(req, res);
      res.status(200).json(out ?? { ok: true });
    } catch (e) {
      const status = e instanceof HttpError ? e.status : 500;
      if (status === 500) console.error(e);
      res.status(status).json({ error: e.message || "Erreur" });
    }
  };
}

export const newId = () => randomUUID();
