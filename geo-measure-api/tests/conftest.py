import io
import os
import tempfile
import zipfile

_TMP = tempfile.mkdtemp(prefix="geo_api_tests_")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"

import pytest  # noqa: E402
import shapefile  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pyproj import CRS  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def make_shapefile_zip(shape_type, shapes, crs: CRS | None, fields=(("name", "C", 40),), records=()):
    """Build an in-memory zipped shapefile. `shapes` is a list of callables(writer)."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type)
    for name, ftype, size in fields:
        w.field(name, ftype, size)
    for i, draw in enumerate(shapes):
        draw(w)
        w.record(*(records[i] if records else (f"feature-{i}",)))
    w.close()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("data.shp", shp.getvalue())
        z.writestr("data.shx", shx.getvalue())
        z.writestr("data.dbf", dbf.getvalue())
        if crs is not None:
            z.writestr("data.prj", crs.to_wkt())
    return out.getvalue()


KML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{body}</Document></kml>"""
