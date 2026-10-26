from pathlib import Path

from examples import publish_arcgis_online as cli


def test_publication_specs_split_both_modes_into_two_services():
    specs = cli.publication_specs(country="ESP", mode="both", n=1000, title=None)

    assert [(spec.mode, spec.title) for spec in specs] == [
        ("area", "popgrid_ESP_area_n1000"),
        ("population", "popgrid_ESP_population_n1000"),
    ]


def test_publication_specs_suffix_custom_title_when_both_modes():
    specs = cli.publication_specs(country="ESP", mode="both", n=1000, title="Spain PopGrid")

    assert [spec.title for spec in specs] == [
        "Spain PopGrid_area",
        "Spain PopGrid_population",
    ]


def test_publication_specs_keep_single_mode_as_one_service():
    specs = cli.publication_specs(country="FRA", mode="population", n=500, title=None)

    assert len(specs) == 1
    assert specs[0].mode == "population"
    assert specs[0].title == "popgrid_FRA_population_n500"


def test_geojson_path_defaults_to_service_title_when_split():
    spec = cli.PublicationSpec(country="ESP", mode="area", n=1000, title="popgrid_ESP_area_n1000")

    assert cli.geojson_path_for(spec, explicit_path=None) == Path(
        "output/popgrid_ESP_area_n1000.geojson"
    )
