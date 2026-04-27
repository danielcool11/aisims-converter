# Bar ID & Member Instance ID — Post-Processing Specification (V2)

Updated: 2026-04-26

## Overview

Two columns are added to all converted CSVs by `add_bar_id.py`:

- **`member_instance_id`** — unique identifier for a physical member instance (e.g., `BD01-F02-TC002A-003`)
- **`bar_id`** — unique identifier for a rebar bar group (e.g., `BD01-F02-TC002A-003-4T25-001`)

The relationship: `bar_id = member_instance_id + bar_mark`. Multiple bars share the same `member_instance_id` (they belong to the same physical member).

Members CSVs get `member_instance_id` only. RebarLengths CSVs get both `bar_id` and `member_instance_id`.

## V2 Format

### member_instance_id: `Building-Floor-Symbol-Serial`

| Component | Format | Examples |
|-----------|--------|---------|
| Building | 4 chars: 2 letters + 2 digits | `BD01`, `BD02` |
| Floor | 3 chars fixed | `F01`, `F02`, `B01`, `B05`, `PIT`, `ROF`, `FTG` |
| Symbol | Alpha prefix + 3-digit padded number + optional suffix | `TC002A`, `B001A`, `CW001`, `MF001`, `S012` |
| Serial | 3-digit (001-999) | `001`, `003`, `006` |

### bar_id: `member_instance_id-BarMark`

BarMark format: `count-PositionDia@spacing-serial`

| Part | Description | Examples |
|------|-------------|---------|
| count | n_bars (MAIN/ADD) or quantity_pieces (stirrup/hoop) | `4`, `15` |
| PositionDia | Position prefix + diameter value | `T25`, `B22`, `M19`, `A13`, `N16`, `F16`, `D29` |
| @spacing | Spacing (only if exists) | `@200`, `@150` |
| serial | Floor-based sequential, lowest=1, ascending | `001`, `002` |

### Position Prefix

| Member Type | Position | Prefix | Example |
|------------|----------|--------|---------|
| Beam | Top / Bottom / Middle | `T` / `B` / `M` | `4-T25-001` |
| Slab | Top / Bottom | `T` / `B` | `18-T13@150-001` |
| Footing | Top / Bottom | `T` / `B` | `20-T19@200-001` |
| Regular Wall | All faces (double layer) | `A` | `12-A13@150-001` |
| Basement Wall | Near / Far face | `N` / `F` | `32-N16@200-001` |
| Column / Stirrup / Hoop / Stair / Dowel | Default | `D` | `11-D29-001` |

### Floor Formatting

| Input | V2 Output |
|-------|-----------|
| `1F`, `2F`, `10F` | `F01`, `F02`, `F10` |
| `B1`, `B2`, `B5` | `B01`, `B02`, `B05` |
| `PIT` | `PIT` |
| `Roof` | `ROF` |
| `FOOTING` | `FTG` |
| `PH1`, `PH2` | `PH1`, `PH2` |

### Symbol Formatting

Standard rule: alpha prefix + 3-digit padded number + optional letter suffix.

| Raw | Padded |
|-----|--------|
| `C1` | `C001` |
| `TC2A` | `TC002A` |
| `LB200` | `LB200` |
| `B1A` | `B001A` |
| `W200` | `W200` |
| `MF1` | `MF001` |

#### Slab/Stair floor stripping

Slab and stair member_ids have floor prefix baked in. Stripped using the known `level` column:

| Raw | Level | Symbol |
|-----|-------|--------|
| `5S12` | 5F | `S012` |
| `10S210` | 10F | `S210` |
| `B4SS1` | B4 | `SS001` |
| `PITS1` | PIT | `S001` |
| `RS11` | Roof | `S011` |
| `PH2S12` | PH | `S012` |

#### Sejong (Project 3) — grid-based members

| Raw | Type | Symbol | Instance Rule |
|-----|------|--------|---------------|
| `F1-X1Y1` | Footing | `F001` | Grid sorted: Y asc, then X asc |
| `AC_X10Y1` | Column | `AC` | Grid stripped, instance from grid sort |
| `AF-G28` | Beam | `AFG028` | AF merged with beam type |

---

## Processing Order

**Phase 1: Members CSVs first** (source of truth for instance IDs)
**Phase 2: RebarLengths CSVs** (look up instance ID from Members by position)

