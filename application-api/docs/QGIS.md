# QGIS, GDAL and script access with personal access tokens

Browser sessions use a cookie and are not suitable for desktop GIS. Use a **personal access token** instead.

## 1. Create a token

Sign in to the viewer (or with curl) and call `POST /auth/tokens`:

```sh
B=http://localhost:8080
curl -s -c jar -H 'Content-Type: application/json' -d '{"username":"<you>","password":"<password>"}' $B/auth/login > login.json
CSRF=$(python3 -c 'import json;print(json.load(open("login.json"))["csrf_token"])')
curl -s -b jar -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"name":"QGIS on my laptop","expires_in_days":30,"scope":"read"}' $B/auth/tokens
# -> {"token":"cgis_pat_…", "expires_at": "…", …}   the token is shown only once
```

* `scope: read` (default) acts as role `user`: discovery, OGC/Tiles (when the gateway protects them) and exports.
* `scope: admin` (admin accounts only) also allows `/admin/*`; use it for automation, not for QGIS.
* List/revoke: `GET /auth/tokens`, `DELETE /auth/tokens/{token_id}`. Changing your password revokes all tokens.

## 2. QGIS

*Settings → Options → Authentication → Add* (or the key icon in the connection dialog):

* **Basic** method: *Username* anything (e.g. your user name), *Password* = the token. Works for
  *OGC API - Features* (`http://localhost:8080/ogc`), *XYZ/vector tiles* and *WMS/WFS*-style HTTP connections.
* or **API Header** method (QGIS ≥ 3.28): header `Authorization`, value `Bearer cgis_pat_…`.

Account passwords are never accepted through HTTP Basic; only tokens are.

## 3. GDAL / OGR

```sh
export GDAL_HTTP_HEADERS="Authorization: Bearer cgis_pat_…"
ogrinfo "OAPIF:http://localhost:8080/ogc" -so
# or: export GDAL_HTTP_USERPWD="any:cgis_pat_…"
```

Download an export with the token:

```sh
curl -s -H "Authorization: Bearer cgis_pat_…" -H 'Content-Type: application/json' $B/api/exports \
  -d '{"dataset_id":"india-l1-pop-2011","kind":"full","format":"COG"}'
curl -s -H "Authorization: Bearer cgis_pat_…" -OJ $B/api/exports/<job_id>/download
```

Never put a token in a URL (`?access_token=` is refused with 400): URLs end up in logs and histories.
