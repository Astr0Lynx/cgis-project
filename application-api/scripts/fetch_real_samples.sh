#!/usr/bin/env sh
# Download two SMALL, openly licensed, REAL geospatial files used to exercise the admin upload/publish and
# export paths with genuine inputs (software evidence only; these are not CyberGIS model outputs):
#
#   * WorldPop Global 1 km unconstrained population count, India, 2011 (people per ~1 km cell, EPSG:4326)
#     https://hub.worldpop.org/ (Global 2000-2020 1 km, IND, 2011) - license CC BY 4.0
#   * geoBoundaries gbOpen India ADM1 (states), simplified, release 9469f09
#     https://www.geoboundaries.org/ - license CC BY 2.5 IN (source: DataMeet India, Election Commission of India)
#
# Files go to $OUT (default ./data/real-samples, git-ignored) and are verified against the SHA-256 below.
#   sh application-api/scripts/fetch_real_samples.sh
set -eu
OUT="${OUT:-data/real-samples}"
mkdir -p "$OUT"

# fetch URL FILE SHA256: download into $OUT unless a verified copy exists; refuse a checksum mismatch.
fetch() {
  if [ -f "$OUT/$2" ] && [ "$(sha256sum "$OUT/$2" | cut -d' ' -f1)" = "$3" ]; then
    echo "ok (cached) $2"; return 0
  fi
  curl -fsSL --retry 3 --max-time 600 -o "$OUT/$2.part" "$1"
  got="$(sha256sum "$OUT/$2.part" | cut -d' ' -f1)"
  if [ "$got" != "$3" ]; then
    echo "checksum mismatch for $2: got $got expected $3 (upstream changed? verify before trusting it)" >&2
    rm -f "$OUT/$2.part"; exit 1
  fi
  mv "$OUT/$2.part" "$OUT/$2"
  echo "ok $2"
}

fetch "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2011/IND/ind_ppp_2011_1km_Aggregated.tif" \
  "ind_ppp_2011_1km_Aggregated.tif" "${WORLDPOP_SHA256:-3a058a6e5cd42c9d18cf8c77e0eeb5f09dbc84a3053e963b981147fb67769703}"
fetch "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM1/geoBoundaries-IND-ADM1_simplified.geojson" \
  "geoBoundaries-IND-ADM1_simplified.geojson" "${GEOBOUNDARIES_SHA256:-4c63fe43294a391e8f2de4e9f86f3edb60f8688275b9fee90c61fb2aa0c26061}"
