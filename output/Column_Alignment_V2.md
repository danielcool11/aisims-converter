# AISIMS Column Alignment — V1 Dictionary vs Converter Output
# V1 사전 vs 변환기 출력 컬럼 정리

Date: 2026-03-24

Legend:
- KEEP = column exists in output, keep as-is
- ADD = not in output, add to converter
- DROP = in V1 dictionary, remove (not useful)
- NEW = not in V1 dictionary, add to dictionary (new from converter)

---

## 1. Nodes.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | node_id | node_id | KEEP | |
| 2 | node_number | — | NEW | MIDAS element traceability |
| 3 | x_mm | x_mm | KEEP | |
| 4 | y_mm | y_mm | KEEP | |
| 5 | z_mm | z_mm | KEEP | |
| 6 | level | level | KEEP | |
| 7 | grid | grid | KEEP | |
| 8 | grid_offset_x_mm | — | NEW | Distance from nearest X grid line |
| 9 | grid_offset_y_mm | — | NEW | Distance from nearest Y grid line |

**V2 total: 9 columns**

---

## 2. Materials.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | material_id | material_id | KEEP | |
| 2 | type | type | KEEP | |
| 3 | grade | grade | KEEP | |
| 4 | fck_MPa | fck_MPa | KEEP | |
| 5 | fy_MPa | fy_MPa | KEEP | |
| 6 | E_MPa | E_MPa | KEEP | |
| 7 | density_kN_m3 | density_kN_m3 | KEEP | |

**V2 total: 7 columns (unchanged)**

---

## 3. Sections.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | section_id | section_id | KEEP | |
| 2 | member_type | member_type | KEEP | |
| 3 | member_id | member_id | KEEP | |
| 4 | level_from | level_from | KEEP | |
| 5 | level_to | level_to | KEEP | |
| 6 | shape | shape | KEEP | |
| 7 | b_mm | b_mm | KEEP | |
| 8 | h_mm | h_mm | KEEP | |
| 9 | diameter_mm | diameter_mm | KEEP | |
| 10 | thickness_mm | thickness_mm | KEEP | |
| 11 | area_m2 | area_m2 | KEEP | |
| 12 | inertia_y_m4 | inertia_y_m4 | KEEP | |
| 13 | inertia_z_m4 | inertia_z_m4 | KEEP | |
| 14 | effective_depth_mm | effective_depth_mm | KEEP | |
| 15 | cover_mm | cover_mm | KEEP | |
| — | material_type | material_type | DROP | Always "concrete", redundant |
| — | type | type | DROP | Ambiguous with member_type |
| — | source | source | DROP | Always "MIDAS_Gen", no value |
| — | remarks | remarks | DROP | Always null |

**V2 total: 15 columns (drop 4 from V1)**

---

## 4. MembersBeam.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | element_id | — | NEW | MIDAS element traceability |
| 2 | member_id | member_id | KEEP | |
| 3 | member_type | type | RENAME in dict | "BEAM" constant |
| 4 | section_id | section_id | KEEP | |
| 5 | node_from | start_node | RENAME in dict | Clearer directional naming |
| 6 | node_to | end_node | RENAME in dict | |
| 7 | level | level_start | RENAME in dict | Beams sit at one level |
| 8 | grid_from | start_grid | RENAME in dict | |
| 9 | grid_to | end_grid | RENAME in dict | |
| 10 | x_from_mm | — | NEW | Coordinate-first: start point |
| 11 | y_from_mm | — | NEW | |
| 12 | x_to_mm | — | NEW | Coordinate-first: end point |
| 13 | y_to_mm | — | NEW | |
| 14 | z_mm | — | NEW | Beam elevation |
| 15 | length_mm | length_mm | KEEP | |
| 16 | b_mm | — | NEW | Quick reference (also in Sections) |
| 17 | h_mm | — | NEW | Quick reference |
| 18 | story_group | story_group | ADD | Derive: level value |
| 19 | material_id | material_id | ADD | From section lookup |
| 20 | segment_no | segment_no | ADD | Sequential per member_id |
| 21 | segment_id | segment_id | ADD | "{member_id}-SEG{no:03d}" |
| — | level_end | level_end | DROP | Same as level for horizontal beams |

