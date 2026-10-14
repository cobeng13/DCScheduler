const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');

let browser, server, baseUrl;
before(async () => {
  const dist = path.resolve(__dirname, '../dist');
  assert.ok(fs.existsSync(path.join(dist, 'index.html')), 'Run npm run build before the browser tests.');
  server = http.createServer((req, res) => {
    const file = path.resolve(dist, '.' + new URL(req.url, 'http://localhost').pathname);
    const safe = file.startsWith(dist + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile();
    const target = safe ? file : path.join(dist, 'index.html');
    const type = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream';
    res.setHeader('Content-Type', type);
    fs.createReadStream(target).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.SCHEDULER_TEST_BROWSER ? { channel: process.env.SCHEDULER_TEST_BROWSER } : {}) });
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

const initialEntries = [
  { id: 1, Program: 'BSCS', Section: 'A', 'Course Code': 'CS101', 'Course Description': 'Introduction', Units: 3, '# of Hours': 2, 'Time (LPU Std)': '7:00a-8:00a', 'Time (24 Hrs)': '07:00-08:00', Days: 'M,W', Room: 'R1', Faculty: 'Ada' },
  { id: 2, Program: 'BSCS', Section: 'B', 'Course Code': 'CS202', 'Course Description': 'Networks', Units: 3, '# of Hours': 1, 'Time (LPU Std)': '9:00a-10:00a', 'Time (24 Hrs)': '09:00-10:00', Days: 'F', Room: 'R2', Faculty: 'Grace' },
];
const names = values => values.map((name, index) => ({ id: index + 1, name }));
const clone = value => structuredClone(value);
const clock = minute => `${Math.floor(minute / 60).toString().padStart(2, '0')}:${(minute % 60).toString().padStart(2, '0')}`;
const lpuClock = minute => `${Math.floor(minute / 60) % 12 || 12}:${(minute % 60).toString().padStart(2, '0')}${minute < 720 ? 'a' : 'p'}`;
const time24 = label => label.split('-').map(part => {
  const [, h, m, ap] = part.match(/(\d+):(\d+)([ap])/);
  return clock((Number(h) % 12 + (ap === 'p' ? 12 : 0)) * 60 + Number(m));
}).join('-');

async function fixture(t, preferences, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let entries = clone(initialEntries).map(entry => ({ ...entry, version: 1, program_id: 1, section_id: entry.Section === 'A' ? 1 : 2 })), snapshots = new Map(), moves = [], excelRequests = [], failedSaves = false, failedMoves = false, failedExports = false;
  entries.push(...clone(options.extraEntries ?? []));
  let catalogs = { '/sections': names(['A', 'B']), '/faculty': names(options.facultyNames ?? ['Ada', 'Grace']), '/rooms': names(['R1', 'R2']) };
  await page.addInitScript(() => { window.EventSource = class { addEventListener() {} close() {} }; localStorage.setItem('scheduler:activity-visible:1', 'false'); });
  if (preferences) await page.addInitScript(value => localStorage.setItem('online:1:1:scheduler.panes', JSON.stringify(value)), preferences);
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    url.pathname = url.pathname.replace(/^\/api/, '');
    if (['/reports/section-schedule.xlsx', '/reports/room-schedule.xlsx'].includes(url.pathname)) {
      excelRequests.push({ ...Object.fromEntries(url.searchParams), path: url.pathname });
      await route.fulfill(failedExports
        ? { status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'Simulated export failure' }) }
        : { contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: Buffer.from('synthetic-xlsx-download') });
      return;
    }
    const data = method === 'GET' || method === 'OPTIONS' ? null : request.postDataJSON();
    let body = {}, status = 200;
    const list = catalogs;
    if (method === 'OPTIONS') body = {};
    else if (url.pathname === '/auth/me') body = { user: { id: 1, username: 'alpha', is_admin: false, disabled: false, must_change_password: false }, csrf_token: 'synthetic-token' };
    else if (url.pathname === '/programs') body = [{ id: 1, name: 'BSCS', assigned_user_id: options.readOnly ? 99 : 1, version: 1 }];
    else if (['/activity', '/activity/actors', '/presence'].includes(url.pathname)) body = [];
    else if (url.pathname === '/presence/heartbeat') body = { ok: true };
    else if (url.pathname === '/rules') body = { rules: { ignoreRoom: false, ignoreFaculty: false, ignoreRoomIds: options.ignoredRoomIds ?? [], ignoreFacultyIds: [] }, version: 1 };
    else if (url.pathname === '/settings') body = { version: 1, settings: { curriculumState: { curricula: [], selectedTerm: 'First Semester', sectionYearLevels: {}, yearLevelCurriculumIds: {} } } };
    else if (url.pathname === '/conflicts') body = { conflicts: options.conflicts ?? [] };
    else if (list[url.pathname]) body = list[url.pathname];
    else if (url.pathname.endsWith('/move-check')) body = { ok: true };
    else if (url.pathname.endsWith('/move/revert')) { entries = clone(snapshots.get(data.move_activity_id)); body = { entries }; }
    else if (url.pathname.endsWith('/move')) {
      moves.push(data);
      if (failedMoves) {
        await route.fulfill({ status: 409, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify({ detail: 'Move blocked by a schedule conflict.' }) });
        return;
      }
      const before = clone(entries), source = entries.find(entry => entry.id === data.expected.id);
      const duration = 60;
      const moved = { ...source, Days: data.destination_day,
        'Time (24 Hrs)': `${clock(data.start_minutes)}-${clock(data.start_minutes + duration)}`,
        'Time (LPU Std)': `${lpuClock(data.start_minutes)}-${lpuClock(data.start_minutes + duration)}` };
      if (data.assignment) moved[data.assignment.kind[0].toUpperCase() + data.assignment.kind.slice(1)] = data.assignment.name;
      const remaining = source.Days.split(',').filter(day => day !== data.source_day);
      if (remaining.length) { source.Days = remaining.join(','); moved.id = 3; entries.push(moved); }
      else Object.assign(source, moved);
      snapshots.set(moves.length, before); body = { entries, moved_entry_id: moved.id, snapshot: { move_activity_id: moves.length } };
    } else if (url.pathname === '/schedule' && method === 'GET') body = url.searchParams.has('program_id') ? entries.filter(entry => entry.program_id === Number(url.searchParams.get('program_id'))) : entries;
    else if (method === 'PUT' && /^\/schedule\/\d+$/.test(url.pathname)) {
      if (failedSaves) { status = 500; body = { detail: 'Simulated save failure' }; }
      else {
        const entry = entries.find(entry => entry.id === Number(url.pathname.split('/')[2]));
        Object.assign(entry, data, { version: entry.version + 1, 'Time (24 Hrs)': time24(data['Time (LPU Std)']) }); body = entry;
      }
    } else if (method === 'DELETE' && /^\/schedule\/\d+$/.test(url.pathname)) {
      entries = entries.filter(entry => entry.id !== Number(url.pathname.split('/')[2])); body = { ok: true };
    } else if (method === 'POST' && url.pathname === '/schedule') {
      body = { ...data, version: 1, program_id: 1, id: Math.max(...entries.map(entry => entry.id), 0) + 1, 'Time (24 Hrs)': time24(data['Time (LPU Std)']) };
      entries.push(body);
    } else throw new Error(`Unexpected test API request: ${method} ${url.pathname}`);
    await route.fulfill({ status, contentType: 'application/json', headers: { 'access-control-allow-origin': '*', 'access-control-allow-methods': '*', 'access-control-allow-headers': '*' }, body: JSON.stringify(body) });
  });
  await page.goto(baseUrl);
  await page.locator('.block-title').first().waitFor();
  await page.getByRole('button', { name: 'Split View', exact: true }).click();
  await page.getByRole('region', { name: 'right schedule' }).waitFor();
  t.after(() => assert.deepEqual(errors, [], 'No browser exceptions'));
  return { page, context, moves, excelRequests, setFailedExport: value => { failedExports = value; }, setFailedSave: value => { failedSaves = value; }, setFailedMove: value => { failedMoves = value; }, entries: () => entries, removeEntity: (kind, name) => { catalogs['/' + kind] = catalogs['/' + kind].filter(entity => entity.name !== name); },
    left: page.getByRole('region', { name: 'left schedule' }), right: page.getByRole('region', { name: 'right schedule' }) };
}

