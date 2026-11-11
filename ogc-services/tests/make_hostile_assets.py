# Writes the hostile files used by the tiler path-safety integration tests into the asset volume:
# hostile/disguised.tif (VRT XML with a .tif suffix) and hostile/escape.tif (symlink out of the root).
# Usage: python tests/make_hostile_assets.py /data/assets   (run by tests/run.sh modules)

import sys
from pathlib import Path


# Creates both hostile files under <root>/hostile (idempotent). Called from the command line.
def main(root: str) -> None:
    hostile = Path(root) / "hostile"
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
    print(f"hostile test assets written under {hostile}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data/assets")
