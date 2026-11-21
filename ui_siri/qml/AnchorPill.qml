pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Effects

/*
    Layer 2 — the floating voice orb that morphs into a type-to-FRIDAY pill.

    States
    ------
    orb      : collapsed, always-on-top, drag anywhere. Click = talk.
    listen   : expanded, live waveform replaces the orb face.
    type     : keyboard-first input, Enter submits, Esc collapses.
    think    : orb spins, "thinking" shimmer, no input.
    speak    : orb pulses with the energy scalar.

    Design notes
    ------------
    * The window is sized to `expanded` in the controller; this component only
      animates its *visual* content so the transparent window never resizes on
      every keystroke (that causes visible flicker on Windows).
    * MultiEffect provides the blur/shadow "frosted" look; it is Qt >= 6.5 and
      is runtime-gated by `Theme.transparency`.
    * No ShaderEffect: see docs/UI_AUDIT_CONTRACT.md §6.
*/
Item {
    id: pill

    required property real energy          // 0..1 from the state machine
    required property string assistantState // BOOTING|LISTENING|THINKING|...|MUTED
    required property string statusLabel
    required property bool muted
    required property bool silentMode
    required property bool connected
    required property string connectionLabel
    required property string personaBadge
    required property string clock
    required property bool reducedMotion

    signal submit(string text)
    signal requestMute()
    signal requestSilent()
    signal requestCancel()
    signal requestInspector()

    property bool expanded: false
    property bool typing: false
    property bool dragging: false

    readonly property bool thinking: assistantState === "THINKING"
                                     || assistantState === "PROCESSING"
    readonly property bool speaking: assistantState === "SPEAKING"
    readonly property color accent: muted ? Theme.mutcol
                                  : thinking ? Theme.acc2
                                  : speaking ? Theme.acc : Theme.pri
    readonly property real orbSize: Math.round(60 * Theme.uiScale)

    implicitWidth: expanded ? Math.round(430 * Theme.uiScale) : orbSize + 26
    implicitHeight: orbSize + 26

    // ── shell ───────────────────────────────────────────────────────────────
    Item {
        id: shell
        anchors.fill: parent
        anchors.margins: 6

        // Soft outer glow behind the whole pill
        Rectangle {
            id: glow
            anchors.fill: body
            anchors.margins: -8
            radius: height / 2
            color: Theme.withAlpha(pill.accent, 0.20)
            opacity: 0.35 + 0.65 * pill.energy
            visible: Theme.glass > 0
            Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        }

        // Frosted body
        Rectangle {
            id: body
            anchors.centerIn: parent
            width: pill.expanded ? parent.width : (pill.orbSize + 14)
            height: pill.orbSize + 14
            radius: height / 2
            color: Theme.withAlpha(Theme.panel, Theme.veilAlpha)
            border.width: 1
            border.color: Theme.withAlpha(pill.accent,
                                          pill.assistantState === "LISTENING" ? 0.55 : 0.30)

            Behavior on width {
                NumberAnimation {
                    duration: Theme.durMed
                    easing.type: Easing.OutCubic
                }
            }
            Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

            // Frosted backdrop: MultiEffect renders the body through a
            // layer and blurs it, which is what makes the pill read as glass
            // over a desktop wallpaper. Gated on Theme.transparency so the
            // effect costs nothing when the user has transparency off.
            layer.enabled: Theme.transparency
            MultiEffect {
                source: body
                anchors.fill: body
                visible: Theme.transparency
                blurEnabled: true
                blur: 0.6
                blurMax: 24
                saturation: 0.1
            }

            // A single inner highlight makes it read as glass, not a flat pill.
            Rectangle {
                anchors.fill: parent
                anchors.margins: 1
                radius: parent.radius
                color: "transparent"
                border.width: 1
                border.color: Theme.withAlpha(Theme.textStrong, 0.06)
            }
        }

        // ── collapsed: the orb ───────────────────────────────────────────────
        Item {
            id: orb
            anchors.verticalCenter: parent.verticalCenter
            x: 7
            width: pill.orbSize
            height: pill.orbSize
            visible: opacity > 0.01
            opacity: pill.expanded ? 0.0 : 1.0
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }

            // concentric energy rings
            Repeater {
                model: 3
                delegate: Rectangle {
                    required property int index
                    anchors.centerIn: parent
                    width: pill.orbSize * (1.0 - index * 0.20)
                    height: width
                    radius: width / 2
                    color: "transparent"
                    border.width: Math.max(1, 2 - index)
                    border.color: Theme.withAlpha(
                        pill.accent,
                        (0.55 - index * 0.13) * (0.45 + 0.55 * pill.energy))
                    scale: 1.0 + 0.06 * Math.sin(pill.phase + index * 1.1)
                                * (pill.reducedMotion ? 0 : 1)
                }
            }

            // core
            Rectangle {
                anchors.centerIn: parent
                width: pill.orbSize * 0.62
                height: width
                radius: width / 2
                gradient: Gradient {
                    orientation: Gradient.RadialGradient
                    GradientStop { position: 0.0
                                   color: Theme.withAlpha(pill.accent, 0.96) }
                    GradientStop { position: 0.62
                                   color: Theme.withAlpha(pill.accent, 0.42) }
                    GradientStop { position: 1.0
                                   color: Theme.withAlpha(pill.accent, 0.06) }
                }
                scale: 0.92 + 0.16 * pill.energy

                // glyph
                Text {
                    anchors.centerIn: parent
                    text: pill.muted ? "⊘" : pill.thinking ? "◈" : pill.speaking ? "◉" : "●"
                    color: Theme.bg
                    font.pixelSize: Math.round(pill.orbSize * 0.34)
                    font.bold: true
                    opacity: 0.9
                }
            }
        }

        // ── expanded: content ────────────────────────────────────────────────
        Item {
            id: content
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 10
            anchors.rightMargin: 12
            height: pill.orbSize
            visible: opacity > 0.01
            opacity: pill.expanded ? 1.0 : 0.0
            Behavior on opacity {
                NumberAnimation { duration: Theme.durShort; easing.type: Easing.OutCubic }
            }

            Row {
                anchors.fill: parent
                spacing: 10

                // mini orb / live meter
                Item {
                    width: pill.orbSize * 0.72
                    height: width
                    anchors.verticalCenter: parent.verticalCenter

                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width * 0.8
                        height: width
                        radius: width / 2
                        color: Theme.withAlpha(pill.accent, 0.22 + 0.5 * pill.energy)
                    }

                    // waveform bars appear while listening/speaking
                    Row {
                        anchors.centerIn: parent
                        spacing: 2
                        visible: pill.speaking || pill.assistantState === "LISTENING"
                        Repeater {
                            model: 5
                            delegate: Rectangle {
                                required property int index
                                width: 2
                                height: 4 + 10 * Math.abs(
                                    Math.sin(pill.phase * (1.2 + index * 0.3) + index))
                                radius: 1
                                color: Theme.textStrong
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }

                // text input (type mode)
                Item {
                    width: parent.width - pill.orbSize * 0.72 - controls.width - 20
                    height: parent.height
                    anchors.verticalCenter: parent.verticalCenter
                    visible: pill.typing

                    TextInput {
                        id: input
                        anchors.fill: parent
                        anchors.rightMargin: 6
                        verticalAlignment: TextInput.AlignVCenter
                        color: Theme.text
                        selectionColor: Theme.withAlpha(Theme.pri, 0.5)
                        selectedTextColor: Theme.bg
                        font.pixelSize: Theme.fontM
                        font.family: "Segoe UI Variable Text, Segoe UI, Inter, sans-serif"
                        clip: true
                        focus: pill.typing
                        cursorVisible: pill.typing && !pill.reducedMotion
                        onAccepted: {
                            if (text.trim().length > 0)
                                pill.submit(text.trim())
                            text = ""
                            pill.typing = false
                        }
                        Keys.onEscapePressed: {
                            text = ""
                            pill.typing = false
                        }
                    }

                    Text {
                        anchors.fill: parent
                        anchors.rightMargin: 6
                        verticalAlignment: Text.AlignVCenter
                        visible: input.text.length === 0
                        text: qsTr("Ask FRIDAY…")
                        color: Theme.withAlpha(Theme.text, 0.42)
                        font.pixelSize: Theme.fontM
                        font.family: input.font.family
                    }
                }

                // status text (non-typing)
                Column {
                    visible: !pill.typing
                    width: parent.width - pill.orbSize * 0.72 - controls.width - 20
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1

                    Row {
                        spacing: 6
                        Text {
                            text: pill.statusLabel
                            color: pill.accent
                            font.pixelSize: Theme.fontS
                            font.bold: true
                            font.letterSpacing: 1.1
                            font.family: "Segoe UI Variable Display, Segoe UI, sans-serif"
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "· " + pill.connectionLabel + " · " + pill.clock
                            color: Theme.withAlpha(Theme.mid, 0.95)
                            font.pixelSize: Theme.fontXS
                            font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                        }
                    }
                    Text {
                        text: pill.personaBadge
                        color: Theme.withAlpha(Theme.acc2, 0.85)
                        font.pixelSize: Theme.fontXS
                        elide: Text.ElideRight
                        width: parent.width
                        font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                    }
                }

                // controls
                Row {
                    id: controls
                    spacing: 4
                    anchors.verticalCenter: parent.verticalCenter

                    component PillButton: Rectangle {
                        id: pb
                        property string glyph: "●"
                        property string tip: ""
                        property color tint: Theme.text
                        signal tapped()
                        width: Math.round(26 * Theme.uiScale)
                        height: width
                        radius: width / 2
                        color: mouse.containsMouse
                               ? Theme.withAlpha(pb.tint, 0.18) : "transparent"
                        border.width: mouse.containsMouse ? 1 : 0
                        border.color: Theme.withAlpha(pb.tint, 0.4)
                        Behavior on color { ColorAnimation { duration: Theme.durMicro } }

                        Text {
                            anchors.centerIn: parent
                            text: pb.glyph
                            color: pb.tint
                            font.pixelSize: Math.round(12 * Theme.uiScale)
                        }
                        MouseArea {
                            id: mouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: pb.tapped()
                        }
                    }

                    PillButton {
                        glyph: pill.muted ? "⊘" : "🎙"
                        tint: pill.muted ? Theme.mutcol : Theme.green
                        onTapped: pill.requestMute()
                    }
                    PillButton {
                        glyph: pill.silentMode ? "▣" : "🔊"
                        tint: pill.silentMode ? Theme.acc2 : Theme.text
                        onTapped: pill.requestSilent()
                    }
                    PillButton { glyph: "⌨"; onTapped: pill.typing = !pill.typing }
                    PillButton { glyph: "◫"; onTapped: pill.requestInspector() }
                    PillButton {
                        glyph: "✕"
                        tint: Theme.red
                        onTapped: {
                            pill.typing = false
                            pill.expanded = false
                            pill.requestCancel()
                        }
                    }
                }
            }
        }

        // ── collapse chevron (only when expanded & not typing) ───────────────
        Rectangle {
            visible: pill.expanded && !pill.typing
            width: Math.round(18 * Theme.uiScale)
            height: width
            radius: width / 2
            color: Theme.withAlpha(Theme.panel, 0.9)
            border.width: 1
            border.color: Theme.withAlpha(Theme.mid, 0.6)
            anchors.right: parent.right
            anchors.rightMargin: 2
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 2
            Text {
                anchors.centerIn: parent
                text: "▾"
                color: Theme.mid
                font.pixelSize: Math.round(10 * Theme.uiScale)
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: pill.expanded = false
            }
        }
    }

    // ── click / drag behaviour ───────────────────────────────────────────────
    MouseArea {
        id: surface
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        property point pressPos
        property bool moved
        onPressed: (mouse) => {
            pressPos = Qt.point(mouse.x, mouse.y)
            moved = false
            // Native move: the OS owns the drag, so it stays smooth on
            // Windows with per-monitor DPI and multi-desktop.
            if (!pill.expanded) {
                const win = pill.Window.window
                if (win) win.startSystemMove()
            }
        }
        onPositionChanged: (mouse) => {
            if (Math.abs(mouse.x - pressPos.x) > 4 || Math.abs(mouse.y - pressPos.y) > 4)
                moved = true
        }
        onClicked: {
            if (moved) return
            if (!pill.expanded) {
                pill.expanded = true
                if (pill.assistantState === "MUTED") pill.requestMute()
                else pill.requestCancel()
            } else if (!pill.typing) {
                pill.typing = true
            }
        }
        onDoubleClicked: {
            pill.expanded = false
            pill.typing = false
        }
    }

    // Shared animation clock (one timer for the whole pill, not per-element).
    property real phase
    NumberAnimation on phase {
        running: Theme.motion > 0
        loops: Animation.Infinite
        from: 0
        to: 6.28318
        duration: 2600
    }
}
