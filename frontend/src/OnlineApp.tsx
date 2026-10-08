import { FormEvent, useEffect, useState } from "react";
import App from "./App";
import { Activity, configure, downloadDatabaseBackup, OnlineUser, Program, request } from "./online";
import "./online.css";

type Session = { user: OnlineUser; csrf_token: string };
const fieldLabels: Record<string, string> = { name: "Name", username: "Username", assigned_user_id: "Assigned account", disabled: "Disabled", is_admin: "Administrator", must_change_password: "Password change required", curriculumState: "Curriculum", ignoreRoom: "Ignore room conflicts", ignoreFaculty: "Ignore faculty conflicts", ignoreRoomIds: "Room conflict exceptions", ignoreFacultyIds: "Faculty conflict exceptions" };
const visibleFields = (event: Activity) => event.changed_fields.filter(field => field in fieldLabels || /^[A-Z#]/.test(field));

export default function OnlineApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [programs, setPrograms] = useState<Program[]>([]);
  const [programId, setProgramId] = useState<number | null>(null);
  const [online, setOnline] = useState<{ id: number; username: string }[]>([]);
  const [actors, setActors] = useState<{ id: number; username: string }[]>([]);
  const [feed, setFeed] = useState<Activity[]>([]);
  const [search, setSearch] = useState("");
  const [feedProgram, setFeedProgram] = useState("");
  const [feedActor, setFeedActor] = useState("");
  const [security, setSecurity] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [activityVisible, setActivityVisible] = useState(true);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const active = programs.find(p => p.id === programId) ?? null;
  configure(session?.user ?? null, session?.csrf_token ?? "", active);

  useEffect(() => {
    if (!session) return;
    try {
      setActivityVisible(window.localStorage.getItem(`scheduler:activity-visible:${session.user.id}`) !== "false");
    } catch { setActivityVisible(true); }
  }, [session?.user.id]);
  const toggleActivity = () => {
    const next = !activityVisible;
    setActivityVisible(next);
    if (session) {
      try { window.localStorage.setItem(`scheduler:activity-visible:${session.user.id}`, String(next)); }
      catch { /* The toggle still works if browser storage is unavailable. */ }
    }
  };

  const loadPrograms = async () => {
    const next = await request<Program[]>("/programs");
    setPrograms(next);
    return next;
  };
  const loadFeed = async (older = false) => {
    const query = new URLSearchParams({ q: search, security: String(security) });
    if (feedProgram) query.set("program_id", feedProgram);
    if (feedActor) query.set("actor_id", feedActor);
    if (older && feed.length) query.set("before", String(feed[feed.length - 1].id));
    const [events, people] = await Promise.all([request<Activity[]>(`/activity?${query}`), request<{ id: number; username: string }[]>("/activity/actors")]);
    setFeed(current => older ? [...current, ...events] : events);
    setActors(people);
  };
  const signOut = () => { setSession(null); setPrograms([]); setProgramId(null); setFeed([]); configure(null, "", null); };
  useEffect(() => {
    request<Session>("/auth/me").then(setSession).catch(() => {}).finally(() => setLoading(false));
    const openAdmin = () => setAdminOpen(true);
    window.addEventListener("scheduler-open-admin", openAdmin);
    window.addEventListener("scheduler-session-expired", signOut);
    const reject = (event: PromiseRejectionEvent) => {
      setError(event.reason instanceof Error ? event.reason.message : "Request failed");
      event.preventDefault();
    };
    window.addEventListener("unhandledrejection", reject);
    return () => { window.removeEventListener("scheduler-open-admin", openAdmin); window.removeEventListener("scheduler-session-expired", signOut); window.removeEventListener("unhandledrejection", reject); };
  }, []);
  useEffect(() => {
    if (!session || session.user.must_change_password) return;
    loadPrograms().then(next => setProgramId(next.find(p => p.assigned_user_id === session.user.id)?.id ?? next[0]?.id ?? null)).catch(e => setError(e.message));
  }, [session]);
  useEffect(() => {
    if (!session || session.user.must_change_password) return;
    const timer = window.setTimeout(() => { loadFeed().catch(e => setError(e.message)); }, 150);
    return () => clearTimeout(timer);
  }, [session, search, feedProgram, feedActor, security]);
  useEffect(() => {
    if (!session || session.user.must_change_password) return;
    let disposed = false;
    const beat = async () => {
      try {
        await request("/presence/heartbeat", { method: "POST" });
        const people = await request<{ id: number; username: string }[]>("/presence");
        if (!disposed) setOnline(people);
      } catch (e) { if (!disposed) setError((e as Error).message); }
    };
    beat();
    const timer = window.setInterval(beat, 30000);
    const events = new EventSource("/api/events", { withCredentials: true });
    let refreshTimer: number | undefined;
    events.onopen = () => {
      setConnected(true);
      // Refresh after every reconnect in addition to replaying durable events.
      window.dispatchEvent(new Event("scheduler-refresh"));
    };
    events.onerror = () => setConnected(false);
    events.addEventListener("session-expired", () => { events.close(); signOut(); });
    events.addEventListener("activity", () => {
      if (refreshTimer !== undefined) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        loadPrograms().catch(e => setError(e.message));
        window.dispatchEvent(new Event("scheduler-refresh"));
        window.dispatchEvent(new Event("scheduler-feed-refresh"));
      }, 150);
    });
    return () => { disposed = true; events.close(); clearInterval(timer); clearTimeout(refreshTimer); };
  }, [session]);
  useEffect(() => {
    const update = () => { loadFeed().catch(e => setError(e.message)); };
    window.addEventListener("scheduler-feed-refresh", update);
    return () => window.removeEventListener("scheduler-feed-refresh", update);
  }, [search, feedProgram, feedActor, security]);

  if (loading) return <main className="account-card">Loading scheduler…</main>;
  if (!session) return <AccountForm onSession={setSession} />;
  if (session.user.must_change_password) return <PasswordForm onDone={signOut} />;
  const canEdit = !!active && (session.user.is_admin || active.assigned_user_id === session.user.id);
  return <>
    <header className="online-header">
      <strong>CAMP Scheduler</strong>
      <label>Program <select value={programId ?? ""} onChange={e => setProgramId(e.target.value ? Number(e.target.value) : null)}>
        <option value="">All programs · view only</option>
        {programs.map(p => <option key={p.id} value={p.id}>{p.name}{p.assigned_user_id === session.user.id ? " · yours" : ""}</option>)}
      </select></label>
      <span>{canEdit ? "Editing enabled" : "Read-only"}</span>
      <span className={connected ? "connected" : "reconnecting"}>{connected ? "Live" : "Reconnecting…"}</span>
      <button type="button" onClick={toggleActivity} aria-expanded={activityVisible} aria-controls="shared-activity"
        title="Hide or show the activity panel. Schedules continue updating live.">
        {activityVisible ? "Hide live updates" : "Show live updates"}
      </button>
      <span>{session.user.username}</span>
      {session.user.is_admin && <button onClick={() => setAdminOpen(!adminOpen)}>Administration</button>}
      <button onClick={async () => { await request("/auth/logout", { method: "POST" }); signOut(); }}>Sign out</button>
    </header>
    {error && <div className="online-error" role="alert">{error} <button onClick={() => setError("")}>Dismiss</button></div>}
    {session.user.is_admin && adminOpen && <AdminPanel programs={programs} reload={loadPrograms} onError={setError} />}
    <div className={`online-layout${activityVisible ? "" : " activity-hidden"}`}>
      <div className="scheduler-workspace">
        {programs.length ? <App key={programId ?? "all"} readOnly={!canEdit} activeProgram={active?.name ?? ""} isAdmin={session.user.is_admin} /> : <div className="account-card">Create programs and accounts in Administration to begin.</div>}
      </div>
      <aside id="shared-activity" className="activity-panel" hidden={!activityVisible}>
        <h2>Shared activity</h2>
        <p className="presence">Online: {online.map(u => u.username).join(", ") || "No active users"}</p>
        <input aria-label="Search activity" placeholder="Search activity…" value={search} onChange={e => setSearch(e.target.value)} />
        <select aria-label="Activity program" value={feedProgram} onChange={e => setFeedProgram(e.target.value)}><option value="">All programs</option>{programs.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Activity user" value={feedActor} onChange={e => setFeedActor(e.target.value)}><option value="">All users</option>{actors.map(actor => <option key={actor.id} value={actor.id}>{actor.username}</option>)}</select>
        {session.user.is_admin && <label><input type="checkbox" checked={security} onChange={e => setSecurity(e.target.checked)} /> Security logs</label>}
        <button onClick={() => loadFeed()}>Refresh history</button>
        {feed.map(event => <article key={event.id} className="activity-event">
          <strong>{event.actor}</strong> {event.action} {event.entity_type}
          <div>{programs.find(p => p.id === event.program_id)?.name ?? "Shared"} · {new Date(event.created_at).toLocaleString()}</div>
          {event.reason && <p>Override: {event.reason}</p>}
          <details><summary>{visibleFields(event).map(field => fieldLabels[field] ?? field).join(", ") || "Details"}</summary>
            {visibleFields(event).map(field => <div key={field}><b>{fieldLabels[field] ?? field}</b>: {JSON.stringify(event.before?.[field] ?? null)} → {JSON.stringify(event.after?.[field] ?? null)}</div>)}
          </details>
        </article>)}
        {!feed.length && <p>No matching activity.</p>}
        {feed.length >= 100 && <button onClick={() => loadFeed(true)}>Load older activity</button>}
      </aside>
    </div>
  </>;
}