**V2 total: 21 columns**

---

## 5. MembersColumn.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | element_id | — | NEW | MIDAS element traceability |
| 2 | member_id | member_id | KEEP | |
| 3 | member_type | type | RENAME in dict | "COLUMN" constant |
| 4 | section_id | section_id | KEEP | |
| 5 | node_from | start_node | RENAME in dict | Bottom node |
| 6 | node_to | end_node | RENAME in dict | Top node |
| 7 | level_from | level_start | RENAME in dict | |
| 8 | level_to | level_end | RENAME in dict | |
| 9 | grid | start_grid | RENAME in dict | Column = single grid point |
| 10 | x_mm | — | NEW | Coordinate-first |
| 11 | y_mm | — | NEW | |
| 12 | height_mm | length_mm | RENAME in dict | "height" is clearer for columns |
| 13 | b_mm | — | NEW | Quick reference |
| 14 | h_mm | — | NEW | Quick reference |
| 15 | story_group | story_group | ADD | "{level_from}~{level_to}" |
| 16 | material_id | material_id | ADD | From section lookup |
| 17 | segment_no | segment_no | ADD | Sequential per member_id |
| 18 | segment_id | segment_id | ADD | "{member_id}-SEG{no:03d}" |
| — | end_grid | end_grid | DROP | Same as start_grid for columns |

**V2 total: 18 columns**

---

## 6. MembersWall.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | element_id | — | NEW | MIDAS element traceability |
| 2 | wall_mark | member_id | RENAME in dict | "wall_mark" is domain-specific |
| 3 | wall_id | — | NEW | MIDAS wall group ID |
| 4 | member_type | type | RENAME in dict | "WALL" constant |
| 5 | level | level_start | RENAME in dict | Wall panel level |
| 6 | node_i | start_node | RENAME in dict | Quad: 4 corner nodes |
| 7 | node_j | — | NEW | |
| 8 | node_k | — | NEW | |
| 9 | node_l | — | NEW | |
| 10 | centroid_x_mm | — | NEW | Coordinate-first |
| 11 | centroid_y_mm | — | NEW | |
| 12 | centroid_z_mm | — | NEW | |
| 13 | thickness_mm | thickness_mm | KEEP | |
| 14 | height_mm | — | NEW | Wall panel height |
| 15 | width_mm | length_mm | RENAME in dict | Wall plan length |
| 16 | story_group | story_group | ADD | Derive from level |
| 17 | section_id | section_id | ADD | "W_{wall_mark}_{thickness}" |
| 18 | material_id | material_id | ADD | From materials |
| 19 | segment_no | segment_no | ADD | Per wall_mark |
| 20 | segment_id | segment_id | ADD | "{wall_mark}-SEG{no:03d}" |
| — | end_node | end_node | DROP | Walls have 4 nodes, not 2 |
| — | level_end | level_end | DROP | Wall panels are single-level |
| — | start_grid, end_grid | — | DROP | Walls span between grids, use nodes |

**V2 total: 20 columns**

---

## 7. MembersSlab.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | member_id | member_id | KEEP | |
| 2 | member_type | type | RENAME in dict | "SLAB" constant |
| 3 | level | level | KEEP | |
| 4 | slab_type | slab_type | KEEP | C (continuous) or B (cantilever) |
| 5 | thickness_mm | thickness_mm | KEEP | |
| 6 | centroid_x_mm | centroid_x_mm | KEEP | |
| 7 | centroid_y_mm | centroid_y_mm | KEEP | |
| 8 | z_mm | z_mm | KEEP | |
| 9 | Lx_mm | Lx_mm | KEEP | |
| 10 | Ly_mm | Ly_mm | KEEP | |
| 11 | boundary_nodes | corner_nodes | RENAME in dict | "boundary" is more accurate (not always 4 corners) |
| 12 | node_count | — | NEW | Number of boundary nodes |
| 13 | short_direction | short_direction | ADD | Compute: "X" if Lx < Ly else "Y" |
| 14 | area_mm2 | area_mm2 | ADD | Compute: Lx * Ly (bounding box) |
| 15 | section_id | section_id | ADD | "SL_{member_id}_{thickness}" |
| 16 | material_id | material_id | ADD | From materials |
| 17 | segment_no | segment_no | ADD | Sequential |
| 18 | segment_id | segment_id | ADD | "{member_id}-SEG{no:03d}" |
| — | boundary_grids | boundary_grids | DROP | Grids derivable from node IDs |

