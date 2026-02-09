from __future__ import annotations

from enum import Enum
from typing import TypedDict, Optional, List, Union, Dict

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
    name: Optional[str] = None
    grid_ref: Optional[str] = None  # Grid reference e.g., "B2-D4"
    bounds: Optional[Dict] = Field(default_factory=dict)  # {x1, y1, x2, y2}
    features: Optional[List[str]] = Field(default_factory=list)


class Dimension(BaseModel):
    value: Optional[float] = None
    unit: Optional[str] = "mm"
    tolerance_class: Optional[str] = None
    zone: Optional[str] = None
    grid_ref: Optional[str] = None  # Grid reference e.g., "C3"
    item_number: Optional[Union[str, int]] = None  # Entity binding to BOM
    coordinates: Optional[Dict] = Field(default_factory=dict)  # {x, y}
    nominal: Optional[float] = None
    upper_tol: Optional[float] = None
    lower_tol: Optional[float] = None
    feature_type: Optional[str] = None  # e.g., "bore_diameter", "shaft_length"
    entity_description: Optional[str] = None  # Description from BOM
    binding_status: Optional[str] = None  # "verified", "unverified", "unbound"


class PartListItem(BaseModel):
    item_number: Optional[Union[str, int]] = None
    description: Optional[str] = None
    material: Optional[str] = None
    quantity: Optional[int] = 1
    weight: Optional[float] = None
    weight_unit: Optional[str] = "kg"


class GDTCallout(BaseModel):
    symbol: Optional[str] = None  # e.g. "⌀", "⏥", "⊥"
    value: Optional[float] = None
    datum: Optional[str] = None
    zone: Optional[str] = None
    grid_ref: Optional[str] = None  # Grid reference e.g., "C3"
    item_number: Optional[Union[str, int]] = None  # Entity binding to BOM
    coordinates: Optional[Dict] = Field(default_factory=dict)


class MachineState(BaseModel):
    zones: List[Zone] = Field(default_factory=list)
    dimensions: List[Dimension] = Field(default_factory=list)
    part_list: List[PartListItem] = Field(default_factory=list)
    gdt_callouts: List[GDTCallout] = Field(default_factory=list)
    raw_text: Union[str, List[str]] = ""
    title_block: Dict = Field(default_factory=dict)


class AuditFinding(BaseModel):
    finding_type: FindingType
    severity: Severity
    description: str
    coordinates: Dict = Field(default_factory=dict)
    source_agent: str = ""
    evidence: Dict = Field(default_factory=dict)
    item_number: Optional[str] = None
    category: Optional[str] = None  # consensus, envelope, omission, decimal, physics
    zone: Optional[str] = None
    affected_features: List[str] = Field(default_factory=list)
    recommendation: Optional[str] = None


class AuditState(TypedDict, total=False):
    drawing_id: str
    file_path: str
    machine_state: Optional[Dict]
    findings: List[Dict]
    agent_log: List[Dict]
    reflexion_count: int
    status: str
    crop_region: Optional[Dict]
    rfi: Optional[Dict]
    inspection_sheet: Optional[Dict]
    integrity_score: Optional[float]


class ComparisonState(TypedDict, total=False):
    session_id: str
    master_drawing_id: str
    master_file_path: str
    check_drawing_id: str
    check_file_path: str
    master_machine_state: Optional[Dict]
    check_machine_state: Optional[Dict]
    comparison_items: List[Dict]
    findings: List[Dict]
    agent_log: List[Dict]
    status: str
    master_balloon_data: List[Dict]
    check_balloon_data: List[Dict]
    summary: Optional[Dict]
    rfi: Optional[Dict]
