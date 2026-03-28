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

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | ID | int | — | Material ID |
| 2 | Name | string | — | Material name (e.g., "C35(Beam,Column)") |
| 3 | Type | string | — | Concrete, Steel, etc. |
| 4 | Standard | string | — | Design code (KS19(RC)) |
| 5 | Code | — | — | (empty) |
| 6 | DB | string | — | Database grade (C35) |
| 7 | Product | — | — | (empty) |
| 8 | Use Mass Density | int | — | Flag (0/1) |
| 9 | Elasticity(N/mm²) | float | MPa | Elastic modulus |
| 10 | Poisson | float | — | Poisson's ratio |
| 11 | Thermal(1/[C]) | float | 1/°C | Thermal expansion coefficient |
| 12 | Density(N/mm³) | float | N/mm³ | Weight density |
| 13 | Mass Density(N/mm³/g) | float | — | Mass density |
| 14-21 | Standard2 ~ Mass Density2 | — | — | Secondary properties (typically empty) |
| 22 | Plastic Matl. | — | — | (empty) |
| 23 | Sp. Heat | float | — | Specific heat |
| 24 | Heat Co. | float | — | Heat conductivity |
| 25 | Material Type | string | — | Isotropic / Orthotropic |
| 26-34 | Shear Mod._xy ~ Poisson_yz | float | — | Anisotropic properties (0 for isotropic) |
| 35 | fck_N/mm² | int | MPa | Concrete compressive strength |
| 36 | Standard.1 | string | — | Rebar design code |
| 37 | Grade(Main Rebar) | string | — | Main rebar grade (SD600) |
| 38 | Grade(Sub Rebar) | string | — | Sub rebar grade (SD500) |
| 39 | fy_N/mm² | int | MPa | Main rebar yield strength |
| 40 | fys_N/mm² | int | MPa | Shear rebar yield strength |

**Note:** Multiple materials may share the same grade (e.g., 3 entries all C35). Converter deduplicates by grade. Columns 35-40 are the most important for the converter.

### A3. Sections.csv (Required)

**Source:** MIDAS Gen → Properties → Section Properties
**Rows:** 197 (P1)

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | ID | int | — | Section ID |
| 2 | Name | string | — | Section name with level+type encoding |
| 3 | Shape | string | — | Shape definition (e.g., "SB\|Solid Rectangle") |
| 4 | DB | — | — | Database reference (typically empty) |
| 5 | Section | — | — | (empty) |
| 6 | Offset | string | — | Section offset (Center-Center) |
| 7 | CC type | — | — | (empty) |
| 8 | Built-Up | string | — | Built-up flag |
| 9 | Shear Deform | int | — | Shear deformation flag (0/1) |
| 10 | Size (H)_mm | int | mm | Section height |
| 11 | Size (B)_mm | int | mm | Section width |
| 12 | Area_mm² | int | mm² | Cross-sectional area |
| 13 | Asy_mm² | float | mm² | Shear area Y |
| 14 | Asz_mm² | float | mm² | Shear area Z |
| 15 | Ixx_mm⁴ | float | mm⁴ | Torsional moment of inertia |
| 16 | Iyy_mm⁴ | int | mm⁴ | Moment of inertia Y |
| 17 | Izz_mm⁴ | float | mm⁴ | Moment of inertia Z |
| 18 | Cyp_mm | int | mm | Centroid Y+ |
| 19 | Cym_mm | int | mm | Centroid Y- |
| 20 | Czp_mm | int | mm | Centroid Z+ |
| 21 | Czm_mm | int | mm | Centroid Z- |
| 22 | Qyb_mm³ | int | mm³ | First moment of area Y |
| 23 | Qzb_mm³ | float | mm³ | First moment of area Z |
| 24 | Peri.(Out)_mm | int | mm | Outer perimeter |
| 25 | Peri.(In)_mm | int | mm | Inner perimeter (0 for solid) |

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

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | Element | int | — | Element ID |
| 2 | Type | string | — | BEAM (covers beams, columns, walls) |
| 3 | Wall Type | string | — | Wall sub-type (typically empty) |
| 4 | Sub Type | string | — | Sub-type code (typically empty) |
| 5 | Wall ID | int | — | Wall identifier (0 if not a wall) |
| 6 | Material | int | — | Material ID (FK to Materials) |
| 7 | Property | int | — | Section or Thickness ID (polymorphic FK) |
| 8 | B-Angle([deg]) | int | deg | Beta angle |
| 9 | Node1 | int | — | Start node (FK to Nodes) |
| 10 | Node2 | int | — | End node (FK to Nodes) |
| 11 | Node3 | int | — | Additional node (0 for line elements) |
| 12 | Node4 | int | — | Additional node (0 for line elements) |
| 13 | Node5 | int | — | Additional node (0 for line elements) |
| 14 | Node6 | int | — | Additional node (0 for line elements) |
| 15 | Node7 | int | — | Additional node (0 for line elements) |
| 16 | Node8 | int | — | Additional node (0 for line elements) |

**Polymorphic FK:** The `Property` column references either Sections.ID (for beams/columns) or Thickness.ID (for walls). The converter auto-detects based on which table contains the ID.

