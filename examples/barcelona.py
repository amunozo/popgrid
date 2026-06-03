"""
Barcelona by district — land area vs population, from a custom GeoDataFrame.

Shows popgrid working on data that is NOT a bundled country: any GeoDataFrame
with polygons and a name column works via ``from_geodataframe``. PopGrid adds a
``weight_col`` to size regions by your own numeric column (here, population).

Data: examples/data/barcelona_districts.geojson
  - district boundaries: Ajuntament de Barcelona (open data)
  - population: municipal register (padró), 2023, approximate
"""

from pathlib import Path

import geopandas as gpd

from popgrid import AreaGrid, PopGrid

DATA = Path(__file__).parent / "data" / "barcelona_districts.geojson"


def main() -> None:
    gdf = gpd.read_file(DATA)

    # Land-area cartogram — sizes each district by its physical area.
    ag = AreaGrid.from_geodataframe(gdf, region_col="district")
    ag.plot(
        title="Land Area of Barcelona",
        subtitle="Every block = 0.1% of city land area",
        data_source="Ajuntament de Barcelona · popgrid",
    ).savefig("barcelona_area.png", dpi=150, bbox_inches="tight")

    # Population cartogram — sizes each district by the 'population' column.
    pg = PopGrid.from_geodataframe(gdf, region_col="district", weight_col="population")
    pg.plot(
        title="Population of Barcelona",
        subtitle="Every block = 0.1% of city population",
        data_source="Ajuntament de Barcelona (padró 2023) · popgrid",
    ).savefig("barcelona_population.png", dpi=150, bbox_inches="tight")

    print("Wrote barcelona_area.png and barcelona_population.png")


if __name__ == "__main__":
    main()
