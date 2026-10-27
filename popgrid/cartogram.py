"""
Contiguous area cartogram (Dougenik–Chrisman–Niemeyer, 1985).

Why this module exists
----------------------
The grid generator (:func:`popgrid.grid.generate_cells`) rasterises whatever
polygon it is given and assigns each cell to the region that contains it.  If
the region polygons are left in their *geographic* shape, the country outline
is locked to geography — a populous-but-tiny region like Madrid can never grow
past its borders, and the map looks like a plain geographic map regardless of
population.

A **contiguous area cartogram** deforms the region polygons so that each
region's *area* becomes proportional to a target value (population), while
preserving:

* relative position (north stays north),
* adjacency / topology (neighbours stay neighbours),
* each region's recognisable silhouette (it is the same polygon, stretched).

Feeding the deformed polygons into ``generate_cells`` then yields, for free, a
grid in which every region receives population-proportional cells *and* keeps
its shape.  The country outline distorts — dense regions bulge, sparse regions
pinch — which is precisely the Civixplorer "population visualised" look.

Algorithm (Dougenik, Chrisman & Niemeyer 1985, "rubber-sheet" forces)
---------------------------------------------------------------------
For each iteration:

1. For every region *j* compute current area ``A_j``, centroid ``C_j`` and the
   equal-area-circle radius ``R_j = sqrt(A_j / π)``.
2. Compute the desired area ``T_j`` (value share × total area, so total area is
   conserved) and desired radius ``D_j = sqrt(T_j / π)``.  The region's "mass"
   is ``m_j = D_j − R_j`` (positive ⇒ must grow, negative ⇒ must shrink).
3. A global damping term ``force_reduction = 1 / (1 + mean_size_error)`` keeps
   the deformation stable.
4. Every boundary vertex *v* is displaced by the sum of radial forces from all
   regions::

       d = |v − C_j|
       F_j =  m_j · R_j / d        if d >  R_j   (decays with distance outside)
       F_j =  m_j · d  / R_j       if d <= R_j   (zero at the centroid, linear inside)
       v  +=  Σ_j  F_j · (v − C_j)/d · force_reduction

Repeating drives each region's area towards its target while the whole sheet
stays connected.  This is the same family of algorithm used by Tilegrams /
cartogram.js under the hood, implemented here in pure NumPy + Shapely so the
library keeps a clean ``pip install`` with no compiled dependencies.
"""

from __future__ import annotations

import heapq
import logging

import geopandas as gpd
import numpy as np
import shapely

logger = logging.getLogger(__name__)


