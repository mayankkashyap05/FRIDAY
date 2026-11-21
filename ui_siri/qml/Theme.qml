pragma Singleton
import QtQuick

/*
    Siri adaptive design-token singleton.

    Registered from Python as `Theme` (see controller.py). All colours are
    strings so they can be driven either by the built-in Siri palette below or
    by the Python-side theme table (the sole `siri` palette now).

    The palette is Apple's Siri optical colour science: a luminous electric
    aqua/cyan primary, a deep sapphire/cobalt mid, radiant magenta/rose for
    attention and confirmation, iridescent violet for reasoning, and pure
    additive white for the white-hot ribbon intersections. The glass tokens
    describe one shared material — a dark, subtly translucent frosted surface
    with a hairline specular border, an inner rim highlight and a top sheen —
    so every surface in the product reads as the same optical object.

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
    property color panel: "#0A0E14"
    property color hdr: "#060A0F"
    property color input: "#0E141C"

    // ── Siri accents ────────────────────────────────────────────────────────
    property color pri: "#06F7FF"   // electric aqua / cyan — primary ribbon
    property color mid: "#1E5FFF"   // deep sapphire / cobalt — ribbon mid
    property color dim: "#1A2430"
    property color dimmer: "#05080C"
    property color acc: "#FF2D9F"   // radiant magenta / rose — attention
    property color acc2: "#B030FF"  // iridescent violet / amethyst — reasoning
    property color text: "#FFFFFF"  // pure additive white for labels
    property color textStrong: "#FFFFFF"
    property color green: "#34D399"
    property color red: "#FB7185"
    property color mutcol: "#F43F5E"
    property color inpBorder: "#2A3644"

    // Iridescent ribbon hues that drift inside the orb (cyan → sapphire →
    // magenta → violet → white-hot at intersections).
    property color iridA: "#06F7FF"
    property color iridB: "#1E5FFF"
    property color iridC: "#FF2D9F"
    property color iridD: "#B030FF"
    property color iridE: "#FFFFFF"

    // ── orb glass core ──────────────────────────────────────────────────────
    // Deep, translucent sapphire-indigo liquid crystal. Never pitch black:
    // the ribbons must read as suspended *inside* a filled optical sphere.
    property color orbGlassA: Qt.rgba(0.094, 0.063, 0.290, 0.92)  // centre
    property color orbGlassB: Qt.rgba(0.043, 0.086, 0.267, 0.94)  // mid
    property color orbGlassC: Qt.rgba(0.129, 0.043, 0.243, 0.90)  // edge

    // ── glass material ──────────────────────────────────────────────────────
    // One material for cards, the pill, the capsule and the drawer.
    property color glassSurface: Qt.rgba(0.039, 0.063, 0.102, 0.62)
    property color glassBorder: Qt.rgba(1, 1, 1, 0.085)
    property color glassBorderHover: Qt.rgba(0.024, 0.969, 1.0, 0.45)
    property color glassSpecular: Qt.rgba(1, 1, 1, 0.55)
    property color glassInnerRim: Qt.rgba(1, 1, 1, 0.16)

    // ── severity rails ──────────────────────────────────────────────────────
    property color railInfo: "#06F7FF"
    property color railSuccess: "#34D399"
    property color railWarn: "#B030FF"
    property color railError: "#FB7185"
    property color railSecurity: "#FF2D9F"

    // ── accessibility / system state ────────────────────────────────────────
    property bool lightMode: false
    property bool reducedMotion: false
    property bool transparency: true
    property real uiScale: 1.0

    // ── typography ──────────────────────────────────────────────────────────
    // Segoe UI Variable is the Windows 11 optical family: "Display" for
    // titles (tighter, larger optical size) and "Text" for body.
    readonly property string fontDisplay: "Segoe UI Variable Display, Segoe UI, sans-serif"
    readonly property string fontText: "Segoe UI Variable Text, Segoe UI, sans-serif"
    // Metrics, times and prices: digit-uniform so columns never jitter.
    readonly property string fontNumeric: "Segoe UI Variable Text, Segoe UI, sans-serif"

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
    readonly property int fontDisplayXL: Math.max(24, Math.round(32 * uiScale))

    // Tabular numerals without a monospace face: `Text.font.features` maps
    // straight onto the OpenType `tnum` feature, so every metric column is
    // digit-uniform while keeping Segoe's proportional letterforms.
    readonly property var tabularFeatures: ({ "tnum": 1 })

    // Motion: a single scalar multiplier means every animation in the app
    // obeys the OS "show animations" setting without extra branching.
    readonly property int durMicro: reducedMotion ? 0 : 90
    readonly property int durShort: reducedMotion ? 0 : 180
    readonly property int durMed: reducedMotion ? 0 : 320
    readonly property int durLong: reducedMotion ? 0 : 620
    readonly property int durSummon: reducedMotion ? 200 : 700
    readonly property int durDismiss: reducedMotion ? 150 : 400
    readonly property int durCard: reducedMotion ? 160 : 380
    readonly property real motion: reducedMotion ? 0.0 : 1.0

    // Frosted-glass strength; 0 when the user disabled transparency.
    readonly property real glass: transparency ? 1.0 : 0.0
    readonly property real surfaceAlpha: transparency ? 0.62 : 0.97
    readonly property real veilAlpha: transparency ? 0.42 : 0.92
    // Opaque-enough background for high-contrast text over the blurred surface.
    readonly property color textOnGlass: lightMode ? "#0b1116" : "#f2fbff"

    // ── shadow / glow tokens ────────────────────────────────────────────────
    readonly property color shadowColor: lightMode ? "#33000000" : "#a6000000"
    readonly property real glowStrength: 1.0

    // ── helpers ─────────────────────────────────────────────────────────────
    function withAlpha(c, a) {
        return Qt.rgba(c.r, c.g, c.b, a)
    }

    // Applies a Python-supplied theme dict (the `siri` palette).
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
            "iridE": "iridE",
            "glass_surface": "glassSurface", "glassSurface": "glassSurface",
            "glass_border": "glassBorder", "glassBorder": "glassBorder",
            "glass_border_hover": "glassBorderHover",
            "glassBorderHover": "glassBorderHover",
            "glass_specular": "glassSpecular", "glassSpecular": "glassSpecular",
            "glass_inner_rim": "glassInnerRim", "glassInnerRim": "glassInnerRim",
            "rail_info": "railInfo", "rail_success": "railSuccess",
            "rail_warn": "railWarn", "rail_error": "railError",
            "rail_security": "railSecurity",
            "orb_glass_a": "orbGlassA", "orb_glass_b": "orbGlassB",
            "orb_glass_c": "orbGlassC"
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
