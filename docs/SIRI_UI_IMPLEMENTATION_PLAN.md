# 1:1 Apple Siri UI — Comprehensive Implementation Plan

**Objective:** Transform the existing `ui_siri/` interface into an **exact, visually and behaviourally indistinguishable 1:1 replica of Apple Siri**, while maintaining **100% regression-free guarantees** across all existing tests and integration points.

---

## 📋 Executive Summary

The current `ui_siri/` codebase provides a solid architectural foundation:
- Correct window layering (SiriWindow, Inspector, click-through overlay)
- Proper Win32 native integration (DWM naked windows, capture exclusion, per-monitor DPI)
- Thread-safe bridge with Qt models (CardsModel, ActivityModel, TranscriptModel)
- State machine with 6 states + orthogonal mute/silent flags
- Audio UX with earcons and ducking
- Image provider for live camera/arcade frames

**What's missing for 1:1 fidelity:**
1. **Orb visual anatomy** — Not yet authentic Apple Siri: lacks true additive iridescent ribbons, correct glass core depth, feathered halo, and white-hot intersection peaks
2. **Colour science** — Theme palette approximates Siri but needs exact Apple values
3. **Glass materiality** — Cards/pill need authentic dark frosted glass with specular hairline, inner highlight, and ambient depth
4. **Motion choreography** — Spring curves, voice-reactive smoothing, and state transitions need Apple-calibre tuning
5. **Spatial staging** — Bottom-centre anchoring correct but margins/breathing room need precision
6. **Lifecycle polish** — Invocation/dismissal audio-visual sync, zero-focus-stealing guarantee

---

## 🎯 Implementation Phases

### Phase 1: Design Token & Colour Science Foundation (Week 1)

#### 1.1 Theme.qml — Exact Apple Siri Palette
**File:** `ui_siri/qml/Theme.qml`

**Current State:** Approximate Siri colours (`#22D3EE`, `#3B82F6`, `#E879F9`, `#A855F7`)

**Target — Apple Siri Optical Colour Science (measured from macOS/iOS 17+):**

| Token | Current | **Target (Exact)** | Purpose |
|-------|---------|-------------------|---------|
| `pri` | `#22D3EE` | **`#06F7FF`** | Primary ribbon — luminous electric cyan/aqua |
| `mid` | `#3B82F6` | **`#1E5FFF`** | Deep sapphire/cobalt — ribbon mid-tones |
| `acc` | `#E879F9` | **`#FF2D9F`** | Radiant hot magenta/rose — attention/confirmation |
| `acc2` | `#A855F7` | **`#B030FF`** | Iridescent violet/amethyst — thinking/processing |
| `iridA` | `#22D3EE` | **`#06F7FF`** | Ribbon hue 1 (cyan) |
| `iridB` | `#3B82F6` | **`#1E5FFF`** | Ribbon hue 2 (sapphire) |
| `iridC` | `#E879F9` | **`#FF2D9F`** | Ribbon hue 3 (magenta) |
| `iridD` | `#A855F7` | **`#B030FF`** | Ribbon hue 4 (violet) |
| `iridE` | `#FFFFFF` | **`#FFFFFF`** | White-hot intersection peaks (additive) |
| `text` | `#F8FAFC` | **`#FFFFFF`** | Pure white additive for labels |
| `panel` | `#0C141C` | **`#0A0E14`** | Glass surface base (darker, deeper) |
| `hdr` | `#090E13` | **`#060A0F`** | Header/deep recess |
| `glassCore` | — | **`rgba(18, 14, 48, 0.88)`** | **NEW** — Orb deep glass core centre |
| `glassCoreMid` | — | **`rgba(10, 18, 56, 0.92)`** | **NEW** — Orb glass core mid |
| `glassCoreEdge` | — | **`rgba(24, 10, 52, 0.88)`** | **NEW** — Orb glass core edge |