def _region_stats(
    gdf: gpd.GeoDataFrame, values: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """Return (cx, cy, radius, mass, force_reduction) for the current geometries."""
    areas = gdf.geometry.area.to_numpy()
    areas = np.maximum(areas, 1e-9)
    total_area = areas.sum()

    cents = gdf.geometry.centroid
    cx = cents.x.to_numpy()
    cy = cents.y.to_numpy()

    radius = np.sqrt(areas / np.pi)

    # Desired area: value share of the (conserved) total area.
    total_value = values.sum()
    desired_area = values / total_value * total_area
    desired_radius = np.sqrt(desired_area / np.pi)

    mass = desired_radius - radius  # >0 grow, <0 shrink

    # Global damping from the mean size error (Dougenik's force-reduction term).
    size_error = np.maximum(areas, desired_area) / np.minimum(areas, desired_area)
    mean_error = float(np.mean(size_error))
    force_reduction = 1.0 / (1.0 + mean_error)

    return cx, cy, radius, mass, force_reduction


def _make_displacer(cx, cy, radius, mass, frf):
    """
    Build a vectorised coordinate-displacement function for ``shapely.transform``.

    The returned closure takes an ``(N, 2)`` array of vertices and returns the
    displaced ``(N, 2)`` array, applying the summed radial forces from every
    region (all region arrays are fixed for the iteration).
    """

    def displace(coords: np.ndarray) -> np.ndarray:
        coords = np.asarray(coords, dtype=float)
        px = coords[:, 0:1]  # (N, 1)
        py = coords[:, 1:2]
        dx = px - cx[np.newaxis, :]  # (N, J)
        dy = py - cy[np.newaxis, :]
        dist = np.hypot(dx, dy)  # (N, J)
        dist_safe = np.maximum(dist, 1e-9)

        # Force magnitude per region: decays outside, linear-to-zero inside.
        outside = dist > radius[np.newaxis, :]
        f_out = mass[np.newaxis, :] * radius[np.newaxis, :] / dist_safe
        f_in = mass[np.newaxis, :] * dist_safe / np.maximum(radius[np.newaxis, :], 1e-9)
        force = np.where(outside, f_out, f_in)  # (N, J)

        ux = dx / dist_safe
        uy = dy / dist_safe
        new_x = coords[:, 0] + frf * np.sum(force * ux, axis=1)
        new_y = coords[:, 1] + frf * np.sum(force * uy, axis=1)
        return np.column_stack([new_x, new_y])

    return displace


def dougenik_cartogram(
    gdf: gpd.GeoDataFrame,
    values: dict[str, float] | np.ndarray,
    value_key: str = "region_name",
    iterations: int = 20,
    max_step_fraction: float = 0.35,
) -> gpd.GeoDataFrame:
    """
    Deform region polygons so area becomes proportional to ``values``.

    Parameters
    ----------
    gdf:
        Projected (equal-area, metres) GeoDataFrame with a ``region_name``
        column and polygon geometries.
    values:
        Either a ``{region_name: value}`` mapping or an array aligned with
        ``gdf`` rows.  Values are the target weights (population).
    value_key:
        Column used to look up ``values`` when a dict is passed.
    iterations:
        Number of rubber-sheet iterations.  15–25 is plenty for ~20 regions.
    max_step_fraction:
        Per-iteration displacement is clamped to this fraction of the mean
        region radius to prevent self-intersection during large inflations
        (e.g. Madrid ~9×).  Lower = more stable but slower convergence.

    Returns
    -------
    A copy of ``gdf`` with deformed geometries.  Region order, ids and names
    are preserved; only ``geometry`` changes.
    """
    out = gdf.copy().reset_index(drop=True)

    if isinstance(values, dict):
        val = np.array(
            [max(float(values.get(str(name), 0.0)), 0.0) for name in out[value_key]],
            dtype=float,
        )
    else:
        val = np.asarray(values, dtype=float)
    val = np.maximum(val, 1e-9)

    # Vertex budget: if make_valid starts manufacturing geometry (severe
    # self-intersection from over-inflation), its cost explodes super-linearly.
    # Stop iterating once complexity balloons — the layout is already a good
    # starting point and fit_quota fixes the exact cell counts downstream.
    try:
        _start_verts = int(shapely.get_num_coordinates(out.geometry.values).sum())
    except Exception:  # noqa: BLE001
        _start_verts = 0
    _vert_budget = max(2000, _start_verts * 4)
    _prev_geoms = list(out.geometry)

    for it in range(iterations):
        cx, cy, radius, mass, frf = _region_stats(out, val)

        # Clamp the step so a single iteration cannot fold the sheet.
        step_cap = max_step_fraction * float(np.mean(radius))
        mass_clamped = np.clip(mass, -step_cap, step_cap)

        displace = _make_displacer(cx, cy, radius, mass_clamped, frf)

        new_geoms = [
            shapely.transform(geom, displace, include_z=False)
            for geom in out.geometry
        ]
        # Repair any self-intersection introduced by large local displacement.
        new_geoms = [shapely.make_valid(g) for g in new_geoms]
        out["geometry"] = gpd.GeoSeries(new_geoms, crs=out.crs)

        # Divergence guard: if complexity has ballooned past the budget, the
        # sheet is folding on itself — revert to the last clean state and stop.
        try:
            nverts = int(shapely.get_num_coordinates(out.geometry.values).sum())
        except Exception:  # noqa: BLE001
            nverts = 0
        if nverts > _vert_budget:
            logger.debug(
                "cartogram stop at iter %d/%d — vertices %d exceed budget %d",
                it + 1, iterations, nverts, _vert_budget,
            )
            out["geometry"] = gpd.GeoSeries(_prev_geoms, crs=out.crs)
            break
        _prev_geoms = list(out.geometry)

        # Convergence diagnostic.
        areas = out.geometry.area.to_numpy()
        desired = val / val.sum() * areas.sum()
        err = np.maximum(areas, desired) / np.minimum(np.maximum(areas, 1e-9), desired)
        logger.debug(
            "cartogram iter %2d/%d — mean area error %.3f, max %.3f",
            it + 1, iterations, float(err.mean()), float(err.max()),
        )

    return out


def area_error(gdf: gpd.GeoDataFrame, values: dict[str, float], value_key="region_name"):
    """Return per-region (target_share, actual_share, ratio) for validation."""
    areas = gdf.geometry.area.to_numpy()
    val = np.array([float(values.get(str(n), 0.0)) for n in gdf[value_key]])
    tshare = val / val.sum()
    ashare = areas / areas.sum()
    ratio = np.where(ashare > 0, tshare / ashare, np.nan)
    return list(zip(gdf[value_key].tolist(), tshare, ashare, ratio))


# ── Population sizing + exact-quota fitting (used by PopGrid) ──────────────────

def scale_to_area(gdf: gpd.GeoDataFrame, target_area: float) -> gpd.GeoDataFrame:
    """
    Uniformly scale every geometry about the group's centroid so the total
    area becomes ``target_area``.  Shape is preserved exactly (similarity
    transform); only size changes.

    Used to size a *detached* landmass (island, enclave) to its population
    quota before rasterising — the population analogue of leaving it at its
    geographic land area.  Mainland regions get their sizing from the
    cartogram instead; this is for single-region or detached landmasses
    that should keep their literal silhouette.
    """
    import shapely.affinity as aff

    cur = float(gdf.geometry.area.sum())
    if cur <= 0 or target_area <= 0:
        return gdf.copy()
    factor = (target_area / cur) ** 0.5
    union = gdf.geometry.union_all()
    ox, oy = float(union.centroid.x), float(union.centroid.y)
    out = gdf.copy()
    out["geometry"] = [
        aff.scale(g, xfact=factor, yfact=factor, origin=(ox, oy))
        for g in out.geometry
    ]
    return out


# ── Compact layout (discrete power diagram) ────────────────────────────────────

def _repair_fragments(
    cell_x: np.ndarray,
    cell_y: np.ndarray,
    assigned: np.ndarray,
    seed_x: np.ndarray,
    seed_y: np.ndarray,
    caps: np.ndarray,
    cell_size: float,
) -> np.ndarray:
    """
    Remove disconnected region fragments from a compact assignment.

    The linear-sum assignment treats cells independently, so a region with a
    small quota can occasionally receive a cell detached from its main blob.
    This pass swaps one stray cell of a fragmented region ``R`` with one cell
    of an adjacent region ``N`` that touches ``R``'s main component: ``R``
    becomes connected, ``N`` stays connected, and both keep their exact cell
    counts.  Candidates are ranked by the increase in the squared cell→seed
    cost, so the assignment's original objective remains the primary one.
    """
    n = len(cell_x)
    minx, miny = float(cell_x.min()), float(cell_y.min())

    def lattice_key(i: int) -> tuple[int, int]:
        return (
            round((cell_x[i] - minx) / cell_size),
            round((cell_y[i] - miny) / cell_size),
        )

    pos = {lattice_key(i): i for i in range(n)}
    nbr_cache: dict[int, list[int]] = {}

    def neighbours(i: int) -> list[int]:
        if i not in nbr_cache:
            ki, kj = lattice_key(i)
            found = []
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                j = pos.get((ki + di, kj + dj))
                if j is not None:
                    found.append(j)
            nbr_cache[i] = found
        return nbr_cache[i]

    def components(region: int, state: np.ndarray) -> list[list[int]]:
        keys = [i for i in range(n) if state[i] == region]
        key_set = set(keys)
        seen: set[int] = set()
        comps: list[list[int]] = []
        for start in keys:
            if start in seen:
                continue
            seen.add(start)
            stack = [start]
            comp = []
            while stack:
                u = stack.pop()
                comp.append(u)
                for v in neighbours(u):
                    if v in key_set and v not in seen:
                        seen.add(v)
                        stack.append(v)
            comps.append(comp)
        return comps

    def sq_cost(i: int, region: int) -> float:
        return (cell_x[i] - seed_x[region]) ** 2 + (cell_y[i] - seed_y[region]) ** 2

    state = assigned.copy()
    for _ in range(100):
        moved = False
        for r in range(len(caps)):
            if caps[r] <= 1:
                continue
            comps = components(r, state)
            if len(comps) <= 1:
                continue
            main = max(comps, key=len)
            main_set = set(main)
            strays = [i for comp in comps if comp is not main for i in comp]
            # Work on the stray cell closest to the region's main blob.
            c = min(
                strays,
                key=lambda i: min(
                    abs(cell_x[i] - cell_x[j]) + abs(cell_y[i] - cell_y[j])
                    for j in main
                ),
            )
            best = None
            for rr in {state[j] for j in neighbours(c) if state[j] != r}:
                # The receiving region must itself be connected; adding c next
                # to one of its cells then keeps it connected.
                if len(components(rr, state)) > 1:
                    continue
                for d in range(n):
                    if state[d] != rr or not any(
                        j in main_set for j in neighbours(d)
                    ):
                        continue
                    # Removing d must not split rr.
                    state[d] = -1
                    rest = [i for i in range(n) if state[i] == rr]
                    ok = True
                    if rest:
                        seen = {rest[0]}
                        stack = [rest[0]]
                        while stack:
                            u = stack.pop()
                            for v in neighbours(u):
                                if state[v] == rr and v not in seen:
                                    seen.add(v)
                                    stack.append(v)
                        ok = len(seen) == len(rest)
                    state[d] = rr
                    # c must touch rr somewhere other than d, or the swap
                    # leaves c as a detached piece of rr.
                    if ok and rest and not any(
                        state[j] == rr and j != d for j in neighbours(c)
                    ):
                        ok = False
                    if not ok:
                        continue
                    delta = (
                        sq_cost(c, rr) + sq_cost(d, r)
                        - sq_cost(c, r) - sq_cost(d, rr)
                    )
                    if best is None or delta < best[0]:
                        best = (delta, d, rr)
            if best is not None:
                _, d, rr = best
                state[c] = rr
                state[d] = r
                moved = True
                break
        if not moved:
            break
    return state


def compact_assign(
    shaped: gpd.GeoDataFrame,
    outline,
    quotas: dict[str, int],
    cell_size: float,
    landmass_id: int,
    lloyd_iterations: int = 4,
    smooth_cells: float = 0.0,
) -> gpd.GeoDataFrame:
    """
    Lay a landmass out as compact population blobs instead of a squeezed
    contiguous cartogram.

    A border-preserving cartogram has to inflate a dense region by pushing its
    neighbours aside, so a dominant city-province can become a disc surrounded
    by one-cell rings.  This alternative keeps the country's real silhouette
    and region positions but drops the literal borders: it tiles the outline
    it is given (PopGrid passes the real, undeformed one) with exactly ``quotas[region]`` cells per region and arranges them
    as a discrete power diagram, which yields compact, convex-ish blobs.

    Method
    ------
    1. **Silhouette cells.**  Candidate centres tile the outline's
       bounding box on the global lattice (same spacing and phase as
       :func:`popgrid.grid.generate_cells`).  Each candidate gets a signed
       distance to the outline boundary: positive inside (depth), negative
       outside (distance to the outline).  Taking the ``sum(quotas)`` largest
       values keeps every interior cell before any exterior one, so the cell
       count is exact and the shape follows the outline.  For a multi-part
       outline (an archipelago) or a tiny landmass, cells are instead grown
       contiguously from the deepest candidate, giving each region one compact
       footprint rather than a scatter of one-cell islets.
    2. **Seeds.**  Each region's seed is its polygon's
       ``representative_point()`` — not its centroid, which for a ring-shaped
       region falls inside the hole it surrounds.
    3. **Assignment.**  Cells are assigned to regions with exact capacities
       (each region's column repeated ``quota`` times) by solving a linear
       sum assignment on squared cell→seed distances
       (``scipy.optimize.linear_sum_assignment``).  This is a discrete power
       diagram, so regions come out as compact blobs.  A capacity-preserving
       swap pass then repairs any one-cell fragment the lattice assignment
       left behind, keeping the squared-cost solution primary.
    4. **Lloyd refinement.**  ``lloyd_iterations`` times: move each seed to
       ``0.5 * (centroid of its cells) + 0.5 * (its representative point)``
       and re-solve the assignment.  The cells stay compact without drifting
       away from the region's anchor on the deformed map.

    Parameters
    ----------
    shaped:
        Deformed (population-sized) projected region polygons for this
        landmass; ``region_id`` / ``region_name`` are preserved.
    outline:
        The deformed landmass outline (union, holes filled).  Its lattice
        supplies the candidate cells and the signed-distance shape.
    quotas:
        ``{region_name: cell quota}``; the landmass total is their sum.
    cell_size:
        Square cell side length in projected units (metres).
    landmass_id:
        Integer tag written into every output row.
    lloyd_iterations:
        Number of seed/assignment refinement rounds.  Default 4.
    smooth_cells:
        Radius, in cells, of a morphological opening applied to the outline
        before tiling.  The deformed outline has thin necks where squeezed
        neighbours were pushed aside; whichever region fills a neck becomes a
        one-cell strip.  Opening removes them, at the cost of rounding the
        coastline.  Default 0 (off): PopGrid passes the real outline, which
        has no such necks.

    Returns
    -------
    GeoDataFrame with the standard cell schema: ``cx``, ``cy``,
    ``region_id``, ``region_name``, ``landmass_id`` and square ``geometry``
    boxes.
    """
    from scipy.optimize import linear_sum_assignment

    if shaped.empty:
        return gpd.GeoDataFrame()

    names = [str(n) for n in shaped["region_name"]]
    caps = np.array([max(int(quotas.get(n, 0)), 0) for n in names], dtype=int)
    n_cells = int(caps.sum())
    if n_cells <= 0:
        return gpd.GeoDataFrame()

    half = cell_size / 2.0
    if smooth_cells > 0:
        r = smooth_cells * cell_size
        opened = shapely.make_valid(outline.buffer(-r).buffer(r))
        # Keep the raw outline if opening would erase most of a small landmass.
        if not opened.is_empty and opened.area > 0.5 * outline.area:
            outline = opened
    minx, miny, maxx, maxy = outline.bounds

    # ── 1. Lattice candidates with a signed distance to the outline ─────────
    # Grow the bounding box by one ring if it is too small to hold the quota
    # (thin shapes, or an outline smaller than a single cell).
    pad = 1
    while True:
        xs = np.arange(minx - pad * cell_size + half, maxx + pad * cell_size, cell_size)
        ys = np.arange(miny - pad * cell_size + half, maxy + pad * cell_size, cell_size)
        if xs.size and ys.size:
            xx, yy = np.meshgrid(xs, ys)
            cx_all = xx.ravel()
            cy_all = yy.ravel()
            pts = shapely.points(cx_all, cy_all)
            inside = shapely.contains_xy(outline, cx_all, cy_all)
            d_boundary = shapely.distance(outline.boundary, pts)
            d_outline = shapely.distance(outline, pts)
            signed = np.where(inside, d_boundary, -d_outline)
            if signed.size >= n_cells or pad > 8:
                break
        pad += 1

    # Deepest interior cells first, then exterior cells nearest the outline.
    # Multi-part outlines (archipelagos) and tiny landmasses grow one connected
    # footprint instead, so they do not scatter into low-compactness islets.
    parts = list(outline.geoms) if hasattr(outline, "geoms") else [outline]
    if len(parts) > 1 or n_cells <= 4:
        nx = xs.size
        score = signed.reshape(ys.size, nx)
        start = int(np.argmax(score))
        sj, si = divmod(start, nx)
        seen = {start}
        heap = [(-float(score[sj, si]), start)]
        chosen: list[int] = []
        while heap and len(chosen) < n_cells:
            _, idx = heapq.heappop(heap)
            chosen.append(idx)
            j, i = divmod(idx, nx)
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                jj, ii = j + dj, i + di
                if 0 <= jj < ys.size and 0 <= ii < nx:
                    k = jj * nx + ii
                    if k not in seen:
                        seen.add(k)
                        heapq.heappush(heap, (-float(score[jj, ii]), k))
        order = np.array(chosen, dtype=int)
    else:
        order = np.argsort(-signed, kind="stable")[:n_cells]
    cell_x = cx_all[order]
    cell_y = cy_all[order]

    # ── 2. Seeds: representative points of the regions ──────────────────────
    reps = shaped.geometry.representative_point()
    seed_x = reps.x.to_numpy(dtype=float)
    seed_y = reps.y.to_numpy(dtype=float)
    region_ids = shaped["region_id"].to_numpy()

    # ── 3./4. Exact-capacity assignment + Lloyd refinement ──────────────────
    col_region = np.repeat(np.arange(len(names)), caps)

    def assign(sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
        dx = cell_x[:, np.newaxis] - sx[col_region][np.newaxis, :]
        dy = cell_y[:, np.newaxis] - sy[col_region][np.newaxis, :]
        cost = dx * dx + dy * dy
        rows, cols = linear_sum_assignment(cost)
        result = np.empty(n_cells, dtype=int)
        result[rows] = col_region[cols]
        return result

    assigned = assign(seed_x, seed_y)
    for _ in range(max(int(lloyd_iterations), 0)):
        new_x = seed_x.copy()
        new_y = seed_y.copy()
        for i in range(len(names)):
            if caps[i] <= 0:
                continue
            mask = assigned == i
            if not mask.any():
                continue
            new_x[i] = 0.5 * float(cell_x[mask].mean()) + 0.5 * seed_x[i]
            new_y[i] = 0.5 * float(cell_y[mask].mean()) + 0.5 * seed_y[i]
        seed_x, seed_y = new_x, new_y
        assigned = assign(seed_x, seed_y)

    assigned = _repair_fragments(
        cell_x, cell_y, assigned, seed_x, seed_y, caps, cell_size
    )

    # ── 5. Standard cell frame ──────────────────────────────────────────────
    recs = {
        "cx": cell_x,
        "cy": cell_y,
        "region_id": region_ids[assigned],
        "region_name": [names[i] for i in assigned],
        "landmass_id": np.full(n_cells, landmass_id, dtype=int),
    }
    frame = gpd.GeoDataFrame(
        recs,
        geometry=shapely.box(
            cell_x - half, cell_y - half, cell_x + half, cell_y + half
        ),
        crs=shaped.crs,
    )
    return frame.reset_index(drop=True)


def fit_quota(
    cells: gpd.GeoDataFrame,
    quotas: dict[str, int],
    cell_size: float,
) -> gpd.GeoDataFrame:
    """
    Adjust each region's cell count to **exactly** ``quotas[region]`` by
    trimming peripheral cells (over quota) or growing compact cells
    (under quota) on the integer lattice.

    Shape-preserving: trimming removes the least-connected boundary cells
    first (ragged edges go before the body), and growth adds the most-
    connected adjacent empty cells first (fills concavities, not spikes).

    Operates per landmass (cells already carry ``landmass_id``); lattice
    indices are derived from each landmass's own cell positions.
    """
    from collections import defaultdict

    if cells is None or cells.empty:
        return cells

    out_frames = []
    for lm_id, lm_cells in cells.groupby("landmass_id"):
        minx = float(lm_cells["cx"].min())
        miny = float(lm_cells["cy"].min())

        def key(cx, cy):
            return (round((cx - minx) / cell_size), round((cy - miny) / cell_size))

        occupied: dict[tuple[int, int], str] = {}
        region_id_of: dict[str, int] = {}
        reg_keys: dict[str, set] = defaultdict(set)
        for _, r in lm_cells.iterrows():
            k = key(r["cx"], r["cy"])
            occupied[k] = r["region_name"]
            reg_keys[r["region_name"]].add(k)
            region_id_of[r["region_name"]] = r["region_id"]

        def neighbours(k):
            i, j = k
            return [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]

        # ── Drop disconnected specks: keep each region's largest 4-connected
        #    component (removes 1–2 cell fragments left by rasterising a thin
        #    deformed neck).  Skipped for single-region landmasses, whose
        #    multiple components are real islands of an archipelago (Canary,
        #    Balearics) that must be preserved. ───────────────────────────────
        if len(reg_keys) > 1:
            for name in list(reg_keys):
                keys = reg_keys[name]
                if len(keys) <= 1:
                    continue
                seen: set = set()
                components: list[set] = []
                for start in keys:
                    if start in seen:
                        continue
                    comp = {start}
                    stack = [start]
                    seen.add(start)
                    while stack:
                        cur = stack.pop()
                        for nb in neighbours(cur):
                            if nb in keys and nb not in seen:
                                seen.add(nb)
                                comp.add(nb)
                                stack.append(nb)
                    components.append(comp)
                if len(components) > 1:
                    largest = max(components, key=len)
                    for comp in components:
                        if comp is not largest:
                            for k in comp:
                                occupied.pop(k, None)
                    reg_keys[name] = largest

        # ── Trim over-quota regions by eroding the OUTER silhouette ─────────
        # Remove the cell with the most empty (background) neighbours, so
        # internal region borders are never cut (cutting them leaves a hole or
        # scrambles a surrounded region). A fully-interior region with no outer
        # cell is left slightly over quota rather than punctured.
        for name in list(reg_keys):
            quota = int(quotas.get(name, len(reg_keys[name])))
            while len(reg_keys[name]) > max(quota, 1):
                cand, cand_score = None, None
                for k in reg_keys[name]:
                    empty_nb = sum(1 for nb in neighbours(k) if nb not in occupied)
                    if empty_nb == 0:
                        continue  # interior cell — removing it makes a hole
                    same_nb = sum(1 for nb in neighbours(k) if occupied.get(nb) == name)
                    score = (empty_nb, -same_nb)
                    if cand_score is None or score > cand_score:
                        cand_score, cand = score, k
                if cand is None:
                    break  # no safe outer cell to remove
                reg_keys[name].discard(cand)
                occupied.pop(cand, None)

        # ── Grow under-quota regions (add most-connected empties first) ─────
        for name in list(reg_keys):
            quota = int(quotas.get(name, len(reg_keys[name])))
            guard = 0
            while len(reg_keys[name]) < quota and guard < 50_000:
                guard += 1
                cands: dict[tuple[int, int], int] = {}
                for k in reg_keys[name]:
                    for nb in neighbours(k):
                        if nb not in occupied:
                            cands[nb] = sum(
                                1 for nb2 in neighbours(nb) if nb2 in reg_keys[name]
                            )
                if not cands:
                    break
                best = max(cands, key=lambda c: cands[c])
                reg_keys[name].add(best)
                occupied[best] = name

        # ── Rebuild GeoDataFrame for this landmass ──────────────────────────
        half = cell_size / 2.0
        recs = {"cx": [], "cy": [], "region_id": [], "region_name": [], "landmass_id": []}
        for name, keys in reg_keys.items():
            for (i, j) in keys:
                recs["cx"].append(minx + i * cell_size)
                recs["cy"].append(miny + j * cell_size)
                recs["region_id"].append(region_id_of[name])
                recs["region_name"].append(name)
                recs["landmass_id"].append(lm_id)
        if not recs["cx"]:
            continue
        cx = np.array(recs["cx"]); cy = np.array(recs["cy"])
        frame = gpd.GeoDataFrame(
            recs,
            geometry=shapely.box(cx - half, cy - half, cx + half, cy + half),
            crs=cells.crs,
        )
        out_frames.append(frame)

    import pandas as pd
    if not out_frames:
        return cells.iloc[:0].copy()
    return gpd.GeoDataFrame(pd.concat(out_frames, ignore_index=True), crs=cells.crs)


# ── Topology enforcement (real borders) ───────────────────────────────────────

def real_adjacency(regions_proj: gpd.GeoDataFrame) -> dict[str, set]:
    """
    Return ``{region_name: {neighbouring region names}}`` from the *geographic*
    polygons — the ground-truth border graph (e.g. Madrid touches only the two
    Castillas).  Computed with a small buffer so regions that share only a
    border (not area) still register as neighbours.
    """
    names = regions_proj["region_name"].tolist()
    geoms = list(regions_proj.geometry)
    # Buffer by a small fraction of the typical region size to catch shared
    # edges and near-touching coastlines.
    scale = float(np.sqrt(regions_proj.geometry.area.mean())) * 0.02
    buffed = [shapely.buffer(g, scale) for g in geoms]
    tree = shapely.STRtree(buffed)
    adj: dict[str, set] = {n: set() for n in names}
    for i, g in enumerate(buffed):
        for j in tree.query(g):
            if j == i:
                continue
            if shapely.intersects(g, buffed[j]):
                adj[names[i]].add(names[j])
                adj[names[j]].add(names[i])
    return adj


def enforce_topology(
    cells: gpd.GeoDataFrame,
    real_adj: dict[str, set],
    quotas: dict[str, int],
    cell_size: float,
    passes: int = 30,
) -> gpd.GeoDataFrame:
    """
    Repair grid adjacencies so two regions touch on the lattice **only if they
    touch in reality**, then rebalance toward quota with topology-safe swaps.

    The repair relabels an illegal border cell (region A touching region B that
    is not a real neighbour) to a *connector* region C that borders both A and
    B in reality.  Iterated, the connector (e.g. Castilla y León / La Mancha)
    grows into a thin band wrapping the inflated region (Madrid), which is
    exactly the real topology: Madrid bordered only by the two Castillas, the
    sparse Castillas reduced to lines around it.

    A light quota rebalance then moves cells from over- to under-quota *real*
    neighbours, but only when the move keeps every adjacency legal — so the
    1-cell connective ring is never eaten away.

    Operates per landmass; single-region landmasses are returned unchanged.
    """
    import pandas as pd
    from collections import defaultdict

    if cells is None or cells.empty:
        return cells

    out_frames = []
    for lm_id, lm_cells in cells.groupby("landmass_id"):
        present = set(lm_cells["region_name"])
        if len(present) < 2:
            out_frames.append(lm_cells.copy())
            continue

        minx = float(lm_cells["cx"].min())
        miny = float(lm_cells["cy"].min())

        def key(cx, cy):
            return (round((cx - minx) / cell_size), round((cy - miny) / cell_size))

        grid: dict[tuple, str] = {}
        region_id_of: dict[str, int] = {}
        for _, r in lm_cells.iterrows():
            grid[key(r["cx"], r["cy"])] = r["region_name"]
            region_id_of[r["region_name"]] = r["region_id"]

        def nbrs(k):
            i, j = k
            return ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))

        def reg_keys_of(region):
            return {k for k, v in grid.items() if v == region}

        def connected_without(region, drop):
            keys = reg_keys_of(region) - {drop}
            if not keys:
                return False
            start = next(iter(keys))
            seen = {start}
            stack = [start]
            while stack:
                c = stack.pop()
                for d in nbrs(c):
                    if d in keys and d not in seen:
                        seen.add(d)
                        stack.append(d)
            return len(seen) == len(keys)

        def legal_at(region, k):
            for d in nbrs(k):
                o = grid.get(d)
                if o is not None and o != region and o not in real_adj.get(region, set()):
                    return False
            return True

        for _p in range(passes):
            changed = 0
            counts = defaultdict(int)
            for v in grid.values():
                counts[v] += 1

            # ── 1. Repair illegal adjacencies ──────────────────────────────
            for k in list(grid.keys()):
                A = grid.get(k)
                if A is None:
                    continue
                for d in nbrs(k):
                    B = grid.get(d)
                    if B is None or B == A or B in real_adj.get(A, set()):
                        continue
                    conn = real_adj.get(A, set()) & real_adj.get(B, set()) & present
                    if not conn:
                        continue
                    # smallest connector grows (forms the thin band)
                    C = min(conn, key=lambda c: counts.get(c, 0))
                    # Forming the ring is a *rearrangement*: relabel the cell of
                    # whichever side is more over (or less under) its quota.
                    # The connector briefly exceeds quota; the gradient flow
                    # below sheds its BODY cells to deficit regions, while the
                    # legal_at guard prevents it from ever shedding the RING
                    # cells (a cell separating two non-neighbours can't legally
                    # become either) — so rings persist and quotas still settle.
                    over_A = counts[A] - quotas.get(A, 0)
                    over_B = counts[B] - quotas.get(B, 0)
                    target_k, target_region = (k, A) if over_A >= over_B else (d, B)
                    if (legal_at(C, target_k)
                            and connected_without(target_region, target_k)):
                        grid[target_k] = C
                        counts[target_region] -= 1
                        counts[C] += 1
                        changed += 1
                        break

            # ── 2. Topology-safe gradient quota flow (multi-hop) ───────────
            # Over-quota regions push boundary cells "downhill" to real
            # neighbours that are more in deficit.  Surplus from a connector
            # that grew to wrap Madrid diffuses across the graph to the small
            # regions it was taken from — restoring quotas without unwrapping.
            for region in sorted(present, key=lambda r: -(counts[r] - quotas.get(r, 0))):
                guard = 0
                while counts[region] - quotas.get(region, 0) > 0 and guard < 4000:
                    guard += 1
                    err_R = counts[region] - quotas.get(region, 0)
                    moved = False
                    for k in list(reg_keys_of(region)):
                        for d in nbrs(k):
                            S = grid.get(d)
                            if S in present and S != region:
                                err_S = counts[S] - quotas.get(S, 0)
                                if (err_R - err_S >= 2
                                        and legal_at(S, k)
                                        and connected_without(region, k)):
                                    grid[k] = S
                                    counts[region] -= 1
                                    counts[S] += 1
                                    moved = True
                                    break
                        if moved:
                            break
                    if not moved:
                        break

            if changed == 0:
                break

        half = cell_size / 2.0
        recs = {"cx": [], "cy": [], "region_id": [], "region_name": [], "landmass_id": []}
        for (i, j), name in grid.items():
            recs["cx"].append(minx + i * cell_size)
            recs["cy"].append(miny + j * cell_size)
            recs["region_id"].append(region_id_of[name])
            recs["region_name"].append(name)
            recs["landmass_id"].append(lm_id)
        cx = np.array(recs["cx"]); cy = np.array(recs["cy"])
        out_frames.append(gpd.GeoDataFrame(
            recs, geometry=shapely.box(cx - half, cy - half, cx + half, cy + half),
            crs=cells.crs,
        ))

    if not out_frames:
        return cells.iloc[:0].copy()
    return gpd.GeoDataFrame(pd.concat(out_frames, ignore_index=True), crs=cells.crs)


