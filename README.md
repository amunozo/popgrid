# popgrid

**Tiled grid maps for Python: population and land area.**

`popgrid` turns any country into a square-grid map where every cell represents an equal share of the country's **population** (or its land area). Regions are sized by their share, automatically colored so neighbours and the largest regions stay distinct, labeled, and geographically detached territories are shown as panel insets.

Inspired by [@Civixplorer](https://twitter.com/Civixplorer).

---

## Installation

```bash
pip install -e .
```

Natural Earth boundary data (~14 MB) downloads automatically on first use and caches at `~/.popgrid/cache/`. Population data for 24 countries ships with the package.

---

## Quick start

Population map (each block = 0.1% of the national population):

```python
from popgrid import PopGrid

pg = PopGrid("ESP", n=1000, dissolve_by="auto")
pg.build()
pg.save("spain.png", dpi=200)
```

Land-area map (each block = an equal share of land area):

```python
from popgrid import AreaGrid

ag = AreaGrid("DEU", dissolve_by="auto")
ag.build()
ag.save("germany.png", dpi=200)
```

Batch-generate population maps from the command line:

```bash
python generate.py                 # default set: ESP DEU ITA FRA GBR
python generate.py ESP DEU JPN     # specific countries
python generate.py ALL             # every country with bundled population data
python generate.py ITA --n 1500    # more blocks = finer detail
python generate.py ESP --out maps  # custom output folder
```

PNGs land in `./out/` (or `--out`). `dissolve_by="auto"` picks the right administrative level per country.

---

## Classes

### `PopGrid` — population-weighted tiled map

Each cell represents an equal share of the country's **population**, allocated proportionally via the Hamilton (largest-remainder) quota method.

```python
from popgrid import PopGrid

pg = PopGrid(
    iso3="ESP",            # ISO 3166-1 alpha-3 country code
    n=1000,                # target number of cells (default: 1000)
    dissolve_by="auto",    # group admin-1 rows into parent regions
    palette="qual",        # "qual", "databites", "bold", or a list of hex
)
pg.build()
fig = pg.plot(title="Population of Spain, Visualised")
pg.save("spain.png", dpi=200)
```

### `AreaGrid` — land-area tiled map

Each cell represents an equal share of the country's **land area**. Same API, same options.

```python
from popgrid import AreaGrid

ag = AreaGrid("DEU", dissolve_by="auto")
ag.build()
ag.save("germany.png", dpi=200)
```

---

## Parameters

### Constructor `(iso3, n, dissolve_by, palette, cluster_distance_km)`

| Parameter | Default | Description |
|---|---|---|
| `iso3` | required | ISO 3166-1 alpha-3 code (`"ESP"`, `"USA"`, `"DEU"`, …) |
| `n` | `1000` | Target number of cells |
| `dissolve_by` | `"auto"` | Column to dissolve admin-1 rows into parent regions. `"auto"` uses `RECOMMENDED_SETTINGS`. |
| `palette` | `"qual"` | Named palette or list of hex colours |
| `cluster_distance_km` | `200` | Max distance (km) to treat landmasses as one group. `"auto"` applies per-country overrides. |

### `.plot(...)` / `.save(path, dpi)`

| Parameter | Default | Description |
|---|---|---|
| `background_style` | `"grid"` | `"grid"` light grey lines · `"solid"` filled rectangle · `"none"` plain |
| `show_region_borders` | `True` | Draw a black outline around each region |
| `scope` | `"all"` | `"all"` · `"islands"` · `"mainland"` |
| `title` | auto | Override the auto-generated title |
| `subtitle` | auto | Override the auto-generated subtitle |
| `data_source` | auto | Attribution shown bottom-left |

---

## CLI

Batch population maps (root script):

```bash
python generate.py [ISO3 ...|ALL] [--n N] [--out DIR] [--dpi N]
```

Single land-area map with full styling options:

```bash
python examples/country.py ESP
python examples/country.py USA --background grid --dpi 300
python examples/country.py FRA --scope mainland --save france.png
```

Inspect Natural Earth data for any country:

```bash
python examples/inspect_country.py ESP
```

---

## Supported countries

Population data and tested `dissolve_by="auto"` settings ship for 24 countries:

ARG, AUS, BEL, BRA, CAN, CHE, CHN, DEU, ESP, FRA, GBR, ITA, JPN, KOR, MEX, NLD, NOR, PHL, POL, PRT, SWE, TUR, USA, ZAF.

Segmentation is chosen per country for legibility, for example:

| Country | Regions shown |
|---|---|
| Spain (ESP) | 19 Autonomous Communities |
| Germany (DEU) | 16 States |
| France (FRA) | 18 Regions (+ overseas in panels) |
| Italy (ITA) | 20 Regions |
| USA | 51 States (+ Alaska, Hawaii in panels) |
| Japan (JPN) | 9 traditional regions (Kanto, Kinki, …) |
| United Kingdom (GBR) | 16 sub-regions |

Any country in Natural Earth admin-1 works for land-area maps; `dissolve_by="auto"` falls back gracefully for unconfigured countries.

---

## Known limitations

- **China-class geography.** Countries with an extreme population-density gradient and long, thin provinces (China is the clearest case) tear the contiguous cartogram during deformation, leaving a central void and disconnected-looking pieces. `popgrid` builds them without error, but they are not as clean as compact, evenly-populated countries. A diffusion-based cartogram (Gastner-Newman) would be needed to handle them well.
- **Population currency.** Bundled population figures are a fixed snapshot (see `popgrid/resources/population.csv`), not live data.

---

## Data source

**Natural Earth** `ne_10m_admin_1_states_provinces` (public domain), auto-downloaded on first use and cached at `~/.popgrid/cache/`. Population figures are bundled in `popgrid/resources/population.csv`.

---

## Roadmap

| Version | Feature |
|---|---|
| **v0.3.0** ✅ | `PopGrid` population maps, `AreaGrid` land-area maps, 24 countries, size-aware colors, per-country segmentation, batch CLI |
| v1.0.0 🔜 | PyPI publish (`pip install popgrid`), docs site, CI |
| later 🔜 | Custom shapefiles via `from_geodataframe()`; diffusion cartogram for China-class countries |

---

## Development

```bash
git clone https://github.com/databites-tech/popgrid.git
cd popgrid
pip install -e ".[dev]"
python -m pytest tests/ -q
```

---

## License

MIT © Josep Ferrer — [databites.tech](https://databites.tech)
