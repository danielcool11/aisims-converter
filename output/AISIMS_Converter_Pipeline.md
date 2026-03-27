# AISIMS Converter Pipeline
# AISIMS 변환기 파이프라인

## Overview

The AISIMS Converter transforms raw MIDAS Gen structural analysis exports into
standardized CSV files for the AISIMS V2 BIM Builder Engine. It runs as a
Streamlit web application with a 5-phase pipeline.

```
 INPUT DATA                    CONVERTER PIPELINE                     OUTPUT
 ==========                    ==================                     ======

 Part A ─────┐
 (MIDAS Gen) │   Phase 1    Phase 2    Phase 2.5   Phase 3    Phase 5
             ├──► FOUND ──► MEMBERS ──► GRID ──► REINF ──► VALID ──► 21 CSVs
 Part B ─────┤   ATION      BERS       AUTO       ORCE      ATION     + Report
 (Engineer)  │                          DETECT     MENT
             │
 Part C ─────┘
 (Basement)
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

---

### Phase 2.5: Grid Auto-Detection

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

---

### Phase 3: Reinforcement + Design Results

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
Phase 1 ──────► Phase 2 ──────► Phase 2.5 ──────► Phase 3 ──────► Phase 5
(Foundation)    (Members)       (Grid Auto)        (Reinf.)        (Valid.)
    │               │               │                  │               │
    │               │               │                  │               │
    ▼               ▼               ▼                  ▼               ▼
 Nodes.csv     MembersBeam    Updated Nodes      ReinfBeam       Report
 Materials     MembersColumn  Updated Members    ReinfColumn
 Sections      MembersWall                       ReinfWall
 Lookups       MembersSlab                       ReinfSlab
               MembersStair                      ReinfStair
               MembersFooting                    ReinfFooting
               MembersBasementWall               ReinfBasementWall
                                                 DesignResults×3
```

**Strict order:**
1. Phase 1 must complete before Phase 2 (sections/nodes needed)
2. Phase 2 must complete before Phase 2.5 (column positions needed)
3. Phase 2.5 must complete before Phase 3 (grid labels in Members)
4. Phase 3 must complete before Phase 5 (all outputs needed for validation)

**Independent within phase:**
- Within Phase 2: slabs, stairs, footings, basement walls can run in parallel
- Within Phase 3: beam, column, wall, slab, stair reinforcement can run in parallel

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
                        │  │Phase 2.5 │  ├─ footings.py               │
                        │  │ GRID     │  └─ basement_walls.py         │
                        │  └────┬─────┘                               │
                        │       │        Reinforcement:               │
                        │       ▼        ├─ reinforcement_beam.py     │
                        │  ┌──────────┐  ├─ reinforcement_column.py   │
                        │  │ Phase 3  │  ├─ reinforcement_wall.py     │
                        │  │ REINF.   │  ├─ reinforcement_slab.py     │
                        │  └────┬─────┘  └─ reinforcement_stair.py    │
                        │       │                                     │
                        │       ▼        Grid:                        │
                        │  ┌──────────┐  └─ grid_detect.py            │
                        │  │ Phase 5  │                               │
                        │  │ VALID.   │  Validation:                  │
                        │  └────┬─────┘  └─ validation.py             │
                        │       │                                     │
                        │       ▼                                     │
                        │  ┌──────────┐                               │
                        │  │ OUTPUT   │──► 21 CSVs + ValidationReport │
                        │  │ (ZIP)    │──► Download as .zip           │
                        │  └──────────┘                               │
                        └─────────────────────────────────────────────┘
```

---

## Tier 2: Rebar Length Calculators (separate module)

The Tier 2 calculators consume Tier 1 output to compute bar-by-bar lengths.
They are NOT part of the Streamlit pipeline but run independently.

```
Tier 1 CSVs ──────────► Tier 2 Calculators ──────► RebarLengths CSVs

Available:
  tier2/rebar_lengths_beam.py    ──► RebarLengthsBeam.csv (2,801 records P1)
  tier2/rebar_lengths_column.py  ──► RebarLengthsColumn.csv (91 records P1)

Pending:
  tier2/rebar_lengths_wall.py    ──► RebarLengthsWall.csv
  tier2/rebar_lengths_slab.py    ──► RebarLengthsSlab.csv
  tier2/rebar_lengths_stair.py   ──► RebarLengthsStair.csv
  tier2/rebar_lengths_footing.py ──► RebarLengthsFooting.csv

Shared resources:
  config/development_lengths.csv  (486 rows: 3 fy × 6 fc × 3 member_types × 9 bars)
  config/lap_splice.csv           (486 rows: same coverage)
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

| Project | Nodes | Elements | Processing Time |
|---------|-------|----------|----------------|
| Project 1 (청담동, 10 stories + 4 basements) | 744 | 1,042 | ~5 seconds |
| Project 2 (불당동, 15 stories + 5 basements) | 9,435 | 10,476 | ~15 seconds |

---

## Repository

GitHub: https://github.com/danielcool11/aisims-converter
Run: `cd D:\Redo AISIMS\converter && venv\Scripts\streamlit run app.py`