**V2 total: 18 columns**

---

## 8. MembersStair.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| | **Base columns** | | | |
| 1 | member_id | member_id | KEEP | |
| 2 | member_type | type | ADD | "STAIR" constant |
| 3 | level_from | level_start | RENAME in dict | |
| 4 | level_to | level_end | RENAME in dict | |
| 5 | centroid_x_mm | — | NEW | From boundary lookup |
| 6 | centroid_y_mm | — | NEW | |
| 7 | z_mm | — | NEW | |
| 8 | boundary_nodes | — | NEW | From slab boundary |
| 9 | Lx_mm | — | NEW | Boundary bounding box |
| 10 | Ly_mm | — | NEW | |
| 11 | story_group | story_group | ADD | "{level_from}~{level_to}" |
| 12 | material_id | material_id | ADD | From materials |
| 13 | segment_no | segment_no | ADD | Sequential |
| 14 | segment_id | segment_id | ADD | |
| | **Stair geometry** | | | |
| 15 | stair_type | stair_type | ADD | "U_SHAPED" |
| 16 | flight_count | flight_count | ADD | 2 |
| 17 | landing_count | landing_count | ADD | 1 |
| 18 | total_height_mm | total_height_mm | RENAME | From stair_height_mm |
| 19 | stair_width_mm | stair_width_mm | KEEP | |
| 20 | flight_run_mm | — | RENAME | From stair_length_mm |
| 21 | gap_mm | gap_mm | ADD | Compute from boundary |
| 22 | landing_lower_mm | landing_length_lower_mm | RENAME | From landing_right_mm |
| 23 | landing_mid_mm | landing_length_mid_mm | RENAME | From landing_left_mm |
| 24 | waist_thickness_mm | waist_thickness_mm | ADD | null (pending inquiry) |
| 25 | riser_height_mm | riser_height_mm | ADD | null (pending inquiry) |
| 26 | tread_depth_mm | tread_depth_mm | ADD | null (pending inquiry) |
| 27 | num_risers | num_risers | ADD | null (pending inquiry) |
| 28 | risers_per_flight | risers_per_flight | ADD | null (pending inquiry) |
| | **8-point model** | | | |
| 29-52 | p1_x..p8_z | p1_x..p8_z | ADD | Compute from boundary + levels |
| | **Flight geometry** | | | |
| 53-55 | flight1_start_x/y/z | flight1_start_x/y/z | ADD | Compute from 8-point |
| 56-58 | flight1_end_x/y/z | flight1_end_x/y/z | ADD | |
| 59 | flight1_num_risers | flight1_num_risers | ADD | null (pending) |
| 60-62 | flight2_start_x/y/z | flight2_start_x/y/z | ADD | |
| 63-65 | flight2_end_x/y/z | flight2_end_x/y/z | ADD | |
| 66 | flight2_num_risers | flight2_num_risers | ADD | null (pending) |
| | **Mid-landing geometry** | | | |
| 67-69 | landing1_start_x/y/z | landing1_start_x/y/z | ADD | |
| 70 | landing1_length_mm | landing1_length_mm | ADD | = landing_mid_mm |
| 71 | landing1_width_mm | landing1_width_mm | ADD | = 2*width + gap |

**V2 total: 71 columns (50 computable now, 7 null pending inquiry, 14 from 8-point model)**

---

## 9. ReinforcementBeam.csv

