from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field


class FindingType(str, Enum):
    MISMATCH = "MISMATCH"
    OMISSION = "OMISSION"
    PHYSICS_FAIL = "PHYSICS_FAIL"
    DECIMAL_ERROR = "DECIMAL_ERROR"
    STACK_UP_ERROR = "STACK_UP_ERROR"
    TOLERANCE_MISSING = "TOLERANCE_MISSING"
    FIT_ERROR = "FIT_ERROR"
    MATERIAL_ERROR = "MATERIAL_ERROR"


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Zone(BaseModel):
    name: str | None = None
    bounds: dict | None = Field(default_factory=dict)  # {x1, y1, x2, y2}
    features: list[str] | None = Field(default_factory=list)


class Dimension(BaseModel):
    value: float | None = None
    unit: str | None = "mm"
    tolerance_class: str | None = None
    zone: str | None = None
    item_number: str | int | None = None
    coordinates: dict | None = Field(default_factory=dict)  # {x, y}
    nominal: float | None = None
    upper_tol: float | None = None
    lower_tol: float | None = None


class PartListItem(BaseModel):
    item_number: str | None = None
    description: str | None = None
    material: str | None = None
    quantity: int | None = 1
    weight: float | None = None
    weight_unit: str | None = "kg"


class GDTCallout(BaseModel):
    symbol: str | None = None  # e.g. "⌀", "⏥", "⊥"
    value: float | None = None
    datum: str | None = None
    zone: str | None = None
    coordinates: dict | None = Field(default_factory=dict)


class MachineState(BaseModel):
    zones: list[Zone] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    part_list: list[PartListItem] = Field(default_factory=list)
    gdt_callouts: list[GDTCallout] = Field(default_factory=list)
    raw_text: str | list[str] = ""
    title_block: dict = Field(default_factory=dict)


class AuditFinding(BaseModel):
    finding_type: FindingType
    severity: Severity
    description: str
    coordinates: dict = Field(default_factory=dict)
    source_agent: str = ""
    evidence: dict = Field(default_factory=dict)
    item_number: str | None = None
    category: str | None = None  # consensus, envelope, omission, decimal, physics
    zone: str | None = None
    affected_features: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class AuditState(TypedDict, total=False):
    drawing_id: str
    file_path: str
    machine_state: dict | None
    findings: list[dict]
    agent_log: list[dict]
    reflexion_count: int
    status: str
    crop_region: dict | None
    rfi: dict | None
    inspection_sheet: dict | None
    integrity_score: float | None


class ComparisonState(TypedDict, total=False):
    session_id: str
    master_drawing_id: str
    master_file_path: str
    check_drawing_id: str
    check_file_path: str
    master_machine_state: dict | None
    check_machine_state: dict | None
    comparison_items: list[dict]
    findings: list[dict]
    agent_log: list[dict]
    status: str
    master_balloon_data: list[dict]
    check_balloon_data: list[dict]
    summary: dict | None
    rfi: dict | None
