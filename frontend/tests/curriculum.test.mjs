import assert from "node:assert/strict";
import { test } from "node:test";
import { parseCurriculumCsv, curriculumIdForSection, coursesForSection } from "../src/curriculum.ts";

const combinedHeader = "Year,Semester,Subject Code,Description,Units Lec,Units Lab,Pre-requisite\n";

test("BSMLS combined units layout uses the selected program and keeps RLE annotations", () => {
  const courses = parseCurriculumCsv("\uFEFF" + combinedHeader +
    'First Year,First Semester,Chem 1,"Chemistry, introductory",3,2,\n' +
    "Fourth Year,Second Semester,MTAC 2,Assessment,,3 (RLE),MTAC 1\n", "BSMLS");
  assert.equal(courses.length, 3);
  const lecture = courses.find(c => c.courseCode === "Chem 1 LEC");
  const lab = courses.find(c => c.courseCode === "Chem 1 LAB");
  assert.equal(lecture.program, "BSMLS");
  assert.equal(lecture.courseDescription, "Chemistry, introductory LEC");
  assert.equal(lecture.hours, 3);
  assert.equal(lab.hours, 6);
  const rle = courses.find(c => c.courseCode === "MTAC 2 LAB");
  assert.equal(rle.totalUnits, 3);
  assert.equal(rle.hours, 9);
  assert.equal(rle.unitNotes, "LAB (RLE)");
  assert.equal(rle.prerequisite, "MTAC 1");
});

test("different curriculum versions retain different units for the same code", () => {
  const row = units => `Fourth Year,First Semester,MTAC 1,Assessment,,${units} (RLE),\n`;
  const old = parseCurriculumCsv(combinedHeader + row(3), "BSMLS");
  const current = parseCurriculumCsv(combinedHeader + row(2), "BSMLS");
  assert.equal(old[0].totalUnits, 3);
  assert.equal(current[0].totalUnits, 2);
  assert.equal(old[0].hours, 9);
  assert.equal(current[0].hours, 6);
});

test("existing separate LEC/LAB format remains supported", () => {
  const courses = parseCurriculumCsv(
    "Program,Year Level,Semester,Course Code,Course Description,Lec/Lab,Units\n" +
    "BSMLS,First Year,1st,C1,Course,LEC,3\nBSMLS,First Year,1st,C1,Course,LAB,1\n", "BSMLS");
  assert.deepEqual(courses.map(c => c.courseCode), ["C1 LAB", "C1 LEC"]);
  assert.equal(courses.find(c => c.courseCode === "C1 LAB").hours, 3);
});

test("invalid curriculum rows are rejected rather than silently dropped", () => {
  assert.throws(() => parseCurriculumCsv(combinedHeader + "First Year,Bad term,C1,Course,3,,\n", "BSMLS"), /Row 2/);
  assert.throws(() => parseCurriculumCsv(combinedHeader + "First Year,First Semester,C1,Course,three,,\n", "BSMLS"), /invalid units/);
  assert.throws(() => parseCurriculumCsv(combinedHeader + "First Year,First Semester,C1,Course,-3,,\n", "BSMLS"), /invalid units/);
  assert.throws(() => parseCurriculumCsv(combinedHeader + "First Year,First Semester,C1,Course,,,\n", "BSMLS"), /greater than zero/);
  assert.throws(() => parseCurriculumCsv(combinedHeader, "BSMLS"), /No curriculum courses/);
  assert.throws(() => parseCurriculumCsv(combinedHeader + '"unclosed', "BSMLS"), /Unclosed/);
});

test("program ownership and field/file limits are enforced before preview", () => {
  assert.throws(() => parseCurriculumCsv(combinedHeader), /Select a program/);
  assert.throws(() => parseCurriculumCsv("Program," + combinedHeader + "OTHER,First Year,First Semester,C1,Course,3,,\n", "BSMLS"), /must match/);
  assert.throws(() => parseCurriculumCsv(combinedHeader + `First Year,First Semester,${"X".repeat(101)},Course,3,,\n`, "BSMLS"), /oversized/);
  assert.throws(() => parseCurriculumCsv("x".repeat(1024 * 1024 + 1), "BSMLS"), /limit/);
});

test("section curriculum overrides its year-level default", () => {
  const state = { sectionYearLevels: { a: "First Year", b: "First Year" },
    yearLevelCurriculumIds: { "first year": "current" }, sectionCurriculumIds: { b: "old" } };
  assert.equal(curriculumIdForSection(" A ", state), "current");
  assert.equal(curriculumIdForSection("B", state), "old");
  assert.equal(curriculumIdForSection("Unassigned", state), "");
});

test("section assignments keep version-specific units and avoid other-year fallback", () => {
  const row = units => combinedHeader + `Fourth Year,First Semester,MTAC 1,Assessment,,${units} (RLE),\n`;
  const curricula = [{ id: "old", courses: parseCurriculumCsv(row(3), "BSMLS") },
    { id: "new", courses: parseCurriculumCsv(row(2), "BSMLS") }];
  const state = { sectionYearLevels: { a: "Fourth Year", b: "Fourth Year", c: "First Year" },
    yearLevelCurriculumIds: { "fourth year": "old", "first year": "new" }, sectionCurriculumIds: { b: "new" } };
  assert.equal(coursesForSection(curricula, "First Semester", "A", state)[0].hours, 9);
  assert.equal(coursesForSection(curricula, "First Semester", "B", state)[0].hours, 6);
  assert.deepEqual(coursesForSection(curricula, "First Semester", "C", state), []);
  assert.deepEqual(coursesForSection(curricula, "Second Semester", "A", state), []);
});
