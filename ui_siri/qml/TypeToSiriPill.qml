import QtQuick

/*
    Type-to-Siri pill — a compact frosted-glass field that appears *only* when
    the user explicitly asks to type (clicking the orb), glides up, and retracts
    cleanly on submit or cancel. It never clutters voice-only interactions.

    The pill is the single place in the Siri stage that accepts keyboard focus.
    Voice invocation leaves the window non-activating; the caret only appears
    because the user clicked the orb.
*/
Item {
    id: pill

    required property var bridge
    required property bool reducedMotion

    signal submitted(string text)
    signal canceled()

    property string text: ""

    // The controller / window never reach inside: the field is exposed so the
    // window can focus it when the user clicks the orb.
    readonly property Item inputItem: input

    readonly property real pillWidth: Math.round(300 * Theme.uiScale)
    readonly property real pillHeight: Math.round(42 * Theme.uiScale)

    implicitWidth: pillWidth
    implicitHeight: pillHeight

    // ── glass surface ───────────────────────────────────────────────────────
    Rectangle {
        id: surface
        anchors.fill: parent
        radius: pill.pillHeight / 2
        color: Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
        border.width: 1
        border.color: input.activeFocus
                      ? Theme.withAlpha(Theme.pri, 0.55)
                      : Theme.glassBorder
        Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

        // Inner rim: the hairline highlight that makes glass read as glass.
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            color: "transparent"
            border.width: 1
            border.color: Theme.glassInnerRim
        }

        // Specular top sheen, clipped to the pill's own rounded shape.
        Rectangle {
            width: parent.width - 2
            height: parent.height / 2
            x: 1; y: 1
            radius: parent.radius
            opacity: 0.16 * Theme.glass
            gradient: Gradient {
                GradientStop {
                    position: 0.0
                    color: Theme.withAlpha(Theme.glassSpecular, 1.0)
                }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }

        // Focus glow: a soft aqua bloom, never a hard outline.
        Rectangle {
            anchors.fill: parent
            anchors.margins: -2
            radius: parent.radius + 2
            color: "transparent"
            border.width: 2
            border.color: Theme.withAlpha(Theme.pri, input.activeFocus ? 0.16 : 0.0)
            Behavior on border.color { ColorAnimation { duration: Theme.durShort } }
        }
    }

    // ── leading glyph ──────────────────────────────────────────────────────
    Text {
        id: glyph
        anchors.left: parent.left
        anchors.leftMargin: Math.round(14 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        text: "🎤"
        font.pixelSize: Math.round(16 * Theme.uiScale)
        opacity: 0.82
    }

    // ── field ──────────────────────────────────────────────────────────────
    TextInput {
        id: input
        objectName: "siriInput"
        anchors.left: glyph.right
        anchors.leftMargin: Math.round(10 * Theme.uiScale)
        anchors.right: parent.right
        anchors.rightMargin: Math.round(14 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        height: parent.height
        verticalAlignment: TextInput.AlignVCenter
        color: Theme.text
        selectionColor: Theme.withAlpha(Theme.pri, 0.5)
        selectedTextColor: Theme.bg
        font.pixelSize: Theme.fontM
        font.family: Theme.fontText
        clip: true
        focus: true
        cursorVisible: input.activeFocus
        cursorDelegate: caretComponent
        onAccepted: {
            pill.text = input.text
            const value = input.text.trim()
            input.text = ""
            if (value.length > 0)
                pill.submitted(value)
        }
        Keys.onEscapePressed: {
            input.text = ""
            pill.canceled()
        }
        onTextChanged: pill.text = input.text
    }

    Component {
        id: caretComponent
        Rectangle {
            width: 1.5
            height: Math.round(18 * Theme.uiScale)
            color: Theme.pri
        }
    }

    // ── placeholder ────────────────────────────────────────────────────────
    Text {
        anchors.left: input.left
        anchors.right: input.right
        anchors.verticalCenter: parent.verticalCenter
        visible: input.text.length === 0
        text: qsTr("Type to Siri…")
        color: Theme.withAlpha(Theme.text, 0.42)
        font.pixelSize: Theme.fontM
        font.family: Theme.fontText
    }

    // Entrance: slide up out of the orb + fade, never a pop-in.
    property real reveal: visible ? 1 : 0
    Behavior on reveal {
        NumberAnimation {
            duration: Theme.durShort
            easing.type: Easing.OutCubic
        }
    }
    transform: Translate {
        y: (1 - pill.reveal) * Math.round(10 * Theme.uiScale) * Theme.motion
    }
}