V1 dictionary expects **normalized** rows (one bar type per row).
Our output is **flat** (one row = all bar specs for a position).

**V2 decision: Reshape to normalized format.**

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | member_id | member_id | KEEP | |
| 2 | level | — | NEW | From section lookup |
| 3 | position | zone | RENAME in dict | I/M/J |
| 4 | bar_position | bar_position | ADD | TOP / BOT / STIRRUP |
| 5 | bar_type | bar_type | ADD | LONG / STIRRUP |
| 6 | bar_spec | — | NEW | Original spec string "12-4-D22" |
| 7 | dia_mm | dia_mm | RESHAPE | From top_dia/bot_dia/stirrup_dia |
| 8 | n_bars | n_bars | RESHAPE | From top_total/bot_total/stirrup_legs |
| 9 | spacing_mm | spacing_mm | RESHAPE | null for LONG, value for STIRRUP |
| 10 | fck_MPa | — | NEW | Design context |
| 11 | fy_MPa | — | NEW | |
| 12 | ratio | — | NEW | Design utilization ratio |

**V2 total: 12 columns (normalized, ~3 rows per position)**

---

## 10. ReinforcementColumn.csv

Same normalization as beam.

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | member_id | member_id | KEEP | |
| 2 | level_from | level_from | ADD | From section name |
| 3 | level_to | level_to | ADD | |
| 4 | bar_group | group | RENAME in dict | MAIN / TIE_END / TIE_MID |
| 5 | bar_type | bar_type | ADD | LONG / TIE |
| 6 | bar_spec | — | NEW | Original spec string |
| 7 | dia_mm | dia_mm | RESHAPE | |
| 8 | n_bars | n_bars | RESHAPE | |
| 9 | spacing_mm | spacing_mm | RESHAPE | null for LONG |
| 10 | fck_MPa | — | NEW | |
| 11 | fy_MPa | — | NEW | |
| 12 | b_mm | — | NEW | Section dimensions |
| 13 | h_mm | — | NEW | |
| 14 | height_mm | — | NEW | Column height |

**V2 total: 14 columns (normalized, 3 rows per column)**

---

## 11. ReinforcementSlab.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | member_id | member_id | KEEP | |
| 2 | level | — | RENAME | Was "level" already |
| 3 | direction | direction | KEEP | X / Y |
| 4 | layer | location | RENAME in dict | Top / Bot — "layer" is clearer |
| 5 | bar_spec | — | NEW | "D10@200" |
| 6 | bar_dia_mm | dia_mm | RENAME in dict | |
| 7 | bar_spacing_mm | spacing_mm | RENAME in dict | |
| 8 | thickness_mm | thickness_mm | KEEP | |
| — | slab_type | slab_type | ADD | From MembersSlab |
| — | design_position | design_position | DROP | Same as direction+layer |

**V2 total: 9 columns**

---

## 12. ReinforcementWall.csv

V1 dictionary expected normalized rows (one direction per row) with many
metadata columns. V2 keeps V and H in the same row — one row = one wall
element with full reinforcement. Design results (fck, fy, ratios) and
geometry (lw, htw, thickness) moved to DesignResultsWall and MembersWall.

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | wall_mark | member_id | RENAME in dict | Wall identifier |
| 2 | level | level_from | RENAME in dict | Story level |
| 3 | v_bar_spec | (was split rows) | NEW | "D13 @150" |
| 4 | v_dia_mm | dia_mm | KEEP (V row) | Vertical bar diameter |
| 5 | v_spacing_mm | spacing_mm | KEEP (V row) | Vertical bar spacing |
| 6 | h_bar_spec | (was split rows) | NEW | "D10 @200" |
| 7 | h_dia_mm | dia_mm | KEEP (H row) | Horizontal bar diameter |
| 8 | h_spacing_mm | spacing_mm | KEEP (H row) | Horizontal bar spacing |
| 9 | bar_layer | layer | RENAME in dict | Single / Double |
| 10 | end_rebar | — | NEW | Boundary zone rebar (nullable) |
| — | wall_id | — | DROP | Traceability via wall_mark + level |
| — | fck_MPa | — | DROP | → DesignResultsWall |
| — | fy_MPa, fys_MPa | — | DROP | → DesignResultsWall |
| — | lw_mm, htw_mm | — | DROP | → MembersWall (geometry) |
| — | thickness_mm | — | DROP | → MembersWall (geometry) |
| — | ratio_axial/moment/shear | — | DROP | → DesignResultsWall |
| — | rebar_id | rebar_id | DROP | Auto-generated, no value |
| — | section_id | section_id | DROP | Derivable |
| — | member_type | member_type | DROP | Always "WALL" |
| — | zone_id | zone_id | DROP | Not applicable |
| — | face | face | DROP | Covered by bar_layer |
| — | direction_marker | direction_marker | DROP | Redundant |
| — | bar_role | bar_role | DROP | Derivable (V=MAIN, H=DIST) |
| — | remarks, source | — | DROP | Empty / constant |

