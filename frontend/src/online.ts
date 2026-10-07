export type OnlineUser = { id: number; username: string; is_admin: boolean; disabled: boolean; must_change_password: boolean };
export type Program = { id: number; name: string; assigned_user_id: number | null; version: number };
export type Activity = { id: number; actor_id: number; actor: string; program_id: number | null; action: string; entity_type: string; entity_id: number; before: Record<string, unknown> | null; after: Record<string, unknown> | null; changed_fields: string[]; reason: string | null; created_at: string };

let csrf = "";
let activeProgram: Program | null = null;
let account: OnlineUser | null = null;
let settingsVersion: number | null = null;
let settingsSnapshot = "";
const versions = new Map<string, number>();
const pinnedVersions = new Map<string, number>();
let expectedVersions: Record<string, number> | null = null;
let overrideReason: string | null = null;

export function configure(user: OnlineUser | null, token: string, program: Program | null) {
  csrf = token;
  account = user;
  if (activeProgram?.id !== program?.id || !user) {
    versions.clear(); pinnedVersions.clear(); settingsVersion = null; settingsSnapshot = "";
    overrideReason = null;
  }
  activeProgram = program;
}

export function pinVersion(kind: string, id: number, version?: number) {
  const key = `${kind}/${id}`;
  const value = version ?? versions.get(key);
  if (value !== undefined) pinnedVersions.set(key, value);
}

export function snapshotVersions() { return Object.fromEntries(versions); }
export function withExpectedVersions(value: Record<string, number> | null) { expectedVersions = value; }
export function canEdit() { return !!activeProgram && !!account && (account.is_admin || activeProgram.assigned_user_id === account.id); }
export function programName() { return activeProgram?.name ?? ""; }

export const scopedStorage = {
  getItem(key: string) { return localStorage.getItem(`online:${account?.id}:${activeProgram?.id ?? "all"}:${key}`); },
  setItem(key: string, value: string) { localStorage.setItem(`online:${account?.id}:${activeProgram?.id ?? "all"}:${key}`, value); },
  removeItem(key: string) { localStorage.removeItem(`online:${account?.id}:${activeProgram?.id ?? "all"}:${key}`); },
};

export function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const data = detail as { message?: string; missing_columns?: string[]; errors?: { row_index: number; reason: string }[] };
    if (data.message) return data.message;
    if (data.missing_columns?.length) return `Missing timetable columns: ${data.missing_columns.join(", ")}. For curriculum files, use File > Load Curricula.`;
    if (data.errors?.length) return data.errors.map(e => `Row ${e.row_index}: ${e.reason}`).join("; ");
  }
  return "The request could not be completed. Review your changes and try again.";
}

export async function request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrf);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await window.fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) window.dispatchEvent(new Event("scheduler-session-expired"));
    throw new Error(errorMessage(body.detail));
  }
  return response.json() as Promise<T>;
}

/** Shared transport for the existing scheduler. Every failure rejects, so a
 * legacy handler cannot silently close a form after a rejected save. */
export async function schedulerFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const url = new URL(String(input), window.location.origin);
  const path = url.pathname.replace(/^\/api/, "");
  const method = (init.method ?? "GET").toUpperCase();
  const mutating = !["GET", "HEAD"].includes(method);
  const settings = path === "/settings";
  const scoped = path === "/schedule" || path === "/sections" || settings || path.startsWith("/file/") || path.startsWith("/reports/");
  if (activeProgram && scoped) url.searchParams.set("program_id", String(activeProgram.id));
  let payload = typeof init.body === "string" ? JSON.parse(init.body) : null;
  if (mutating && !settings && path !== "/export/png" && !canEdit()) {
    return failure("This program is read-only. Choose a program assigned to you.");
  }
  if (payload && /^\/(sections|faculty|rooms)$/.test(path) && activeProgram) payload.program_id = activeProgram.id;
  if (overrideReason && method !== "GET" && path.startsWith("/schedule") && !path.endsWith("/move-check")) url.searchParams.set("override_reason", overrideReason);
  if (path === "/schedule/batch" && payload) {
    payload.operations = payload.operations.map((op: { method: string; id?: number; entry?: Record<string, unknown>; version?: number }) => {
      const key = `schedule/${op.id}`;
      const version = expectedVersions?.[key] ?? op.entry?.version ?? versions.get(key);
      return op.method === "PUT" ? { ...op, entry: { ...op.entry, version } } : op.method === "DELETE" ? { ...op, version: op.version ?? version } : op;
    });
  }
  const match = path.match(/^\/(schedule|sections|faculty|rooms)\/(\d+)(?:\/remove)?$/);
  if (match && mutating) {
    const key = `${match[1]}/${match[2]}`;
    const version = expectedVersions?.[key] ?? pinnedVersions.get(key) ?? payload?.version ?? versions.get(key);
    if (method === "PUT") payload = { ...payload, version };
    else url.searchParams.set("version", String(version ?? 0));
  }
  if (settings && payload) {
    if (!canEdit()) delete payload.settings.curriculumState;
    // Personal customization must not rewrite an unchanged shared curriculum.
    if (JSON.stringify(payload.settings?.curriculumState) === settingsSnapshot) delete payload.settings.curriculumState;
    payload.version = settingsVersion;
  }
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrf);
  const options = { ...init, headers, body: payload ? JSON.stringify(payload) : init.body, credentials: "same-origin" as const };
  const send = async () => {
    try { return await window.fetch(url, options); }
    catch { return failure("Connection interrupted. Your form is preserved; refresh the schedule and review it before retrying."); }
  };
  let response = await send();
  if (!response.ok && account?.is_admin && path.startsWith("/schedule") && method !== "GET") {
    const body = await response.clone().json().catch(() => ({}));
    if (body.detail?.code === "scheduling_conflict" && !body.detail.conflicts.some((c: { conflict_type: string }) => c.conflict_type === "section")) {
      const reason = window.prompt("Administrator override: enter the reason for allowing this room/faculty conflict.");
      if (reason?.trim()) {
        url.searchParams.set("override_reason", reason.trim());
        response = await send();
      }
    }
  }
  if (!response.ok) {
    const body = await response.clone().json().catch(() => ({}));
    if (response.status === 401) window.dispatchEvent(new Event("scheduler-session-expired"));
    return failure(errorMessage(body.detail));
  }
  if (response.headers.get("content-type")?.includes("application/json")) {
    const data = await response.clone().json();
    if (path.endsWith("/move-check") && data.ok === false && account?.is_admin && !data.conflicts.some((c: { conflict_type: string }) => c.conflict_type === "section")) {
      const reason = window.prompt("Administrator override: enter the reason for allowing this room/faculty conflict.");
      if (reason?.trim()) {
        overrideReason = reason.trim();
        return new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } });
      }
    }
    if (settings) {
      settingsVersion = data.version;
      settingsSnapshot = JSON.stringify(data.settings?.curriculumState);
    }
    const kind = path.split("/")[1];
    for (const item of Array.isArray(data) ? data : [data]) {
      if (item.id && item.version) {
        const key = `${kind}/${item.id}`;
        versions.set(key, item.version);
        if (mutating && pinnedVersions.has(key)) pinnedVersions.set(key, item.version);
      }
    }
    if (mutating && path.startsWith("/schedule") && !path.endsWith("/move-check")) overrideReason = null;
  }
  return response;
}

function failure(message: string): never {
  window.dispatchEvent(new CustomEvent("scheduler-error", { detail: message }));
  throw new Error(message);
}
