# AISIMS Raw Data Format Reference

**Version:** 2.0
**Date:** 2026-03-28
**Reference Project:** Project 1 (청담동 78-5)

This document describes the raw input data format expected by the AISIMS Converter.
Data is organized into three parts based on source.

---

## Part A — MIDAS Gen Exports

Exported directly from MIDAS Gen structural analysis software.
All CSVs use UTF-8 encoding with header row. Units are in mm and N.

### A1. Nodes.csv (Required)

**Source:** MIDAS Gen → Results → Result Tables → Node Table
**Rows:** 704 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| Node | int | — | Node number (unique ID) |
| X_mm | float | mm | X coordinate |
| Y_mm | float | mm | Y coordinate |
| Z_mm | float | mm | Z coordinate (negative = below ground) |

```
Node, X_mm, Y_mm, Z_mm
1, 0, 0, -18100
7, 6360, 0, -18100
```

### A2. Materials.csv (Required)

**Source:** MIDAS Gen → Properties → Material Properties
**Rows:** 3 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| ID | int | — | Material ID |
| Name | string | — | Material name (e.g., "C35(Beam,Column)") |
| Type | string | — | Concrete, Steel, etc. |
| Standard | string | — | Design code (KS19(RC)) |
| DB | string | — | Database grade (C35) |
| fck_N/mm² | float | MPa | Concrete compressive strength |
| fy_N/mm² | float | MPa | Main rebar yield strength |
| fys_N/mm² | float | MPa | Shear rebar yield strength |
| ... | | | (40 columns total, most are optional properties) |

```
ID, Name, Type, ..., fck_N/mm², fy_N/mm², fys_N/mm²
1, C35(Beam,Column), Concrete, ..., 35, 400, 400
```

**Note:** Multiple materials may share the same grade (e.g., 3 entries all C35). Converter deduplicates by grade.

### A3. Sections.csv (Required)

**Source:** MIDAS Gen → Properties → Section Properties
**Rows:** 197 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| ID | int | — | Section ID |
| Name | string | — | Section name with level+type encoding |
| Shape | string | — | Shape definition (e.g., "SB\|Solid Rectangle") |
| Size (H)_mm | float | mm | Section height |
| Size (B)_mm | float | mm | Section width |
| Area_mm² | float | mm² | Cross-sectional area |
| Iyy_mm⁴ | float | mm⁴ | Moment of inertia Y |
| Izz_mm⁴ | float | mm⁴ | Moment of inertia Z |
| ... | | | (25 columns total) |

**Section name encoding:**

| Pattern | Example | Level | Type | Base ID |
|---------|---------|-------|------|---------|
| Joined | 6C1 | 6F | COLUMN | C1 |
| Joined | 3~4TC1 | 3F~4F | COLUMN | TC1 |
| Joined | B3G1 | B3 | BEAM | G1 |
| Space-separated | 1 B1 | 1F | BEAM | B1 |
| Parenthetical | TC1 (1-P) | 1F~P | COLUMN | TC1 |
| Wall | BT1 | — | WALL | BT1 |

**Recognized prefixes:** C, TC, TCG, B, TB, G, TG, W, BT, RG, CB, WCG, TWG, LB, S, SS, ST

### A4. Elements.csv (Required)

**Source:** MIDAS Gen → Results → Result Tables → Element Table
**Rows:** 1,042 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| Element | int | — | Element ID |
| Type | string | — | BEAM (covers beams, columns, walls) |
| Wall Type | string | — | Wall sub-type (if applicable) |
| Sub Type | string | — | Sub-type code |
| Wall ID | int | — | Wall identifier (0 if not a wall) |
| Material | int | — | Material ID (FK to Materials) |
| Property | int | — | Section or Thickness ID (FK) |
| B-Angle | float | deg | Beta angle |
| Node1 | int | — | Start node (FK to Nodes) |
| Node2 | int | — | End node (FK to Nodes) |
| Node3-8 | int | — | Additional nodes (typically empty for line elements) |

