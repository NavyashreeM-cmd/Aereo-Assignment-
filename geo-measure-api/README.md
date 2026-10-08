# Geospatial File Measurement API

A FastAPI backend that accepts a **Shapefile (.zip)** or a **KML** file, extracts every feature
(ID, geometry type, geometry, CRS, attributes) and returns **area** (polygons) and **length**
(lines), computed in a projected metric CRS — never in degrees.

## Setup

Requires Python 3.10+. Everything is pure-Python wheels (no GDAL install needed).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

* Interactive docs: http://127.0.0.1:8000/docs
* Run tests: `pip install -r requirements-dev.txt && pytest`
* Docker: `docker build -t geo-api . && docker run -p 8000:8000 geo-api`
* Sample data: `samples/sample.kml`; `python samples/make_sample_shapefile.py` creates a sample zip.

Configuration (environment variables): `DATA_DIR` (default `data`), `DATABASE_URL`
(default SQLite at `data/app.db`), `MAX_UPLOAD_MB` (50), `MAX_UNCOMPRESSED_MB` (300).

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/files/` | Upload & process a `.zip` (Shapefile) or `.kml` (multipart field `file`) |
| GET | `/api/files/{id}/` | File info |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements + totals (`limit`, `offset`, `geometry_type`) |
| GET | `/api/files/{id}/features/` | Per-feature geometry (GeoJSON), CRS and attributes |
| GET | `/api/files/` | List uploaded files |
| DELETE | `/api/files/{id}/` | Delete a file and its data |
| GET | `/health` | Health check |

### Upload

```bash
curl -F "file=@samples/sample.kml" http://127.0.0.1:8000/api/files/
```
```json
{
  "id": "5f0c1e2a9b7d4c3e8a1b2c3d4e5f6a7b",
  "filename": "sample.kml",
  "file_type": "kml",
  "status": "COMPLETED",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "created_at": "2026-10-07T11:00:00Z",
  "error": null,
  "warnings": ["KML altitude values are ignored; measurements are 2D (ground surface)."],
  "summary": {
    "geometry_types": {"Polygon": 1, "LineString": 1, "Point": 1},
    "measurement_status": {"MEASURED": 2, "NOT_REQUIRED": 1},
    "total_area_sq_m": 1217891.43,
    "total_length_m": 1646.7
  }
}
```

Errors: `415` unsupported extension, `400` empty file, `413` too large, `422` unreadable/invalid
file (the body contains the file `id` with `status: FAILED`; the record stays queryable), `404` unknown id,
`409` when asking for features/measurements of a file that is not `COMPLETED`.

### Measurements

`GET /api/files/{id}/measurements/?limit=100&offset=0&geometry_type=Polygon`
```json
{
  "file_id": "5f0c1e2a9b7d4c3e8a1b2c3d4e5f6a7b",
  "count": 3, "limit": 100, "offset": 0,
  "summary": {"total_area_sq_m": 1217891.43, "total_length_m": 1646.7, "...": "..."},
  "results": [
    {
      "index": 0, "geometry_type": "Polygon", "crs": "EPSG:4326",
      "measurement_status": "MEASURED", "is_valid": true, "message": null,
      "measurements": {
        "area_sq_m": 1217891.43, "area_hectares": 121.789143, "area_sq_km": 1.21789143,
        "perimeter_m": 4416.5,
        "projected_crs": "Lambert Azimuthal Equal Area (lat_0=11, lon_0=77)",
        "method": "Equal-area projection (Lambert Azimuthal Equal Area)"
      }
    },
    {
      "index": 1, "geometry_type": "LineString", "crs": "EPSG:4326",
      "measurement_status": "MEASURED", "is_valid": true, "message": null,
      "measurements": {"length_m": 1646.7, "length_km": 1.6467, "projected_crs": "EPSG:32643",
                       "method": "UTM projection (zone of the geometry centroid)"}
    },
    {
      "index": 2, "geometry_type": "Point", "crs": "EPSG:4326",
      "measurement_status": "NOT_REQUIRED", "measurements": null,
      "message": "Point has no area or length.", "is_valid": true
    }
  ]
}
```
(Numbers above are illustrative.) `measurement_status` is one of `MEASURED`, `NOT_REQUIRED` (points),
`UNSUPPORTED` (empty/null geometry, GeometryCollection, …) or `ERROR` (failure isolated to that feature).

## Architecture

```
app/
  main.py            app + lifespan (creates tables)
  config.py          env-based settings
  database.py        SQLAlchemy engine/session
  models.py          UploadedFile, Feature
  schemas.py         Pydantic response models
  api/files.py       HTTP layer only (validation, status codes)
  services/
    readers/         kml.py, shapefile.py  -> common RawFeature/ReadResult
    crs.py           CRS labels, transformer cache, projected-CRS selection
    measurements.py  per-geometry measurement
    processor.py     orchestrates read -> measure -> persist
