"""Shapefile reader. Expects a .zip containing .shp/.shx/.dbf (+ optional .prj/.cpg)."""
import json
import zipfile
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import shapefile  # pyshp
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely import force_2d
from shapely.geometry import shape as shapely_shape

from app.config import settings
from app.services.errors import GeoFileError
from app.services.readers.base import RawFeature, ReadResult

WGS84 = CRS.from_epsg(4326)


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract a zip defensively (zip-slip + zip-bomb protection)."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise GeoFileError("Uploaded file is not a valid zip archive.") from exc

    with zf:
        infos = zf.infolist()
        if sum(i.file_size for i in infos) > settings.max_uncompressed_bytes:
            raise GeoFileError("Zip archive is too large when uncompressed.")
        dest_resolved = dest.resolve()
        for info in infos:
            target = (dest / info.filename).resolve()
            if dest_resolved != target and dest_resolved not in target.parents:
                raise GeoFileError("Zip archive contains an unsafe path.")
        zf.extractall(dest)


def _find_shapefile_parts(root: Path) -> tuple[Path, Path | None, Path | None, Path | None, Path | None]:
    shp_files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() == ".shp" and "__MACOSX" not in p.parts
    ]
    if not shp_files:
        raise GeoFileError("Zip archive does not contain a .shp file.")
    if len(shp_files) > 1:
        raise GeoFileError("Zip archive contains more than one shapefile; upload one per zip.")
    shp = shp_files[0]

    def sibling(ext: str) -> Path | None:
        for p in shp.parent.iterdir():
            if p.is_file() and p.stem.lower() == shp.stem.lower() and p.suffix.lower() == ext:
                return p
        return None

    return shp, sibling(".shx"), sibling(".dbf"), sibling(".prj"), sibling(".cpg")


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


def _read_crs(prj: Path | None, sample_bounds: tuple[float, float, float, float] | None,
              warnings: list[str]) -> CRS:
    if prj is not None:
        try:
            return CRS.from_wkt(prj.read_text(encoding="utf-8", errors="replace"))
        except CRSError as exc:
            raise GeoFileError(f"Could not parse the .prj coordinate system: {exc}") from exc

    # No .prj: only guess if the coordinates are plausible lon/lat values.
    if sample_bounds is not None:
        minx, miny, maxx, maxy = sample_bounds
        if -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90:
            warnings.append("No .prj file found; assuming EPSG:4326 because coordinates look like lon/lat.")
            return WGS84
    raise GeoFileError(
        "No .prj file found and the coordinates do not look like lon/lat, so the CRS is unknown."
    )


def read_shapefile_zip(zip_path: Path, workdir: Path) -> ReadResult:
    safe_extract(zip_path, workdir)
    shp, shx, dbf, prj, cpg = _find_shapefile_parts(workdir)
    if dbf is None:
        raise GeoFileError("Shapefile is missing its .dbf attribute table.")

    encoding = "utf-8"
    if cpg is not None:
        cpg_text = cpg.read_text(errors="ignore").strip()
        if cpg_text:
            encoding = cpg_text

    warnings: list[str] = []
    features: list[RawFeature] = []
    bbox = None

    with ExitStack() as stack:
        try:
            reader = shapefile.Reader(
                shp=stack.enter_context(open(shp, "rb")),
                shx=stack.enter_context(open(shx, "rb")) if shx else None,
                dbf=stack.enter_context(open(dbf, "rb")),
                encoding=encoding,
                encodingErrors="replace",
            )
        except Exception as exc:
            raise GeoFileError(f"Could not open shapefile: {exc}") from exc

        try:
            for index, sr in enumerate(reader.iterShapeRecords()):
                props = {k: _json_safe(v) for k, v in sr.record.as_dict().items()}
                if sr.shape.shapeType == shapefile.NULL:
                    features.append(RawFeature(index=index, geometry=None, properties=props))
                    continue
                try:
                    geom = shapely_shape(json.loads(json.dumps(sr.shape.__geo_interface__)))
                    features.append(RawFeature(index=index, geometry=geom, properties=props))
                except Exception as exc:
                    features.append(
                        RawFeature(index=index, geometry=None, properties=props,
                                   error=f"Could not parse geometry: {exc}")
                    )
            bbox = tuple(reader.bbox) if getattr(reader, "bbox", None) else None
        except Exception as exc:
            raise GeoFileError(f"Could not read shapefile records: {exc}") from exc

    if not features:
        raise GeoFileError("Shapefile contains no features.")

    crs = _read_crs(prj, bbox, warnings)
    # Drop Z / M: measurements are 2D.
    for f in features:
        if f.geometry is not None:
            f.geometry = force_2d(f.geometry)
    return ReadResult(features=features, crs=crs, warnings=warnings)