This order is critical — rebar inherits its `member_instance_id` from the parent member, not the other way around.

---

## Instance ID Assignment Per Member Type

### MembersBeam — Globally unique per (member_id, level)

1. Group all beam rows by `(member_id, level)`
2. Sort by `x_from_mm` first, then `y_from_mm` (left-to-right, bottom-to-top)
3. Assign 1-based index sequentially: 001, 002, 003...

Each physical beam span gets a unique instance number. Same member_id on different gridlines at the same floor gets different instance numbers.

Example — LB200 at 10F with 14 spans across multiple gridlines:
```
BD01-F10-LB200-001  x=-86300, y=9250   (gridline Y1)
BD01-F10-LB200-002  x=-86300, y=18340  (gridline Y3)
BD01-F10-LB200-014  x=-20000, y=38100  (gridline Y7)
```

### MembersColumn — Position-based (member_id, x, y)

1. Group by `(member_id, x_mm, y_mm)` — each unique position is one instance
2. Sort positions by x then y
3. Assign 001, 002, ...
4. Sejong: grid-based instances with Y-then-X sorting

### MembersWall — Sequential per (wall_mark, level) or segment_id

- **With segment_id** (Sejong): extract number from `RW3-SEG001` → `001`
- **Without segment_id** (Buldang/Cheongdam): assign sequential instance per `(wall_mark, level)`, sorted by centroid position (x then y). Each quad panel gets a unique instance number.

**Fix (27Apr26)**: Previously used position-based grouping via `(wall_mark, centroid_x, centroid_y)` which collapsed multiple panels at similar positions into the same `-000` instance. Changed to sequential assignment — every quad panel now gets a unique ID.

Example — W2 at 5F with 49 panels:
```
BD01-F05-W002-001  centroid=(-76500, 2150)
BD01-F05-W002-002  centroid=(-76500, 5400)
...
BD01-F05-W002-049  centroid=(-20700, 38100)
```

### MembersBasementWall — panel_no or centroid

- **With panel_no**: use `panel_no` zero-padded to 3 digits
- **Without panel_no**: group by `(wall_mark, centroid_x_mm, centroid_y_mm)`, sort, assign 001...

### MembersSlab — Centroid or segment_id

- **With segment_id** (Sejong): extract from `S3-SEG001` → `001`
- **Without segment_id** (Buldang): group by `(member_id, centroid_x_mm, centroid_y_mm)`, sort, assign 001...

### MembersFooting — Centroid or segment_id

- **With segment_id**: extract number
- **Without segment_id**: group by `(member_id, centroid_x_mm, centroid_y_mm)`, sort, assign 001...
- Uses `level` from CSV if available, else `FTG`
- Sejong: grid-based instances with Y-then-X sorting

### MembersStair — segment_id

- Extract from `SS1-SEG001` → `001`
- Floor from `story_group` lower level (e.g., `B5~B4` → `B5`)

---

## RebarLengths Instance ID Assignment (Lookup from Members)

All rebar processors look up their `member_instance_id` from the corresponding Members table by position matching. No rebar creates its own instance — it always inherits from its parent member.

### RebarLengthsBeam — Bbox lookup from MembersBeam

For each rebar bar at `(member_id, level, x_start, y_start)`:
1. Find candidates: `MembersBeam` rows with same `(member_id, level)`
2. Check if rebar position falls within span bbox: `x_min-500 <= x_start <= x_max+500` AND `y_min-500 <= y_start <= y_max+500`
3. Take the matching span's `member_instance_id`

The 500mm tolerance handles diagonal beams and stirrups offset from beam axis.

### RebarLengthsColumn — Closest position from MembersColumn

For each rebar bar at `(member_id, level_from, x_start, y_start)`:
1. Find candidates: `MembersColumn` rows with same `(member_id, level_from)`
2. **FOOTING level fallback**: if `level_from=FOOTING` has no candidates, search all levels for the same member_id (DOWEL bars belong to the lowest-level column)
3. Find closest member by Euclidean distance

### RebarLengthsWall / BasementWall — Closest centroid from MembersWall

For each rebar bar at `(wall_mark, level, mesh_origin_x, mesh_origin_y)`:
1. Find candidates: `MembersWall` rows with same `(wall_mark, level)`
2. Find closest member by distance to centroid

