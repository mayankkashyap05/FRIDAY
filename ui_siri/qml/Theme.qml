pragma Singleton
import QtQuick

/*
    Siri adaptive design-token singleton.

    Registered from Python as `Theme` (see controller.py). All colours are
    strings so they can be driven either by the built-in Siri palette below or
    by the Python-side theme table (the sole ``siri`` palette now).

    Reduced-motion and transparency are hard constraints here: components read
    `Theme.motion` / `Theme.surfaceAlpha` and degrade to static, opaque
    rendering instead of branching inside every animation.
*/
QtObject {
    id: theme

    // ── palette identity ────────────────────────────────────────────────────
    property string name: "siri"

    // ── core surfaces (dark frosted glass) ──────────────────────────────────
    property color bg: "#000000"
    property color panel: "#0C141C"
    property color hdr: "#090E13"
    property color input: "#0F1720"

    // ── Siri accents ────────────────────────────────────────────────────────
    property color pri: "#22D3EE"   // cyan / aqua
    property color mid: "#3B82F6"   // sapphire / cobalt
    property color dim: "#1E293B"
    property color dimmer: "#070B10"
    property color acc: "#E879F9"   // magenta / rose
    property color acc2: "#A855F7"  // iridescent violet
    property color text: "#F8FAFC"  // near-white additive
    property color textStrong: "#FFFFFF"
    property color green: "#34D399"
    property color red: "#FB7185"
    property color mutcol: "#F43F5E"
    property color inpBorder: "#334155"

    // Iridescent ribbon hues that drift inside the orb (cyan → blue → magenta
    // → violet → white-hot at intersections).
    property color iridA: "#22D3EE"
    property color iridB: "#3B82F6"
    property color iridC: "#E879F9"
    property color iridD: "#A855F7"
    property color iridE: "#FFFFFF"

    // ── accessibility / system state ────────────────────────────────────────
    property bool lightMode: false
    property bool reducedMotion: false
    property bool transparency: true
    property real uiScale: 1.0

    // ── derived metrics ─────────────────────────────────────────────────────
    readonly property int radiusPill: Math.round(24 * uiScale)
    readonly property int radiusCard: Math.round(18 * uiScale)
    readonly property int radiusChip: Math.round(10 * uiScale)
    readonly property int gap: Math.round(10 * uiScale)
    readonly property int pad: Math.round(16 * uiScale)
    readonly property int fontXS: Math.max(9, Math.round(9 * uiScale))
    readonly property int fontS: Math.max(10, Math.round(11 * uiScale))
    readonly property int fontM: Math.max(12, Math.round(13 * uiScale))
    readonly property int fontL: Math.max(14, Math.round(17 * uiScale))
    readonly property int fontXL: Math.max(20, Math.round(26 * uiScale))

    // Motion: a single scalar multiplier means every animation in the app
    // obeys the OS "show animations" setting without extra branching.
    readonly property int durMicro: reducedMotion ? 0 : 90
    readonly property int durShort: reducedMotion ? 0 : 180
    readonly property int durMed: reducedMotion ? 0 : 320
    readonly property int durLong: reducedMotion ? 0 : 620
    readonly property real motion: reducedMotion ? 0.0 : 1.0

    // Frosted-glass strength; 0 when the user disabled transparency.
    readonly property real glass: transparency ? 1.0 : 0.0
    readonly property real surfaceAlpha: transparency ? 0.60 : 0.97
    readonly property real veilAlpha: transparency ? 0.42 : 0.92
    // Opaque-enough background for high-contrast text over the blurred surface.
    readonly property color onGlass: lightMode ? "#0b1116" : "#f2fbff"

    // ── shadow / glow tokens ────────────────────────────────────────────────
    readonly property color shadowColor: lightMode ? "#33000000" : "#cc000000"
    readonly property real glowStrength: 1.0

    // ── helpers ─────────────────────────────────────────────────────────────
    function withAlpha(c, a) {
        return Qt.rgba(c.r, c.g, c.b, a)
    }

    // Applies a Python-supplied theme dict (the ``siri`` palette).
    function apply(data) {
        if (!data)
            return
        var map = {
            "name": "name", "bg": "bg", "panel": "panel", "hdr": "hdr",
            "input": "input", "pri": "pri", "mid": "mid", "dim": "dim",
            "dimmer": "dimmer", "acc": "acc", "acc2": "acc2", "text": "text",
            "green": "green", "red": "red", "mutcol": "mutcol",
            "inp_border": "inpBorder", "inpBorder": "inpBorder",
            "irid_a": "iridA", "iridA": "iridA", "irid_b": "iridB",
            "iridB": "iridB", "irid_c": "iridC", "iridC": "iridC",
            "irid_d": "iridD", "iridD": "iridD", "irid_e": "iridE",
            "iridE": "iridE"
        }
        for (var key in map) {
            if (data[key] !== undefined && data[key] !== null)
                theme[map[key]] = data[key]
        }
        // Siri is dark-only: the palette never follows a light-mode toggle.
        if (data.lightMode !== undefined) theme.lightMode = false
        if (data.reducedMotion !== undefined) theme.reducedMotion = !!data.reducedMotion
        if (data.transparency !== undefined) theme.transparency = !!data.transparency
        if (data.uiScale !== undefined) theme.uiScale = data.uiScale
    }
}
