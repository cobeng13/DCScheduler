from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CANONICAL_HEADERS = [
    "Program",
    "Section",
    "Course Code",
    "Course Description",
    "Units",
    "# of Hours",
    "Time (LPU Std)",
    "Time (24 Hrs)",
    "Days",
    "Room",
    "Faculty",
]


class ScheduleEntryBase(BaseModel):
    program: str = Field(..., alias="Program", max_length=200)
    section: str = Field(..., alias="Section", max_length=200)
    course_code: str = Field(..., alias="Course Code", max_length=100)
    course_description: str = Field(..., alias="Course Description", max_length=2000)
    units: float = Field(..., alias="Units", ge=0, allow_inf_nan=False)
    hours: float = Field(..., alias="# of Hours", ge=0, allow_inf_nan=False)
    time_lpu: str = Field(..., alias="Time (LPU Std)", max_length=100)
    time_24: Optional[str] = Field("", alias="Time (24 Hrs)", max_length=100)
    days: str = Field(..., alias="Days", max_length=64)
    room: str = Field(..., alias="Room", max_length=200)
    faculty: str = Field(..., alias="Faculty", max_length=200)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("*", mode="after")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Text cannot contain null characters")
        return value


class ScheduleEntryCreate(ScheduleEntryBase):
    pass


class ScheduleEntryUpdate(ScheduleEntryBase):
    version: int = Field(..., ge=1)


class ScheduleEntry(ScheduleEntryBase):
    # Existing rows remain readable even if they predate the write limits.
    # Editing them requires bringing the fields within the current limits.
    program: str = Field(alias="Program")
    section: str = Field(alias="Section")
    course_code: str = Field(alias="Course Code")
    course_description: str = Field(alias="Course Description")
    time_lpu: str = Field(alias="Time (LPU Std)")
    time_24: Optional[str] = Field(default="", alias="Time (24 Hrs)")
    days: str = Field(alias="Days")
    room: str = Field(alias="Room")
    faculty: str = Field(alias="Faculty")
    id: int
    program_id: int
    section_id: int
    room_id: Optional[int] = None
    faculty_id: Optional[int] = None
    version: int

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class MoveAssignment(BaseModel):
    kind: Literal["section", "faculty", "room"]
    name: str = Field(min_length=1, max_length=200)


class MeetingMove(BaseModel):
    source_day: str
    destination_day: str
    start_minutes: int = Field(strict=True)
    assignment: Optional[MoveAssignment] = None
    expected: ScheduleEntry


class MeetingMoveSnapshot(BaseModel):
    move_activity_id: int = Field(ge=1)


class MeetingMoveResult(BaseModel):
    entries: List[ScheduleEntry]
    snapshot: Optional[MeetingMoveSnapshot]
    moved_entry_id: int


class NamedEntity(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class NamedEntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ConflictSummary(BaseModel):
    entry_id: int
    conflicts_with: List[int]
    conflict_type: str


class ConflictReport(BaseModel):
    conflicts: List[ConflictSummary]


class SelectionRequest(BaseModel):
    ids: Optional[List[int]] = None


class AppSettingsPayload(BaseModel):
    settings: Dict[str, Any]
