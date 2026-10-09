export type CurriculumTerm = "First Semester" | "Second Semester" | "Term Break";

export type CurriculumCourse = {
  program: string;
  yearLevel: string;
  semester: CurriculumTerm;
  courseCode: string;
  courseDescription: string;
  lecUnits: number;
  labUnits: number;
  totalUnits: number;
  hours: number;
  unitNotes?: string;
  prerequisite?: string;
};

export type Curriculum = {
  id: string;
  name: string;
  sourceFileName: string;
  importedAt: string;
  courses: CurriculumCourse[];
};

export type CurriculumState = {
  curricula: Curriculum[];
  selectedTerm: CurriculumTerm;
  sectionYearLevels: Record<string, string>;
  yearLevelCurriculumIds: Record<string, string>;
  sectionCurriculumIds: Record<string, string>;
};


const normalizeMatchValue = (value: string) => value.trim().toLowerCase();
const roundHours = (value: number) => Math.round(value * 100) / 100;

export function coursePlotStatus(plottedHours: number, requiredHours: number | null) {
  const difference = requiredHours === null ? null : roundHours(plottedHours - requiredHours);
  return {
    plottedHours,
    requiredHours,
    isComplete: difference === 0,
    isOverPlotted: difference !== null && difference > 0,
  };
}

export const curriculumTerms: CurriculumTerm[] = ["First Semester", "Second Semester", "Term Break"];

export const normalizeSemester = (value: string): CurriculumTerm | null => {
  const cleaned = normalizeMatchValue(value);
  if (["1st", "1st sem", "first sem", "first semester"].includes(cleaned)) {
    return "First Semester";
  }
  if (["2nd", "2nd sem", "second sem", "second semester"].includes(cleaned)) {
    return "Second Semester";
  }
  if (["term break", "term-break", "summer", "midyear"].includes(cleaned)) {
    return "Term Break";
  }
  return curriculumTerms.find((term) => normalizeMatchValue(term) === cleaned) ?? null;
};

const calculateCurriculumHours = (
  semester: CurriculumTerm,
  lecUnits: number,
  labUnits: number
) => {
  if (semester === "Term Break") {
    return roundHours(lecUnits * 4.25);
  }
  return roundHours(lecUnits + labUnits * 3);
};

const parseCsvText = (text: string) => {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && inQuotes && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(cell);
      if (row.some((value) => value.trim())) {
        rows.push(row);
      }
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  if (inQuotes) throw new Error("Unclosed quoted CSV cell");
  row.push(cell);
  if (row.some((value) => value.trim())) {
    rows.push(row);
  }
  return rows;
};

