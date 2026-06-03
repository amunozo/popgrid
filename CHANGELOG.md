## v0.6.0
- Real-border topology enforcement: regions touch on the grid only if they touch in reality (Madrid borders only the two Castillas, never Galicia/Asturias/Cantabria). Sparse connector regions thin into bands wrapping dense ones.
- Detached territories re-anchored to the deformed mainland frame, so enclaves (e.g. Melilla) keep their inline placement instead of drifting into a panel. Island-classification logic unchanged.
- Gradient quota flow rebalances cells across the adjacency graph after topology repair.

## v0.5.0
- PopGrid now builds a true population cartogram: regions are deformed (Dougenik) so area is proportional to population, then rasterised. Country outline distorts while regions keep position and shape.
- Adjacency-aware colouring so neighbouring regions are always visually distinct.
- Labels placed at each region largest contiguous cluster.
- Exact per-region quota fitting with contiguity cleanup; archipelago shapes preserved.