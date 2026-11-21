// Serveur de dev local imitant Vercel (Redis émulé en mémoire).
// Usage : DASHBOARD_PASSWORD=x INGEST_TOKEN=y node dev/dev-server.js  → http://localhost:3000
import http from "node:http";
import { readFile } from "node:fs/promises";
import { installFakeRedis } from "../test/fake-redis.js";

installFakeRedis();
const routes = {};
for (const n of ["ingest", "state", "command", "health"]) routes[n] = (await import(`../api/${n}.js`)).default;
const port = Number(process.env.PORT || 3000);

http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  res.status = (c) => ((res.statusCode = c), res);
  res.json = (j) => { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(j)); };
  const m = u.pathname.match(/^\/api\/(\w+)$/);
  if (m && routes[m[1]]) { req.query = Object.fromEntries(u.searchParams); return routes[m[1]](req, res); }
  const file = u.pathname === "/" ? "index.html" : u.pathname.slice(1);
  try {
    const data = await readFile(new URL(`../public/${file.replace(/\.\./g, "")}`, import.meta.url));
    res.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : "text/html; charset=utf-8");
    res.end(data);
  } catch { res.statusCode = 404; res.end("404"); }
}).listen(port, () => console.log(`dev cloud sur http://localhost:${port}`));
