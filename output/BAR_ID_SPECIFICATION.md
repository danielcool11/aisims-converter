# AISIMS Bar Identification System — Specification

> Three-tier identification for structural members and their reinforcement bars.
> Implemented in `converter/converters/bar_id.py`.

---

## Overview

| ID | Scope | Example | Where used |
|---|---|---|---|
| **Member Instance ID** | One physical member at one location | `BD01-F05-W002-001` | MembersWall, MembersBeam, etc. |
| **Bar ID** | One rebar entry (unique per bar record) | `BD01-F05-W002-001-4-AD22@200-001` | RebarLengthsWall, RebarLengthsBeam, etc. |
| **Bar Mark** | Rebar specification within a member | `4-AD22@200-001` | Suffix of Bar ID |

Relationship:
```
Member Instance ID ──┬── Bar Mark 1  →  Bar ID 1
                     ├── Bar Mark 2  →  Bar ID 2
                     └── Bar Mark N  →  Bar ID N

Bar ID = Member Instance ID + "-" + Bar Mark
```

---

## 1. Member Instance ID

**Format**: `Building-Floor-Symbol-Serial`

Uniquely identifies one physical member at one location and one level.

### Components

| Field | Width | Description | Examples |
|---|---|---|---|
| **Building** | 4 chars | Building code | `BD01` (default), `BD02` |
| **Floor** | 3 chars | Level code | `F05`, `B01`, `PIT`, `ROF`, `FTG` |
| **Symbol** | Variable | Member mark, number-padded | `W002`, `C001`, `G001A`, `MF001` |
| **Serial** | 3 digits | Instance sequence within (Floor, Symbol) | `001`, `002` |

### Floor Code Mapping

| Level | Code | Level | Code |
|---|---|---|---|
| B5 | `B05` | 1F | `F01` |
| B4 | `B04` | 2F | `F02` |
| B3 | `B03` | 10F | `F10` |
| B2 | `B02` | 15F | `F15` |
| B1 | `B01` | Roof | `ROF` |
| PIT | `PIT` | Footing | `FTG` |

### Symbol Rules

1. **Standard**: Extract letter prefix + number, pad number to 3 digits
   - `W2` → `W002`, `G1A` → `G001A`, `CW13` → `CW013`, `MF1` → `MF001`

2. **Slab/Stair floor-prefix stripping**: Remove the level prefix embedded in the mark name
   - `10S1` at 10F → strip `10` → `S1` → `S001`
   - `B4SS1` at B4 → strip `B4` → `SS1` → `SS001`
   - `PHRS13` at Roof → strip `PHR` → `S13` → `S013`

   Floor prefixes recognized: `PHR*`, `PH\d+`, `PIT`, `R` (before S), `B\d+`, `\d+`

3. **Collision fallback**: If stripping produces the same symbol for different marks at the same level, the original unstripped mark is used instead.
   - `RS12` and `PH2S12` both strip to `S012` → collision detected
   - `RS12` → `RS012`, `PH2S12` → `PH2S12` (unstripped)

### Serial Assignment by Member Type

| Member | Grouping key | Sort order |
|---|---|---|
| **Beam** | `(member_id, level)` | Endpoint coordinates (x_from, y_from) |
| **Column** | `(member_id, level_from)` | Centroid (x, y) |
| **Wall** | `(wall_mark, level)` | Centroid (x, y) |
| **Basement Wall** | `(wall_mark, level)` | panel_no or centroid |
| **Slab** | `(member_id, level)` | Centroid (x, y) |
| **Footing** | `(member_id)` | Centroid (x, y) |
| **Stair** | `(member_id, story_group)` | segment_id |

### Examples

```
BD01-F05-W002-001     Wall mark W2, 5th floor, 1st instance
BD01-F05-W002-002     Wall mark W2, 5th floor, 2nd instance (different location)
BD01-B01-G001A-003    Beam G1A, basement 1, 3rd span
BD01-ROF-RS012-001    Slab RS12, roof (collision-safe, unstripped)
BD01-FTG-MF001-001    Mat footing MF1, 1st piece
BD01-F03-SS001-001    Stair SS1, 3rd floor
```

---

## 2. Bar Mark

**Format**: `Count-PositionDia@Spacing-Serial`

Describes the rebar specification within a member. Not globally unique — unique only within a member instance.

### Components

| Field | Description | Examples |
|---|---|---|
| **Count** | Number of bars (`n_bars`) or pieces (`quantity_pieces` for stirrups) | `4`, `2`, `12` |
| **Position** | 1-char position prefix (see table below) | `T`, `B`, `A`, `N`, `F` |
| **Dia** | Diameter in mm (integer) | `22`, `29`, `10` |
| **@Spacing** | Optional: spacing in mm (only if `spacing_mm > 0`) | `@200`, `@150` |
| **Serial** | 3-digit sequence within identical (member, position, dia, spacing) group | `001`, `002` |

### Position Prefix

