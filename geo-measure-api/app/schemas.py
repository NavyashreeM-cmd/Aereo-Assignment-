"""Pydantic response models."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    status: str
    feature_count: int
    crs: str | None
    created_at: datetime
    error: str | None = None
    warnings: list[str] = []
    summary: dict[str, Any] = {}


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    geometry_type: str
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    geometry_type: str
    crs: str | None
    measurement_status: str
    measurements: dict[str, Any] | None
    message: str | None
    is_valid: bool


class Page(BaseModel):
    count: int
    limit: int
    offset: int


class FeatureList(Page):
    file_id: str
    results: list[FeatureOut]


class MeasurementList(Page):
    file_id: str
    summary: dict[str, Any]
    results: list[MeasurementOut]


class FileList(Page):
    results: list[FileOut]
