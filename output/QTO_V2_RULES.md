# QTO V2 Rules — Upgrade from V1 / QTO V2 규칙 — V1에서 업그레이드

> **Date / 일자**: 2026-04-06
> **Author / 작성자**: Daniel
> **Status / 상태**: DRAFT — 3명 합의 대기 (Awaiting consensus)
> **Base**: V1 `backend/services/qto/` (AISIMS-origin)

---

## 1. Overview / 개요

V2 QTO upgrades V1 by using **junction geometry data** from Daniel's converter
(`extend_start_mm`, `extend_end_mm`, polygon vertices) to produce more accurate
concrete volumes and formwork areas at member junctions.

V2 QTO는 V1을 기반으로, Daniel의 converter에서 제공하는 접합부 기하 데이터를 활용하여
부재 접합부의 콘크리트 체적 및 거푸집 면적을 더 정확하게 산출합니다.

**Priority (unchanged from V1)**: Column (4) > Wall (3) > Slab (2) > Beam (1)

---

## 2. Concrete Rules / 콘크리트 규칙

### 2.1 Column — No Change from V1 / 변경 없음

```
V1: V = b × h × L (full storey height, no deductions)
V2: Same — Column owns the junction volume.
```

Column has highest priority → no other member deducts from it.
기둥은 최고 우선순위 → 다른 부재가 기둥 체적에서 공제하지 않음.

### 2.2 Beam — V2 Upgrade: Junction Length Deduction / 접합부 길이 공제

```
V1: V = b × downstand × L_span
    (L_span = full span between node centers, no column deduction)

V2: V = b × downstand × L_clear
    L_clear = L_span - extend_start - extend_end
    (extend_start/end = half column/wall width at each end)
```

**Why**: The beam concrete inside the column zone is already counted as column concrete.
Beam should only measure the **clear span** between column faces.

**이유**: 기둥 구간 내의 보 콘크리트는 이미 기둥 체적으로 계산됨.
보는 기둥 면 사이의 순경간(clear span)만 측정해야 함.

**Real example (P1)**:
```
B17: L_span=5400mm, ext_start=0, ext_end=250mm (half of 500mm column)
  V1: V = 0.4 × downstand × 5.4m
  V2: V = 0.4 × downstand × 5.15m  (5400 - 0 - 250 = 5150mm)
  Difference: -4.6%
```

**Data source**: `MembersBeam.extend_start_mm`, `MembersBeam.extend_end_mm`

### 2.3 Normal Wall — V2 Upgrade: Junction Length Deduction / 접합부 길이 공제

```
V1: V = thickness × height × length (full panel length)
    Opening deductions if BOTH thresholds exceeded.

V2: V = thickness × height × L_clear
    L_clear = length - extend_start - extend_end
    (extend = half column/wall thickness at junction)
    Opening deductions same as V1.
```

**Why**: Where wall meets column at each end, the column owns that junction volume.

**Data source**: `MembersWall.extend_start_mm`, `MembersWall.extend_end_mm`

### 2.4 Basement Wall — V2 Upgrade: Column Overlap Deduction / 기둥 중첩 공제

Basement walls often have columns **embedded** inside them (outer faces aligned).
The column may be wider than the wall thickness → partial overlap.

지하벽에는 기둥이 **벽 내부에** 매입되는 경우가 많음 (외면 정렬).
기둥이 벽 두께보다 클 수 있음 → 부분 중첩.

```
Case: Column 800×800 inside basement wall (thickness=600)

Plan view:
          800 (column)
      ┌──────────┐
      │          │ ← 200mm protrudes
══════╪══════════╪════════════════
      │   OVERLAP│   600mm wall
══════╪══════════╪════════════════
      └──────────┘

Overlap area = min(col_b, wall_thickness) × col_h
             = 600 × 800 = 480,000 mm²
             (NOT 800 × 800 — column protrudes beyond wall)

V2 Concrete:
  V_wall = thickness × height × L_clear
         - Σ (overlap_area × wall_height)  per embedded column
  overlap_area = min(col_depth_in_wall_direction, wall_thickness)
               × col_width_along_wall

V2 Formwork:
  Wall inner face: deduct column width (800mm) where column sits
  Column: deduct wall contact face (600mm × col_height on one side)
          keep 200mm protruding portion fully formed
```

