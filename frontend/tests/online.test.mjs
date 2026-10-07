import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { configure, schedulerFetch, pinVersion, withExpectedVersions, scopedStorage } from "../src/online.ts";

const alpha = { id: 2, username: "alpha", is_admin: false, disabled: false, must_change_password: false };
const program = { id: 1, name: "P1", assigned_user_id: 2, version: 1 };
const target = new EventTarget();
const storage = new Map();
let calls;
let responses;
globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
globalThis.window = {
  location: { origin: "https://scheduler.test" },
  dispatchEvent: event => target.dispatchEvent(event),
  prompt: () => "Shared lecture",
  fetch: async (url, init) => {
    calls.push({ url: String(url), init });
    const value = responses.shift();
    if (value instanceof Error) throw value;
    return new Response(JSON.stringify(value?.body ?? value ?? {}), { status: value?.status ?? 200, headers: { "Content-Type": "application/json" } });
  },
};
beforeEach(() => { calls = []; responses = []; configure(null, "", null); withExpectedVersions(null); configure(alpha, "csrf-test", program); });

test("same-origin transport adds scope and CSRF", async () => {
  responses.push([]);
  await schedulerFetch("https://scheduler.test/api/schedule");
  assert.equal(new URL(calls[0].url).searchParams.get("program_id"), "1");
  assert.equal(calls[0].init.headers.get("X-CSRF-Token"), "csrf-test");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("live refresh cannot replace an open form's expected version", async () => {
  responses.push([{ id: 7, version: 1 }]);
  await schedulerFetch("https://scheduler.test/api/schedule");
  pinVersion("schedule", 7, 1);
  responses.push([{ id: 7, version: 2 }]);
  await schedulerFetch("https://scheduler.test/api/schedule");
  responses.push({ id: 7, version: 2 });
  await schedulerFetch("https://scheduler.test/api/schedule/7", { method: "PUT", body: JSON.stringify({ "Course Description": "My draft" }) });
  assert.equal(JSON.parse(calls.at(-1).init.body).version, 1);
});

test("undo uses its original expected version after a live refresh", async () => {
  responses.push([{ id: 7, version: 3 }]);
  await schedulerFetch("https://scheduler.test/api/schedule");
  withExpectedVersions({ "schedule/7": 2 });
  responses.push({ ok: true });
  await schedulerFetch("https://scheduler.test/api/schedule/7", { method: "DELETE" });
  assert.equal(new URL(calls.at(-1).url).searchParams.get("version"), "2");
});

test("batch undo carries its saved versions", async () => {
  withExpectedVersions({ "schedule/7": 2, "schedule/8": 1 });
  responses.push([]);
  await schedulerFetch("https://scheduler.test/api/schedule/batch", { method: "POST", body: JSON.stringify({ operations: [
    { method: "DELETE", id: 8 }, { method: "PUT", id: 7, entry: { version: 1 } },
  ] }) });
  const operations = JSON.parse(calls[0].init.body).operations;
  assert.equal(operations[0].version, 1);
  assert.equal(operations[1].entry.version, 2);
});

test("read-only program mutations fail before sending a request", async () => {
  configure(alpha, "csrf-test", { ...program, assigned_user_id: 3 });
  await assert.rejects(schedulerFetch("https://scheduler.test/api/schedule", { method: "POST", body: "{}" }), /read-only/);
  assert.equal(calls.length, 0);
});

test("permission and conflict failures reject instead of reporting success", async () => {
  responses.push({ status: 409, body: { detail: { code: "stale_version", message: "Review your draft" } } });
  await assert.rejects(schedulerFetch("https://scheduler.test/api/schedule/7", { method: "PUT", body: "{}" }), /Review your draft/);
});

test("network failures notify the editor to release its saving state", async () => {
  let message;
  target.addEventListener("scheduler-error", event => { message = event.detail; }, { once: true });
  responses.push(new Error("network unavailable"));
  await assert.rejects(schedulerFetch("https://scheduler.test/api/schedule/7", { method: "PUT", body: "{}" }), /form is preserved/);
  assert.match(message, /Connection interrupted/);
});

test("local preferences are isolated by account and program", () => {
  scopedStorage.setItem("curriculum", "P1 data");
  configure(alpha, "csrf-test", { ...program, id: 2 });
  assert.equal(scopedStorage.getItem("curriculum"), null);
  configure({ ...alpha, id: 3 }, "csrf-test", program);
  assert.equal(scopedStorage.getItem("curriculum"), null);
});
