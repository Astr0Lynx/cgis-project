"""Headless QGIS interoperability check against the test stack.

    docker run --rm --network cgis-ogc-test_default -e QT_QPA_PLATFORM=offscreen \
        -v "$PWD/ogc-services/tests/qgis:/qgis:ro" qgis/qgis:3.44.15 python3 /qgis/check_qgis.py

Loads synthetic-towns through QGIS's own OGC API - Features provider (OAPIF) and the OGC API - Tiles
URI template as an XYZ raster layer, renders the raster, and checks pixels. Exits non-zero on failure.
"""

import os
import sys

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

OGC = os.environ.get("CGIS_OGC_URL", "http://gateway:8080/ogc")
failures = []


def check(condition, message):
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


app = QgsApplication([], False)
app.initQgis()
print("QGIS", app.applicationVersion() if hasattr(app, "applicationVersion") else "")

# -- OGC API - Features via the OAPIF provider --------------------------------
towns = QgsVectorLayer(f"url='{OGC}' typename='synthetic-towns'", "towns", "OAPIF")
check(towns.isValid(), f"OAPIF layer valid ({towns.error().summary() if not towns.isValid() else 'ok'})")
if towns.isValid():
    features = list(towns.getFeatures())
    names = sorted(f["name"] for f in features)
    check(names == ["Town A", "Town B"], f"OAPIF features {names}")
    check(towns.crs().authid() in ("OGC:CRS84", "EPSG:4326"), f"OAPIF layer CRS {towns.crs().authid()}")
    check(all(f.geometry().wkbType() in (6, 1006, 3006) for f in features),
          f"geometry types {[f.geometry().wkbType() for f in features]} are MultiPolygon")
    by_name = {f["name"]: f.geometry().boundingBox() for f in features}
    a = by_name.get("Town A")
    check(a is not None and abs(a.xMinimum() - 78.01) < 1e-9 and abs(a.yMaximum() - 17.99) < 1e-9,
          f"Town A extent {a.toString() if a else None}")
    towns.setSubsetString("")  # bbox-filtered request via the provider's spatial filter
    from qgis.core import QgsFeatureRequest
    in_box = [f["name"] for f in towns.getFeatures(QgsFeatureRequest(QgsRectangle(78.009, 17.979, 78.021, 17.991)))]
    check(in_box == ["Town A"], f"spatially filtered request returned {in_box}")

# -- OGC API - Tiles tile template as an XYZ raster layer ---------------------
template = f"{OGC}/collections/synthetic-worldpop-2001/tiles/WebMercatorQuad/{{z}}/{{y}}/{{x}}?f%3Dpng"
raster = QgsRasterLayer(f"type=xyz&url={template}&zmin=0&zmax=13", "pop2001", "wms")
check(raster.isValid(), f"XYZ layer on the OGC tile template valid ({raster.error().summary() if not raster.isValid() else 'ok'})")
if raster.isValid():
    QgsProject.instance().addMapLayer(raster)
    web = QgsCoordinateReferenceSystem("EPSG:3857")
    to_web = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), web, QgsProject.instance())
    extent = to_web.transformBoundingBox(QgsRectangle(78.0, 17.936, 78.064, 18.0))
    settings = QgsMapSettings()
    settings.setLayers([raster])
    settings.setDestinationCrs(web)
    settings.setExtent(extent)
    settings.setOutputSize(QSize(512, 512))
    # The rendered QImage may carry no alpha channel, so transparency is checked as
    # "background shows through": an opaque magenta background behind the tiles.
    background = QColor(255, 0, 255, 255)
    settings.setBackgroundColor(background)
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()

    def pixel(lon, lat):
        pt = settings.mapToPixel().transform(to_web.transform(QgsPointXY(lon, lat)))
        return QColor(image.pixel(int(pt.x()), int(pt.y())))

    valid, nodata_col, nodata_row = pixel(78.03, 17.97), pixel(78.002, 17.97), pixel(78.03, 17.998)
    check(valid.getRgb()[:3] == (253, 231, 36), f"rendered valid cell RGB {valid.getRgb()[:3]} (viridis maximum)")
    check(nodata_col.getRgb() == background.getRgb() and nodata_row.getRgb() == background.getRgb(),
          f"NoData strips transparent (background visible: {nodata_col.getRgb()}, {nodata_row.getRgb()})")
    edge_inside, edge_outside = pixel(78.0045, 17.97), pixel(78.0035, 17.97)
    check(edge_inside.getRgb()[:3] == (253, 231, 36) and edge_outside.getRgb() == background.getRgb(),
          "NoData/valid boundary at 78.004 E (4 cells x 0.001 deg)")

print("RESULT", "FAILED" if failures else "OK", f"({len(failures)} failure(s))")
sys.stdout.flush()
QgsProject.instance().clear()
app.exitQgis()
# PyQGIS can segfault while the interpreter tears down Qt objects after exitQgis();
# exit immediately so the status reflects the checks, not the teardown.
os._exit(1 if failures else 0)