async function waitFor(page, condition, arg) { await page.waitForFunction(condition, arg); }

test('independent tabs and selectors, shared highlighting, popup edit, failure and focus', async t => {
  const { page, left, right, setFailedSave } = await fixture(t);
  assert.equal(await page.locator('.content > aside').count(), 0);
  assert.equal(await left.locator('select').inputValue(), 'A');
  assert.equal(await right.locator('select').inputValue(), 'Ada');
  if (process.env.SCHEDULER_TEST_SCREENSHOTS) {
    const output = path.resolve(__dirname, '../../output');
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, 'split-view.png') });
  }
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  await right.locator('select').selectOption('R2');
  assert.equal(await left.locator('select').inputValue(), 'A');
  await right.getByRole('tab', { name: 'Faculty', exact: true }).click();
  assert.equal(await right.locator('select').inputValue(), 'Ada');
  await left.locator('.block').first().click();
  const dialog = page.getByRole('dialog', { name: 'Edit Class', exact: true });
  await dialog.waitFor();
  if (process.env.SCHEDULER_TEST_SCREENSHOTS) await page.screenshot({ path: path.resolve(__dirname, '../../output/class-editor.png') });
  assert.equal(await left.locator('.block.selected').count(), 2);
  assert.equal(await right.locator('.block.selected').count(), 2);
  await dialog.getByLabel('Faculty', { exact: true }).fill('Grace');
  setFailedSave(true);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Simulated save failure').waitFor();
  assert.equal(await dialog.getByLabel('Faculty', { exact: true }).inputValue(), 'Grace');
  setFailedSave(false);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await right.locator('select').inputValue(), 'Grace');
  assert.equal(await right.locator('.block').count(), 3);
  assert.equal(await left.locator('.block').count(), 2);
  await left.locator('.block').first().click();
  await dialog.waitFor();
  await dialog.getByLabel('Faculty', { exact: true }).fill('Ada');
  page.once('dialog', prompt => prompt.dismiss());
  await page.keyboard.press('Escape');
  assert.ok(await dialog.isVisible());
  page.once('dialog', prompt => prompt.accept());
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
});