export const parseCurriculumCsv = (text: string, selectedProgram = "") => {
  if (new TextEncoder().encode(text).length > 1024 * 1024) throw new Error("Curriculum CSV limit is 1 MiB");
  const rows = parseCsvText(text);
  const headers = rows[0]?.map((header) => header.replace(/^\uFEFF/, "").trim().toLowerCase()) ?? [];
  const headerIndex = (...names: string[]) => headers.findIndex(header => names.some(name => header === name.toLowerCase()));
  const indexes = {
    program: headerIndex("Program"), yearLevel: headerIndex("Year Level", "Year"),
    semester: headerIndex("Semester"), courseCode: headerIndex("Course Code", "Subject Code"),
    courseDescription: headerIndex("Course Description", "Description"),
    lecLab: headerIndex("Lec/Lab"), units: headerIndex("Units"),
    lecUnits: headerIndex("Units Lec", "Lec Units"), labUnits: headerIndex("Units Lab", "Lab Units"),
    prerequisite: headerIndex("Pre-requisite", "Prerequisite"),
  };
  const combinedUnits = indexes.lecUnits !== -1 && indexes.labUnits !== -1;
  const required = ["yearLevel", "semester", "courseCode", "courseDescription"] as const;
  const missing = required.filter(key => indexes[key] === -1);
  if (missing.length || (!combinedUnits && (indexes.lecLab === -1 || indexes.units === -1))) {
    throw new Error(`Missing curriculum columns: ${[...missing, ...(!combinedUnits && (indexes.lecLab === -1 || indexes.units === -1) ? ["Units Lec + Units Lab, or Lec/Lab + Units"] : [])].join(", ")}. Use Load Curricula for curricula; Import Timetable CSV requires scheduled classes.`);
  }
  if (indexes.program === -1 && !selectedProgram) throw new Error("Select a program before loading a curriculum without a Program column");
  const parseUnits = (value: string, row: number) => {
    const input = value.trim();
    if (!input) return { value: 0, note: "" };
    const match = input.match(/^(\d+(?:\.\d+)?|\.\d+)\s*(\([^()]{1,100}\))?$/);
    if (!match || !Number.isFinite(Number(match[1]))) throw new Error(`Row ${row}: invalid units '${input}'. Use a nonnegative number, optionally followed by a note in parentheses.`);
    return { value: Number(match[1]), note: match[2] ?? "" };
  };

  type CurriculumCourseParts = {
    program: string;
    yearLevel: string;
    semester: CurriculumTerm;
    courseCode: string;
    courseDescription: string;
    lecUnits: number;
    labUnits: number;
    unitNotes: string;
    prerequisite: string;
  };

  const coursesByKey = new Map<string, CurriculumCourseParts>();
  rows.slice(1).forEach((row, index) => {
    const rowNumber = index + 2;
    const semester = normalizeSemester(row[indexes.semester] ?? "");
    const courseCode = (row[indexes.courseCode] ?? "").trim();
    const courseDescription = (row[indexes.courseDescription] ?? "").trim();
    if (!semester || !courseCode || !courseDescription) throw new Error(`Row ${rowNumber}: semester, course code and description are required`);
    const program = (indexes.program === -1 ? selectedProgram : row[indexes.program] ?? "").trim();
    const yearLevel = (row[indexes.yearLevel] ?? "").trim();
    const prerequisite = (row[indexes.prerequisite] ?? "").trim();
    if (!program || (selectedProgram && program !== selectedProgram)) throw new Error(`Row ${rowNumber}: program must match the selected program '${selectedProgram}'`);
    if (!yearLevel || program.length > 200 || yearLevel.length > 100 || courseCode.length > 100 || courseDescription.length > 2000 || prerequisite.length > 2000 || row.some(cell => cell.includes("\0"))) throw new Error(`Row ${rowNumber}: invalid or oversized course text`);
    const lecLab = normalizeMatchValue(row[indexes.lecLab] ?? "");
    if (!combinedUnits && !["lec", "lecture", "lab", "laboratory"].includes(lecLab)) throw new Error(`Row ${rowNumber}: Lec/Lab must be LEC or LAB`);
    const lec = parseUnits(combinedUnits ? row[indexes.lecUnits] ?? "" : lecLab.startsWith("lec") ? row[indexes.units] ?? "" : "", rowNumber);
    const lab = parseUnits(combinedUnits ? row[indexes.labUnits] ?? "" : lecLab.startsWith("lab") ? row[indexes.units] ?? "" : "", rowNumber);
    if (lec.value + lab.value === 0) throw new Error(`Row ${rowNumber}: at least one unit value must be greater than zero`);
    const key = [
      normalizeMatchValue(program),
      normalizeMatchValue(yearLevel),
      normalizeMatchValue(semester),
      normalizeMatchValue(courseCode),
      normalizeMatchValue(courseDescription),
    ].join("|");
    const existing =
      coursesByKey.get(key) ??
      {
        program,
        yearLevel,
        semester,
        courseCode,
        courseDescription,
        lecUnits: 0,
        labUnits: 0,
        unitNotes: "",
        prerequisite,
      };
    existing.lecUnits += lec.value;
    existing.labUnits += lab.value;
    existing.unitNotes = [existing.unitNotes, lec.note && `LEC ${lec.note}`, lab.note && `LAB ${lab.note}`].filter(Boolean).join("; ");
    coursesByKey.set(key, existing);
  });

  const courses = [...coursesByKey.values()].flatMap((course) => {
    const hasLec = course.lecUnits > 0;
    const hasLab = course.labUnits > 0;
    const hasBoth = hasLec && hasLab;
    const entries: CurriculumCourse[] = [];

    if (hasLec) {
      entries.push({
        ...course,
        courseCode: hasBoth ? `${course.courseCode} LEC` : course.courseCode,
        courseDescription: hasBoth
          ? `${course.courseDescription} LEC`
          : course.courseDescription,
        lecUnits: course.lecUnits,
        labUnits: 0,
        totalUnits: roundHours(course.lecUnits),
        hours: calculateCurriculumHours(course.semester, course.lecUnits, 0),
      });
    }

    if (hasLab) {
      entries.push({
        ...course,
        courseCode: `${course.courseCode} LAB`,
        courseDescription: `${course.courseDescription} LAB`,
        lecUnits: 0,
        labUnits: course.labUnits,
        totalUnits: roundHours(course.labUnits),
        hours: calculateCurriculumHours(course.semester, 0, course.labUnits),
      });
    }

    return entries;
  });

  if (!courses.length) throw new Error("No curriculum courses found");
  if (courses.length > 2000) throw new Error("Curriculum limit is 2000 lecture/lab course entries");
  if (courses.some(course => course.courseCode.length > 100 || course.courseDescription.length > 2000 || !Number.isFinite(course.hours))) throw new Error("Course text or hours exceed the supported limits after separating lecture and laboratory");
  return courses.sort((left, right) =>
    left.courseCode.localeCompare(right.courseCode, undefined, { sensitivity: "base" })
  );
};

export function curriculumIdForSection(section: string, state: Pick<CurriculumState, "sectionYearLevels" | "yearLevelCurriculumIds" | "sectionCurriculumIds">) {
  const key = normalizeMatchValue(section);
  const year = state.sectionYearLevels[key] ?? "";
  return state.sectionCurriculumIds[key] || state.yearLevelCurriculumIds[normalizeMatchValue(year)] || "";
}

export function coursesForSection(curricula: Curriculum[], term: CurriculumTerm, section: string,
  state: Pick<CurriculumState, "sectionYearLevels" | "yearLevelCurriculumIds" | "sectionCurriculumIds">) {
  const identity = curriculumIdForSection(section, state);
  const courses = identity ? curricula.find(curriculum => curriculum.id === identity)?.courses ?? [] : curricula.flatMap(curriculum => curriculum.courses);
  const active = courses.filter(course => course.semester === term);
  const year = state.sectionYearLevels[normalizeMatchValue(section)] ?? "";
  if (!year) return active;
  const matching = active.filter(course => normalizeMatchValue(course.yearLevel) === normalizeMatchValue(year));
  // An explicit curriculum must never fall back to a different cohort/year.
  return matching.length || identity ? matching : active;
}
