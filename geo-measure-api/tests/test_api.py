import shapefile
from pyproj import CRS, Geod

from tests.conftest import KML_TEMPLATE, make_shapefile_zip

GEOD = Geod(ellps="WGS84")


def upload(client, name, content, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, content, ctype)})


def rel_close(a, b, tol=1e-3):
    return abs(a - b) <= tol * abs(b)


# ---------------------------------------------------------------- KML
KML_BODY = """
<Placemark><name>Plot A</name>
  <ExtendedData><Data name="owner"><value>Ravi</value></Data></ExtendedData>
  <Polygon><outerBoundaryIs><LinearRing><coordinates>
    77.00,11.00,0 77.01,11.00,0 77.01,11.01,0 77.00,11.01,0 77.00,11.00,0
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Road</name>
  <LineString><coordinates>77.00,11.00 77.00,11.10</coordinates></LineString></Placemark>
<Placemark><name>Well</name><Point><coordinates>77.005,11.005</coordinates></Point></Placemark>
<Placemark><name>Mixed</name><MultiGeometry>
  <Point><coordinates>77.0,11.0</coordinates></Point>
  <LineString><coordinates>77.0,11.0 77.1,11.0</coordinates></LineString>
</MultiGeometry></Placemark>
<Placemark><name>No geometry</name></Placemark>
"""


def test_kml_upload_and_measurements(client):
    r = upload(client, "survey.kml", KML_TEMPLATE.format(body=KML_BODY).encode())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 5
    assert body["crs"] == "EPSG:4326"
    assert body["file_type"] == "kml"
    fid = body["id"]

    info = client.get(f"/api/files/{fid}/").json()
    assert info["filename"] == "survey.kml" and info["status"] == "COMPLETED"

    feats = client.get(f"/api/files/{fid}/features/").json()
    assert feats["count"] == 5
    poly = feats["results"][0]
    assert poly["geometry_type"] == "Polygon"
    assert poly["properties"]["name"] == "Plot A"
    assert poly["properties"]["owner"] == "Ravi"
    assert poly["crs"] == "EPSG:4326"

    m = client.get(f"/api/files/{fid}/measurements/").json()
    res = {x["index"]: x for x in m["results"]}

    # Polygon area vs geodesic reference
    lons, lats = [77.0, 77.01, 77.01, 77.0], [11.0, 11.0, 11.01, 11.01]
    geo_area, _ = GEOD.polygon_area_perimeter(lons, lats)
    assert res[0]["measurement_status"] == "MEASURED"
    assert rel_close(res[0]["measurements"]["area_sq_m"], abs(geo_area))

    # Line length vs geodesic reference
    geo_len = GEOD.line_length([77.0, 77.0], [11.0, 11.1])
    assert rel_close(res[1]["measurements"]["length_m"], geo_len)
    assert res[1]["measurements"]["projected_crs"] == "EPSG:32643"

    assert res[2]["measurement_status"] == "NOT_REQUIRED"        # point
    assert res[3]["geometry_type"] == "GeometryCollection"
    assert res[3]["measurement_status"] == "UNSUPPORTED"          # handled gracefully
    assert res[4]["measurement_status"] == "UNSUPPORTED"          # no geometry
    assert m["summary"]["measurement_status"]["MEASURED"] == 2


def test_degrees_are_not_used_for_area(client):
    """0.01 x 0.01 degree square must be ~1.2 km^2 (~120 ha), never 0.0001."""
    r = upload(client, "a.kml", KML_TEMPLATE.format(body=KML_BODY).encode())
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/?geometry_type=Polygon").json()
    assert m["count"] == 1
    assert 1_000_000 < m["results"][0]["measurements"]["area_sq_m"] < 1_400_000


def test_invalid_kml(client):
    r = upload(client, "bad.kml", b"<kml><Document><Placemark></kml>")
    assert r.status_code == 422
    assert r.json()["detail"]["status"] == "FAILED"
    # failed file is still queryable
    fid = r.json()["detail"]["id"]
    assert client.get(f"/api/files/{fid}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{fid}/measurements/").status_code == 409


