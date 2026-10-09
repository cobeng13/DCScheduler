import assert from "node:assert/strict";
import { test } from "node:test";
import { buildPastedClass, pasteTargetForCell } from "../src/paste.ts";

test("right-clicking Saturday replaces a stale selection on another day", () => {
  const selection = { day: "M", startMinutes: 420, endMinutes: 660 };
  assert.deepEqual(pasteTargetForCell({ day: "Sa", startMinutes: 780 }, selection),
    { day: "Sa", startMinutes: 780 });
  assert.deepEqual(pasteTargetForCell({ day: "M", startMinutes: 450 }, selection),
    { day: "M", startMinutes: 420 });
  assert.deepEqual(pasteTargetForCell({ day: "M", startMinutes: 720 }, selection),
    { day: "M", startMinutes: 720 });
});

const source = { id: 10, version: 4, section_id: 2, Section: "BS Pharm 1A",
  Days: "T,Th", "Time (24 Hrs)": "07:00-11:00", "Time (LPU Std)": "7:00a-11:00a",
  "Course Code": "Phar Chem 2 LAB", Room: "Lab 1", Faculty: "Garcia", program_id: 1 };

test("pasting a weekday class onto Saturday checks and saves only the Saturday destination", () => {
  const result = buildPastedClass(source, "BS Pharm 1B", { day: "Sa", startMinutes: 780 }, 240);
  assert.equal(result.Days, "Sa");
  assert.equal(result.Section, "BS Pharm 1B");
  assert.equal(result["Time (24 Hrs)"], "13:00-17:00");
  assert.equal(result["Time (LPU Std)"], "1:00p-5:00p");
  assert.equal(result.id, 0);
  assert.equal("version" in result, false);
  assert.equal("section_id" in result, false);
  assert.equal(result.Room, source.Room);
  assert.equal(result.Faculty, source.Faculty);
  assert.equal(result.program_id, 1);
  assert.equal(source.Days, "T,Th");
  assert.equal(source.Section, "BS Pharm 1A");
});

test("same-section paste keeps the meeting duration rather than the clicked cell duration", () => {
  const result = buildPastedClass(source, source.Section, { day: "Sa", startMinutes: 435 }, 240);
  assert.equal(result["Time (24 Hrs)"], "07:15-11:15");
  assert.equal(result["Time (LPU Std)"], "7:15a-11:15a");
});

test("paste rejects invalid or out-of-day destinations before saving", () => {
  for (const [day, start, duration] of [["Sa", 1320, 240], ["Sa", 420, 0],
    ["Sa", -1, 60], ["TBA", 420, 60], ["Sa", NaN, 60]]) {
    assert.throws(() => buildPastedClass(source, source.Section, { day, startMinutes: start }, duration), /does not fit/);
  }
});
