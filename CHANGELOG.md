# Changelog

All notable changes to popgrid are documented here.

## [Unreleased]

### Added
- **Optional ArcGIS Online publishing** (`popgrid.arcgis`), contributed by
  [@aitorcalero](https://github.com/aitorcalero) in #2: convert popgrid blocks to
  WGS84 GeoDataFrames, publish them as Hosted Feature Layers with region colours,
  and two example CLIs. Install with `pip install "popgrid[arcgis]"`; normal use
  does not need ArcGIS.

### Added
- **`PopGrid(layout="compact")`**: tiles the cartogram outline with compact blobs, one per
  region, via an exact-capacity optimal assignment. Drops literal shared borders, but
  avoids the one-cell rings a border-preserving cartogram forces around a dominant region
  (Madrid among Spain's provinces). Default for Spain; `--layout` flag in `generate.py`.
  Adds a `scipy` dependency.

### Fixed
- PopGrid now gives every region **exactly** its population quota of blocks. Enclosed
  regions used to end up far off (Cuenca 11 blocks instead of 4, Madrid 107 instead of 142).

### Changed
- **Spain now defaults to its 52 provinces.** `AreaGrid("ESP")` / `PopGrid("ESP")`
  render provinces (INE Census 2021 province populations) instead of the 19 autonomous
  communities and cities. Pass `dissolve_by="region"` to keep the coarser
  17 CCAA + Ceuta + Melilla view (province populations are summed up).
- **Spanish population figures corrected.** The previous CCAA rows were labelled
  "INE Census 2021" but were Padrón figures from mixed years (2016–2021). They are
  now the actual INE Census 2021 province figures (national total 47,400,798).
- `publish_hosted_feature_layer` now requires an authenticated `GIS` and raises a
  clear error instead of falling back to an anonymous session that cannot publish.

## [0.4.0] - 2026-06-03

Custom data release. `from_geodataframe` now drives both `AreaGrid` and
`PopGrid`, so any GeoDataFrame — a city's districts, a custom shapefile, a
PostGIS query — can be turned into an area or population cartogram, not just the
24 bundled countries.

### Added
- **`PopGrid.from_geodataframe(gdf, region_col=..., weight_col=...)`.** Sizes
  each region by a numeric column you supply (e.g. population), so population
  cartograms work on arbitrary polygon data. `AreaGrid.from_geodataframe`
  continues to weight by land area and ignores `weight_col`.
- **Barcelona worked example** (`examples/barcelona.py` +
  `examples/data/barcelona_districts.geojson`): the city's 10 districts by land
  area vs population, built entirely from a custom GeoDataFrame. District
  boundaries from Ajuntament de Barcelona open data; population from the 2023
  municipal register.

### Changed
- `_prepare_custom_gdf` carries an optional weight column through as
  `region_weight`; `PopGrid.build` uses it when present and otherwise falls back
  to the bundled population table for the 24 known countries.

### Notes
- Calling `PopGrid.from_geodataframe` without `weight_col` raises a clear error
  pointing you to `AreaGrid.from_geodataframe` for an area-weighted map.

## [0.3.0] - 2026-06-03

The population release. `PopGrid` turns a country into a block cartogram where
every cell is an equal share of the population, alongside the existing
land-area `AreaGrid`.

### Added
- **`PopGrid` — population-weighted maps.** Regions are deformed with a
  Dougenik rubber-sheet cartogram so each region's area is proportional to its
  population, then rasterised to a grid (each cell = an equal share of the
  national population, allocated by the Hamilton / largest-remainder method).
  Population data for 24 countries ships in `popgrid/resources/population.csv`.
- **Stable region colours across grid modes.** Colours are assigned from
  mode-invariant geography (real border adjacency + land area), so an
  `AreaGrid` and a `PopGrid` of the same country share an identical region
  colour map and can be compared region by region (e.g. Madrid keeps its colour
  while growing from land area to population).
- **Adjacency- and size-aware colouring.** Neighbouring regions are always
  visually distinct, and the largest regions are pushed apart in colour space so
  big blocks never share a hue.
- **Unified `generate.py` CLI.** One script generates population *or* land-area
  maps (`--mode pop|area`), for one country, several, or `ALL`, with full
  styling flags (palette, scope, background, labels, etc.). Replaces the old
  single-country `examples/country.py`.
- **Per-country segmentation** for over-granular countries: Japan renders its 9
  traditional regions (Kanto, Kinki, …) rather than 47 prefectures, the UK its
  16 sub-regions rather than 232 districts. Population is aggregated from the
  fine source level up to the parent regions automatically.

### Fixed
- **Gap-free rasterisation.** Cells in the seams between deformed regions are now
  filled (interior holes of the deformed outline are closed before gridding),
  and remaining enclosed pockets up to 50 cells are filled count-neutrally so
  block totals don't drift. All 24 bundled countries build with zero interior
  holes except China.
- **Grid-aligned territories.** Detached and inline territories (Spain's Ceuta,
  Melilla, Balearic Islands, etc.) now snap to the mainland's global lattice
  instead of sitting up to half a cell off; re-anchoring is a rigid translation
  that preserves each territory's internal spacing.
- **Build performance / no more hangs.** Pathologically high-vertex coastlines
  (e.g. UK 'Highlands and Islands', 4,200+ vertices) are simplified before
  deformation, and the cartogram stops iterating if geometry complexity balloons
  from over-inflation. UK went from a 260s+ timeout to a few seconds. Normal
  adjacent regions are never simplified, so quotas stay correct.

### Changed
- **Japan layout:** Hokkaido renders inline (it was being pushed into a detached
  panel) and Okinawa moves to its own panel, via a higher inline margin (near
  islands like Hokkaido and Corsica sit inline; far territories like the
  Canaries, Hawaii, Alaska and French overseas stay in panels) plus a tighter
  per-country cluster distance for Japan.

### Known limitations
- **China-class geography.** Countries with an extreme population-density
  gradient and long, thin provinces (China is the clearest case) tear the
  contiguous cartogram during deformation, leaving a central void and
  disconnected-looking pieces. They build without error but are not as clean as
  compact, evenly-populated countries. A diffusion-based cartogram
  (Gastner-Newman) would be needed to handle them well.
- **Population currency.** Bundled population figures are a fixed snapshot, not
  live data.

## [0.2.0]

### Added
- `AreaGrid.from_geodataframe()` — build a land-area grid from any GeoDataFrame
  (custom shapefiles / GeoJSON), not just bundled Natural Earth countries.
- Configurable `title`, `subtitle`, and `data_source` at construction.

## [0.1.0]

### Added
- Initial release: `AreaGrid` land-area tiled maps for Natural Earth countries,
  qualitative palettes, panel insets for detached territories, and CLI tools.