def test_kml_with_no_placemarks(client):
    r = upload(client, "empty.kml", KML_TEMPLATE.format(body="").encode())
    assert r.status_code == 422


# ---------------------------------------------------------------- Shapefile
def test_shapefile_wgs84_polygon(client):
    def draw(w):
        w.poly([[[77.0, 11.0], [77.0, 11.01], [77.01, 11.01], [77.01, 11.0], [77.0, 11.0]]])

    data = make_shapefile_zip(shapefile.POLYGON, [draw], CRS.from_epsg(4326))
    r = upload(client, "plots.zip", data)
    assert r.status_code == 201, r.text
    assert r.json()["crs"] == "EPSG:4326"
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()
    geo_area, _ = GEOD.polygon_area_perimeter([77.0, 77.0, 77.01, 77.01], [11.0, 11.01, 11.01, 11.0])
    assert rel_close(m["results"][0]["measurements"]["area_sq_m"], abs(geo_area))


def test_shapefile_projected_utm_polygon(client):
    """100 m x 100 m square in UTM 43N -> ~10,000 m^2."""
    def draw(w):
        w.poly([[[500000, 1200000], [500000, 1200100], [500100, 1200100], [500100, 1200000],
                 [500000, 1200000]]])

    data = make_shapefile_zip(shapefile.POLYGON, [draw], CRS.from_epsg(32643))
    r = upload(client, "utm.zip", data)
    assert r.status_code == 201, r.text
    assert r.json()["crs"] == "EPSG:32643"
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["results"][0]["measurements"]
    assert rel_close(m["area_sq_m"], 10_000.0)
    assert rel_close(m["perimeter_m"], 400.0)


def test_shapefile_lines_and_points(client):
    lines = make_shapefile_zip(
        shapefile.POLYLINE,
        [lambda w: w.line([[[77.0, 11.0], [77.0, 11.1]]])],
        CRS.from_epsg(4326),
    )
    r = upload(client, "lines.zip", lines)
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["results"][0]
    assert rel_close(m["measurements"]["length_m"], GEOD.line_length([77.0, 77.0], [11.0, 11.1]))

    points = make_shapefile_zip(shapefile.POINT, [lambda w: w.point(77.0, 11.0)], CRS.from_epsg(4326))
    r = upload(client, "pts.zip", points)
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["results"][0]
    assert m["measurement_status"] == "NOT_REQUIRED" and m["measurements"] is None


def test_shapefile_without_prj(client):
    def draw(w):
        w.point(77.0, 11.0)

    ok = make_shapefile_zip(shapefile.POINT, [draw], None)
    r = upload(client, "noprj.zip", ok)
    assert r.status_code == 201
    assert any("assuming EPSG:4326" in w for w in r.json()["warnings"])

    far = make_shapefile_zip(shapefile.POINT, [lambda w: w.point(500000, 1200000)], None)
    assert upload(client, "unknown_crs.zip", far).status_code == 422


def test_zip_without_shapefile(client):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hello")
    assert upload(client, "x.zip", buf.getvalue()).status_code == 422
    assert upload(client, "notzip.zip", b"not a zip").status_code == 422


# ---------------------------------------------------------------- misc API behaviour
def test_unsupported_extension_and_empty(client):
    assert upload(client, "data.geojson", b"{}").status_code == 415
    assert upload(client, "empty.kml", b"").status_code == 400


def test_not_found_list_and_delete(client):
    assert client.get("/api/files/doesnotexist/").status_code == 404
    assert client.get("/api/files/doesnotexist/measurements/").status_code == 404
    r = upload(client, "d.kml", KML_TEMPLATE.format(body=KML_BODY).encode())
    fid = r.json()["id"]
    assert client.get("/api/files/").json()["count"] >= 1
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    assert client.get(f"/api/files/{fid}/").status_code == 404


def test_pagination(client):
    r = upload(client, "p.kml", KML_TEMPLATE.format(body=KML_BODY).encode())
    fid = r.json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=1").json()
    assert page["count"] == 5 and len(page["results"]) == 2
    assert [x["index"] for x in page["results"]] == [1, 2]
