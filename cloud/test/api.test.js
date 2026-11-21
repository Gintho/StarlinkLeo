import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeRedis } from "./fake-redis.js";

installFakeRedis();
process.env.DASHBOARD_PASSWORD = "secret-pw";
process.env.INGEST_TOKEN = "agent-token";
const ingest = (await import("../api/ingest.js")).default;
const state = (await import("../api/state.js")).default;
const command = (await import("../api/command.js")).default;

function call(h, { method = "GET", token, body, ip = "1.2.3.4", query = {} } = {}) {
  return new Promise((resolve) => {
    const req = { method, body, query, headers: { authorization: token ? "Bearer " + token : "", "x-forwarded-for": ip } };
    const res = { code: 200, status(c) { this.code = c; return this; }, json(j) { resolve({ code: this.code, body: j }); } };
    h(req, res);
  });
}

test("ingest refuse un mauvais jeton", async () => {
  const r = await call(ingest, { method: "POST", token: "nope", body: {} });
  assert.equal(r.code, 401);
});

test("cycle complet : ingest -> state -> command -> ingest renvoie la commande", async () => {
  const snap = { ts: Date.now() / 1000, status: { state: "CONNECTED", downlink_throughput_bps: 1e8 }, findings: [] };
  assert.equal((await call(ingest, { method: "POST", token: "agent-token", body: snap })).code, 200);
  const s = await call(state, { token: "secret-pw" });
  assert.equal(s.code, 200);
  assert.equal(s.body.state.status.state, "CONNECTED");
  assert.equal(s.body.hist.length, 1);

  assert.equal((await call(command, { method: "POST", token: "secret-pw", body: { action: "explode" } })).code, 400);
  const c = await call(command, { method: "POST", token: "secret-pw", body: { action: "reboot" } });
  assert.equal(c.code, 200);
  assert.equal((await call(state, { token: "secret-pw" })).body.pending.length, 1);

  const i2 = await call(ingest, { method: "POST", token: "agent-token", body: { ...snap, command_results: [] } });
  assert.deepEqual(i2.body.commands.map((x) => x.action), ["reboot"]);
  const i3 = await call(ingest, { method: "POST", token: "agent-token", body: { ...snap, command_results: [{ id: c.body.command.id, action: "reboot", ok: true, ts: 1 }] } });
  assert.equal(i3.body.commands.length, 0);
  const s3 = await call(state, { token: "secret-pw" });
  assert.equal(s3.body.pending.length, 0);
  assert.equal(s3.body.results[0].ok, true);
});

test("mot de passe faux puis blocage après 10 essais", async () => {
  for (let i = 0; i < 10; i++) assert.equal((await call(state, { token: "bad", ip: "9.9.9.9" })).code, 401);
  assert.equal((await call(state, { token: "secret-pw", ip: "9.9.9.9" })).code, 429);
  assert.equal((await call(state, { token: "secret-pw", ip: "8.8.8.8" })).code, 200);
});

test("méthode non autorisée", async () => {
  assert.equal((await call(command, { method: "GET", token: "secret-pw" })).code, 405);
});