**Polymorphic FK:** The `Property` column references either Sections.ID (for beams/columns) or Thickness.ID (for walls). The converter auto-detects based on which table contains the ID.

### A5. Thickness.csv (Required)

**Source:** MIDAS Gen → Properties → Thickness Properties
**Rows:** 8 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| ID | int | — | Thickness ID |
| NAME | string | — | Thickness name (e.g., "Core", "W200") |
| Type | string | — | Definition type (Value) |
| In,Out | string | — | In-plane/out-of-plane flag |
| Thick-In_mm | float | mm | Thickness value |

### A6. StoryDefinition.csv (Required)

**Source:** MIDAS Gen → Structure → Story
**Rows:** 12 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| Module Name | string | — | Module name (Base) |
| Story Name | string | — | Level name (Roof, 6F, 5F, ..., B1, B2, B3, B4) |
| Level_mm | float | mm | Absolute elevation |
| Height_mm | float | mm | Story height (0 for Roof) |
| Floor Diaphragm | string | — | Diaphragm type (Consider) |

```
Module Name, Story Name, Level_mm, Height_mm, Floor Diaphragm
Base, Roof, 32300, 0, Consider
Base, 6F, 28900, 3400, Consider
Base, 5F, 25500, 3400, Consider
...
Base, B4, -13700, 4400, Consider
```

### A7. DesignBeam.csv (Required)

**Source:** MIDAS Gen → Design → RC Design → Beam Design Results
**Rows:** 536 (P1), **No header row** (row 1 is sub-header)
**Block structure:** 3 rows per beam element

| Row | Content |
|-----|---------|
| Row 1 | Sub-header: SECT, Bc, Hc, fy, ..., Rebar, ... |
| Row 2 | Section name, fck, position (I/M/J), rebar specs, stirrup specs |
| Row 3 | Design ratios: Mu, Vu, rho, etc. |

**Key columns (row 2):**

| Position | Content | Example |
|----------|---------|---------|
| Col A | Section name | 6G1 |
| Col F | Position | I, M, or J |
| Col H | Negative rebar spec | 6-4-D22 |
| Col N | Positive rebar spec | 6-4-D22 |
| Col T | Stirrup spec | 3-D10 @150 |

**Rebar notation:**
- Main bars: `N1-N2-Dxx` → N1 main + N2 additional bars of diameter xx
- Stirrups: `N-Dxx @sss` → N legs, diameter xx, spacing sss
- Pipe format: `4|5-D13@150` → max(4,5) = 5 legs

### A8. DesignColumn.csv (Required)

**Source:** MIDAS Gen → Design → RC Design → Column Design Results
**Rows:** 33 (P1), **No header row**
**Block structure:** 2 rows per column element

| Row | Content |
|-----|---------|
| Row 1 | Sub-header: SECT, Bc, Hc, Height, fys, ... |
| Row 2 | Section name, fck, fy, main bar spec, tie specs, design ratios |

**Key columns (row 2):**

| Position | Content | Example |
|----------|---------|---------|
| Col A | Section name | 6C1 |
| Col I | Main bar spec | 12-4-D22 |
| Col P | Tie end spec | 3-D10 @150 |
| Col S | Tie mid spec | 3-D10 @200 |

### A9. DesignWall.csv (Conditional)

**Source:** MIDAS Gen → Design → RC Design → Wall Design Results
**Rows:** 243 (P1), **No header row**
**Block structure:** 2 rows per wall element

| Row | Content |
|-----|---------|
| Row 1 | Sub-header: Story, Lw, HTw, hw, fys, ... |
| Row 2 | Wall ID, wall mark, dimensions, V-rebar, H-rebar, design ratios |

**Key columns (row 2):**

| Position | Content | Example |
|----------|---------|---------|
| Col A | Wall ID | 13 |
| Col C | Wall mark | CW1 |
| Col I | Vertical rebar | D13 @150 |
| Col W | Horizontal rebar | D10 @200 |

