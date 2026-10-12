import io

import openpyxl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import models, room_schedule
from app.db import SessionLocal
from app.main import app

URL = '/api/reports/room-schedule.xlsx'
ROOMS = [{'id': 1, 'name': 'R1'}, {'id': 2, 'name': 'R2'}]


def entry(**changes):
    return {'id': 1, 'room_id': 1, 'Course Code': 'C101', 'Section': 'A', 'Program': 'P1',
            'Faculty': 'F1', 'Room': 'R1', 'Days': 'M,W', 'Time (24 Hrs)': '08:00-09:30', **changes}


def build(entries, rooms=ROOMS, **options):
    return openpyxl.load_workbook(io.BytesIO(room_schedule.build_workbook(rooms, entries, '2026 - 2027', **options)))


def download(client, **query):
    response = client.get(URL, params={'school_year': '2026 - 2027', **query})
    assert response.status_code == 200, response.text if response.status_code != 200 else ''
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return openpyxl.load_workbook(io.BytesIO(response.content))


def values(sheet):
    return '\n'.join(str(cell.value) for row in sheet for cell in row if cell.value is not None)


def test_daily_matrices_use_half_hour_rows_and_exact_meeting_spans():
    book = build([entry()])
    assert book.sheetnames == ['ROOM MON', 'ROOM TUE', 'ROOM WED', 'ROOM THU', 'ROOM FRI', 'ROOM SAT']
    mon = book['ROOM MON']
    assert [cell.value for cell in mon[6]] == ['Time', 'R1', 'R2']
    assert mon.max_row == 34  # 28 half-hour slots from 7 AM to 9 PM.
    assert mon['A7'].value == '7:00 AM–7:30 AM' and mon['A34'].value == '8:30 PM–9:00 PM'
    assert 'B9:B11' in set(map(str, mon.merged_cells.ranges))
    assert mon['B9'].value == 'C101\nA (P1)\n8:00 AM–9:30 AM\nF1'
    assert book['ROOM WED']['B9'].value == mon['B9'].value
    assert not any('C101' in str(cell.value) for row in book['ROOM TUE'] for cell in row)
    assert mon.freeze_panes == 'B7' and mon.print_title_cols == '$A:$A'
    assert mon.page_setup.orientation == 'landscape' and mon.page_setup.scale == 80


def test_shared_rooms_include_foreign_programs_and_selected_room_ids(clients, entry_payload):
    assert clients['alpha'].post('/api/schedule', json=entry_payload).status_code == 200
    assert clients['beta'].post('/api/schedule', json={**entry_payload, 'Program': 'P2', 'Course Code': 'FOREIGN',
        'Faculty': 'F2', 'Time (LPU Std)': '10:00a-11:00a'}).status_code == 200
    sheet = download(clients['alpha'], room_ids=[1])['ROOM MON']
    assert sheet.max_column == 2
    assert 'A (P1)' in values(sheet) and 'A (P2)' in values(sheet) and 'FOREIGN' in values(sheet)
    assert 'FOREIGN' not in values(download(clients['alpha'], room_ids=[2])['ROOM MON'])
    # Export is a view operation, not restricted to a program's assigned editor.
    assert 'C101' in values(download(clients['beta'])['ROOM MON'])


def test_section_export_option_adds_complete_room_schedules(clients, entry_payload):
    assert clients['alpha'].post('/api/schedule', json=entry_payload).status_code == 200
    assert clients['beta'].post('/api/schedule', json={**entry_payload, 'Program': 'P2', 'Course Code': 'P2ONLY',
        'Days': 'T', 'Faculty': 'F2'}).status_code == 200
    for scope in ['program', 'sections', 'all']:
        query = {'scope': scope, 'school_year': '2026 - 2027', 'include_rooms': True}
        if scope != 'all':
            query['program_id'] = 1
        if scope == 'sections':
            query['section_ids'] = [1]
        response = clients['alpha'].get('/api/reports/section-schedule.xlsx', params=query)
        assert response.status_code == 200, response.text if response.status_code != 200 else ''
        book = openpyxl.load_workbook(io.BytesIO(response.content))
        assert book.sheetnames[:1] == ['P1']
        assert 'P2ONLY' in values(book['ROOM TUE'])
        assert ('P2' in book.sheetnames) == (scope == 'all')


