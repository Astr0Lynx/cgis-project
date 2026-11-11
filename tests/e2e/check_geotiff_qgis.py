# Opens a GeoTIFF/COG exported by the platform in headless QGIS (qgis/qgis image) and checks it.
#   python3 check_geotiff_qgis.py FILE [--crs EPSG:4326] [--town-a]
# Always: QGIS opens it, a NoData value is set, the CRS matches --crs if given. --town-a adds the exact
# Town A / 2001 clip expectations (EPSG:4326, 10 x 10, extent 78.010,17.980 : 78.020,17.990, all 1s).
# Used by `tests/run.sh e2e --qgis` (--town-a) and tests/bench/run_national_bench.sh (--crs).
import sys

from qgis.core import QgsApplication, QgsRasterBandStats, QgsRasterLayer

args = sys.argv[1:]
path = args[0]
want_crs = args[args.index("--crs") + 1] if "--crs" in args else None
town_a = "--town-a" in args
app = QgsApplication([], False)
app.initQgis()
failures = []


# Prints one PASS/FAIL line and remembers failures. Used by the script body below.
def check(ok, message):
    print(("PASS " if ok else "FAIL ") + message)
    if not ok:
        failures.append(message)


layer = QgsRasterLayer(path, "clip")
check(layer.isValid(), f"QGIS opens {path}")
if layer.isValid():
    provider = layer.dataProvider()
    ext = layer.extent()
    crs = layer.crs().authid()
    nodata = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else None
    stats = provider.bandStatistics(1, QgsRasterBandStats.All)
    print(f"INFO {layer.width()} x {layer.height()} px, CRS {crs}, extent {ext.toString()}, NoData {nodata}; "
          f"stats min={stats.minimumValue} max={stats.maximumValue} sum={stats.sum} count={stats.elementCount}")
    check(nodata is not None, f"NoData {nodata}")
    if want_crs:
        check(crs == want_crs, f"CRS {crs} (want {want_crs})")
    if town_a:
        check(crs == "EPSG:4326", f"CRS {crs}")
        check((layer.width(), layer.height()) == (10, 10), f"size {layer.width()} x {layer.height()}")
        check(abs(ext.xMinimum() - 78.01) < 1e-9 and abs(ext.yMinimum() - 17.98) < 1e-9
              and abs(ext.xMaximum() - 78.02) < 1e-9 and abs(ext.yMaximum() - 17.99) < 1e-9,
              f"extent {ext.toString()}")
        check(nodata == -9999, f"NoData {nodata} is -9999")
        check(stats.minimumValue == 1 and stats.maximumValue == 1 and stats.sum == 100 and stats.elementCount == 100,
              "every valid value is 1 and they sum to 100")

print("RESULT", "FAILED" if failures else "OK", f"({len(failures)} failure(s))")
app.exitQgis()
sys.exit(1 if failures else 0)
