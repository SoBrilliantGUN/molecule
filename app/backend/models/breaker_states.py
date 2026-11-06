from datetime import date, datetime, timezone
from typing import Any, Optional

from core.database import Base
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class BreakerStates(Base):
    __tablename__ = 'breaker_states'
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, nullable=False, autoincrement=True)
    breaker_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    failures: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
