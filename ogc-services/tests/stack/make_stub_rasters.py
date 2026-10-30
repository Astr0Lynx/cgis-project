"""TEST STUB ONLY: write the two synthetic rasters defined in contracts/SYNTHETIC_DATA.md.

Agent 3 owns the real fixture generator; this lightweight stub follows the same
contract so the tile services can be tested in isolation. It also writes the
hostile files used by the path-safety tests (a VRT disguised as .tif and a
symlink that escapes the asset root).

Usage: python make_stub_rasters.py /data/assets
"""

import os
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

NODATA = -9999.0
SIZE = 64


def write_cog(path: Path, value: float) -> None:
    data = np.full((SIZE, SIZE), value, dtype="float32")
    data[:4, :] = NODATA
    data[:, :4] = NODATA
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "COG", "width": SIZE, "height": SIZE, "count": 1, "dtype": "float32",
        "crs": "EPSG:4326", "transform": from_origin(78.000, 18.000, 0.001, 0.001),
        "nodata": NODATA, "compress": "DEFLATE", "blocksize": 256,
    }
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(SYNTHETIC="true", source="deterministic-fixture", units="people_per_cell")
    os.replace(tmp, path)


def main(root: str) -> None:
    root_path = Path(root)
    write_cog(root_path / "synthetic" / "synthetic_2001.tif", 1.0)
    write_cog(root_path / "synthetic" / "synthetic_2011.tif", 2.0)

    hostile = root_path / "hostile"
    hostile.mkdir(parents=True, exist_ok=True)
    (hostile / "disguised.tif").write_text(
        '<VRTDataset rasterXSize="64" rasterYSize="64"><VRTRasterBand dataType="Float32" band="1">'
        "<SimpleSource><SourceFilename>/etc/hostname</SourceFilename></SimpleSource>"
        "</VRTRasterBand></VRTDataset>\n"
    )
    link = hostile / "escape.tif"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to("/etc/hostname")
    print(f"stub rasters written under {root_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data/assets")
