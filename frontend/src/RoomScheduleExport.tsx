import { useEffect, useRef, useState, type FormEvent } from "react";
import { downloadRoomSchedule, scopedStorage, type RoomScheduleOptions } from "./online";

export function RoomScheduleExport({ rooms, onClose, onExported }: {
  rooms: { id: number; name: string }[]; onClose: () => void; onExported: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [options, setOptions] = useState<RoomScheduleOptions>(() => {
    const headings = { schoolYear: `${new Date().getFullYear()} - ${new Date().getFullYear() + 1}`,
      institution: "LYCEUM OF THE PHILIPPINES UNIVERSITY - BATANGAS", college: "COLLEGE OF ALLIED MEDICAL PROFESSIONS", title: "ROOM SCHEDULE" };
    try {
      const saved = JSON.parse(scopedStorage.getItem("scheduler.sectionSchedule.headings") || "null");
      for (const field of ["schoolYear", "institution", "college"] as const) {
        if (typeof saved?.[field] === "string") headings[field] = saved[field];
      }
      const roomHeadings = JSON.parse(scopedStorage.getItem("scheduler.roomSchedule.headings") || "null");
      for (const field of Object.keys(headings) as Array<keyof typeof headings>) {
        if (typeof roomHeadings?.[field] === "string") headings[field] = roomHeadings[field];
      }
    } catch { /* Keep defaults if saved preferences are invalid. */ }
    return { ...headings, roomIds: null, includeSunday: false };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    setOptions(previous => {
      if (previous.roomIds === null) return previous;
      const eligible = new Set(rooms.map(room => room.id));
      const ids = previous.roomIds.filter(id => eligible.has(id));
      return ids.length === previous.roomIds.length ? previous : { ...previous, roomIds: ids };
    });
  }, [rooms]);
  const field = <K extends keyof RoomScheduleOptions>(name: K, value: RoomScheduleOptions[K]) => {
    setOptions(previous => ({ ...previous, [name]: value })); setError("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const blob = await downloadRoomSchedule(options);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "room-schedules.xlsx";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      const { roomIds: _rooms, includeSunday: _sunday, ...headings } = options;
      try { scopedStorage.setItem("scheduler.roomSchedule.headings", JSON.stringify(headings)); } catch { /* Download succeeds without preference storage. */ }
      onExported(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not export the room schedule. Try again."); }
    finally { setBusy(false); }
  };
  return <dialog ref={dialog} className="modal section-schedule-dialog" aria-labelledby="room-schedule-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={submit}>
      <h3 id="room-schedule-title">Export Room Schedule (Excel)</h3>
      <fieldset disabled={busy} className="section-schedule-fields">
        <label className="modal-field">Rooms
          <select value={options.roomIds === null ? "all" : "selected"} onChange={event => field("roomIds", event.target.value === "all" ? null : rooms.map(room => room.id))}>
            <option value="all">All rooms</option><option value="selected">Selected rooms</option>
          </select>
        </label>
        {options.roomIds !== null && <div className="faculty-export-list">{rooms.map(room => <label key={room.id} className="menu-checkbox">
          <input type="checkbox" checked={options.roomIds!.includes(room.id)} onChange={event => field("roomIds", event.target.checked
            ? [...options.roomIds!, room.id] : options.roomIds!.filter(id => id !== room.id))} />{room.name}
        </label>)}{!rooms.length && <p>No rooms to select.</p>}</div>}
        <label className="menu-checkbox"><input type="checkbox" checked={options.includeSunday}
          onChange={event => field("includeSunday", event.target.checked)} />Include Sunday even when empty</label>
        <label className="modal-field">School year<input required maxLength={40} value={options.schoolYear} onChange={event => field("schoolYear", event.target.value)} /></label>
        <label className="modal-field">Institution<input maxLength={200} value={options.institution} onChange={event => field("institution", event.target.value)} /></label>
        <label className="modal-field">College<input maxLength={200} value={options.college} onChange={event => field("college", event.target.value)} /></label>
        <label className="modal-field">Report title<input required maxLength={200} value={options.title} onChange={event => field("title", event.target.value)} /></label>
      </fieldset>
      <p className="modal-note">One worksheet per day, with rooms across columns and 30-minute rows from 7 AM to 9 PM.
        Bookings from all programs are included; rooms marked Ignore conflicts are excluded. Earlier or later bookings extend the grid; Sunday is included when booked.
        Exact times and faculty names are shown as stored. TBA entries are listed separately.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="modal-actions"><button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" disabled={busy || (options.roomIds !== null && !options.roomIds.length)}>{busy ? "Exporting…" : "Download Excel"}</button>
      </div>
    </form>
  </dialog>;
}
