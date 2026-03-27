# V1 vs V2 Data Dictionary Comparison
# V1-V2 데이터 사전 비교

V1: AISIMS Data Dictionary (pdf2bim parser, 24 files)
V2: AISIMS Converter (MIDAS Gen converter, 21 files)

---

## Summary of Changes

| Aspect | V1 | V2 | Reason |
|--------|----|----|--------|
| Data source | PDF parsing + manual | MIDAS Gen export (CSV/MGT) | Reliable, no OCR errors |
| File count | 24 | 21 (18 core + 3 conditional) | Merged some, removed unused |
| Reinforcement format | Normalized (1 bar per row) | Flat (all bars per member in 1 row) | Matches MIDAS output structure |
| Design results | Mixed into reinforcement | Separate files | Clean separation of concerns |
| Coordinates | Grid-based positioning | Coordinate-first (grid as metadata) | Works without grid labels |
| Naming | Mixed conventions | PascalCase files, snake_case headers | Consistent |

---

## File-Level Changes

### Files in V1 but NOT in V2

| V1 File | Reason removed |
|---------|---------------|
| Connections.csv | Tier 2 computed data, not a Tier 1 output |
| RebarLengthsBeam.csv | Tier 2 (built separately in tier2/ module) |
| RebarLengthsColumn.csv | Tier 2 |
| RebarLengthsSlab.csv | Tier 2 (not yet built) |
| RebarLengthsFooting.csv | Tier 2 (not yet built) |
| RebarLengthsStair.csv | Tier 2 (not yet built) |
| RebarLengthsWall.csv | Tier 2 (not yet built) |

### Files in V2 but NOT in V1

| V2 File | Reason added |
|---------|-------------|
| DesignResultsBeam.csv | Split from ReinforcementBeam (design ratios separated) |
| DesignResultsColumn.csv | Split from ReinforcementColumn |
| DesignResultsWall.csv | Split from ReinforcementWall |
| MembersBasementWall.csv | New: basement/retaining walls (Part C data) |
| ReinforcementBasementWall.csv | New: basement wall rebar with zone/face detail |

---

## Column-Level Comparison

### Nodes.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Same | node_id, x_mm, y_mm, z_mm, level, grid | Same | |
| Added | — | node_number | MIDAS element traceability |
| Added | — | grid_offset_x_mm, grid_offset_y_mm | Distance from nearest grid line |
| Added | — | source | MIDAS or BOUNDARY (merged footing nodes) |

### Materials.csv

No changes. Identical columns in V1 and V2.

### Sections.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Same | section_id, member_type, member_id, level_from, level_to, shape, b_mm, h_mm, diameter_mm, thickness_mm, area_m2, inertia_y_m4, inertia_z_m4, effective_depth_mm, cover_mm | Same | |
| Dropped | material_type | — | Always "concrete", redundant |
| Dropped | type | — | Ambiguous with member_type |
| Dropped | source | — | Always same source |
| Dropped | remarks | — | Always null |

### MembersBeam.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Renamed | start_node | node_from | Directional naming |
| Renamed | end_node | node_to | |
| Renamed | level_start | level | Beams sit at one level |
| Renamed | start_grid | grid_from | |
| Renamed | end_grid | grid_to | |
| Renamed | type | — | Implicitly BEAM (file name) |
| Added | — | element_id | MIDAS element traceability |
| Added | — | design_key | Raw MIDAS section name (FK to reinforcement) |
| Added | — | x_from_mm, y_from_mm, x_to_mm, y_to_mm, z_mm | Coordinate-first architecture |
| Added | — | b_mm, h_mm | Quick reference (also in Sections) |
| Dropped | level_end | — | Same as level for horizontal beams |
| Dropped | story_group | — | Derivable from level |
| Dropped | material_id | — | Derivable from section lookup |
| Dropped | segment_no, segment_id | — | Not needed for Tier 1 |

### MembersColumn.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Renamed | start_node | node_from | Bottom node |
| Renamed | end_node | node_to | Top node |
| Renamed | level_start | level_from | |
| Renamed | level_end | level_to | |
| Renamed | start_grid | grid | Single grid point |
| Added | — | element_id | MIDAS traceability |
| Added | — | design_key | FK to reinforcement |
| Added | — | x_mm, y_mm, x_top_mm, y_top_mm | Coordinates (top differs for slanted) |
| Added | — | length_mm | 3D length (>height for slanted columns) |
| Added | — | b_mm, h_mm | Quick reference |
| Dropped | end_grid | — | Same as start_grid for columns |
| Dropped | story_group, material_id, segment_no, segment_id | — | Derivable or not needed |

