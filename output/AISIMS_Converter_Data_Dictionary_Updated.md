# AISIMS Converter Data Dictionary

Generated: 2026-04-04

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| File names | PascalCase | MembersBeam.csv, ReinforcementColumn.csv |
| Column headers | snake_case | member_id, x_from_mm, bar_spacing_mm |
| Unit suffixes | Standard SI notation | _mm, _m2, _m4, _MPa, _kN_m3 |
| Structural symbols | Engineering convention | Lx_mm, Mu_neg, phiMn_pos, Vu, Pu |

## File Overview

### Tier 1 — Core Files (18 files, every project)

| # | File | Source | Description |
|---|------|--------|-------------|
| 1 | Nodes.csv | Part A | All nodes with level, grid, coordinates |
| 2 | Materials.csv | Part A | Concrete + rebar material properties |
| 3 | Sections.csv | Part A | Parsed section definitions with dimensions |
| 4 | MembersBeam.csv | Part A | Beam elements with coordinates and junction extensions |
| 5 | MembersColumn.csv | Part A | Column elements with 3D length (slanted support) |
| 6 | MembersWall.csv | Part A | Wall quad panel elements with junction polygons |
| 7 | MembersSlab.csv | Part B | Slab boundary polygons |
| 8 | MembersStair.csv | Part B | U-shaped stair 8-point geometry |
| 9 | MembersFooting.csv | Part B | Mat foundation quads/polygons |
| 10 | ReinforcementBeam.csv | Part A | Beam rebar per position (I/M/J) |
| 11 | ReinforcementColumn.csv | Part A | Column main bars + ties |
| 12 | ReinforcementWall.csv | Part A | Wall V + H bars (paired per element) |
| 13 | ReinforcementSlab.csv | Part B | Slab rebar per direction/layer |
| 14 | ReinforcementStair.csv | Part B | Stair rebar per zone/direction/layer |
| 15 | ReinforcementFooting.csv | Part B | Footing base + additional + stirrup zones |
| 16 | DesignResultsBeam.csv | Part A | Beam design capacity and ratios |
| 17 | DesignResultsColumn.csv | Part A | Column design capacity and ratios |
| 18 | ValidationReport.txt | System | Cross-check results |

### Tier 1 — Conditional Files (3 files, project-dependent)

| # | File | Source | Condition | Description |
|---|------|--------|-----------|-------------|
| 19 | DesignResultsWall.csv | Part A | Shear wall system | Wall design ratios + geometry |
| 20 | MembersBasementWall.csv | Part C | Project has basement | Basement/retaining wall panels with zone dimensions |
| 21 | ReinforcementBasementWall.csv | Part C | Project has basement | Basement wall rebar per direction/face/zone |

### Tier 2 — Rebar Length Files (7 files, computed from Tier 1)

| # | File | Input | Description |
|---|------|-------|-------------|
| 22 | RebarLengthsBeam.csv | Members + Reinf + Sections | Bar-by-bar beam rebar with anchorage and splices |
| 23 | RebarLengthsColumn.csv | Members + Reinf + Sections | Column main bars, dowels, hoops with splice zones |
| 24 | RebarLengthsWall.csv | Members + Reinf + Nodes | Normal wall V/H bars with continuity stacking |
| 25 | RebarLengthsBasementWall.csv | Members + Reinf + Nodes | Basement wall H/V bars, U-bars, dowels |
| 26 | RebarLengthsSlab.csv | Members + Reinf + Beams | Slab bars with polygon scan-line clipping |
| 27 | RebarLengthsFooting.csv | Members + Reinf | Footing base, additional, and stirrup bars |
| 28 | RebarLengthsStair.csv | Members + Reinf | Stair bars with bend points for flights |

---

## Column Definitions — Tier 1

### 1. Nodes.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| node_id | string | — | Unique node identifier (N_{level}_{grid}) |
| node_number | int | — | Original MIDAS node number |
| x_mm | float | mm | X coordinate |
| y_mm | float | mm | Y coordinate |
| z_mm | float | mm | Z coordinate |
| level | string | — | Story level (1F, B1, Roof, PIT, etc.) |
| grid | string | — | Grid label (X1Y1) or OFF_GRID |
| grid_offset_x_mm | float | mm | Distance from nearest X grid line |
| grid_offset_y_mm | float | mm | Distance from nearest Y grid line |
| source | string | — | MIDAS or BOUNDARY |

### 2. Materials.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| material_id | string | — | Material identifier (C35, SD400) |
| type | string | — | concrete or rebar |
| grade | string | — | Grade name |
| fck_MPa | float | MPa | Concrete compressive strength (null for rebar) |
| fy_MPa | float | MPa | Rebar yield strength (null for concrete) |
| E_MPa | float | MPa | Elastic modulus |
| density_kN_m3 | float | kN/m3 | Unit weight |

