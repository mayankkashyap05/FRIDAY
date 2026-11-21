pragma Singleton
import QtQuick

/*
    FRIDAY adaptive design-token singleton.

    Registered from Python as `Theme` (see controller.py). All colours are
    strings so they can be driven either by the built-in palettes below or by
    the Python-side theme table (which mirrors the legacy THEMES dict so the
    four classic looks survive the migration).

    Reduced-motion and transparency are hard constraints here: components read
    `Theme.motion` / `Theme.surfaceAlpha` and degrade to static, opaque
    rendering instead of branching inside every animation.
*/
QtObject {
    id: theme

    // ── palette identity ────────────────────────────────────────────────────
    property string name: "cyber"

    // ── core surfaces ───────────────────────────────────────────────────────
    property color bg: "#000000"
    property color panel: "#010c10"
    property color hdr: "#00080d"
    property color input: "#000d12"

    // ── accents ─────────────────────────────────────────────────────────────
    property color pri: "#00d4ff"
    property color mid: "#007a99"
    property color dim: "#003344"
    property color dimmer: "#001520"
    property color acc: "#ff6600"
    property color acc2: "#ffcc00"
    property color text: "#8ffcff"
    property color textStrong: "#ffffff"
    property color green: "#00ff88"
    property color red: "#ff3333"
    property color mutcol: "#ff3366"
    property color inpBorder: "#003344"

    // Iridescent sweep used by the perimeter glow & orb.
    property color iridA: "#00d4ff"
    property color iridB: "#7d5cff"
    property color iridC: "#ff4fd8"
    property color iridD: "#ffcc00"

    // ── accessibility / system state ────────────────────────────────────────
    property bool lightMode: false
    property bool reducedMotion: false
    property bool transparency: true
    property real uiScale: 1.0

    // ── derived metrics ─────────────────────────────────────────────────────
    readonly property int radiusPill: Math.round(26 * uiScale)
    readonly property int radiusCard: Math.round(20 * uiScale)
    readonly property int radiusChip: Math.round(10 * uiScale)
    readonly property int gap: Math.round(10 * uiScale)
    readonly property int pad: Math.round(16 * uiScale)
    readonly property int fontXS: Math.max(9, Math.round(9 * uiScale))
    readonly property int fontS: Math.max(10, Math.round(11 * uiScale))
    readonly property int fontM: Math.max(12, Math.round(13 * uiScale))
    readonly property int fontL: Math.max(14, Math.round(17 * uiScale))
    readonly property int fontXL: Math.max(18, Math.round(22 * uiScale))

    // Motion: a single scalar multiplier means every animation in the app
    // obeys the OS "show animations" setting without extra branching.
    readonly property int durMicro: reducedMotion ? 0 : 90
    readonly property int durShort: reducedMotion ? 0 : 180
    readonly property int durMed: reducedMotion ? 0 : 320
    readonly property int durLong: reducedMotion ? 0 : 620
    readonly property real motion: reducedMotion ? 0.0 : 1.0

    // Frosted-glass strength; 0 when the user disabled transparency.
    readonly property real glass: transparency ? 1.0 : 0.0
    readonly property real surfaceAlpha: transparency ? 0.62 : 0.97
    readonly property real veilAlpha: transparency ? 0.38 : 0.92
    // Opaque-enough background for high-contrast text over the blurred surface.
    readonly property color onGlass: lightMode ? "#0b1116" : "#f2fbff"

    // ── shadow / glow tokens ────────────────────────────────────────────────
    readonly property color shadowColor: lightMode ? "#33000000" : "#cc000000"
    readonly property real glowStrength: 1.0

    // ── helpers ─────────────────────────────────────────────────────────────
    function withAlpha(c, a) {
        return Qt.rgba(c.r, c.g, c.b, a)
    }

    // Applies a Python-supplied theme dict (legacy THEMES parity).
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
            "irid_d": "iridD", "iridD": "iridD"
        }
        for (var key in map) {
            if (data[key] !== undefined && data[key] !== null)
                theme[map[key]] = data[key]
        }
        if (data.lightMode !== undefined) theme.lightMode = !!data.lightMode
        if (data.reducedMotion !== undefined) theme.reducedMotion = !!data.reducedMotion
        if (data.transparency !== undefined) theme.transparency = !!data.transparency
        if (data.uiScale !== undefined) theme.uiScale = data.uiScale
    }
}
