"""Measurement calculation for a single geometry."""
import math
from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from app.models import MeasurementStatus
from app.services import crs as crs_utils

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
NO_MEASUREMENT_TYPES = {"Point", "MultiPoint"}


@dataclass
class MeasureResult:
    status: MeasurementStatus
    measurements: dict[str, Any] | None = None
    message: str | None = None
    is_valid: bool = True
    warnings: list[str] = field(default_factory=list)


def _project(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    projected = shp_transform(crs_utils.get_transformer(src, dst).transform, geom)
    minx, miny, maxx, maxy = projected.bounds
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        raise ValueError("Projection produced non-finite coordinates (geometry outside CRS valid area?)")
    return projected


def measure_geometry(geom: BaseGeometry | None, source_crs: CRS) -> MeasureResult:
    """Return the measurement outcome for one geometry. Never raises for bad data."""
    if geom is None:
        return MeasureResult(MeasurementStatus.UNSUPPORTED, message="Missing or unreadable geometry.")
    if geom.is_empty:
        return MeasureResult(MeasurementStatus.UNSUPPORTED, message="Empty geometry.")

    gtype = geom.geom_type
    if gtype in NO_MEASUREMENT_TYPES:
        return MeasureResult(MeasurementStatus.NOT_REQUIRED, message=f"{gtype} has no area or length.")
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return MeasureResult(
            MeasurementStatus.UNSUPPORTED,
            message=f"Measurement is not supported for geometry type {gtype}.",
        )

    try:
        # 1. Work out where the geometry is (lon/lat).
        wgs84 = geom if crs_utils.is_wgs84(source_crs) else _project(geom, source_crs, crs_utils.WGS84)
        is_valid = bool(wgs84.is_valid)
        warnings = [] if is_valid else ["Geometry is invalid (e.g. self-intersection); result may be unreliable."]
        c = wgs84.centroid
        lon, lat = c.x, c.y
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordinates are outside valid longitude/latitude ranges.")

        # 2. Project to a metric CRS and measure.
        if gtype in AREA_TYPES:
            dst, label = crs_utils.area_crs(lon, lat)
            projected = _project(wgs84, crs_utils.WGS84, dst)
            area = projected.area
            values = {
                "area_sq_m": round(area, 4),
                "area_hectares": round(area / 10_000, 6),
                "area_sq_km": round(area / 1_000_000, 8),
                "perimeter_m": round(projected.length, 4),
                "projected_crs": label,
                "method": "Equal-area projection (Lambert Azimuthal Equal Area)",
            }
        else:
            dst, label = crs_utils.length_crs(lon, lat)
            projected = _project(wgs84, crs_utils.WGS84, dst)
            length = projected.length
            values = {
                "length_m": round(length, 4),
                "length_km": round(length / 1000, 6),
                "projected_crs": label,
                "method": "UTM projection (zone of the geometry centroid)",
            }
        return MeasureResult(MeasurementStatus.MEASURED, measurements=values,
                             is_valid=is_valid, warnings=warnings)
    except Exception as exc:  # isolate failures to this feature
        return MeasureResult(MeasurementStatus.ERROR, message=f"Measurement failed: {exc}")
