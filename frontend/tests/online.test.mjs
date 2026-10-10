import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { configure, schedulerFetch, readSchedule, pinVersion, withExpectedVersions, scopedStorage, errorMessage, request, downloadDatabaseBackup } from "../src/online.ts";

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

test("curriculum CSV sent to timetable import receives a useful format error", () => {
  assert.match(errorMessage({ missing_columns: ["Program", "Section", "Days"], errors: [] }), /Load Curricula/);
  assert.notEqual(errorMessage({ errors: [] }), "");
});

test("same-origin transport adds scope and CSRF", async () => {
  responses.push([]);
  await schedulerFetch("https://scheduler.test/api/schedule");
  assert.equal(new URL(calls[0].url).searchParams.get("program_id"), "1");
  assert.equal(calls[0].init.headers.get("X-CSRF-Token"), "csrf-test");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("room timetables always read all programs while other views retain the selected program", async () => {
  for (const selected of [program, { ...program, id: 2 }, null]) {
    configure(alpha, "csrf-test", selected);
    responses.push([{ id: 7, program_id: 1 }, { id: 8, program_id: 2 }]);
    const response = await schedulerFetch("https://scheduler.test/api/schedule?room=Lab%201&program_id=99");
    const url = new URL(calls.at(-1).url);
    assert.equal(url.searchParams.has("program_id"), false);
    assert.equal(url.searchParams.get("room"), "Lab 1");
    assert.equal((await response.json()).length, 2);
  }
  configure(alpha, "csrf-test", program);
  for (const query of ["", "?section=1A", "?faculty=Teacher"]) {
    responses.push([]);
    await schedulerFetch(`https://scheduler.test/api/schedule${query}`);
    assert.equal(new URL(calls.at(-1).url).searchParams.get("program_id"), "1");
  }
});

test("room CSV exports include all programs without changing program CSV exports", async () => {
  responses.push({});
  await schedulerFetch("https://scheduler.test/api/reports/timetable/room.csv?filter_value=Lab%201&program_id=1");
  const roomUrl = new URL(calls.at(-1).url);
  assert.equal(roomUrl.searchParams.has("program_id"), false);
  assert.equal(roomUrl.searchParams.get("filter_value"), "Lab 1");
  responses.push({});
  await schedulerFetch("https://scheduler.test/api/reports/text.csv");
  assert.equal(new URL(calls.at(-1).url).searchParams.get("program_id"), "1");
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


test("signed-in users can add faculty while viewing another program", async () => {
  configure(alpha, "csrf-test", { ...program, assigned_user_id: 3 });
  responses.push({ id: 9, name: "New teacher", version: 1 });
  await schedulerFetch("https://scheduler.test/api/faculty", { method: "POST", body: JSON.stringify({ name: "New teacher" }) });
  assert.equal(calls.length, 1);
  await assert.rejects(schedulerFetch("https://scheduler.test/api/rooms", { method: "POST", body: JSON.stringify({ name: "New room" }) }));
  assert.equal(calls.length, 1);
});


test("administrators can add global rooms with all programs selected", async () => {
  configure({ ...alpha, is_admin: true }, "csrf-test", null);
  responses.push({ id: 10, name: "New room", version: 1 });
  await schedulerFetch("https://scheduler.test/api/rooms", { method: "POST", body: JSON.stringify({ name: "New room" }) });
  assert.equal(calls.length, 1);
});


test("database backup download uses authenticated same-origin POST and CSRF", async () => {
  configure({ ...alpha, is_admin: true }, "csrf-test", program);
  responses.push({ archived: true });
  const blob = await downloadDatabaseBackup();
  assert.ok(blob.size > 0);
  assert.equal(calls[0].url, "/api/admin/database/backup");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["X-CSRF-Token"], "csrf-test");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("database upload preserves raw file content and private confirmation headers", async () => {
  const file = new Blob(["backup-content"]);
  responses.push({ ok: true });
  await request("/admin/database/restore", { method: "POST", body: file, headers: {
    "Content-Type": "application/octet-stream", "X-Admin-Password": "typed-password", "X-Restore-Confirmation": "REPLACE DATABASE",
  } });
  assert.equal(calls[0].init.body, file);
  assert.equal(calls[0].init.headers.get("Content-Type"), "application/octet-stream");
  assert.equal(calls[0].init.headers.get("X-CSRF-Token"), "csrf-test");
  assert.equal(calls[0].init.headers.get("X-Admin-Password"), "typed-password");
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.ok(!calls[0].url.includes("typed-password"));
});


test("atomic move responses refresh versions for every affected class", async () => {
  responses.push({ entries: [{ id: 1, version: 2 }, { id: 2, version: 1 }], moved_entry_id: 2, snapshot: { move_activity_id: 5 } });
  await schedulerFetch("https://scheduler.test/api/schedule/1/move", { method: "POST", body: JSON.stringify({ expected: { id: 1, version: 1 } }) });
  responses.push({});
  await schedulerFetch("https://scheduler.test/api/schedule/2", { method: "PUT", body: JSON.stringify({}) });
  assert.equal(JSON.parse(calls[1].init.body).version, 1);
  responses.push({ entries: [{ id: 1, version: 3 }], removed_ids: [2] });
  await schedulerFetch("https://scheduler.test/api/schedule/1/move/revert", { method: "POST", body: JSON.stringify({ move_activity_id: 5 }) });
  responses.push({});
  await schedulerFetch("https://scheduler.test/api/schedule/1", { method: "PUT", body: JSON.stringify({}) });
  assert.equal(JSON.parse(calls[3].init.body).version, 3);
});


test("one schedule collection filters the selected program while preserving shared rooms", async () => {
  responses.push([{ id: 1, program_id: 1, version: 3 }, { id: 2, program_id: 2, version: 4 }]);
  const result = await readSchedule();
  assert.deepEqual(result.entries.map(entry => entry.id), [1]);
  assert.deepEqual(result.sharedEntries.map(entry => entry.id), [1, 2]);
  assert.equal(calls[0].url, '/api/schedule');
  assert.equal(calls[0].init.headers.get('X-CSRF-Token'), 'csrf-test');
  responses.push({});
  await schedulerFetch('https://scheduler.test/api/schedule/1', { method: 'PUT', body: JSON.stringify({}) });
  assert.equal(JSON.parse(calls[1].init.body).version, 3);
});
