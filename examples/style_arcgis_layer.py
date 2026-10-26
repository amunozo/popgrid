"""
Apply popgrid thematic symbology to an existing ArcGIS Online Feature Layer.

Examples
--------
Find by item title:
    python examples/style_arcgis_layer.py --profile popgrid_agol --title popgrid_ESP_both_n1000

Use a known item id:
    python examples/style_arcgis_layer.py --profile popgrid_agol --item-id abc123...
"""

from __future__ import annotations

import argparse

from popgrid.arcgis import apply_region_renderer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply region/color_hex thematic symbology to a popgrid Feature Layer."
    )
    parser.add_argument("--profile", help="ArcGIS API for Python profile name.")
    parser.add_argument("--item-id", help="ArcGIS Online item id for the Feature Layer.")
    parser.add_argument("--title", help="Exact or searchable Feature Layer title.")
    parser.add_argument("--layer-index", type=int, default=0, help="Layer index in the item.")
    return parser.parse_args()


def find_item(gis, item_id: str | None, title: str | None):
    if item_id:
        item = gis.content.get(item_id)
        if item is None:
            raise RuntimeError(f"No item found with id {item_id!r}.")
        return item

    if not title:
        raise RuntimeError("Pass either --item-id or --title.")

    results = gis.content.search(
        query=f'title:"{title}"',
        item_type="Feature Layer",
        max_items=10,
    )
    if not results:
        raise RuntimeError(f"No Feature Layer found matching title {title!r}.")

    exact = [item for item in results if item.title == title]
    return exact[0] if exact else results[0]


def main() -> int:
    args = parse_args()

    from arcgis.gis import GIS

    gis = GIS(profile=args.profile) if args.profile else GIS("home")
    item = find_item(gis, args.item_id, args.title)
    layer = item.layers[args.layer_index]
    sdf = layer.query().sdf
    result = apply_region_renderer(item, sdf, layer_index=args.layer_index)
    print(f"Styled layer: {item.title}")
    print(f"Update result: {result}")
    print(item.homepage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
