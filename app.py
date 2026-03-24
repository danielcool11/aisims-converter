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
from converters.nodes import convert_nodes
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
from converters.validation import validate_outputs, format_report


# ══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS (must be defined before use)
# ══════════════════════════════════════════════════════════════

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
    st.subheader("Part A — MIDAS Gen Exports")

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
    st.subheader("Part B — Engineer Data")

    slab_boundary_file = st.file_uploader("SlabBoundary.csv", type=['csv'], key='slab_boundary')
    slab_reinf_file = st.file_uploader("SlabReinforcement.csv", type=['csv'], key='slab_reinf')
    stair_reinf_file = st.file_uploader("StairReinforcement.csv", type=['csv'], key='stair_reinf')

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
        sections_df, section_lookup, thickness_lookup = convert_sections(
            sections_raw, thickness_raw, cover_path
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

        # ── Phase 3: Reinforcement ──
        if design_beam_file:
            progress.progress(65, text="Phase 3: Beam reinforcement...")
            design_beam_raw = pd.read_csv(design_beam_file, encoding='utf-8-sig', header=None)
            reinf_beam_df = convert_reinforcement_beam(design_beam_raw, section_lookup)
            outputs['reinf_beam'] = reinf_beam_df
            log(f"ReinfBeam: {len(reinf_beam_df)} rows")

        if design_col_file:
            progress.progress(70, text="Phase 3: Column reinforcement...")
            design_col_raw = pd.read_csv(design_col_file, encoding='utf-8-sig', header=None)
            reinf_col_df = convert_reinforcement_column(design_col_raw, section_lookup)
            outputs['reinf_column'] = reinf_col_df
            log(f"ReinfColumn: {len(reinf_col_df)} rows")

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

        # ── Phase 5: Validation ──
        progress.progress(90, text="Phase 5: Validation...")
        validation_results = validate_outputs(outputs)
        report_text = format_report(validation_results)
        outputs['validation_report'] = report_text
        log("Validation complete")

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
        'reinf_column': 'ReinforcementColumn.csv',
        'reinf_wall': 'ReinforcementWall.csv',
        'design_wall': 'DesignResultsWall.csv',
        'reinf_slab': 'ReinforcementSlab.csv',
        'reinf_stair': 'ReinforcementStair.csv',
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