test('cross-pane drag reassigns the dragged meeting and Undo restores both panes', async t => {
  const { page, left, right, moves } = await fixture(t);
  await right.locator('select').selectOption('Grace');
  const destination = right.locator('[data-day="T"][data-slot="480"]');
  await left.locator('.block').first().dragTo(destination);
  await waitFor(page, () => [...document.querySelectorAll('[aria-label="right schedule"] .block-title')].some(node => node.textContent === 'CS101'));
  assert.equal(moves.length, 1);
  assert.deepEqual(moves[0].assignment, { kind: 'faculty', name: 'Grace' });
  assert.equal(moves[0].source_day, 'M');
  assert.equal(moves[0].destination_day, 'T');
  assert.equal(moves[0].start_minutes, 480);
  assert.equal(await left.locator('.block').count(), 2);
  await page.getByRole('button', { name: 'Edit ▼', exact: true }).click();
  await page.getByRole('button', { name: /^Undo/ }).click();
  await waitFor(page, () => document.querySelector('[aria-label="right schedule"] .block-title')?.textContent === 'CS202');
  assert.equal(await left.locator('.block').count(), 2);
});

test('linked scrolling aligns times at different zoom levels; horizontal scrolling stays independent', async t => {
  const { page, left, right } = await fixture(t);
  await page.setViewportSize({ width: 1100, height: 1000 });
  for (let index = 0; index < 4; index++) await right.getByRole('button', { name: '+', exact: true }).click();
  await left.locator('.timetable').evaluate(element => { element.scrollTop = 500; element.scrollLeft = 40; });
  await page.waitForFunction(() => {
    const times = [...document.querySelectorAll('.split-timetables .timetable')].map(element => {
      const grid = element.querySelector('.timetable-grid');
      return (element.scrollTop - grid.offsetTop) / parseFloat(getComputedStyle(grid).getPropertyValue('--row-height'));
    });
    return times.length === 2 && Math.abs(times[0] - times[1]) < 0.05;
  });
  assert.equal(await right.locator('.timetable').evaluate(element => element.scrollLeft), 0);
  assert.ok(await left.locator('.timetable').evaluate(element => element.scrollLeft > 0));
  await page.getByLabel('Link scrolling', { exact: true }).uncheck();
  const previous = await right.locator('.timetable').evaluate(element => element.scrollTop);
  await left.locator('.timetable').evaluate(element => { element.scrollTop = 700; });
  await page.waitForTimeout(100);
  assert.equal(await right.locator('.timetable').evaluate(element => element.scrollTop), previous);
});