### 3. Sections.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| section_id | string | — | Unique section ID (RC_{member}_{level}) |
| member_type | string | — | BEAM, COLUMN, or WALL |
| member_id | string | — | Base member identifier (G1, TC1, BT1) |
| level_from | string | — | Start level (nullable) |
| level_to | string | — | End level (nullable) |
| shape | string | — | RECT or CIRCLE |
| b_mm | float | mm | Width |
| h_mm | float | mm | Height/depth |
| diameter_mm | float | mm | Diameter for circular sections |
| thickness_mm | float | mm | Wall thickness |
| area_m2 | float | m2 | Cross-sectional area |
| inertia_y_m4 | float | m4 | Moment of inertia (Y-axis) |
| inertia_z_m4 | float | m4 | Moment of inertia (Z-axis) |
| effective_depth_mm | float | mm | d = h - cover - dia/2 |
| cover_mm | float | mm | Concrete cover (KDS) |

### 4. MembersBeam.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| element_id | int | — | MIDAS element number |
| member_id | string | — | Base member ID (G1, B11, LB1) |
| section_id | string | — | Section reference |
| design_key | string | — | Raw MIDAS section name (FK to reinforcement) |
| node_from | string | — | Start node ID |
| node_to | string | — | End node ID |
| level | string | — | Story level |
| grid_from | string | — | Start grid label |
| grid_to | string | — | End grid label |
| x_from_mm | float | mm | Start X coordinate |
| y_from_mm | float | mm | Start Y coordinate |
| x_to_mm | float | mm | End X coordinate |
| y_to_mm | float | mm | End Y coordinate |
| z_mm | float | mm | Beam elevation (Z) |
| length_mm | float | mm | Span length |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section depth |
| extend_start_mm | float | mm | Junction extension at start (half adjacent column/beam width) |
| extend_end_mm | float | mm | Junction extension at end |

### 5. MembersColumn.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| element_id | int | — | MIDAS element number |
| member_id | string | — | Base member ID (C1, TC1) |
| section_id | string | — | Section reference |
| design_key | string | — | Raw MIDAS section name (FK to reinforcement) |
| node_from | string | — | Bottom node ID |
| node_to | string | — | Top node ID |
| level_from | string | — | Bottom level |
| level_to | string | — | Top level |
| grid | string | — | Grid location |
| x_mm | float | mm | Bottom X coordinate |
| y_mm | float | mm | Bottom Y coordinate |
| x_top_mm | float | mm | Top X coordinate (differs for slanted) |
| y_top_mm | float | mm | Top Y coordinate (differs for slanted) |
| height_mm | float | mm | Vertical height (Z component) |
| length_mm | float | mm | Actual 3D length (>height for slanted columns) |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section depth |

### 6. MembersWall.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| element_id | int | — | MIDAS element number |
| wall_mark | string | — | Wall mark name (CW1, W201) |
| wall_id | int | — | MIDAS wall group ID |
| level | string | — | Story level |
| centroid_x_mm | float | mm | Panel centroid X |
| centroid_y_mm | float | mm | Panel centroid Y |
| centroid_z_mm | float | mm | Panel centroid Z |
| thickness_mm | float | mm | Wall thickness |
| height_mm | float | mm | Panel height |
| width_mm | float | mm | Panel width (plan length) |
| node_i | string | — | Corner node 1 |
| node_j | string | — | Corner node 2 |
| node_k | string | — | Corner node 3 |
| node_l | string | — | Corner node 4 |
| wall_status | string | — | DESIGNED / NO_DESIGN (see wall deduplication below) |
| poly_0x_mm..poly_3y_mm | float | mm | Junction-modified polygon vertices (4 corners x 2 coords) |
| extend_start_mm | float | mm | Junction extension at start (half adjacent wall thickness) |
| extend_end_mm | float | mm | Junction extension at end |

**Wall deduplication:** Elements from MembersWall that spatially overlap with Part C
basement wall panels are removed (they are handled by MembersBasementWall instead).
Remaining elements without DesignWall reinforcement are flagged as `NO_DESIGN`.

### 7. MembersSlab.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Slab identifier (B3S1, 6S2) |
| level | string | — | Story level |
| slab_type | string | — | C (continuous) or B (cantilever) |
| thickness_mm | float | mm | Slab thickness |
| centroid_x_mm | float | mm | Centroid X |
| centroid_y_mm | float | mm | Centroid Y |
| z_mm | float | mm | Slab elevation |
| Lx_mm | float | mm | X-direction span (bounding box) |
| Ly_mm | float | mm | Y-direction span (bounding box) |
| boundary_nodes | string | — | Polygon nodes (semicolon-separated) |
| node_count | int | — | Number of boundary nodes |

