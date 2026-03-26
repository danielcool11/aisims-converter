"""
Validation module — cross-checks all converter outputs for consistency.

Checks:
1. Completeness: expected output files are non-empty
2. Material presence: at least 1 concrete + 1 rebar material
3. Geometry sanity: no zero-length beams, no zero-height columns
4. Node grid coverage
5. Reinforcement coverage: every beam/column member has reinforcement
   (uses base member_id matching — strips level prefix)
6. Section type distribution: no UNKNOWN types
"""

import re


def _extract_base_member_id(name: str) -> str:
    """
    Strip level prefix/suffix from a member_id to get the base ID.

    Project 1 style (joined):
        '6C1'           → 'C1'
        '-2~-1TC1'      → 'TC1'
        '3~4B11'        → 'B11'
        '-1G1'          → 'G1'
        'PHRWG1'        → 'WG1'
        'LB1'           → 'LB1'

    Project 2 style (space-separated, parenthetical):
        '-1~-4 G1'      → 'G1'
        '1 B1'          → 'B1'
        'P G8A'         → 'G8A'
        'TC1 (1-P)'     → 'TC1'
        'C1 (B5-B1)'    → 'C1'
        'P G8A (sayOK)' → 'G8A'
        '3~R WB2~5'     → 'WB2'
    """
    if not name or not isinstance(name, str):
        return str(name)

    name = name.strip()

    # Strip annotations like (sayOK)
    name = re.sub(r'\s*\(say\w*\)\s*$', '', name, flags=re.IGNORECASE)

    # Project 2: member_id (level_range) — 'TC1 (1-P)', 'C1 (B5-B1)'
    m = re.match(r'^([A-Za-z]+\d+[A-Za-z]*)\s*\([^)]+\)$', name)
    if m:
        return m.group(1)

    # Project 2: space-separated — '-1~-4 G1', '1 B1', 'P G8A', '3~R WB2~5'
    if ' ' in name:
        parts = name.split(None, 1)
        member_part = parts[1].strip() if len(parts) > 1 else parts[0]
        # Remove trailing sub-range like ~5 from WB2~5
        member_part = re.sub(r'~\d+$', '', member_part)
        return member_part

    # Project 1: level_range + member — '3~4TC1', '-2~-1B11'
    m = re.match(r'^-?\d+~-?\d+([A-Za-z]+\d+[A-Za-z]*)$', name)
    if m:
        return m.group(1)

    # Project 1: single_floor + member — '6C1', '-1G1'
    m = re.match(r'^-?\d+([A-Za-z]+\d+[A-Za-z]*)$', name)
    if m:
        return m.group(1)

    # Project 1: R/PH/PHR prefix + member — 'RG1', 'PHRWG1'
    m = re.match(r'^(?:PHR?|R)([A-Za-z]*\d+[A-Za-z]*)$', name)
    if m:
        return m.group(1)

    # No prefix — already base
    return name


