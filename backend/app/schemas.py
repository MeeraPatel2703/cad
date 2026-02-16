from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class DrawingOut(BaseModel):
    id: uuid.UUID
    filename: str
    upload_date: datetime
    integrity_score: Optional[float] = None
    status: str

    model_config = {"from_attributes": True}


class DrawingDetail(DrawingOut):
    file_path: str
    metadata_json: Optional[Dict] = None
    machine_state: Optional[Dict] = None
    rfi_json: Optional[Dict] = None
    inspection_sheet: Optional[Dict] = None
    balloon_data: Optional[list] = None


class AuditResultOut(BaseModel):
    id: uuid.UUID
    drawing_id: uuid.UUID
    agent_name: str
    result_type: str
    severity: str
    details: Optional[Dict] = None
    coordinates: Optional[Dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditStatusOut(BaseModel):
    drawing_id: uuid.UUID
    status: str
    integrity_score: Optional[float] = None
    findings: List[AuditResultOut] = Field(default_factory=list)


class UploadResponse(BaseModel):
    drawing_id: uuid.UUID
    status: str
    filename: str


class WSMessage(BaseModel):
    agent: str
    type: str  # "thought" | "finding" | "complete" | "error"
    data: Dict = Field(default_factory=dict)


# ── Inspection Session schemas ──


class InspectionSessionOut(BaseModel):
    id: uuid.UUID
    master_drawing_id: uuid.UUID
    check_drawing_id: Optional[uuid.UUID] = None
    status: str
    created_at: datetime
    updated_at: datetime
    summary: Optional[Dict] = None

    model_config = {"from_attributes": True}


class InspectionSessionDetail(InspectionSessionOut):
    master_drawing: DrawingOut
    check_drawing: Optional[DrawingOut] = None
    comparison_results: Optional[Dict] = None
    review_results: Optional[Dict] = None


class ComparisonItemOut(BaseModel):
    id: uuid.UUID
    balloon_number: int
    feature_description: str
    zone: Optional[str] = None
    master_nominal: Optional[float] = None
    master_upper_tol: Optional[float] = None
    master_lower_tol: Optional[float] = None
    master_unit: str = "mm"
    master_tolerance_class: Optional[str] = None
    check_actual: Optional[float] = None
    deviation: Optional[float] = None
    status: str
    master_coordinates: Optional[Dict] = None
    check_coordinates: Optional[Dict] = None
    notes: Optional[str] = None
    highlight_region: Optional[Dict] = None
    check_highlight_region: Optional[Dict] = None
    master_ocr_verified: Optional[bool] = None
    check_ocr_verified: Optional[bool] = None

    model_config = {"from_attributes": True}


class BalloonData(BaseModel):
    balloon_number: int
    value: Optional[float] = None
    unit: Optional[str] = "mm"
    coordinates: Dict
    tolerance_class: Optional[str] = None
    nominal: Optional[float] = None
    upper_tol: Optional[float] = None
    lower_tol: Optional[float] = None
    status: Optional[str] = "pending"


class DrawingBalloons(BaseModel):
    drawing_id: uuid.UUID
    balloons: List[BalloonData] = Field(default_factory=list)
