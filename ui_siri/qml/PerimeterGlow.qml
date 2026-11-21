import QtQuick
pragma ComponentBehavior: Bound
import QtQuick.Shapes

/*
    Layer 1 — fullscreen, click-through iridescent edge glow.

    Painted with Shape arcs rather than a ShaderEffect because Qt 6 requires
    pre-compiled .qsb shaders and `pyside6-qsb` is not present in the audited
    environment (see docs/UI_AUDIT_CONTRACT.md §6).

    The window itself is made click-through by the controller via
    `SetWindowDisplayAffinity` + `WS_EX_TRANSPARENT`, and is excluded from
    screen capture so it never shows up in Teams/OBS/recordings.

    Intensity is driven by `energy` (0..1) from the state machine so the ring
    visibly "breathes" while FRIDAY speaks and dims when muted.
*/
Item {
    id: root
    anchors.fill: parent

    // 0 = idle … 1 = speaking
    property real energy: 0.35
    // "BOOTING" | "LISTENING" | "THINKING" | "PROCESSING" | "SPEAKING" | "MUTED"
    property string assistantState: "LISTENING"
    property bool muted: false
    property bool dnd: false
    // Animate only when the OS allows animation.
    property real motion: 1.0

    readonly property color live: muted ? Theme.mutcol
                                        : (assistantState === "THINKING" || assistantState === "PROCESSING"
                                           ? Theme.acc2 : Theme.pri)
    readonly property real edge: Math.round(3 * Theme.uiScale)
    readonly property real inset: Math.round(6 * Theme.uiScale)

    opacity: dnd ? 0.35 : 1.0
    Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

    // ── breathing envelope ────────────────────────────────────────────────
    property real breath: 0.5
    SequentialAnimation on breath {
        running: root.motion > 0
        loops: Animation.Infinite
        NumberAnimation { to: 1.0; duration: Math.max(1, 2200 - root.energy * 1500)
                          easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.35; duration: Math.max(1, 2200 - root.energy * 1500)
                          easing.type: Easing.InOutSine }
    }

    readonly property real pulse: 0.45 + 0.55 * (root.motion > 0 ? root.breath : 0.8)

    // ── iridescent rim: four offset arcs that rotate at different rates ────
    Shape {
        id: rim
        anchors.fill: parent
        antialiasing: true
        layer.enabled: false

        ShapePath {
            strokeColor: Theme.withAlpha(root.live,
                                         0.10 + 0.55 * root.energy * root.pulse)
            strokeWidth: root.edge
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: rim.width / 2; centerY: rim.height / 2
                radiusX: Math.min(rim.width, rim.height) / 2 - root.inset
                radiusY: Math.min(rim.width, rim.height) / 2 - root.inset
                startAngle: root.spinA; sweepAngle: 96
            }
        }
        ShapePath {
            strokeColor: Theme.withAlpha(Theme.iridB, 0.08 + 0.42 * root.energy)
            strokeWidth: root.edge * 1.4
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: rim.width / 2; centerY: rim.height / 2
                radiusX: Math.min(rim.width, rim.height) / 2 - root.inset
                radiusY: Math.min(rim.width, rim.height) / 2 - root.inset
                startAngle: root.spinB; sweepAngle: 124
            }
        }
        ShapePath {
            strokeColor: Theme.withAlpha(Theme.iridC, 0.06 + 0.34 * root.energy)
            strokeWidth: root.edge * 0.8
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: rim.width / 2; centerY: rim.height / 2
                radiusX: Math.min(rim.width, rim.height) / 2 - root.inset * 2
                radiusY: Math.min(rim.width, rim.height) / 2 - root.inset * 2
                startAngle: root.spinC; sweepAngle: 70
            }
        }
    }

    property real spinA: 0
    property real spinB: 120
    property real spinC: 240

    // Speech makes the sweep ~4x faster; this is the single strongest
    // "FRIDAY is talking" cue at the edges of the screen.
    readonly property real spinSpeed: root.motion <= 0 ? 0
                                      : (root.assistantState === "SPEAKING" ? 2.4
                                         : root.assistantState === "THINKING" ? 1.1 : 0.35)

    NumberAnimation on spinA {
        running: root.motion > 0
        loops: Animation.Infinite
        from: 0; to: 360
        duration: Math.max(1, 26000 / Math.max(0.001, root.spinSpeed))
    }
    NumberAnimation on spinB {
        running: root.motion > 0
        loops: Animation.Infinite
        from: 120; to: -240
        duration: Math.max(1, 34000 / Math.max(0.001, root.spinSpeed))
    }
    NumberAnimation on spinC {
        running: root.motion > 0
        loops: Animation.Infinite
        from: 240; to: 600
        duration: Math.max(1, 44000 / Math.max(0.001, root.spinSpeed))
    }

    // ── corner vignettes: radial light that intensifies with energy ────────
    Repeater {
        model: [
            { ax: 0.0, ay: 0.0 }, { ax: 1.0, ay: 0.0 },
            { ax: 0.0, ay: 1.0 }, { ax: 1.0, ay: 1.0 }
        ]
        delegate: Rectangle {
            required property var modelData
            required property int index
            width: Math.round(320 * Theme.uiScale)
            height: width
            radius: width / 2
            x: modelData.ax * root.width - width / 2
            y: modelData.ay * root.height - height / 2
            opacity: (0.05 + 0.20 * root.energy) * root.pulse
            gradient: Gradient {
                orientation: Gradient.RadialGradient
                GradientStop { position: 0.0; color: Theme.withAlpha(root.live, 0.9) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // ── muted indicator: a single slow red dot in the top-right ────────────
    Rectangle {
        visible: root.muted
        width: Math.round(7 * Theme.uiScale)
        height: width
        radius: width / 2
        color: Theme.mutcol
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.rightMargin: Math.round(16 * Theme.uiScale)
        anchors.topMargin: Math.round(16 * Theme.uiScale)
        SequentialAnimation on opacity {
            running: root.motion > 0 && root.muted
            loops: Animation.Infinite
            NumberAnimation { to: 0.25; duration: 1400 }
            NumberAnimation { to: 1.0; duration: 1400 }
        }
    }
}