| Prefix | Meaning | Used for |
|---|---|---|
| `T` | Top | Beam, Slab, Footing (bar_position=TOP) |
| `B` | Bottom | Beam, Slab, Footing (bar_position=BOT) |
| `M` | Middle | Beam, Slab (bar_position=MID) |
| `A` | All (generic) | Wall |
| `N` | Near (interior) | Basement Wall (face=INTERIOR) |
| `F` | Far (exterior) | Basement Wall (face=EXTERIOR) |
| `D` | Default | Any unclassified position |

### Serial Assignment

Bars within the same `(member, position, dia, spacing, bar_role)` group are sorted by level rank (B5 < B1 < PIT < 1F < ... < Roof), then assigned serial 001, 002, ...

### Examples

```
4-TD22-001            4 top bars, D22, 1st group
2-BD29-002            2 bottom bars, D29, 2nd group
12-AD16@200-001       12 wall bars, D16 at 200mm spacing, 1st group
8-ND13@150-001        8 interior basement wall bars, D13 at 150mm spacing
1-DD10@100-003        1 hoop, D10 at 100mm spacing, 3rd set
```

---

## 3. Bar ID

**Format**: `MemberInstanceID-BarMark`

Globally unique identifier for each rebar record. Concatenation of Member Instance ID and Bar Mark.

### Construction

```
Bar ID = {Building}-{Floor}-{Symbol}-{Serial}-{Count}-{Position}{Dia}@{Spacing}-{BarSerial}
         |_______ Member Instance ID ________|  |_____________ Bar Mark _______________|
```

### Examples

```
BD01-F05-G001-003-4-TD22-001
│    │    │    │   │ │  │  └── bar serial (1st group of this spec)
│    │    │    │   │ │  └───── diameter 22mm
│    │    │    │   │ └──────── position: Top
│    │    │    │   └────────── 4 bars
│    │    │    └────────────── 3rd beam span of G1 at 5F
│    │    └─────────────────── beam mark G1 (padded)
│    └──────────────────────── 5th floor
└───────────────────────────── building BD01

BD01-B01-CW013-001-12-AD16@200-001
│    │    │      │   │  │  │    └── bar serial
│    │    │      │   │  │  └────── spacing 200mm
│    │    │      │   │  └───────── diameter 16mm, position: All (wall)
│    │    │      │   └──────────── 12 bars
│    │    │      └──────────────── 1st instance of CW13 at B1
│    │    └─────────────────────── core wall mark CW13 (padded)
│    └──────────────────────────── basement 1
└───────────────────────────────── building BD01

BD01-PIT-PW200-001-2-BD29-002
    PIT level, exterior wall PW200, 1st instance, 2 bottom bars D29, 2nd group
```

---

## Matching: Member ↔ Rebar

Rebar records are matched to members via spatial lookup:

| Member type | Matching method |
|---|---|
| **Beam** | Bounding box containment (rebar x_start within beam x_from~x_to ± 500mm) |
| **Column** | Closest centroid by (x, y) distance |
| **Wall** | Closest centroid by (x, y) distance |
| **Basement Wall** | Closest centroid by (x, y) distance |
| **Slab** | Closest centroid by (x, y) distance |
| **Footing** | Closest centroid by (x, y) distance (no level grouping) |
| **Stair** | Direct: story_group + segment_id → instance ID |

---

## File Locations

| File | Contains |
|---|---|
| `MembersBeam.csv` | `member_instance_id` column (first column) |
| `MembersColumn.csv` | `member_instance_id` column |
| `MembersWall.csv` | `member_instance_id` column |
| `MembersSlab.csv` | `member_instance_id` column |
| `MembersFooting.csv` | `member_instance_id` column |
| `MembersStair.csv` | `member_instance_id` column |
| `MembersBasementWall.csv` | `member_instance_id` column |
| `RebarLengthsBeam.csv` | `bar_id` + `member_instance_id` columns (first two) |
| `RebarLengthsColumn.csv` | `bar_id` + `member_instance_id` columns |
| `RebarLengthsWall.csv` | `bar_id` + `member_instance_id` columns |
| `RebarLengthsSlab.csv` | `bar_id` + `member_instance_id` columns |
| `RebarLengthsFooting.csv` | `bar_id` + `member_instance_id` columns |
| `RebarLengthsStair.csv` | `bar_id` + `member_instance_id` columns |
| `RebarLengthsBasementWall.csv` | `bar_id` + `member_instance_id` columns |

---

## Implementation

Source: `converter/converters/bar_id.py`

- `assign_bar_ids(outputs, building)` — main entry point
  - Phase 1: Assign `member_instance_id` to all Members DataFrames
  - Phase 2: Assign `bar_id` + `member_instance_id` to all RebarLengths DataFrames
  - Phase 3: Fix footing boundary nodes, beam split nodes
  - Phase 4: Reorder columns (`member_instance_id` first, `bar_id` first for rebar)
