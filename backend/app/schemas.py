from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    program: str = Field(..., alias="Program")
    section: str = Field(..., alias="Section")
    course_code: str = Field(..., alias="Course Code")
    course_description: str = Field(..., alias="Course Description")
    units: float = Field(..., alias="Units", ge=0, allow_inf_nan=False)
    hours: float = Field(..., alias="# of Hours", ge=0, allow_inf_nan=False)
    time_lpu: str = Field(..., alias="Time (LPU Std)")
    time_24: Optional[str] = Field("", alias="Time (24 Hrs)")
    days: str = Field(..., alias="Days")
    room: str = Field(..., alias="Room")
    faculty: str = Field(..., alias="Faculty")

    model_config = ConfigDict(populate_by_name=True)


class ScheduleEntryCreate(ScheduleEntryBase):
    pass


class ScheduleEntryUpdate(ScheduleEntryBase):
    version: int = Field(..., ge=1)


class ScheduleEntry(ScheduleEntryBase):
    id: int
    program_id: int
    section_id: int
    room_id: Optional[int] = None
    faculty_id: Optional[int] = None
    version: int

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NamedEntity(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class NamedEntityCreate(BaseModel):
    name: str


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
