"""KML reader (pure Python, no GDAL).

KML coordinates are always WGS84 longitude,latitude[,altitude] (EPSG:4326).
Every <Placemark> becomes one feature; <MultiGeometry> becomes a Multi* geometry
(or a GeometryCollection when the members are of mixed types).
"""
from typing import Any

from defusedxml import ElementTree as SafeET
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.services.errors import GeoFileError
from app.services.readers.base import RawFeature, ReadResult

WGS84 = CRS.from_epsg(4326)


def _local(tag: Any) -> str:
    """Tag name without XML namespace (KML 2.0/2.1/2.2 use different namespaces)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _children(el, name: str):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name: str):
    found = _children(el, name)
    return found[0] if found else None


def _text(el, name: str) -> str | None:
    c = _child(el, name)
    if c is not None and c.text and c.text.strip():
        return c.text.strip()
    return None


def _parse_coords(text: str | None) -> list[tuple[float, float]]:
    """Parse a <coordinates> string into [(lon, lat), ...]. Altitude is dropped."""
    if not text:
        return []
    coords = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise GeoFileError(f"Malformed KML coordinate: {token!r}")
        try:
            coords.append((float(parts[0]), float(parts[1])))
        except ValueError as exc:
            raise GeoFileError(f"Malformed KML coordinate: {token!r}") from exc
    return coords


def _ring(el) -> list[tuple[float, float]]:
    ring_el = _child(el, "LinearRing")
    if ring_el is None:
        return []
    return _parse_coords(_text(ring_el, "coordinates"))


def _parse_geometry(el) -> BaseGeometry | None:
    kind = _local(el.tag)

    if kind == "Point":
        coords = _parse_coords(_text(el, "coordinates"))
        return Point(coords[0]) if coords else None

    if kind in ("LineString", "LinearRing"):
        coords = _parse_coords(_text(el, "coordinates"))
        return LineString(coords) if len(coords) >= 2 else None

    if kind == "Polygon":
        outer_el = _child(el, "outerBoundaryIs")
        shell = _ring(outer_el) if outer_el is not None else []
        if len(shell) < 3:
            return None
        holes = [_ring(h) for h in _children(el, "innerBoundaryIs")]
        holes = [h for h in holes if len(h) >= 3]
        return Polygon(shell, holes)

    if kind == "MultiGeometry":
        parts = []
        for child in el:
            if _local(child.tag) in ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"):
                geom = _parse_geometry(child)
                if geom is not None:
                    parts.append(geom)
        if not parts:
            return None
        if all(isinstance(p, Point) for p in parts):
            return MultiPoint(parts)
        if all(isinstance(p, LineString) for p in parts):
            return MultiLineString(parts)
        if all(isinstance(p, Polygon) for p in parts):
            return MultiPolygon(parts)
        return GeometryCollection(parts)

    return None


_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}


def _placemark_geometry(pm) -> BaseGeometry | None:
    for child in pm:
        if _local(child.tag) in _GEOMETRY_TAGS:
            return _parse_geometry(child)
    return None


def _properties(pm) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key in ("name", "description"):
        value = _text(pm, key)
        if value is not None:
            props[key] = value

    ext = _child(pm, "ExtendedData")
    if ext is not None:
        for data in _children(ext, "Data"):
            name = data.get("name")
            if name:
                props[name] = _text(data, "value")
        for schema_data in _children(ext, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                name = simple.get("name")
                if name:
                    props[name] = (simple.text or "").strip() or None
    return props


def read_kml(path) -> ReadResult:
    try:
        tree = SafeET.parse(str(path))
    except Exception as exc:  # malformed XML, forbidden entities, etc.
        raise GeoFileError(f"Invalid KML file: {exc}") from exc

    placemarks = [el for el in tree.getroot().iter() if _local(el.tag) == "Placemark"]
    features: list[RawFeature] = []
    warnings: list[str] = []

    for index, pm in enumerate(placemarks):
        props = _properties(pm)
        try:
            geom = _placemark_geometry(pm)
            features.append(RawFeature(index=index, geometry=geom, properties=props))
        except GeoFileError as exc:
            features.append(RawFeature(index=index, geometry=None, properties=props, error=str(exc)))

    if not features:
        raise GeoFileError("KML file contains no Placemark features.")

    warnings.append("KML altitude values are ignored; measurements are 2D (ground surface).")
    return ReadResult(features=features, crs=WGS84, warnings=warnings)
