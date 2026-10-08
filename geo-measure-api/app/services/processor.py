"""Upload processing pipeline: store -> read -> measure -> persist."""
import logging
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from shapely.geometry import mapping
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.services import crs as crs_utils
from app.services.errors import GeoFileError
from app.services.measurements import measure_geometry
from app.services.readers import ReadResult, read_kml, read_shapefile_zip

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise GeoFileError("Unsupported file type. Upload a .zip (Shapefile) or a .kml file.")
    return SUPPORTED_EXTENSIONS[ext]


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:200] or "upload"


def storage_path_for(file_id: str, filename: str) -> Path:
    directory = settings.upload_dir / file_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / safe_filename(filename)


def delete_stored_file(record: UploadedFile) -> None:
    shutil.rmtree(Path(record.stored_path).parent, ignore_errors=True)


def _read(record: UploadedFile) -> ReadResult:
    path = Path(record.stored_path)
    if record.file_type == "kml":
        return read_kml(path)
    with tempfile.TemporaryDirectory(prefix="shp_") as tmp:
        return read_shapefile_zip(path, Path(tmp))


def process_file(db: Session, record: UploadedFile) -> UploadedFile:
    """Read the stored file, compute measurements, persist features.

    On failure the record is marked FAILED (with an error message) and the
    exception is re-raised so the API layer can pick the right HTTP status.
    """
    try:
        result = _read(record)
        crs_label = crs_utils.crs_label(result.crs)

        counts: Counter = Counter()
        type_counts: Counter = Counter()
        total_area = 0.0
        total_length = 0.0
        rows: list[Feature] = []

        for raw in result.features:
            outcome = measure_geometry(raw.geometry, result.crs)
            if raw.error and outcome.status == MeasurementStatus.UNSUPPORTED:
                outcome.message = raw.error

            geom = raw.geometry
            gtype = geom.geom_type if geom is not None else "Unknown"
            geojson = mapping(geom) if geom is not None and not geom.is_empty else None

            m = outcome.measurements
            if m:
                total_area += m.get("area_sq_m", 0.0)
                total_length += m.get("length_m", 0.0)
            message = outcome.message
            if outcome.warnings:
                message = " ".join(filter(None, [message, *outcome.warnings]))

            counts[outcome.status.value] += 1
            type_counts[gtype] += 1
            rows.append(
                Feature(
                    file_id=record.id,
                    index=raw.index,
                    geometry_type=gtype,
                    geometry=geojson,
                    crs=crs_label,
                    properties=raw.properties,
                    measurement_status=outcome.status.value,
                    measurements=m,
                    message=message,
                    is_valid=outcome.is_valid,
                )
            )

        db.add_all(rows)
        record.crs = crs_label
        record.feature_count = len(rows)
        record.warnings = result.warnings
        record.summary = {
            "geometry_types": dict(type_counts),
            "measurement_status": dict(counts),
            "total_area_sq_m": round(total_area, 4),
            "total_length_m": round(total_length, 4),
        }
        record.status = FileStatus.COMPLETED.value
        record.error = None
        db.commit()
        return record
    except Exception as exc:
        db.rollback()
        record.status = FileStatus.FAILED.value
        record.error = str(exc) if isinstance(exc, GeoFileError) else "Unexpected error while processing file."
        if not isinstance(exc, GeoFileError):
            log.exception("Unexpected failure processing file %s", record.id)
        db.add(record)
        db.commit()
        raise