### RebarLengthsSlab — Closest centroid from MembersSlab

Same as wall: match by `(member_id, level)`, find closest centroid.

### RebarLengthsFooting — Closest centroid from MembersFooting

Match by `member_id` (no level filter). Find closest centroid.

### RebarLengthsStair — segment_id extraction

Extract from `SS1-SEG001` → instance `001`. Build from `(building, floor, member_id, instance)`.

---

## Serial Number (BarMark last component)

Groups bars by identity keys, sorts by floor rank (lowest floor = 1), assigns sequential numbers.

| Member Type | Group Keys |
|-------------|-----------|
| Beam | `(segment_id, bar_position, bar_role, dia_mm, bar_type)` |
| Column | `(member_id, x_start, y_start, bar_role, dia_mm)` |
| Wall | `(wall_mark, mesh_origin_x, mesh_origin_y, bar_role, dia_mm)` |
| Slab | `(member_id, mesh_origin_x, mesh_origin_y, bar_role, dia_mm)` |
| Footing | `(member_id, mesh_origin_x, mesh_origin_y, bar_role, dia_mm)` |
| Stair | `(segment_id, dia_mm)` |

Floor ordering (lowest = 1): FTG, B5, B4, B3, B2, B1, PIT, 1F, 2F, ..., Roof

---

## Node Data Integrity Fixes

The following post-processing fixes ensure all node references in Members CSVs resolve to valid entries in Nodes.csv. These are applied after conversion and bar_id assignment.

### Footing boundary_nodes — raw MIDAS node numbers

**Problem**: The converter writes footing `boundary_nodes` using raw MIDAS node numbers (e.g., `25127;25129;25145`) instead of the converted `N_{level}_OFF{number}` format used in Nodes.csv.

**Fix**: Look up each raw node number via `node_number` column in Nodes.csv and replace with the corresponding `node_id`:
```
25127 → N_B5_OFF25127
25129 → N_B5_OFF25129
```

**Scope**:
| Project | Refs fixed |
|---------|-----------|
| Buldang | 6 (MF1, 6-node polygon) |
| Cheongdam | 16 (MF1, 4 footings) |
| Sejong | 0 (already correct) |

### Beam N_SPLIT nodes — missing from Nodes.csv

**Problem**: The converter splits long beams into segments and creates synthetic `N_SPLIT_{beam}_{level}_X{coord}` node IDs for `node_from`/`node_to`, but does NOT write these nodes to Nodes.csv — dangling references.

**Fix**: Extract coordinates from the beam's inline fields (`x_from_mm`/`x_to_mm`, `y_from_mm`/`y_to_mm`, `z_mm`) and add entries to Nodes.csv with `source=CONVERTER_SPLIT`, `grid=SPLIT`.

Example node added:
```csv
node_id,node_number,x_mm,y_mm,z_mm,level,grid,grid_offset_x_mm,grid_offset_y_mm,source
N_SPLIT_WB1_10F_X-68080,,-68080.0,16900.0,43300.0,10F,SPLIT,0.0,0.0,CONVERTER_SPLIT
```

**Scope**:
| Project | Nodes added | Beam refs after fix |
|---------|------------|-------------------|
| Buldang | 659 | 6,620/6,620 (100%) |
| Cheongdam | 7 | 482/482 (100%) |
| Sejong | 0 (none needed) | 1,730/1,730 (100%) |

### Slab N_SF nodes — slab-fill synthetic boundaries

**Context**: The slab-fill tool creates gap-filled slab panels with synthetic boundary nodes named `N_SF_{level}_{number}_{corner}` (BL/BR/TR/TL). These are written to Nodes.csv by the slab-fill tool itself.

**No fix needed** — the slab-fill tool already writes these nodes. Verified:
| Project | N_SF_ refs | Status |
|---------|-----------|--------|
| Buldang SlabFilled | 17,704 | 0 missing |
| Cheongdam SlabFilled | 104 | 0 missing |

### Validation results (all projects, 27Apr26)

| Project | Footing | Beam | Slab |
|---------|---------|------|------|
| Buldang original | 6/6 | 6,620/6,620 | 742/742 |
| Buldang SlabFilled | 6/6 | 6,620/6,620 | 18,446/18,446 |
| Cheongdam original | 16/16 | 482/482 | 123/123 |
| Cheongdam SlabFilled | 16/16 | 482/482 | 227/227 |
| Sejong original | 748/748 | 1,730/1,730 | 368/368 |
| Sejong SlabFilled | 748/748 | 1,730/1,730 | 1,452/1,452 |

