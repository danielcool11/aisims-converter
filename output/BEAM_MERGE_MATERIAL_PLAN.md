# Plan: Beam Material Propagation + Beam Merging

## Context

The beam rebar calculator produces wrong bar lengths for two reasons:
1. **Material loss**: Raw MIDAS data assigns concrete grade (C24/C30/C40) and rebar grades (SD600/SD400) per element, but the converter drops `material_id` when creating MembersBeam.csv. The rebar calculator defaults to C35/fy=600, producing wrong Ldh/Lpt/Lpb values.
2. **Short FEM segments**: MIDAS splits beams at every FEM node, creating many tiny segments (48mm to 900mm). These get treated as standalone beams with their own hooks/laps, producing absurd bar lengths (e.g., 3350mm bar for a 200mm beam).

## Part 1: Material ID Propagation

### Which members have material data?

| Member Type | In Elements.csv | P1 | P2 | Action |
|-------------|----------------|-----|-----|--------|
| **Beam** | ✓ Material column | All C35 | C24/C30/C40 | Propagate from Elements.csv |
| **Column** | ✓ Material column | All C35 | C24/C30/C40 | Propagate from Elements.csv |
| **Wall** | ✓ Material column | All C35 | C24/C30/C40 | Propagate from Elements.csv |
| **Slab** | ✗ Not in data | — | — | Default to project's dominant grade |
| **Footing** | ✗ Not in data | — | — | Default to project's dominant grade |
| **Stair** | ✗ Not in data | — | — | Default to project's dominant grade |

Slabs/footings/stairs get their rebar specs from separate Part_B files which don't include material. For these, use the project's dominant concrete grade (e.g., C35 for P1, could infer from most-used horizontal material for P2).

### 1A. `converters/materials.py` — Build material_map

Add a second return value: a dict mapping raw MIDAS material ID → material properties.

```python
# Return: (materials_df, material_map)
# material_map = {
#   11: {'concrete_grade': 'C24', 'fck': 24, 'fy_main': 600, 'fy_sub': 400},
#   41: {'concrete_grade': 'C40', 'fck': 40, 'fy_main': 600, 'fy_sub': 500},
#   50: {'concrete_grade': 'C30', 'fck': 30, 'fy_main': 600, 'fy_sub': 400},
#   21: {'concrete_grade': 'C24', 'fck': 24, 'fy_main': None, 'fy_sub': None},  # vertical, no rebar grade
# }
```

Parse from raw Materials.csv columns: `ID`, `fck_N/㎟`, `Grade(Main Rebar)`, `Grade(Sub Rebar)`, `fy_N/㎟`, `fys_N/㎟`. Materials without rebar grades (vertical-only materials like ID=21,22) store `fy_main=None` — the rebar calculator falls back to the project-level diameter→fy map.

Also build a **project-level diameter→fy map** from the horizontal materials:
```python
# dia_fy_map built from the most common horizontal material's rebar grades:
# P2 (Material 11: Main=SD600, Sub=SD400):
#   dia_fy_map = {10: 400, 13: 400, 16: 600, 19: 600, 22: 600, 25: 600, 29: 600, 32: 600, 35: 600}
# P1 (Material 1: Main=SD600, Sub=SD500):
#   dia_fy_map = {10: 500, 13: 500, 16: 600, 19: 600, 22: 600, 25: 600, 29: 600, 32: 600, 35: 600}
```
Rule: D10/D13 use `fy_sub` (sub rebar grade), D16+ use `fy_main` (main rebar grade).
This replaces the hardcoded `_steel_grade()` function across ALL tier2 calculators.

Materials.csv output stays unchanged (reference table only).

Return value: `(materials_df, material_map, dia_fy_map)`

### 1B. `converters/elements.py` — Add material fields to beam, column, wall records

In beam/column/wall record creation, add:
```python
'material_id': material_map.get(raw_mat_id, {}).get('concrete_grade', 'C35'),
'fy_main': material_map.get(raw_mat_id, {}).get('fy_main'),
'fy_sub': material_map.get(raw_mat_id, {}).get('fy_sub'),
```

`convert_elements()` gains a `material_map=None` parameter (backward compatible).

### 1C. `app.py` — Wire material_map

```python
materials_df, material_map = convert_materials(materials_raw, mgt_data)
elem_result = convert_elements(..., material_map=material_map)
```

### 1D. Tier 2 rebar calculators — Use real fc/fy

**`tier2/rebar_lengths_beam.py`:**
- `_steel_grade(dia, fy_override=None)`: use override when available
- Line 699: `fc = _parse_fc(sp.get('material_id', 'C35'))` — now receives real value
- Line 711: `fy = _steel_grade(dia_top, sp.get('fy_main'))` — uses per-beam fy

**`tier2/rebar_lengths_column.py`:**
- Same pattern: read `material_id` and `fy_main` from column record for fc/fy lookup

