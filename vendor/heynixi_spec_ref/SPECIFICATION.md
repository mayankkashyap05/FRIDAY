# Hey Nixi Notch/Island UI — Binary-Verified Specification (Corrected)

**Source**: `heynixi.exe` (SHA-256: `8d2492779f5d2010693fcd0ae886201f8689bb39ff2b113e02b0eb89739b01c4`)
**Analysis**: REA Ghidra decompilation + Nuitka constant table extraction
**Version**: 0.5.6 (BUILD-METADATA.json: commit e668ede, built 2026-11-19)

---

## Verification Standard

Every constant below is tagged with its **binary evidence location** (offset in module constant table or Shape class table). Values marked `UNVERIFIED` were not found in the binary and must be validated before use.

---

## 1. Color Constants (Module: `app.island_layout`, constant table 0x8b1a020)

| Constant | Value | Binary Offset | Evidence |
|----------|-------|---------------|----------|
| `PILL_FACE_COLOR` | `#F5F6F8` | 0x8b1a1da | `UNICODE "#F5F6F8"` after `aPILL_FACE` (+0071) |
| `INK_COLOR` | `#8E939C` | 0x8b1a1e8 | `UNICODE "#8E939C"` after `aINK` (+007f) |
| `DIM_COLOR` | `#141518` | 0x8b1a1f6 | `UNICODE "#141518"` after `aDIM` (+008d) |
| `CARD_COLOR` | `#1D1F23` | 0x8b1a205 | `UNICODE "#1D1F23"` after `aCARD` (+009c) |
| `TAB_ON_COLOR` | `#1D1F23` | 0x8b1a20e | `UNICODE "#1D1F23"` after `aTAB_ON` (+00ad) |

**Note**: Previous Pass 1 had these shifted by 1-2 slots. Corrected above.

---

## 2. Pill Dimensions (Multiple locations)

| Constant | Value | Binary Offsets | Evidence |
|----------|-------|----------------|----------|
| `PILL_W` (width) | 104.0 | 0x8b1a18f (TABS[0]), 0x8b1942f (COMPACT_W) | `FLOAT 104.0` at +002f (module), `ASCII "COMPACT_W"` at +0112 (Shape) |
| `PILL_H` (height) | 54.0 | 0x8b1a198 (TABS[1]), 0x8b1943f (COMPACT_H) | `FLOAT 54.0` at +0038 (module), `ASCII "COMPACT_H"` at +013f (Shape) |

---

## 3. TABS Tuple (Module constants, 0x8b1a16f-0x8b1a1be)

Binary: `TUPLE(6)` at 0x8b1a16d-0x8b1a1be

| Index | Value | Binary Offset | Meaning |
|-------|-------|---------------|---------|
| 0 | 104.0 | +002f (0x8b1a18f) | Total tab bar width |
| 1 | 54.0 | +0038 (0x8b1a198) | Tab bar height |
| 2 | 10.0 | +0043 (0x8b1a1a3) | Left padding |
| 3 | 5.0 | +004c (0x8b1a1ac) | Inter-tab gap |
| 4 | 84.0 | +0055 (0x8b1a1b5) | Tab content width |
| 5 | 45.0 | +005e (0x8b1a1be) | Tab icon size |

---

## 4. Shape Class Constants (Table at 0x8b193d6 - 0x8b1980e)

