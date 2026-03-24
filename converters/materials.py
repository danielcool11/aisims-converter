"""
Materials converter — transforms raw MIDAS materials + MGT rebar grades
into standardized Materials.csv.

Input:  Materials.csv (ID, Name, Type, DB, Elasticity, Poisson, Density, ...)
        MGT parsed data (rebar_grades from *DGNCRITERIA)
Output: Materials.csv (material_id, type, grade, fck_MPa, fy_MPa, E_MPa, density_kN_m3)
"""

import pandas as pd
import re


def convert_materials(
    materials_df: pd.DataFrame,
    mgt_data: dict = None,
) -> pd.DataFrame:
    """
    Convert raw MIDAS materials to standardized format.

    Args:
        materials_df: DataFrame from Materials.csv
        mgt_data: parsed MGT data (from parsers.mgt.parse_mgt)

    Returns:
        DataFrame with columns [material_id, type, grade, fck_MPa, fy_MPa, E_MPa, density_kN_m3]
    """

    results = []

    # ── 1. Concrete materials from Materials sheet ──
    seen_grades = set()

    for _, row in materials_df.iterrows():
        mat_type = str(row.get('Type', '')).strip()
        if mat_type.lower() != 'concrete':
            continue

        grade = str(row.get('DB', '')).strip()
        if not grade or grade in seen_grades:
            continue
        seen_grades.add(grade)

        # Extract fck from grade string: "C35" → 35
        fck_match = re.search(r'C(\d+)', grade)
        fck = int(fck_match.group(1)) if fck_match else None

        # Elasticity: N/mm2 = MPa (direct, no conversion needed)
        e_col = None
        for col in materials_df.columns:
            if 'elastic' in col.lower():
                e_col = col
                break
        E_mpa = float(row[e_col]) if e_col and pd.notna(row[e_col]) else None

        # Density: N/mm3 → kN/m3
        # N/mm3 × 1e9 mm3/m3 = N/m3, then ÷ 1000 = kN/m3
        density_col = None
        for col in materials_df.columns:
            cl = col.lower()
            if 'density' in cl and 'mass' not in cl:
                density_col = col
                break
        if density_col and pd.notna(row[density_col]):
            density_raw = float(row[density_col])
            if density_raw < 1:  # N/mm3 format (e.g., 2.35E-05)
                density_kn_m3 = density_raw * 1e9 / 1000
            else:  # already in kN/m3
                density_kn_m3 = density_raw
        else:
            density_kn_m3 = 25.0  # default concrete

        results.append({
            'material_id': grade,
            'type': 'concrete',
            'grade': grade,
            'fck_MPa': fck,
            'fy_MPa': None,
            'E_MPa': E_mpa,
            'density_kN_m3': round(density_kn_m3, 1),
        })

    # ── 2. Rebar materials from MGT ──
    if mgt_data and 'rebar_grades' in mgt_data:
        for fy_mpa, diameters in sorted(mgt_data['rebar_grades'].items()):
            grade = f'SD{fy_mpa}'
            results.append({
                'material_id': grade,
                'type': 'rebar',
                'grade': grade,
                'fck_MPa': None,
                'fy_MPa': fy_mpa,
                'E_MPa': 200000,
                'density_kN_m3': 78.5,
            })

    # ── 3. Fallback: if no MGT, derive from design data ──
    if not mgt_data or not mgt_data.get('rebar_grades'):
        # Add default SD400 if no rebar info at all
        if not any(r['type'] == 'rebar' for r in results):
            results.append({
                'material_id': 'SD400',
                'type': 'rebar',
                'grade': 'SD400',
                'fck_MPa': None,
                'fy_MPa': 400,
                'E_MPa': 200000,
                'density_kN_m3': 78.5,
            })

    result_df = pd.DataFrame(results)

    # Log summary
    concrete = [r for r in results if r['type'] == 'concrete']
    rebar = [r for r in results if r['type'] == 'rebar']
    print(f'[Materials] {len(results)} materials: '
          f'{len(concrete)} concrete ({", ".join(r["grade"] for r in concrete)}), '
          f'{len(rebar)} rebar ({", ".join(r["grade"] for r in rebar)})')

    return result_df