**Data needed / 필요한 데이터:**
- Column position (x_mm, y_mm) and dimensions (b_mm, h_mm) at each level
- Basement wall position, direction, and thickness
- Junction overlap computed from column footprint ∩ wall boundary

**Computation approach / 계산 방법:**
```python
for each basement wall panel:
    for each column at same level:
        if column_xy overlaps with wall boundary:
            # Determine overlap depth (perpendicular to wall)
            overlap_depth = min(col_dim_perp_to_wall, wall_thickness)
            # Determine overlap width (along wall direction)
            overlap_width = col_dim_along_wall
            # Deduct from wall volume
            deduction = overlap_depth × overlap_width × wall_height / 1e9  # m³
```

**Real example / 실제 예시:**
```
Column TC1: b=800, h=800 at grid X3Y1
Basement wall BW1: thickness=600, height=4150, at same location
  overlap_depth = min(800, 600) = 600mm
  overlap_width = 800mm
  deduction = 600 × 800 × 4150 / 1e9 = 1.992 m³ per level
```

### 2.5 Slab — No Change from V1 / 변경 없음

```
V1: V = (gross_area - column_footprints - wall_footprints) × thickness - opening_deductions
V2: Same — V1 already handles footprint deductions correctly.
```

Slab deductions use vertical member footprint areas per level.
V1 logic is already junction-aware for slabs.

### 2.5 Footing — No Change / 변경 없음

```
V1: V = area × thickness
V2: Same — Footings don't have junction overlaps.
```

### 2.6 Stair — No Change / 변경 없음

```
V1: V = waist_area × waist_thickness + step_volume
V2: Same — Stairs don't overlap with other members.
```

---

## 3. Formwork Rules / 거푸집 규칙

### 3.1 Column — V2 Upgrade: Inner/Outer Face Separation / 내외면 분리

```
V1: F = 2(b + h) × L (full perimeter, all 4 sides)

V2: Separate inner and outer faces.
    For each face of the column:
      If face is against a beam/slab → "inner" (concrete-to-concrete contact)
      If face is exposed → "outer" (needs formwork)

    In practice for V2 initial release:
    F = 2(b + h) × L (same as V1 — simplified)
    Report separately: total_perimeter, beam_contact_faces (info only)

    Exception — Column embedded in basement wall:
    If column is inside a wall (faces aligned):
      Deduct wall contact face from column formwork
      Contact face = min(col_depth, wall_thickness) × col_height
      Protruding portion (col_depth - wall_thickness) keeps full formwork
```

**Rationale**: Full deduction of beam-contact faces is complex (partial contact, varying depth).
For V2 initial release, keep V1 formula but **report** which faces have beam contact for future refinement.

### 3.2 Beam — V2 Upgrade: Clear Span + Inner/Outer Side Separation / 순경간 + 내외면 분리

```
V1: F = b × L (soffit) + 2 × downstand × L (sides)
    L = full span, no column deduction

V2: F_soffit = b × L_clear
    F_sides_outer = 2 × downstand × L_clear
    F_sides_inner = 0  (sides inside column zone removed)
    L_clear = L_span - extend_start - extend_end

    Additionally, for beam-beam junctions (T-joints):
      The perpendicular beam's side formwork is deducted
      where it meets the primary beam
```

**What changes**:
1. Soffit length uses **clear span** (not full span)
2. Side formwork uses **clear span**
3. At column junctions: beam sides inside column are not formed (no formwork)

**Real example (P1)**:
```
B21: L=5400, ext_start=100, ext_end=0, b=400, h=1250, slab_t=200
  downstand = 1250 - 200 = 1050mm
  V1: soffit = 0.4 × 5.4 = 2.16m², sides = 2 × 1.05 × 5.4 = 11.34m²
  V2: soffit = 0.4 × 5.3 = 2.12m², sides = 2 × 1.05 × 5.3 = 11.13m²
  Difference: soffit -1.9%, sides -1.9%
```

