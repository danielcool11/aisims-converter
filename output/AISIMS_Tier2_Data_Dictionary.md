# AISIMS Tier 2 Data Dictionary — RebarLengths CSVs

**Version:** 2.0
**Date:** 2026-03-27

Tier 2 rebar length calculators consume Tier 1 output and produce bar-by-bar length records for BIM modeling. Each record represents a group of identical bars with computed lengths, anchorage, and placement coordinates.

---

## Common Columns

The following columns appear in all 7 RebarLengths files:

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int/float | mm | Bar spacing (0 or null for count-based bars) |
| n_bars | int | — | Number of bars in this group |
| length_mm | int | mm | Individual bar length (after anchorage, before stock split) |
| total_length_mm | int | mm | Total material length (length_mm x n_bars) |
| split_piece | float | — | Stock split piece number (null if bar <= 12m) |
| split_total | float | — | Total pieces after stock split (null if no split) |
| original_length_mm | float | mm | Original bar length before stock split (null if no split) |

### Coordinate Conventions

Two coordinate systems are used depending on bar placement type:

**Individual bar placement** (Beam, Column, Stair):
- `x_start_mm, y_start_mm, z_start_mm` — bar start point
- `x_end_mm, y_end_mm, z_end_mm` — bar end point

**Distributed bar mesh** (Slab, Wall, Footing, Basement Wall):
- `mesh_origin_x/y/z_mm` — mesh start point
- `mesh_terminus_x/y/z_mm` — mesh end point
- `mesh_distribution_axis` — axis along which n_bars are distributed

### Stock Split

Bars exceeding 12m stock length are split into multiple pieces with lap splices at each joint. The shared utility (`tier2/stock_split.py`) computes:
- Number of pieces: `ceil(length / 12000)`
- Piece length: `(original_length + (n_pieces - 1) x lap) / n_pieces`
- Coordinates interpolated for each piece

---

## 1. RebarLengthsBeam.csv (40 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| segment_id | string | — | Unique segment ID (e.g., B1-SEG001) |
| level | string | — | Story level (1F, 2F, B1, etc.) |
| direction | string | — | Beam direction (X or Y) |
| line_grid | float | mm | Perpendicular gridline coordinate |
| member_id | string | — | Base member ID (e.g., B1, G1) |
| span_index | int | — | Span number along the beam line |
| start_grid | string | — | Grid label at beam start (or OFF_GRID) |
| end_grid | string | — | Grid label at beam end (or OFF_GRID) |
| bar_position | string | — | TOP, BOT, or STIRRUP |
| bar_role | string | — | Detailed role (MAIN_SINGLE, MAIN_START, MAIN_INTERMEDIATE, MAIN_END, ADD_TOP, ADD_BOT, STIRRUP_END, STIRRUP_MID) |
| bar_type | string | — | MAIN or STIRRUP |
| dia_mm | float | mm | Bar diameter |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| layer | float | — | Bar layer number (1, 2 for multi-layer) |
| spacing_mm | float | mm | Stirrup spacing (null for main bars) |
| zone_length_mm | float | mm | Length of the reinforcement zone |
| quantity_pieces | float | — | Number of stirrup sets in zone |
| total_length_mm | float | mm | Total material length |
| anchorage_start | string | — | Start anchorage type (HOOK, LAP, NONE) |
| anchorage_end | string | — | End anchorage type (HOOK, LAP, NONE) |
| lap_length_mm | float | mm | Lap splice length used |
| development_length_mm | float | mm | Development length (Ldh) used |
| splice_start_mm | float | mm | Splice zone start Z coordinate |
| splice_start_end_mm | float | mm | Splice zone start end coordinate |
| splice_end_mm | float | mm | Splice zone end Z coordinate |
| splice_end_end_mm | float | mm | Splice zone end end coordinate |
| transition_type | string | — | Bar transition type at span boundary |
| reinforcement_type | string | — | UNIFORM or VARYING |
| split_piece | float | — | Stock split piece number |
| original_length_mm | float | mm | Original length before stock split |
| x_start_mm | float | mm | Bar start X coordinate |
| y_start_mm | float | mm | Bar start Y coordinate |
| z_start_mm | float | mm | Bar start Z coordinate |
| x_end_mm | float | mm | Bar end X coordinate |
| y_end_mm | float | mm | Bar end Y coordinate |
| z_end_mm | float | mm | Bar end Z coordinate |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section height |
| shape | string | — | Section shape (RECT) |