### A10. project.mgt (Optional)

**Source:** MIDAS Gen → File → Save As → MGT format
**Size:** ~1 MB (P1)
**Encoding:** UTF-8 text file with `*SECTION_NAME` delimited blocks

**Sections used by converter:**

| Section | Content | Used for |
|---------|---------|----------|
| *REBAR-MATL-CODE | Rebar material grades | SD400/500/600 detection |
| *NODE | All node definitions | Fallback node lookup |
| *ELEMENT | All element definitions | Cross-reference |
| *MATERIAL | Material properties | fck extraction |
| *SECTION | Section definitions | Name parsing |
| *WALL-SSRF | Wall surface definitions | Wall mark mapping |
| *STORY | Story definitions | Level assignment |

**Format:**
```
*REBAR-MATL-CODE
; iSEQ, MAT_ID, SECT_ID, POSI, GRADE
1, 1, 0, MAIN, KS-SD400
2, 1, 0, SUB, KS-SD400
```

---

## Part B — Engineer Input Data

Manually prepared by the structural engineer. Contains data not available in MIDAS exports.
Provided as either individual CSVs or a single Excel workbook with 5 sheets.

### B1. SlabBoundary.csv (Required)

**Source:** MIDAS Gen → Initial Forces → Floor Load → export boundary nodes, with engineer-added slab IDs
**Rows:** 70 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| NO | int | — | Load case number |
| Load_Type | string | — | Load type |
| Distribution Type | string | — | Two Way / One Way |
| Nodes for Loading Area | string | — | Comma-separated node numbers defining slab boundary |
| Slab NO. | string | — | Engineer-assigned slab ID (e.g., B3S1, 1FS3) |
| ... | | | (15 columns total, most from MIDAS export) |

**Note:** The `Slab NO.` column (last column) is added by the engineer. Stair entries use SS/ST suffix (e.g., B3SS1) and are filtered out during slab conversion.

### B2. SlabReinforcement.csv (Required)

**Source:** Engineer manual input
**Rows:** 20 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Slab ID matching SlabBoundary (e.g., B3S1) |
| position | string | — | Level (B3, 1F, etc.) |
| slab_type | string | — | Slab type code (C, etc.) |
| thickness_mm | int | mm | Slab thickness |
| X_Top | string | — | X-direction top rebar spec (e.g., D10@200) |
| X_Bot. | string | — | X-direction bottom rebar spec |
| Y_Top | string | — | Y-direction top rebar spec |
| Y_Bot. | string | — | Y-direction bottom rebar spec |

**Rebar spec format:** `Dxx@sss` (diameter @ spacing)
**Composite format:** `D13+D16@100` (alternating bars, converter splits into 2 rows)

### B3. StairReinforcement.csv (Required)

**Source:** Engineer manual input (geometry + reinforcement combined)
**Rows:** 10 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Stair ID (e.g., B3SS1) |
| level_start | string | — | Lower level (B4) |
| level_end | string | — | Upper level (B3) |
| Stair_Thickness_mm | int | mm | Flight slab thickness |
| Stair_Height_mm | int | mm | Total stair height (story height) |
| Stair_Width_mm | int | mm | Flight width |
| Stair_Length_mm | int | mm | Flight horizontal length |
| landing(Left)_mm | int | mm | Lower landing length |
| landing(Right)_mm | int | mm | Upper (mid) landing length |
| riser_height | float | mm | Riser height |
| tread depth | float | mm | Tread depth |
| landing(Left)_transverse_Top | string | — | Rebar spec (e.g., D13@150) |
| landing(Left)_transverse_Bot. | string | — | Rebar spec |
| landing(Left)_longitudinal_Top | string | — | Rebar spec |
| landing(Left)_longitudinal_Bot. | string | — | Rebar spec |
| Stair_transverse_Top | string | — | Flight transverse rebar |
| Stair_transverse_Bot. | string | — | Flight transverse rebar |
| Stair_longitudinal_Top | string | — | Flight longitudinal rebar |
| Stair_longitudinal_Bot. | string | — | Flight longitudinal rebar |

