# AISIMS Converter Pipeline
# AISIMS 변환기 파이프라인

## Overview

The AISIMS Converter transforms raw MIDAS Gen structural analysis exports into
standardized CSV files for the AISIMS V2 BIM Builder Engine. It runs as a
Streamlit web application with a 6-phase pipeline.

```
 INPUT DATA                          CONVERTER PIPELINE                              OUTPUT
 ==========                          ==================                              ======

 Part A ─────┐
 (MIDAS Gen) │  Phase 1   Phase 2   Phase 3   Phase 4   Phase 5   Phase 6
             ├──► FOUND ─► MEMB ──► GRID ──► REINF ──► VALID ──► REBAR ──► 28 CSVs
 Part B ─────┤   ATION    ERS       AUTO      ORCE      ATION     LENGTHS   + Report
 (Engineer)  │                      DETECT    MENT                (Tier 2)
             │                                                        │
 Part C ─────┘                       ┌────────────────────────────────┘
 (Basement)                          │  Tier 1 DataFrames passed
                                     │  in-memory (no re-upload)
                                     └──► 7 RebarLengths CSVs
```

---

## Input Data Sources

### Part A — MIDAS Gen Exports (10 files)

| File | Content | Required |
|------|---------|----------|
| Nodes.csv | Node coordinates (Node, X, Y, Z) | Yes |
| Materials.csv | Material properties (40+ columns) | Yes |
| Sections.csv | Section definitions (25 columns) | Yes |
| Elements.csv | Element connectivity (16 columns) | Yes |
| Thickness.csv | Wall thickness properties | Yes |
| StoryDefinition.csv | Level names and elevations | Yes |
| DesignBeam.csv | Beam design results (3-row blocks) | Yes |
| DesignColumn.csv | Column design results (2-row blocks) | Yes |
| DesignWall.csv | Wall design results (2-row blocks) | Conditional |
| project.mgt | MIDAS Gen text file (rebar grades, wall marks) | Optional |

### Part B — Engineer Input Data (5 files)

| File | Content | Required |
|------|---------|----------|
| SlabBoundary.csv | Slab boundary polygons with node references | Yes |
| SlabReinforcement.csv | Slab rebar per direction/layer | Yes |
| StairReinforcement.csv | Stair geometry + rebar (with member_id) | Yes |
| FootBoundary.csv | Foundation boundary nodes | Yes |
| FootReinforcement.csv | Foundation rebar with zone definitions | Yes |

### Part C — Basement Walls (1 Excel file, conditional)

| Sheet | Content | Required |
|-------|---------|----------|
| BasementWall Boundary | Wall panel nodes with zone dimensions | If basement exists |
| BasementWall Reinforcement | Rebar per direction/face/zone | If basement exists |

---

## Pipeline Phases

### Phase 1: Foundation Data

**Purpose**: Build the base lookup tables that all subsequent phases depend on.

```
Nodes.csv ──────────────► convert_nodes() ──────────► Nodes.csv (output)
StoryDefinition.csv ────┘                              │
                                                       │ node lookup
Materials.csv ──────────► convert_materials() ─────► Materials.csv
project.mgt ────────────┘                              │
                                                       │ material info
Sections.csv ───────────► convert_sections() ──────► Sections.csv
Thickness.csv ──────────┘   │                          │
StoryDefinition.csv ────────┘ (level resolution)       │
                                                       ▼
                                              section_lookup dict
                                              thickness_lookup dict
```

**Key operations:**
- **Nodes**: Match Z coordinates to StoryDefinition levels (tolerance 100mm). Assign grid labels if manual grid provided.
- **Materials**: Consolidate duplicate concrete grades (3x C35 → 1). Derive rebar grades from MGT file (SD400/500/600).
- **Sections**: Parse section names using 20+ prefix patterns. Handle both joined (`6C1`) and space-separated (`1 B1`, `TC1 (1-P)`) conventions. Resolve ambiguous levels (`P` → PIT or PH based on StoryDefinition). Deduplicate section_ids.

**Error handling:**
- Missing StoryDefinition → nodes get `Z{value}` as level fallback
- Missing MGT → default SD400 rebar added
- Unparseable section name → member_type = UNKNOWN (flagged in validation)

#### Pseudocode: convert_nodes()

```
Load StoryDefinition → build level_z_map {level_name → z_mm}

FOR each raw node (node_number, x, y, z):
  Match z to nearest StoryDefinition level (tolerance 100mm)
  IF match found: level = matched level name
  ELSE:           level = "Z{z_value}" (fallback)

  IF manual grid provided:
    Match (x, y) to nearest grid intersection (tolerance 50mm)
    IF on grid: node_id = "N_{level}_{gridX}{gridY}", grid = "{gridX}{gridY}"
    ELSE:       node_id = "N_{level}_OFF{node_number}", grid = "OFF_GRID"
  ELSE:
    node_id = "N_{level}_OFF{node_number}", grid = "OFF_GRID"

  Emit: node_id, node_number, x_mm, y_mm, z_mm, level, grid, offsets
```