---

## 2. RebarLengthsColumn.csv (31 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Column member ID (e.g., TC1, C1) |
| start_grid | string | — | Grid location (e.g., X1Y3) |
| level_from | string | — | Bottom level of the continuous stack |
| level_to | string | — | Top level of the continuous stack |
| bar_position | string | — | MAIN or HOOP |
| bar_role | string | — | MAIN_TOP, MAIN_BOTTOM, MAIN_INTERMEDIATE, MAIN_SINGLE, HOOP_END, HOOP_MID |
| bar_type | string | — | MAIN or HOOP |
| dia_mm | float | mm | Bar diameter |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| spacing_mm | float | mm | Hoop spacing (null for main bars) |
| zone_length_mm | float | mm | Length of the hoop zone |
| quantity_pieces | float | — | Number of hoop sets in zone |
| total_length_mm | float | mm | Total material length |
| splice_start_mm | float | mm | Splice zone start Z coordinate |
| splice_start_end_mm | float | mm | Splice zone end coordinate |
| splice_end_mm | float | mm | Splice zone end Z coordinate |
| splice_end_end_mm | float | mm | Splice zone end end coordinate |
| x_start_mm | float | mm | Bar start X coordinate |
| y_start_mm | float | mm | Bar start Y coordinate |
| z_start_mm | float | mm | Bar start Z coordinate |
| x_end_mm | float | mm | Bar end X coordinate |
| y_end_mm | float | mm | Bar end Y coordinate |
| z_end_mm | float | mm | Bar end Z coordinate |
| segment_id | string | — | Unique segment ID |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section depth |
| shape | string | — | Section shape (RECT) |
| split_piece | — | — | Null (columns don't exceed 12m) |
| split_total | — | — | Null |
| original_length_mm | — | — | Null |

---

## 3. RebarLengthsSlab.csv (40 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Slab member ID (e.g., B3S1, 1FS3) |
| level | string | — | Story level |
| slab_type | string | — | Slab type code |
| thickness_mm | float | mm | Slab thickness |
| direction | string | — | Bar direction (X or Y) |
| layer | string | — | Top or Bot |
| bar_role | string | — | MAIN_SINGLE, MAIN_START, MAIN_INTERMEDIATE, MAIN_END, or ANCHOR variants |
| start_type | string | — | Start anchorage (hook or lap) |
| end_type | string | — | End anchorage (hook or lap) |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| l_cl_mm | float | mm | Clear span between beam faces |
| Wg1_mm | float | mm | Beam width at start edge |
| Wg2_mm | float | mm | Beam width at end edge |
| Ldh_mm | float | mm | Development length (hook) |
| Llap_mm | float | mm | Lap splice length used |
| Lx_mm | float | mm | Panel X dimension |
| Ly_mm | float | mm | Panel Y dimension |
| short_direction | string | — | Short span direction (X or Y) |
| panel_role | string | — | SINGLE, START, INTERMEDIATE, END |
| mismatch_before | bool | — | Thickness mismatch with panel before |
| mismatch_after | bool | — | Thickness mismatch with panel after |
| adj_thickness_before_mm | float | mm | Adjacent panel thickness (before) |
| adj_thickness_after_mm | float | mm | Adjacent panel thickness (after) |
| centroid_x_mm | float | mm | Panel centroid X |
| centroid_y_mm | float | mm | Panel centroid Y |
| z_mm | float | mm | Panel elevation |
| mesh_origin_x_mm | float | mm | Mesh line start X |
| mesh_origin_y_mm | float | mm | Mesh line start Y |
| mesh_origin_z_mm | float | mm | Mesh line start Z (bar position within thickness) |
| mesh_terminus_x_mm | float | mm | Mesh line end X |
| mesh_terminus_y_mm | float | mm | Mesh line end Y |
| mesh_terminus_z_mm | float | mm | Mesh line end Z |
| mesh_distribution_axis | string | — | Distribution axis (X or Y) |
| split_piece | float | — | Stock split piece number |
| split_total | float | — | Total split pieces |
| original_length_mm | float | mm | Original length before split |
| total_length_mm | int | mm | Total material length |

---

## 4. RebarLengthsStair.csv (29 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| segment_id | string | — | Unique segment ID |
| member_id | string | — | Stair member ID (e.g., B3SS1) |
| story_group | string | — | Story range (e.g., B4~B3) |
| zone | string | — | LOWER_LANDING, MID_LANDING, FLIGHT1, FLIGHT2 |
| sub_zone | string | — | Specific bar location (TOP_ALONG_A, BOT_ALONG_A, DIST_SPAN_B, etc.) |
| direction | string | — | LONGITUDINAL or TRANSVERSE |
| layer | string | — | TOP, BOTTOM, or BOTH |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total material length |
| cover_mm | float | mm | Concrete cover (30mm for stairs) |
| Ldh_mm | int | mm | Development length (hook) |
| lap_top_mm | int | mm | Top lap splice length |
| lap_bot_mm | int | mm | Bottom lap splice length |
| start_x | float | mm | Bar start X coordinate |
| start_y | float | mm | Bar start Y coordinate |
| start_z | float | mm | Bar start Z coordinate |
| end_x | float | mm | Bar end X coordinate |
| end_y | float | mm | Bar end Y coordinate |
| end_z | float | mm | Bar end Z coordinate |
| width_dir_x | float | — | Distribution direction unit vector X |
| width_dir_y | float | — | Distribution direction unit vector Y |
| width_dir_z | float | — | Distribution direction unit vector Z |
| width_span_mm | float | mm | Distribution span (width minus covers) |
| split_piece | — | — | Null (stairs don't exceed 12m) |
| split_total | — | — | Null |
| original_length_mm | — | — | Null |

---

## 5. RebarLengthsWall.csv (29 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_id | int | — | Numeric wall ID (FK to MembersWall) |
| wall_mark | string | — | Wall mark name (CW1, W201, etc.) |
| level | string | — | Story level (or FOOTING for dowels) |
| direction | string | — | VERTICAL or HORIZONTAL |
| bar_role | string | — | MAIN_BOTTOM, MAIN_INTERMEDIATE, MAIN_TOP, MAIN_SINGLE, MAIN_SINGLE_WITH_DOWEL, DOWEL, U_BAR |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars (includes face multiplier) |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total material length |
| height_mm | float | mm | Wall story height |
| width_mm | float | mm | Wall plan width |
| thickness_mm | float | mm | Wall thickness |
| bar_layer | string | — | Double or Single |
| splice_start_mm | float | mm | Splice zone start Z |
| splice_start_end_mm | float | mm | Splice zone start end |
| splice_end_mm | float | mm | Splice zone end Z |
| splice_end_end_mm | float | mm | Splice zone end end |
| cover_mm | float | mm | Concrete cover (50mm) |
| mesh_origin_x_mm | float | mm | Mesh line start X |
| mesh_origin_y_mm | float | mm | Mesh line start Y |
| mesh_origin_z_mm | float | mm | Mesh line start Z |
| mesh_terminus_x_mm | float | mm | Mesh line end X |
| mesh_terminus_y_mm | float | mm | Mesh line end Y |
| mesh_terminus_z_mm | float | mm | Mesh line end Z |
| mesh_distribution_axis | string | — | ALONG_WALL_LENGTH or ALONG_WALL_HEIGHT |
| split_piece | float | — | Stock split piece number |
| split_total | float | — | Total split pieces |
| original_length_mm | float | mm | Original length before split |

---

## 6. RebarLengthsFooting.csv (26 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Footing member ID (MF1, MF2) |
| zone | string | — | Zone name (MF1, R1, R2, V1, etc.) |
| zone_type | string | — | BASE, ADDITIONAL, or STIRRUP |
| direction | string | — | X, Y, or VERTICAL (stirrup) |
| layer | string | — | Top, Bot, or null (stirrup) |
| bar_role | string | — | BASE_X_TOP, BASE_Y_BOT, ADDITIONAL_X_TOP, STIRRUP, etc. |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total material length |
| Ldh_mm | float | mm | Development length (hook) |
| Llap_mm | float | mm | Lap splice length |
| cover_mm | float | mm | Concrete cover (75mm) |
| bar_span_mm | float | mm | Bar span before anchorage |
| dist_width_mm | float | mm | Distribution width |
| mesh_distribution_axis | string | — | X, Y, or XY_GRID (stirrup) |
| mesh_origin_x_mm | float | mm | Mesh line start X |
| mesh_origin_y_mm | float | mm | Mesh line start Y |
| mesh_origin_z_mm | float | mm | Mesh line start Z |
| mesh_terminus_x_mm | float | mm | Mesh line end X |
| mesh_terminus_y_mm | float | mm | Mesh line end Y |
| mesh_terminus_z_mm | float | mm | Mesh line end Z |
| split_piece | float | — | Stock split piece number |
| split_total | float | — | Total split pieces |
| original_length_mm | float | mm | Original length before split |

---

## 7. RebarLengthsBasementWall.csv (29 columns)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_mark | string | — | Basement wall name (BW1, DW1, FW1) |
| level | string | — | Story level (B1, B2, B4~B1) |
| direction | string | — | VERTICAL or HORIZONTAL |
| face | string | — | INTERIOR or EXTERIOR |
| zone | string | — | TOP, MIDDLE, BOTTOM (vertical) or LEFT, MIDDLE, RIGHT (horizontal) |
| bar_role | string | — | BOTTOM_V_TOP, INTERMEDIATE_V_MIDDLE, FULL_HEIGHT_V_BOTTOM, H_LEFT, H_MIDDLE, H_RIGHT, etc. |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total material length |
| height_mm | float | mm | Wall story height |
| length_wall_mm | float | mm | Wall plan length |
| thickness_mm | float | mm | Wall thickness |
| zone_height_mm | float | mm | Vertical zone height (TOP/MID/BOT) |
| Ldh_mm | float | mm | Development length (hook) |
| Lpc_mm | float | mm | Lap splice length (vertical continuity) |
| cover_mm | float | mm | Concrete cover (50mm) |
| mesh_origin_x_mm | float | mm | Mesh line start X |
| mesh_origin_y_mm | float | mm | Mesh line start Y |
| mesh_origin_z_mm | float | mm | Mesh line start Z |
| mesh_terminus_x_mm | float | mm | Mesh line end X |
| mesh_terminus_y_mm | float | mm | Mesh line end Y |
| mesh_terminus_z_mm | float | mm | Mesh line end Z |
| mesh_distribution_axis | string | — | ALONG_WALL_LENGTH or ALONG_WALL_HEIGHT |
| split_piece | float | — | Stock split piece number |
| split_total | float | — | Total split pieces |
| original_length_mm | float | mm | Original length before split |
| zone_width_mm | float | mm | Horizontal zone width (LEFT/MID/RIGHT) |

---

## bar_role Reference

### Beam bar_role Values

| bar_role | Description |
|----------|-------------|
| MAIN_SINGLE | Single-span beam main bar (hook at both ends) |
| MAIN_START | First span of multi-span (hook at start, lap at end) |
| MAIN_INTERMEDIATE | Middle span (lap at both ends) |
| MAIN_END | Last span (lap at start, hook at end) |
| ADD_TOP | Additional top bar at support |
| ADD_BOT | Additional bottom bar at midspan |
| STIRRUP_END | Stirrup in end zone (dense spacing) |
| STIRRUP_MID | Stirrup in middle zone |

### Column bar_role Values

| bar_role | Description |
|----------|-------------|
| MAIN_TOP | Top of continuous stack (hook at top) |
| MAIN_BOTTOM | Bottom of stack (lap at top) |
| MAIN_INTERMEDIATE | Middle of stack (lap at top) |
| MAIN_SINGLE | Single-level column |
| HOOP_END | Hoop in end zone |
| HOOP_MID | Hoop in middle zone |

### Slab bar_role Values

| bar_role | Description |
|----------|-------------|
| MAIN_SINGLE | Isolated panel (hook at both ends) |
| MAIN_START | First panel in row (hook at start, lap at end) |
| MAIN_INTERMEDIATE | Middle panel (lap at both ends) |
| MAIN_END | Last panel (lap at start, hook at end) |
| MAIN_*_ANCHOR | Thickness mismatch variant (hook instead of lap at mismatch edge) |

### Wall bar_role Values

| bar_role | Description |
|----------|-------------|
| MAIN_BOTTOM | Bottom of continuous stack (lap at top for continuity) |
| MAIN_INTERMEDIATE | Middle of stack (lap at top) |
| MAIN_TOP | Top of stack (hook at top — wall terminates) |
| MAIN_SINGLE | Single-level wall (hook at top) |
| MAIN_SINGLE_WITH_DOWEL | Single level at foundation (dowel below, hook at top) |
| DOWEL | Foundation dowel bar |
| U_BAR | Horizontal bar with U-turn at free edge |

### Footing bar_role Values

| bar_role | Description |
|----------|-------------|
| BASE_X_TOP | Base reinforcement, X-direction, top layer |
| BASE_X_BOT | Base reinforcement, X-direction, bottom layer |
| BASE_Y_TOP | Base reinforcement, Y-direction, top layer |
| BASE_Y_BOT | Base reinforcement, Y-direction, bottom layer |
| ADDITIONAL_X_TOP | Additional zone, X-direction, top |
| ADDITIONAL_Y_BOT | Additional zone, Y-direction, bottom |
| STIRRUP | Vertical tie at column location |

### Basement Wall bar_role Values

| bar_role | Description |
|----------|-------------|
| BOTTOM_V_TOP | Bottom level, vertical, top zone |
| BOTTOM_V_MIDDLE | Bottom level, vertical, middle zone |
| BOTTOM_V_BOTTOM | Bottom level, vertical, bottom zone |
| INTERMEDIATE_V_* | Intermediate level, vertical, per zone |
| TOP_V_* | Top level, vertical, per zone |
| SINGLE_V_* | Single level, vertical, per zone |
| FULL_HEIGHT_V_* | Full-height wall (B4~B1), vertical, per zone |
| H_LEFT | Horizontal bar, left zone |
| H_MIDDLE | Horizontal bar, middle zone |
| H_RIGHT | Horizontal bar, right zone |

---

## Tier 1 Input Dependencies

| RebarLengths File | Tier 1 Inputs Required |
|---|---|
| RebarLengthsBeam | MembersBeam, MembersColumn, Sections, ReinforcementBeam, Nodes |
| RebarLengthsColumn | MembersColumn, ReinforcementColumn, Sections, Nodes |
| RebarLengthsSlab | MembersSlab, ReinforcementSlab, MembersBeam, Nodes |
| RebarLengthsStair | MembersStair, ReinforcementStair |
| RebarLengthsWall | MembersWall, ReinforcementWall, Nodes |
| RebarLengthsFooting | MembersFooting, ReinforcementFooting |
| RebarLengthsBasementWall | MembersBasementWall, ReinforcementBasementWall, Nodes |

All calculators also require: `config/development_lengths.csv`, `config/lap_splice.csv`
Optional: `config/cover_requirements.csv`
