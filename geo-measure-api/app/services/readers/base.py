"""Common types returned by every file reader."""
from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


@dataclass
class RawFeature:
    index: int
    geometry: BaseGeometry | None  # None for null / unreadable geometries
    properties: dict[str, Any] = field(default_factory=dict)
    error: str | None = None       # set when the geometry could not be parsed


@dataclass
class ReadResult:
    features: list[RawFeature]
    crs: CRS
    warnings: list[str] = field(default_factory=list)
