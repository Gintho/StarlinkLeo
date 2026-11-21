// Émulateur minimal de l'API REST Upstash pour les tests.
export function installFakeRedis() {
  const db = new Map();
  const list = (k) => (db.has(k) ? db.get(k) : (db.set(k, []), db.get(k)));
  const idx = (a, i) => (i < 0 ? a.length + i : i);
  const run = ([cmd, ...a]) => {
    switch (cmd) {
      case "GET": return db.has(a[0]) ? db.get(a[0]) : null;
      case "SET": db.set(a[0], a[1]); return "OK";
      case "DEL": return db.delete(a[0]) ? 1 : 0;
      case "INCR": { const v = Number(db.get(a[0]) || 0) + 1; db.set(a[0], String(v)); return v; }
      case "EXPIRE": return 1;
      case "LPUSH": { const l = list(a[0]); l.unshift(a[1]); return l.length; }
      case "RPUSH": { const l = list(a[0]); l.push(a[1]); return l.length; }
      case "LRANGE": { const l = db.get(a[0]) || []; return l.slice(idx(l, a[1]), idx(l, a[2]) + 1); }
      case "LTRIM": { const l = db.get(a[0]) || []; db.set(a[0], l.slice(idx(l, a[1]), idx(l, a[2]) + 1)); return "OK"; }
      default: throw new Error("cmd " + cmd);
    }
  };
  process.env.KV_REST_API_URL = "https://fake-redis.test";
  process.env.KV_REST_API_TOKEN = "t";
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (!String(url).startsWith("https://fake-redis.test")) return realFetch(url, opts);
    const body = JSON.parse(opts.body);
    return { ok: true, status: 200, json: async () => body.map((c) => ({ result: run(c) })) };
  };
  return db;
}
