"""Database models."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FileStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(str, enum.Enum):
    MEASURED = "MEASURED"          # area / length computed
    NOT_REQUIRED = "NOT_REQUIRED"  # e.g. points
    UNSUPPORTED = "UNSUPPORTED"    # unsupported or empty geometry
    ERROR = "ERROR"                # unexpected failure on this feature only


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "shapefile" | "kml"
    status: Mapped[str] = mapped_column(String(16), default=FileStatus.PROCESSING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    stored_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    geometry_type: Mapped[str] = mapped_column(String(32))
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, source CRS
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    measurement_status: Mapped[str] = mapped_column(String(16))
    measurements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
