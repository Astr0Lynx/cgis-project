# Shared SYNTHETIC fixtures for the cgis_data tests (never presented as real science).
import http.server
import socketserver
import threading
from functools import partial
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


# Factory writing a small single-band GeoTIFF into tmp_path; used by most raster tests.
@pytest.fixture
def make_raster(tmp_path):
    # Writes one raster with the given data/CRS/transform/NoData and returns its path.
    def _make(name="r.tif", data=None, crs="EPSG:4326", transform=None, nodata=-99999.0, dtype="float32"):
        data = np.arange(64, dtype=dtype).reshape(8, 8) if data is None else data
        transform = transform or from_origin(77.0, 18.0, 0.01, 0.01)
        p = tmp_path / name
        with rasterio.open(p, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype=dtype,
                           crs=crs, transform=transform, nodata=nodata) as ds:
            ds.write(data.astype(dtype), 1)
        return str(p)
    return _make


# Static HTTP handler that honours a single 'Range: bytes=N-' header (toggle with honour_range).
class RangeHandler(http.server.SimpleHTTPRequestHandler):
    honour_range = True

    # Serves a 206 partial body for Range requests, else the normal file.
    def do_GET(self):
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not (rng and self.honour_range and path.is_file()):
            return super().do_GET()
        start = int(rng.split("=")[1].split("-")[0])
        data = path.read_bytes()
        self.send_response(206)
        self.send_header("Content-Length", str(len(data) - start))
        self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
        self.end_headers()
        self.wfile.write(data[start:])

    # Silences request logging.
    def log_message(self, *a):
        pass


# Local HTTP server over tmp_path/srv; yields (served_dir, base_url) for fetch tests.
@pytest.fixture
def http_server(tmp_path):
    served = tmp_path / "srv"
    served.mkdir()
    handler = partial(RangeHandler, directory=str(served))
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield served, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