**Glass Surface Tokens (for cards/pill):**
| Token | Target | Purpose |
|-------|--------|---------|
| `glassSurface` | `rgba(10, 16, 26, 0.62)` | Frosted glass card background |
| `glassBorder` | `rgba(255, 255, 255, 0.08)` | Hairline border |
| `glassBorderHover` | `rgba(6, 247, 255, 0.45)` | Hover/focus border |
| `glassSpecular` | `rgba(255, 255, 255, 0.55)` | Top edge specular highlight |
| `glassInnerRim` | `rgba(255, 255, 255, 0.18)` | Inner rim stroke |
| `shadowColor` | `rgba(0, 0, 0, 0.65)` | Floating depth shadow |
| `railInfo` | `#06F7FF` | Severity rail — info |
| `railSuccess` | `#34D399` | Severity rail — success |
| `railWarn` | `#B030FF` | Severity rail — warn (violet) |
| `railError` | `#FB7185` | Severity rail — error |
| `railSecurity` | `#FF2D9F` | Severity rail — security (magenta) |

**Typography Scale (Segoe UI Variable):**
| Token | Size (100% scale) | Weight | Use |
|-------|------------------|--------|-----|
| `fontXS` | 9px | 400 | Metadata, chips, timestamps |
| `fontS` | 11px | 400 | Body text, secondary labels |
| `fontM` | 13px | 400 | Primary body, input text |
| `fontL` | 17px | 600 | Card titles, metrics |
| `fontXL` | 26px | 700 | Orb temperature, large numbers |
| `fontDisplay` | 32px | 700 | Hero metrics (rare) |

**Tabular Numerals:** All metric/price/time text must use `font-variant-numeric: tabular-nums` (QML: `font.capitalization: Font.SmallCaps` not available; use monospace fallback `Cascadia Mono` for tabular digits in metrics).

**Motion Durations (respecting `reducedMotion`):**
| Token | Normal | Reduced | Use |
|-------|--------|---------|-----|
| `durMicro` | 90ms | 0 | Micro-interactions (hover, focus) |
| `durShort` | 180ms | 0 | Pill expand/collapse, border transitions |
| `durMed` | 320ms | 0 | Card entrance, state transitions |
| `durLong` | 620ms | 0 | Orb summon bloom, dismissal |
| `springSummon` | 700ms, OutElastic(1.15, 0.32) | 200ms, OutCubic | Orb materialization |
| `springDismiss` | 400ms, InCubic | 150ms, InCubic | Orb dematerialization |
| `springCard` | 380ms, OutBack(1.12) | 180ms, OutCubic | Card entrance |

---

### Phase 2: Siri Orb — Exact Visual Anatomy (Week 1-2)

#### 2.1 SiriOrb.qml — Complete Rewrite
**File:** `ui_siri/qml/SiriOrb.qml`

**Current Issues:**
- Glass core uses hardcoded `rgba(22,18,59,0.88)` etc. instead of theme tokens
- Ribbon hues array references theme but not exact Siri values
- Halo gradient stops not feathered to 0% inside canvas bounds
- No true additive blending verification
- White-hot peak at centre needs to be more pronounced
- Glass rim at `r * 0.93` should be `r * 0.96` (thinner, crisper)
- Inner sheen position/size needs Apple calibration

**Target Visual Anatomy (per design mandate):**

