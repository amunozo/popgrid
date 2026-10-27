"""
Tests for popgrid.data — region loading and dissolve logic.
"""

import pytest
import geopandas as gpd

from popgrid.data import (
    KNOWN_DISSOLVE,
    RECOMMENDED_SETTINGS,
    REGION_LABELS,
    admin1_parent_map,
    load_country_regions,
    load_region_populations,
)
from popgrid.exceptions import CountryNotFoundError


# ── KNOWN_DISSOLVE ────────────────────────────────────────────────────────────

def test_known_dissolve_is_dict():
    assert isinstance(KNOWN_DISSOLVE, dict)
    assert len(KNOWN_DISSOLVE) > 0


def test_known_dissolve_keys_are_iso3():
    for key in KNOWN_DISSOLVE:
        assert len(key) == 3, f"Expected ISO3 key, got '{key}'"
        assert key.isupper(), f"Expected uppercase ISO3 key, got '{key}'"


# ── load_country_regions — basic ──────────────────────────────────────────────

def test_load_germany_returns_geodataframe():
    gdf = load_country_regions("DEU")
    assert isinstance(gdf, gpd.GeoDataFrame)


def test_load_germany_correct_columns():
    gdf = load_country_regions("DEU")
    assert "region_id" in gdf.columns
    assert "region_name" in gdf.columns
    assert "geometry" in gdf.columns


def test_load_germany_crs_is_wgs84():
    gdf = load_country_regions("DEU")
    assert gdf.crs is not None
    assert gdf.crs.to_epsg() == 4326


def test_load_germany_row_count():
    gdf = load_country_regions("DEU")
    # Natural Earth has exactly 16 Bundesländer for Germany
    assert len(gdf) == 16


def test_load_germany_no_null_geometries():
    gdf = load_country_regions("DEU")
    assert gdf["geometry"].notna().all()


def test_load_germany_no_empty_geometries():
    gdf = load_country_regions("DEU")
    assert gdf["geometry"].apply(lambda g: not g.is_empty).all()


def test_load_case_insensitive():
    gdf_upper = load_country_regions("DEU")
    gdf_lower = load_country_regions("deu")
    assert len(gdf_upper) == len(gdf_lower)


def test_load_invalid_iso_raises():
    with pytest.raises(CountryNotFoundError):
        load_country_regions("ZZZ")


# ── load_country_regions — dissolve ──────────────────────────────────────────

def test_spain_raw_has_52_rows():
    gdf = load_country_regions("ESP")
    assert len(gdf) == 52


def test_spain_dissolve_region_gives_19_ccaa():
    gdf = load_country_regions("ESP", dissolve_by="region")
    # 17 CCAA + Ceuta + Melilla
    assert len(gdf) == 19


def test_spain_dissolve_includes_ceuta_melilla():
    gdf = load_country_regions("ESP", dissolve_by="region")
    names = gdf["region_name"].tolist()
    assert "Ceuta" in names
    assert "Melilla" in names


def test_italy_dissolve_gives_20_regioni():
    gdf = load_country_regions("ITA", dissolve_by="region")
    assert len(gdf) == 20


def test_france_dissolve_gives_18_regions():
    gdf = load_country_regions("FRA", dissolve_by="region")
    assert len(gdf) == 18


def test_dissolve_result_has_correct_columns():
    gdf = load_country_regions("ESP", dissolve_by="region")
    assert set(gdf.columns) == {"region_id", "region_name", "geometry"}


def test_dissolve_result_no_null_geometries():
    gdf = load_country_regions("ESP", dissolve_by="region")
    assert gdf["geometry"].notna().all()


def test_dissolve_nonexistent_column_falls_back_gracefully():
    # Should warn but return raw rows, not raise
    gdf = load_country_regions("DEU", dissolve_by="nonexistent_col")
    assert len(gdf) == 16  # Falls back to raw admin-1


def test_dissolve_empty_column_falls_back_gracefully():
    # Germany has no 'region' values — should warn and return raw rows
    gdf_raw = load_country_regions("DEU")
    gdf_dissolve = load_country_regions("DEU", dissolve_by="region")
    assert len(gdf_raw) == len(gdf_dissolve)


# ── Spain — default level is provinces, CCAA via dissolve ────────────────────

def test_spain_recommended_settings_use_provinces():
    assert RECOMMENDED_SETTINGS["ESP"]["dissolve_by"] is None
    assert REGION_LABELS["ESP"] == "Provinces"
    # CCAA remain available as an explicit dissolve
    assert KNOWN_DISSOLVE["ESP"] == "region"


def test_spain_default_load_gives_52_provinces():
    gdf = load_country_regions("ESP")
    assert len(gdf) == 52
    assert "Madrid" in gdf["region_name"].tolist()
    assert "Barcelona" in gdf["region_name"].tolist()


def test_spain_province_populations_sum_to_national_total(caplog):
    names = load_country_regions("ESP")["region_name"].tolist()
    pops = load_region_populations("ESP", names)

    assert len(pops) == 52
    assert all(p > 0 for p in pops.values())
    # Every province is in population.csv — no mean-value fallback is used.
    assert "No population data" not in caplog.text
    assert sum(pops.values()) == 47_400_798


def test_spain_province_populations_aggregate_to_ccaa():
    names = load_country_regions("ESP")["region_name"].tolist()
    pops = load_region_populations("ESP", names)
    parent = admin1_parent_map("ESP")

    ccaa: dict[str, int] = {}
    for name in names:
        ccaa[parent[name]] = ccaa.get(parent[name], 0) + pops[name]

    # 17 CCAA + Ceuta + Melilla, matching INE Census 2021 CCAA totals.
    assert len(ccaa) == 19
    assert ccaa["Andalucía"] == 8_484_804
    assert ccaa["Cataluña"] == 7_749_896
    assert ccaa["Canary Is."] == 2_178_924
    assert ccaa["País Vasco"] == 2_212_628
    assert ccaa["Madrid"] == 6_726_640
    assert ccaa["Ceuta"] == 84_071
    assert ccaa["Melilla"] == 86_450


def test_spain_dissolve_region_aggregates_provinces_up_to_ccaa():
    gdf = load_country_regions("ESP", dissolve_by="region")
    names = gdf["region_name"].tolist()
    pops = load_region_populations(
        "ESP", names, name_to_parent=admin1_parent_map("ESP")
    )

    assert len(names) == 19
    assert len(pops) == 19
    assert pops["Andalucía"] == 8_484_804
    assert pops["Cataluña"] == 7_749_896
    assert pops["Madrid"] == 6_726_640
    assert sum(pops.values()) == 47_400_798
