"""Daily room occupancy matrices using the scheduler's 30-minute time grid."""
import io
import math
from copy import copy
from collections import defaultdict

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

from . import time_utils
from .section_schedule import DEFAULT_COLLEGE, DEFAULT_INSTITUTION, DAYS, DAY_LABELS, natural, sheet_name, write_value, wrapped_height


def clock(minutes):
    return f'{minutes // 60 % 12 or 12}:{minutes % 60:02d} {"AM" if minutes % 1440 < 720 else "PM"}'


def interval(entry):
    try:
        text = entry.get('Time (24 Hrs)') or ''
        start, end = time_utils.parse_time_range(text)
        for index, part in enumerate(text.strip().split('-')):
            hour, minute = map(int, part.split(':'))
            if minute >= 60 or hour >= 24 and not (index == 1 and hour == 24 and minute == 0):
                raise ValueError('Invalid clock time')
    except ValueError:
        try:
            _, _, start, end = time_utils.parse_time_lpu(entry.get('Time (LPU Std)') or '')
        except ValueError:
            return None
    # Legacy imported values must not extend Excel beyond a single calendar day.
    if not 0 <= start < end <= 1440:
        return None
    return start, end


def meeting_days(entry):
    value = entry.get('Days') or ''
    return set() if time_utils.is_tba(value) else time_utils.normalize_days(value)


def label(entry, start, end):
    return '\n'.join([entry.get('Course Code') or 'TBA',
                      f'{entry.get("Section") or "TBA"} ({entry.get("Program") or "TBA"})',
                      f'{clock(start)}–{clock(end)}', entry.get('Faculty') or 'TBA'])


def configure_sheet(sheet, last_column):
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = 'B7'
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = '8'  # A3; room columns continue on additional pages.
    sheet.page_setup.scale = 80
    sheet.sheet_properties.pageSetUpPr.fitToPage = False
    sheet.print_title_rows = '1:6'
    sheet.print_title_cols = 'A:A'
    sheet.page_margins.left = sheet.page_margins.right = 0.25
    sheet.page_margins.top = sheet.page_margins.bottom = 0.25
    sheet.column_dimensions['A'].width = 25
    for column in range(2, last_column + 1):
        sheet.column_dimensions[sheet.cell(6, column).column_letter].width = 25
    # Keep readable text rather than shrink every room onto a single page.
    for column in range(7, last_column, 6):
        sheet.col_breaks.append(Break(id=column))