```
┌─────────────────────────────────────────────────────────────┐
│ Canvas (orbSize × orbSize, e.g., 84×84 at 100% scale)      │
│                                                              │
│  1. OUTER AMBIENT HALO (outside clip)                       │
│     Radial gradient: center → r*1.55                        │
│     Stops: 0.00 → pri @ 0.22×energy                         │
│            0.35 → pri @ 0.05×energy                         │
│            0.70 → pri @ 0.01×energy                         │
│            1.00 → transparent (0.0) — NO HARD EDGE          │
│     Feathered to 0% well inside canvas (r*1.55 < w/2)      │
│                                                              │
│  2. CLIP TO CIRCLE (r = min(w,h)/2 * 0.92)                 │
│     ctx.arc(cx, cy, r, 0, 2π); ctx.clip()                  │
│                                                              │
│  3. DEEP TRANSLUCENT GLASS CORE (inside clip)              │
│     Radial gradient: center → r                             │
│     Stops: 0.00 → glassCore      (rgba(18,14,48,0.88))     │
│            0.45 → glassCoreMid   (rgba(10,18,56,0.92))     │
│            1.00 → glassCoreEdge  (rgba(24,10,52,0.88))     │
│     NEVER pitch black — deep sapphire-indigo translucency  │
│                                                              │
│  4. IRIDESCENT FLUID RIBBONS (additive "lighter")          │
│     6 ribbons, independent harmonic phases                 │
│     Hues: [iridA, iridB, iridC, iridD, iridA, iridB]       │
│     Each ribbon:                                            │
│       - Base angle = i × (2π/6)                             │
│       - Continuous rotation: phase + i×0.4                  │
│       - Voice-driven reach: 0.30 + 0.55×amp + 0.20×sin(t)  │
│       - Wave amplitude: 0.08 + 0.12×amp                     │
│       - Wave frequency: 3.0 + i×0.5                         │
│       - Gradient: hue@0.5 → white@0.8 → white@0.8 → hue@0.4│
│     Additive blending → white-hot peaks at intersections   │
│                                                              │
│  5. WHITE-HOT CENTRAL PEAK (source-over)                   │
│     Radial: center → r×0.15                                 │
│     Opacity: (0.5 + 0.5×sin(t×8)) × (0.6 + 0.4×amp)        │
│     Pulses at 8Hz, breathes with amplitude                 │
│                                                              │
│  6. GLASS RIM HIGHLIGHT (source-over)                      │
│     Stroke: r×0.96, 1.5px, rgba(255,255,255,0.28)          │
│     Crisp, razor-thin inner edge                           │
│                                                              │
│  7. INNER CRESCENT SHEEN (source-over)                     │
│     Radial: (cx-r×0.28, cy-r×0.28), radius r×0.42          │
│     Stops: 0.0 → rgba(255,255,255,0.20)                    │
│            0.45 → rgba(255,255,255,0.03)                   │
│            1.0 → transparent                                │
│     Soft, curved, tucked inside upper contour              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**State-Driven Colour/Behaviour Mapping:**

| State | Ribbon Hue Shift | Reach Base | Energy | Halo | Audio Cue |
|-------|------------------|------------|--------|------|-----------|
| **Summoning** | Full spectrum cycle | 0.95 → settle | 1.0 → 0.42 | Rapid bloom | `wake` earcon |
| **LISTENING (idle)** | Slow cyan→blue→violet drift | 0.30 + 0.20×sin(t) | 0.42 | Gentle pulse | — |
| **LISTENING (speaking)** | Reactive to `micAmplitude` | 0.30 + 0.55×amp | 0.42→1.0 | Expands with voice | — |
| **THINKING** | Inward draw, violet dominant | 0.25 (contracting) | 0.62 | Focused, tighter | `think` earcon |
| **PROCESSING** | Cohesive orbital weave | 0.20 | 0.62 | Steady | — |
| **SPEAKING** | Warm rhythmic breathing | 0.35 + 0.30×sin(t×1.5) | 1.0 | Conversational | — |
| **MUTED** | Dimmed, slow cyan only | 0.20 | 0.18 | Near-invisible | `mute` earcon |
| **ATTENTION** | Magenta/rose warm pulse | 0.45 | 0.75 | Rose halo | `confirm` earcon |
| **ERROR** | Micro-shake + magenta flash | 0.30 | 0.50 | Brief rose | `error` earcon |
| **Dismissing** | Reverse: contract + fade | 1.0 → 0.15 | 0.42 → 0 | Collapse | `done` earcon |

**Voice-Reactive Smoothing (Critical):**
```qml
// Current: Behavior on smoothAmplitude { NumberAnimation { duration: 80 } }
// Target: Custom spring-damper for frame-interpolated smoothness
property real smoothAmplitude: 0.0
property real _amplitudeVelocity: 0.0

