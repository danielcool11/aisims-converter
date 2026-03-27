"""
Stock length split utility — shared across all Tier 2 calculators.

Splits bars exceeding MAX_STOCK_LENGTH_MM (12m) into multiple pieces
with lap splices at each joint.

For a bar of length L with lap splice Llap:
  n_pieces = ceil(L / MAX_STOCK_LENGTH_MM)
  piece_length = (L + (n_pieces - 1) * Llap) / n_pieces
  Total material = L + (n_pieces - 1) * Llap
"""

import math

MAX_STOCK_LENGTH_MM = 12000


def split_bar(bar_dict, L_lap):
    """
    Split a bar record if it exceeds 12m stock length.

    Args:
        bar_dict: dict with at least 'length_mm' key
        L_lap: lap splice length for this bar size

    Returns:
        list of bar dicts (1 if no split, n if split)
    """
    length = bar_dict.get('length_mm', 0)
    if length <= MAX_STOCK_LENGTH_MM:
        bar_dict['split_piece'] = None
        bar_dict['split_total'] = None
        bar_dict['original_length_mm'] = None
        return [bar_dict]

    # Number of pieces needed
    n_pieces = math.ceil(length / MAX_STOCK_LENGTH_MM)
    # Each piece length: share the total (original + laps) equally
    total_material = length + (n_pieces - 1) * L_lap
    piece_len = int(round(total_material / n_pieces))

    # Get start/end coordinates for interpolation
    xs = bar_dict.get('mesh_origin_x_mm') or bar_dict.get('start_x', 0) or 0
    ys = bar_dict.get('mesh_origin_y_mm') or bar_dict.get('start_y', 0) or 0
    zs = bar_dict.get('mesh_origin_z_mm') or bar_dict.get('start_z', 0) or 0
    xe = bar_dict.get('mesh_terminus_x_mm') or bar_dict.get('end_x', 0) or 0
    ye = bar_dict.get('mesh_terminus_y_mm') or bar_dict.get('end_y', 0) or 0
    ze = bar_dict.get('mesh_terminus_z_mm') or bar_dict.get('end_z', 0) or 0

    pieces = []
    for i in range(n_pieces):
        t_start = i / n_pieces
        t_end = (i + 1) / n_pieces

        # Interpolate coordinates
        px_s = xs + (xe - xs) * t_start
        py_s = ys + (ye - ys) * t_start
        pz_s = zs + (ze - zs) * t_start
        px_e = xs + (xe - xs) * t_end
        py_e = ys + (ye - ys) * t_end
        pz_e = zs + (ze - zs) * t_end

        piece = {**bar_dict}
        piece['length_mm'] = piece_len
        piece['split_piece'] = i + 1
        piece['split_total'] = n_pieces
        piece['original_length_mm'] = length

        # Update coordinates based on which keys exist
        if 'mesh_origin_x_mm' in bar_dict:
            piece['mesh_origin_x_mm'] = round(px_s, 1)
            piece['mesh_origin_y_mm'] = round(py_s, 1)
            piece['mesh_origin_z_mm'] = round(pz_s, 1)
            piece['mesh_terminus_x_mm'] = round(px_e, 1)
            piece['mesh_terminus_y_mm'] = round(py_e, 1)
            piece['mesh_terminus_z_mm'] = round(pz_e, 1)
        if 'start_x' in bar_dict:
            piece['start_x'] = round(px_s, 1)
            piece['start_y'] = round(py_s, 1)
            piece['start_z'] = round(pz_s, 1)
            piece['end_x'] = round(px_e, 1)
            piece['end_y'] = round(py_e, 1)
            piece['end_z'] = round(pz_e, 1)

        # Update total_length_mm if present
        if 'total_length_mm' in piece and 'n_bars' in piece:
            piece['total_length_mm'] = int(round(piece_len * piece.get('n_bars', 1)))

        pieces.append(piece)

    return pieces
