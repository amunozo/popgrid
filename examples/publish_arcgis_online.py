"""
Publish popgrid blocks to ArcGIS Online as a Hosted Feature Layer.

Examples
--------
Preview the datasets locally:
    python examples/publish_arcgis_online.py ESP --n 200 --dry-run

Publish area and population as separate Hosted Feature Layers:
    python examples/publish_arcgis_online.py ESP --n 1000 --profile my_agol_profile
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from popgrid.arcgis import build_country_feature_layer_gdf, publish_hosted_feature_layer


@dataclass(frozen=True)
class PublicationSpec:
    country: str
    mode: str
    n: int
    title: str


def publication_specs(
    country: str,
    mode: str,
    n: int,
    title: str | None,
) -> list[PublicationSpec]:
    modes = ("area", "population") if mode == "both" else (mode,)
    specs: list[PublicationSpec] = []
    for selected_mode in modes:
        service_title = title
        if service_title is None:
            service_title = f"popgrid_{country}_{selected_mode}_n{n}"
        elif len(modes) > 1:
            service_title = f"{service_title}_{selected_mode}"
        specs.append(
            PublicationSpec(
                country=country,
                mode=selected_mode,
                n=n,
                title=service_title,
            )
        )
    return specs


def geojson_path_for(spec: PublicationSpec, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return Path(f"output/{spec.title}.geojson")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish popgrid blocks to ArcGIS Online as a Hosted Feature Layer."
    )
    parser.add_argument("country", help="ISO3 country code, e.g. ESP, DEU, FRA.")
    parser.add_argument("--n", type=int, default=1000, help="Target block count per mode.")
    parser.add_argument(
        "--mode",
        choices=["area", "population", "both"],
        default="both",
        help="Which popgrid mode to publish.",
    )
    parser.add_argument(
        "--title",
        help="Hosted Feature Layer title. Defaults to popgrid_<COUNTRY>_<MODE>_n<N>.",
    )
    parser.add_argument("--folder", help="ArcGIS Online folder name.")
    parser.add_argument(
        "--profile",
        help="ArcGIS API for Python profile name. If omitted, uses GIS('home').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the GeoDataFrame and write GeoJSON without publishing.",
    )
    parser.add_argument(
        "--geojson",
        default=None,
        help="Optional local GeoJSON path for inspection before publishing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    country = args.country.upper()
    specs = publication_specs(country=country, mode=args.mode, n=args.n, title=args.title)

    prepared = []
    for spec in specs:
        gdf = build_country_feature_layer_gdf(spec.country, modes=(spec.mode,), n=spec.n)
        geojson_path = geojson_path_for(
            spec,
            explicit_path=args.geojson if len(specs) == 1 else None,
        )
        geojson_path.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(geojson_path, driver="GeoJSON")
        prepared.append((spec, gdf))
        print(f"Prepared {len(gdf):,} {spec.mode} popgrid blocks -> {geojson_path}")

    if args.dry_run:
        print("Dry run only; nothing published to ArcGIS Online.")
        return 0

    from arcgis.gis import GIS

    gis = GIS(profile=args.profile) if args.profile else GIS("home")
    for spec, gdf in prepared:
        item = publish_hosted_feature_layer(
            gdf,
            title=spec.title,
            gis=gis,
            folder=args.folder,
        )
        print(f"Published Hosted Feature Layer: {item.title}")
        print(item.homepage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