def append_sheets(workbook, rooms, entries, school_year, institution=DEFAULT_INSTITUTION,
                  college=DEFAULT_COLLEGE, title='ROOM SCHEDULE', include_sunday=False):
    rooms = sorted(rooms, key=lambda room: natural(room['name']))
    room_ids = {room['id'] for room in rooms}
    used = {sheet.title.lower() for sheet in workbook.worksheets}
    bookings, unplotted = [], []
    for entry in entries:
        times = interval(entry)
        days = meeting_days(entry)
        if entry.get('room_id') not in room_ids or not times or not days:
            unplotted.append(entry)
        else:
            bookings.append((entry, *times, days))
    start = min([420] + [start // 30 * 30 for _, start, _, _ in bookings])
    end = max([1260] + [math.ceil(end / 30) * 30 for _, _, end, _ in bookings])
    days = DAYS if include_sunday or any('Su' in item[3] for item in bookings) else DAYS[:-1]
    if len(rooms) * ((end - start) // 30) * len(days) > 250000 or len(entries) > 50000:
        raise HTTPException(413, 'This export is too large. Select fewer rooms.')
    edge = Side(style='thin', color='B8C4D2')
    border = Border(left=edge, right=edge, top=edge, bottom=edge)
    slots = list(range(start, end, 30))
    occupied = defaultdict(list)
    occupancy_count = 0
    for index, (entry, begin, finish, booked_days) in enumerate(bookings):
        occupancy_count += len(booked_days) * (math.ceil(finish / 30) - begin // 30)
        if occupancy_count > 250000:
            raise HTTPException(413, 'This export has too many bookings. Select fewer rooms.')
        for day in booked_days:
            for minute in range(begin // 30 * 30, math.ceil(finish / 30) * 30, 30):
                occupied[day, entry['room_id'], minute].append(index)
    last_column = max(2, len(rooms) + 1)
    for day in days:
        sheet = workbook.create_sheet(sheet_name(f'ROOM {DAY_LABELS[day]}', used))
        configure_sheet(sheet, last_column)
        for row, value in [(1, institution), (2, college), (3, f'{title} — {DAY_LABELS[day]}'),
                           (4, f'SY {school_year} • All programs • 30-minute rows'),
                           (5, 'Exact times are shown. Red = overlapping room bookings. TBA entries are listed separately.')]:
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_column)
            cell = write_value(sheet, row, 1, value)
            cell.font = Font(name='Arial Narrow', size=12 if row != 5 else 10, bold=row != 5)
            cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
            sheet.row_dimensions[row].height = 24 if row != 5 else 30
        for column, value in enumerate(['Time'] + [room['name'] for room in rooms], 1):
            cell = write_value(sheet, 6, column, value)
            cell.font = Font(name='Arial Narrow', size=11, bold=True)
            cell.fill = PatternFill('solid', fgColor='DCE6F1')
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = border
        sheet.row_dimensions[6].height = 30
        for offset, minute in enumerate(slots, 7):
            sheet.row_dimensions[offset].height = 30
            cell = write_value(sheet, offset, 1, f'{clock(minute)}–{clock(minute + 30)}')
            cell.font = Font(name='Arial Narrow', size=10)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.fill = PatternFill('solid', fgColor='F2F5F8')
            cell.border = border
            for column in range(2, last_column + 1):
                sheet.cell(offset, column).border = border
        for column, room in enumerate(rooms, 2):
            offset = 0
            while offset < len(slots):
                indexes = tuple(occupied[day, room['id'], slots[offset]])
                if not indexes:
                    offset += 1
                    continue
                until = offset + 1
                while until < len(slots) and tuple(occupied[day, room['id'], slots[until]]) == indexes:
                    until += 1
                selected = [bookings[index] for index in indexes]
                overlap = any(time_utils.overlap(a[1], a[2], b[1], b[2])
                              for index, a in enumerate(selected) for b in selected[index + 1:])
                text = '\n\n'.join(label(entry, begin, finish) for entry, begin, finish, _ in selected)
                if len(text) > 32767:
                    raise HTTPException(413, f'{room["name"]}: too many bookings in one time block to export safely.')
                first, last = offset + 7, until + 6
                cell = write_value(sheet, first, column, text)
                cell.font = Font(name='Arial Narrow', size=10)
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                cell.fill = PatternFill('solid', fgColor='FCE4D6' if overlap else 'DCE6F1')
                cell.border = border
                # Short or stacked classes need enough height to retain every label.
                height = wrapped_height([text], [25]) / (until - offset)
                for row in range(first, last + 1):
                    sheet.row_dimensions[row].height = max(sheet.row_dimensions[row].height, height)
                    sheet.cell(row, column).fill = copy(cell.fill)
                if first != last:
                    sheet.merge_cells(start_row=first, start_column=column, end_row=last, end_column=column)
                offset = until
        if not rooms:
            write_value(sheet, 7, 2, 'No rooms to export.')
        sheet.print_area = f'A1:{get_column_letter(last_column)}{6 + len(slots)}'
    if unplotted:
        sheet = workbook.create_sheet(sheet_name('Room TBA', used))
        headers = ['Course Code', 'Section', 'Program', 'Time', 'Days', 'Room', 'Faculty', 'Reason']
        widths = [20, 24, 24, 26, 18, 24, 30, 42]
        sheet.freeze_panes = 'A3'
        sheet.merge_cells('A1:H1')
        write_value(sheet, 1, 1, 'Entries not plotted: missing room, day, or valid time').font = Font(bold=True, size=12)
        for column, (header, width) in enumerate(zip(headers, widths), 1):
            cell = write_value(sheet, 2, column, header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill('solid', fgColor='DCE6F1')
            sheet.column_dimensions[cell.column_letter].width = width
        for row, entry in enumerate(unplotted, 3):
            reason = []
            if entry.get('room_id') not in room_ids:
                reason.append('Room not assigned or unavailable')
            if not interval(entry):
                reason.append('Time TBA or invalid')
            if not meeting_days(entry):
                reason.append('Days TBA or invalid')
            values = [entry.get('Course Code'), entry.get('Section'), entry.get('Program'),
                      entry.get('Time (24 Hrs)') or entry.get('Time (LPU Std)') or 'TBA',
                      entry.get('Days') or 'TBA', entry.get('Room') or 'TBA', entry.get('Faculty') or 'TBA', '; '.join(reason)]
            for column, value in enumerate(values, 1):
                cell = write_value(sheet, row, column, value)
                cell.alignment = Alignment(wrap_text=True, vertical='center')
                cell.border = border
            sheet.row_dimensions[row].height = wrapped_height(values, widths)
        sheet.auto_filter.ref = f'A2:H{sheet.max_row}'
        sheet.page_setup.orientation = 'landscape'
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = '1:2'
        sheet.print_area = f'A1:H{sheet.max_row}'


def build_workbook(rooms, entries, school_year, institution=DEFAULT_INSTITUTION,
                   college=DEFAULT_COLLEGE, title='ROOM SCHEDULE', include_sunday=False):
    workbook = Workbook()
    workbook.remove(workbook.active)
    append_sheets(workbook, rooms, entries, school_year, institution, college, title, include_sunday)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
