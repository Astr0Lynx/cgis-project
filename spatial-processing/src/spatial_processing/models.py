"""Request/response schemas (used for validation and the OpenAPI document).

Requests forbid unknown fields so a client cannot smuggle e.g. a file path or URL.
CRS, format and operation names are validated in the service layer so they map to
specific error codes instead of a generic validation error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_TOWN_A = {
    "type": "Polygon",
    "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99], [78.01, 17.99], [78.01, 17.98]]],
}


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., max_length=128, description="Catalog dataset ID (not a path or URL).")
    aoi: dict[str, Any] = Field(
        ..., description="GeoJSON Polygon or MultiPolygon (or a Feature wrapping one), x/y (lon/lat) order."
    )
    aoi_crs: str = Field(..., max_length=64, description="CRS of `aoi` as 'EPSG:<code>' (e.g. 'EPSG:4326').")


class ClipRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "dataset_id": "synthetic-worldpop-2001",
                "aoi": _TOWN_A,
                "aoi_crs": "EPSG:4326",
                "output_crs": "EPSG:4326",
                "format": "GTiff",
            }
        },
    )

    output_crs: str | None = Field(
        None,
        max_length=64,
        description="Output CRS 'EPSG:<code>'. Omit (or use the native CRS) for an exact native-grid clip; "
        "a different CRS triggers nearest-neighbour reprojection (totals not preserved).",
    )
    format: str = Field("GTiff", max_length=16, description="'GTiff' or 'COG'.")


class ZonalStatisticsRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "dataset_id": "synthetic-worldpop-2001",
                "aoi": _TOWN_A,
                "aoi_crs": "EPSG:4326",
                "operations": ["sum", "mean", "count"],
            }
        },
    )

    operations: list[str] | None = Field(
        None, max_length=8, description="Subset of 'sum', 'mean', 'count'. Default: all."
    )


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PixelSelection(BaseModel):
    rule: Literal["pixel_center"]
    all_touched: bool
    description: str


class ZonalStatisticsResponse(BaseModel):
    job_id: str
    status: str
    dataset_id: str
    dataset_version: int
    count_valid: int | None = Field(description="Number of non-NoData pixels whose centers fall in the AOI.")
    sum: float | int | None = Field(description="Sum of valid pixel values; see `sum_semantics`.")
    mean: float | None
    units: str | None
    sum_semantics: Literal["total_count", "not_applicable_density", "sum_of_cell_values"]
    pixels_in_aoi: int
    nodata_excluded: int = Field(description="Pixels in the AOI that were NoData/masked and excluded.")
    nodata_value: float | None
    aoi_crs: str
    raster_crs: str
    pixel_selection: PixelSelection
    operations: list[str]
    warnings: list[str]
    dataset: dict[str, Any]


class JobLinks(BaseModel):
    self: str
    download: str | None = None
    metadata: str | None = None


class JobResponse(BaseModel):
    job_id: str
    kind: Literal["clip", "zonal-statistics"]
    status: Literal["queued", "running", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None
    links: JobLinks