def test_quarter_hour_meetings_are_preserved_without_false_conflict():
    book = build([entry(**{'Days': 'M', 'Time (24 Hrs)': '07:00-07:15'}),
                  entry(id=2, **{'Days': 'M', 'Course Code': 'NEXT', 'Time (24 Hrs)': '07:15-07:30'})])
    cell = book['ROOM MON']['B7']
    assert 'C101' in cell.value and 'NEXT' in cell.value and '7:15 AM' in cell.value
    assert cell.fill.fgColor.rgb == '00DCE6F1'
    assert 'Room TBA' not in book.sheetnames


def test_true_overlaps_are_visible_and_neither_booking_is_lost():
    book = build([entry(**{'Days': 'M', 'Time (24 Hrs)': '07:00-08:00'}),
                  entry(id=2, **{'Days': 'M', 'Course Code': 'CONFLICT', 'Time (24 Hrs)': '07:30-08:30'})])
    mon = book['ROOM MON']
    assert 'C101' in mon['B7'].value
    assert 'C101' in mon['B8'].value and 'CONFLICT' in mon['B8'].value
    assert mon['B8'].fill.fgColor.rgb == '00FCE4D6'
    assert 'CONFLICT' in mon['B9'].value


def test_boundaries_extend_grid_and_sunday_is_never_dropped():
    book = build([entry(**{'Days': 'Su', 'Time (24 Hrs)': '06:15-06:45'}),
                  entry(id=2, **{'Days': 'M', 'Time (24 Hrs)': '21:15-22:00'})])
    assert 'ROOM SUN' in book.sheetnames
    assert book['ROOM SUN']['A7'].value == '6:00 AM–6:30 AM'
    assert '6:15 AM–6:45 AM' in values(book['ROOM SUN'])
    assert book['ROOM MON'].cell(book['ROOM MON'].max_row, 1).value == '9:30 PM–10:00 PM'
    assert 'ROOM SUN' in build([], include_sunday=True).sheetnames


def test_tba_invalid_or_unavailable_assignments_are_listed_separately():
    book = build([entry(**{'room_id': None, 'Room': 'TBA'}),
                  entry(id=2, **{'Course Code': 'NO TIME', 'Time (24 Hrs)': 'TBA'}),
                  entry(id=3, **{'Course Code': 'NO DAY', 'Days': 'TBA'}),
                  entry(id=4, **{'Course Code': 'INVALID', 'Time (24 Hrs)': '25:00-26:00'})])
    assert book['Room TBA'].max_row == 6
    text = values(book['Room TBA'])
    assert all(word in text for word in ['C101', 'NO TIME', 'NO DAY', 'INVALID'])
    assert 'C101' not in values(book['ROOM MON'])


def test_empty_room_catalog_safe_text_and_sheet_name_collisions():
    assert build([], rooms=[])['ROOM MON']['B7'].value == 'No rooms to export.'
    book = build([entry(**{'Course Code': '=HYPERLINK("bad")'})], institution='=1+1')
    assert book['ROOM MON']['A1'].data_type == 's' and book['ROOM MON']['B9'].data_type == 's'
    book.create_sheet('Room TBA')
    room_schedule.append_sheets(book, ROOMS, [entry(**{'Days': 'TBA'})], '2026')
    assert 'ROOM MON (2)' in book.sheetnames and 'Room TBA (2)' in book.sheetnames


@pytest.mark.parametrize('query', [{'room_ids': [999]}, {'room_ids': [1, 999]}, {'school_year': ' '}, {'title': ' '}])
def test_invalid_export_requests_are_rejected(clients, query):
    assert clients['alpha'].get(URL, params={'school_year': '2026', **query}).status_code == 422


