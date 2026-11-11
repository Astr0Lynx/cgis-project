"""Open a GeoTIFF/COG produced by the platform in headless QGIS and print its georeferencing.

    docker run --rm -e QT_QPA_PLATFORM=offscreen -v "$PWD:/repo:ro" qgis/qgis:3.44.15 \
        python3 /repo/infra/bench/qgis_check_tif.py /repo/data/bench/clip_async.tif [EPSG:4326]
"""

import sys

from qgis.core import QgsApplication, QgsRasterBandStats, QgsRasterLayer

path = sys.argv[1]
want_crs = sys.argv[2] if len(sys.argv) > 2 else None
app = QgsApplication([], False)
app.initQgis()
fails = 0
layer = QgsRasterLayer(path, "clip")
if not layer.isValid():
    print("FAIL layer invalid")
    sys.exit(1)
dp = layer.dataProvider()
crs = layer.crs().authid()
ext = layer.extent()
nodata = dp.sourceNoDataValue(1) if dp.sourceHasNoDataValue(1) else None
stats = dp.bandStatistics(1, QgsRasterBandStats.All)
print(f"PASS opened {path}: {layer.width()} x {layer.height()} px, CRS {crs}")
print(f"INFO extent {ext.xMinimum():.6f},{ext.yMinimum():.6f} : {ext.xMaximum():.6f},{ext.yMaximum():.6f}")
print(f"INFO nodata {nodata}; valid-cell stats min {stats.minimumValue:.4f} max {stats.maximumValue:.4f} "
      f"sum {stats.sum:.4f} count {stats.elementCount}")
if want_crs and crs != want_crs:
    print(f"FAIL CRS {crs} != {want_crs}")
    fails += 1
if nodata is None:
    print("FAIL no NoData value")
    fails += 1
print(f"RESULT {'OK' if not fails else 'FAIL'} ({fails} failure(s))")
app.exitQgis()
sys.exit(1 if fails else 0)