### 8. MembersStair.csv (78 columns)

| Column Group | Columns | Description |
|-------------|---------|-------------|
| Base (15) | member_id, member_type, level_from, level_to, centroid_x/y, z_mm, Lx/Ly_mm, boundary_nodes, length_mm, story_group, material_id, segment_no, segment_id | Member identification and location |
| Configuration (10) | stair_type, flight_count, landing_count, total_height_mm, stair_width_mm, flight_run_mm, flight_slope_mm, gap_mm, landing_lower_mm, landing_mid_mm | Stair geometry parameters |
| Detailing (5) | waist_thickness_mm, riser_height_mm, tread_depth_mm, num_risers, risers_per_flight | Step dimensions (from design office) |
| 8-point model (24) | p1_x..p8_z | Lower landing (P1-P4) + mid-landing (P5-P8) coordinates |
| Flight geometry (14) | flight1/2_start/end_x/y/z, flight1/2_num_risers | Flight start/end points and riser counts |
| Mid-landing (5) | landing1_start_x/y/z, landing1_length_mm, landing1_width_mm | Mid-landing position and dimensions |

### 9. MembersFooting.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Foundation ID (MF1, MF2) |
| part_id | string | — | Part identifier (MF1-1, MF1-2) |
| member_type | string | — | FOOTING |
| footing_type | string | — | MAT |
| shape | string | — | RECT or POLYGON_N |
| level | string | — | Foundation level |
| thickness_mm | float | mm | Foundation thickness |
| centroid_x_mm | float | mm | Centroid X |
| centroid_y_mm | float | mm | Centroid Y |
| z_mm | float | mm | Footing top surface elevation (not center) |
| Lx_mm | float | mm | X-direction span |
| Ly_mm | float | mm | Y-direction span |
| area_mm2 | float | mm2 | Plan area |
| x_min_mm | float | mm | Bounding box X min |
| y_min_mm | float | mm | Bounding box Y min |
| x_max_mm | float | mm | Bounding box X max |
| y_max_mm | float | mm | Bounding box Y max |
| boundary_nodes | string | — | Nodes (semicolon-separated) |
| material_id | string | — | Material reference |
| segment_no | int | — | Part sequence number |
| segment_id | string | — | Segment identifier |

### 10. ReinforcementBeam.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| element_id | int | — | MIDAS element (nullable, I position only) |
| member_id | string | — | Raw MIDAS section name (level-prefixed) |
| section_id | string | — | Section number |
| position | string | — | I (start), M (midspan), J (end) |
| fck_MPa | float | MPa | Concrete strength |
| fy_MPa | float | MPa | Main bar yield strength |
| fys_MPa | float | MPa | Stirrup yield strength |
| bar_role | string | — | Bar role identifier |
| top_bar_spec | string | — | Top bar specification (e.g., 6-4-D22) |
| top_total | int | — | Total top bars (main + additional) |
| top_main | int | — | Main (continuous) top bars |
| top_additional | int | — | Additional top bars |
| top_dia_mm | int | mm | Top bar diameter |
| bot_bar_spec | string | — | Bottom bar specification |
| bot_total | int | — | Total bottom bars |
| bot_main | int | — | Main bottom bars |
| bot_additional | int | — | Additional bottom bars |
| bot_dia_mm | int | mm | Bottom bar diameter |
| stirrup_spec | string | — | Stirrup specification (e.g., 3-D10 @150) |
| stirrup_legs | int | — | Number of stirrup legs |
| stirrup_dia_mm | int | mm | Stirrup diameter |
| stirrup_spacing_mm | int | mm | Stirrup spacing |

### 11. ReinforcementColumn.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| element_id | int | — | MIDAS element |
| member_id | string | — | Raw MIDAS section name (level-prefixed) |
| section_id | string | — | Section number |
| fck_MPa | float | MPa | Concrete strength |
| fy_MPa | float | MPa | Main bar yield strength |
| fys_MPa | float | MPa | Tie yield strength |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section depth |
| height_mm | float | mm | Column height |
| main_bar_spec | string | — | Main bar spec (e.g., 16-5-D29) |
| main_total | int | — | Total main bars |
| main_count | int | — | Continuous main bars |
| main_additional | int | — | Additional main bars |
| main_dia_mm | int | mm | Main bar diameter |
| tie_end_spec | string | — | Tie at column ends (e.g., 3-D10 @150) |
| tie_end_legs | int | — | Tie legs at ends |
| tie_end_dia_mm | int | mm | Tie diameter at ends |
| tie_end_spacing_mm | int | mm | Tie spacing at ends |
| tie_mid_spec | string | — | Tie at mid-height |
| tie_mid_legs | int | — | Tie legs at mid |
| tie_mid_dia_mm | int | mm | Tie diameter at mid |
| tie_mid_spacing_mm | int | mm | Tie spacing at mid |