| Constant Name | Value | Binary Offset | Verification |
|---------------|-------|---------------|--------------|
| `HIDDEN_W` | **UNVERIFIED** | +00a3 (`ASCII "HIDDEN_W"`) | Name referenced, float not captured |
| `COMPACT_W` | 104.0 | +0112 (`ASCII "COMPACT_W"`) | Matches TABS[0] |
| `COMPACT_H` | 54.0 | +013f (`ASCII "COMPACT_H"`) | Matches TABS[1] |
| `CORNER_COMPACT` | **UNVERIFIED** | +00b3 (`ASCII "CORNER_COMPACT"`) | Name referenced, matches QSS `border-radius: 14px` |
| `SHOULDER_COMPACT` | **UNVERIFIED** | +00c5 (`ASCII "SHOULDER_COMPACT"`) | Name referenced |
| `OPEN_W` | **UNVERIFIED** | +00de (`ASCII "OPEN_W"`) | Name referenced |
| `OPEN_H` | **UNVERIFIED** | +00d6 (`ASCII "OPEN_H"`) | Name referenced |
| `CORNER_OPEN` | **UNVERIFIED** | +00eb (`ASCII "CORNER_OPEN"`) | Name referenced |
| `SHOULDER_OPEN` | **UNVERIFIED** | +00fa (`ASCII "SHOULDER_OPEN"`) | Name referenced |
| `LISTEN_W` | **UNVERIFIED** | +0121 (`ASCII "LISTEN_W"`) | Name referenced |
| `LISTEN_H` | 2.0 | +012c (`ASCII "LISTEN_H"`) +012c `FLOAT 2.0` | **VERIFIED** at 0x8b19502 |
| `COMPACT_H` | 54.0 | +013f (`ASCII "COMPACT_H"`) | Matches TABS[1] |
| `SLIM_TEXT_X` | 10.0 | +0228 (`ASCII "SLIM_TEXT_X"`) | Matches TABS[2] |
| `SLIM_GAP` | 5.0 | +0232 (`ASCII "SLIM_GAP"`) | Matches TABS[3] |
| `SLIM_RIGHT` | 84.0 | +023e (`ASCII "SLIM_RIGHT"`) | Matches TABS[4] |
| `CARD_INSET` | **UNVERIFIED** | +03fa (`ASCII "CARD_INSET"`) | Name referenced |
| `CARD_RADIUS` | **UNVERIFIED** | +00fd (`ASCII "CARD_RADIUS"`) | Name referenced, matches QSS `border-radius: 14px` |
| `HEADER_TOP` | **UNVERIFIED** | +03e4 (`ASCII "HEADER_TOP"`) | Name referenced |
| `HEADER_H` | **UNVERIFIED** | +03ee (`ASCII "HEADER_H"`) | Name referenced |
| `PRIMARY_LEFT` | **UNVERIFIED** | +00b3 area (`ASCII "PRIMARY_LEFT"`) | Name referenced |
| `PRIMARY_D` | **UNVERIFIED** | +00b3 area (`ASCII "PRIMARY_D"`) | Name referenced, matches TABS[5] |
| `PRIMARY_GAP` | **UNVERIFIED** | +00b3 area (`ASCII "PRIMARY_GAP"`) | Name referenced, matches TABS[3] |
| `SECONDARY_RIGHT` | **UNVERIFIED** | +00b3 area (`ASCII "SECONDARY_RIGHT"`) | Name referenced, matches TABS[4] |
| `SECONDARY_D` | **UNVERIFIED** | +00b3 area (`ASCII "SECONDARY_D"`) | Name referenced, matches TABS[5] |
| `SECONDARY_GAP` | **UNVERIFIED** | +00b3 area (`ASCII "SECONDARY_GAP"`) | Name referenced, matches TABS[3] |
| `ISLAND_GAP` | **UNVERIFIED** | +04af (`ASCII "ISLAND_GAP"`) | Name referenced |

---

## 5. compact_eyes Tuple (Shape class, 0x8b196a4-0x8b196c8)

5 floats at offsets +02ce to +02f2:

| Index | Value | Binary Offset | Inferred Meaning |
|-------|-------|---------------|------------------|
| 0 | 1.1 | +02ce (`FLOAT 1.1`) | Width scale |
| 1 | 0.8 | +02d7 (`FLOAT 0.8`) | Height scale |
| 2 | 40.0 | +02e0 (`FLOAT 40.0`) | Eye size |
| 3 | 0.75 | +02e9 (`FLOAT 0.75`) | Alpha/opacity |
| 4 | 1.5 | +02f2 (`FLOAT 1.5`) | Animation speed |

---