### MembersWall.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | 2-node line model | 4-node quad panel | Matches MIDAS element structure |
| Added | — | element_id | MIDAS traceability |
| Added | — | wall_mark, wall_id | Wall identification (mark = name, id = numeric) |
| Added | — | centroid_x/y/z_mm | Coordinate-first |
| Added | — | height_mm, width_mm | Panel dimensions |
| Added | — | node_i, node_j, node_k, node_l | 4 corner nodes (quad) |
| Dropped | start_node, end_node | — | Replaced by 4 corner nodes |
| Dropped | start_grid, end_grid | — | Walls span between grids |
| Dropped | story_group, material_id, segment_no, segment_id | — | Not needed |

### MembersSlab.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Renamed | corner_nodes | boundary_nodes | Polygons can have >4 nodes |
| Added | — | node_count | Number of boundary nodes |
| Dropped | type | — | Implicitly SLAB |
| Dropped | boundary_grids | — | Derivable from node IDs |
| Dropped | short_direction | — | Derivable from Lx vs Ly |
| Dropped | area_mm2 | — | Derivable from Lx * Ly |
| Dropped | section_id, material_id, segment_no, segment_id | — | Not needed |

### MembersStair.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Renamed | level_start | level_from | Consistent naming |
| Renamed | level_end | level_to | |
| Added | — | centroid_x/y_mm, z_mm, Lx/Ly_mm | Location from slab boundary |
| Added | — | boundary_nodes | Stairwell boundary polygon |
| Added | — | flight_run_mm, flight_slope_mm | Explicit flight dimensions |
| Added | — | story_group, material_id, segment_no, segment_id | Kept from V1 spec |
| Core structure | Same 8-point model (p1-p8, flights, landing) | Same | Compatible |

### MembersFooting.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Isolated footing (single pad) | Mat foundation (quad/polygon parts) | V1 was isolated, V2 handles mat |
| Added | — | part_id, shape (RECT/POLYGON_N) | Multi-part support |
| Added | — | x_min/y_min/x_max/y_max_mm | Bounding box for BIM rendering |
| Added | — | area_mm2, boundary_nodes | Polygon area and nodes |
| Dropped | footing_id, grid, node_id, depth_mm | — | Different structure |

### ReinforcementBeam.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Normalized (1 row per bar type per zone) | Flat (1 row per position with all specs) | Matches MIDAS 3-row block output |
| V1 cols | member_id, zone, bar_position, bar_type, dia_mm, n_bars, spacing_mm | — | 7 cols, many rows |
| V2 cols | member_id, position, top_bar_spec, top_total/main/additional/dia, bot_bar_spec, bot_total/main/additional/dia, stirrup_spec/legs/dia/spacing | — | 22 cols, fewer rows |
| Added | — | element_id, section_id, fck/fy/fys_MPa | Traceability and material context |
| Added | — | bar_role | Bar role identifier |
| Dropped | — | ratio_negative, ratio_positive, ratio_shear | Moved to DesignResultsBeam |

### ReinforcementColumn.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Normalized (group: main1, hoop_end, hoop_mid) | Flat (1 row with main + tie_end + tie_mid) | Matches MIDAS 2-row block output |
| V1 cols | member_id, group, bar_type, dia_mm, n_bars, spacing_mm, level_from, level_to | — | 8 cols, 3 rows per column |
| V2 cols | member_id, main_bar_spec/total/count/additional/dia, tie_end_spec/legs/dia/spacing, tie_mid_spec/legs/dia/spacing | — | 22 cols, 1 row per column |
| Added | — | element_id, section_id, fck/fy/fys, b/h/height_mm | Traceability and geometry |
| Dropped | — | ratio_axial, ratio_moment_y/z, ratio_shear_end/mid | Moved to DesignResultsColumn |