### 12. ReinforcementWall.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_id | int | — | MIDAS wall group ID |
| wall_mark | string | — | Wall mark name |
| level | string | — | Story level |
| v_bar_spec | string | — | Vertical bar spec (e.g., D13 @150) |
| v_dia_mm | int | mm | Vertical bar diameter |
| v_spacing_mm | int | mm | Vertical bar spacing |
| h_bar_spec | string | — | Horizontal bar spec |
| h_dia_mm | int | mm | Horizontal bar diameter |
| h_spacing_mm | int | mm | Horizontal bar spacing |
| bar_layer | string | — | Single or Double |
| end_rebar | string | — | Boundary zone rebar (nullable) |

### 13. ReinforcementSlab.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Slab identifier |
| level | string | — | Story level |
| direction | string | — | X or Y |
| layer | string | — | Top or Bot |
| bar_spec | string | — | Bar specification (e.g., D10@200) |
| bar_dia_mm | int | mm | Bar diameter |
| bar_spacing_mm | int | mm | Bar spacing |
| thickness_mm | float | mm | Slab thickness |

### 14. ReinforcementStair.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Stair identifier |
| level_from | string | — | Start level |
| level_to | string | — | End level |
| zone | string | — | landing_left, landing_right, stair |
| direction | string | — | transverse or longitudinal |
| layer | string | — | Top or Bot |
| bar_spec | string | — | Bar specification |
| bar_dia_mm | int | mm | Bar diameter |
| bar_spacing_mm | int | mm | Bar spacing |

### 15. ReinforcementFooting.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Foundation ID (MF1, MF2) |
| zone | string | — | Zone ID (MF1=base, R1=additional, V1=stirrup) |
| zone_type | string | — | BASE, ADDITIONAL, or STIRRUP |
| direction | string | — | X or Y (null for stirrups) |
| layer | string | — | Top or Bot (null for stirrups) |
| bar_spec | string | — | Bar specification |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_legs | int | — | Stirrup legs (null for bars) |
| zone_x_min | float | mm | Zone bounding box X min |
| zone_x_max | float | mm | Zone bounding box X max |
| zone_y_min | float | mm | Zone bounding box Y min |
| zone_y_max | float | mm | Zone bounding box Y max |
| zone_boundary | string | — | Zone node coordinates |

### 16. DesignResultsBeam.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Raw MIDAS section name |
| position | string | — | I, M, or J |
| fck_MPa | float | MPa | Concrete strength |
| fy_MPa | float | MPa | Main bar yield strength |
| fys_MPa | float | MPa | Stirrup yield strength |
| top_bar_spec | string | — | Top bar specification |
| bot_bar_spec | string | — | Bottom bar specification |
| stirrup_spec | string | — | Stirrup specification |
| Mu_neg | float | N-mm | Negative factored moment |
| phiMn_neg | float | N-mm | Negative moment capacity |
| ratio_negative | float | — | Negative moment D/C ratio |
| Mu_pos | float | N-mm | Positive factored moment |
| phiMn_pos | float | N-mm | Positive moment capacity |
| ratio_positive | float | — | Positive moment D/C ratio |
| Vu | float | N | Factored shear |
| phiVc | float | N | Concrete shear capacity |
| ratio_shear | float | — | Shear D/C ratio |

### 17. DesignResultsColumn.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Raw MIDAS section name |
| fck_MPa | float | MPa | Concrete strength |
| fy_MPa | float | MPa | Main bar yield strength |
| fys_MPa | float | MPa | Tie yield strength |
| b_mm | float | mm | Section width |
| h_mm | float | mm | Section depth |
| height_mm | float | mm | Column height |
| main_bar_spec | string | — | Main bar specification |
| phiPn_max | float | N | Maximum axial capacity |
| Pu | float | N | Factored axial load |
| ratio_axial | float | — | Axial D/C ratio |
| ratio_moment_y | float | — | Moment Y D/C ratio |
| ratio_moment_z | float | — | Moment Z D/C ratio |
| Vu_end | float | N | End shear force |
| ratio_shear_end | float | — | End shear D/C ratio |
| Vu_mid | float | N | Mid shear force |
| ratio_shear_mid | float | — | Mid shear D/C ratio |