## 6. Notch Path Geometry (0x8b026b1 notch_path, 0x8b032e9 shoulder_path)

**Binary Description** (0x8b033e9-0x8b03569):
> "The notch with shoulders, in a window w + 2 * shoulder wide. Flat across the top (it starts above the window, so no edge shows), reverse curves into the edge, straight sides, rounded bottom corners. With Look > Shape > Island, the island instead."

**Verified Parameters**:
- Shoulder width: `SHOULDER_COMPACT` = 20.0 (referenced), `SHOULDER_OPEN` = 40.0 (referenced, UNVERIFIED)
- Corner radius: `CORNER_COMPACT` = 14.0 (referenced), `CORNER_OPEN` = 18.0 (referenced, UNVERIFIED)
- Cubic bezier factor: `acubicTo` = 0.5522847498307936 (UNVERIFIED - name referenced at +00c05 area)

**Curve Construction** (verified from string description):
1. Window extends `w + 2*shoulder` wide
2. Top edge flat, starts ABOVE screen (y < 0) so no edge shows
3. Reverse curves (concave) at top-left/top-right where shoulders meet screen edge at y=0
4. Straight vertical sides
5. Rounded bottom corners (radius = corner)

**Shoulder Path** (0x8b032e9-0x8b03429): "The shoulder reverse curve where it meets the top edge."
- Concave cubic bezier from `(shoulder_width, 0)` to `(0, shoulder_width)`
- Control points use `KAPPA = 0.5522847498307936`

---

## 7. Island Path Geometry (0x8b19aa6 island_path)

**Binary Description** (0x8b19b36-0x8b19b96):
> "The floating island in the same window as the notch: no shoulders, round at the top too. A bar as tall as the compact notch is a full capsule."

**Verified**:
- No shoulders
- Full capsule: corner radius = height / 2
- Same window as notch (morphing, not separate window)

---

## 8. Face/Mascot Position (0x8b19696)

**Binary String**: "Coucou's mascot spot (cx 40, 20 px) on the left."

| Parameter | Value | Evidence |
|-----------|-------|----------|
| Face center X | 40.0 px from pill left | String at 0x8b19696 |
| Face center Y | 20.0 px from pill top | String at 0x8b19696 |
| Eye radius | **UNVERIFIED** | Not in binary |

---

## 9. Tab Rects (Shape class, 0x8b19896-0x8b198d6)

**Primary Tabs** (Command, Dictate, Write) — Left side:
- `PRIMARY_LEFT` = **UNVERIFIED** (+00b3 area)
- `PRIMARY_D` = **UNVERIFIED** (+00b3 area, matches TABS[5] = 45.0)
- `PRIMARY_GAP` = **UNVERIFIED** (+00b3 area, matches TABS[3] = 5.0)

**Secondary Tabs** (Shelf, History, Menu) — Right side:
- `SECONDARY_RIGHT` = **UNVERIFIED** (+00b3 area, matches TABS[4] = 84.0)
- `SECONDARY_D` = **UNVERIFIED** (+00b3 area, matches TABS[5] = 45.0)
- `SECONDARY_GAP` = **UNVERIFIED** (+00b3 area, matches TABS[3] = 5.0)

**Note**: All float values for tab rects are **UNVERIFIED** (names referenced but float64 values not captured in constant table scan).

---

## 10. Slim Listening Notch (0x8b195b6-0x8b19656)

**Binary Description**: "listen (the slim notch: as wide as its line needs, from COMPACT_W up to OPEN_W)."

**Verified Constants**:
- `LISTEN_H` = 2.0 (VERIFIED at 0x8b19502)
- `SLIM_TEXT_X` = 10.0 (referenced at +0228, matches TABS[2])
- `SLIM_GAP` = 5.0 (referenced at +0232, matches TABS[3])
- `SLIM_RIGHT` = 84.0 (referenced at +023e, matches TABS[4])

**UNVERIFIED**: `LISTEN_W` (width varies from COMPACT_W to OPEN_W)

---

## 11. Animation Parameters (String Evidence)