**V2 total: 10 columns (one row per wall element, V+H paired)**

---

## 13. ReinforcementStair.csv

| # | V2 Column | V1 Dictionary | Action | Source |
|---|-----------|---------------|--------|--------|
| 1 | member_id | member_id | KEEP | |
| 2 | level_from | — | KEEP | |
| 3 | level_to | — | KEEP | |
| 4 | zone | — | KEEP | landing_left / landing_right / stair |
| 5 | direction | direction | KEEP | transverse / longitudinal |
| 6 | layer | — | KEEP | Top / Bot |
| 7 | bar_spec | bar_size | RENAME in dict | "D13@150" |
| 8 | bar_dia_mm | dia_mm | RENAME in dict | |
| 9 | bar_spacing_mm | spacing_mm | RENAME in dict | |
| — | rebar_id | rebar_id | DROP | No source data |
| — | section_id | section_id | DROP | Derivable |
| — | member_type | member_type | DROP | Always "STAIR" |
| — | distribution_mode | distribution_mode | DROP | Covered by zone+direction |
| — | n_bars | n_bars | DROP | Computed in Tier 2 |
| — | bar_role | bar_role | DROP | Covered by zone+direction+layer |
| — | source | source | DROP | Always same |

**V2 total: 9 columns (unchanged from current)**

---

## Summary of Changes

| File | V1 Cols | V2 Cols | Action |
|------|---------|---------|--------|
| Nodes.csv | 6 | 9 | +3 new (node_number, offsets) |
| Materials.csv | 7 | 7 | No change |
| Sections.csv | 19 | 15 | -4 dropped (source, remarks, etc.) |
| MembersBeam.csv | 14 | 21 | Rename 5, add 7 (coords, segment) |
| MembersColumn.csv | 14 | 18 | Rename 5, add 5 |
| MembersWall.csv | 15 | 20 | Major restructure (quad nodes, centroid) |
| MembersSlab.csv | 18 | 18 | Rename 1, add 5, drop 1 |
| MembersStair.csv | 70 | 71 | Major expansion (8-point model) |
| ReinforcementBeam.csv | 7 | 12 | Reshape flat → normalized |
| ReinforcementColumn.csv | 8 | 14 | Reshape flat → normalized |
| ReinforcementSlab.csv | 8 | 9 | Minor rename |
| ReinforcementWall.csv | 17 | 15 | Reshape, drop redundant |
| ReinforcementStair.csv | 13 | 9 | Drop redundant |

### Naming Convention Decisions

| V1 Dictionary | V2 Decision | Reason |
|---|---|---|
| start_node / end_node | node_from / node_to | Directional, consistent |
| level_start / level_end | level_from / level_to | Consistent with node naming |
| start_grid / end_grid | grid_from / grid_to | Consistent |
| type | member_type | Avoids ambiguity with Python `type()` |
| location (slab) | layer | "Top/Bot" is a layer, not a location |
| corner_nodes (slab) | boundary_nodes | Polygons can have >4 nodes |
| length_mm (column) | height_mm | Columns are vertical, "height" is clearer |