tests/               pytest suite (API + known-value checks vs. geodesic)
```

**File-processing flow:** upload is streamed to disk with a size cap → extension picks the reader →
(zip is extracted to a temp dir with zip-slip/zip-bomb checks) → reader yields `RawFeature`s + a source CRS →
each feature is measured → features and a file-level summary are saved in one transaction →
status `COMPLETED` (or `FAILED` with an error message).

**Measurement flow (per feature):** null/empty → `UNSUPPORTED`; Point/MultiPoint → `NOT_REQUIRED`;
Polygon/MultiPolygon → area + perimeter; LineString/MultiLineString → length; anything else → `UNSUPPORTED`.
Each feature is wrapped in its own try/except so one bad geometry never fails the file.

**CRS handling:** geometry → WGS84 (to locate it) → projected CRS → measure.
* **Area:** Lambert Azimuthal Equal Area centred on the geometry (equal-area by construction, anywhere on Earth).
* **Length:** the UTM zone of the geometry's centroid (UPS near the poles); distortion is typically < 0.1 %.
* Source CRS comes from KML (always EPSG:4326) or the shapefile `.prj`. Projected inputs (e.g. UTM,
  Web Mercator) are re-projected too, so a Web-Mercator file doesn't give inflated areas.
* Missing `.prj`: assumed EPSG:4326 *only* if coordinates look like lon/lat (with a warning); otherwise rejected.
* Stored geometries stay in the file's original CRS; each feature reports it.

## Design Decisions

* **FastAPI over Django/DRF** – small, I/O-bound service; typed models and automatic OpenAPI docs.
* **Pure-Python geo stack (pyshp + shapely + pyproj + defusedxml) instead of GeoPandas/Fiona/GDAL** – trivial installs
  and Docker image; the formats needed are simple. Trade-off: fewer formats (no KMZ, GeoPackage…).
* **Equal-area for area, UTM for length** instead of one CRS for everything – each quantity uses the
  property-preserving projection. Alternative considered: ellipsoidal geodesic maths (most accurate, but
  the task asks for projected-CRS measurement); tests use geodesic results as the reference.
* **Synchronous processing** – simple, and `status` already models the lifecycle. Large files would move to a queue.
* **Measure at upload, store results** – reads are cheap and consistent; recomputation needs re-upload.
* **SQLite + SQLAlchemy** – zero-setup; swap `DATABASE_URL` for Postgres/PostGIS.
* **Security** – size limit, safe zip extraction, `defusedxml` against XML entity attacks, sanitized filenames.

## Learnings & Future Scope

**Learnings:** KML is always lon/lat/altitude order and namespaces vary between versions; shapefile
`.prj` files are ESRI-flavoured WKT; degrees-based area is the classic trap; per-feature error isolation
matters with messy real data.

**Future scope:** async processing with Celery/RQ and progress; KMZ/GeoJSON/GeoPackage support; PostGIS storage
and spatial queries; geodesic cross-check and per-feature UTM-zone-spanning handling; antimeridian handling;
auth and per-user quotas; GeoJSON export; Alembic migrations; CI and streaming for very large files.