**`tier2/rebar_lengths_wall.py`:**
- Same pattern: read `material_id` from wall record for fc lookup

**ALL tier2 calculators — Replace `_steel_grade()` with `dia_fy_map` lookup:**

Every calculator's `_steel_grade(dia)` is replaced with a lookup against the project-level `dia_fy_map`:
```python
def _steel_grade(dia_mm, dia_fy_map=None, fy_override=None):
    """Get fy for a rebar diameter. Priority: override > dia_fy_map > hardcoded fallback."""
    if fy_override is not None:
        return int(fy_override)
    if dia_fy_map and int(dia_mm) in dia_fy_map:
        return dia_fy_map[int(dia_mm)]
    # Legacy fallback (should not reach here with proper material data)
    return 400 if int(dia_mm) in (10, 13) else 600
```

**For beams/columns/walls** (have per-element material_id):
- Use `fy_main` from the element record as `fy_override` for main bars
- Use `fy_sub` from the element record for stirrups/hoops
- Use `fc` from `material_id` field

**For slabs/stairs** (no material_id):
- Use project-level `dia_fy_map` for fy lookup
- Use project-level `fc` (most common horizontal beam concrete grade, typically one grade for all floors)
- In practice, all slabs/stairs use the same concrete grade. Use the mode of beam fc values.

**For footings** (no material_id):
- Use project-level `dia_fy_map` for fy lookup
- Use fc from the lowest basement level's beam material

**For basement walls** (Part C, no material_id):
- Use project-level `dia_fy_map` for fy lookup
- Use fc from beam material at each level (fc_by_level map)

**Building project-level maps (in `app.py`):**
```python
# After material propagation, build project-level maps from beams
import re, statistics
fc_by_level = {}
all_fc_values = []
for _, b in outputs['beams'].iterrows():
    mat = b.get('material_id', '')
    fc = int(re.search(r'(\d+)', str(mat)).group(1)) if re.search(r'(\d+)', str(mat)) else 35
    level = b.get('level', '')
    if level:
        fc_by_level.setdefault(level, fc)
    all_fc_values.append(fc)
default_fc = statistics.mode(all_fc_values) if all_fc_values else 35
```

### 1E. `BIM-Viewer-V2/backend/ingest_csv.py` — Read material_id

Update beam, column, and wall ingest to read `material_id` field.

---

## Part 2: Beam Merging

### Merge Rules
1. Same `member_id` + same `level` + same `direction`
2. Contiguous (end-to-start within 100mm tolerance)
3. **Break at structural supports**: if intermediate node grid is in `support_grids` set → break point. Support grids = union of:
   - Column grids (from MembersColumn)
   - Wall grids (from MembersWall — all walls, since MIDAS only models structural walls)
   - Beam-beam junctions (grid points where beams of different `member_id` intersect)
4. **Break on section change**: different `section_id` → different beam size
5. **Break on material change**: different `material_id` → different concrete grade
6. **Max length**: merged span ≤ 12000mm

### Additional Considerations
- **extend_start/end_mm after merge**: Only outer endpoints of merged span keep extensions. Internal merged points lose theirs.
- **Stirrup fy vs main bar fy**: Use `fy_main` for main bars, `fy_sub` for stirrups/ties/hoops. Each calculator must differentiate.
- **Sanity check**: Assert sum of element lengths before merge == sum of merged span lengths.
- **Diagonal beams**: Direction detection via dominant coordinate axis. Sort by that axis for merging.
- **segment_id after merge**: New IDs (e.g., `TG6-SPAN001`). Verify `design_key` → reinforcement FK still works.

### Real Example — P2 TG6 at 3F (y=42800, X-direction)

**Before merge (4 elements between X12Y12 and X10Y12):**
```
E23383  X12Y12→OFF_GRID  1050mm  ─┐
E23384  OFF_GRID→OFF_GRID  280mm   ├── merge → TG6-SPAN001 (8400mm)
E23385  OFF_GRID→OFF_GRID 5250mm   │   grid: X12Y12→X10Y12
E23386  OFF_GRID→X10Y12   1820mm  ─┘   element_ids: 23383,23384,23385,23386
                                        material_id: C40, fy_main: 600, fy_sub: 500
--- BREAK at X10Y12 (column TC6A exists here) ---

E23387  X10Y12→OFF_GRID    490mm  ─┐
E23388  OFF_GRID→OFF_GRID  2170mm   ├── merge → TG6-SPAN002 (7400mm)
E23389  OFF_GRID→OFF_GRID   490mm   │   grid: X10Y12→X9Y12
E23390  OFF_GRID→OFF_GRID  3150mm   │   element_ids: 23387,23388,23389,23390,23391
E23391  OFF_GRID→X9Y12     1100mm  ─┘
--- BREAK at X9Y12 (column TC6 exists here) ---
```