**Note:** P2 has additional columns for Stair_Thickness_mm, and may have different bar diameters for landing vs flight (e.g., D13 landing + D10 flight).

### B4. FootBoundary.csv (Required)

**Source:** MIDAS Gen → Query → Node Detail Table, with engineer-added footing IDs
**Encoding:** CP949 (Korean Windows encoding)
**Rows:** 56 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| NODE | int | — | Node number |
| X_mm | float | mm | X coordinate |
| Y_mm | float | mm | Y coordinate (header shows X but is actually Y) |
| Z_mm | float | mm | Z coordinate (header shows X but is actually Z) |
| Foot No. | string | — | Footing ID (MF1, MF2) |
| Position | string | — | Level (B4, B3) |

**Note:** Column headers have encoding issues (mm shows as garbled Korean). The actual order is NODE, X, Y, Z, Foot_ID, Level.

**Footing shapes:**
- 4n nodes → split into n rectangular quads
- Other counts → polygon (area computed via Shoelace formula)

### B5. FootReinforcement.csv (Required)

**Source:** Engineer manual input
**Rows:** 7 (P1)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Footing ID (MF1, MF2) |
| position | string | — | Level (B4) |
| slab_type | string | — | Type code (C) |
| thickness_mm | float | mm | Foundation thickness |
| X_Top | string | — | X-direction top rebar (D19@250) |
| X_Bot. | string | — | X-direction bottom rebar |
| Y_Top | string | — | Y-direction top rebar |
| Y_Bot. | string | — | Y-direction bottom rebar |
| STR | string | — | Stirrup spec (e.g., 5-10@200) |

**Additional reinforcement zones** (R1, R2, etc.) and **stirrup zones** (V1) appear as additional rows with zone-specific rebar.

---

## Part C — Basement Wall Data

Manually prepared by the structural engineer for basement/retaining walls.
Provided as a single Excel workbook with 2 sheets.

### C1. BasementWall Boundary (sheet)

**Source:** Engineer manual input
**Rows:** 144 (P1), **Header at row 2** (row 1 is title)
**Nodes per wall-level:** 4 per panel (multiple panels possible)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| Node | int | — | MIDAS node number at wall corner |
| NAME | string | — | Wall name (BW1, BW1A, DW1, FW1) |
| Position | string | — | Level (B1, B2, B3, B4) |
| Length_mm | int | mm | Wall plan length |
| Height_mm | int | mm | Wall story height |
| Left_mm | int | mm | Left zone width |
| Middle_mm | int | mm | Middle zone width |
| Right_mm | int | mm | Right zone width |
| Top_mm | int | mm | Top zone height |
| Middle_mm | int | mm | Middle zone height |
| Bottom_mm | int | mm | Bottom zone height |

**Structure:** Each wall-level has 4 rows (4 corner nodes). Zone dimensions may be null in the first row and filled in subsequent rows.

**Wall naming:**
- BW = Basement Wall (exterior)
- DW = Division Wall (interior partition)
- FW = Foundation Wall (at footing level)

**Full-height walls:** Position = "B4~B1" means the wall spans all basement levels as a single panel (not split per level).

### C2. BasementWall Reinforcement (sheet)