function updateAmplitude(target) {
    // Critically damped spring: no overshoot, no lag
    const stiffness = 180.0
    const damping = 24.0
    const dt = 1/60
    const force = (target - smoothAmplitude) * stiffness
    _amplitudeVelocity = (_amplitudeVelocity + force * dt) * (1 - damping * dt)
    smoothAmplitude += _amplitudeVelocity * dt
    canvas.requestPaint()
}
// Called from bridge.setAmplitude() at audio callback rate (~30-60Hz)
```

**Performance:**
- Canvas repaint timer: 16ms (60 FPS) ONLY when `opacity > 0.01`
- Dormant: `timer.running = false` = 0% CPU
- All gradients pre-computed per frame (no allocation in hot path)
- `toCss()` helper caches parsed theme colours

---

### Phase 3: Glassmorphic Card System (Week 2)

#### 3.1 BaseGlassCard.qml — Authentic Apple Frosted Glass
**File:** `ui_siri/qml/cards/BaseGlassCard.qml`

**Target Visual Specification:**
```
┌─────────────────────────────────────────────────────────────┐
│ Card (340×minHeight, radius 18px @ 100% scale)             │
│                                                              │
│  SHADOW (behind, radius+2, rgba(0,0,0,0.55), opacity 0.35) │
│                                                              │
│  SURFACE (radius 18px)                                      │
│    Background: glassSurface (rgba(10,16,26,0.62))           │
│    Border: 1px glassBorder (rgba(255,255,255,0.08))         │
│    Inner border: 1px glassInnerRim inset 1px                │
│    Top sheen: height/2.4, gradient glassSpecular→transparent│
│    Severity rail: 3px left, rail{Info|Success|Warn|Error}  │
│                                                              │
│  CONTENT (inset 16px + 4px for rail)                        │
│    Header: title (Display, 13px, bold, letter-spacing 0.7) │
│             kindChip (if not "text") — pill, 18px radius   │
│    Body: text (Text, 11px, line-height 1.18, wrap)         │
│    Footer: metadata (11px, mid alpha)                       │
│    TTL hairline: bottom, 2px, rail colour @ 0.55           │
│    Dismiss: 18px circle, top-right on hover, red@0.22 bg   │
│                                                              │
│  ENTRANCE: translateY(18) → 0, opacity 0→1, scale 0.94→1   │
│            OutBack(1.12), 380ms, staggered 60ms per card   │
│  HOVER:  translateY(-2), border→hover, shadow↑             │
│  STACKED: opacity 0, scale 0.94 (compressed behind front)  │
└─────────────────────────────────────────────────────────────┘
```

**Key Changes from Current:**
- Replace hardcoded colours with theme tokens
- Add inner 1px highlight (`glassInnerRim`)
- Severity rail: 3px (not 2px), rounded caps, 0.85 opacity
- Top sheen: more pronounced, `glassSpecular` gradient
- Shadow: softer, larger, behind surface not inline
- Hover lift: -2px with border transition
- Tabular numerals for all metrics: use `Cascadia Mono` for numbers in body

#### 3.2 Typed Cards — Unified Aesthetics
**Files:** All 10 cards in `ui_siri/qml/cards/`

Each card must:
- Inherit BaseGlassCard spacing/typography exactly
- Use `font-variant-numeric: tabular-nums` equivalent for times/prices/metrics
- Iconography: restrained, 16-20px, `pri` colour
- ConfirmationCard: Apple-style pill buttons (28px height, 14px radius, `green`/`red` with pressed state)
- ControlPillCard: circular icon (46px), large value (26px bold), action pills (24px height)

---

### Phase 4: Type-to-Siri Pill & Live Activity Bar (Week 2-3)

#### 4.1 TypeToSiriPill.qml — Refined Frosted Glass
**File:** `ui_siri/qml/TypeToSiriPill.qml`

**Target:**
- Pill: 300×40px, radius 20px
- Surface: `glassSurface`, border `glassBorder`, inner highlight `glassInnerRim`
- Top sheen: `glassSpecular` gradient
- Mic glyph: 17px, `pri` colour, 0.85 opacity
- Input: `fontM`, `text` colour, placeholder `text@0.42`
- Focus: border→`glassBorderHover`, subtle glow `pri@0.20`
- Entrance: slide up + fade, `durShort`, OutCubic
- No `MultiEffect` — pure QML rectangles with gradients (perf + compatibility)

#### 4.2 LiveActivityBar.qml — Siri-Native Dynamic Island
**File:** `ui_siri/qml/LiveActivityBar.qml`

**Target:**
- Collapsed: 240×30px, radius 15px, `glassSurface`, border `glassBorder`
- Spinner: 12px, 2px stroke `dim@0.9`, rotating dot `pri` (1200ms linear)
- Text: `fontXS`, `text@0.9`, elided
- Expanded: rows with 4px indicator dot (`acc` when active), title `fontXS bold`, detail `fontXS mid@0.95`
- Progress hairline: 2px bottom, `acc` colour, animated width
- Cancel: 14px circle, hover `red@0.3`, `✕` glyph
- Hover expand: `durMed`, OutCubic
- Auto-collapse: 1400ms after mouse leave

---

### Phase 5: Setup Card & Inspector (Week 3)

#### 5.1 SetupCard.qml — First-Run Glass
**File:** `ui_siri/qml/SetupCard.qml`

- Same glass surface as BaseGlassCard
- Border pulse: `pri@0.25` ↔ `pri@0.65` (1600ms, infinite)
- Key field: monospace `fontS`, password toggle
- Submit button: pill, `pri@0.18` → `pri@0.34` pressed, `pri` text bold

#### 5.2 InspectorDrawer.qml & InspectorWindow.qml — Diagnostic Glass
**Files:** `ui_siri/qml/InspectorDrawer.qml`, `ui_siri/qml/InspectorWindow.qml`

- Tab chips: 30px height, 10px radius, `pri@0.18` active, `transparent` inactive
- Camera/arcade preview: `#050a0e` bg, `pri@0.28` border, aspect-fit
- Vision mode chips: pill, `pri@0.08` → `pri@0.24` hover
- Arcade game chips: `dimmer@0.9` → `acc2@0.16` hover, `acc2@0.24` current
- Settings toggles: 34×18px track, 14px thumb, animated
- Diagnostics: monospace `fontXS-2`, `dim` colour