test('pane preferences survive reload, narrow layout and text view; divider supports keyboard', async t => {
  const { page, left, right } = await fixture(t);
  await left.locator('select').selectOption('B');
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  await right.locator('select').selectOption('R2');
  const divider = page.getByRole('separator');
  await divider.focus(); await page.keyboard.press('ArrowRight');
  assert.ok(Number(await divider.getAttribute('aria-valuenow')) > 50);
  await page.reload();
  await left.locator('.block').waitFor();
  assert.equal(await left.locator('select').inputValue(), 'B');
  assert.equal(await right.locator('select').inputValue(), 'R2');
  await page.setViewportSize({ width: 800, height: 900 });
  await page.getByRole('button', { name: 'Left pane', exact: true }).waitFor();
  assert.equal(await page.locator('.schedule-pane').count(), 1);
  await page.getByRole('button', { name: 'Left pane', exact: true }).click();
  assert.equal(await left.locator('select').inputValue(), 'B');
  await page.getByRole('button', { name: 'Text View', exact: true }).click();
  await page.locator('.text-view').waitFor();
  await page.getByRole('button', { name: 'Timetable: Per Section', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await right.waitFor();
  assert.equal(await right.locator('select').inputValue(), 'R2');
});

test('Add Class uses the active pane, traps focus and synchronizes both schedules', async t => {
  const { page, left, right } = await fixture(t);
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  await right.getByRole('button', { name: 'Add Class', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add Class', exact: true });
  await dialog.waitFor();
  assert.equal(await dialog.getByLabel('Room', { exact: true }).inputValue(), 'R1');
  await dialog.getByLabel('Section', { exact: true }).fill('A');
  await dialog.getByLabel('Course Code', { exact: true }).fill('NEW101');
  await dialog.getByLabel('Time (LPU Std)', { exact: true }).fill('11:00a-12:00p');
  await dialog.getByLabel('Days', { exact: true }).fill('T');
  await dialog.getByLabel('Faculty', { exact: true }).fill('Ada');
  await dialog.getByRole('button', { name: 'Save', exact: true }).focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Close class editor');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await left.getByText('NEW101', { exact: true }).count(), 1);
  assert.equal(await right.getByText('NEW101', { exact: true }).count(), 1);
});

test('current PNG export targets active pane; mass export preserves selections', async t => {
  const { page, left, right } = await fixture(t);
  await right.locator('select').selectOption('Grace');
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export Timetable (Current View)', exact: true }).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), 'timetable_faculty_grace.png');
  assert.equal(await left.locator('select').inputValue(), 'A');
  assert.equal(await right.locator('select').inputValue(), 'Grace');
  const downloads = [];
  page.on('download', download => downloads.push(download.suggestedFilename()));
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Mass Export Timetables ▸', exact: true }).click();
  await page.getByLabel('By Section', { exact: true }).check();
  await page.getByRole('button', { name: 'Start Mass Export', exact: true }).click();
  await page.locator('.export-capture').waitFor({ state: 'hidden', timeout: 30000 });
  assert.deepEqual(downloads.sort(), ['timetable_section_a.png', 'timetable_section_b.png']);
  assert.equal(await left.locator('select').inputValue(), 'A');
  assert.equal(await right.locator('select').inputValue(), 'Grace');
});

test('same entity in both panes stays synchronized on deletion and keyboard Undo', async t => {
  const { page, left, right } = await fixture(t);
  await right.getByRole('tab', { name: 'Section', exact: true }).click();
  await right.locator('select').selectOption('A');
  assert.equal(await right.locator('.block').count(), 2);
  await right.locator('.block').first().click({ button: 'right' });
  page.once('dialog', prompt => prompt.accept());
  await right.getByRole('button', { name: 'Delete', exact: true }).click();
  await waitFor(page, () => document.querySelectorAll('.split-timetables .block').length === 0);
  await page.keyboard.press('Control+z');
  await waitFor(page, () => document.querySelectorAll('.split-timetables .block').length === 4);
  assert.equal(await left.locator('.block').count(), 2);
  assert.equal(await right.locator('.block').count(), 2);
});

test('keyboard copy and paste target the active section pane; selection prefills Add Class', async t => {
  const { page, left, right } = await fixture(t);
  await left.locator('.block').first().focus();
  await page.keyboard.press('Control+c');
  await right.getByRole('tab', { name: 'Section', exact: true }).click();
  await right.locator('select').selectOption('B');
  await right.getByRole('button', { name: 'Add Class', exact: true }).focus();
  await page.keyboard.press('Control+v');
  await waitFor(page, () => document.querySelectorAll('[aria-label="right schedule"] .block').length === 2);
  assert.equal(await left.locator('.block').count(), 2);
  const cell = right.locator('[data-day="T"][data-slot="480"]');
  await cell.click({ button: 'right' });
  await right.locator('.context-menu').getByRole('button', { name: 'Add Class', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add Class', exact: true });
  assert.equal(await dialog.getByLabel('Section', { exact: true }).inputValue(), 'B');
  assert.equal(await dialog.getByLabel('Days', { exact: true }).inputValue(), 'T');
  assert.equal(await dialog.getByLabel('Time (24 Hrs)', { exact: true }).inputValue(), '08:00-08:30');
  await dialog.getByLabel('Course Code', { exact: true }).fill('UNSAVED');
  await page.locator('.class-editor-backdrop').click({ position: { x: 5, y: 5 } });
  assert.ok(await dialog.isVisible());
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.ok(await dialog.isVisible());
});

test('quarter-hour slots, visible preview, time bounds and 420px minimum panes', async t => {
  const { page, left, right, moves } = await fixture(t);
  await page.setViewportSize({ width: 900, height: 1000 });
  assert.ok((await left.boundingBox()).width >= 420);
  assert.ok((await right.boundingBox()).width >= 420);
  await page.getByLabel('15-minute slots', { exact: true }).check();
  await right.locator('select').selectOption('Grace');
  await left.locator('.block').first().dispatchEvent('dragstart');
  await right.locator('[data-day="T"][data-slot="435"]').dispatchEvent('dragover');
  const preview = right.locator('.block.preview');
  await preview.waitFor();
  assert.ok((await preview.textContent()).includes('Grace'));
  assert.notEqual(await preview.evaluate(element => getComputedStyle(element).color), 'rgba(0, 0, 0, 0)');
  await right.locator('[data-day="T"][data-slot="1245"]').dispatchEvent('dragover');
  await right.locator('.timetable-grid').dispatchEvent('drop');
  await page.getByText('Choose a destination and a time between 7 AM and 9 PM.').waitFor();
  assert.equal(moves.length, 0);
  assert.equal(await left.locator('.block:not(.preview)').count(), 2);
});

test('drop onto an occupied class uses that slot and preserves both panes when blocked', async t => {
  const { page, left, right, moves, setFailedMove } = await fixture(t);
  await right.locator('select').selectOption('Grace');
  setFailedMove(true);
  await left.locator('.block').first().dragTo(right.locator('.block').first());
  await page.getByText('Move blocked by a schedule conflict.', { exact: true }).waitFor();
  assert.equal(moves.length, 1);
  assert.equal(moves[0].destination_day, 'F');
  assert.ok(moves[0].start_minutes >= 540 && moves[0].start_minutes < 600);
  assert.equal(await left.locator('.block').count(), 2);
  assert.equal(await right.locator('.block').count(), 1);
  assert.equal(await right.locator('.block-title').textContent(), 'CS202');
});


test('shared room panes include other programs but block their edits and drags', async t => {
  const other = { ...initialEntries[1], id: 10, Program: 'OTHER', program_id: 2, version: 1, Section: 'Other section', Room: 'R1', Days: 'T' };
  const { page, left, right, moves } = await fixture(t, undefined, { extraEntries: [other] });
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  const foreign = right.locator('[data-entry-id="10"]');
  assert.equal(await foreign.count(), 1);
  assert.equal(await left.locator('[data-entry-id="10"]').count(), 0);
  assert.equal(await foreign.getAttribute('draggable'), 'false');
  await foreign.click();
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await foreign.getAttribute('aria-label'), 'View CS202, Other section, T');
  await foreign.click({ button: 'right' });
  assert.ok(await right.getByRole('button', { name: 'Edit', exact: true }).isDisabled());
  assert.ok(await right.getByRole('button', { name: 'Delete', exact: true }).isDisabled());
  assert.equal(moves.length, 0);
});

test('read-only programs keep both panes usable with editing controls disabled', async t => {
  const { page, left, right, moves } = await fixture(t, undefined, { readOnly: true });
  assert.ok(await left.getByRole('button', { name: 'Add Class', exact: true }).isDisabled());
  assert.ok(await right.getByRole('button', { name: 'Add Class', exact: true }).isDisabled());
  await left.locator('.block').first().click();
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await left.locator('.block').first().getAttribute('draggable'), 'false');
  await right.locator('select').selectOption('Grace');
  assert.equal(await right.locator('.block-title').textContent(), 'CS202');
  assert.equal(moves.length, 0);
});

test('removing selected entities resets both panes and empty catalogs disable selectors', async t => {
  const { page, left, right, removeEntity } = await fixture(t);
  await right.getByRole('tab', { name: 'Section', exact: true }).click();
  await left.locator('select').selectOption('B');
  await right.locator('select').selectOption('B');
  removeEntity('sections', 'B');
  await page.evaluate(() => window.dispatchEvent(new Event('scheduler-refresh')));
  await waitFor(page, () => [...document.querySelectorAll('.schedule-pane select')].every(node => node.value === 'A'));
  removeEntity('sections', 'A');
  await page.evaluate(() => window.dispatchEvent(new Event('scheduler-refresh')));
  await left.getByText('No section yet.', { exact: true }).waitFor();
  assert.ok(await left.locator('select').isDisabled());
  assert.ok(await right.locator('select').isDisabled());
});

test('activity panel and viewport resizing suspend split layout until both panes fit', async t => {
  const { page, left, right } = await fixture(t);
  await page.setViewportSize({ width: 1150, height: 1000 });
  await page.getByRole('button', { name: 'Show live updates', exact: true }).click();
  await page.getByRole('button', { name: 'Left pane', exact: true }).waitFor();
  assert.equal(await page.locator('.schedule-pane').count(), 1);
  await page.getByRole('button', { name: 'Hide live updates', exact: true }).click();
  await right.waitFor();
  const divider = page.getByRole('separator');
  await divider.focus();
  for (let index = 0; index < 12; index++) await page.keyboard.press('ArrowRight');
  await page.setViewportSize({ width: 900, height: 1000 });
  await waitFor(page, () => [...document.querySelectorAll('.schedule-pane')].every(node => node.getBoundingClientRect().width >= 420));
  assert.ok((await left.boundingBox()).width >= 420);
  assert.ok((await right.boundingBox()).width >= 420);
});


test('Single View retains its editor and removes redundant catalog creation forms', async t => {
  const { page, left } = await fixture(t);
  await page.getByRole('button', { name: 'Single View', exact: true }).click();
  const editor = page.locator('.content > aside.panel');
  await editor.waitFor();
  assert.equal(await editor.getByRole('heading', { name: 'Add Section', exact: true }).count(), 0);
  assert.equal(await editor.getByRole('heading', { name: 'Add Faculty', exact: true }).count(), 0);
  assert.equal(await editor.getByRole('heading', { name: 'Add Room', exact: true }).count(), 0);
  await left.locator('.block').first().click();
  assert.equal(await editor.getByLabel('Course Code', { exact: true }).inputValue(), 'CS101');
  await editor.getByLabel('Faculty', { exact: true }).fill('Grace');
  await editor.getByRole('button', { name: 'Save Changes to Selected Class', exact: true }).click();
  await waitFor(page, () => document.querySelector('.toast')?.textContent.includes('Class updated'));
  assert.equal(await left.locator('.block').count(), 2);
  assert.equal(await page.getByRole('dialog').count(), 0);
});

test('conflict details include bookings belonging to other programs', async t => {
  const other = { ...initialEntries[0], id: 10, Program: 'OTHER', program_id: 2, version: 1, Section: 'Other section', 'Course Code': 'OTHER101', Days: 'M' };
  const { page, left, right } = await fixture(t, undefined, { extraEntries: [other], conflicts: [{ entry_id: 1, conflicts_with: [10], conflict_type: 'room' }] });
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  assert.equal(await right.locator('[data-entry-id="10"]').count(), 1);
  assert.ok((await page.locator('.ribbon-conflicts').textContent()).includes('OTHER101'));
  assert.equal(await left.locator('.block.conflict').count(), 2);
});


test('copying a shared-room booking pastes into the active program and section', async t => {
  const other = { ...initialEntries[1], id: 10, Program: 'OTHER', program_id: 2, version: 1, Section: 'Other section', 'Course Code': 'OTHER101', Room: 'R1' };
  const { page, left, right, entries } = await fixture(t, undefined, { extraEntries: [other] });
  await right.getByRole('tab', { name: 'Room', exact: true }).click();
  await right.locator('[data-entry-id="10"]').focus();
  await page.keyboard.press('Control+c');
  await left.getByRole('button', { name: 'Add Class', exact: true }).focus();
  await page.keyboard.press('Control+v');
  await left.getByText('OTHER101', { exact: true }).waitFor();
  const pasted = entries().find(entry => entry['Course Code'] === 'OTHER101' && entry.id !== 10);
  assert.equal(pasted.Program, 'BSCS');
  assert.equal(pasted.Section, 'A');
});


test('faculty timetable selectors hide zero-load and other-program teachers', async t => {
  const other = { ...initialEntries[1], id: 10, Program: 'OTHER', program_id: 2, version: 1, Faculty: 'Other program' };
  const { page, left, right } = await fixture(t, undefined, { facultyNames: ['Ada', 'Grace', 'No load', 'Other program'], extraEntries: [other] });
  assert.deepEqual(await right.locator('select option').evaluateAll(options => options.map(option => option.value)), ['Ada', 'Grace']);
  await left.getByRole('tab', { name: 'Faculty', exact: true }).click();
  assert.deepEqual(await left.locator('select option').evaluateAll(options => options.map(option => option.value)), ['Ada', 'Grace']);
  await page.getByRole('button', { name: 'Single View', exact: true }).click();
  assert.deepEqual(await left.locator('select option').evaluateAll(options => options.map(option => option.value)), ['Ada', 'Grace']);
  // Catalog faculty remain assignable when adding a teacher's first class.
  assert.ok(await page.locator('#faculty-list option[value="No load"]').count());
});

test('move notices expire or dismiss without removing Undo', async t => {
  const { page, left, right } = await fixture(t);
  await right.locator('select').selectOption('Grace');
  await left.locator('.block').first().dragTo(right.locator('[data-day="T"][data-slot="480"]'));
  await page.getByText('Class moved', { exact: true }).waitFor();
  await page.getByText('Class moved', { exact: true }).waitFor({ state: 'hidden', timeout: 8000 });
  await page.keyboard.press('Control+z');
  await waitFor(page, () => document.querySelectorAll('[aria-label="left schedule"] .block[data-entry-id="1"]').length === 2);
  await left.locator('.block').first().dragTo(right.locator('[data-day="T"][data-slot="480"]'));
  await page.getByRole('button', { name: 'Dismiss move notice', exact: true }).click();
  assert.equal(await page.getByText('Class moved', { exact: true }).count(), 0);
  await page.keyboard.press('Control+z');
  await waitFor(page, () => document.querySelectorAll('[aria-label="left schedule"] .block[data-entry-id="1"]').length === 2);
});


test('Excel export selects sections or programs and preserves headings on failure', async t => {
  const { page, left, right, excelRequests, setFailedExport } = await fixture(t);
  await left.locator('select').selectOption('B');
  await right.locator('select').selectOption('Grace');
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Export Section Schedule (Excel)', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Export Section Schedule (Excel)', exact: true });
  await dialog.waitFor();
  await dialog.getByLabel('Export scope', { exact: true }).selectOption('sections');
  await dialog.getByLabel('School year', { exact: true }).fill('2026 - 2027');
  await dialog.getByLabel('Report title', { exact: true }).fill('Schedule for review');
  setFailedExport(true);
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(await dialog.getByLabel('Report title', { exact: true }).inputValue(), 'Schedule for review');
  assert.equal(excelRequests[0].scope, 'sections');
  assert.equal(excelRequests[0].program_id, '1');
  assert.equal(excelRequests[0].section_ids, '2');
  setFailedExport(false);
  const downloaded = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  assert.equal((await downloaded).suggestedFilename(), 'BSCS-section-schedules.xlsx');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await left.locator('select').inputValue(), 'B');
  assert.equal(await right.locator('select').inputValue(), 'Grace');
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Export Section Schedule (Excel)', exact: true }).click();
  assert.equal(await dialog.getByLabel('Report title', { exact: true }).inputValue(), 'Schedule for review');
  await dialog.getByLabel('Export scope', { exact: true }).selectOption('all');
  await dialog.getByLabel('Include room schedules (all programs, 30-minute rows)', { exact: true }).check();
  const allDownloaded = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  assert.equal((await allDownloaded).suggestedFilename(), 'all-program-schedules.xlsx');
  assert.equal(excelRequests.at(-1).scope, 'all');
  assert.equal(excelRequests.at(-1).program_id, undefined);
  assert.equal(excelRequests.at(-1).include_rooms, 'true');
});

test('read-only users can export a program and Escape closes the export dialog', async t => {
  const { page, excelRequests } = await fixture(t, undefined, { readOnly: true });
  const open = async () => {
    await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
    await page.getByRole('button', { name: 'Export Section Schedule (Excel)', exact: true }).click();
  };
  await open();
  const dialog = page.getByRole('dialog', { name: 'Export Section Schedule (Excel)', exact: true });
  await dialog.waitFor();
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  await open();
  const downloaded = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  await downloaded;
  assert.equal(excelRequests[0].scope, 'program');
});

test('room Excel export supports room selection, failed drafts and read-only users', async t => {
  const { page, left, right, excelRequests, setFailedExport } = await fixture(t, undefined, { readOnly: true, ignoredRoomIds: [2] });
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Export Room Schedule (Excel)', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Export Room Schedule (Excel)', exact: true });
  await dialog.waitFor();
  assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true);
  await dialog.getByLabel('Rooms', { exact: true }).selectOption('selected');
  assert.equal(await dialog.getByLabel('R2', { exact: true }).count(), 0);
  assert.equal(await dialog.getByLabel('R1', { exact: true }).isChecked(), true);
  await dialog.getByLabel('Report title', { exact: true }).fill('Room allocation');
  setFailedExport(true);
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(await dialog.getByLabel('Report title', { exact: true }).inputValue(), 'Room allocation');
  setFailedExport(false);
  const downloaded = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download Excel', exact: true }).click();
  assert.equal((await downloaded).suggestedFilename(), 'room-schedules.xlsx');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(excelRequests.at(-1).path, '/reports/room-schedule.xlsx');
  assert.equal(excelRequests.at(-1).room_ids, '1');
  assert.equal(excelRequests.at(-1).program_id, undefined);
  assert.equal(await left.locator('select').inputValue(), 'A');
  assert.equal(await right.locator('select').inputValue(), 'Ada');
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Export Room Schedule (Excel)', exact: true }).click();
  assert.equal(await dialog.getByLabel('Report title', { exact: true }).inputValue(), 'Room allocation');
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
});