# ── Gap filling (close seams left between deformed regions) ────────────────────

def fill_gaps(
    cells: gpd.GeoDataFrame,
    cell_size: float,
    quotas: dict | None = None,
    min_neighbours: int = 3,
    passes: int = 12,
) -> gpd.GeoDataFrame:
    """
    Close empty cells that sit *inside* the filled area — the seams left
    between adjacent deformed region polygons and any enclosed holes.

    An empty lattice cell is filled (assigned to the majority region among its
    occupied 4-neighbours) when it is clearly interior: either it has at least
    ``min_neighbours`` occupied neighbours, or it is a one-cell-wide seam
    (occupied cells on two opposite sides).  Cells on the open outer edge of a
    region keep fewer neighbours and are left alone, so the country silhouette
    is preserved while internal white gaps disappear.

    Iterated, so holes that only become interior after their neighbours fill
    are also closed.  Operates per landmass.
    """
    import pandas as pd
    from collections import Counter

    if cells is None or cells.empty:
        return cells

    out_frames = []
    for lm_id, lm_cells in cells.groupby("landmass_id"):
        minx = float(lm_cells["cx"].min())
        miny = float(lm_cells["cy"].min())

        occ: dict[tuple, str] = {}
        region_id_of: dict[str, int] = {}
        for _, r in lm_cells.iterrows():
            occ[(round((r["cx"] - minx) / cell_size),
                 round((r["cy"] - miny) / cell_size))] = r["region_name"]
            region_id_of[r["region_name"]] = r["region_id"]

        def nbrs(k):
            i, j = k
            return ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))

        orig_counts = Counter(occ.values())

        for _p in range(passes):
            iis = [k[0] for k in occ]
            jjs = [k[1] for k in occ]
            imin, imax = min(iis) - 1, max(iis) + 1
            jmin, jmax = min(jjs) - 1, max(jjs) + 1

            # Flood-fill empty space inward from the bounding-box border.  Any
            # empty cell the outside cannot reach is an *enclosed* interior hole
            # — fill it (whatever its size) with the majority bordering region.
            exterior: set = set()
            stack = []
            for i in range(imin, imax + 1):
                for j in (jmin, jmax):
                    if (i, j) not in occ:
                        stack.append((i, j))
            for j in range(jmin, jmax + 1):
                for i in (imin, imax):
                    if (i, j) not in occ:
                        stack.append((i, j))
            while stack:
                c = stack.pop()
                if c in exterior or c in occ:
                    continue
                exterior.add(c)
                ci, cj = c
                for nb in ((ci + 1, cj), (ci - 1, cj), (ci, cj + 1), (ci, cj - 1)):
                    ni, nj = nb
                    if imin <= ni <= imax and jmin <= nj <= jmax and nb not in occ:
                        stack.append(nb)

            # Enclosed empty cells the exterior flood-fill didn't reach. Group
            # into connected pockets and fill only the small ones (the seams
            # between adjacent regions). A very large enclosed void — produced
            # only by extreme deformation, e.g. China — is left alone, since
            # filling it would dump dozens of cells into a surrounded region.
            MAX_POCKET = 50
            enclosed = [
                (i, j)
                for i in range(imin, imax + 1)
                for j in range(jmin, jmax + 1)
                if (i, j) not in occ and (i, j) not in exterior
            ]
            enclosed_set = set(enclosed)
            seen_p: set = set()
            to_fill: dict[tuple, str] = {}
            for start in enclosed:
                if start in seen_p:
                    continue
                pocket = [start]
                seen_p.add(start)
                qi = 0
                while qi < len(pocket):
                    ci, cj = pocket[qi]
                    qi += 1
                    for nb in ((ci + 1, cj), (ci - 1, cj), (ci, cj + 1), (ci, cj - 1)):
                        if nb in enclosed_set and nb not in seen_p:
                            seen_p.add(nb)
                            pocket.append(nb)
                if len(pocket) > MAX_POCKET:
                    continue  # leave large voids unfilled (China)
                for k in pocket:
                    occ_nb = [occ[d] for d in nbrs(k) if d in occ]
                    if occ_nb:
                        to_fill[k] = Counter(occ_nb).most_common(1)[0][0]
            if not to_fill:
                break
            occ.update(to_fill)

        # Count-neutral PER REGION: every region sheds exactly as many outer-
        # silhouette cells as it gained from hole-filling, so closing seams
        # never changes any region's block count (no quota drift).
        now_counts = Counter(occ.values())
        for region in now_counts:
            surplus = now_counts[region] - orig_counts.get(region, 0)
            for _ in range(surplus):
                cand, cand_e = None, 0
                for k in [c for c, v in occ.items() if v == region]:
                    e = sum(1 for nb in nbrs(k) if nb not in occ)
                    if e > cand_e:
                        cand_e, cand = e, k
                if cand is None or cand_e == 0:
                    break
                del occ[cand]

        half = cell_size / 2.0
        recs = {"cx": [], "cy": [], "region_id": [], "region_name": [], "landmass_id": []}
        for (i, j), name in occ.items():
            recs["cx"].append(minx + i * cell_size)
            recs["cy"].append(miny + j * cell_size)
            recs["region_id"].append(region_id_of[name])
            recs["region_name"].append(name)
            recs["landmass_id"].append(lm_id)
        cx = np.array(recs["cx"]); cy = np.array(recs["cy"])
        out_frames.append(gpd.GeoDataFrame(
            recs, geometry=shapely.box(cx - half, cy - half, cx + half, cy + half),
            crs=cells.crs,
        ))

    if not out_frames:
        return cells.iloc[:0].copy()
    return gpd.GeoDataFrame(pd.concat(out_frames, ignore_index=True), crs=cells.crs)