### 3.3 Normal Wall — V2 Upgrade: Clear Length + Opening Reveals / 순길이 + 개구부 리빌

```
V1: F = 2 × height × length (both faces) + edge
    Opening deductions + reveal area added back

V2: F = 2 × height × L_clear (both faces)
    L_clear = length - extend_start - extend_end
    Opening deductions + reveal same as V1
    
    Additionally, where wall meets column:
      Column face area deducted from wall formwork
      (column-wall contact = no formwork needed)
```

### 3.4 Basement Wall — V2 Upgrade: Column Overlap Formwork / 기둥 중첩 거푸집

```
Basement wall with embedded column:

V2: F_inner (면 facing building interior):
      Deduct column width × wall_height where column sits
      F_inner = 2 × height × L - Σ(col_width_along_wall × wall_height)

    F_outer (면 facing soil/earth):
      Depends on construction method:
        If cast against earth → no formwork (soil acts as form)
        If formed → full face area

    Column inside wall:
      Protruding face (col_depth - wall_thickness) × col_height
      gets full formwork.
      Contact face (wall_thickness × col_height) has no formwork
      on the column side (covered by wall concrete).

Real example:
  Column TC1 800×800, Wall BW1 thickness=600, height=4150
    Wall inner face deduction: 800 × 4150 = 3.32 m²
    Column formwork: protruding 200mm × 2 sides × 4150 = 1.66 m²
                   + 800mm × 1 exposed face × 4150 = 3.32 m²
                   = 4.98 m² (instead of full 2(800+800)×4150 = 26.56 m²)
```

**Note / 참고:**
지하벽 외면(토사 측)의 거푸집은 시공 방법에 따라 다름.
흙막이 시공 시 외면 거푸집 불필요 (토사가 거푸집 역할).
Basement wall outer face formwork depends on construction method.
When cast against retained earth, outer face needs no formwork.

### 3.5 Slab — No Change from V1 / 변경 없음

```
V1: F_soffit = gross_area - beam_footprint_area
    F_edge = perimeter × thickness
V2: Same — V1 already handles beam footprint deduction.
```

### 3.5 Footing/Stair — No Change / 변경 없음

Same as V1.

---

## 4. Inner vs Outer Formwork / 내면 vs 외면 거푸집

### 4.1 Concept / 개념

At every junction where two members meet, one face of one member is in contact
with the other member's concrete. That contact face does NOT need formwork.

두 부재가 만나는 모든 접합부에서, 한 부재의 한 면은 다른 부재의 콘크리트와 접촉합니다.
그 접촉면은 거푸집이 필요하지 않습니다.

### 4.2 Junction Types / 접합부 유형

| Junction / 접합부 | Higher Priority | Lower Priority | Contact Area |
|---------|----------------|----------------|-------------|
| Column-Beam | Column | Beam | Beam end face (b × downstand) at each column |
| Column-Wall | Column | Wall | Wall face area (thickness × height) inside column |
| Column-Slab | Column | Slab | Column footprint area deducted from slab soffit |
| Beam-Slab | Beam | Slab | Beam footprint (b × L) deducted from slab soffit |
| Wall-Slab | Wall | Slab | Wall footprint deducted from slab soffit |
| Beam-Beam (T) | Primary | Secondary | Secondary beam end face (b × h) |
| Wall-Wall (L/T) | Through wall | Abutting wall | Abutting wall end face (thickness × height) |

### 4.3 V2 Implementation Approach / V2 구현 방침

**Phase 1 (initial)**: Use `extend_start_mm`/`extend_end_mm` for length-based deductions only.
Report inner/outer formwork as informational but don't deduct face areas yet.

**Phase 2 (future)**: Full face-area deductions at junctions using polygon intersection data.

---

## 5. Rebar QTO — V2 Upgrade / 철근 물량 V2 업그레이드