def test_export_requires_login_and_is_read_only(clients, entry_payload):
    with TestClient(app) as anonymous:
        assert anonymous.get(URL, params={'school_year': '2026'}).status_code == 401
    alpha = clients['alpha']
    created = alpha.post('/api/schedule', json=entry_payload).json()
    activity = alpha.get('/api/activity').json()
    download(alpha, institution='Custom institution', college='Custom college', title='Custom rooms')
    assert alpha.get('/api/schedule').json() == [created]
    assert alpha.get('/api/activity').json() == activity


def test_export_limits_prevent_excessively_large_grids():
    with pytest.raises(HTTPException) as error:
        build([], rooms=[{'id': i, 'name': str(i)} for i in range(1600)])
    assert error.value.status_code == 413


def test_lpu_fallback_invalid_clock_minutes_and_midnight_are_handled():
    book = build([entry(**{'Days': 'M', 'Time (24 Hrs)': '', 'Time (LPU Std)': '11:30p-12:00a'}),
                  entry(id=2, **{'Course Code': 'BAD MINUTES', 'Time (24 Hrs)': '08:99-10:00', 'Time (LPU Std)': 'TBA'})])
    # LPU's same-day format cannot express an overnight interval, so use an
    # authoritative 24-hour value ending at midnight for a valid boundary.
    assert 'BAD MINUTES' in values(book['Room TBA'])
    book = build([entry(**{'Days': 'M', 'Time (24 Hrs)': '23:30-24:00'})])
    assert '11:30 PM–12:00 AM' in values(book['ROOM MON'])
    book = build([entry(**{'Days': 'M', 'Time (24 Hrs)': '', 'Time (LPU Std)': '8:15a-9:00a'})])
    assert '8:15 AM–9:00 AM' in values(book['ROOM MON'])


def test_ignored_rooms_and_their_bookings_are_excluded_from_both_export_paths(clients, entry_payload):
    alpha = clients['alpha']
    assert alpha.post('/api/schedule', json={**entry_payload, 'Course Code': 'EXCLUDED'}).status_code == 200
    assert alpha.post('/api/schedule', json={**entry_payload, 'Course Code': 'KEPT', 'Room': 'R2', 'Days': 'T'}).status_code == 200
    assert clients['admin'].put('/api/admin/rules', json={'version': 1, 'ignoreRoom': False,
        'ignoreFaculty': False, 'ignoreRoomIds': [1]}).status_code == 200
    # Explicit old selections cannot bypass the server's exclusion either.
    for query in [{}, {'room_ids': [1, 2]}, {'room_ids': [1]}]:
        book = download(alpha, **query)
        text = '\n'.join(values(sheet) for sheet in book)
        assert 'R1' not in text and 'EXCLUDED' not in text
        assert 'Room TBA' not in book.sheetnames
        if query.get('room_ids') != [1]:
            assert 'KEPT' in text and book['ROOM MON']['B6'].value == 'R2'
    response = alpha.get('/api/reports/section-schedule.xlsx', params={
        'scope': 'program', 'program_id': 1, 'school_year': '2026', 'include_rooms': True})
    assert response.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert 'EXCLUDED' in values(book['P1'])  # Section tables still describe their classes.
    assert 'EXCLUDED' not in '\n'.join(values(sheet) for sheet in book if sheet.title != 'P1')
    assert book['ROOM MON']['B6'].value == 'R2'


def test_global_ignore_switch_does_not_exclude_untagged_rooms(clients, entry_payload):
    assert clients['alpha'].post('/api/schedule', json=entry_payload).status_code == 200
    assert clients['admin'].put('/api/admin/rules', json={'version': 1, 'ignoreRoom': True,
        'ignoreFaculty': False, 'ignoreRoomIds': []}).status_code == 200
    assert 'C101' in values(download(clients['alpha'])['ROOM MON'])