### 19. DesignResultsWall.csv (Conditional)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_id | int | — | MIDAS wall group ID |
| wall_mark | string | — | Wall mark name |
| level | string | — | Story level |
| fck_MPa | float | MPa | Concrete strength |
| fy_MPa | float | MPa | Vertical bar yield strength |
| fys_MPa | float | MPa | Horizontal bar yield strength |
| lw_mm | float | mm | Wall length |
| htw_mm | float | mm | Wall total height |
| thickness_mm | float | mm | Wall thickness |
| ratio_axial | float | — | Axial D/C ratio |
| ratio_moment | float | — | Moment D/C ratio |
| ratio_shear | float | — | Shear D/C ratio |

### 20. MembersBasementWall.csv (Conditional)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_mark | string | — | Wall name (BW1, RW1, DW1) |
| level | string | — | Story level (B1, B4~B1) |
| panel_no | int | — | Panel sequence (for multi-panel walls) |
| wall_type | string | — | Wall type code (A, B, etc.) |
| thickness_mm | float | mm | Wall thickness |
| length_mm | float | mm | Wall plan length |
| height_mm | float | mm | Wall height |
| zone_width_left_mm | float | mm | Left zone width |
| zone_width_middle_mm | float | mm | Middle zone width |
| zone_width_right_mm | float | mm | Right zone width |
| zone_height_top_mm | float | mm | Top zone height |
| zone_height_middle_mm | float | mm | Middle zone height |
| zone_height_bottom_mm | float | mm | Bottom zone height |
| node_i | string | — | Corner node 1 (converted ID, or MISSING_xxx) |
| node_j | string | — | Corner node 2 |
| node_k | string | — | Corner node 3 |
| node_l | string | — | Corner node 4 |
| centroid_x_mm | float | mm | Panel centroid X |
| centroid_y_mm | float | mm | Panel centroid Y |
| z_mm | float | mm | Panel elevation (computed via height stacking) |
| node_status | string | — | OK / PARTIAL / INFERRED / MISSING |
| start_x_mm | float | mm | Actual horizontal span start X (from ELEMENT sheet, P2 only) |
| start_y_mm | float | mm | Actual horizontal span start Y |
| end_x_mm | float | mm | Actual horizontal span end X |
| end_y_mm | float | mm | Actual horizontal span end Y |
| extend_start_mm | float | mm | Junction extension at start |
| extend_end_mm | float | mm | Junction extension at end |
| poly_0x_mm..poly_3y_mm | float | mm | Junction-modified polygon vertices (4 corners x 2 coords) |

Note: `start_x/y_mm` and `end_x/y_mm` are populated from the Part C ELEMENT sheet for projects with vertical panel nodes (P2). They provide the actual horizontal endpoints when node_i/node_j have identical XY coordinates.

### 21. ReinforcementBasementWall.csv (Conditional)

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_mark | string | — | Wall name |
| level | string | — | Story level |
| wall_type | string | — | Wall type code |
| thickness_mm | float | mm | Wall thickness |
| direction | string | — | HORIZONTAL or VERTICAL |
| face | string | — | INTERIOR or EXTERIOR |
| zone | string | — | LEFT/MIDDLE/RIGHT (H) or TOP/MIDDLE/BOTTOM/FULL (V) |
| bar_spec | string | — | Bar specification (e.g., D13@200) |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |

Note: Composite bars (e.g., D13+D16@100) are split into 2 rows with
doubled spacing per individual bar type (D13@200 + D16@200).

---

## Column Definitions — Tier 2 (Rebar Lengths)