### 5.1 V1 → V2 Changes / 변경 사항

```
V1: weight = total_length × unit_weight_per_dia
    total_length from RebarLengths tables (length_mm × n_bars)
    unit_weight from REBAR_UNIT_WEIGHT constant table

V2: Same formula, but:
    1. Uses correct fc/fy from material_id (affects development/lap lengths)
    2. Uses merged beam spans (correct bar lengths, no absurd 3350mm on 200mm beams)
    3. Uses dia_fy_map for correct yield strength lookup
    4. Supports revision_id for original vs optimized comparison
```

### 5.2 Rebar Unit Weights / 철근 단위 중량 (unchanged)

| Diameter | kg/m |
|----------|------|
| D10 | 0.617 |
| D13 | 1.042 |
| D16 | 1.578 |
| D19 | 2.226 |
| D22 | 2.984 |
| D25 | 3.853 |
| D29 | 5.185 |
| D32 | 6.313 |

### 5.3 SLP Scope / SLP 대상 (unchanged from V1)

- **SLP target**: Main bars with diameter ≥ 16mm
- **Excluded**: Stirrups, hoops, ties (coil rebar), diameter < 16mm
- **Stock length**: 12m default

---

## 6. Data Flow / 데이터 흐름

```
Converter Output (Daniel):
  MembersBeam.csv     → extend_start_mm, extend_end_mm, material_id, element_ids
  MembersColumn.csv   → material_id, fy_main, fy_sub
  MembersWall.csv     → extend_start_mm, extend_end_mm, material_id
  RebarLengths*.csv   → 3D rebar with correct fc/fy from material

QTO V2 Engine:
  Concrete:
    Column: b × h × L (no change)
    Beam:   b × downstand × (L - ext_start - ext_end)  ← NEW
    Wall:   t × h × (L - ext_start - ext_end)           ← NEW
    Slab:   (area - footprints) × thickness (no change)

  Formwork:
    Column: 2(b+h) × L (no change, report inner/outer info)
    Beam:   (b + 2×downstand) × (L - ext_start - ext_end)  ← NEW
    Wall:   2 × h × (L - ext_start - ext_end)               ← NEW
    Slab:   area - beam_footprint (no change)

  Rebar:
    weight = Σ (length_mm × n_bars / 1000 × unit_weight)
    Grouped by: diameter, member_type, level, bar_role
```

---

## 7. Polygon Members / 다각형 부재

### 7.1 Which Members Can Be Polygons / 다각형이 가능한 부재

| Member / 부재 | Rectangular / 직사각형 | Polygon / 다각형 | Data / 데이터 |
|------|:-:|:-:|------|
| **Slab** | 4 nodes | 5-22 nodes | `boundary_nodes`, `node_count` |
| **Footing** | RECT | POLYGON_N (e.g., 6-node) | `shape`, `boundary_nodes` |
| **Wall** | 4-node quad | Junction-modified polygon | `poly_0x..poly_3y_mm` |
| **Basement Wall** | 4-node quad | Junction-modified polygon | `poly_0x..poly_3y_mm` |
| Column | Always RECT | — | — |
| Beam | Always linear | — | — |

**Real data (P2)**:
- 55 rectangular slabs (4 nodes) + 52 polygon slabs (5-22 nodes)
- 1 polygon footing (POLYGON_6, 6 nodes)
- All walls have junction-modified polygon data

### 7.2 Concrete — Polygon Area / 콘크리트 — 다각형 면적

```
V1: V = area_mm2 × thickness  (area_mm2 = Lx × Ly bounding box for slabs)
    Problem: bounding box overestimates area for non-rectangular shapes

V2: V = polygon_area × thickness
    polygon_area computed from boundary_nodes using Shoelace formula
    For RECT shapes: same as V1 (Lx × Ly)
    For POLYGON_N: actual polygon area from vertices
```

**Data source**: `boundary_nodes` (semicolon-separated node IDs) → resolve to XY coords → Shoelace area

**Impact**: L-shaped or trapezoidal slabs could have 10-30% less area than bounding box.

