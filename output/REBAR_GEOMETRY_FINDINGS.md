# Rebar Geometry Findings — 2026-05-15

Two rebar-geometry defects observed in the AISIMS-V2 3D viewer (project
Buldang-dong, revision V1.15). Recorded here so the converter side can be
picked up. The viewer faithfully draws the CSV-ingested `rebar_lengths_*`
geometry — both defects are converter-side, not viewer-side.

---

## Finding 1 — Wall DOWEL `wall_dir_x/y_mm` — RESOLVED (re-ingest needed)

### Symptom
In the viewer, DOWEL bars on wall **CW8** rendered scattered away from the
wall's MAIN vertical bars instead of lapped adjacent to them. Other walls
looked fine.

### Root cause
DOWEL distribution direction depends on `wall_dir_x_mm`/`wall_dir_y_mm`. When
NULL, the viewer falls back to wall-segment matching, which picks the wrong
segment on geometrically ambiguous walls (e.g. CW8) and scatters the dowels.

In Buldang V1.15's ingested data, **all 52 DOWEL rows have NULL `wall_dir`**
while all `MAIN_*` vertical bars have it populated.

### Status — already fixed in the converter
`tier2/rebar_lengths_wall.py` `_emit_dowel()` (lines 605-622) now computes
`wall_dir_x_mm`/`wall_dir_y_mm` from the wall segment, inside `if seg:`.
This landed in commit **`0fe900c`** (2026-05-13, "fix: add wall_dir to DOWEL
rebar for L-shaped wall distribution").

Buldang V1.15 was converted **before** `0fe900c`, so its CSV/DB data is stale.

### Action
- **Re-ingest** Buldang V1.15 (and any other pre-2026-05-13 revision) to pick
  up the corrected DOWEL `wall_dir`. No converter code change needed.
- The AISIMS-V2 viewer also has a **defensive workaround** (in
  `frontend/src/bim-vendor/lib/bim/rebar/wallRebar.ts`): a DOWEL with NULL
  `wall_dir` borrows it from the nearest MAIN vertical bar of the same
  `wall_mark`. This makes stale revisions render correctly without re-ingest.

---

## Finding 2 — Stair lower-landing TOP bar anchorage — OPEN

### Symptom
The lower-landing longitudinal bars have two ends with asymmetric treatment.
One end (toward the flight) looks normal; the other end (at the wall) shows a
short vertical stub that Daniel flagged as a "weird anchorage starting
position."

### Landing reinforcement layout
The lower landing is reinforced in **two directions**, like a two-way slab:
- **Direction A** — longitudinal, `*_ALONG_A` bars (`TOP_ALONG_A`, `BOT_ALONG_A`)
- **Direction B** — transverse, `DIST_SPAN_B` bars

Both directions carry the same 12d wall-hook + layer-overlap concerns below.

### What the converter does (intentional, but flagged for review)
`converters/reinforcement_stair.py`:
- `HOOK_EXT_FACTOR = 12` (line 36), `hook_tail = 12 * dist_dia` (line 276).
- Bar #1 `LOWER_LANDING / TOP_ALONG_A` (lines 278-287):
  `hook_start_1 = wall_pt_1 + _vec(0, 0, -hook_tail)` — the bar starts 12d
  **below** the landing at the wall edge, bends up at the wall, then runs
  along the landing. Comment: *"90° hook at wall end (P1): bar starts below
  landing, bends up at wall edge."*
- Bar #2 `LOWER_LANDING / BOT_ALONG_A` (lines 289-293): starts at the landing
  edge with **no hook**, laps into the flight slope at the other end.
- Bar #3 `LOWER_LANDING / DIST_SPAN_B` (lines 300-307): same 12d down-hook
  pattern, `hook_start_3 = wall_pt_3 + _vec(0, 0, -hook_tail)`.

So the 12d 90° down-hook at the wall is **deliberate** — the converter's model
of "hook into the supporting wall."

### Open questions for review
1. **Hook tail direction is hardcoded `-Z` (straight down).** For Buldang V1.15
   the lower landing is ~156mm thick and `d13 → 12d = 156mm`, so the tail
   reaches exactly the landing soffit — visually a vertical stub spanning the
   full slab depth. If the supporting wall isn't modeled right there, the stub
   appears to float below the landing. Confirm the hook should bend down into
   the wall vs. some other direction, and whether tail length should be capped
   to the landing thickness.
2. **TOP vs BOTTOM longitudinal landing bars render at the same elevation.**
   In V1.15 data, `BOT_ALONG_A` and `TOP_ALONG_A` both run their flat portion
   at the same `z` (e.g. −18100). They overlap with no layer separation. The
   converter does not encode the top/bottom layer offset in `z`, and the
   viewer applies offset 0 for explicit TOP/BOTTOM layers — so they coincide.
   Either the converter should separate them by `(thickness − 2·cover)`, or the
   viewer should apply the layer offset for stair landing bars.

### Action
Converter-side review of stair lower-landing bar anchorage geometry. Not
viewer-fixable (the endpoint coordinates come from the converter).

Reference data — Buldang V1.15, `BD01-B05-SS001-001`, `LOWER_LANDING`:

| bar | start_z | bend1_z | end_z |
|---|---|---|---|
| `BOT_ALONG_A` (bottom) | −18100 | −18100 | −17797 |
| `TOP_ALONG_A` (top)    | −18256 | −18100 | −18100 |

`−18256 = −18100 − 156`, and `156 = 12 × 13mm (d13) = 12d` hook tail.
