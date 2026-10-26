import { useEffect, useRef, useState, type FormEvent } from "react";
import { downloadSectionSchedule, scopedStorage, type SectionScheduleOptions } from "./online";

type Section = { id: number; name: string };
const defaults = () => ({
  schoolYear: `${new Date().getFullYear()} - ${new Date().getFullYear() + 1}`,
  institution: "LYCEUM OF THE PHILIPPINES UNIVERSITY - BATANGAS",
  college: "COLLEGE OF ALLIED MEDICAL PROFESSIONS",
  title: "SCHEDULE OF CLASSES",
});

export function SectionScheduleExport({ program, sections, selectedSection, onClose, onExported }: {
  program: string; sections: Section[]; selectedSection: string;
  onClose: () => void; onExported: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [options, setOptions] = useState<SectionScheduleOptions>(() => {
    let headings = defaults();
    try {
      const saved = JSON.parse(scopedStorage.getItem("scheduler.sectionSchedule.headings") || "null");
      if (saved) for (const field of Object.keys(headings) as Array<keyof typeof headings>) {
        if (typeof saved[field] === "string") headings[field] = saved[field];
      }
    } catch { /* Keep the editable defaults when a saved preference is invalid. */ }
    const selected = sections.find(section => section.name === selectedSection);
    return { ...headings, scope: program ? "program" : "all", sectionIds: selected ? [selected.id] : sections.map(section => section.id) };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); }, []);
  const field = <K extends keyof SectionScheduleOptions>(name: K, value: SectionScheduleOptions[K]) => {
    setOptions(previous => ({ ...previous, [name]: value }));
    setError("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const blob = await downloadSectionSchedule(options);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = options.scope === "all" ? "all-program-schedules.xlsx" : `${program.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")}-section-schedules.xlsx`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      const { scope: _scope, sectionIds: _ids, includeRooms: _rooms, ...headings } = options;
      try { scopedStorage.setItem("scheduler.sectionSchedule.headings", JSON.stringify(headings)); } catch { /* Download still succeeds without preference storage. */ }
      onExported(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not export the schedule. Try again."); }
    finally { setBusy(false); }
  };
  return <dialog ref={dialog} className="modal section-schedule-dialog" aria-labelledby="section-schedule-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={submit}>
      <h3 id="section-schedule-title">Export Section Schedule (Excel)</h3>
      <fieldset disabled={busy} className="section-schedule-fields">
        <label className="modal-field">Export scope
          <select value={options.scope} onChange={event => field("scope", event.target.value as SectionScheduleOptions["scope"])}>
            <option value="sections" disabled={!program}>Selected sections{program ? ` in ${program}` : ""}</option>
            <option value="program" disabled={!program}>Entire program{program ? `: ${program}` : ""}</option>
            <option value="all">All programs (separate worksheets)</option>
          </select>
        </label>
        <label className="menu-checkbox"><input type="checkbox" checked={!!options.includeRooms}
          onChange={event => field("includeRooms", event.target.checked)} />Include room schedules (all programs, 90-minute bands)</label>
        {options.includeRooms && <p className="modal-note">Rooms marked Ignore conflicts are excluded from the room worksheets.</p>}
        {options.scope === "sections" && <div>
          <label className="menu-checkbox"><input type="checkbox" checked={sections.length > 0 && options.sectionIds.length === sections.length}
            onChange={event => field("sectionIds", event.target.checked ? sections.map(section => section.id) : [])} />Select all sections</label>
          <div className="faculty-export-list">{sections.map(section => <label key={section.id} className="menu-checkbox">
            <input type="checkbox" checked={options.sectionIds.includes(section.id)} onChange={event => field("sectionIds", event.target.checked
              ? [...options.sectionIds, section.id] : options.sectionIds.filter(id => id !== section.id))} />{section.name}
          </label>)}</div>
          {!sections.length && <p>No sections in this program.</p>}
        </div>}
        <label className="modal-field">School year<input required maxLength={40} value={options.schoolYear} onChange={event => field("schoolYear", event.target.value)} /></label>
        <label className="modal-field">Institution<input maxLength={200} value={options.institution} onChange={event => field("institution", event.target.value)} /></label>
        <label className="modal-field">College<input maxLength={200} value={options.college} onChange={event => field("college", event.target.value)} /></label>
        <label className="modal-field">Report title<input required maxLength={200} value={options.title} onChange={event => field("title", event.target.value)} /></label>
      </fieldset>
      <p className="modal-note">Uses each program's current semester and each section's assigned curriculum. Unscheduled courses show TBA.
        Assign curricula and year levels in Edit Sections to include the full course list. Faculty names appear as stored; modality is omitted.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="modal-actions"><button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" disabled={busy || (options.scope === "sections" && !options.sectionIds.length)}>{busy ? "Exporting…" : "Download Excel"}</button>
      </div>
    </form>
  </dialog>;
}
