# AISIMS Data Inquiry - Project 1 (청담동 78-5)
# AISIMS 데이터 문의 - 프로젝트 1 (청담동 78-5)

Date: 2026-03-24

We have completed the initial data conversion of the provided MIDAS Gen exports.
During processing, we identified the following items that require clarification.

제공받은 MIDAS Gen 데이터의 초기 변환을 완료했습니다.
처리 과정에서 아래와 같은 확인이 필요한 사항을 발견했습니다.

---

## Issue 1: Missing Section Definition for 4 Beam Elements
## 문의 1: 4개 보 부재의 단면 정보 누락

**Description / 설명:**

In `Elements.csv`, the following 4 elements are defined as `Type=BEAM`,
but their `Property` value (1) references the Thickness table ("Core", 200mm)
instead of the Sections table. No matching beam section exists in `Sections.csv`.

`Elements.csv`에서 아래 4개 부재가 `Type=BEAM`으로 정의되어 있으나,
`Property` 값(1)이 단면(Sections) 테이블이 아닌 두께(Thickness) 테이블의
"Core" (200mm)를 참조하고 있습니다. `Sections.csv`에 해당 보 단면이 존재하지 않습니다.

| Element ID | Level | Node i | Node j | Span (mm) | Location (X, Y) |
|------------|-------|--------|--------|-----------|-----------------|
| 892        | 2F    | 434    | 435    | 750       | (7750, 7100) - (8500, 7100) |
| 1121       | 4F    | 573    | 574    | 750       | (7750, 7100) - (8500, 7100) |
| 1236       | 5F    | 642    | 643    | 750       | (7750, 7100) - (8500, 7100) |
| 1500       | 3F    | 507    | 508    | 750       | (7750, 7100) - (8500, 7100) |

All 4 elements share the same X-Y position across floors 2F-5F,
with a short span of 750mm. They appear to be **link beams (연결보)**
within or adjacent to a core wall.

4개 부재 모두 2F~5F에 걸쳐 동일한 X-Y 좌표에 위치하며,
경간이 750mm로 짧습니다. 코어 벽체 내부 또는 인접한 **연결보(link beam)**로 보입니다.

**Request / 요청:**

1. Are these elements intended to be beams? If so, please provide the beam section
   (b x h) for these members.
2. Or should they be modeled as part of the wall (Type=WALL)?

1. 해당 부재가 보로 설계된 것이 맞습니까? 맞다면, 보 단면 (b x h)을 제공해 주십시오.
2. 또는 벽체(Type=WALL)의 일부로 모델링해야 합니까?

---

## Issue 2: Missing Design Output for TB14
## 문의 2: TB14 설계 결과 누락

**Description / 설명:**

Section `5TB14` (Transfer Beam TB14 at 5F) exists in `Sections.csv` (ID=14611),
but no corresponding design result appears in `DesignBeam.csv`.

단면 `5TB14` (5층 전이보 TB14)이 `Sections.csv`(ID=14611)에 존재하지만,
`DesignBeam.csv`에 해당 설계 결과가 포함되어 있지 않습니다.

**Request / 요청:**

1. Was TB14 excluded from the beam design analysis intentionally?
2. If designed, please provide the DesignBeam output for this member.

1. TB14가 보 설계 해석에서 의도적으로 제외된 것입니까?
2. 설계가 완료되었다면, 해당 부재의 DesignBeam 결과를 제공해 주십시오.

---

## Issue 3: Missing Stair Dimensions
## 문의 3: 계단 치수 누락

**Description / 설명:**

`StairReinforcement.csv` provides stair flight width (1300mm), flight run (2970mm),
total height, and landing lengths. However, the following dimensions required
for BIM modeling and rebar calculation are not included in the provided data.

`StairReinforcement.csv`에 계단 비행폭(1300mm), 비행 수평거리(2970mm),
총 높이, 참 길이가 포함되어 있으나, BIM 모델링 및 철근 산출에 필요한
아래 치수가 데이터에 포함되어 있지 않습니다.

**Missing dimensions / 누락 치수:**

| Dimension | Description | Description (KR) |
|-----------|-------------|------------------|
| Waist slab thickness (mm) | Structural slab thickness of the stair flight | 계단 경사 슬래브 두께 |
| Riser height (mm) | Height of each step | 챌면 높이 |
| Tread depth (mm) | Horizontal depth of each step | 디딤판 깊이 |

**Stair members requiring these dimensions / 해당 계단 부재:**

| member_id | level_from | level_to | total_height_mm |
|-----------|------------|----------|-----------------|
| B3SS1     | B4         | B3       | 4400            |
| B2SS1     | B3         | B2       | 5950            |
| B1SS1     | B2         | B1       | 3600            |
| 1SS1      | B1         | 1F       | 4150            |
| 2SS1      | 1F         | 2F       | 4500            |
| 3SS1      | 2F         | 3F       | 4150            |
| 4SS1      | 3F         | 4F       | 4150            |
| 5SS1      | 4F         | 5F       | 4150            |
| 6SS1      | 5F         | 6F       | 4150            |
| RSS1      | 6F         | Roof     | 6200            |

**Request / 요청:**

1. Please provide the waist slab thickness for the stair flights.
2. Please provide the riser height and tread depth.
3. If these dimensions vary by floor, please specify per stair member.

1. 계단 비행부의 허리 슬래브 두께를 제공해 주십시오.
2. 챌면 높이와 디딤판 깊이를 제공해 주십시오.
3. 층별로 치수가 다른 경우, 각 계단 부재별로 명시해 주십시오.

---

## Summary Table / 요약

| # | Issue | Elements | Status |
|---|-------|----------|--------|
| 1 | BEAM elements referencing Thickness instead of Sections | 892, 1121, 1236, 1500 | Need section definition or reclassification |
| 2 | TB14 missing from DesignBeam.csv | 5TB14 (Section ID 14611) | Need design output |
| 3 | Stair dimensions missing | All 10 stairs (B3SS1 ~ RSS1) | Need waist thickness, riser height, tread depth |

Please respond at your earliest convenience so we can finalize the data conversion.
확인 후 회신 부탁드립니다.
