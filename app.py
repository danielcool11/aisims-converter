"""
AISIMS Data Converter — Streamlit Application

Transforms raw MIDAS Gen exports into standardized CSVs for AISIMS V2.
Run: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import os
import io
import zipfile
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers.mgt import parse_mgt
from converters.nodes import convert_nodes, merge_boundary_nodes
from converters.materials import convert_materials
from converters.sections import convert_sections
from converters.elements import convert_elements
from converters.slabs import convert_slabs
from converters.stairs import convert_stairs
from converters.grid_detect import detect_grid_from_columns, reassign_node_grids
from converters.reinforcement_beam import convert_reinforcement_beam
from converters.reinforcement_column import convert_reinforcement_column
from converters.reinforcement_wall import convert_reinforcement_wall
from converters.reinforcement_slab import convert_reinforcement_slab
from converters.reinforcement_stair import convert_reinforcement_stair
from converters.footings import convert_footings
from converters.basement_walls import convert_basement_walls
from converters.validation import validate_outputs, format_report
from converters.wall_dedup import deduplicate_walls
from converters.junction_polygon import run_junction_detection
from tier2.rebar_lengths_beam import calculate_beam_rebar_lengths
from tier2.rebar_lengths_column import calculate_column_rebar_lengths
from tier2.rebar_lengths_slab import calculate_slab_rebar_lengths
from tier2.rebar_lengths_stair import calculate_stair_rebar_lengths
from tier2.rebar_lengths_wall import calculate_wall_rebar_lengths
from tier2.rebar_lengths_footing import calculate_footing_rebar_lengths
from tier2.rebar_lengths_basement_wall import calculate_basement_wall_rebar_lengths


# ══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS (must be defined before use)
# ══════════════════════════════════════════════════════════════

def _fix_bwall_nodes_from_elements(bwall_df, elem_df, nodes_df, log_fn):
    """Fix basement wall panel geometry using ELEMENT sheet quad nodes.

    The ELEMENT sheet has actual MIDAS quad elements with horizontal spans
    (n1→n2 = bottom edge, n3→n4 = top edge). Groups elements by Wall ID
    to find the full horizontal extent per panel, then updates the panel's
    centroid and length to match the actual geometry.

    Also adds synthetic node_i/node_j entries to nodes_df for junction detection.
    """
    import math

    if bwall_df is None or elem_df is None or bwall_df.empty or elem_df.empty:
        return

    # Build node_number → (x, y, z) lookup
    node_map = {}
    for _, r in nodes_df.iterrows():
        nn = r.get('node_number')
        if pd.notna(nn):
            node_map[int(nn)] = (float(r['x_mm']), float(r['y_mm']), float(r['z_mm']))

    # Group elements by (NAME, Position, Wall ID) to get all XY points per segment
    # Wall ID maps to wall_mark (W9901→RW1), Position = level
    seg_points = {}  # (name, level, wall_id) → set of (x, y)
    for _, e in elem_df.iterrows():
        name = str(e.get('NAME', ''))
        level = str(e.get('Position', ''))
        wid = int(e['Wall ID']) if pd.notna(e.get('Wall ID')) else None
        if not wid:
            continue
        key = (name, level, wid)
        if key not in seg_points:
            seg_points[key] = set()
        for ncol in ['Node 1', 'Node 2', 'Node 3', 'Node 4']:
            nn = int(e[ncol]) if pd.notna(e.get(ncol)) else None
            if nn and nn in node_map:
                xy = (round(node_map[nn][0], 1), round(node_map[nn][1], 1))
                seg_points[key].add(xy)

    # For each (name, level, wall_id), compute the horizontal bounding endpoints
    seg_extent = {}  # (name, level, wall_id) → (start_x, start_y, end_x, end_y, centroid_x, centroid_y, length)
    for (name, level, wid), points in seg_points.items():
        if len(points) < 2:
            continue
        pts = list(points)
        # Find the two most distant unique XY points (the endpoints of this wall segment)
        max_dist = 0
        p1, p2 = pts[0], pts[-1]
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = math.sqrt((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2)
                if d > max_dist:
                    max_dist = d
                    p1, p2 = pts[i], pts[j]
        cx = (p1[0] + p2[0]) / 2
        cy = (p1[1] + p2[1]) / 2
        seg_extent[(name, level, wid)] = (p1[0], p1[1], p2[0], p2[1], cx, cy, max_dist)

    # Match each bwall panel to its closest segment extent by centroid proximity
    fixed = 0
    for idx, row in bwall_df.iterrows():
        wm = str(row['wall_mark'])
        level = str(row['level'])
        cx = float(row.get('centroid_x_mm', 0) or 0)
        cy = float(row.get('centroid_y_mm', 0) or 0)

        # Find best matching segment
        best_key = None
        best_dist = float('inf')
        for (name, lv, wid), ext in seg_extent.items():
            if name != wm or lv != level:
                continue
            d = math.sqrt((ext[4] - cx) ** 2 + (ext[5] - cy) ** 2)
            if d < best_dist:
                best_dist = d
                best_key = (name, lv, wid)

        if best_key and best_dist < 5000:
            sx, sy, ex, ey, new_cx, new_cy, new_len = seg_extent[best_key]
            bwall_df.at[idx, 'centroid_x_mm'] = round(new_cx, 1)
            bwall_df.at[idx, 'centroid_y_mm'] = round(new_cy, 1)
            if new_len > 10:
                bwall_df.at[idx, 'length_mm'] = round(new_len, 1)
            # Store actual endpoint coordinates for proper rendering direction
            bwall_df.at[idx, 'start_x_mm'] = round(sx, 1)
            bwall_df.at[idx, 'start_y_mm'] = round(sy, 1)
            bwall_df.at[idx, 'end_x_mm'] = round(ex, 1)
            bwall_df.at[idx, 'end_y_mm'] = round(ey, 1)
            fixed += 1

    if fixed:
        log_fn(f"Fixed {fixed}/{len(bwall_df)} basement wall panels from ELEMENT geometry")


def _parse_grid_text(text: str) -> list:
    """Parse 'X1=0, X2=6000, X3=12000' into [(X1, 0), (X2, 6000), ...]"""
    result = []
    if not text:
        return result
    for part in text.split(','):
        part = part.strip()
        if '=' in part:
            label, val = part.split('=', 1)
            try:
                result.append((label.strip(), float(val.strip())))
            except ValueError:
                pass
    return result


def _positions_to_spacing(positions: list) -> list:
    """Convert absolute positions to (label, spacing) for nodes converter."""
    if not positions:
        return []
    result = [(positions[0][0], 0)]
    for i in range(1, len(positions)):
        label = positions[i][0]
        spacing = positions[i][1] - positions[i-1][1]
        result.append((label, spacing))
    return result


def _parse_grid_csv(grid_df: pd.DataFrame) -> tuple:
    """Parse grid definition CSV into x and y position lists."""
    grid_x = []
    grid_y = []
    for _, row in grid_df.iterrows():
        axis = str(row.get('axis', row.get('Axis', ''))).upper()
        label = str(row.get('label', row.get('Label', '')))
        pos = float(row.get('position_mm', row.get('Position_mm', 0)))
        if axis == 'X':
            grid_x.append((label, pos))
        elif axis == 'Y':
            grid_y.append((label, pos))
    return grid_x, grid_y


# ── Page config ──
st.set_page_config(page_title="AISIMS Data Converter", layout="wide")
st.title("AISIMS Data Converter")
st.caption("Transforms raw MIDAS Gen exports into standardized CSVs for AISIMS V2")

# ── Session state ──
if 'outputs' not in st.session_state:
    st.session_state.outputs = None
if 'log' not in st.session_state:
    st.session_state.log = []


def log(msg: str):
    st.session_state.log.append(msg)


# ══════════════════════════════════════════════════════════════
# STEP 1: FILE UPLOAD
# ══════════════════════════════════════════════════════════════
st.header("Step 1: Upload Files")

col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Part A - MIDAS Gen Exports")

    nodes_file = st.file_uploader("Nodes.csv", type=['csv'], key='nodes')
    materials_file = st.file_uploader("Materials.csv", type=['csv'], key='materials')
    sections_file = st.file_uploader("Sections.csv", type=['csv'], key='sections')
    elements_file = st.file_uploader("Elements.csv", type=['csv'], key='elements')
    thickness_file = st.file_uploader("Thickness.csv", type=['csv'], key='thickness')
    story_file = st.file_uploader("StoryDefinition.csv", type=['csv'], key='story')
    design_beam_file = st.file_uploader("DesignBeam.csv", type=['csv'], key='design_beam')
    design_col_file = st.file_uploader("DesignColumn.csv", type=['csv'], key='design_col')
    design_wall_file = st.file_uploader("DesignWall.csv", type=['csv'], key='design_wall')
    mgt_file = st.file_uploader("project.mgt (optional)", type=['mgt', 'txt'], key='mgt')

with col_b:
    st.subheader("Part B - Engineer Data")

    slab_boundary_file = st.file_uploader("SlabBoundary.csv", type=['csv'], key='slab_boundary')
    slab_reinf_file = st.file_uploader("SlabReinforcement.csv", type=['csv'], key='slab_reinf')
    stair_reinf_file = st.file_uploader("StairReinforcement.csv", type=['csv'], key='stair_reinf')
    foot_boundary_file = st.file_uploader("FootBoundary.csv", type=['csv'], key='foot_boundary')
    foot_reinf_file = st.file_uploader("FootReinforcement.csv", type=['csv'], key='foot_reinf')

    st.subheader("Part C - Basement Walls")
    bwall_file = st.file_uploader("Part C Excel (BasementWall)", type=['xlsx'], key='bwall')

# ══════════════════════════════════════════════════════════════
# STEP 2: GRID DEFINITION
# ══════════════════════════════════════════════════════════════
st.header("Step 2: Grid Definition")

grid_mode = st.radio(
    "Grid source:",
    ["Auto-detect from columns", "Manual entry", "Upload CSV"],
    horizontal=True,
)

grid_x_input = None
grid_y_input = None
x_origin = 0.0
y_origin = 0.0

if grid_mode == "Manual entry":
    st.caption("Enter grid labels and spacings (comma-separated). E.g.: X1=0, X2=6000, X3=12000")

    col_gx, col_gy = st.columns(2)
    with col_gx:
        x_text = st.text_area("X-axis grid (label=position_mm)", "X1=0, X2=6000, X3=12000")
    with col_gy:
        y_text = st.text_area("Y-axis grid (label=position_mm)", "Y1=0, Y2=8000")

    x_origin = st.number_input("X origin (mm)", value=0.0)
    y_origin = st.number_input("Y origin (mm)", value=0.0)

    grid_x_input = _parse_grid_text(x_text)
    grid_y_input = _parse_grid_text(y_text)

elif grid_mode == "Upload CSV":
    grid_csv = st.file_uploader("Grid definition CSV", type=['csv'], key='grid_csv')
    if grid_csv:
        grid_df = pd.read_csv(grid_csv)
        grid_x_input, grid_y_input = _parse_grid_csv(grid_df)

# ══════════════════════════════════════════════════════════════
# STEP 3: CONVERT
# ══════════════════════════════════════════════════════════════
st.header("Step 3: Convert")

if st.button("CONVERT", type="primary", use_container_width=True):
    st.session_state.log = []
    st.session_state.outputs = None

    progress = st.progress(0, text="Starting conversion...")
    outputs = {}

    try:
        # ── Validate required files ──
        if not all([nodes_file, materials_file, sections_file, elements_file, story_file]):
            st.error("Missing required Part A files: Nodes, Materials, Sections, Elements, StoryDefinition")
            st.stop()

        # ── Read CSVs ──
        progress.progress(5, text="Reading input files...")
        nodes_raw = pd.read_csv(nodes_file, encoding='utf-8-sig')
        materials_raw = pd.read_csv(materials_file, encoding='utf-8-sig')
        sections_raw = pd.read_csv(sections_file, encoding='utf-8-sig')
        elements_raw = pd.read_csv(elements_file, encoding='utf-8-sig')
        story_raw = pd.read_csv(story_file, encoding='utf-8-sig')

        thickness_raw = pd.read_csv(thickness_file, encoding='utf-8-sig') if thickness_file else None

        # Parse MGT if provided
        mgt_data = None
        if mgt_file:
            # Save temp file for MGT parser
            mgt_bytes = mgt_file.read()
            mgt_path = os.path.join('output', '_temp_mgt.txt')
            os.makedirs('output', exist_ok=True)
            with open(mgt_path, 'wb') as f:
                f.write(mgt_bytes)
            mgt_data = parse_mgt(mgt_path)
            os.remove(mgt_path)
            log(f"MGT parsed: {mgt_data.get('unit')}, "
                f"{len(mgt_data.get('rebar_grades', {}))} rebar grades, "
                f"{len(mgt_data.get('wall_marks', {}))} wall marks")

        # ── Phase 1: Foundation ──
        progress.progress(15, text="Phase 1: Nodes...")

        # Convert nodes (initially without grid or with manual grid)
        cover_path = os.path.join('config', 'cover_requirements.csv')
        if not os.path.exists(cover_path):
            cover_path = None

        if grid_mode == "Manual entry" and grid_x_input and grid_y_input:
            # Convert positions to spacing format
            grid_x_spacing = _positions_to_spacing(grid_x_input)
            grid_y_spacing = _positions_to_spacing(grid_y_input)
            nodes_df = convert_nodes(nodes_raw, story_raw,
                                     grid_x=grid_x_spacing, grid_y=grid_y_spacing,
                                     x_origin=x_origin, y_origin=y_origin)
        else:
            # No grid yet — will auto-detect after Phase 2
            nodes_df = convert_nodes(nodes_raw, story_raw)

        outputs['nodes'] = nodes_df
        log(f"Nodes: {len(nodes_df)} nodes")

        progress.progress(25, text="Phase 1: Materials...")
        materials_df = convert_materials(materials_raw, mgt_data)
        outputs['materials'] = materials_df
        log(f"Materials: {len(materials_df)} materials")

        progress.progress(30, text="Phase 1: Sections...")
        # Extract story names for resolving ambiguous levels (P -> PIT or PH)
        story_names = story_raw.iloc[:, 1].dropna().astype(str).str.strip().tolist() \
            if story_raw is not None else []
        sections_df, section_lookup, thickness_lookup = convert_sections(
            sections_raw, thickness_raw, cover_path, story_names
        )
        outputs['sections'] = sections_df
        log(f"Sections: {len(sections_df)} sections")

        # ── Phase 2: Members ──
        progress.progress(40, text="Phase 2: Elements → Members...")
        wall_marks = mgt_data.get('wall_marks', {}) if mgt_data else {}
        elem_result = convert_elements(
            elements_raw, nodes_df, section_lookup, thickness_lookup, wall_marks
        )
        outputs['beams'] = elem_result['beams']
        outputs['columns'] = elem_result['columns']
        outputs['walls'] = elem_result['walls']
        log(f"Members: {len(elem_result['beams'])} beams, "
            f"{len(elem_result['columns'])} columns, "
            f"{len(elem_result['walls'])} walls")

        # ── Phase 2.5: Grid auto-detection ──
        if grid_mode == "Auto-detect from columns" and not elem_result['columns'].empty:
            progress.progress(50, text="Phase 2.5: Auto-detecting grid...")
            col_df = elem_result['columns']
            col_positions = list(zip(col_df['x_mm'].tolist(), col_df['y_mm'].tolist()))
            grid_result = detect_grid_from_columns(col_positions)

            if grid_result['grid_x'] and grid_result['grid_y']:
                reassign_node_grids(
                    nodes_df,
                    grid_result['grid_x'],
                    grid_result['grid_y'],
                )
                log(f"Grid: {len(grid_result['grid_x'])} X-lines, "
                    f"{len(grid_result['grid_y'])} Y-lines detected")

                # Re-run elements with updated nodes
                elem_result = convert_elements(
                    elements_raw, nodes_df, section_lookup, thickness_lookup, wall_marks
                )
                outputs['beams'] = elem_result['beams']
                outputs['columns'] = elem_result['columns']
                outputs['walls'] = elem_result['walls']

        # Read Part B files once (file pointer can only be read once)
        slab_boundary_raw = None
        slab_reinf_raw = None
        stair_raw = None

        if slab_boundary_file:
            slab_boundary_raw = pd.read_csv(slab_boundary_file, encoding='utf-8-sig')
        if slab_reinf_file:
            slab_reinf_raw = pd.read_csv(slab_reinf_file, encoding='utf-8-sig')
        if stair_reinf_file:
            stair_raw = pd.read_csv(stair_reinf_file, encoding='utf-8-sig')

        foot_boundary_raw = None
        foot_reinf_raw = None
        if foot_boundary_file:
            # Try utf-8-sig first, fall back to cp949 for Korean encoded files
            try:
                foot_boundary_raw = pd.read_csv(foot_boundary_file, encoding='utf-8-sig')
            except UnicodeDecodeError:
                foot_boundary_file.seek(0)
                foot_boundary_raw = pd.read_csv(foot_boundary_file, encoding='cp949')
        if foot_reinf_file:
            try:
                foot_reinf_raw = pd.read_csv(foot_reinf_file, encoding='utf-8-sig')
            except UnicodeDecodeError:
                foot_reinf_file.seek(0)
                foot_reinf_raw = pd.read_csv(foot_reinf_file, encoding='cp949')

        # Slabs (also extracts stair boundary data)
        stair_boundaries = {}
        if slab_boundary_raw is not None and slab_reinf_raw is not None:
            progress.progress(55, text="Phase 2: Slabs...")
            slabs_df, stair_boundaries = convert_slabs(slab_boundary_raw, slab_reinf_raw, nodes_df)
            outputs['slabs'] = slabs_df
            log(f"Slabs: {len(slabs_df)} slab members")

        # Stairs (uses boundary data from slabs for location)
        if stair_raw is not None:
            progress.progress(60, text="Phase 2: Stairs...")
            walls_for_stair = elem_result['walls'] if 'walls' in elem_result else None
            stairs_df = convert_stairs(stair_raw, stair_boundaries, nodes_df, walls_for_stair)
            outputs['stairs'] = stairs_df
            log(f"Stairs: {len(stairs_df)} stair members")

        # Footings
        if foot_boundary_raw is not None and foot_reinf_raw is not None:
            progress.progress(62, text="Phase 2: Footings...")
            # Merge footing boundary nodes into main nodes table
            nodes_df = merge_boundary_nodes(nodes_df, foot_boundary_raw)
            outputs['nodes'] = nodes_df  # update with merged nodes
            footings_df, reinf_footing_df = convert_footings(foot_boundary_raw, foot_reinf_raw)
            outputs['footings'] = footings_df
            outputs['reinf_footing'] = reinf_footing_df
            log(f"Footings: {len(footings_df)} members, {len(reinf_footing_df)} reinforcement rows")

        # Basement walls (Part C) — separate from standard walls
        if bwall_file:
            progress.progress(63, text="Phase 2: Basement walls...")
            bwall_boundary = pd.read_excel(bwall_file, sheet_name='BasementWall Boundary', header=1)
            bwall_reinf = pd.read_excel(bwall_file, sheet_name='BasementWall Reinforcement', header=1)
            bwall_members, bwall_reinf_df = convert_basement_walls(bwall_boundary, bwall_reinf, nodes_df)
            outputs['bwall_members'] = bwall_members
            outputs['reinf_bwall'] = bwall_reinf_df
            log(f"Basement walls: {len(bwall_members)} panels, {len(bwall_reinf_df)} reinforcement rows")

            # Parse element-to-wall mapping sheet for dedup cross-validation
            try:
                bwall_elements = pd.read_excel(bwall_file, sheet_name='BasementWall Boundary (ELEMENT)', header=1)
                outputs['bwall_elements'] = bwall_elements
                log(f"Basement wall elements: {len(bwall_elements)} element mappings loaded")

                # Fix basement wall node positions from ELEMENT sheet quad nodes.
                # The ELEMENT sheet has actual MIDAS quad elements with horizontal spans,
                # while boundary sheet nodes may be vertical (same XY) pairs.
                _fix_bwall_nodes_from_elements(outputs['bwall_members'], bwall_elements, nodes_df, log)
            except Exception as e:
                log(f"Basement wall element processing: {e}")

        # ── Phase 3: Reinforcement ──
        if design_beam_file:
            progress.progress(65, text="Phase 3: Beam reinforcement...")
            design_beam_raw = pd.read_csv(design_beam_file, encoding='utf-8-sig', header=None)
            reinf_beam_df, design_beam_df = convert_reinforcement_beam(design_beam_raw, section_lookup)
            outputs['reinf_beam'] = reinf_beam_df
            outputs['design_beam'] = design_beam_df
            log(f"ReinfBeam: {len(reinf_beam_df)} rows, DesignBeam: {len(design_beam_df)} rows")

        if design_col_file:
            progress.progress(70, text="Phase 3: Column reinforcement...")
            design_col_raw = pd.read_csv(design_col_file, encoding='utf-8-sig', header=None)
            reinf_col_df, design_col_df = convert_reinforcement_column(design_col_raw, section_lookup)
            outputs['reinf_column'] = reinf_col_df
            outputs['design_column'] = design_col_df
            log(f"ReinfColumn: {len(reinf_col_df)} rows, DesignColumn: {len(design_col_df)} rows")

        if design_wall_file:
            progress.progress(75, text="Phase 3: Wall reinforcement...")
            design_wall_raw = pd.read_csv(design_wall_file, encoding='utf-8-sig', header=None)
            reinf_wall_df, design_wall_df = convert_reinforcement_wall(design_wall_raw)
            outputs['reinf_wall'] = reinf_wall_df
            outputs['design_wall'] = design_wall_df
            log(f"ReinfWall: {len(reinf_wall_df)} rows, DesignWall: {len(design_wall_df)} rows")

        if slab_reinf_raw is not None:
            progress.progress(80, text="Phase 3: Slab reinforcement...")
            reinf_slab_df = convert_reinforcement_slab(slab_reinf_raw)
            outputs['reinf_slab'] = reinf_slab_df
            log(f"ReinfSlab: {len(reinf_slab_df)} rows")

        if stair_raw is not None:
            progress.progress(85, text="Phase 3: Stair reinforcement...")
            reinf_stair_df = convert_reinforcement_stair(stair_raw)
            outputs['reinf_stair'] = reinf_stair_df
            log(f"ReinfStair: {len(reinf_stair_df)} rows")

        # ── Wall deduplication (remove elements covered by Part C) ──
        if 'walls' in outputs and outputs['walls'] is not None:
            outputs['walls'] = deduplicate_walls(
                outputs['walls'],
                outputs.get('reinf_wall'),
                outputs.get('bwall_members'),
                outputs.get('nodes'),
                outputs.get('bwall_elements'),
            )
            log(f"Wall dedup: {len(outputs['walls'])} elements after dedup")

        # ── Junction Detection (polygon-based) ──
        if 'nodes' in outputs and outputs['nodes'] is not None:
            progress.progress(83, text="Junction detection...")
            try:
                # Build nodes dict: node_id → {x_mm, y_mm, z_mm}
                nodes_dict = {}
                for _, nr in outputs['nodes'].iterrows():
                    nid = str(nr.get('node_id', ''))
                    if nid:
                        nodes_dict[nid] = {
                            'x_mm': float(nr.get('x_mm', 0) or 0),
                            'y_mm': float(nr.get('y_mm', 0) or 0),
                            'z_mm': float(nr.get('z_mm', 0) or 0),
                        }

                cols_df = outputs.get('columns')
                beams_df = outputs.get('beams')
                walls_df = outputs.get('walls')

                result_cols, result_beams, result_walls = run_junction_detection(
                    columns_df=cols_df,
                    beams_df=beams_df,
                    walls_df=walls_df,
                    nodes=nodes_dict,
                )
                if result_cols is not None:
                    outputs['columns'] = result_cols
                if result_beams is not None:
                    outputs['beams'] = result_beams
                if result_walls is not None:
                    outputs['walls'] = result_walls

                # Basement walls: each CSV row = one segment (use row index as wall_id)
                bw_df = outputs.get('bwall_members')
                if bw_df is not None and len(bw_df) > 0:
                    from converters.junction_polygon import process_wall_junctions
                    import math
                    bw_df = bw_df.copy()
                    bw_df['element_id'] = range(9000, 9000 + len(bw_df))
                    bw_df['wall_id'] = range(9000, 9000 + len(bw_df))

                    # Fix vertical panels: when node_i XY == node_j XY,
                    # create synthetic nodes from centroid + length
                    synth_count = 0
                    for idx, row in bw_df.iterrows():
                        ni = str(row.get('node_i', ''))
                        nj = str(row.get('node_j', ''))
                        ci = nodes_dict.get(ni, {})
                        cj = nodes_dict.get(nj, {})
                        if ci and cj:
                            dx = abs(ci['x_mm'] - cj['x_mm'])
                            dy = abs(ci['y_mm'] - cj['y_mm'])
                            if dx < 1 and dy < 1:
                                # Vertical panel — reconstruct from centroid + length
                                cx = float(row.get('centroid_x_mm', 0) or 0)
                                cy = float(row.get('centroid_y_mm', 0) or 0)
                                length = float(row.get('length_mm', 0) or 0)
                                if length > 100 and (cx != 0 or cy != 0):
                                    half = length / 2
                                    # Infer direction from centroid vs node
                                    dx_c = abs(cx - ci['x_mm'])
                                    dy_c = abs(cy - ci['y_mm'])
                                    syn_i = f'_SYN_BWI_{idx}'
                                    syn_j = f'_SYN_BWJ_{idx}'
                                    z_avg = (ci.get('z_mm', 0) + cj.get('z_mm', 0)) / 2
                                    if dx_c > dy_c:
                                        nodes_dict[syn_i] = {'x_mm': cx - half, 'y_mm': cy, 'z_mm': z_avg}
                                        nodes_dict[syn_j] = {'x_mm': cx + half, 'y_mm': cy, 'z_mm': z_avg}
                                    else:
                                        nodes_dict[syn_i] = {'x_mm': cx, 'y_mm': cy - half, 'z_mm': z_avg}
                                        nodes_dict[syn_j] = {'x_mm': cx, 'y_mm': cy + half, 'z_mm': z_avg}
                                    bw_df.at[idx, 'node_i'] = syn_i
                                    bw_df.at[idx, 'node_j'] = syn_j
                                    synth_count += 1

                    if synth_count:
                        log(f"Junction: {synth_count} basement wall panels reconstructed from centroid")

                        # Merge nearby synthetic nodes (within 500mm) to share node_ids
                        # so the junction algorithm can detect connections
                        syn_nodes = {k: v for k, v in nodes_dict.items() if k.startswith('_SYN_')}
                        syn_keys = list(syn_nodes.keys())
                        merge_map = {}  # old_key → canonical_key
                        for i, ki in enumerate(syn_keys):
                            if ki in merge_map:
                                continue
                            pi = syn_nodes[ki]
                            for j in range(i + 1, len(syn_keys)):
                                kj = syn_keys[j]
                                if kj in merge_map:
                                    continue
                                pj = syn_nodes[kj]
                                d = math.sqrt((pi['x_mm'] - pj['x_mm'])**2 +
                                              (pi['y_mm'] - pj['y_mm'])**2)
                                if d < 500:
                                    merge_map[kj] = ki

                        if merge_map:
                            for idx, row in bw_df.iterrows():
                                ni = str(row.get('node_i', ''))
                                nj = str(row.get('node_j', ''))
                                if ni in merge_map:
                                    bw_df.at[idx, 'node_i'] = merge_map[ni]
                                if nj in merge_map:
                                    bw_df.at[idx, 'node_j'] = merge_map[nj]

                    result_bw = process_wall_junctions(bw_df, nodes_dict)
                    # Keep only the extension + polygon columns, drop synthetic IDs
                    for col in ['extend_start_mm', 'extend_end_mm']:
                        outputs['bwall_members'][col] = result_bw[col].values
                    for col in [c for c in result_bw.columns if c.startswith('poly_')]:
                        outputs['bwall_members'][col] = result_bw[col].values

                log("Junction detection complete")
            except Exception as e:
                log(f"Junction detection FAILED: {e}")

        # ── Phase 5: Validation ──
        progress.progress(85, text="Phase 5: Validation...")
        validation_results = validate_outputs(outputs)
        report_text = format_report(validation_results)
        outputs['validation_report'] = report_text
        log("Validation complete")

        # ── Phase 6: Tier 2 Rebar Lengths ──
        dev_path = os.path.join(os.path.dirname(__file__), 'config', 'development_lengths.csv')
        lap_path = os.path.join(os.path.dirname(__file__), 'config', 'lap_splice.csv')
        cover_path = os.path.join(os.path.dirname(__file__), 'config', 'cover_requirements.csv')

        if os.path.exists(dev_path) and os.path.exists(lap_path):
            tier2_count = 0

            # Beam
            if all(k in outputs for k in ('beams', 'columns', 'sections', 'reinf_beam', 'nodes')):
                progress.progress(87, text="Phase 6: Rebar lengths - Beam...")
                try:
                    rebar_beam = calculate_beam_rebar_lengths(
                        outputs['beams'], outputs['columns'], outputs['sections'],
                        outputs['reinf_beam'], outputs['nodes'], dev_path, lap_path)
                    outputs['rebar_beam'] = rebar_beam
                    log(f"RebarLengthsBeam: {len(rebar_beam)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsBeam FAILED: {e}")

            # Column
            if all(k in outputs for k in ('columns', 'reinf_column', 'sections', 'nodes')):
                progress.progress(89, text="Phase 6: Rebar lengths - Column...")
                try:
                    rebar_col = calculate_column_rebar_lengths(
                        outputs['columns'], outputs['reinf_column'], outputs['sections'],
                        outputs['nodes'], dev_path, lap_path)
                    outputs['rebar_column'] = rebar_col
                    log(f"RebarLengthsColumn: {len(rebar_col)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsColumn FAILED: {e}")

            # Slab
            if all(k in outputs for k in ('slabs', 'reinf_slab', 'beams', 'nodes')):
                progress.progress(91, text="Phase 6: Rebar lengths - Slab...")
                try:
                    rebar_slab = calculate_slab_rebar_lengths(
                        outputs['slabs'], outputs['reinf_slab'], outputs['beams'],
                        outputs['nodes'], dev_path, lap_path)
                    outputs['rebar_slab'] = rebar_slab
                    log(f"RebarLengthsSlab: {len(rebar_slab)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsSlab FAILED: {e}")

            # Stair
            if all(k in outputs for k in ('stairs', 'reinf_stair')):
                progress.progress(93, text="Phase 6: Rebar lengths - Stair...")
                try:
                    rebar_stair = calculate_stair_rebar_lengths(
                        outputs['stairs'], outputs['reinf_stair'],
                        dev_path, lap_path, cover_path=cover_path)
                    outputs['rebar_stair'] = rebar_stair
                    log(f"RebarLengthsStair: {len(rebar_stair)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsStair FAILED: {e}")

            # Wall
            if all(k in outputs for k in ('walls', 'reinf_wall', 'nodes')):
                progress.progress(95, text="Phase 6: Rebar lengths - Wall...")
                try:
                    rebar_wall = calculate_wall_rebar_lengths(
                        outputs['walls'], outputs['reinf_wall'], outputs['nodes'],
                        dev_path, lap_path, cover_path=cover_path)
                    outputs['rebar_wall'] = rebar_wall
                    log(f"RebarLengthsWall: {len(rebar_wall)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsWall FAILED: {e}")

            # Footing
            if all(k in outputs for k in ('footings', 'reinf_footing')):
                progress.progress(97, text="Phase 6: Rebar lengths - Footing...")
                try:
                    rebar_footing = calculate_footing_rebar_lengths(
                        outputs['footings'], outputs['reinf_footing'],
                        dev_path, lap_path, cover_path=cover_path)
                    outputs['rebar_footing'] = rebar_footing
                    log(f"RebarLengthsFooting: {len(rebar_footing)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsFooting FAILED: {e}")

            # Basement Wall
            if all(k in outputs for k in ('bwall_members', 'reinf_bwall', 'nodes')):
                progress.progress(99, text="Phase 6: Rebar lengths - Basement Wall...")
                try:
                    rebar_bwall = calculate_basement_wall_rebar_lengths(
                        outputs['bwall_members'], outputs['reinf_bwall'], outputs['nodes'],
                        dev_path, lap_path, cover_path=cover_path)
                    outputs['rebar_bwall'] = rebar_bwall
                    log(f"RebarLengthsBasementWall: {len(rebar_bwall)} records")
                    tier2_count += 1
                except Exception as e:
                    log(f"RebarLengthsBasementWall FAILED: {e}")

            log(f"Tier 2 complete: {tier2_count} calculators ran")
        else:
            log("Tier 2 skipped: development_lengths.csv or lap_splice.csv not found")

        progress.progress(100, text="Done!")
        st.session_state.outputs = outputs

    except Exception as e:
        st.error(f"Conversion failed: {e}")
        import traceback
        st.code(traceback.format_exc())

# ══════════════════════════════════════════════════════════════
# STEP 4: RESULTS
# ══════════════════════════════════════════════════════════════
if st.session_state.outputs:
    st.header("Step 4: Results")
    outputs = st.session_state.outputs

    # Show log
    with st.expander("Conversion Log", expanded=False):
        for msg in st.session_state.log:
            st.text(msg)

    # Show validation report
    if 'validation_report' in outputs:
        with st.expander("Validation Report", expanded=True):
            st.code(outputs['validation_report'])

    # Output file mapping
    file_map = {
        'nodes': 'Nodes.csv',
        'materials': 'Materials.csv',
        'sections': 'Sections.csv',
        'beams': 'MembersBeam.csv',
        'columns': 'MembersColumn.csv',
        'walls': 'MembersWall.csv',
        'slabs': 'MembersSlab.csv',
        'stairs': 'MembersStair.csv',
        'reinf_beam': 'ReinforcementBeam.csv',
        'design_beam': 'DesignResultsBeam.csv',
        'reinf_column': 'ReinforcementColumn.csv',
        'design_column': 'DesignResultsColumn.csv',
        'reinf_wall': 'ReinforcementWall.csv',
        'design_wall': 'DesignResultsWall.csv',
        'bwall_members': 'MembersBasementWall.csv',
        'reinf_bwall': 'ReinforcementBasementWall.csv',
        'reinf_slab': 'ReinforcementSlab.csv',
        'reinf_stair': 'ReinforcementStair.csv',
        'footings': 'MembersFooting.csv',
        'reinf_footing': 'ReinforcementFooting.csv',
        'rebar_beam': 'RebarLengthsBeam.csv',
        'rebar_column': 'RebarLengthsColumn.csv',
        'rebar_slab': 'RebarLengthsSlab.csv',
        'rebar_stair': 'RebarLengthsStair.csv',
        'rebar_wall': 'RebarLengthsWall.csv',
        'rebar_footing': 'RebarLengthsFooting.csv',
        'rebar_bwall': 'RebarLengthsBasementWall.csv',
    }

    # Show previews
    cols = st.columns(3)
    col_idx = 0
    for key, filename in file_map.items():
        df = outputs.get(key)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            with cols[col_idx % 3]:
                st.metric(filename, f"{len(df)} rows")
                with st.expander(f"Preview {filename}"):
                    st.dataframe(df.head(10), use_container_width=True)
            col_idx += 1

    # Download all as ZIP
    st.subheader("Download")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for key, filename in file_map.items():
            df = outputs.get(key)
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                csv_str = df.to_csv(index=False)
                zf.writestr(filename, csv_str)

        # Add validation report
        if 'validation_report' in outputs:
            zf.writestr('ValidationReport.txt', outputs['validation_report'])

    st.download_button(
        "Download All CSVs (.zip)",
        data=zip_buffer.getvalue(),
        file_name="AISIMS_Converted.zip",
        mime="application/zip",
        use_container_width=True,
    )