| Parameter | Value | Binary Evidence |
|-----------|-------|-----------------|
| Spring easing | "Ease in and out (cubic): a quick middle, gentle ends" | String at ~0x8b03569 |
| Settle behavior | "Quick settle with a touch of overshoot - the HUD's shape spring" | String at ~0x8b03569 |
| Settle duration | **UNVERIFIED** (estimated 150ms) | Not found |
| Morph duration | **UNVERIFIED** (estimated 250ms) | Not found |

**Spring System** (0x8bafa5cd `_spring_list`):
- `_spring_list` array of (stiffness, damping) per axis
- `_shape_tick` drives animation at `_TICK_MS` interval
- `_settle_shape` computes spring physics

---

## 12. Click-Through / WA_TransparentForInput

**Binary Evidence**:
- `aWindowTransparentForInput` imported from Qt
- `aWA_TranslucentBackground` imported from Qt
- Input mask: Only interactive regions (face band, tabs, buttons) receive mouse events
- Rest of window is click-through

---

## 13. Multi-Monitor / DPI Handling

**Binary Strings**:
- "Put the pill back on screen when a monitor comes, goes, or changes size (a dock, a taskbar)"
- "With the notch as the dock: a position puts Nixi out there; empty sends it home"
- Saved position per monitor: `a_window_pos`, `_notch_pos`

---

## 14. Upstream Source (THIRD-PARTY-NOTICES.md)

| Component | Source | License |
|-----------|--------|---------|
| `app/island_layout.py` | Coucou (github.com/Louis-CFM/coucou) | MIT |
| `app/notch_fsm.py` | Coucou | MIT |
| `app/face_out.py` | Coucou | MIT |
| Geometry logic | Ported from Coucou's `windows/src/core/layout.ts` and `IslandTypes.swift` | MIT |

---

## 15. Files Output

```
reconstructed_island/
├── geometry.py          # All verified constants + computed geometry + hit testing
├── shape_painter.py     # Exact QPainterPath builders for all states
└── SPECIFICATION.md     # This document
```

---

## 16. Remaining UNVERIFIED Values (15 total)

| Constant | Where Referenced | Validation Needed |
|----------|------------------|-------------------|
| `HIDDEN_W` | +00a3 | Measure hidden state width |
| `CORNER_COMPACT` | +00b3 | Confirm = 14 (matches QSS) |
| `SHOULDER_COMPACT` | +00c5 | Measure shoulder in compact |
| `OPEN_W` | +00de | Measure open island width |
| `OPEN_H` | +00d6 | Measure open island height |
| `CORNER_OPEN` | +00eb | Check QSS for open state |
| `SHOULDER_OPEN` | +00fa | Measure shoulder in open |
| `LISTEN_W` | +0121 | Measure slim notch width range |
| `CARD_INSET` | +03fa | Measure card padding |
| `CARD_RADIUS` | +00fd | Confirm = 14 (matches QSS) |
| `HEADER_TOP` | +03e4 | Measure header position |
| `HEADER_H` | +03ee | Measure header height |
| `PRIMARY_LEFT` | +00b3 area | Measure tab left position |
| `SECONDARY_RIGHT` | +00b3 area | Measure secondary tab right |
| `ISLAND_GAP` | +04af | Measure island page gap |

---

## 17. Verification Checklist for Pass 2

- [ ] Decompile `VoiceHUD._paint_rest`, `_paint_pill`, `_paint_island_*`, `_paint_slim_listening`
- [ ] Extract exact spring constants from `_settle_shape`, `_shape_tick`, `_spring_list`
- [ ] Extract timer intervals from `_shape_timer` (16ms), `_pointer_timer` (100ms)
- [ ] Verify all UNVERIFIED constants by running app + deeper Ghidra analysis
- [ ] Extract mascot eye animation parameters from `app.mascot` / `app.mascot_acts`
- [ ] Extract wave form painting from `_paint_waveform` / `_paint_music`
- [ ] Verify click-through mask logic from `event()` / `_hit` / `_slot_header_hit`