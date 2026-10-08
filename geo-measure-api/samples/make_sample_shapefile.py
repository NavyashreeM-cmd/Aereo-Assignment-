"""Create samples/sample_shapefile.zip (EPSG:4326 polygon + attributes). Run from repo root."""
import io
import zipfile

import shapefile
from pyproj import CRS

shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
w.field("name", "C", 40)
w.field("crop", "C", 20)
w.poly([[[76.9558, 11.0168], [76.9558, 11.0268], [76.9658, 11.0268], [76.9658, 11.0168], [76.9558, 11.0168]]])
w.record("Farm plot", "Banana")
w.close()

with zipfile.ZipFile("samples/sample_shapefile.zip", "w") as z:
    z.writestr("plots.shp", shp.getvalue())
    z.writestr("plots.shx", shx.getvalue())
    z.writestr("plots.dbf", dbf.getvalue())
    z.writestr("plots.prj", CRS.from_epsg(4326).to_wkt())
print("wrote samples/sample_shapefile.zip")