### A5. Thickness.csv (Required)

**Source:** MIDAS Gen → Properties → Thickness Properties
**Rows:** 8 (P1)

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | ID | int | — | Thickness ID |
| 2 | NAME | string | — | Thickness name (e.g., "Core", "W200") |
| 3 | Type | string | — | Definition type (Value) |
| 4 | In,Out | string | — | In-plane/out-of-plane flag (Yes/No) |
| 5 | Thick-In_mm | int | mm | Thickness value (in-plane) |
| 6 | Thick-In_mm.1 | int | mm | Thickness value (out-of-plane, typically 0) |
| 7 | Offset | string | — | Offset flag (No) |
| 8 | Offset Type | string | — | Offset type (Ratio) |
| 9 | Offset Ratio | int | — | Offset ratio value |
| 10 | Offset Value_mm | int | mm | Offset distance |

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
**Rows:** 536 (P1), 24 columns
**Block structure:** 3 rows per beam element (sub-header + data + ratios)

| # | Header (row 1) | Sub-header (row 2) | Data row example | Description |
|---|----------------|-------------------|------------------|-------------|
| 1 | MEMB | SECT | 6G1 | Section name (with level prefix) |
| 2 | SEL | — | — | Selection flag |
| 3 | Section | Bc | 500 | Section width (mm) |
| 4 | C | Hc | 1200 | Section height (mm) |
| 5 | fck | fy | 400 | Yield strength / fck (MPa) |
| 6 | POS | — | I / M / J | Design position |
| 7 | CHK | — | OK | Check result |
| 8 | Negative Moment | Rebar | 6-4-D22 | Negative moment main bar spec |
| 9 | H | As.use | 3041 | Steel area used (mm²) |
| 10 | I | N(-) Mu | 1234 | Negative moment (kN·m) |
| 11 | J | LCB | 5 | Load combination |
| 12 | L | N(-) φMn | 1500 | Negative moment capacity |
| 13 | M | Rat-N | 0.82 | Negative moment ratio |
| 14 | Positive Moment | Rebar | 6-4-D22 | Positive moment main bar spec |
| 15 | O | As.use | 3041 | Steel area used |
| 16 | P | P(+) Mu | 800 | Positive moment |
| 17 | Q | LCB | 3 | Load combination |
| 18 | S | P(+) φMn | 1500 | Positive moment capacity |
| 19 | T | Rat-P | 0.53 | Positive moment ratio |
| 20 | Shear Strength | Stirrup | 3-D10 @150 | Stirrup spec |
| 21 | V | Vu | 500 | Shear force (kN) |
| 22 | X | LCB | 5 | Load combination |
| 23 | Y | φVc | 300 | Shear capacity |
| 24 | AC | Rat-V | 0.65 | Shear ratio |

**Rebar notation:**
- Main bars: `N1-N2-Dxx` → N1 main + N2 additional bars of diameter xx
- Stirrups: `N-Dxx @sss` → N legs, diameter xx, spacing sss
- Pipe format: `4|5-D13@150` → max(4,5) = 5 legs

### A8. DesignColumn.csv (Required)

**Source:** MIDAS Gen → Design → RC Design → Column Design Results
**Rows:** 33 (P1), 18 columns
**Block structure:** 2 rows per column element (sub-header + data)

| # | Header (row 1) | Sub-header (row 2) | Data row example | Description |
|---|----------------|-------------------|------------------|-------------|
| 1 | MEMB | SECT | 6C1 | Section name |
| 2 | SEL | — | — | Selection flag |
| 3 | Section | Bc | 600 | Section width (mm) |
| 4 | C | Hc | 600 | Section height (mm) |
| 5 | fck | Height | 3400 | Story height (mm) |
| 6 | fy | fys | 400 | Shear rebar yield strength |
| 7 | CHK | — | OK | Check result |
| 8 | LCB | — | 5 | Load combination |
| 9 | V-Rebar | — | 12-4-D22 | Main bar spec |
| 10 | φPn-max | — | 15000 | Max axial capacity |
| 11 | Pu | Rat-P | 0.35 | Axial force / ratio |
| 12 | MF.y | MF.z | 1.2 | Moment magnification factor |
| 13 | Mcy | Rat-My | 0.45 | Moment Y / ratio |
| 14 | Mcz | Rat-Mz | 0.38 | Moment Z / ratio |
| 15 | LCB.1 | — | 3 | Shear load combination |
| 16 | H-Rebar.end | H-Rebar.mid | 3-D10 @150 / 3-D10 @200 | Tie spec end / mid |
| 17 | Vu.end | Vu.mid | 250 / 180 | Shear force end / mid |
| 18 | Rat-V.end | Rat-V.mid | 0.65 / 0.45 | Shear ratio end / mid |

### A9. DesignWall.csv (Conditional)

**Source:** MIDAS Gen → Design → RC Design → Wall Design Results
**Rows:** 243 (P1), 26 columns
**Block structure:** 2 rows per wall element (sub-header + data)