---

### Phase 6: Window Orchestration & Lifecycle (Week 3-4)

#### 6.1 Controller.py — Spatial Precision & Zero Footprint
**File:** `ui_siri/controller.py`

**Current State:** Good foundation, needs precision tuning.

**Target Changes:**

1. **Dormant/Idle (Strict 0% Footprint):**
   - `start_hidden=True` already set ✓
   - `dwm_naked=True` on SiriWindow ✓ (applies `DWMWCP_DONOTROUND` + `DWMWA_COLOR_NONE`)
   - Verify: no 1px DWM border, no taskbar entry, no Alt-Tab entry
   - `_hidden = True` initially, window created but `hide()` called

2. **Bottom-Centre Anchoring (Exact):**
   ```python
   # Current: y = avail.y() + (avail.height() - stage_h) - int(70 * self._scale)
   # Target: Precise 68px above taskbar at 100% scale (72px visual center)
   stage_w = int(400 * self._scale)   # tighter around orb
   stage_h = int(320 * self._scale)
   x = avail.x() + (avail.width() - stage_w) // 2
   y = avail.y() + avail.height() - stage_h - int(68 * self._scale)
   ```

3. **Invocation (Summon) — Elastic Spring Bloom:**
   - `show()` → `siri.set_root_property("invoked", True)`
   - QML: `summoningAnimation` (700ms OutElastic 1.15/0.32 + 450ms opacity)
   - Audio: `audio.play("wake")` synchronized (start of animation)
   - Focus: `WindowDoesNotAcceptFocus` remains ✓

4. **Multi-Turn Continuity:**
   - State machine: `THINKING` → `SPEAKING` → `LISTENING` keeps `invoked=true`
   - No re-summon, orb transitions smoothly via `assistantState` prop

5. **Type-to-Siri:**
   - Click orb → `root.typing = true` → pill slides up
   - Submit → `typing = false`, pill slides down
   - Escape → cancel, pill slides down

6. **Auto-Dismissal Grace (Refined):**
   - Current: 4000ms after `LISTENING` + no cards + no activities
   - Target: 5000ms grace, **pauses** on:
     - Mouse hover over any card (`SnippetStack` sets `hovered=true`)
     - User scrolling card content
     - Typing in pill
     - Unresolved `ConfirmationCard` present
     - `LiveActivityBar` expanded
   - Resume grace when condition clears
   - Escape: immediate `hide()` → dismissal animation → `windowDismissed()` → `siri.hide()`

7. **Audio-Visual Sync:**
   - `audio_ux.py` earcons: `wake` (summon), `listen` (listening), `think` (thinking), `done` (dismiss), `confirm` (attention), `error` (error), `mute` (mute toggle)
   - Controller `_on_state` listener already triggers `audio.on_speaking_start/end`
   - Add explicit `audio.play("wake")` on `show()`, `audio.play("done")` on `hide()`

#### 6.2 Bridge.py — Voice-Reactive Amplitude Pipeline
**File:** `ui_siri/bridge.py`

**Current:** `setAmplitude(value)` with 0.01 threshold, emits `amplitudeChanged`

**Target:** High-frequency, frame-interpolated pipeline
```python
def setAmplitude(self, value: float) -> None:
    value = max(0.0, min(1.0, float(value or 0.0)))
    # No threshold — every sample matters for smoothness
    if abs(value - self._micAmplitude) < 0.001:
        return
    self._micAmplitude = value
    self.amplitudeChanged.emit()

# Called from audio callback at ~30-60Hz
# QML smoothAmplitude spring handles interpolation
```