#### Pseudocode: convert_materials()

```
Load raw Materials.csv (40+ columns per material)
IF MGT file exists:
  Parse *MATERIAL section → extract rebar grades (SD400/500/600)

FOR each material:
  Detect type: CONCRETE (contains fck) or REBAR (from MGT)
  IF CONCRETE: extract fck_MPa, normalize name (e.g., "C35")
  Deduplicate: 3× C35 with same properties → 1 row

Emit: material_id, material_type, fck_MPa, fy_MPa, ...
Append rebar materials from MGT (SD400, SD500, SD600)
```

#### Pseudocode: convert_sections()

```
Load Sections.csv + Thickness.csv
Build StoryDefinition lookup for level resolution

FOR each section:
  Parse section name using 20+ prefix patterns:
    Joined format:    "6C1" → level=6F, type=COLUMN, base=C1
    Space-separated:  "1 B1" → level=1F, type=BEAM, base=B1
    Parenthetical:    "TC1 (1-P)" → level=1F~P, type=COLUMN, base=TC1
    Prefixes: C, TC, B, TB, G, TG, W, BT, RG, CB, WCG, TWG, ...
  Resolve ambiguous levels: "P" → PIT or PH (check StoryDefinition)
  Determine member_type: BEAM, COLUMN, WALL from prefix
  Extract dimensions: b_mm, h_mm from section properties

Merge Thickness entries (wall/slab thickness definitions)
Deduplicate section_ids (first wins, rest added to lookup)

Emit: section_id, member_type, level, b_mm, h_mm, ...
Build: section_lookup dict, thickness_lookup dict
```

---

### Phase 2: Members

**Purpose**: Convert raw elements into categorized structural members.

```
Elements.csv ────────────► convert_elements() ─────► MembersBeam.csv
section_lookup ──────────┘   │                       MembersColumn.csv
thickness_lookup ────────────┘                       MembersWall.csv
node_lookup ─────────────────┘
wall_marks (MGT) ────────────┘

                              ┌─ Polymorphic FK:
                              │  BEAM type → Sections table
                              │  WALL type → Thickness table
                              │
                              ├─ Orientation check:
                              │  Vertical + COLUMN prefix → Column
                              │  Horizontal + BEAM prefix → Beam
                              │  Vertical + WALL/BT prefix → Wall
                              │
                              └─ Link beam detection:
                                 BEAM type + Thickness property + horizontal
                                 → labeled as ELEM_{id}

SlabBoundary.csv ────────────► convert_slabs() ────► MembersSlab.csv
SlabReinforcement.csv ───────┘   │                     │
node_lookup ─────────────────────┘                     │ stair_boundaries
                                  │                    ▼
                                  ├─ Filter: SS/ST suffix → stair
                                  └─ Remaining → slab members

StairReinforcement.csv ──────► convert_stairs() ───► MembersStair.csv (73 cols)
stair_boundaries ────────────┘   │
node_lookup ─────────────────────┘
walls (for wall detection) ──────┘
                                  │
                                  ├─ 8-point U-shaped geometry
                                  ├─ Wall side auto-detection from core walls
                                  ├─ Flight centerline inset (stair_width/2)
                                  └─ Gap = boundary_Lx - 2×width

FootBoundary.csv ────────────► convert_footings() ─► MembersFooting.csv
FootReinforcement.csv ───────┘                       ReinforcementFooting.csv
                                  │
                                  ├─ Nodes % 4 == 0 → split into quads
                                  ├─ Otherwise → polygon (Shoelace area)
                                  └─ Zones: BASE / ADDITIONAL / STIRRUP

BasementWall Excel ──────────► convert_basement_walls() ► MembersBasementWall.csv
node_lookup ─────────────────┘                             ReinforcementBasementWall.csv
                                  │
                                  ├─ Panels: 4 nodes per quad
                                  ├─ Zone dimensions (Left/Middle/Right × Top/Middle/Bottom)
                                  └─ Composite bars: D13+D16@100 → 2 rows @ doubled spacing
```

**Slanted column handling:**
- Columns with different X/Y at bottom vs top → `length_mm > height_mm`
- TC1/TC2/TC3 in Project 1 shift from grid X3 (basement) to X1 (upper floors)
- Rebar calculator uses `length_mm` for bar length, not `height_mm`