**100% node resolution across all 6 datasets.**

---

## Verified Results

### Uniqueness (member_instance_id per physical member/panel)

| Project | Beam | Column | Wall | BW | Slab | Footing | Stair |
|---------|------|--------|------|----|------|---------|-------|
| Buldang | 3,310/3,310 | 432/432 | 6,467/6,467 | 40/40 | 105/107 | 1/1 | 22/22 |
| Cheongdam | 241/241 | 28/28 | 294/294 | 51/51 | 20/20 | 4/4 | 10/10 |
| Sejong | 865/865 | OK | OK | N/A | OK | OK | OK |

All unique across all projects (original + SlabFilled variants). Verified 27Apr26 data.

### Match Rates (RebarLengths member_instance_id in Members member_instance_id)

| Project | Beam | Column | Wall | BW | Slab | Footing | Stair |
|---------|------|--------|------|----|------|---------|-------|
| Buldang | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Cheongdam | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Sejong | 100% | 100% | 100% | N/A | 100% | 100% | 100% |

---

## Script Location

```
converter/output/add_bar_id.py
```

### Usage

```bash
python add_bar_id.py <input_folder> <output_folder> --building BD01
```

The `--building` flag sets the building code (V2 uses `BD01`, `BD02`, etc.).

The script:
1. Reads all Members and RebarLengths CSVs from input folder
2. Processes Members first (Phase 1) — assigns member_instance_id
3. Processes RebarLengths second (Phase 2) — looks up member_instance_id from Members + builds bar_id
4. Copies all other CSVs unchanged
5. Writes everything to output folder (input folder is NOT modified)

Node fixes (footing boundary_nodes, beam N_SPLIT nodes) are applied separately via `fix_nodes.py`.

### Key Design Decisions

1. **Members first, rebar second** — Members is the source of truth. Rebar inherits, never creates.
2. **No data modification** — only two columns added. All existing data preserved in original order.
3. **Globally unique beam instances** — instance per `(member_id, level)` sorted by `(x_from, y_from)`. NOT per-gridline (which caused duplicates). Fixed in issue #117.
4. **Position-based matching** — uses coordinate proximity (not segment_id string matching) to link rebar to members.
5. **Raw member_id for lookup** — rebar uses the original `member_id` (e.g., `WB6`) to find Members, not the formatted symbol (`WB006`).
6. **FOOTING-level column DOWEL** — falls back to matching against B1-level member when FOOTING level has no MembersColumn rows.
7. **Area members (wall, slab, footing)** — many rebar bars at different mesh positions belong to ONE physical member. All inherit the same member_instance_id via closest-centroid matching.

---

## Full Examples

### Beam
```
BD01-F02-B001A-006-4T25-001    (B1A, 2F, instance 6, 4 bars Top D25, serial 1)
BD01-F02-B001A-006-3B22-001    (same beam, 3 bars Bottom D22)
BD01-F02-B001A-006-7D10@150-001 (same beam, stirrup D10@150)
```

### Column
```
BD01-B05-C001-001-11D29-001    (C1, B5, instance 1, 11 bars D29)
BD01-B05-C001-001-6D10@150-001 (same column, hoop D10@150)
```

### Wall
```
BD01-PIT-CW001-003-12A13@150-001 (CW1, PIT, instance 3, 12 bars All-face D13@150)
```

### Basement Wall
```
BD01-B01-RW001-001-32N16@200-001 (RW1, B1, instance 1, 32 bars Near D16@200)
BD01-B01-RW001-001-32F16@200-001 (same wall, Far face)
```

### Slab
```
BD01-F05-S012-001-18T13@150-001  (5S12 -> S012, 5F, Top)
BD01-F05-S012-001-18B13@150-001  (same slab, Bottom)
```

### Footing
```
BD01-B05-MF001-001-20T19@200-001 (MF1, B5, Top)
BD01-B05-MF001-001-20B19@200-001 (same footing, Bottom)
```

### Stair
```
BD01-B04-SS001-001-9D13@150-001  (B4SS1 -> SS001, B4)
```
