# Opens the exported Town A / 2001 clip in headless QGIS (run by `tests/run.sh e2e --qgis`) and checks:
# EPSG:4326, 10 x 10 pixels, extent 78.010,17.980 : 78.020,17.990, NoData -9999, every value 1 (sum 100).

import sys

from qgis.core import QgsApplication, QgsRasterBandStats, QgsRasterLayer

path = sys.argv[1] if len(sys.argv) > 1 else "/e2e/clip_town_a_2001.tif"
app = QgsApplication([], False)
app.initQgis()
failures = []


# Prints one PASS/FAIL check and remembers failures. Used by the script body below.
def check(ok, message):
    print(("PASS " if ok else "FAIL ") + message)
    if not ok:
        failures.append(message)


layer = QgsRasterLayer(path, "clip")
check(layer.isValid(), f"QGIS opens {path}")
if layer.isValid():
    provider = layer.dataProvider()
    ext = layer.extent()
    check(layer.crs().authid() == "EPSG:4326", f"CRS {layer.crs().authid()}")
    check((layer.width(), layer.height()) == (10, 10), f"size {layer.width()} x {layer.height()}")
    check(abs(ext.xMinimum() - 78.01) < 1e-9 and abs(ext.yMinimum() - 17.98) < 1e-9
          and abs(ext.xMaximum() - 78.02) < 1e-9 and abs(ext.yMaximum() - 17.99) < 1e-9,
          f"extent {ext.toString()}")
    check(provider.sourceHasNoDataValue(1) and provider.sourceNoDataValue(1) == -9999,
          f"NoData {provider.sourceNoDataValue(1)}")
    stats = provider.bandStatistics(1, QgsRasterBandStats.All)
    check(stats.minimumValue == 1 and stats.maximumValue == 1 and stats.sum == 100 and stats.elementCount == 100,
          f"stats min={stats.minimumValue} max={stats.maximumValue} sum={stats.sum} count={stats.elementCount}")

print("RESULT", "FAILED" if failures else "OK", f"({len(failures)} failure(s))")
app.exitQgis()
sys.exit(1 if failures else 0)