**Node merging:**
- FootBoundary nodes (1001-1040 in P1) merged into main Nodes.csv
- Tagged with `source=BOUNDARY` to distinguish from MIDAS nodes

#### Pseudocode: convert_elements()

```
FOR each element (node_i, node_j, section_id, ...):
  Lookup section → get member_type, dimensions
  Get node coordinates → compute orientation

  IF section references Thickness table (not Sections):
    IF vertical: type = WALL
    IF horizontal: type = LINK_BEAM (labeled ELEM_{id})

  Classify by orientation + section prefix:
    Horizontal + BEAM prefix → MembersBeam
    Vertical + COLUMN prefix → MembersColumn
    Vertical + WALL/BT prefix → MembersWall

  FOR beams:
    length_mm = distance(node_i, node_j)
    Assign: x_from_mm, y_from_mm, x_to_mm, y_to_mm, level, grid, design_key

  FOR columns:
    height_mm = |z_top - z_bottom|
    length_mm = 3D distance(node_i, node_j)  (for slanted columns)
    x_top_mm, y_top_mm (may differ from bottom for slanted)

  FOR walls:
    height_mm, width_mm from aggregated node positions
    wall_id from numeric element ID
    wall_mark from MGT wall marks lookup

Emit: MembersBeam.csv, MembersColumn.csv, MembersWall.csv
```

#### Pseudocode: convert_slabs()

```
FOR each slab boundary entry:
  Parse boundary nodes (semicolon-separated)
  Lookup node coordinates → compute bounding box
  Filter out stair entries (SS/ST suffix in member_id)

  Compute:
    Lx_mm, Ly_mm from bounding box
    centroid_x/y from average of boundary node coordinates
    z_mm from node Z (should be consistent for a slab)
    area_mm2 from bounding box (or Shoelace for polygons)
    thickness_mm from linked Thickness entry

Emit: MembersSlab.csv
```

#### Pseudocode: convert_stairs()

```
FOR each stair reinforcement entry (contains geometry + rebar):
  Extract: member_id, level, stair_width, landing dimensions, riser/tread
  Identify boundary nodes → lookup coordinates
  Auto-detect wall side from core wall positions

  Build 8-point U-shaped geometry model:
    P1 = lower landing start (at wall)
    P2 = lower landing end (flight start)
    P3 = flight 1 bottom
    P4 = flight 1 top = mid landing start
    P5 = mid landing end (flight 2 start)
    P6 = flight 2 bottom
    P7 = flight 2 top = upper landing start
    P8 = upper landing end (at wall)

  Flight centerline inset by stair_width/2 from boundary
  Flight 2 end Z = z_start + total_height (not P3's Z)
  Gap = boundary_Lx - 2 × stair_width

Emit: MembersStair.csv (73 columns per stair)
```

#### Pseudocode: convert_footings()

```
FOR each footing boundary entry:
  Parse boundary nodes
  IF node_count % 4 == 0: split into quads (rectangular panels)
  ELSE: treat as polygon (compute area via Shoelace formula)

  Group by member_id → detect multi-part footings (MF1-1, MF1-2)
  Compute: bounding box, centroid, area, Lx, Ly
  Boundary nodes use semicolon separator (Excel compatibility)

FOR each footing reinforcement entry:
  Parse zone_type: BASE / ADDITIONAL / STIRRUP
  Parse zone_boundary (pipe-separated quads for L-shaped footprints)
  Parse bar specs: D19@250, 5-10@200

Emit: MembersFooting.csv, ReinforcementFooting.csv
```

#### Pseudocode: convert_basement_walls()

```
FOR each boundary row:
  Group by (wall_name, level) → collect nodes
  Fill zone dimensions from first non-null row per group
  Build raw-to-converted node ID mapping

Validate nodes:
  Check existence in Nodes.csv AND Z in basement range (≤ 0)
  Compute wall reference XY from valid basement nodes
  Compute Z centroid via height stacking (not from node Z)
  Mark node_status: OK / PARTIAL / INFERRED / MISSING

Split nodes into panels (4 per quad)

FOR each reinforcement row:
  Map 12 column positions to (direction, face, zone):
    H_Int.(Left), H_Ext.(Left), ..., V_Int.(Top), V_Ext.(Top), ...
  Handle composite bars: D13+D16@100 → 2 rows with doubled spacing
  Parse bar specs via rebar_spec parser

Emit: MembersBasementWall.csv, ReinforcementBasementWall.csv
```

---

### Phase 3: Grid Auto-Detection

**Purpose**: Detect grid lines from column positions when no manual grid is provided.

