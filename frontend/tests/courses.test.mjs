import assert from "node:assert/strict";
import { test } from "node:test";
import { courseOptions, meetingLanes } from "../src/courses.ts";
import { loadPaneSettings } from "../src/panes.ts";

test("courses combine case and whitespace variants without merging different codes", () => {
  const options = courseOptions([
    { "Course Code": " CS101 ", "Course Description": "" },
    { "Course Code": "cs101", "Course Description": "Introduction" },
    { "Course Code": "CS101 LAB", "Course Description": "Laboratory" },
    { "Course Code": " ", "Course Description": "Unknown" },
  ]);
  assert.equal(options.length, 2);
  assert.equal(options[0].name, "CS101");
  assert.equal(options[0].description, "Introduction");
});

test("connected overlaps share lanes while adjacent meetings reuse full width", () => {
  const lanes = meetingLanes([
    { id: 1, start: 420, end: 480 }, { id: 2, start: 450, end: 510 },
    { id: 3, start: 480, end: 540 }, { id: 4, start: 540, end: 600 },
  ]);
  assert.deepEqual(lanes.get(1), { lane: 0, count: 2 });
  assert.deepEqual(lanes.get(2), { lane: 1, count: 2 });
  assert.deepEqual(lanes.get(3), { lane: 0, count: 2 });
  assert.deepEqual(lanes.get(4), { lane: 0, count: 1 });
});

test("saved course panes load and older panes get an empty course selection", () => {
  const settings = loadPaneSettings({ getItem: () => JSON.stringify({ left: { mode: "timetable-course", course: "CS101" }, right: { mode: "timetable-room", room: "R1" } }) });
  assert.equal(settings.left.course, "CS101");
  assert.equal(settings.left.mode, "timetable-course");
  assert.equal(settings.right.course, "");
  assert.equal(settings.right.room, "R1");
});