| # | Header (row 1) | Sub-header (row 2) | Data row example | Description |
|---|----------------|-------------------|------------------|-------------|
| 1 | Wall ID | Story | B4 | Wall element ID / story |
| 2 | SEL | — | — | Selection flag |
| 3 | Wall Mark | Lw | 4530 | Wall mark / wall length (mm) |
| 4 | D | HTw | 200 | Thickness (mm) |
| 5 | fck | hw | 4400 | fck / wall height (mm) |
| 6 | fy | fys | 400 | Yield strengths (MPa) |
| 7 | CHK | — | OK | Check result |
| 8 | LCB | — | 5 | Load combination |
| 9 | V-Rebar | H-Rebar | D13 @150 | Vertical / horizontal rebar spec |
| 10 | End-Rebar | Bar Layer | Double | End zone rebar / layer type |
| 11 | φPn-max | Pu | 3000 | Axial capacity / force |
| 12 | Rat-Py | Rat-Pz | 0.25 | Axial ratios |
| 13 | MF.y | MF.z | 1.0 | Moment magnification |
| 14 | Mcy | Mcz | 800 | Moment capacities |
| 15 | Rat-My | Rat-Mz | 0.30 | Moment ratios |
| 16 | Vu | Rat-V | 200 | Shear force / ratio |
| 17 | CHK.1 | — | — | Secondary check |
| 18 | V-Rebar.1 | ρ.max(%) | 1.2 | Alt V-rebar / max reinforcement ratio |
| 19 | AL | ρ.use(%) | 0.8 | Used reinforcement ratio |
| 20 | AM | ρ.min(%) | 0.25 | Min reinforcement ratio |
| 21 | AN | s.max | 300 | Max spacing |
| 22 | AO | s.use | 150 | Used spacing |
| 23 | H-Rebar | ρ.use(%) | 0.5 | Horizontal rebar ratio |
| 24 | AQ | ρ.min(%) | 0.25 | Min horizontal ratio |
| 25 | AR | s.max | 300 | Max horizontal spacing |
| 26 | AS | s.use | 200 | Used horizontal spacing |

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

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | NO | int | — | Load case number |
| 2 | Load_Type | string | — | Load type (활하중, 고정하중, etc.) |
| 3 | Distribution Type | string | — | Two Way / One Way |
| 4 | Load Angle | int | deg | Load angle (typically 0) |
| 5 | Sub Beam No | int | — | Sub beam number (typically 0) |
| 6 | Sub Beam Angle_[deg] | int | deg | Sub beam angle (typically 0) |
| 7 | Unit Self Weight(N/mm²) | int | — | Unit self weight (typically 0) |
| 8 | Load Direction | string | — | Global Z |
| 9 | Projection | string | — | No |
| 10 | Nodes for Loading Area | string | — | Comma-separated node numbers defining slab boundary |
| 11 | Description | string | — | (typically empty) |
| 12 | Exclude Inner Elem. | int | — | Exclusion flag (0/1) |
| 13 | Allow Polygon Type | int | — | Polygon flag (0/1) |
| 14 | Group | string | — | Group name (Default) |
| 15 | Slab NO. | string | — | Engineer-assigned slab ID (e.g., B3S1, 1FS3) |

**Note:** Columns 1-14 are from MIDAS Gen floor load export. Column 15 (`Slab NO.`) is added by the engineer. Stair entries use SS/ST suffix (e.g., B3SS1) and are filtered out during slab conversion.

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

| # | Column | Type | Unit | Description |
|---|--------|------|------|-------------|
| 1 | member_id | string | — | Stair ID (e.g., B3SS1) |
| 2 | level_start | string | — | Lower level (B4) |
| 3 | level_end | string | — | Upper level (B3) |
| 4 | Stair_Thickness_mm | int | mm | Flight slab thickness |
| 5 | Stair_Height_mm | int | mm | Total stair height (story height) |
| 6 | Stair_Width_mm | int | mm | Flight width |
| 7 | Stair_Length_mm | int | mm | Flight horizontal length |
| 8 | landing(Left)_mm | int | mm | Lower landing length |
| 9 | landing(Right)_mm | int | mm | Upper (mid) landing length |
| 10 | riser_height | float | mm | Riser height |
| 11 | tread depth | float | mm | Tread depth |
| 12 | landing(Left)_transverse_Top | string | — | Landing transverse top rebar (e.g., D13@150) |
| 13 | landing(Left)_transverse_Bot. | string | — | Landing transverse bottom rebar |
| 14 | landing(Left)_longitudinal_Top | string | — | Landing longitudinal top rebar |
| 15 | landing(Left)_longitudinal_Bot. | string | — | Landing longitudinal bottom rebar |
| 16 | Stair_transverse_Top | string | — | Flight transverse top rebar |
| 17 | Stair_transverse_Bot. | string | — | Flight transverse bottom rebar |
| 18 | Stair_longitudinal_Top | string | — | Flight longitudinal top rebar |
| 19 | Stair_longitudinal_Bot. | string | — | Flight longitudinal bottom rebar |

**Note:** P2 may have different bar diameters for landing vs flight (e.g., D13 landing + D10 flight). Column 4 (Stair_Thickness) may be absent in some projects — extracted from Excel source if CSV drops it.

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
