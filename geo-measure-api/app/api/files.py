"""File upload / retrieval endpoints."""
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Feature, FileStatus, UploadedFile
from app.schemas import FeatureList, FileList, FileOut, MeasurementList
from app.services import processor
from app.services.errors import GeoFileError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

CHUNK = 1024 * 1024


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status != FileStatus.COMPLETED.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File is not ready (status={record.status}"
            + (f", error={record.error}" if record.error else "")
            + ").",
        )


@router.post("/", response_model=FileOut, status_code=status.HTTP_201_CREATED)
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a `.zip` (Shapefile) or `.kml` file; it is processed synchronously."""
    filename = file.filename or ""
    try:
        file_type = processor.detect_file_type(filename)
    except GeoFileError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    record = UploadedFile(id=uuid.uuid4().hex, filename=processor.safe_filename(filename), file_type=file_type, stored_path="")
    stored = processor.storage_path_for(record.id, filename)
    record.stored_path = str(stored)

    # Stream to disk with a hard size limit.
    written = 0
    try:
        with open(stored, "wb") as out:
            while chunk := file.file.read(CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
                    )
                out.write(chunk)
    except HTTPException:
        processor.delete_stored_file(record)
        raise
    if written == 0:
        processor.delete_stored_file(record)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty.")

    db.add(record)
    db.commit()

    try:
        processor.process_file(db, record)
    except GeoFileError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"id": record.id, "status": FileStatus.FAILED.value, "error": str(exc)},
        )
    except Exception:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"id": record.id, "status": FileStatus.FAILED.value, "error": record.error},
        )
    return record


@router.get("/", response_model=FileList)
def list_files(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(UploadedFile)) or 0
    rows = db.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return FileList(count=total, limit=limit, offset=offset, results=rows)


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.delete("/{file_id}/", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, db: Session = Depends(get_db)):
    record = _get_file_or_404(db, file_id)
    processor.delete_stored_file(record)
    db.delete(record)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{file_id}/features/", response_model=FeatureList)
def list_features(file_id: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                  geometry_type: str | None = None, db: Session = Depends(get_db)):
    """Features with geometry (GeoJSON, in the file's CRS) and attributes."""
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    q = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        q = q.where(Feature.geometry_type == geometry_type)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Feature.index).limit(limit).offset(offset)).all()
    return FeatureList(file_id=file_id, count=total, limit=limit, offset=offset, results=rows)


@router.get("/{file_id}/measurements/", response_model=MeasurementList)
def list_measurements(file_id: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                      geometry_type: str | None = None, db: Session = Depends(get_db)):
    """Per-feature area / length, plus file-level totals."""
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    q = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        q = q.where(Feature.geometry_type == geometry_type)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Feature.index).limit(limit).offset(offset)).all()
    return MeasurementList(
        file_id=file_id, summary=record.summary, count=total, limit=limit, offset=offset, results=rows
    )
