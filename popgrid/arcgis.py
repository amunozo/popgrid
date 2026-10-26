"""
ArcGIS Online publishing helpers for popgrid.

The conversion functions only depend on GeoPandas. The ArcGIS API for Python is
imported lazily by ``publish_hosted_feature_layer`` so popgrid stays usable
without ArcGIS installed.
"""

from __future__ import annotations

from typing import Iterable, Literal
import inspect

import geopandas as gpd

from .core import AreaGrid, PopGrid

GridMode = Literal["area", "population"]


def _validate_pyshp_compatibility(shapefile_module) -> None:
    field_params = inspect.signature(shapefile_module.Writer.field).parameters
    if "fieldType" not in field_params:
        version = getattr(shapefile_module, "__version__", "unknown")
        raise ImportError(
            "ArcGIS API for Python 2.4.x expects pyshp 2.x. "
            f"Found pyshp {version}, whose Writer.field() signature is incompatible. "
            "Install the compatible version with: python -m pip install pyshp==2.3.1"
        )


def _hex_to_rgba(hex_color: str, alpha: int = 218) -> list[int]:
    color = hex_color.strip().lstrip("#")
    if len(color) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {hex_color!r}.")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)] + [alpha]


def region_unique_value_renderer(gdf: gpd.GeoDataFrame, alpha: int = 218) -> dict:
    """
    Build ArcGIS ``drawingInfo`` for region-colored popgrid polygons.
    """
    required = {"region", "color_hex"}
    missing = required.difference(gdf.columns)
    if missing:
        raise ValueError(f"GeoDataFrame is missing renderer field(s): {sorted(missing)}")

    region_colors = (
        gdf[["region", "color_hex"]]
        .dropna()
        .drop_duplicates()
        .sort_values("region")
    )
    unique_value_infos = []
    for row in region_colors.itertuples(index=False):
        unique_value_infos.append(
            {
                "value": row.region,
                "label": row.region,
                "symbol": {
                    "type": "esriSFS",
                    "style": "esriSFSSolid",
                    "color": _hex_to_rgba(row.color_hex, alpha=alpha),
                    "outline": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": [36, 44, 41, 190],
                        "width": 0.45,
                    },
                },
            }
        )

    return {
        "drawingInfo": {
            "renderer": {
                "type": "uniqueValue",
                "field1": "region",
                "field2": None,
                "field3": None,
                "fieldDelimiter": ", ",
                "defaultLabel": "Other",
                "defaultSymbol": {
                    "type": "esriSFS",
                    "style": "esriSFSSolid",
                    "color": [128, 128, 128, alpha],
                    "outline": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": [36, 44, 41, 190],
                        "width": 0.45,
                    },
                },
                "uniqueValueInfos": unique_value_infos,
            },
            "transparency": 0,
        }
    }


def _grid_country_iso3(grid: AreaGrid | PopGrid) -> str | None:
    return getattr(grid, "country_iso3", None)


def _cells_from_grid(grid: AreaGrid | PopGrid) -> gpd.GeoDataFrame:
    grid.build()
    frames = []
    for landmass in getattr(grid, "_landmasses", []) or []:
        cells = landmass.get("cells")
        if cells is not None and not cells.empty:
            frames.append(cells.copy())

    if not frames:
        raise ValueError("Grid has no cells to publish. Build it with a larger n or valid data.")

    return gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )


