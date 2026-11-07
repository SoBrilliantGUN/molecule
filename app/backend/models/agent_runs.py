from datetime import date, datetime, timezone
from typing import Any, Optional

from core.database import Base
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class AgentRuns(Base):
    __tablename__ = 'agent_runs'
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, nullable=False, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    intent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checkpoint_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # SSE 事件持久化（run 产生的 stage/dag/verify/done 事件，供订阅端回放 + 断连续收）
    events_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