```
MembersColumn (x_mm, y_mm) ──► detect_grid_from_columns() ──► grid_x, grid_y
                                    │
                                    ├─ Cluster X positions (tolerance 100mm)
                                    ├─ Cluster Y positions (tolerance 100mm)
                                    ├─ Min 2 columns per grid line
                                    └─ Label: X1, X2, ... / Y1, Y2, ...

grid_x, grid_y ──────────────► reassign_node_grids() ──────► Updated Nodes.csv
Nodes.csv ───────────────────┘
                                    │
                                    ├─ Match each node to nearest grid intersection
                                    ├─ Within 50mm tolerance → on-grid
                                    ├─ Beyond tolerance → OFF_GRID
                                    └─ Compute grid_offset_x/y_mm

Re-run convert_elements() with updated nodes → updated grid labels in Members
```

**When skipped:**
- Manual grid entry → grid already assigned in Phase 1
- Grid CSV upload → positions provided directly

#### Pseudocode: detect_grid_from_columns()

```
Collect all column (x_mm, y_mm) positions across all levels
Cluster X positions (tolerance 100mm) → grid_x lines
Cluster Y positions (tolerance 100mm) → grid_y lines
Filter: require minimum 2 columns per grid line
Label: X1, X2, X3, ... / Y1, Y2, Y3, ... (sorted by position)

reassign_node_grids(nodes, grid_x, grid_y):
  FOR each node:
    Find nearest grid_x label (within 50mm)
    Find nearest grid_y label (within 50mm)
    IF both match: grid = "{gridX}{gridY}", update node_id
    ELSE: grid = "OFF_GRID", keep OFF node_id
    Compute grid_offset_x/y_mm (distance from nearest grid)

  Re-run convert_elements() with updated grid labels in nodes
```

---

### Phase 4: Reinforcement + Design Results

**Purpose**: Parse MIDAS design output into reinforcement specifications and design capacity data.

```
DesignBeam.csv ──────────► convert_reinforcement_beam() ──► ReinforcementBeam.csv
section_lookup ──────────┘                                  DesignResultsBeam.csv
                              │
                              ├─ 3-row blocks: I (start), M (mid), J (end)
                              ├─ Parse main bars: 6-4-D22 → total=10, main=6, add=4
                              ├─ Parse stirrups: 3-D10 @150 → legs=3, dia=10, sp=150
                              ├─ Pipe format: 4|5-D13 @150 → legs=5 (max)
                              └─ Split: rebar → Reinforcement, ratios → DesignResults

DesignColumn.csv ────────► convert_reinforcement_column() ► ReinforcementColumn.csv
section_lookup ──────────┘                                  DesignResultsColumn.csv
                              │
                              ├─ 2-row blocks: data row + geometry row
                              ├─ Main + tie_end + tie_mid in one row
                              └─ Split: rebar → Reinforcement, ratios → DesignResults

DesignWall.csv ──────────► convert_reinforcement_wall() ──► ReinforcementWall.csv
                              │                             DesignResultsWall.csv
                              ├─ 2-row blocks: design + story/geometry
                              ├─ V + H bars paired in one row
                              ├─ wall_id added for FK joining
                              └─ Split: rebar → Reinforcement, ratios → DesignResults

SlabReinforcement.csv ───► convert_reinforcement_slab() ──► ReinforcementSlab.csv
                              │
                              ├─ Each slab → 4 rows (X/Y × Top/Bot)
                              ├─ Composite bars split into 2 rows
                              └─ Level normalized (R→Roof, 1→1F)

StairReinforcement.csv ──► convert_reinforcement_stair() ─► ReinforcementStair.csv
                              │
                              ├─ Each stair → 8 rows (zone × direction × layer)
                              ├─ Zones: landing_left, landing_right, stair
                              └─ Level normalized
```

**member_id handling:**
- Reinforcement member_ids have level prefixes: `-1B11`, `6G1`, `TC1 (1-P)`
- Members use base IDs: `B11`, `G1`, `TC1`
- `design_key` column in Members stores the raw name for FK joining
- Validation uses `_extract_base_member_id()` for coverage checking

#### Pseudocode: convert_reinforcement_beam()

```
Parse DesignBeam.csv in 3-row blocks (I=start, M=mid, J=end):
  Row 1: section_id, element_id, fck, fy, fys, b, h
  Row 2: main bar spec + stirrup spec at position I/M/J
  Row 3: design ratios (Mu, Vu, rho, etc.)

FOR each 3-row block:
  Parse main bars: "6-4-D22" → main_count=6, additional=4, dia=22, total=10
  Parse stirrups: "3-D10 @150" → legs=3, dia=10, spacing=150
    Handle pipe format: "4|5-D13@150" → legs=max(4,5)=5
    Handle no-D-prefix: "5-10@200"

  Split output:
    ReinforcementBeam → member_id, position(I/M/J), bar specs, counts
    DesignResultsBeam → member_id, position, Mu, Vu, rho, ratios

Emit: ReinforcementBeam.csv, DesignResultsBeam.csv
```