test('course panes group sections, show TBA, preserve selection and prefill additions', async t => {
  const extraEntries = [
    { ...initialEntries[0], id: 10, version: 1, program_id: 1, Section: 'B', 'Course Code': ' cs101 ', Days: 'M' },
    { ...initialEntries[0], id: 11, version: 1, program_id: 1, Section: 'B', Days: 'TBA', 'Time (24 Hrs)': null, 'Time (LPU Std)': 'TBA' },
    { ...initialEntries[0], id: 12, version: 1, program_id: 2, Program: 'OTHER', 'Course Code': 'FOREIGN' },
  ];
  const { page, left, right } = await fixture(t, undefined, { extraEntries });
  await right.getByRole('tab', { name: 'Course', exact: true }).click();
  assert.equal(await right.getByLabel('Select course').inputValue(), 'CS101');
  assert.equal(await right.locator('.block').count(), 3);
  assert.equal(await right.locator('.course-meetings button').count(), 3);
  assert.match(await right.locator('.course-meetings').innerText(), /TBA/);
  assert.equal(await right.locator('option[value="FOREIGN"]').count(), 0);
  const blocks = await right.locator('.block').evaluateAll(nodes => nodes.map(node => ({ id: node.dataset.entryId, rect: node.getBoundingClientRect().toJSON() })));
  const first = blocks.find(block => block.id === '1');
  const second = blocks.find(block => block.id === '10');
  assert.ok(first.rect.right <= second.rect.left || second.rect.right <= first.rect.left, 'Overlapping sections remain side by side');
  if (process.env.SCHEDULER_TEST_SCREENSHOTS) await page.screenshot({ path: path.resolve(__dirname, '../../output/course-split-view.png') });
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export ▼', exact: true }).click();
  await page.getByRole('button', { name: 'Export Timetable (Current View)', exact: true }).click();
  assert.match((await downloadPromise).suggestedFilename(), /timetable_course_cs101\.png/);
  await right.getByLabel('Select course').selectOption('CS202');
  assert.equal(await left.getByLabel('Select section').inputValue(), 'A');
  await page.reload();
  await right.getByLabel('Select course').waitFor();
  await waitFor(page, () => document.querySelector('[aria-label="right schedule"] [aria-label="Select course"]')?.value === 'CS202');
  assert.equal(await right.getByLabel('Select course').inputValue(), 'CS202');
  await right.getByRole('button', { name: 'Add Class', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add Class', exact: true });
  assert.equal(await dialog.getByLabel('Course Code', { exact: true }).inputValue(), 'CS202');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Single View', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Timetable: Per Course', exact: true }).getAttribute('class'), 'active');
  if (process.env.SCHEDULER_TEST_SCREENSHOTS) await page.screenshot({ path: path.resolve(__dirname, '../../output/course-view.png') });
});

