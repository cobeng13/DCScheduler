# Curriculum CSV compatibility and multiple curricula

There are two different imports. **File > Import Timetable CSV** imports scheduled classes (section, time, days, room, faculty) and replaces the selected program's timetable after confirmation. **File > Load Curricula** loads course catalogs, units and calculated hours; it does not create scheduled classes or replace the timetable. Use Load Curricula for the two BSMLS files reviewed here.

## Reviewed files

| File | Source rows | Parsed lecture/laboratory entries |
| --- | ---: | ---: |
| BSMLS_CURRICULUM_23_24.csv | 58 | 81 |
| BSMLS_CURRICULUM_26_27.csv | 59 | 82 |

Both files have the same headers and cover First through Fourth Year and First Semester, Second Semester and Term Break. The old curriculum parser expected a different layout and a Program column; the timetable importer requires actual class-booking fields. Neither previously accepted these files as-is.

| Your column | Supported curriculum meaning |
| --- | --- |
| Year | Year Level (both spellings accepted) |
| Semester | Semester |
| Subject Code | Course Code (both spellings accepted) |
| Description | Course Description (both spellings accepted) |
| Units Lec | Lecture units |
| Units Lab | Laboratory units; blank means zero |
| Pre-requisite | Preserved as course metadata; prerequisite sequencing is not enforced by scheduling conflict checks |
| No Program column | Uses the program selected in the application; preview identifies that program before saving |

The earlier curriculum format remains supported:

```csv
Program,Year Level,Semester,Course Code,Course Description,Lec/Lab,Units
BSMLS,First Year,First Semester,C1,Course,LEC,3
BSMLS,First Year,First Semester,C1,Course,LAB,1
```

The newly supported combined layout is:

```csv
Year,Semester,Subject Code,Description,Units Lec,Units Lab,Pre-requisite
First Year,First Semester,C1,Course,3,1,
```

When both lecture and laboratory units are present, the existing scheduler convention creates separate `LEC` and `LAB` course entries. This explains why the parsed entry counts exceed the source row counts. Decimal units, quoted descriptions, BOM and CRLF files are supported. Invalid semesters, missing required fields, invalid/negative units and malformed quotes produce row errors rather than silently discarding courses. CSV input is limited to 1 MiB per curriculum file; the existing backend limits (100 curricula, 2000 total entries and 256 KiB settings payload) also apply.

## Unit annotations and version differences

The 2023–24 file has `3 (RLE)` laboratory units for MTAC 1/2; the 2026–27 file has `2 (RLE)`. The numeric units and parenthesized notes are retained independently in each curriculum. Existing hour rules are preserved: lecture units count as one hour and laboratory units as three hours in First/Second Semester; the existing Term Break calculation is unchanged. Thus MTAC lab entries calculate to nine hours in the older curriculum and six in the newer one. RLE is retained as a note, not interpreted as a new clinical-hours policy. Review these calculated values against your intended institutional rules before plotting those courses.

## Loading and assigning both versions

1. Select the authorized BSMLS program in the top program selector.
2. Open **File > Load Curricula**, select both files together (or load them separately), review the names and semester counts, then choose **Load and Save**. Existing curricula remain. Success is shown only after the server accepts the complete update; failure keeps the preview for review/retry.
3. Open **File > Manage Curricula** or **Edit > Sections**. Set a default curriculum for each year level, for example the newer version for First Year and the older version for continuing cohorts, as appropriate for your actual implementation.
4. In the section list, assign each section its year level. Its curriculum defaults to the year-level choice; the new curriculum dropdown can override that choice for a specific section. Two sections in the same year can therefore use different curricula.
5. Choose the semester. Course descriptions, units and hours follow the section's assigned curriculum. An explicitly assigned curriculum never falls back to another year when it has no matching courses. Assign a specific curriculum when versions reuse a code with different units; the unassigned All curricula view combines catalogs and cannot identify a cohort for you.

Removing a curriculum clears its year/section assignments. Renaming or removing a section updates its local assignment keys before settings are saved. Curriculum-assigned sections retain their version-specific descriptions and are excluded from the global description-cleanup prompt. Viewing is shared; saving still requires program ownership or administrator access, a current settings version and CSRF validation. The server rejects duplicate curriculum IDs and assignments pointing to missing curricula. No database schema migration is needed; old states default to an empty section-override map.

## Validation performed

- Parsed both actual supplied CSV files directly without modifying them: 81 and 82 entries; MTAC numeric units and RLE notes preserved.
- `npm test`: 16 passed; covers both formats, unit annotations, different curriculum versions, invalid rows, ownership/size checks, section overrides, year filtering, and timetable-import error messages.
- `npm run build`: TypeScript and production Vite build passed.
- `python -m pytest backend/tests -q`: 47 passed, 4 PostgreSQL integration cases skipped because TEST_POSTGRES_URL is unset. Includes multi-curriculum save/read, actor permissions, stale versions, atomic rejection of foreign courses and invalid references, and audit history.
- Browser file-picker verification was stopped because browser upload permission was declined. End-to-end upload of the supplied files through the browser is not claimed as verified. Direct parser and API regression tests passed.

The supplied CSV files are not copied into this repository. Production imports and deployment were not performed.