function AccountForm({ onSession }: { onSession: (value: Session) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault(); setBusy(true); setError("");
    const form = new FormData(e.currentTarget);
    try { onSession(await request<Session>("/auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <form className="account-card" onSubmit={submit}><h1>CAMP Scheduler</h1><p>Sign in with the account provided by your administrator.</p>
    <label>Username<input name="username" autoComplete="username" required /></label>
    <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
    {error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form>;
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const [error, setError] = useState("");
  return <form className="account-card" onSubmit={async e => {
    e.preventDefault(); const data = new FormData(e.currentTarget);
    if (data.get("password") !== data.get("confirmation")) { setError("Passwords do not match"); return; }
    try { await request("/auth/password", { method: "POST", body: JSON.stringify({ current_password: data.get("current_password"), password: data.get("password") }) }); onDone(); }
    catch (e) { setError((e as Error).message); }
  }}><h1>Change your initial password</h1>
    <label>Initial password<input name="current_password" type="password" autoComplete="current-password" required /></label>
    <label>New password<input name="password" type="password" autoComplete="new-password" minLength={12} required /></label>
    <label>Repeat new password<input name="confirmation" type="password" autoComplete="new-password" minLength={12} required /></label>
    <p>Use at least 12 characters. You will sign in again afterward.</p>{error && <p role="alert">{error}</p>}<button>Change password</button>
  </form>;
}

function AdminPanel({ programs, reload, onError }: { programs: Program[]; reload: () => Promise<Program[]>; onError: (message: string) => void }) {
  const [users, setUsers] = useState<OnlineUser[]>([]);
  const load = () => request<OnlineUser[]>("/admin/users").then(setUsers);
  useEffect(() => { load().catch(e => onError(e.message)); }, []);
  const run = async (work: () => Promise<unknown>) => { try { await work(); await load(); await reload(); } catch (e) { onError((e as Error).message); } };
  return <section className="admin-panel"><h2>Administration</h2>
    <form onSubmit={e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form);
      run(async () => { await request("/admin/users", { method: "POST", body: JSON.stringify({ username: data.get("username"), password: data.get("password"), is_admin: data.get("is_admin") === "on" }) }); form.reset(); });
    }}><h3>Create account</h3><input aria-label="New username" name="username" placeholder="Username" required maxLength={100} />
      <input aria-label="Initial password" name="password" type="password" placeholder="Initial password (12+ characters)" required minLength={12} />
      <label><input name="is_admin" type="checkbox" /> Administrator</label><button>Create account</button></form>
    <form onSubmit={e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form);
      run(async () => { await request("/admin/programs", { method: "POST", body: JSON.stringify({ name: data.get("name"), assigned_user_id: data.get("user") ? Number(data.get("user")) : null }) }); form.reset(); });
    }}><h3>Create program</h3><input aria-label="Program name" name="name" placeholder="Program name" required />
      <select name="user" aria-label="Assigned scheduler"><option value="">Unassigned</option>{users.filter(u => !u.disabled).map(u => <option key={u.id} value={u.id}>{u.username}</option>)}</select><button>Create program</button></form>
    <h3>Program assignments</h3>{programs.map(p => <label key={p.id}>{p.name} <select value={p.assigned_user_id ?? ""} onChange={e => {
      const id = e.target.value ? Number(e.target.value) : null;
      run(() => request(`/admin/programs/${p.id}`, { method: "PUT", body: JSON.stringify({ assigned_user_id: id, version: p.version }) }));
    }}><option value="">Unassigned</option>{users.map(u => <option disabled={u.disabled} key={u.id} value={u.id}>{u.username}{u.disabled ? " (disabled)" : ""}</option>)}</select></label>)}
    <h3>Accounts</h3>{users.map(u => <div className="admin-user" key={u.id}><span>{u.username} {u.is_admin ? "(admin)" : ""} {u.disabled ? "· disabled" : ""}</span>
      <button onClick={() => run(() => request(`/admin/users/${u.id}`, { method: "PUT", body: JSON.stringify({ disabled: !u.disabled }) }))}>{u.disabled ? "Enable" : "Disable"}</button>
      <button onClick={() => { const password = window.prompt(`New initial password for ${u.username} (12+ characters). This revokes their sessions.`); if (password) run(() => request(`/admin/users/${u.id}`, { method: "PUT", body: JSON.stringify({ password }) })); }}>Reset password</button>
    </div>)}
    <DatabaseControls />
    <p>Use the Rules menu to turn room and faculty conflict checks on or off for everyone. Only admins can change these settings. A section cannot have two classes at the same time.</p>
  </section>;
}

function DatabaseControls() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await action(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="database-controls"><h3>Database and timetable</h3>
    <p>Backups include every program, accounts, password hashes, curricula, settings, and activity history. Keep downloaded backups private. Browser backups support up to 12 MiB; use the server backup scripts for larger databases.</p>
    <button type="button" disabled={busy} onClick={() => run(async () => {
      const blob = await downloadDatabaseBackup();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `scheduler-${new Date().toISOString().replace(/[:.]/g, "-")}.scheduler-backup`;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setMessage("Database backup downloaded.");
    })}>Download full database backup</button>
    <form onSubmit={e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form);
      const password = String(data.get("password") ?? "");
      const passwordInput = form.querySelector<HTMLInputElement>('input[name="password"]');
      if (passwordInput) passwordInput.value = "";
      run(async () => {
        if (!file || !file.size || file.size > 12 * 1024 * 1024) throw new Error("Choose a .scheduler-backup file no larger than 12 MiB.");
        await request("/admin/database/restore", { method: "POST", body: file, headers: {
          "Content-Type": "application/octet-stream", "X-Admin-Password": password, "X-Restore-Confirmation": "REPLACE DATABASE",
        } });
        window.dispatchEvent(new Event("scheduler-session-expired"));
      });
    }}><h4>Replace the entire database</h4>
      <p>This replaces all programs, accounts, schedules, curricula, settings, and history with the uploaded backup. Everyone is signed out. Sign in with an account and password from that backup. Download a current backup first if you need to keep the current data.</p>
      <p>Upload a .scheduler-backup downloaded here. PostgreSQL .dump files use the server restore script.</p>
      <input aria-label="Database backup file" type="file" accept=".scheduler-backup" required disabled={busy} onChange={e => setFile(e.target.files?.[0] ?? null)} />
      <label>Current administrator password<input name="password" type="password" autoComplete="current-password" required maxLength={256} disabled={busy} /></label>
      <label><input type="checkbox" required disabled={busy} /> I understand this replaces the entire database.</label>
      <button disabled={busy}>Upload and replace database</button>
    </form>
    <form onSubmit={e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form);
      const password = String(data.get("password") ?? "");
      form.reset();
      run(async () => {
        const result = await request<{ deleted: number }>("/admin/timetable/clear", { method: "POST", body: JSON.stringify({ password }) });
        setMessage(`Timetable cleared: ${result.deleted} classes removed across all programs.`);
        window.dispatchEvent(new Event("scheduler-refresh"));
        window.dispatchEvent(new Event("scheduler-feed-refresh"));
      });
    }}><h4>Clear current timetable</h4>
      <p>Removes all scheduled classes across all programs. Accounts, programs, sections, rooms, faculty, curricula, settings, and activity history are kept.</p>
      <label>Re-type your administrator password<input name="password" type="password" autoComplete="current-password" required maxLength={256} disabled={busy} /></label>
      <label><input type="checkbox" required disabled={busy} /> I understand this clears classes for every program.</label>
      <button disabled={busy}>Clear timetable for all programs</button>
    </form>
    {busy && <p role="status">Working… Keep this page open until the operation finishes.</p>}
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
  </section>;
}
