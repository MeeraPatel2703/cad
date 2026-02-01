from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class DrawingOut(BaseModel):
    id: uuid.UUID
    filename: str
    upload_date: datetime
    integrity_score: float | None = None
    status: str

    model_config = {"from_attributes": True}


class DrawingDetail(DrawingOut):
    file_path: str
    metadata_json: dict | None = None
    machine_state: dict | None = None
    rfi_json: dict | None = None
    inspection_sheet: dict | None = None


class AuditResultOut(BaseModel):
    id: uuid.UUID
    drawing_id: uuid.UUID
    agent_name: str
    result_type: str
    severity: str
    details: dict | None = None
    coordinates: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditStatusOut(BaseModel):
    drawing_id: uuid.UUID
    status: str
    integrity_score: float | None = None
    findings: list[AuditResultOut] = Field(default_factory=list)


class UploadResponse(BaseModel):
    drawing_id: uuid.UUID
    status: str
    filename: str


class WSMessage(BaseModel):
    agent: str
    type: str  # "thought" | "finding" | "complete" | "error"
    data: dict = Field(default_factory=dict)


# ── Inspection Session schemas ──


class InspectionSessionOut(BaseModel):
    id: uuid.UUID
    master_drawing_id: uuid.UUID
    check_drawing_id: uuid.UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    summary: dict | None = None

    model_config = {"from_attributes": True}


class InspectionSessionDetail(InspectionSessionOut):
    master_drawing: DrawingOut
    check_drawing: DrawingOut | None = None
    comparison_results: dict | None = None


class ComparisonItemOut(BaseModel):
    id: uuid.UUID
    balloon_number: int
    feature_description: str
    zone: str | None = None
    master_nominal: float | None = None
    master_upper_tol: float | None = None
    master_lower_tol: float | None = None
    master_unit: str = "mm"
    master_tolerance_class: str | None = None
    check_actual: float | None = None
    deviation: float | None = None
    status: str
    master_coordinates: dict | None = None
    check_coordinates: dict | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class BalloonData(BaseModel):
    balloon_number: int
    value: float | None = None
    unit: str = "mm"
    coordinates: dict
    tolerance_class: str | None = None
    nominal: float | None = None
    upper_tol: float | None = None
    lower_tol: float | None = None
    status: str = "pending"


class DrawingBalloons(BaseModel):
    drawing_id: uuid.UUID
    balloons: list[BalloonData] = Field(default_factory=list)