### 22. RebarLengthsBeam.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| segment_id | string | — | Beam segment ID (G1-SEG001) |
| level | string | — | Story level |
| direction | string | — | X or Y |
| line_grid | string | — | Grid line identifier |
| member_id | string | — | Base member ID |
| span_index | int | — | Span position in continuous group |
| start_grid | string | — | Start grid label |
| end_grid | string | — | End grid label |
| bar_position | string | — | TOP or BOT |
| bar_role | string | — | MAIN_SINGLE, MAIN_START, MAIN_END, MAIN_INTERMEDIATE, ADD_START, ADD_END, ADD_INTERMEDIATE, ADD_MIDSPAN |
| bar_type | string | — | MAIN or STIRRUP |
| dia_mm | float | mm | Bar diameter |
| n_bars | int | — | Number of bars |
| length_mm | float | mm | Individual bar length |
| layer | string | — | Bar layer |
| spacing_mm | float | mm | Stirrup spacing (null for main bars) |
| zone_length_mm | float | mm | Stirrup zone length |
| quantity_pieces | int | — | Number of stirrup pieces in zone |
| total_length_mm | float | mm | Total bar length (length x n_bars or quantity) |
| anchorage_start | string | — | HOOK, LAP, or STRAIGHT |
| anchorage_end | string | — | HOOK, LAP, or STRAIGHT |
| lap_length_mm | float | mm | Lap splice length (Lpt or Lpb) |
| development_length_mm | float | mm | Hook development length (Ldh) |
| splice_start_mm | float | mm | Splice zone start Z coordinate |
| splice_start_end_mm | float | mm | Splice zone start end Z |
| splice_end_mm | float | mm | Splice zone end Z coordinate |
| splice_end_end_mm | float | mm | Splice zone end end Z |
| transition_type | string | — | Diameter transition type |
| reinforcement_type | string | — | Reinforcement classification |
| split_piece | int | — | Piece index for stock-split bars (null if no split) |
| original_length_mm | float | mm | Original bar length before stock split |
| x_start_mm | float | mm | Bar start X (structural coords) |
| y_start_mm | float | mm | Bar start Y |
| z_start_mm | float | mm | Bar start Z |
| x_end_mm | float | mm | Bar end X |
| y_end_mm | float | mm | Bar end Y |
| z_end_mm | float | mm | Bar end Z |
| b_mm | float | mm | Beam section width |
| h_mm | float | mm | Beam section depth |
| shape | string | — | Section shape (RECT) |

### 23. RebarLengthsColumn.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Column member ID (C1, TC1) |
| start_grid | string | — | Grid location |
| level_from | string | — | Bottom level |
| level_to | string | — | Top level |
| bar_position | string | — | MAIN or HOOP |
| bar_role | string | — | DOWEL, MAIN_BOTTOM, MAIN_INTERMEDIATE, MAIN_TOP, MAIN_FULL, HOOP_END_BOTTOM, HOOP_END_TOP, HOOP_MID |
| bar_type | string | — | MAIN or HOOP |
| dia_mm | float | mm | Bar diameter |
| n_bars | int | — | Number of bars |
| length_mm | float | mm | Individual bar length |
| spacing_mm | float | mm | Hoop spacing (null for main bars) |
| zone_length_mm | float | mm | Hoop zone length |
| quantity_pieces | int | — | Number of hoops in zone |
| total_length_mm | float | mm | Total bar length |
| splice_start_mm | float | mm | Splice zone start Z |
| splice_start_end_mm | float | mm | Splice zone start end Z |
| splice_end_mm | float | mm | Splice zone end Z |
| splice_end_end_mm | float | mm | Splice zone end end Z |
| x_start_mm | float | mm | Bar start X |
| y_start_mm | float | mm | Bar start Y |
| z_start_mm | float | mm | Bar start Z |
| x_end_mm | float | mm | Bar end X |
| y_end_mm | float | mm | Bar end Y |
| z_end_mm | float | mm | Bar end Z |
| bend1_x/y/z_mm | float | mm | Bend point 1 location (slanted columns, P1 only) |
| bend1_end_x/y/z_mm | float | mm | Bend point 1 end (direction change end) |
| bend2_x/y/z_mm | float | mm | Bend point 2 location |
| bend2_end_x/y/z_mm | float | mm | Bend point 2 end |
| segment_id | string | — | Column segment ID (C1-SEG001) |
| b_mm | float | mm | Column section width |
| h_mm | float | mm | Column section depth |
| shape | string | — | Section shape (RECT) |
| split_piece | int | — | Piece index for stock-split bars |
| split_total | int | — | Total pieces after stock split |
| original_length_mm | float | mm | Original length before split |

Note: Bend point columns (bend1_*, bend2_*) are present for projects with slanted columns (P1). Absent in projects without slanted columns (P2).