**After merge:** 2 spans instead of 9 elements. Rebar calculator sees proper column-to-column spans.

### Real Example — P2 TG5A at 3F (short beam problem)

**Before merge:**
```
TG5A  OFF_GRID→OFF_GRID  200mm   MAIN_SINGLE → L_bar=3350mm (absurd!)
TG5A  OFF_GRID→OFF_GRID  900mm   MAIN_START  → L_bar=3350mm
...
```

**After merge:** These short segments merge with adjacent segments into column-to-column spans. No more standalone 200mm beams.

### Real Example — P1 B11 at B3 (short vertical segments)

**Before merge:**
```
E131  OFF_GRID→OFF_GRID  340mm  ─┐
E132  OFF_GRID→OFF_GRID 1220mm   ├── merge into one span
E138  OFF_GRID→OFF_GRID  390mm  ─┘
```

### New file: `converters/beam_merge.py`

```python
def merge_beam_spans(beams_df, columns_df, tolerance=100.0, max_length=12000.0):
    """Merge adjacent FEM beam elements into structural spans.
    
    Breaks at: column grid points, section changes, material changes, max length.
    Preserves traceability via element_ids column.
    """
    # 1. Build column_grids set from columns_df
    # 2. Group by (level, member_id, direction), sort by coordinate
    # 3. Chain contiguous elements
    # 4. Split chains at break points (column grids, section/material change, max length)
    # 5. Merge each sub-chain into one span record
    # Returns DataFrame with same schema + element_ids column
```

### Integration in `app.py`

Insert after Phase 2.5 (grid auto-detection), before Phase 3:
```python
from converters.beam_merge import merge_beam_spans
outputs['beams'] = merge_beam_spans(outputs['beams'], outputs['columns'])
```

### Output schema change — MembersBeam.csv

New columns added:
- `material_id` (e.g., "C40")
- `fy_main` (e.g., 600)
- `fy_sub` (e.g., 400)  
- `element_ids` (e.g., "23383,23384,23385,23386")

Existing `element_id` keeps the first element's ID for backward compatibility.

---

## Implementation Order

1. **Phase A** — Material propagation (no breaking changes):
   - A1: `materials.py` → return material_map
   - A2: `elements.py` → add material_id/fy_main/fy_sub to beam, column, wall records
   - A3: `app.py` → wire material_map + build fc_by_level map from beams
   - A4: `rebar_lengths_beam.py` → use real fc/fy from per-beam material_id
   - A5: `rebar_lengths_column.py` → use real fc/fy from per-column material_id
   - A6: `rebar_lengths_wall.py` → use real fc from per-wall material_id
   - A7: `rebar_lengths_slab.py` → accept fc_by_level, use beam fc at same level
   - A8: `rebar_lengths_footing.py` → accept fc from lowest-level beam material
   - A9: `rebar_lengths_stair.py` → accept fc_by_level, use beam fc at same level
   - A10: `rebar_lengths_basement_wall.py` → accept fc_by_level
   - A11: `ingest_csv.py` → read material_id for beam, column, wall

2. **Phase B** — Beam merging (depends on A for material_id):
   - B1: Create `beam_merge.py`
   - B2: Integrate into `app.py`
   - B3: Add `element_ids` to viewer model/ingest

## Verification

1. Re-run converter for P1 → verify fc=35 for all beams (unchanged behavior)
2. Re-run converter for P2 → verify:
   - TG6 beams at 3F get fc=40 (transfer level), not 35
   - Beams at B1-B4 get fc=30 (basement), not 35
   - Beams at upper floors get fc=24, not 35
3. Check beam count: P2 should drop from 3352 to ~1500-2000 merged spans
4. Check TG5A: no more 200mm standalone beams
5. Re-ingest and verify in viewer: bars anchor properly into columns without protruding

## Critical Files

**Converter:**
- `converters/materials.py` — material_map return value
- `converters/elements.py` — propagate material_id/fy_main/fy_sub to beam, column, wall records
- `converters/beam_merge.py` — NEW: merge algorithm
- `app.py` — wire material_map, build fc_by_level, merge step

**Tier 2 (all use fc/fy):**
- `tier2/rebar_lengths_beam.py` — use per-beam material_id for fc, fy_main for fy
- `tier2/rebar_lengths_column.py` — use per-column material_id for fc, fy_main for fy
- `tier2/rebar_lengths_wall.py` — use per-wall material_id for fc
- `tier2/rebar_lengths_slab.py` — accept fc_by_level map
- `tier2/rebar_lengths_footing.py` — accept fc from app.py
- `tier2/rebar_lengths_stair.py` — accept fc_by_level map
- `tier2/rebar_lengths_basement_wall.py` — accept fc_by_level map

**Viewer:**
- `BIM-Viewer-V2/backend/ingest_csv.py` — read material_id for beam/column/wall