#### Pseudocode: convert_reinforcement_column()

```
Parse DesignColumn.csv in 2-row blocks:
  Row 1: section_id, element_id, fck, main bar spec, tie specs
  Row 2: b, h, height, design ratios

FOR each block:
  Parse main: "24-8-D29" → main_count=24, additional=8, dia=29, total=32
  Parse tie_end + tie_mid: "3-D13 @150" → legs, dia, spacing

  Split output:
    ReinforcementColumn → member_id, main bar props, tie end/mid props
    DesignResultsColumn → member_id, Pu, Mu, ratios

Emit: ReinforcementColumn.csv, DesignResultsColumn.csv
```

#### Pseudocode: convert_reinforcement_wall()

```
Parse DesignWall.csv in 2-row blocks:
  Row 1: wall_id, V bar spec, H bar spec, design ratios
  Row 2: story, height, thickness, geometry

FOR each block:
  Parse V bars: "D13 @150" → dia=13, spacing=150
  Parse H bars: "D10 @200" → dia=10, spacing=200
  Add wall_id (numeric) for FK joining (wall_mark naming differs)

  Split output:
    ReinforcementWall → wall_id, wall_mark, v/h bar specs
    DesignResultsWall → wall_id, wall_mark, shear ratios

Emit: ReinforcementWall.csv, DesignResultsWall.csv
```

#### Pseudocode: convert_reinforcement_slab()

```
FOR each slab reinforcement entry:
  Parse bar spec: "D10@200" → dia=10, spacing=200
  Handle composite: "D13+D16@100" → 2 rows @ doubled spacing (D13@200 + D16@200)

  Expand: each slab → 4 rows (X_Top, X_Bot, Y_Top, Y_Bot)
  Normalize level names (R→Roof, 1→1F)

Emit: ReinforcementSlab.csv
```

#### Pseudocode: convert_reinforcement_stair()

```
FOR each stair reinforcement entry:
  Expand into 8 rows per stair:
    landing_left × (longitudinal, transverse) × (Top, Bot)
    landing_right × (longitudinal, transverse) × (Top, Bot)
    stair × (longitudinal, transverse) × (Top, Bot)
  Parse bar specs per zone
  Normalize level names

Emit: ReinforcementStair.csv
```

---

### Phase 5: Validation

**Purpose**: Cross-check all outputs for consistency and completeness.

```
All outputs ──────────────► validate_outputs() ────► ValidationReport.txt

Checks performed:
  1. Completeness      — all output DataFrames non-empty
  2. Materials         — at least 1 concrete + 1 rebar
  3. Beam geometry     — no zero-length beams
  4. Column geometry   — no zero-height columns
  5. Grid coverage     — % of nodes on-grid
  6. Beam reinf coverage — base member_id matching
  7. Column reinf coverage — base member_id matching
  8a. Beam design key  — every element's design_key in DesignResultsBeam
  8b. Column design key — every element's design_key in DesignResultsColumn
  8c. Wall design key  — wall_id matching (numeric, not wall_mark)
  9. Section types     — no UNKNOWN member_types
```

**Result categories:**
- **PASS**: Check passed
- **WARN**: Issue detected but not blocking (e.g., low grid coverage, missing design keys for known gaps)
- **FAIL**: Critical issue (e.g., no concrete materials)

---

## Phase Dependencies

```
Phase 1 ─► Phase 2 ─► Phase 3 ─► Phase 4 ─► Phase 5 ─► Phase 6
(Found.)   (Members)  (Grid)     (Reinf.)   (Valid.)   (RebarLen)
   │          │          │           │          │           │
   ▼          ▼          ▼           ▼          ▼           ▼
 Nodes     Members×7  Updated    Reinf×5     Report    RebarLengths×7
 Materials            Nodes      Design×3
 Sections             Members
 Lookups
```

**Strict order:**
1. Phase 1 must complete before Phase 2 (sections/nodes needed)
2. Phase 2 must complete before Phase 3 (column positions needed)
3. Phase 3 must complete before Phase 4 (grid labels in Members)
4. Phase 4 must complete before Phase 5 (all outputs needed for validation)
5. Phase 5 must complete before Phase 6 (Tier 1 data needed for rebar calculation)

**Independent within phase:**
- Within Phase 2: slabs, stairs, footings, basement walls can run in parallel
- Within Phase 4: beam, column, wall, slab, stair reinforcement can run in parallel
- Within Phase 6: all 7 rebar length calculators can run in parallel

---

## Data Flow Diagram

