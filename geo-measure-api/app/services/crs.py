"""CRS helpers: labels, transformers and choice of a projected CRS for measuring.

Strategy
--------
Measurements are never computed in degrees. Each geometry is first brought to
WGS84 lon/lat (only used to find *where* it is), then projected to a CRS suited
to the quantity being measured:

* Area   -> Lambert Azimuthal Equal Area centred on the geometry. This projection
            preserves area exactly, wherever on Earth the feature is.
* Length -> The UTM zone containing the geometry's centroid (UPS near the poles).
            UTM is conformal with scale error < ~0.1 % inside a zone.
"""
from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS) -> str:
    """Human-readable identifier, e.g. 'EPSG:4326' (falls back to the CRS name)."""
    epsg = crs.to_epsg(min_confidence=70)
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name or "UNKNOWN"


def is_wgs84(crs: CRS) -> bool:
    return crs.to_epsg(min_confidence=70) == 4326


@lru_cache(maxsize=256)
def get_transformer(src: CRS, dst: CRS) -> Transformer:
    # always_xy -> coordinates are (x=lon/easting, y=lat/northing) regardless of CRS axis order
    return Transformer.from_crs(src, dst, always_xy=True)


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone for a lon/lat (UPS for |lat| >= 84)."""
    if lat >= 84:
        return 32661
    if lat <= -80:
        return 32761
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=1024)
def _utm_crs(epsg: int) -> CRS:
    return CRS.from_epsg(epsg)


@lru_cache(maxsize=1024)
def _laea_crs(lat0: int, lon0: int) -> CRS:
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def length_crs(lon: float, lat: float) -> tuple[CRS, str]:
    epsg = utm_epsg(lon, lat)
    return _utm_crs(epsg), f"EPSG:{epsg}"


def area_crs(lon: float, lat: float) -> tuple[CRS, str]:
    # Centre snapped to a whole degree so CRS objects/transformers can be cached.
    # Equal-area holds for any centre, so this does not affect the area result.
    lat0, lon0 = round(lat), round(lon)
    return _laea_crs(lat0, lon0), f"Lambert Azimuthal Equal Area (lat_0={lat0}, lon_0={lon0})"