### ReinforcementWall.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Normalized (1 row per direction per face) | Paired (V + H in same row) | One row = one wall element |
| V1 cols | rebar_id, section_id, member_id, member_type, level_from, level_to, zone_id, face, direction, direction_marker, bar_size, dia_mm, spacing_mm, bar_role, remarks, layer, source | — | 17 cols |
| V2 cols | wall_id, wall_mark, level, v_bar_spec, v_dia_mm, v_spacing_mm, h_bar_spec, h_dia_mm, h_spacing_mm, bar_layer, end_rebar | — | 11 cols |
| Added | — | wall_id | Numeric FK for joining with MembersWall |
| Dropped | rebar_id, section_id, member_type, zone_id, face, direction_marker, bar_role, remarks, source | — | Redundant or constant |

### ReinforcementSlab.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Renamed | location | layer | Top/Bot is a layer, not location |
| Renamed | dia_mm | bar_dia_mm | Consistent with bar_spacing_mm |
| Renamed | spacing_mm | bar_spacing_mm | |
| Added | — | level, bar_spec | Level and original spec string |
| Dropped | slab_type, design_position | — | Derivable from member lookup |

### ReinforcementStair.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Normalized with metadata | Compact per zone/direction/layer | Simpler |
| V2 cols | member_id, level_from, level_to, zone, direction, layer, bar_spec, bar_dia_mm, bar_spacing_mm | — | 9 cols |
| Dropped | rebar_id, section_id, member_type, distribution_mode, bar_size, n_bars, bar_role, source | — | Redundant |

### ReinforcementFooting.csv

| Change | V1 | V2 | Notes |
|--------|----|----|-------|
| Restructured | Isolated footing bars | Mat foundation zones (BASE/ADDITIONAL/STIRRUP) | Different foundation type |
| Added | — | zone, zone_type, zone_x/y_min/max, zone_boundary | Zone-based reinforcement layout |
| Added | — | n_legs | Stirrup legs for V-zones |

### DesignResultsBeam.csv (NEW in V2)

| V1 (mixed in ReinforcementBeam) | V2 (separate file) |
|------|------|
| Not separate | member_id, position, fck/fy/fys_MPa, top/bot/stirrup_spec, Mu_neg, phiMn_neg, ratio_negative, Mu_pos, phiMn_pos, ratio_positive, Vu, phiVc, ratio_shear |

### DesignResultsColumn.csv (NEW in V2)

| V1 (mixed in ReinforcementColumn) | V2 (separate file) |
|------|------|
| Not separate | member_id, fck/fy/fys_MPa, b/h/height_mm, main_bar_spec, phiPn_max, Pu, ratio_axial, ratio_moment_y/z, Vu_end/mid, ratio_shear_end/mid |

### DesignResultsWall.csv (NEW in V2)

| V1 (mixed in ReinforcementWall) | V2 (separate file) |
|------|------|
| Not separate | wall_id, wall_mark, level, fck/fy/fys_MPa, lw/htw/thickness_mm, ratio_axial/moment/shear |

---

## Key Design Decisions (V1 → V2)

| # | Decision | V1 Approach | V2 Approach | Reason |
|---|----------|------------|------------|--------|
| 1 | Coordinate system | Grid-based (X1Y1) | Coordinate-first (x_mm, y_mm) | Works with 0% grid coverage |
| 2 | Reinforcement structure | Normalized (1 bar per row) | Flat (all bars per member) | Matches MIDAS output, fewer joins |
| 3 | Design results | Mixed with reinforcement | Separate files | Clean separation, different consumers |
| 4 | Wall model | 2-node line | 4-node quad panel | Matches MIDAS element mesh |
| 5 | Footing model | Isolated pad | Mat foundation with zones | Handles real-world mat foundations |
| 6 | Slanted columns | Not handled | length_mm + x_top/y_top | Correct rebar length for inclined members |
| 7 | Level normalization | Manual | Automatic (R→Roof, P→PIT/PH, 1→1F) | Handles multiple project conventions |
| 8 | Section name parsing | PDF OCR | Regex patterns (20+ prefixes) | Handles both joined (6C1) and space-separated (1 B1) formats |
| 9 | FK between Members↔Reinforcement | member_id (same format) | design_key (raw MIDAS name) + wall_id (numeric) | Handles level-prefix mismatch |
| 10 | Basement walls | Not supported | Part C separate files | Different reinforcement pattern (zone × face) |