```
                        ┌─────────────────────────────────────────────┐
                        │           STREAMLIT APP (app.py)            │
                        │                                             │
  Part A ──────────────►│  ┌──────────┐                               │
  (10 CSV + 1 MGT)      │  │ Phase 1  │ Parsers:                     │
                        │  │ FOUND.   │ ├─ section_name.py (20+ prefixes)
  Part B ──────────────►│  │          │ ├─ rebar_spec.py (main/stirrup/composite)
  (5 CSV)               │  └────┬─────┘ ├─ mgt.py (rebar grades, wall marks)
                        │       │        └─ level_normalizer.py (R→Roof, P→PIT)
  Part C ──────────────►│       ▼                                     │
  (1 Excel)             │  ┌──────────┐                               │
                        │  │ Phase 2  │ Converters:                   │
  Grid Definition ─────►│  │ MEMBERS  │ ├─ nodes.py                   │
  (manual/auto/CSV)     │  │          │ ├─ materials.py               │
                        │  └────┬─────┘ ├─ sections.py                │
                        │       │        ├─ elements.py                │
                        │       ▼        ├─ slabs.py                  │
                        │  ┌──────────┐  ├─ stairs.py (8-point model) │
                        │  │ Phase 3  │  ├─ footings.py               │
                        │  │ GRID     │  └─ basement_walls.py         │
                        │  └────┬─────┘                               │
                        │       │        Reinforcement:               │
                        │       ▼        ├─ reinforcement_beam.py     │
                        │  ┌──────────┐  ├─ reinforcement_column.py   │
                        │  │ Phase 4  │  ├─ reinforcement_wall.py     │
                        │  │ REINF.   │  ├─ reinforcement_slab.py     │
                        │  └────┬─────┘  └─ reinforcement_stair.py    │
                        │       │                                     │
                        │       ▼        Grid:                        │
                        │  ┌──────────┐  └─ grid_detect.py            │
                        │  │ Phase 5  │                               │
                        │  │ VALID.   │  Validation:                  │
                        │  └────┬─────┘  └─ validation.py             │
                        │       │                                     │
                        │       ▼        Tier 2:                      │
                        │  ┌──────────┐  ├─ rebar_lengths_beam.py     │
                        │  │ Phase 6  │  ├─ rebar_lengths_column.py   │
                        │  │ REBAR    │  ├─ rebar_lengths_slab.py     │
                        │  │ LENGTHS  │  ├─ rebar_lengths_stair.py    │
                        │  └────┬─────┘  ├─ rebar_lengths_wall.py     │
                        │       │        ├─ rebar_lengths_footing.py  │
                        │       │        ├─ rebar_lengths_basement_wall│
                        │       │        └─ stock_split.py (shared)   │
                        │       ▼                                     │
                        │  ┌──────────┐                               │
                        │  │ OUTPUT   │──► 28 CSVs + ValidationReport │
                        │  │ (ZIP)    │──► Download as .zip           │
                        │  └──────────┘                               │
                        └─────────────────────────────────────────────┘
```

---

### Phase 6: Rebar Lengths (Tier 2)

**Purpose**: Compute bar-by-bar lengths from Tier 1 output for BIM rebar modeling.

```
Tier 1 DataFrames ──► Tier 2 Calculators ──► RebarLengths CSVs
(in-memory)              │
                         ├─ rebar_lengths_beam.py       ──► RebarLengthsBeam.csv
                         ├─ rebar_lengths_column.py     ──► RebarLengthsColumn.csv
                         ├─ rebar_lengths_slab.py       ──► RebarLengthsSlab.csv
                         ├─ rebar_lengths_stair.py      ──► RebarLengthsStair.csv
                         ├─ rebar_lengths_wall.py       ──► RebarLengthsWall.csv
                         ├─ rebar_lengths_footing.py    ──► RebarLengthsFooting.csv
                         └─ rebar_lengths_basement_wall.py ► RebarLengthsBasementWall.csv

Shared resources:
  config/development_lengths.csv  (486 rows: 3 fy × 6 fc × 3 member_types × 9 bars)
  config/lap_splice.csv           (486 rows: same coverage)
  config/cover_requirements.csv   (member_type → cover_mm)
  tier2/stock_split.py            (shared >12m bar splitting utility)
```

**Data flow:** Phase 6 consumes Tier 1 DataFrames directly from memory (same objects
that get written to the ZIP download). No re-upload or file I/O needed.

**Conditional execution:** Each calculator only runs if its required Tier 1 inputs exist.
Each is wrapped in try/except so a failure in one does not block the others.

---

#### Pseudocode: Beam Rebar Lengths

