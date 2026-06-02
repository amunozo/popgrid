"""
Tests for popgrid.core — AreaGrid class, end-to-end pipeline.
"""

import pytest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from popgrid import AreaGrid
from popgrid.exceptions import CountryNotFoundError


# ── Instantiation ─────────────────────────────────────────────────────────────

def test_instantiation_does_not_build():
    pg = AreaGrid("DEU")
    assert not pg._built


def test_repr_before_build():
    pg = AreaGrid("DEU")
    assert "not built" in repr(pg)


def test_repr_after_build():
    pg = AreaGrid("DEU", n=200)
    pg.build()
    assert "built" in repr(pg)


def test_invalid_country_raises_on_build():
    pg = AreaGrid("ZZZ")
    with pytest.raises(CountryNotFoundError):
        pg.build()


# ── Germany — no dissolve ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def germany():
    pg = AreaGrid("DEU", n=500)
    pg.build()
    return pg


def test_germany_total_cells_near_target(germany):
    assert 450 <= germany.total_cells <= 550


def test_germany_cell_size_km_positive(germany):
    assert germany.cell_size_km > 0


def test_germany_n_landmasses_at_least_one(germany):
    assert germany.n_landmasses >= 1


def test_germany_regions_geodataframe(germany):
    import geopandas as gpd
    assert isinstance(germany.regions, gpd.GeoDataFrame)


def test_germany_regions_has_16_rows(germany):
    assert len(germany.regions) == 16


def test_germany_color_map_is_dict(germany):
    assert isinstance(germany.color_map, dict)


def test_germany_color_map_covers_all_regions(germany):
    region_names = germany.regions["region_name"].tolist()
    for name in region_names:
        assert name in germany.color_map, f"Region '{name}' missing from color_map"


def test_germany_color_map_values_are_hex(germany):
    import re
    hex_pattern = re.compile(r"^#[0-9a-fA-F]{6}$")
    for name, color in germany.color_map.items():
        assert hex_pattern.match(color), f"Invalid hex color '{color}' for '{name}'"


# ── Spain — dissolve to CCAA ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def spain():
    pg = AreaGrid("ESP", n=500, dissolve_by="region")
    pg.build()
    return pg


def test_spain_has_ccaa_not_provinces(spain):
    # Should have 19 (17 CCAA + Ceuta + Melilla), not 52 provinces
    assert len(spain.regions) == 19


def test_spain_ceuta_and_melilla_present(spain):
    names = spain.regions["region_name"].tolist()
    assert "Ceuta" in names
    assert "Melilla" in names


def test_spain_has_multiple_landmasses(spain):
    assert spain.n_landmasses >= 3


def test_spain_all_landmasses_have_cells(spain):
    for lm in spain._landmasses:
        assert len(lm["cells"]) >= 1, (
            f"Landmass {lm['id']} has 0 cells — sub-cell fallback should guarantee 1"
        )


# ── plot() ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def germany_figure(germany):
    fig = germany.plot(title="Test", show_labels=False)
    yield fig
    plt.close(fig)


def test_plot_returns_figure(germany_figure):
    assert isinstance(germany_figure, plt.Figure)


def test_plot_has_axes(germany_figure):
    assert len(germany_figure.axes) >= 1


def test_plot_custom_background(germany):
    fig = germany.plot(background_color="#ffffff", show_labels=False)
    assert fig.get_facecolor() is not None
    plt.close(fig)


def test_plot_no_labels_does_not_crash(germany):
    fig = germany.plot(show_labels=False)
    plt.close(fig)


def test_save_writes_file(germany, tmp_path):
    out = tmp_path / "test_germany.png"
    germany.save(str(out), show_labels=False)
    assert out.exists()
    assert out.stat().st_size > 0


# ── Palettes ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("palette", ["qual", "databites", "bold"])
def test_named_palettes_work(palette):
    pg = AreaGrid("DEU", n=200, palette=palette)
    pg.build()
    fig = pg.plot(show_labels=False)
    plt.close(fig)


def test_custom_palette_list():
    custom = ["#e63946", "#457b9d", "#2a9d8f", "#e9c46a", "#f4a261"]
    pg = AreaGrid("DEU", n=200, palette=custom)
    pg.build()
    fig = pg.plot(show_labels=False)
    plt.close(fig)


# ── from_geodataframe ─────────────────────────────────────────────────────────

def _make_test_gdf():
    """Minimal 3-region GeoDataFrame in WGS84."""
    import geopandas as gpd
    import shapely
    geoms = [
        shapely.box(0, 0, 2, 2),
        shapely.box(2, 0, 4, 2),
        shapely.box(0, 2, 4, 4),
    ]
    return gpd.GeoDataFrame(
        {"NAME": ["Alpha", "Beta", "Gamma"], "geometry": geoms},
        crs="EPSG:4326",
    )


def test_from_geodataframe_returns_area_grid():
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME")
    assert isinstance(ag, AreaGrid)
    assert not ag._built


def test_from_geodataframe_custom_gdf_stored():
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME")
    assert ag._custom_gdf is not None
    assert ag._custom_region_col == "NAME"


def test_from_geodataframe_builds():
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME", n=50)
    ag.build()
    assert ag._built
    assert ag.total_cells > 0


def test_from_geodataframe_plot_returns_figure():
    import matplotlib.pyplot as plt
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME", n=50)
    fig = ag.plot(title="Test Map")
    assert fig is not None
    plt.close(fig)


def test_from_geodataframe_custom_title():
    import matplotlib.pyplot as plt
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME", n=50,
                                    title="My Custom Map")
    fig = ag.plot()
    plt.close(fig)
    assert ag.title == "My Custom Map"


def test_from_geodataframe_wrong_col_raises():
    gdf = _make_test_gdf()
    with pytest.raises(ValueError, match="not found"):
        AreaGrid.from_geodataframe(gdf, region_col="NONEXISTENT").build()


def test_from_geodataframe_wrong_type_raises():
    import pandas as pd
    df = pd.DataFrame({"NAME": ["A"], "x": [1]})
    with pytest.raises(TypeError, match="GeoDataFrame"):
        AreaGrid.from_geodataframe(df, region_col="NAME").build()


def test_from_geodataframe_repr():
    gdf = _make_test_gdf()
    ag = AreaGrid.from_geodataframe(gdf, region_col="NAME")
    assert "custom GeoDataFrame" in repr(ag)


def test_from_geodataframe_any_crs():
    """CRS other than WGS84 should work — reprojected to WGS84 then LAEA."""
    import geopandas as gpd
    import shapely
    # Two boxes in WGS84 reprojected to EPSG:3857 (Web Mercator)
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    def to_3857(lon0, lat0, lon1, lat1):
        x0, y0 = transformer.transform(lon0, lat0)
        x1, y1 = transformer.transform(lon1, lat1)
        return shapely.box(x0, y0, x1, y1)
    geoms = [to_3857(0, 0, 2, 2), to_3857(2, 0, 4, 2)]
    gdf = gpd.GeoDataFrame(
        {"region": ["A", "B"], "geometry": geoms},
        crs="EPSG:3857",
    )
    ag = AreaGrid.from_geodataframe(gdf, region_col="region", n=20)
    ag.build()
    assert ag.total_cells > 0
