#!/usr/bin/env bash
# Full, restartable Level 1 sequence from the repository root: tests, inventory, run, evaluate, ingestion
# validation, summary and evidence copy into science/results/level1/<label>/. Log in data/outputs/level1-logs/.
#   science/level1/scripts/run_all.sh [config=science/level1/config/india_full.toml] [label=india-full]
set -euo pipefail
CFG="${1:-science/level1/config/india_full.toml}"
LABEL="${2:-india-full}"
export PYTHONPATH="science/level1:.${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p data/outputs/level1-logs
LOG="data/outputs/level1-logs/${LABEL}-$(date +%Y%m%dT%H%M%S).log"
{
  echo "== tests";      (cd science/level1 && python -m pytest -q)
  echo "== inventory";  python -m cgis_level1 inventory --config "$CFG"
  echo "== run";        python -m cgis_level1 run       --config "$CFG"
  echo "== evaluate";   python -m cgis_level1 evaluate  --config "$CFG"
  echo "== validate (repository ingestion validator)"
  python - "$CFG" <<'EOF'
import sys
from cgis_level1.config import load_config
from cgis_level1.grid import ensure_proj_data
from ingestion.cli import main
ensure_proj_data()
sys.exit(main(["validate", "--manifest", str(load_config(sys.argv[1]).out_dir / "manifest.json")]))
EOF
  echo "== summary";    python -m cgis_level1 summary  --config "$CFG"
  echo "== evidence";   python -m cgis_level1 evidence --config "$CFG" --label "$LABEL"
} 2>&1 | tee "$LOG"
