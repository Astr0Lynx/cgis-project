# Manual tool (not run by run_national_bench.sh): per-stage time and peak memory of the importer on one
# file (raster: validate -> write COG -> verify; vector: parse + normalize). Reports ru_maxrss and the
# cgroup memory.peak, since docker stats counts page cache. Run inside the ingestion image:
#   docker run --rm --memory 3g -v "$PWD:/repo" -w /app --entrypoint python cgis-ingestion:local \
#       /repo/tests/bench/measure_import_rss.py /repo/data/incoming/real/ind_ppp_2011.tif /tmp/out.tif

import json
import resource
import sys
import time
from pathlib import Path

from ingestion.manifest import parse_entry
from ingestion.raster import inspect_raster, verify_cog, write_cog
from ingestion.vector import read_vector


# Returns (process peak RSS MiB, cgroup memory.peak MiB or None). Used after each stage below.
def peak_mib():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    try:
        cg = int(Path("/sys/fs/cgroup/memory.peak").read_text()) / 2**20
    except OSError:
        cg = None
    return round(rss, 1), (round(cg, 1) if cg else None)


src, out = Path(sys.argv[1]), Path(sys.argv[2])
report = {"file": src.name, "size_bytes": src.stat().st_size}
if src.suffix.lower() in (".geojson", ".json"):
    spec = parse_entry({"dataset_id": "measure", "version": 1, "title": "t", "kind": "vector", "product_level": "source",
                        "source": "measure", "file": str(src), "id_property": "shapeID", "name_property": "shapeName",
                        "level_property": "shapeType"}, src.parent)
    t0 = time.monotonic()
    info = read_vector(spec)
    data = info.to_geojson_bytes(spec)
    report["vector_parse_normalize"] = {"seconds": round(time.monotonic() - t0, 1), "features": len(info.features),
                                        "normalized_bytes": len(data), "peak_rss_mib, cgroup_peak_mib": peak_mib()}
else:
    spec = parse_entry({"dataset_id": "measure", "version": 1, "title": "t", "kind": "raster", "product_level": "source",
                        "units": "people_per_cell", "source": "measure", "file": str(src)}, src.parent)
    t0 = time.monotonic()
    info = inspect_raster(spec)
    report["validate"] = {"seconds": round(time.monotonic() - t0, 1), "peak_rss_mib, cgroup_peak_mib": peak_mib()}
    t0 = time.monotonic()
    write_cog(spec, info, out, {"MEASURE": "1"})
    report["write_cog"] = {"seconds": round(time.monotonic() - t0, 1), "cog_bytes": out.stat().st_size,
                           "peak_rss_mib, cgroup_peak_mib": peak_mib()}
    t0 = time.monotonic()
    verify_cog(spec, info, out)
    report["verify_cog"] = {"seconds": round(time.monotonic() - t0, 1), "peak_rss_mib, cgroup_peak_mib": peak_mib()}
print(json.dumps(report, indent=2))