```
FOR each beam gridline group (clustered by perpendicular coordinate):
  Sort beams along the gridline by position
  FOR each span:
    Lookup reinforcement (TOP/BOT at I/M/J positions)
    Determine bar_role based on span position:
      SINGLE (1 span), START/INTERMEDIATE/END (multi-span)
    FOR each bar position (TOP, BOT):
      bar_length = clear_span
      IF start is free edge:  bar_length += Ldh (hook)
      ELSE:                   bar_length += Llap (lap into adjacent span)
      IF end is free edge:    bar_length += Ldh (hook)
      ELSE:                   bar_length += Llap (lap)
      IF bar_length > 12000mm:
        split into pieces via stock_split
      Compute splice zone coordinates
      Emit record with x_start/y_start/z_start → x_end/y_end/z_end
    FOR stirrups (END zone + MID zone):
      stirrup_length = 2*(b + h) - 8*cover + 2*hook
      n_stirrups = zone_length / spacing
      Emit record
```

#### Pseudocode: Column Rebar Lengths

```
Group columns by (grid, member_id)
Merge slanted stacks (same member_id, Z-continuous, different XY)
FOR each wall stack:
  Split into continuous groups (Z-based gap detection, tolerance 200mm)
  FOR each continuous group:
    Sort by Z (bottom to top)
    FOR each level in group:
      Determine role: BOTTOM / INTERMEDIATE / TOP / SINGLE
      Main bars:
        IF BOTTOM:       length = height + Lpc (lap at top)
        IF INTERMEDIATE: length = height + Lpc (lap at top)
        IF TOP:          length = height + Ldh (hook at top)
        IF SINGLE:       length = height + Ldh
        Use length_mm (3D) for slanted columns, not height_mm
      Hoops:
        END zone = 2 × max(h, b, height/6, 450mm)
        MID zone = height - 2 × end_zone
        hoop_length = 2*(b + h) - 8*cover + 2*hook
        n_hoops = zone_length / spacing
      Emit records with x_start/y_start/z_start → x_end/y_end/z_end
```

#### Pseudocode: Slab Rebar Lengths

```
Build panel bounds from boundary nodes (bounding box per slab)
Build beam width lookup (for clear span calculation)

FOR each slab panel:
  FOR each direction (X, Y):
    Detect panel role via coordinate adjacency:
      Find panels sharing edges → SINGLE/START/INTERMEDIATE/END
    Check thickness mismatch with adjacent panels
    FOR each layer (Top, Bot):
      Determine anchorage strategy:
        Top bars: hook at free edges, lap at continuous edges
        Bot bars: hook at thickness mismatch, lap at same-thickness
      clear_span = panel_span - 0.5*(beam_width_1 + beam_width_2)
      bar_length = clear_span + anchorage_start + anchorage_end
      n_bars = perpendicular_width / spacing + 1
      z_bar = panel_z ± (thickness/2 - cover - dia/2)
      IF bar_length > 12000mm:
        split into pieces via stock_split (with coordinate interpolation)
      Emit record with mesh_origin → mesh_terminus, distribution_axis
```

#### Pseudocode: Stair Rebar Lengths

```
FOR each stair (8-point U-shaped geometry model):
  Extract geometry: P1-P8 points, A (lower landing), B (width), C (mid landing)
  Compute flight slope vectors and lengths
  Lookup dev/lap lengths for stair bar diameter

  Emit 10 bar types per stair:
    #1  Lower landing TOP along A     = A (hook at wall)
    #2  Lower landing BOT along A     = A + lap_bot (extends into flight slope)
    #3  Lower landing DIST span B     = B + Ldh + dia (hook at wall)
    #4  Mid landing TOP along C       = C + lap_top (extends from flight slope)
    #5  Mid landing BOT along C       = C (hook at wall)
    #6  Mid landing DIST span B       = B + Ldh + dia
    #7  Flight slope TOP              = slope_length + lap_top + lap_top
    #8  Flight slope BOT              = slope_length + lap_bot + lap_bot
    #9  Flight transverse             = flight_width + 2*Ldh
    #10 (Flight 2 mirrors Flight 1)

  Each record: start/end 3D coordinates + width_dir vector + width_span
```

#### Pseudocode: Wall Rebar Lengths

```
Group walls by wall_id → sort by level
Split into continuous groups (Z-based gap detection, tolerance 200mm)

FOR each continuous group:
  FOR each level:
    Determine vertical bar role: BOTTOM/INTERMEDIATE/TOP/SINGLE
    Aggregate node positions for wall segment XY bounds

    VERTICAL bars:
      IF BOTTOM at foundation: emit DOWEL + main bar with lap at top
      IF BOTTOM above foundation: main bar with lap at top
      IF TOP: main bar with hook at top (wall terminates)
      IF INTERMEDIATE: main bar with lap at top
      n_bars = (width - 2*cover) / spacing × face_multiplier (×2 for Double)
      IF bar_length > 12000mm: stock_split

    HORIZONTAL bars:
      U_bar_length = width + (thickness - 2*cover)
      n_bars = (height - 2*cover) / spacing × face_multiplier
      IF bar_length > 12000mm: stock_split

    Emit records with mesh_origin → mesh_terminus
```