def grid_cells_to_geodataframe(
    grid: AreaGrid | PopGrid,
    mode: GridMode,
    country_iso3: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Convert a built popgrid object into WGS84 polygons ready for ArcGIS Online.

    The output keeps one row per block and adds stable publishing attributes so
    the layer can be filtered by country, mode, region, block index, and color.
    """
    if mode not in ("area", "population"):
        raise ValueError("mode must be 'area' or 'population'.")

    cells = _cells_from_grid(grid)
    total_blocks = int(grid.total_cells)
    if total_blocks <= 0:
        raise ValueError("Grid has no cells to publish. Build it with a larger n or valid data.")

    out = cells.to_crs("EPSG:4326").reset_index(drop=True)
    colors = grid.color_map

    out.insert(0, "block_id", range(1, len(out) + 1))
    out["iso3"] = country_iso3 or _grid_country_iso3(grid) or "CUSTOM"
    out["mode"] = mode
    out["target_n"] = int(grid.n)
    out["blocks"] = total_blocks
    out["share_pct"] = round(100.0 / total_blocks, 6)
    out["cell_km"] = float(grid.cell_size_km)
    out["color_hex"] = out["region_name"].map(colors).fillna("#808080")
    if "landmass_id" in out.columns:
        out = out.rename(columns={"landmass_id": "land_id"})
    out = out.rename(columns={"region_name": "region"})

    preferred = [
        "iso3",
        "mode",
        "block_id",
        "region",
        "color_hex",
        "target_n",
        "blocks",
        "share_pct",
        "cell_km",
        "land_id",
        "cx",
        "cy",
        "geometry",
    ]
    return out[[col for col in preferred if col in out.columns]]


def build_country_feature_layer_gdf(
    country_iso3: str,
    modes: Iterable[GridMode] = ("area", "population"),
    n: int = 1000,
    **grid_kwargs,
) -> gpd.GeoDataFrame:
    """
    Build area/population popgrid blocks for a bundled country as one dataset.
    """
    frames: list[gpd.GeoDataFrame] = []
    for mode in modes:
        if mode == "area":
            grid = AreaGrid(country_iso3, n=n, **grid_kwargs)
        elif mode == "population":
            grid = PopGrid(country_iso3, n=n, **grid_kwargs)
        else:
            raise ValueError("modes must contain only 'area' and/or 'population'.")
        frames.append(grid_cells_to_geodataframe(grid, mode=mode, country_iso3=country_iso3))

    if not frames:
        raise ValueError("At least one mode is required.")

    return gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )


def publish_hosted_feature_layer(
    gdf: gpd.GeoDataFrame,
    title: str,
    gis=None,
    folder: str | None = None,
    tags: str | list[str] | None = None,
):
    """
    Publish a GeoDataFrame as an ArcGIS Online Hosted Feature Layer.

    ``gis`` must be an authenticated ``arcgis.gis.GIS`` instance, for example
    ``GIS(profile="popgrid_agol")``. An anonymous session cannot publish.
    """
    if gdf.empty:
        raise ValueError("Cannot publish an empty GeoDataFrame.")
    if gis is None:
        raise ValueError(
            "Publishing needs an authenticated ArcGIS session. Pass one with "
            "gis=GIS(profile='your_profile'); an anonymous GIS() cannot publish."
        )

    try:
        import pandas as pd
        import shapefile
        from arcgis.features import GeoAccessor, GeoSeriesAccessor  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Publishing requires the ArcGIS API for Python. Install it with: "
            "pip install 'popgrid[arcgis]'"
        ) from exc
    _validate_pyshp_compatibility(shapefile)

    publish_gdf = gdf.to_crs("EPSG:4326").copy()
    sdf = GeoAccessor.from_geodataframe(publish_gdf)

    if tags is None:
        tags = ["popgrid", "cartogram", "population", "hosted feature layer"]

    # Ensure the pandas spatial accessor is registered before publishing.
    if not isinstance(sdf, pd.DataFrame):
        raise TypeError("ArcGIS conversion did not return a pandas DataFrame.")

    item = sdf.spatial.to_featurelayer(
        title=title,
        gis=gis,
        folder=folder,
        tags=tags,
        sanitize_columns=True,
    )
    apply_region_renderer(item, publish_gdf)
    return item


def apply_region_renderer(item, gdf: gpd.GeoDataFrame, layer_index: int = 0):
    """
    Apply region/color_hex symbology to a Hosted Feature Layer item.
    """
    if not getattr(item, "layers", None):
        raise ValueError("Item has no feature layers to symbolize.")
    layer = item.layers[layer_index]
    return layer.manager.update_definition(region_unique_value_renderer(gdf))
