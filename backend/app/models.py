import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Float, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Drawing(Base):
    __tablename__ = "drawings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    upload_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    integrity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="uploaded")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    machine_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rfi_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    inspection_sheet: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    balloon_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    audit_results: Mapped[list["AuditResult"]] = relationship(back_populates="drawing", cascade="all, delete-orphan")


class AuditResult(Base):
    __tablename__ = "audit_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drawing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drawings.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    result_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="info")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    coordinates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    drawing: Mapped["Drawing"] = relationship(back_populates="audit_results")


class InspectionSession(Base):
    __tablename__ = "inspection_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_drawing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drawings.id"), nullable=False)
    check_drawing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("drawings.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="awaiting_check")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    comparison_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    master_drawing: Mapped["Drawing"] = relationship(foreign_keys=[master_drawing_id])
    check_drawing: Mapped["Drawing"] = relationship(foreign_keys=[check_drawing_id])
    comparison_items: Mapped[list["ComparisonItem"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ComparisonItem(Base):
    __tablename__ = "comparison_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inspection_sessions.id"), nullable=False)
    balloon_number: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_description: Mapped[str] = mapped_column(Text, default="")
    zone: Mapped[str | None] = mapped_column(String(200), nullable=True)
    master_nominal: Mapped[float | None] = mapped_column(Float, nullable=True)
    master_upper_tol: Mapped[float | None] = mapped_column(Float, nullable=True)
    master_lower_tol: Mapped[float | None] = mapped_column(Float, nullable=True)
    master_unit: Mapped[str] = mapped_column(String(20), default="mm")
    master_tolerance_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    check_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    master_coordinates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    check_coordinates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["InspectionSession"] = relationship(back_populates="comparison_items")