### 24. RebarLengthsWall.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_id | int | — | MIDAS wall group ID |
| wall_mark | string | — | Wall mark name |
| level | string | — | Story level |
| direction | string | — | VERTICAL or HORIZONTAL |
| bar_role | string | — | DOWEL, MAIN_BOTTOM, MAIN_INTERMEDIATE, MAIN_TOP, MAIN_SINGLE, MAIN_SINGLE_WITH_DOWEL, H_BAR, U_BAR |
| dia_mm | float | mm | Bar diameter |
| spacing_mm | float | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | float | mm | Individual bar length |
| total_length_mm | float | mm | Total bar length |
| height_mm | float | mm | Wall panel height |
| width_mm | float | mm | Wall panel width |
| thickness_mm | float | mm | Wall thickness |
| bar_layer | string | — | Single or Double |
| splice_start_mm | float | mm | Splice zone start Z |
| splice_start_end_mm | float | mm | Splice zone start end Z |
| splice_end_mm | float | mm | Splice zone end Z |
| splice_end_end_mm | float | mm | Splice zone end end Z |
| cover_mm | float | mm | Concrete cover |
| wall_dir_x_mm | float | mm | Wall plan direction X component (for V-bar distribution) |
| wall_dir_y_mm | float | mm | Wall plan direction Y component |
| mesh_origin_x/y/z_mm | float | mm | Bar group start position (structural coords) |
| mesh_terminus_x/y/z_mm | float | mm | Bar group end position |
| mesh_distribution_axis | string | — | ALONG_WALL_LENGTH or ALONG_WALL_HEIGHT |
| split_piece | int | — | Piece index for stock-split bars |
| split_total | int | — | Total pieces after split |
| original_length_mm | float | mm | Original length before split |

### 25. RebarLengthsBasementWall.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| wall_mark | string | — | Wall name (BW1, RW1) |
| level | string | — | Story level |
| direction | string | — | HORIZONTAL or VERTICAL |
| face | string | — | INTERIOR or EXTERIOR |
| zone | string | — | LEFT/MIDDLE/RIGHT/FULL (H) or TOP/MIDDLE/BOTTOM/DOWEL (V) |
| bar_role | string | — | H_LEFT, H_MIDDLE, H_RIGHT, H_FULL, V_TOP, V_MIDDLE, V_BOTTOM, U_BAR, DOWEL |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total bar length |
| height_mm | float | mm | Wall panel height |
| length_wall_mm | float | mm | Actual panel plan length (from quad nodes) |
| thickness_mm | float | mm | Wall thickness |
| zone_height_mm | float | mm | Vertical zone height (V bars only) |
| Ldh_mm | float | mm | Hook development length |
| Lpc_mm | float | mm | Lap splice length (V bars only) |
| cover_mm | float | mm | Concrete cover |
| mesh_origin_x/y/z_mm | float | mm | Bar group start position |
| mesh_terminus_x/y/z_mm | float | mm | Bar group end position |
| mesh_distribution_axis | string | — | ALONG_WALL_LENGTH or ALONG_WALL_HEIGHT |
| wall_dir_x_mm | float | mm | Wall plan direction X (for distribution) |
| wall_dir_y_mm | float | mm | Wall plan direction Y |
| split_piece | float | — | Piece index for stock-split bars |
| split_total | float | — | Total pieces after split |
| original_length_mm | float | mm | Original length before split |
| zone_width_mm | float | mm | Horizontal zone width |

### 26. RebarLengthsSlab.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Slab identifier |
| level | string | — | Story level |
| slab_type | string | — | C or B |
| thickness_mm | float | mm | Slab thickness |
| direction | string | — | X or Y |
| layer | string | — | Top or Bot |
| bar_role | string | — | SINGLE, START, END, INTERMEDIATE |
| start_type | string | — | hook, lap, or straight |
| end_type | string | — | hook, lap, or straight |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars in this group |
| length_mm | int | mm | Individual bar length |
| l_cl_mm | float | mm | Clear span |
| Wg1_mm | float | mm | Beam width at start |
| Wg2_mm | float | mm | Beam width at end |
| Ldh_mm | float | mm | Hook development length |
| Llap_mm | float | mm | Lap splice length |
| Lx_mm | float | mm | Slab X span |
| Ly_mm | float | mm | Slab Y span |
| short_direction | string | — | X or Y (short span direction) |
| panel_role | string | — | SINGLE, START, END, INTERMEDIATE |
| mismatch_before | string | — | Thickness mismatch at start (nullable) |
| mismatch_after | string | — | Thickness mismatch at end (nullable) |
| adj_thickness_before_mm | float | mm | Adjacent slab thickness before |
| adj_thickness_after_mm | float | mm | Adjacent slab thickness after |
| centroid_x/y_mm | float | mm | Slab centroid |
| z_mm | float | mm | Slab elevation |
| mesh_origin_x/y/z_mm | float | mm | Bar start position |
| mesh_terminus_x/y/z_mm | float | mm | Bar end position |
| mesh_distribution_axis | string | — | ALONG_X or ALONG_Y |
| split_piece | int | — | Piece index for stock-split bars |
| split_total | int | — | Total pieces |
| original_length_mm | float | mm | Original length before split |
| total_length_mm | int | mm | Total bar length |

Note: For polygon slabs (non-rectangular, 3+ nodes), bars are scan-line clipped to the slab boundary. Each group of adjacent bars with the same length is output as one record.

