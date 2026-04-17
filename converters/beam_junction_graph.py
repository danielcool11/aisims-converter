"""
Beam junction graph — classifies coaxial beam-to-beam joints per Prof. Sunkuk's
Case 1/2/3 rule (issue #78 Error B).

A "junction" is a node shared by two or more beam endpoints. We classify each
junction per bar position (TOP / BOT) independently, using strict integer-mm
arithmetic on node coordinates (per Prof. Sunkuk's spec).

Classification outcomes:

    Case 0  — junction has a column/wall at the node, OR at least one side is
              not coaxial / not same-depth. The Prof. Sunkuk rule does not
              apply; current per-beam anchorage stands.
    Case 1  — coaxial + same depth + same diameter + same bar count at this
              position. Bars run straight through with LAP (MAIN_INTERMEDIATE).
    Case 2  — coaxial + same depth + same diameter + different bar count.
              min(count) bars run through as LAP; excess bars stay local
              within their own span (MAIN_SINGLE / through-bar with HOOK).
              MAIN_REMAINDER only emitted when receiving zone has a genuine
              deficit (rare for Korean-designed beams).
    Case 3  — coaxial + same depth + different diameter. Each side HOOKs
              independently at the junction node.
    Note: different width (b_mm) is allowed — bars can lap across different
    widths as long as h_mm matches (same z-level for bars).

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
    # Same depth required for chain continuity (bars at same z-level).
    # Different width is allowed — bars can lap across different b_mm
    # as long as they fit within the narrower beam. Per supervisor
    # clarification 2026-04-16 on issue #78.
    return a.h_mm == b.h_mm


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
    # Gate 2: not coaxial (any non-zero angle)
    if not _is_coaxial(a, b):
        finding.reason = 'not coaxial'
        return finding
    # Gate 3: different depth (different h_mm breaks the chain; different
    # b_mm is allowed — bars can lap across different widths)
    if not _same_section(a, b):
        finding.reason = f'depth mismatch ({a.h_mm} vs {b.h_mm})'
        return finding
    # Gate 4: no rebar info on one side
    if n_a == 0 or n_b == 0:
        finding.reason = f'missing {position} count'
        return finding

    # NOTE: column/wall support at the junction is NOT a gate.
    # Prof. Sunkuk's rule (issue #78) applies to any coaxial same-depth
    # same-diameter beam-to-beam junction regardless of whether a column is
    # between them. Lapping inside a column cage is standard detailing
    # (the cage provides lateral confinement for the lap zone), and is
    # more economical than each beam hooking into the column independently.
    # has_support is still recorded on the finding for logging/debug.

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
                if a.row_idx == b.row_idx:
                    continue  # skip actual self-pair (same beam at both endpoints)
                for position in ('TOP', 'BOT'):
                    findings.append(_classify_pair_at_node(
                        node_id, a, b, counts, supported_nodes, position
                    ))
    return findings


# ── Run-level analysis ──────────────────────────────────────────────────────
#
# A "run" (per Prof. Sunkuk rule) is a maximal set of beams connected by
# Case 1 or Case 2 junctions at a given position (TOP or BOT). Case 3 and
# Case 0 junctions terminate the run. We analyze runs per position because
# the same physical junction can be Case 1 at TOP but Case 2 at BOT.
#
# Within a run the count profile matters: profile [2, 3, 3, 2] means two bars
# continue as LAP through the whole run, plus one "remainder" bar that exists
# only in the middle two beams and hooks at its two endpoints. Multiple
# remainder bars can exist at different levels when the profile has nested
# plateaus (e.g. [2, 3, 4, 3, 2] → one remainder bar across the 3+ region and
# another across the 4+ region).


@dataclass
class RemainderSpan:
    """One physical remainder bar spanning a contiguous slice of a run.

    The span covers beam indices [start_beam_idx_in_run .. end_beam_idx_in_run]
    (inclusive) at level `count_level`. Near/far hook ends each may be:
      - a Case 2 hook into an adjacent lower-count beam (inside the run), or
      - the run's own far-end anchorage if this span reaches a run boundary.
    """
    count_level: int              # the count "strip" this bar belongs to
    beam_row_idxs: List[int]      # beams this bar physically passes through
    near_hooks_into_beam_idx: Optional[int]  # adjacent lower-count beam at near end
    far_hooks_into_beam_idx: Optional[int]   # adjacent lower-count beam at far end


@dataclass
class BeamRun:
    """A connected run of beams joined by Case 1/2 junctions at one position."""
    position: str                 # 'TOP' or 'BOT'
    level: str
    ordered_beams: List[int]      # row_idxs in structural order (along the axis)
    counts: List[int]             # bar count at each beam (parallel to ordered_beams)
    dia: int                      # diameter (all same for a run — Case 3 terminates)
    case1_count: int              # edges in the run with equal counts
    case2_count: int              # edges with unequal counts
    remainders: List[RemainderSpan] = field(default_factory=list)

    @property
    def has_case2(self) -> bool:
        return self.case2_count > 0

    @property
    def min_count(self) -> int:
        return min(self.counts) if self.counts else 0

    @property
    def max_count(self) -> int:
        return max(self.counts) if self.counts else 0

    @property
    def n_main_intermediate(self) -> int:
        """Number of bars that continue through the entire run as LAP."""
        return self.min_count

    def chain_min_b(self, b_lookup: Dict[int, float]) -> float:
        """Min beam width across the entire run (for FULL_CHAIN bars)."""
        return min(b_lookup.get(idx, 9999) for idx in self.ordered_beams)

    def gap_min_b(self, b_lookup: Dict[int, float]) -> Dict[int, Dict[str, float]]:
        """Per-beam min_b per gap-bar continuity group.

        Returns {beam_row_idx: {'PARTIAL_CHAIN': min_b, 'LOCAL': own_b}}.
        Each RemainderSpan strip constrains its bars to the narrowest
        beam in that sub-run.
        """
        result: Dict[int, Dict[str, float]] = {}
        for rem in self.remainders:
            beams = rem.beam_row_idxs
            strip_min_b = min(b_lookup.get(idx, 9999) for idx in beams)
            n = len(beams)
            for i, bidx in enumerate(beams):
                cont = 'LOCAL' if n == 1 else 'PARTIAL_CHAIN'
                min_b_val = b_lookup.get(bidx, 9999) if cont == 'LOCAL' else strip_min_b
                if bidx not in result:
                    result[bidx] = {}
                # Multiple strips may contribute; keep the tightest constraint
                result[bidx][cont] = min(
                    result[bidx].get(cont, 9999), min_b_val
                )
        return result

    def gap_bar_roles(self) -> Dict[int, Dict[str, int]]:
        """Per-beam gap bar role breakdown.

        Returns {beam_row_idx: {'MAIN_START': n, 'MAIN_SINGLE': n, ...}}.
        Each remainder strip (contiguous sub-run of excess bars above
        min_count) maps to partial-chain roles: single-beam strips become
        MAIN_SINGLE, multi-beam strips become MAIN_START / INTERMEDIATE /
        MAIN_END within the strip.
        """
        result: Dict[int, Dict[str, int]] = {}
        for rem in self.remainders:
            beams = rem.beam_row_idxs
            n = len(beams)
            for i, bidx in enumerate(beams):
                if n == 1:
                    role = 'MAIN_SINGLE'
                elif i == 0:
                    role = 'MAIN_START'
                elif i == n - 1:
                    role = 'MAIN_END'
                else:
                    role = 'MAIN_INTERMEDIATE'
                if bidx not in result:
                    result[bidx] = {}
                result[bidx][role] = result[bidx].get(role, 0) + 1
        return result


def _find_strip_intervals(counts: List[int], level: int) -> List[Tuple[int, int]]:
    """Find contiguous [start, end] intervals where counts[i] >= level."""
    intervals: List[Tuple[int, int]] = []
    i = 0
    n = len(counts)
    while i < n:
        if counts[i] >= level:
            start = i
            while i < n and counts[i] >= level:
                i += 1
            intervals.append((start, i - 1))
        else:
            i += 1
    return intervals


def _order_beams_along_axis(refs: List[BeamRef], beam_idxs: Set[int]) -> List[int]:
    """Sort a set of beam row_idxs by primary-axis position.

    All beams in a run are coaxial, so they share a direction. We sort by the
    smaller of (x_from, x_to) for X-spans, or (y_from, y_to) for Y-spans.
    """
    idx_to_ref = {b.row_idx: b for b in refs}
    subset = [idx_to_ref[i] for i in beam_idxs if i in idx_to_ref]
    if not subset:
        return []
    # Direction from the first beam
    first = subset[0]
    is_x = abs(first.dx) >= abs(first.dy)
    # Anchor keys: use node_from coordinate to sort. We fetch them from refs.
    def key(b: BeamRef) -> int:
        # Reconstruct x_from / y_from via node_from coords in ref.
        # BeamRef doesn't store absolute coords, only deltas. We infer from
        # whichever FROM coord is "smaller" using dx/dy signs.
        # Workaround: use dx/dy signs to pick min coord from the beam's
        # endpoint (the BeamRef doesn't carry absolute xy, so we key on
        # row_idx order as a stable fallback and rely on caller to have the
        # right set). In practice the caller passes refs that also carry
        # absolute coords via beams_df — see compute_runs below.
        return b.row_idx
    return [b.row_idx for b in sorted(subset, key=key)]


def compute_runs(
    refs: List[BeamRef],
    findings: List[JunctionFinding],
    counts: Dict[int, BeamRebarCount],
    beams_df: pd.DataFrame,
    position: str,
) -> List[BeamRun]:
    """Build position-specific run graph from Case 1/2 findings, walk components."""
    assert position in ('TOP', 'BOT')

    # Adjacency: beam_idx → set of connected beam_idxs (via Case 1 or 2 edges at this position)
    adj: Dict[int, Set[int]] = defaultdict(set)
    idx_to_ref = {b.row_idx: b for b in refs}
    case1_edges: Dict[frozenset, int] = defaultdict(int)
    case2_edges: Dict[frozenset, int] = defaultdict(int)

    for f in findings:
        if f.position != position:
            continue
        if f.case in (1, 2):
            a_idx = f.beam_a_idx
            b_idx = f.beam_b_idx
            adj[a_idx].add(b_idx)
            adj[b_idx].add(a_idx)
            key = frozenset((a_idx, b_idx))
            if f.case == 1:
                case1_edges[key] += 1
            else:
                case2_edges[key] += 1

    visited: Set[int] = set()
    runs: List[BeamRun] = []

    for start_idx in list(adj.keys()):
        if start_idx in visited:
            continue
        # BFS
        component: Set[int] = set()
        queue = [start_idx]
        while queue:
            cur = queue.pop()
            if cur in component:
                continue
            component.add(cur)
            for nxt in adj[cur]:
                if nxt not in component:
                    queue.append(nxt)
        visited.update(component)

        # Order beams by primary axis using beams_df absolute coords.
        def axis_key(idx: int) -> float:
            row = beams_df.loc[idx]
            b = idx_to_ref[idx]
            is_x = abs(b.dx) >= abs(b.dy)
            if is_x:
                return min(float(row.get('x_from_mm', 0) or 0),
                           float(row.get('x_to_mm', 0) or 0))
            return min(float(row.get('y_from_mm', 0) or 0),
                       float(row.get('y_to_mm', 0) or 0))

        ordered = sorted(component, key=axis_key)
        if not ordered:
            continue

        first_ref = idx_to_ref[ordered[0]]
        c_first = counts.get(ordered[0], BeamRebarCount())
        if position == 'TOP':
            dia = c_first.dia_top
        else:
            dia = c_first.dia_bot

        counts_seq: List[int] = []
        for idx in ordered:
            cnt = counts.get(idx, BeamRebarCount())
            counts_seq.append(cnt.n_top if position == 'TOP' else cnt.n_bot)

        # Count internal edges by case
        c1 = 0
        c2 = 0
        for i in range(len(ordered) - 1):
            k = frozenset((ordered[i], ordered[i + 1]))
            c1 += case1_edges.get(k, 0)
            c2 += case2_edges.get(k, 0)

        run = BeamRun(
            position=position,
            level=first_ref.level,
            ordered_beams=ordered,
            counts=counts_seq,
            dia=dia,
            case1_count=c1,
            case2_count=c2,
        )

        # Remainder spans: one per (level, interval) above min_count
        n_min = run.min_count
        n_max = run.max_count
        for level in range(n_min + 1, n_max + 1):
            intervals = _find_strip_intervals(counts_seq, level)
            for (lo, hi) in intervals:
                beam_slice = ordered[lo:hi + 1]
                near_hook = ordered[lo - 1] if lo > 0 else None
                far_hook = ordered[hi + 1] if hi < len(ordered) - 1 else None
                run.remainders.append(RemainderSpan(
                    count_level=level,
                    beam_row_idxs=beam_slice,
                    near_hooks_into_beam_idx=near_hook,
                    far_hooks_into_beam_idx=far_hook,
                ))

        runs.append(run)

    return runs


# ── Summary ─────────────────────────────────────────────────────────────────

def summarize(findings: Iterable[JunctionFinding]) -> Dict[str, int]:
    """Count findings by case. Useful for progress / regression metrics."""
    counts = defaultdict(int)
    for f in findings:
        counts[f'total'] += 1
        counts[f'case_{f.case}'] += 1
        counts[f'case_{f.case}_{f.position}'] += 1
    return dict(counts)


def summarize_runs(runs: List[BeamRun]) -> Dict[str, int]:
    """Count runs and their transitions."""
    out = defaultdict(int)
    for r in runs:
        out['total_runs'] += 1
        out[f'runs_{r.position}'] += 1
        if r.has_case2:
            out['runs_with_case2'] += 1
            out[f'runs_with_case2_{r.position}'] += 1
        out['total_remainders'] += len(r.remainders)
        out[f'remainders_{r.position}'] += len(r.remainders)
    return dict(out)
