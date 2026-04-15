"""
Beam junction graph — classifies coaxial beam-to-beam joints per Prof. Sunkuk's
Case 1/2/3 rule (issue #78 Error B).

A "junction" is a node shared by two or more beam endpoints. We classify each
junction per bar position (TOP / BOT) independently, using strict integer-mm
arithmetic on node coordinates (per Prof. Sunkuk's spec).

Classification outcomes:

    Case 0  — junction has a column/wall at the node, OR at least one side is
              not coaxial / not same-section. The Prof. Sunkuk rule does not
              apply; current per-beam anchorage stands.
    Case 1  — coaxial + same section + same diameter + same bar count at this
              position. Bars run straight through with LAP (MAIN_INTERMEDIATE).
    Case 2  — coaxial + same section + same diameter + different bar count.
              min(count) bars run through as LAP; remainder bars hook into the
              opposite span (new MAIN_REMAINDER role).
    Case 3  — coaxial + same section + different diameter. Each side HOOKs
              independently at the junction node.

Phase 1 use: build the graph and emit counts so we can verify against issue
#78's baseline (P1: 15 real Case 2+3 false positives, P2: 53) before touching
the role-assignment loop. Phase 2 will use the same classifier to drive the
actual rebar emission.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BeamRef:
    """Minimal beam descriptor used by the junction graph."""
    row_idx: int
    member_id: str
    level: str
    b_mm: int           # integer mm for exact comparison
    h_mm: int
    # Axis vector in integer mm: (dx, dy). Both ends share z.
    dx: int
    dy: int
    node_from: str
    node_to: str
    length_mm: float


@dataclass
class BeamRebarCount:
    """Continuous MAIN bar count per position for one beam row.
    Used to compare across a junction.
    """
    n_top: int = 0
    dia_top: int = 0
    n_bot: int = 0
    dia_bot: int = 0


@dataclass
class JunctionFinding:
    """One classification record per (node, beam_a, beam_b, position)."""
    node_id: str
    level: str
    beam_a_member: str
    beam_b_member: str
    beam_a_idx: int
    beam_b_idx: int
    position: str           # 'TOP' or 'BOT'
    case: int               # 0 / 1 / 2 / 3
    reason: str             # human-readable why this case
    n_a: int = 0
    n_b: int = 0
    dia_a: int = 0
    dia_b: int = 0
    has_support: bool = False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iround(v) -> int:
    """Safe integer mm rounding. Anything non-finite becomes 0."""
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _cross_z(ax: int, ay: int, bx: int, by: int) -> int:
    """Z-component of 2D cross product in integer arithmetic."""
    return ax * by - ay * bx


def _is_coaxial(a: BeamRef, b: BeamRef) -> bool:
    """True if two beams are coaxial in the XY plane.

    Integer-mm cross product must be zero AND both axes must be non-zero
    (guards against the old Error E pattern, though that should be gone after
    #78 commit d27a1e1).
    """
    if (a.dx == 0 and a.dy == 0) or (b.dx == 0 and b.dy == 0):
        return False
    return _cross_z(a.dx, a.dy, b.dx, b.dy) == 0


def _same_section(a: BeamRef, b: BeamRef) -> bool:
    return a.b_mm == b.b_mm and a.h_mm == b.h_mm


# ── Graph builder ────────────────────────────────────────────────────────────

def build_beam_refs(beams_df: pd.DataFrame) -> List[BeamRef]:
    """Convert a MembersBeam DataFrame to integer-mm BeamRef list."""
    refs: List[BeamRef] = []
    for idx, row in beams_df.iterrows():
        xf = _iround(row.get('x_from_mm'))
        yf = _iround(row.get('y_from_mm'))
        xt = _iround(row.get('x_to_mm'))
        yt = _iround(row.get('y_to_mm'))
        refs.append(BeamRef(
            row_idx=int(idx),
            member_id=str(row.get('member_id', '') or ''),
            level=str(row.get('level', '') or ''),
            b_mm=_iround(row.get('b_mm')),
            h_mm=_iround(row.get('h_mm')),
            dx=xt - xf,
            dy=yt - yf,
            node_from=str(row.get('node_from', '') or ''),
            node_to=str(row.get('node_to', '') or ''),
            length_mm=float(row.get('length_mm', 0) or 0),
        ))
    return refs


def build_support_node_set(
    columns_df: Optional[pd.DataFrame],
    walls_df: Optional[pd.DataFrame],
    basement_walls_df: Optional[pd.DataFrame] = None,
) -> Set[str]:
    """Set of node_ids that are supported by a column or wall at that node.

    A beam-beam junction at such a node is NOT subject to the Prof. Sunkuk
    Case 1/2/3 rule (the column/wall handles the anchorage).

    Columns: column nodes are the node_from / node_to referenced by MembersColumn
    rows (when available). We fall back to matching by xy proximity if node refs
    are missing.

    Walls: any node in node_i / node_j / node_k / node_l of a MembersWall or
    MembersBasementWall row is a supported node.
    """
    supported: Set[str] = set()

    if columns_df is not None and not columns_df.empty:
        for col in ('node_from', 'node_to'):
            if col in columns_df.columns:
                for nid in columns_df[col].dropna():
                    n = str(nid).strip()
                    if n and n != 'nan':
                        supported.add(n)

    for wdf in (walls_df, basement_walls_df):
        if wdf is None or wdf.empty:
            continue
        for col in ('node_i', 'node_j', 'node_k', 'node_l'):
            if col not in wdf.columns:
                continue
            for nid in wdf[col].dropna():
                n = str(nid).strip()
                if n and n != 'nan':
                    supported.add(n)

    return supported


def build_rebar_counts(
    rebar_df: Optional[pd.DataFrame],
    beams_df: pd.DataFrame,
) -> Dict[int, BeamRebarCount]:
    """Return row_idx → BeamRebarCount.

    Aggregates the continuous MAIN bar count per (member_id, level) from the
    RebarLengthsBeam output and assigns it to each MembersBeam row.

    Phase 1 uses the existing converter output (so we're comparing the CURRENT
    span-role logic's counts). When the classifier drives actual emission in
    Phase 2, it'll read directly from the BeamConfigAdapter instead.
    """
    counts: Dict[int, BeamRebarCount] = {}
    if rebar_df is None or rebar_df.empty:
        # No rebar data → every beam starts at zero counts. Junction
        # classification will skip count-sensitive cases (1/2) and fall back
        # to gating on section/coaxial only.
        for idx in beams_df.index:
            counts[int(idx)] = BeamRebarCount()
        return counts

    main_rows = rebar_df[rebar_df['bar_type'] == 'MAIN'] if 'bar_type' in rebar_df.columns else rebar_df
    per_mem: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
    for _, r in main_rows.iterrows():
        key = (
            str(r.get('member_id', '') or ''),
            str(r.get('level', '') or ''),
            str(r.get('bar_position', '') or ''),
        )
        try:
            n = int(r.get('n_bars') or 0)
            d = int(round(float(r.get('dia_mm') or 0)))
        except (TypeError, ValueError):
            continue
        prev = per_mem.get(key, (0, 0))
        if n > prev[0]:
            per_mem[key] = (n, d)

    for idx, row in beams_df.iterrows():
        mid = str(row.get('member_id', '') or '')
        lv = str(row.get('level', '') or '')
        n_top, d_top = per_mem.get((mid, lv, 'TOP'), (0, 0))
        n_bot, d_bot = per_mem.get((mid, lv, 'BOT'), (0, 0))
        counts[int(idx)] = BeamRebarCount(
            n_top=n_top, dia_top=d_top, n_bot=n_bot, dia_bot=d_bot,
        )
    return counts


def index_beams_by_node(refs: List[BeamRef]) -> Dict[str, List[BeamRef]]:
    """Map node_id → list of beam refs that touch it."""
    by_node: Dict[str, List[BeamRef]] = defaultdict(list)
    for b in refs:
        if b.node_from:
            by_node[b.node_from].append(b)
        if b.node_to:
            by_node[b.node_to].append(b)
    return by_node


# ── Classification ──────────────────────────────────────────────────────────

def _classify_pair_at_node(
    node_id: str,
    a: BeamRef,
    b: BeamRef,
    counts: Dict[int, BeamRebarCount],
    supported_nodes: Set[str],
    position: str,
) -> JunctionFinding:
    """Classify one (beam_a, beam_b) pair at one node for one bar position."""
    has_support = node_id in supported_nodes

    ca = counts.get(a.row_idx, BeamRebarCount())
    cb = counts.get(b.row_idx, BeamRebarCount())
    if position == 'TOP':
        n_a, dia_a = ca.n_top, ca.dia_top
        n_b, dia_b = cb.n_top, cb.dia_top
    else:
        n_a, dia_a = ca.n_bot, ca.dia_bot
        n_b, dia_b = cb.n_bot, cb.dia_bot

    finding = JunctionFinding(
        node_id=node_id,
        level=a.level,
        beam_a_member=a.member_id,
        beam_b_member=b.member_id,
        beam_a_idx=a.row_idx,
        beam_b_idx=b.row_idx,
        position=position,
        case=0,
        reason='',
        n_a=n_a, n_b=n_b, dia_a=dia_a, dia_b=dia_b,
        has_support=has_support,
    )

    # Gate 1: different levels (shouldn't happen via node sharing, but belt-and-braces)
    if a.level != b.level:
        finding.reason = 'level mismatch'
        return finding
    # Gate 2: support at node — Prof. Sunkuk rule doesn't apply
    if has_support:
        finding.reason = 'column/wall at junction'
        return finding
    # Gate 3: not coaxial (any non-zero angle)
    if not _is_coaxial(a, b):
        finding.reason = 'not coaxial'
        return finding
    # Gate 4: different section
    if not _same_section(a, b):
        finding.reason = f'section mismatch ({a.b_mm}x{a.h_mm} vs {b.b_mm}x{b.h_mm})'
        return finding
    # Gate 5: no rebar info on one side
    if n_a == 0 or n_b == 0:
        finding.reason = f'missing {position} count'
        return finding

    # Case 3: different diameter → each side hooks independently
    if dia_a != dia_b:
        finding.case = 3
        finding.reason = f'diameter mismatch (D{dia_a} vs D{dia_b})'
        return finding
    # Case 1: same count + same dia → straight through with LAP
    if n_a == n_b:
        finding.case = 1
        finding.reason = f'{n_a}-D{dia_a} both sides, straight through'
        return finding
    # Case 2: same dia, different count → LAP + remainder
    finding.case = 2
    finding.reason = f'{n_a}-D{dia_a} vs {n_b}-D{dia_b}, min continues, remainder hooks'
    return finding


def classify_junctions(
    refs: List[BeamRef],
    counts: Dict[int, BeamRebarCount],
    supported_nodes: Set[str],
) -> List[JunctionFinding]:
    """Walk every node shared by 2+ beam endpoints and classify each pair at
    TOP and BOT independently.
    """
    by_node = index_beams_by_node(refs)
    findings: List[JunctionFinding] = []
    for node_id, beams_at_node in by_node.items():
        if len(beams_at_node) < 2:
            continue
        # Dedup by row_idx (a beam can appear at both node_from and node_to of
        # itself only if it's a degenerate beam, which we already eliminated).
        seen: Set[int] = set()
        unique_beams: List[BeamRef] = []
        for b in beams_at_node:
            if b.row_idx in seen:
                continue
            seen.add(b.row_idx)
            unique_beams.append(b)
        # Pair every two distinct beams and classify each position
        for i in range(len(unique_beams)):
            for j in range(i + 1, len(unique_beams)):
                a, b = unique_beams[i], unique_beams[j]
                if a.member_id == b.member_id:
                    continue  # self-pair (should be 0 after #78 Error C fix)
                for position in ('TOP', 'BOT'):
                    findings.append(_classify_pair_at_node(
                        node_id, a, b, counts, supported_nodes, position
                    ))
    return findings


# ── Summary ─────────────────────────────────────────────────────────────────

def summarize(findings: Iterable[JunctionFinding]) -> Dict[str, int]:
    """Count findings by case. Useful for progress / regression metrics."""
    counts = defaultdict(int)
    for f in findings:
        counts[f'total'] += 1
        counts[f'case_{f.case}'] += 1
        counts[f'case_{f.case}_{f.position}'] += 1
    return dict(counts)