### 27. RebarLengthsFooting.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| member_id | string | — | Foundation ID |
| zone | string | — | Zone ID |
| zone_type | string | — | BASE, ADDITIONAL, or STIRRUP |
| direction | string | — | X or Y |
| layer | string | — | Top or Bot |
| bar_role | string | — | BASE_X_TOP, BASE_Y_BOT, ADD_X_TOP, STIRRUP, etc. |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total bar length |
| Ldh_mm | float | mm | Hook development length |
| Llap_mm | float | mm | Lap splice length |
| cover_mm | float | mm | Concrete cover |
| bar_span_mm | float | mm | Clear bar span |
| dist_width_mm | float | mm | Distribution width |
| mesh_distribution_axis | string | — | ALONG_X or ALONG_Y |
| mesh_origin_x/y/z_mm | float | mm | Bar start position |
| mesh_terminus_x/y/z_mm | float | mm | Bar end position |
| split_piece | int | — | Piece index for stock-split bars |
| split_total | int | — | Total pieces |
| original_length_mm | float | mm | Original length before split |

Note: Footing z_mm is the **top surface** elevation, not center. Top bars at z - cover, bottom bars at z - thickness + cover.

### 28. RebarLengthsStair.csv

| Column | Type | Unit | Description |
|--------|------|------|-------------|
| segment_id | string | — | Stair segment ID |
| member_id | string | — | Stair member ID |
| story_group | string | — | Story range (e.g., B1~1F) |
| zone | string | — | landing_lower, flight1, landing_mid, flight2 |
| sub_zone | string | — | FULL, LEFT, RIGHT, etc. |
| direction | string | — | longitudinal or transverse |
| layer | string | — | Top or Bot |
| dia_mm | int | mm | Bar diameter |
| spacing_mm | int | mm | Bar spacing |
| n_bars | int | — | Number of bars |
| length_mm | int | mm | Individual bar length |
| total_length_mm | int | mm | Total bar length |
| cover_mm | float | mm | Concrete cover |
| Ldh_mm | float | mm | Hook development length |
| lap_top_mm | float | mm | Top bar lap splice length |
| lap_bot_mm | float | mm | Bottom bar lap splice length |
| start_x/y/z | float | mm | Bar start position |
| end_x/y/z | float | mm | Bar end position |
| width_dir_x/y/z | float | — | Bar distribution direction (unit vector) |
| width_span_mm | float | mm | Distribution span width |
| bend1_x/y/z | float | mm | Bend point 1 (flights only) |
| bend2_x/y/z | float | mm | Bend point 2 (flights only) |
| split_piece | int | — | Piece index for stock-split bars |
| split_total | int | — | Total pieces |
| original_length_mm | float | mm | Original length before split |

---

## FK Relationships

| From | Column | To | Column | Relationship |
|------|--------|----|--------|-------------|
| MembersBeam | design_key | ReinforcementBeam | member_id | Many-to-many (3 positions per beam) |
| MembersColumn | design_key | ReinforcementColumn | member_id | Many-to-one |
| MembersWall | wall_id | ReinforcementWall | wall_id | Many-to-many (numeric join) |
| MembersWall | wall_id | DesignResultsWall | wall_id | Many-to-many |
| MembersSlab | member_id | ReinforcementSlab | member_id | One-to-many |
| MembersStair | member_id | ReinforcementStair | member_id | One-to-many |
| MembersFooting | member_id | ReinforcementFooting | member_id | One-to-many |
| MembersBasementWall | wall_mark + level | ReinforcementBasementWall | wall_mark + level | One-to-many |
| All Members | node_* | Nodes | node_id / node_number | Many-to-one |
| All Members | section_id | Sections | section_id | Many-to-one |
| All Members | material_id | Materials | material_id | Many-to-one |

## Notes

- All coordinates in mm (MIDAS Gen global coordinate system)
- Level names follow StoryDefinition (1F, B1, Roof, PIT, etc.)
- Semicolons (;) used as separator in boundary_nodes to prevent Excel interpretation
- Composite bars (D16+D13@200) split into 2 rows with doubled spacing
- Pipe format stirrups (4|5-D13@150) take max leg count
- Stock split: bars exceeding 12m are split into equal pieces with lap splices at joints
- Polygon slabs: non-rectangular slabs (3+ nodes) use scan-line clipping for rebar
- Footing z_mm represents the top surface, not the center of the foundation
- Bend columns (bend1_*, bend2_*) present only for slanted column transitions
- wall_dir_x/y_mm provides explicit wall plan direction for reliable V-bar distribution in the renderer