---

### Phase 7: Win32 Polish & HiDPI (Week 4)

#### 7.1 Win32 Native — Zero Artifacts
**File:** `ui_siri/win32_native.py`

**Verify/Enhance:**
- `apply_dwm_naked()` — confirms `DWMWCP_DONOTROUND` + `DWMWA_COLOR_NONE` + `DWMNCRP_DISABLED`
- `make_click_through()` — `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`
- `exclude_from_capture()` — `WDA_EXCLUDEFROMCAPTURE` (Win10 19041+)
- `enable_per_monitor_dpi()` — `DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2` **before** `QGuiApplication`
- `enable_alpha_buffer()` — `QQuickWindow.setDefaultAlphaBuffer(True)` **before** `QGuiApplication`
- Multi-monitor: `enumerate_monitors()` returns virtual desktop coordinates
- HiDPI: `devicePixelRatio()` clamped 0.85–1.6, orb size scales correctly at 100/125/150/200%

---

### Phase 8: Test Verification & Regression Gate (Week 4-5)

#### 8.1 Test Suite Execution
**Command:**
```bash
set FRIDAY_GUI_TESTS=1 && python -m pytest tests/test_ui_contract.py tests/test_ui_siri_core.py tests/test_ui_siri_qt.py tests/test_ui_thread_safety.py tests/test_windows_bringup.py tests/test_live_session_fixes.py -v
```

**All Tests Must Pass:**
- `test_ui_contract.py` — Legacy API surface intact
- `test_ui_siri_core.py` — Pure-Python core (state machine, card mapper, event bus, win32, audio)
- `test_ui_siri_qt.py` — QML lint clean, models, image provider, controller boot
- `test_ui_thread_safety.py` — Dispatcher thread safety, bridge ingest under load
- `test_windows_bringup.py` — Windows 11 regressions (qml_dir, wake_word import, re import, DPI, capabilities)
- `test_live_session_fixes.py` — Model constants, speaking release, Ctrl+C handling

#### 8.2 Visual/Behavioural Audit Checklist

**Dormant State:**
- [ ] Zero visible pixels on desktop
- [ ] No window rectangle/outline (1px DWM border)
- [ ] No taskbar entry
- [ ] No Alt-Tab entry
- [ ] 0% CPU (orb paint timer stopped)

**Invocation:**
- [ ] Orb springs from bottom-centre (elastic OutElastic)
- [ ] `wake` earcon synchronized with bloom start
- [ ] No focus stolen from foreground app
- [ ] Window appears at correct monitor (active screen)

**Orb Visual (Every State):**
- [ ] Perfect circle, no clipping artifacts
- [ ] Deep translucent sapphire-indigo core (never black)
- [ ] 6 additive ribbons, white-hot intersections
- [ ] Feathered halo fading to 0% inside canvas
- [ ] 1.5px crisp inner glass rim
- [ ] Soft upper-left crescent sheen
- [ ] State colours: cyan (listen), violet (think), rose (attention), magenta (speak), dim (mute)
- [ ] Voice-reactive: smooth, frame-interpolated, no stepping

**Cards & Stack:**
- [ ] Authentic frosted glass (dark vibrancy)
- [ ] Hairline border + inner highlight
- [ ] Severity rail (3px, correct colours)
- [ ] Spring entrance (OutBack 1.12, staggered)
- [ ] Hover lift (-2px, border transition)
- [ ] Tabular numerals in metrics
- [ ] TTL hairline bottom
- [ ] Dismiss on hover (✕)

**Type-to-Siri Pill:**
- [ ] Frosted glass pill, correct radius
- [ ] Mic glyph, placeholder, focus border
- [ ] Smooth slide up/down

**Live Activity Bar:**
- [ ] Collapsed pill with spinner
- [ ] Hover expand with rows
- [ ] Progress hairline animated
- [ ] Cancel buttons work

**Dismissal:**
- [ ] Escape: immediate reverse spring + fade
- [ ] Auto: 5000ms grace (pauses on interaction)
- [ ] `done` earcon synchronized
- [ ] Window hides after animation (`windowDismissed`)

**HiDPI/Multi-Monitor:**
- [ ] 100%, 125%, 150%, 200% scaling — crisp, no blur
- [ ] Multi-monitor — follows active screen
- [ ] No DWM border leakage at any scale