#### Pseudocode: Footing Rebar Lengths

```
FOR each reinforcement zone:
  IF zone_type == BASE:
    Parse zone_boundary into sub-rectangles (pipe-separated quads)
    Compute bar groups:
      X-direction: each sub-rect generates bars spanning its X-width
      Y-direction: merge Y-adjacent sub-rects sharing X-overlap
                   → continuous bars across L-shaped footprint
    FOR each bar group:
      bar_length = clear_span + 2*Ldh (hook at both free edges)
      n_bars = distribution_width / spacing + 1
      z_bar = footing_z ± (thickness/2 - cover - dia/2)
      Stock split (mat foundations easily exceed 12m)

  IF zone_type == ADDITIONAL:
    Simple rectangular zone
    bar_length = zone_span + 2*Ldh
    n_bars = perpendicular_width / spacing + 1

  IF zone_type == STIRRUP:
    bar_length = thickness - 2*cover + 2*hook_extension
    n_total = n_x × n_y × n_legs (grid of vertical ties)
```

#### Pseudocode: Basement Wall Rebar Lengths

```
Skip panels with node_status != OK (unreliable coordinates)
Group panels by wall_mark
Separate full-height (B4~B1) from per-level panels

FOR full-height panels:
  Process as FULL_HEIGHT role (hook at top for each vertical zone)

FOR per-level panels:
  Split into continuous groups (Z-based, same as standard wall)
  Determine role per level: BOTTOM/INTERMEDIATE/TOP/SINGLE

FOR each panel:
  VERTICAL bars (per zone: TOP/MIDDLE/BOTTOM):
    zone_height from zone_height_top/middle/bottom_mm
    bar_length = zone_height + Ldh or Lpc (based on role)
    n_bars = (wall_length - 2*cover) / spacing
    Stock split if needed

  HORIZONTAL bars (per zone: LEFT/MIDDLE/RIGHT):
    zone_width from zone_width_left/middle/right_mm
    U_bar_length = zone_width + (thickness - 2*cover)
    n_bars = (height - 2*cover) / spacing
    Stock split if needed

  Each zone × each face (INTERIOR/EXTERIOR) = separate record
```

#### Stock Split Algorithm (shared)

```
IF bar_length <= 12000mm:
  RETURN [original bar]  (no split needed)

n_pieces = ceil(bar_length / 12000)
total_material = bar_length + (n_pieces - 1) × L_lap
piece_length = total_material / n_pieces

FOR each piece i = 1..n_pieces:
  t_start = (i-1) / n_pieces
  t_end = i / n_pieces
  Interpolate start/end coordinates along original bar line
  piece.length_mm = piece_length
  piece.split_piece = i
  piece.split_total = n_pieces
  piece.original_length_mm = bar_length

RETURN [piece_1, piece_2, ..., piece_n]
```

---

## Error Recovery

| Scenario | Behavior |
|----------|----------|
| Missing Part A file | Error: "Missing required files" — blocks conversion |
| Missing Part B file | Skipped: corresponding Members/Reinforcement not generated |
| Missing Part C Excel | Skipped: no basement wall output |
| Missing MGT file | Fallback: default SD400 rebar, no wall marks |
| Unparseable section name | member_type = UNKNOWN, flagged in validation |
| Element references Thickness instead of Sections | Auto-detect: vertical → wall, horizontal → link beam (ELEM_{id}) |
| Level "P" ambiguous | Resolved against StoryDefinition (PIT or PH) |
| Composite bars (D13+D16@100) | Split into 2 rows with doubled spacing |
| Pipe stirrups (4\|5-D13@150) | Take max leg count (5) |
| File encoding issues | Try utf-8-sig first, fallback to cp949 |
| Duplicate section_ids | First entry kept, duplicates added to lookup only |
| Wall mark mismatch (W204 vs wM0204) | Join by wall_id (numeric), not wall_mark |

---

## Performance

| Project | Nodes | Elements | Tier 1 | Tier 2 | Total Output |
|---------|-------|----------|--------|--------|-------------|
| P1 (청담동, 10F + 4B) | 744 | 1,042 | ~5s | ~3s | 21 + 7 CSVs (3,866 rebar records) |
| P2 (불당동, 15F + 5B) | 9,435 | 10,476 | ~15s | ~8s | 21 + 6 CSVs (no basement wall) |

---