test('moving into a course pane preserves the code and rejects mismatched courses', async t => {
  const { page, left, right, moves } = await fixture(t);
  await right.getByRole('tab', { name: 'Course', exact: true }).click();
  await left.locator('.block').first().dragTo(right.locator('[data-day="T"][data-slot="480"]'));
  await waitFor(page, () => document.querySelector('[data-entry-id="3"]'));
  assert.equal(moves.length, 1);
  assert.equal(moves[0].assignment, null);
  await right.getByLabel('Select course').selectOption('CS202');
  await left.locator('.block').first().dragTo(right.locator('[data-day="T"][data-slot="540"]'));
  assert.equal(moves.length, 1);
});

test('all-program course view includes shared codes and remains read-only', async t => {
  const { page } = await fixture(t, undefined, { extraEntries: [
    { ...initialEntries[0], id: 20, version: 1, program_id: 2, Program: 'OTHER', Section: 'Other A' },
  ] });
  await page.getByRole('combobox', { name: 'Program', exact: true }).selectOption('');
  await page.getByRole('button', { name: 'Timetable: Per Course', exact: true }).click();
  const pane = page.getByRole('region', { name: 'left schedule' });
  await pane.getByLabel('Select course').waitFor();
  assert.equal(await pane.locator('.block').count(), 4);
  assert.match(await pane.locator('.course-meetings').innerText(), /OTHER/);
  assert.equal(await pane.locator('.block[draggable="true"]').count(), 0);
  await pane.locator('.course-meetings button').first().click();
  assert.equal(await page.getByRole('dialog', { name: 'Edit Class', exact: true }).count(), 0);
});
