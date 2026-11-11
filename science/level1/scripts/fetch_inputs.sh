#!/usr/bin/env bash
# Documented LAWFUL FALLBACK acquisition of the open (CC BY 4.0) Level 1 inputs, used because the Agent 1
# source deliveries (science/data, science/inputs) were not yet available on the frozen base. Agent 1 owns
# acquisition; when its manifests exist, point the config patterns at Agent 1's files instead.
#
#   WorldPop Global1 unconstrained India ppp 2001-2011  (~1.85 GB each, ~20.3 GB total)
#   GHSL GHS-BUILT-S + NRES R2023A 4326_3ss, epochs 2000/2005/2010 (+2015 for D-L1-01 option C)
#   GHS-POP R2023A 4326_3ss, epochs 2000/2005/2010/2015 (evaluation reference)
#   geoBoundaries gbOpen IND ADM1/ADM2 (diagnostic evaluation zones)
#
# Usage: science/level1/scripts/fetch_inputs.sh [DATA_ROOT=data/incoming]   (resumable; re-run to continue)
# WorldPop's HTTPS server is throttled to ~0.1 MB/s per connection and does not support ranges; its FTP
# mirror (same files) is used with resume, 4 years in parallel (the FTP limit per IP; ~0.5 MB/s each).
set -euo pipefail
ROOT="${1:-data/incoming}"
WP="$ROOT/worldpop"; GH="$ROOT/ghsl"; BD="$ROOT/boundaries"
mkdir -p "$WP" "$GH" "$BD"
PAR="${PARALLEL:-4}"   # ftp.worldpop.org refuses >4 connections per IP (421)

wp_one() {
  y=$1; f="$WP/ind_ppp_$y.tif"
  exp=$(curl -sIL "https://data.worldpop.org/GIS/Population/Global_2000_2020/$y/IND/ind_ppp_$y.tif" \
        | grep -i content-length | tail -1 | tr -dc 0-9)
  for _ in $(seq 1 30); do
    have=$(stat -c %s "$f" 2>/dev/null || echo 0)
    [ "$have" = "$exp" ] && break
    curl -s -C - --retry 3 --speed-limit 10000 --speed-time 120 -o "$f" \
      "ftp://ftp.worldpop.org/GIS/Population/Global_2000_2020/$y/IND/ind_ppp_$y.tif" || true
  done
  [ "$(stat -c %s "$f")" = "$exp" ] && echo "worldpop $y ok" || { echo "worldpop $y INCOMPLETE"; return 1; }
}
export -f wp_one; export WP
seq 2001 2011 | xargs -P "$PAR" -I{} bash -c 'wp_one {}'

B=https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL
TILES="R6_C25 R6_C26 R6_C27 R6_C28 R7_C25 R7_C26 R7_C27 R7_C28 R8_C26 R8_C27 R8_C28 R9_C26 R9_C27 R9_C28"
for e in 2000 2005 2010 2015; do
  for p in "GHS_BUILT_S_GLOBE_R2023A GHS_BUILT_S_E${e}_GLOBE_R2023A_4326_3ss" \
           "GHS_BUILT_S_GLOBE_R2023A GHS_BUILT_S_NRES_E${e}_GLOBE_R2023A_4326_3ss" \
           "GHS_POP_GLOBE_R2023A GHS_POP_E${e}_GLOBE_R2023A_4326_3ss"; do
    set -- $p; mkdir -p "$GH/$2"
    for t in $TILES; do
      [ -f "$GH/$2/${2}_V1_0_$t.tif" ] && continue
      z="$GH/$2/${2}_V1_0_$t.zip"
      curl -s --retry 5 -f -o "$z" "$B/$1/$2/V1-0/tiles/${2}_V1_0_$t.zip" || { echo "absent $2 $t"; rm -f "$z"; continue; }
      (cd "$GH/$2" && unzip -o -q "$(basename "$z")" '*.tif' && rm -f "$(basename "$z")")
    done
    curl -s -o "$GH/$2/copyright.txt" "$B/$1/$2/V1-0/copyright.txt"
    echo "ghsl $2 ok"
  done
done
# India 10x10-degree tiles: rows R6-R9 x cols C25-C28. R8_C25 and R9_C25 do not exist (open sea).

for l in ADM1 ADM2; do
  [ -s "$BD/geoBoundaries-IND-$l.geojson" ] || curl -sL -o "$BD/geoBoundaries-IND-$l.geojson" \
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/$l/geoBoundaries-IND-$l.geojson"
done
echo "inputs complete under $ROOT; next: python -m cgis_level1 inventory --config science/level1/config/india_full.toml"
