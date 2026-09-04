"""SQLAlchemy models for SBA data — leads, meetings, handoffs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from admin.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeadModel(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    business_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(64), default="manual")
    score: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(32), default="new")
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    meeting_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(64), default=lambda: _now().isoformat())
    updated_at: Mapped[str] = mapped_column(String(64), default=lambda: _now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "business_name": self.business_name,
            "email": self.email,
            "phone": self.phone,
            "source": self.source,
            "score": self.score,
            "status": self.status,
            "notes": self.notes or [],
            "meeting_ids": self.meeting_ids or [],
            "context": self.context or {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MeetingModel(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    title: Mapped[str] = mapped_column(String(255), default="Meeting")
    lead_name: Mapped[str] = mapped_column(String(255), default="")
    link: Mapped[str] = mapped_column(String(512), default="")
    date: Mapped[str] = mapped_column(String(32), default="")
    time: Mapped[str] = mapped_column(String(32), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    transcript: Mapped[str] = mapped_column(Text, default="")
    transcript_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    action_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    lead_response: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(64), default=lambda: _now().isoformat())
    updated_at: Mapped[str] = mapped_column(String(64), default=lambda: _now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "title": self.title,
            "lead_name": self.lead_name,
            "link": self.link,
            "date": self.date,
            "time": self.time,
            "duration_minutes": self.duration_minutes,
            "status": self.status,
            "notes": self.notes or [],
            "transcript": self.transcript,
            "transcript_analysis": self.transcript_analysis,
            "action_items": self.action_items or [],
            "summary": self.summary,
            "lead_response": self.lead_response,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class HandoffModel(Base):
    __tablename__ = "handoffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    handed_off_to_ceo: Mapped[bool] = mapped_column(default=True)
    handoff_time: Mapped[str] = mapped_column(String(64), default=lambda: _now().isoformat())
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    full_dump: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ceo_message: Mapped[str] = mapped_column(Text, default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "handed_off_to_ceo": self.handed_off_to_ceo,
            "handoff_time": self.handoff_time,
            "workspace_id": self.workspace_id,
            "brief": self.brief or {},
            "full_dump": self.full_dump or {},
            "ceo_message": self.ceo_message,
        }