### 7.3 Formwork — Polygon Perimeter / 거푸집 — 다각형 둘레

```
V1: F_edge = 2(Lx + Ly) × thickness  (rectangular perimeter)

V2: F_edge = polygon_perimeter × thickness
    polygon_perimeter = sum of edge lengths from boundary_nodes
    For RECT: same as V1
    For POLYGON_N: actual perimeter (could be longer than bounding box perimeter)

    F_soffit = polygon_area - beam_footprint - column_footprint
    (same deduction rules, but using actual polygon area)
```

### 7.4 Rebar — Already Handled / 철근 — 이미 처리됨

Polygon rebar is already correctly calculated by Daniel's converter:
다각형 철근은 이미 Daniel의 converter에서 정확히 계산됨:

- **Slab**: Scan-line polygon clipping produces per-bar lengths that follow the polygon boundary.
  Individual bar lengths vary based on where they intersect the polygon edges.
- **Footing**: Same scan-line clipping for polygon footings (POLYGON_N).
- **Wall/BWall**: Rebar follows the wall panel geometry (not affected by junction polygon — that's for concrete/formwork only).

The RebarLengths CSV already contains correct bar lengths for polygon members.
QTO rebar calculation just sums `length_mm × n_bars × unit_weight` — no polygon handling needed in QTO engine.

QTO 철근 계산은 `length_mm × n_bars × unit_weight`만 합산 — QTO 엔진에서 다각형 처리 불필요.

---

## 8. Filtering & Reporting / 필터링 및 리포트

All QTO results should be filterable:
모든 QTO 결과는 필터링 가능해야 함:

| Filter / 필터 | Example / 예시 |
|------|------|
| By floor / 층별 | "3F 콘크리트 물량만" / "Concrete at 3F only" |
| By member type / 부재 유형별 | "기둥만" / "Columns only" |
| By cross-filter / 교차 필터 | "3F 기둥 콘크리트" / "Column concrete at 3F" |
| By diameter / 직경별 (rebar) | "D22 철근 물량" / "D22 rebar weight" |

**Output grouping / 출력 그룹:**
- Concrete: by_level, by_member_type, by_level×type matrix
- Formwork: by_level, by_member_type, by_face_type (soffit/side/edge)
- Rebar: by_level, by_member_type, by_diameter, by_bar_role

---

## 9. Implementation Tasks / 구현 작업

1. **Upgrade beam concrete**: Use `L_clear = L_span - ext_start - ext_end`
2. **Upgrade normal wall concrete**: Same clear length approach
3. **Basement wall-column overlap**: Compute column footprint ∩ wall boundary, deduct overlap volume
4. **Upgrade beam formwork**: Clear span for soffit and sides
5. **Upgrade normal wall formwork**: Clear length for both faces
6. **Basement wall-column formwork**: Deduct column contact from wall inner face, deduct wall contact from column perimeter
7. **Add inner/outer formwork reporting** (informational, Phase 1)
8. **Polygon concrete/formwork**: Use Shoelace area + polygon perimeter for non-rectangular slabs/footings
9. **Rebar QTO**: Already correct from converter — just ensure revision_id filtering
10. **Filtering**: by_level, by_member_type, cross-filter, by_diameter
11. **API**: Add `revision_id` parameter + filter parameters to all QTO endpoints

---

## 10. Consensus Items / 합의 필요 사항

1. **Opening threshold values**: Keep V1 defaults? (area=0.5m², volume=0.05m³)
2. **Slab thickness for downstand**: Keep mode (most common) or use per-beam lookup?
3. **Inner/outer formwork Phase 2 timing**: After BIM viewer integration?
4. **Stair/Footing concrete**: Any junction rules needed?
5. **Polygon area source**: Use `area_mm2` from CSV (if correctly computed) or recompute from `boundary_nodes`?
6. **Basement wall outer face**: Assume no formwork (cast against earth) or project-configurable?
7. **Column-in-wall detection**: Use spatial intersection (column XY ∩ wall boundary) or rely on junction data?
