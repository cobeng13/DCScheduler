export const courseKey = (code: string) => code.trim().toLowerCase();

export function courseOptions(entries: { "Course Code": string; "Course Description": string }[]) {
  const courses = new Map<string, { id: number; name: string; description: string }>();
  for (const entry of entries) {
    const key = courseKey(entry["Course Code"]);
    if (!key) continue;
    const previous = courses.get(key);
    if (!previous) courses.set(key, { id: courses.size, name: entry["Course Code"].trim(), description: entry["Course Description"].trim() });
    else if (!previous.description) previous.description = entry["Course Description"].trim();
  }
  return [...courses.values()].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

// Allocate lanes per connected overlap group so simultaneous sections remain visible.
export function meetingLanes(meetings: { id: number; start: number; end: number }[]) {
  const result = new Map<number, { lane: number; count: number }>();
  const sorted = [...meetings].sort((a, b) => a.start - b.start || a.end - b.end || a.id - b.id);
  let group: typeof sorted = [], ends: number[] = [], groupEnd = -Infinity;
  const finish = () => { for (const meeting of group) result.get(meeting.id)!.count = ends.length; };
  for (const meeting of sorted) {
    if (meeting.start >= groupEnd) { finish(); group = []; ends = []; }
    let lane = ends.findIndex(end => end <= meeting.start);
    if (lane < 0) lane = ends.length;
    ends[lane] = meeting.end;
    result.set(meeting.id, { lane, count: 1 });
    group.push(meeting);
    groupEnd = group.length === 1 ? meeting.end : Math.max(groupEnd, meeting.end);
  }
  finish();
  return result;
}
