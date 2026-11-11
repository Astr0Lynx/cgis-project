import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # cgis_level1
sys.path.insert(0, str(HERE.parents[2]))       # repository root (ingestion package, contract checks)

from cgis_level1.grid import ensure_proj_data  # noqa: E402

ensure_proj_data()


@pytest.fixture(scope="session")
def synthetic_inputs(tmp_path_factory):
    from cgis_level1.synthetic import make_synthetic
    d = tmp_path_factory.mktemp("synin")
    make_synthetic(d)
    return d


def write_config(path: Path, inputs: Path, out: Path, *, tile=100, workers=1, years=None, block=10,
                 policy="interpolate_2015", extra="") -> Path:
    years = years or [2001, 2005, 2010, 2011]
    policy_line = f'policy_2011 = "{policy}"' if policy else ""
    path.write_text(f"""
[paths]
root = "{out.as_posix()}"
work_dir = "work"
out_dir = "outputs"
[run]
name = "test"
synthetic = true
years = {years}
tile_size = {tile}
workers = {workers}
max_rss_gb = 4
[parent]
block = {block}
[inputs.worldpop]
pattern = "{(inputs / 'worldpop').as_posix()}/ind_ppp_{{year}}.tif"
[inputs.ghsl]
total_glob = "{(inputs / 'ghsl').as_posix()}/total_E{{epoch}}/*.tif"
nres_glob = "{(inputs / 'ghsl').as_posix()}/nres_E{{epoch}}/*.tif"
epochs = [2000, 2005, 2010]
[inputs.ghs_pop]
glob = "{(inputs / 'ghsl').as_posix()}/pop_E{{epoch}}/*.tif"
epochs = [2000, 2005, 2010, 2015]
[temporal]
{policy_line}
[output]
dataset_prefix = "synthetic-india-l1-pop-"
[evaluation]
supports = [1, 10, 50]
sample_step = 3
[evaluation.zones.d]
path = "{(inputs / 'zones' / 'synthetic_districts.geojson').as_posix()}"
id_property = "zid"
name_property = "name"
{extra}
""", encoding="utf-8")
    return path