**Source:** Engineer manual input
**Rows:** 27 (P1), **Header at row 2** (row 1 is title)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| NAME | string | — | Wall name |
| Position | string | — | Level |
| TYP | string | — | Wall type (A, B) |
| THK. | int | mm | Wall thickness |
| H_Int.(Left) | string | — | Horizontal, Interior face, Left zone |
| H_Ext.(Left) | string | — | Horizontal, Exterior face, Left zone |
| H_Int.(Middle) | string | — | Horizontal, Interior face, Middle zone |
| H_Ext.(Middle) | string | — | Horizontal, Exterior face, Middle zone |
| H_Int.(Right) | string | — | Horizontal, Interior face, Right zone |
| H_Ext.(Right) | string | — | Horizontal, Exterior face, Right zone |
| V,Int.(Top) | string | — | Vertical, Interior face, Top zone |
| V,Ext.(Top) | string | — | Vertical, Exterior face, Top zone |
| V,Int.(Middle) | string | — | Vertical, Interior face, Middle zone |
| V,Ext.(Middle) | string | — | Vertical, Exterior face, Middle zone |
| V,Int.(Bottom) | string | — | Vertical, Interior face, Bottom zone |
| V,Ext.(Bottom) | string | — | Vertical, Exterior face, Bottom zone |

**Rebar spec format:** `Dxx@sss` (e.g., D13@200)
**Composite format:** `D13+D16@100` → alternating bars, split into 2 rows with doubled spacing

**3×3 zone grid:**
```
             LEFT        MIDDLE       RIGHT
          ┌───────────┬───────────┬───────────┐
  TOP     │ V,Int/Ext │ V,Int/Ext │ V,Int/Ext │  ← zone_height_top
          ├───────────┼───────────┼───────────┤
  MIDDLE  │ V,Int/Ext │ V,Int/Ext │ V,Int/Ext │  ← zone_height_middle
          ├───────────┼───────────┼───────────┤
  BOTTOM  │ V,Int/Ext │ V,Int/Ext │ V,Int/Ext │  ← zone_height_bottom
          └───────────┴───────────┴───────────┘
            zone_w_L    zone_w_M    zone_w_R

  Horizontal bars: LEFT / MIDDLE / RIGHT zones (same height)
  Vertical bars: TOP / MIDDLE / BOTTOM zones (same width)
  Each zone has Interior + Exterior face reinforcement
```

---

## Grid Definition (Manual Entry or CSV)

Not part of the structural data — entered by the user in the converter UI or uploaded as CSV.

### Grid CSV Format (Optional)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| axis | string | — | X or Y |
| label | string | — | Grid label (X1, X2, Y1, Y2, ...) |
| position_mm | float | mm | Coordinate position |

**Alternative:** User enters grid positions directly in the Streamlit UI text fields:
```
X-axis: X1=0, X2=6360, X3=11600
Y-axis: Y1=0, Y2=7800, Y3=15000
```

If no grid is provided, Phase 3 auto-detects from column positions.

---

## File Organization

Recommended folder structure for raw data delivery:

```
Project_Name/
├── Part_A/                          ← MIDAS Gen exports
│   ├── Nodes.csv                    (required)
│   ├── Materials.csv                (required)
│   ├── Sections.csv                 (required)
│   ├── Elements.csv                 (required)
│   ├── Thickness.csv                (required)
│   ├── StoryDefinition.csv          (required)
│   ├── DesignBeam.csv               (required)
│   ├── DesignColumn.csv             (required)
│   ├── DesignWall.csv               (conditional — if walls designed)
│   └── project.mgt                  (optional — for rebar grades, wall marks)
│
├── Part_B/                          ← Engineer input
│   ├── SlabBoundary.csv             (required)
│   ├── SlabReinforcement.csv        (required)
│   ├── StairReinforcement.csv       (required)
│   ├── FootBoundary.csv             (required)
│   └── FootReinforcement.csv        (required)
│
└── Part_C_BasementWall.xlsx         ← Basement walls (conditional)
    ├── BasementWall Boundary        (sheet)
    └── BasementWall Reinforcement   (sheet)
```

**Encoding:**
- Part A CSVs: UTF-8 with BOM (utf-8-sig) — MIDAS default
- Part B CSVs: UTF-8 or CP949 (Korean Windows)
- Part C Excel: .xlsx (openpyxl compatible)
- project.mgt: UTF-8 text

**Note:** Part B may also be delivered as a single Excel workbook (`Part_B_수평부재 입력 Data.xlsx`) with 5 sheets matching the 5 CSV files.