---

## 🔧 File Modification Summary

| File | Scope | Risk |
|------|-------|------|
| `ui_siri/qml/Theme.qml` | **Complete palette rewrite** — exact Apple Siri colours, glass tokens, typography, motion | Low (data only) |
| `ui_siri/qml/SiriOrb.qml` | **Complete rewrite** — Canvas anatomy, additive ribbons, state choreography, voice-reactive spring | **High** (core visual) |
| `ui_siri/qml/SiriWindow.qml` | Layout tuning, margins, stage sizing | Low |
| `ui_siri/qml/SnippetStack.qml` | Spacing, maxVisible, rail styling | Low |
| `ui_siri/qml/cards/BaseGlassCard.qml` | **Major revision** — glass tokens, inner rim, sheen, shadow, hover, tabular numerals | Medium |
| `ui_siri/qml/cards/*.qml` (10 files) | Inherit BaseGlassCard, use tabular numerals for metrics | Low |
| `ui_siri/qml/TypeToSiriPill.qml` | Frosted glass, remove MultiEffect, gradients only | Low |
| `ui_siri/qml/LiveActivityBar.qml` | Siri-native styling, progress hairline | Low |
| `ui_siri/qml/SetupCard.qml` | Glass aesthetic, border pulse | Low |
| `ui_siri/qml/InspectorDrawer.qml` | Tab chips, preview frames, settings toggles | Low |
| `ui_siri/qml/InspectorWindow.qml` | Props binding | Low |
| `ui_siri/controller.py` | Positioning (68px), grace pause logic, audio sync, multi-monitor | Medium |
| `ui_siri/bridge.py` | Amplitude pipeline (remove threshold) | Low |
| `ui_siri/state_machine.py` | No changes needed (solid) | — |
| `ui_siri/themes.py` | Sync with Theme.qml palette | Low |
| `ui_siri/win32_native.py` | Verify dwm_naked, DPI, alpha buffer | Low |
| `ui_siri/audio_ux.py` | Verify earcon timing | Low |
| `ui.py`, `main.py`, `actions/*` | **NO CHANGES** — regression boundary | — |
| `tests/*` | **NO CHANGES** — verification gate | — |

---

## ⚠️ Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Orb Canvas rewrite breaks on some GPUs | Canvas 2D only, no shaders; fallback to software rasterization via `renderTarget: Canvas.FramebufferObject` |
| Additive blending (`lighter`) not supported | Qt Canvas 2D supports `globalCompositeOperation = "lighter"` on all Windows Qt 6.x builds |
| Theme token mismatch Python↔QML | Single source: `themes.py` → `build_theme()` → `Theme.apply()` — keep in sync |
| Voice-reactive amplitude too jittery | Critically damped spring in QML (`stiffness=180, damping=24`) + 60 FPS canvas |
| Auto-dismissal grace too aggressive | Pause conditions: hover, scroll, typing, confirmation, activity expanded |
| HiDPI blur at 150%/200% | `devicePixelRatio` clamp, `QQuickWindow.setDefaultAlphaBuffer(True)`, per-monitor DPI v2 |
| QML lint warnings | Every file lints clean; dynamic models use delegate-level property capture pattern |

---

## ✅ Definition of Done

1. **All 6 test suites pass** with `FRIDAY_GUI_TESTS=1` in single pytest run
2. **Zero QML lint warnings** across all 22 `.qml` files
3. **Zero runtime console errors** (no invalid gradients, anchor warnings, focus spam)
4. **Visual audit passes** all 37 checklist items above
5. **No regressions** in: Gemini Live audio, wake word, system tray, hotkeys, tool execution, memory writes
6. **Performance:** 60+ FPS orb animation, 0% CPU dormant, <50MB RAM overhead

---

## 📅 Estimated Timeline

| Week | Focus |
|------|-------|
| 1 | Theme.qml palette, SiriOrb.qml Canvas rewrite (core anatomy) |
| 2 | SiriOrb.qml state choreography + voice-reactive spring; BaseGlassCard.qml + typed cards |
| 3 | TypeToSiriPill, LiveActivityBar, SetupCard, Inspector — glass unification |
| 4 | Controller positioning, dismissal grace, audio-visual sync, Win32 verification |
| 5 | Full test suite execution, visual audit, bug fixes, regression gate |

**Total: 5 weeks** for production-grade 1:1 replica with zero regressions.