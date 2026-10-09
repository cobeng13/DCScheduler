export type PasteTarget = { day: string; startMinutes: number };

export function pasteTargetForCell(clicked: PasteTarget, selection: (PasteTarget & { endMinutes: number }) | null) {
  return selection && clicked.day === selection.day &&
    clicked.startMinutes >= selection.startMinutes && clicked.startMinutes < selection.endMinutes
    ? { day: selection.day, startMinutes: selection.startMinutes } : clicked;
}

type CopiedClass = {
  id: number;
  version?: number;
  section_id?: number;
  Section: string;
  Days: string;
  "Time (24 Hrs)": string | null;
  "Time (LPU Std)": string;
};

// A paste copies one class meeting, even when the source meets on several days.
export function buildPastedClass<T extends CopiedClass>(
  source: T, section: string, target: PasteTarget, durationMinutes: number
) {
  const end = target.startMinutes + durationMinutes;
  if (!["M", "T", "W", "Th", "F", "Sa", "Su"].includes(target.day) ||
      !Number.isInteger(target.startMinutes) || target.startMinutes < 0 ||
      !Number.isInteger(durationMinutes) || durationMinutes <= 0 || end >= 1440) {
    throw new Error("The copied class does not fit at this time. Choose an earlier slot or a class with a valid duration.");
  }
  const clock = (minutes: number) => `${Math.floor(minutes / 60).toString().padStart(2, "0")}:${(minutes % 60).toString().padStart(2, "0")}`;
  const lpu = (minutes: number) => {
    const hour = Math.floor(minutes / 60);
    return `${hour % 12 || 12}:${(minutes % 60).toString().padStart(2, "0")}${hour >= 12 ? "p" : "a"}`;
  };
  const { version: _version, section_id: _sectionId, ...copy } = source;
  return {
    ...copy,
    id: 0,
    Section: section,
    Days: target.day,
    "Time (24 Hrs)": `${clock(target.startMinutes)}-${clock(end)}`,
    "Time (LPU Std)": `${lpu(target.startMinutes)}-${lpu(end)}`,
  };
}