# ── Exact quota balancing (final step of PopGrid quota handling) ───────────────

def balance_quotas(
    cells: gpd.GeoDataFrame,
    quotas: dict[str, int],
    cell_size: float,
    real_adj: dict[str, set] | None = None,
) -> gpd.GeoDataFrame:
    """
    Force every region's cell count to **exactly** ``quotas[region]``.

    ``fit_quota`` / ``enforce_topology`` / ``fill_gaps`` are shape- and
    topology-driven: they can only grow a region into *empty* lattice cells
    next to it and only trim from the outer silhouette, so a region enclosed by
    others (Madrid, Toledo) can never reach its quota, and a connector that grew
    a legal ring around it (Cuenca) cannot shed the ring cells.  This pass is
    quota-driven instead: exact counts are a hard guarantee, while real-border
    topology and contiguity are best-effort preferences.

    Two phases, per landmass (lattice indices derived from each landmass's own
    cell positions, exactly like :func:`fit_quota`):

    a. **Fix the landmass total** so it equals the sum of the quotas of the
       regions present.  If short, add empty cells adjacent to the silhouette
       (most occupied 4-neighbours first, i.e. concavities before spikes), each
       labelled with the adjacent region that is furthest below quota.  If over,
       remove silhouette cells (a cell with an empty 4-neighbour can always go
       without punching an interior hole), most over-quota region first, and
       within a region the cell with the most empty neighbours.

    b. **Move surplus inwards.**  While a region R is over quota, BFS over the
       lattice region-adjacency graph (regions whose cells are 4-adjacent) to
       the *nearest* region U that is under quota.  For the path
       ``R = X0 → X1 → … → Xk = U`` one cell is transferred across every hop,
       starting at the U end (``Xk-1 → Xk``, then ``Xk-2 → Xk-1``, …), so each
       intermediate region ends up with the same count it started with.  For a
       hop ``X → Y`` the cell of X adjacent to Y is chosen preferring
       (1) X stays 4-connected *and* relabelling is topology-legal (every
       4-neighbour of the cell is Y, a real neighbour of Y, or empty),
       (2) X stays 4-connected, (3) anything; within a tier the cell with the
       most Y neighbours wins (compact transfer).  Every path transfer lowers
       ``Σ |count − quota|`` by two, so the loop terminates.
    """
    import pandas as pd
    from collections import Counter, deque

    real_adj = real_adj or {}
    if cells is None or cells.empty:
        return cells

    out_frames = []
    for lm_id, lm_cells in cells.groupby("landmass_id"):
        minx = float(lm_cells["cx"].min())
        miny = float(lm_cells["cy"].min())

        def key(cx, cy):
            return (round((cx - minx) / cell_size), round((cy - miny) / cell_size))

        def nbrs(k):
            i, j = k
            return ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))

        grid: dict[tuple[int, int], str] = {}
        region_id_of: dict[str, int] = {}
        for _, r in lm_cells.iterrows():
            grid[key(r["cx"], r["cy"])] = r["region_name"]
            region_id_of[r["region_name"]] = r["region_id"]

        present = sorted(set(grid.values()))
        quota = {n: max(int(quotas.get(n, 1)), 1) for n in present}
        counts = Counter(grid.values())
        target = sum(quota.values())

        def region_keys(name):
            return {k for k, v in grid.items() if v == name}

        def connected_without(name, drop):
            """Is ``name`` still 4-connected after removing ``drop``?"""
            keys = region_keys(name)
            keys.discard(drop)
            if len(keys) <= 1:
                return True
            start = next(iter(keys))
            seen = {start}
            stack = [start]
            while stack:
                c = stack.pop()
                for d in nbrs(c):
                    if d in keys and d not in seen:
                        seen.add(d)
                        stack.append(d)
            return len(seen) == len(keys)

        def legal_at(region, k):
            """Would relabelling cell ``k`` to ``region`` keep borders legal?"""
            real = real_adj.get(region, set())
            for d in nbrs(k):
                o = grid.get(d)
                if o is not None and o != region and o not in real:
                    return False
            return True

        # ── (a) Pending total: grow into concavities or erode the outline ────
        guard = 0
        while sum(counts.values()) < target and guard < 200_000:
            guard += 1
            cands: dict[tuple[int, int], int] = {}
            for k in grid:
                for d in nbrs(k):
                    if d not in grid:
                        cands[d] = cands.get(d, 0) + 1
            if not cands:
                break
            # Most occupied neighbours first (fill concavities); deterministic
            # tie-break on lattice position.
            best = max(cands, key=lambda c: (cands[c], -c[0], -c[1]))
            owners = [grid[d] for d in nbrs(best) if d in grid]
            # Hand it to the adjacent region furthest below its quota.
            label = min(owners, key=lambda o: (counts[o] - quota[o], o))
            grid[best] = label
            counts[label] += 1

        guard = 0
        while sum(counts.values()) > target and guard < 200_000:
            guard += 1
            cands = []
            for k, name in grid.items():
                empty_nb = sum(1 for d in nbrs(k) if d not in grid)
                if empty_nb == 0:
                    continue  # interior cell: removing it would punch a hole
                same_nb = sum(1 for d in nbrs(k) if grid.get(d) == name)
                cands.append((counts[name] - quota[name], empty_nb, -same_nb, k))
            cands.sort(reverse=True)
            victim = None
            for strict in (True, False):
                for cand in cands:
                    k = cand[-1]
                    name = grid[k]
                    if counts[name] <= 1:
                        continue
                    if strict and not connected_without(name, k):
                        continue
                    victim = k
                    break
                if victim is not None:
                    break
            if victim is None:
                break
            counts[grid.pop(victim)] -= 1

        # ── (b) Flow surplus to the nearest under-quota region ──────────────
        def adjacency():
            adj: dict[str, set] = {n: set() for n in present}
            for k, name in grid.items():
                for d in nbrs(k):
                    o = grid.get(d)
                    if o is not None and o != name:
                        adj[name].add(o)
            return adj

        def nearest_under(adj, root):
            """Shortest region path root → some region below its quota."""
            seen = {root}
            parent: dict[str, str] = {}
            queue = deque([root])
            while queue:
                cur = queue.popleft()
                for nxt in sorted(adj.get(cur, ())):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    parent[nxt] = cur
                    if counts[nxt] < quota[nxt]:
                        path = [nxt]
                        while path[-1] != root:
                            path.append(parent[path[-1]])
                        path.reverse()
                        return path
                    # An intermediate region must survive the hop that empties
                    # one of its cells (it gets one back afterwards).
                    if counts[nxt] >= 2:
                        queue.append(nxt)
            return None

        def pick_cell(src, dst):
            """A cell of ``src`` 4-adjacent to ``dst``, best tier first."""
            best, best_score = None, None
            for k, name in grid.items():
                if name != src:
                    continue
                dst_nb = sum(1 for d in nbrs(k) if grid.get(d) == dst)
                if dst_nb == 0:
                    continue
                conn = connected_without(src, k)
                if conn and legal_at(dst, k):
                    tier = 0
                elif conn:
                    tier = 1
                else:
                    tier = 2
                score = (-tier, dst_nb)
                if best_score is None or score > best_score:
                    best_score, best = score, k
            return best

        guard = 0
        limit = 4 * (sum(counts.values()) + target) + 10_000
        while guard < limit:
            guard += 1
            over = [n for n in present if counts[n] > quota[n]]
            if not over:
                break
            root = max(over, key=lambda n: (counts[n] - quota[n], n))
            path = nearest_under(adjacency(), root)
            if path is None or len(path) < 2:
                break
            # One cell across every hop, starting at the deficit end, so the
            # intermediate regions keep their counts.
            moved = True
            for src, dst in zip(reversed(path[:-1]), reversed(path[1:])):
                cell = pick_cell(src, dst)
                if cell is None or counts[src] <= 1:
                    moved = False
                    break
                grid[cell] = dst
                counts[src] -= 1
                counts[dst] += 1
            if not moved:
                break

        half = cell_size / 2.0
        recs = {"cx": [], "cy": [], "region_id": [], "region_name": [], "landmass_id": []}
        for (i, j), name in grid.items():
            recs["cx"].append(minx + i * cell_size)
            recs["cy"].append(miny + j * cell_size)
            recs["region_id"].append(region_id_of[name])
            recs["region_name"].append(name)
            recs["landmass_id"].append(lm_id)
        cx = np.array(recs["cx"]); cy = np.array(recs["cy"])
        out_frames.append(gpd.GeoDataFrame(
            recs, geometry=shapely.box(cx - half, cy - half, cx + half, cy + half),
            crs=cells.crs,
        ))

    if not out_frames:
        return cells.iloc[:0].copy()
    return gpd.GeoDataFrame(pd.concat(out_frames, ignore_index=True), crs=cells.crs)
