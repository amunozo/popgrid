import geopandas as gpd
import pytest
import shapely

from popgrid.arcgis import (
    _hex_to_rgba,
    _validate_pyshp_compatibility,
    grid_cells_to_geodataframe,
    publish_hosted_feature_layer,
    region_unique_value_renderer,
)


class DummyGrid:
    country_iso3 = "ESP"
    n = 100
    cell_size_km = 12.5
    color_map = {"Madrid": "#F2C94C", "Catalonia": "#F2994A"}

    def __init__(self):
        self._landmasses = [
            {
                "id": 0,
                "cells": gpd.GeoDataFrame(
                    {
                        "region_name": ["Madrid", "Catalonia"],
                        "landmass_id": [0, 0],
                        "cx": [0.5, 1.5],
                        "cy": [0.5, 0.5],
                    },
                    geometry=[
                        shapely.box(0, 0, 1, 1),
                        shapely.box(1, 0, 2, 1),
                    ],
                    crs="EPSG:3857",
                ),
            }
        ]

    @property
    def total_cells(self):
        return 2

    def build(self):
        return self


def test_grid_cells_to_geodataframe_adds_agol_publish_attributes():
    gdf = grid_cells_to_geodataframe(DummyGrid(), mode="population")

    assert all(len(column) <= 10 for column in gdf.columns if column != "geometry")
    assert list(gdf["iso3"]) == ["ESP", "ESP"]
    assert list(gdf["mode"]) == ["population", "population"]
    assert list(gdf["block_id"]) == [1, 2]
    assert list(gdf["region"]) == ["Madrid", "Catalonia"]
    assert list(gdf["color_hex"]) == ["#F2C94C", "#F2994A"]
    assert list(gdf["target_n"]) == [100, 100]
    assert list(gdf["blocks"]) == [2, 2]
    assert list(gdf["share_pct"]) == [50.0, 50.0]
    assert list(gdf["cell_km"]) == [12.5, 12.5]
    assert gdf.crs.to_epsg() == 4326


def test_grid_cells_to_geodataframe_rejects_empty_grid():
    grid = DummyGrid()
    grid._landmasses[0]["cells"] = grid._landmasses[0]["cells"].iloc[:0]

    with pytest.raises(ValueError, match="no cells"):
        grid_cells_to_geodataframe(grid, mode="area")


def test_validate_pyshp_compatibility_rejects_pyshp_3_style_signature():
    def field(name, field_type="C", size=50, decimal=0):
        return None

    Writer = type("Writer", (), {"field": staticmethod(field)})

    FakeShapefile = type(
        "FakeShapefile",
        (),
        {"__version__": "3.0.9", "Writer": Writer},
    )

    with pytest.raises(ImportError, match="pyshp==2.3.1"):
        _validate_pyshp_compatibility(FakeShapefile)


def test_hex_to_rgba_converts_css_hex_to_arcgis_color_array():
    assert _hex_to_rgba("#F1CE63", alpha=210) == [241, 206, 99, 210]


def test_region_unique_value_renderer_uses_region_colors():
    gdf = grid_cells_to_geodataframe(DummyGrid(), mode="population")

    drawing_info = region_unique_value_renderer(gdf)
    renderer = drawing_info["drawingInfo"]["renderer"]

    assert renderer["type"] == "uniqueValue"
    assert renderer["field1"] == "region"
    assert [info["value"] for info in renderer["uniqueValueInfos"]] == [
        "Catalonia",
        "Madrid",
    ]
    first_symbol = renderer["uniqueValueInfos"][0]["symbol"]
    assert first_symbol["type"] == "esriSFS"
    assert first_symbol["style"] == "esriSFSSolid"
    assert first_symbol["color"] == [242, 153, 74, 218]


def test_publish_requires_an_authenticated_gis():
    gdf = gpd.GeoDataFrame(
        {"region": ["Madrid"], "color_hex": ["#F2C94C"]},
        geometry=[shapely.box(0, 0, 1, 1)],
        crs="EPSG:4326",
    )

    with pytest.raises(ValueError, match="authenticated ArcGIS session"):
        publish_hosted_feature_layer(gdf, title="popgrid_test", gis=None)