def validate_outputs(outputs: dict) -> list:
    """
    Run all validation checks on converter outputs.

    Args:
        outputs: dict with keys matching output names → DataFrames
            Expected keys: 'nodes', 'materials', 'sections',
            'beams', 'columns', 'walls', 'slabs', 'stairs',
            'reinf_beam', 'reinf_column', 'reinf_wall',
            'reinf_slab', 'reinf_stair'

    Returns:
        list of validation result dicts:
            {check: str, status: 'PASS'|'WARN'|'FAIL', detail: str}
    """
    results = []

    nodes = outputs.get('nodes')
    materials = outputs.get('materials')
    sections = outputs.get('sections')
    beams = outputs.get('beams')
    columns = outputs.get('columns')
    walls = outputs.get('walls')
    slabs = outputs.get('slabs')
    stairs = outputs.get('stairs')
    reinf_beam = outputs.get('reinf_beam')
    reinf_column = outputs.get('reinf_column')

    # 1. Completeness check
    for name, df in outputs.items():
        if isinstance(df, str):
            continue  # skip validation_report text
        if df is not None and not df.empty:
            results.append({
                'check': f'Completeness: {name}',
                'status': 'PASS',
                'detail': f'{len(df)} rows',
            })
        elif df is not None and df.empty:
            results.append({
                'check': f'Completeness: {name}',
                'status': 'WARN',
                'detail': 'Empty DataFrame',
            })

    # 2. Materials check
    if materials is not None and not materials.empty:
        concrete = materials[materials['type'] == 'concrete']
        rebar = materials[materials['type'] == 'rebar']
        if len(concrete) == 0:
            results.append({
                'check': 'Materials: concrete present',
                'status': 'FAIL',
                'detail': 'No concrete materials found',
            })
        else:
            results.append({
                'check': 'Materials: concrete present',
                'status': 'PASS',
                'detail': f'{len(concrete)} concrete grades',
            })
        if len(rebar) == 0:
            results.append({
                'check': 'Materials: rebar present',
                'status': 'WARN',
                'detail': 'No rebar materials found (using default SD400)',
            })
        else:
            results.append({
                'check': 'Materials: rebar present',
                'status': 'PASS',
                'detail': f'{len(rebar)} rebar grades',
            })

    # 3. Geometry sanity — beams
    if beams is not None and not beams.empty and 'length_mm' in beams.columns:
        zero_len = beams[beams['length_mm'] == 0]
        if len(zero_len) > 0:
            ids = zero_len['member_id'].unique()[:5].tolist()
            results.append({
                'check': 'Geometry: zero-length beams',
                'status': 'WARN',
                'detail': f'{len(zero_len)} beams with length=0 ({ids})',
            })
        else:
            results.append({
                'check': 'Geometry: beam lengths',
                'status': 'PASS',
                'detail': f'All {len(beams)} beams have positive length',
            })

    # 4. Geometry sanity — columns
    if columns is not None and not columns.empty and 'height_mm' in columns.columns:
        zero_h = columns[columns['height_mm'] == 0]
        if len(zero_h) > 0:
            ids = zero_h['member_id'].unique().tolist()
            results.append({
                'check': 'Geometry: zero-height columns',
                'status': 'WARN',
                'detail': f'{len(zero_h)} columns with height=0 '
                          f'(likely horizontal beams misclassified: {ids})',
            })
        else:
            results.append({
                'check': 'Geometry: column heights',
                'status': 'PASS',
                'detail': f'All {len(columns)} columns have positive height',
            })

    # 5. Node grid coverage
    if nodes is not None and not nodes.empty and 'grid' in nodes.columns:
        on_grid = nodes[nodes['grid'] != 'OFF_GRID']
        off_grid = nodes[nodes['grid'] == 'OFF_GRID']
        pct = len(on_grid) / len(nodes) * 100
        status = 'PASS' if pct > 50 else 'WARN'
        results.append({
            'check': 'Nodes: grid coverage',
            'status': status,
            'detail': f'{len(on_grid)}/{len(nodes)} on-grid ({pct:.1f}%)',
        })

    # 6. Reinforcement coverage — beams (base member_id matching)
    if beams is not None and reinf_beam is not None:
        if not beams.empty and not reinf_beam.empty:
            beam_base_ids = set(beams['member_id'].unique()) if 'member_id' in beams.columns else set()
            reinf_base_ids = set(
                _extract_base_member_id(mid)
                for mid in reinf_beam['member_id'].unique()
            ) if 'member_id' in reinf_beam.columns else set()
            missing = beam_base_ids - reinf_base_ids
            covered = beam_base_ids & reinf_base_ids
            if missing:
                results.append({
                    'check': 'Coverage: beam reinforcement',
                    'status': 'WARN' if len(missing) <= 5 else 'WARN',
                    'detail': f'{len(covered)}/{len(beam_base_ids)} beam types covered, '
                              f'{len(missing)} missing: '
                              f'{sorted(missing)[:10]}',
                })
            else:
                results.append({
                    'check': 'Coverage: beam reinforcement',
                    'status': 'PASS',
                    'detail': f'All {len(beam_base_ids)} beam types have reinforcement',
                })

    # 7. Reinforcement coverage — columns (base member_id matching)
    if columns is not None and reinf_column is not None:
        if not columns.empty and not reinf_column.empty:
            col_base_ids = set(columns['member_id'].unique()) if 'member_id' in columns.columns else set()
            reinf_base_ids = set(
                _extract_base_member_id(mid)
                for mid in reinf_column['member_id'].unique()
            ) if 'member_id' in reinf_column.columns else set()
            missing = col_base_ids - reinf_base_ids
            covered = col_base_ids & reinf_base_ids
            if missing:
                results.append({
                    'check': 'Coverage: column reinforcement',
                    'status': 'WARN',
                    'detail': f'{len(covered)}/{len(col_base_ids)} column types covered, '
                              f'{len(missing)} missing: {sorted(missing)}',
                })
            else:
                results.append({
                    'check': 'Coverage: column reinforcement',
                    'status': 'PASS',
                    'detail': f'All {len(col_base_ids)} column types have reinforcement',
                })

    # 8. Design key coverage — every element's design_key should exist in design results
    design_beam = outputs.get('design_beam')
    design_column = outputs.get('design_column')

    if beams is not None and design_beam is not None:
        if not beams.empty and not design_beam.empty and 'design_key' in beams.columns:
            beam_keys = set(beams['design_key'].dropna().unique())
            design_keys = set(design_beam['member_id'].unique()) if 'member_id' in design_beam.columns else set()
            missing_keys = beam_keys - design_keys
            # Filter out empty/synthetic keys
            missing_keys = {k for k in missing_keys if k and not k.startswith('ELEM_') and not k.startswith('LINK')}
            covered_keys = beam_keys - missing_keys
            if missing_keys:
                n_elements = len(beams[beams['design_key'].isin(missing_keys)])
                results.append({
                    'check': 'Design key: beam elements without design results',
                    'status': 'WARN',
                    'detail': f'{n_elements} beam elements ({len(missing_keys)} design keys) '
                              f'have no design results: {sorted(missing_keys)[:10]}',
                })
            else:
                results.append({
                    'check': 'Design key: beam design results coverage',
                    'status': 'PASS',
                    'detail': f'All {len(covered_keys)} beam design keys have design results',
                })

    if columns is not None and design_column is not None:
        if not columns.empty and not design_column.empty and 'design_key' in columns.columns:
            col_keys = set(columns['design_key'].dropna().unique())
            design_keys = set(design_column['member_id'].unique()) if 'member_id' in design_column.columns else set()
            missing_keys = col_keys - design_keys
            missing_keys = {k for k in missing_keys if k and not k.startswith('UNK')}
            covered_keys = col_keys - missing_keys
            if missing_keys:
                n_elements = len(columns[columns['design_key'].isin(missing_keys)])
                results.append({
                    'check': 'Design key: column elements without design results',
                    'status': 'WARN',
                    'detail': f'{n_elements} column elements ({len(missing_keys)} design keys) '
                              f'have no design results: {sorted(missing_keys)}',
                })
            else:
                results.append({
                    'check': 'Design key: column design results coverage',
                    'status': 'PASS',
                    'detail': f'All {len(covered_keys)} column design keys have design results',
                })

    # 8c. Design key coverage — walls (wall_mark matching)
    design_wall = outputs.get('design_wall')
    reinf_wall = outputs.get('reinf_wall')

    if walls is not None and (design_wall is not None or reinf_wall is not None):
        if not walls.empty and 'wall_mark' in walls.columns:
            wall_marks_in_elements = set(walls['wall_mark'].dropna().unique())
            # Get wall marks from design results or reinforcement
            design_marks = set()
            if design_wall is not None and not design_wall.empty and 'wall_mark' in design_wall.columns:
                design_marks = set(design_wall['wall_mark'].unique())
            elif reinf_wall is not None and not reinf_wall.empty and 'wall_mark' in reinf_wall.columns:
                design_marks = set(reinf_wall['wall_mark'].unique())

            if design_marks:
                missing_marks = wall_marks_in_elements - design_marks
                covered_marks = wall_marks_in_elements & design_marks
                if missing_marks:
                    n_elements = len(walls[walls['wall_mark'].isin(missing_marks)])
                    results.append({
                        'check': 'Design key: wall elements without design results',
                        'status': 'WARN',
                        'detail': f'{n_elements} wall elements ({len(missing_marks)} wall marks) '
                                  f'have no design results: {sorted(missing_marks)[:10]}',
                    })
                else:
                    results.append({
                        'check': 'Design key: wall design results coverage',
                        'status': 'PASS',
                        'detail': f'All {len(covered_marks)} wall marks have design results',
                    })

    # 9. Section type distribution
    if sections is not None and not sections.empty and 'member_type' in sections.columns:
        types = sections['member_type'].value_counts().to_dict()
        unknown = types.get('UNKNOWN', 0)
        if unknown > 0:
            results.append({
                'check': 'Sections: unknown types',
                'status': 'WARN',
                'detail': f'{unknown} sections with UNKNOWN member_type',
            })
        else:
            results.append({
                'check': 'Sections: type classification',
                'status': 'PASS',
                'detail': f'All sections classified: {types}',
            })

    return results


def format_report(results: list) -> str:
    """Format validation results as a text report."""
    lines = ['=' * 60, 'AISIMS Converter - Validation Report', '=' * 60, '']

    pass_count = sum(1 for r in results if r['status'] == 'PASS')
    warn_count = sum(1 for r in results if r['status'] == 'WARN')
    fail_count = sum(1 for r in results if r['status'] == 'FAIL')

    lines.append(f'Summary: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL')
    lines.append('')

    for r in results:
        icon = {'PASS': '[OK]', 'WARN': '[!!]', 'FAIL': '[XX]'}[r['status']]
        lines.append(f"  {icon} {r['check']}: {r['detail']}")

    lines.append('')
    lines.append('=' * 60)

    return '\n'.join(lines)
