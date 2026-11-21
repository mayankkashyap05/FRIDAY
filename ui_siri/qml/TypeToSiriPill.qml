import QtQuick
import QtQuick.Effects

/*
    Type-to-Siri pill — compact frosted-glass text field that appears *only*
    when the user explicitly triggers typing (clicking the orb), glides out,
    and retracts cleanly once submitted. It never clutters voice-only
    interactions.

    Positioned at the bottom of the Siri stage column, just below the orb.
*/
Item {
    id: pill

    required property var bridge
    required property bool reducedMotion

    signal submitted(string text)
    signal canceled()

    property string text: ""

    readonly property real pillWidth: Math.round(300 * Theme.uiScale)
    readonly property real pillHeight: Math.round(40 * Theme.uiScale)

    implicitWidth: pillWidth
    implicitHeight: pillHeight

    // Frosted glass surface — pill-shaped.
    Rectangle {
        id: surface
        anchors.fill: parent
        radius: pill.pillHeight / 2
        color: Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
        border.width: 1
        border.color: Theme.withAlpha(Theme.pri, input.activeFocus ? 0.55 : 0.25)

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            color: "transparent"
            border.width: 1
            border.color: Theme.withAlpha(Theme.textStrong, 0.06)
        }

        MultiEffect {
            source: surface
            visible: Theme.transparency
            blurEnabled: true
            blur: 10
            blurMax: 28
            saturation: 0.05
        }
    }

    // Leading microphone glyph.
    Text {
        anchors.left: parent.left
        anchors.leftMargin: Math.round(12 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        text: "🎤"
        font.pixelSize: Math.round(17 * Theme.uiScale)
        opacity: 0.85
    }

    TextInput {
        id: input
        anchors.left: prev.right
        anchors.right: parent.right
        anchors.rightMargin: Math.round(10 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        width: pill.pillWidth - Math.round(62 * Theme.uiScale)
        color: Theme.text
        selectionColor: Theme.withAlpha(Theme.pri, 0.5)
        selectedTextColor: Theme.bg
        font.pixelSize: Theme.fontM
        font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
        clip: true
        focus: true
        cursorVisible: true
        onAccepted: {
            pill.text = input.text
            pill.submitted(input.text.trim())
            input.text = ""
        }
        Keys.onEscapePressed: {
            input.text = ""
            pill.canceled()
        }
        onTextChanged: pill.text = input.text
    }

    Text {
        anchors.fill: input
        anchors.rightMargin: Math.round(10 * Theme.uiScale)
        verticalAlignment: TextInput.AlignVCenter
        visible: input.text.length === 0
        text: qsTr("Type to Siri…")
        color: Theme.withAlpha(Theme.text, 0.42)
        font.pixelSize: Theme.fontM
        font.family: input.font.family
    }

    Item { id: prev; width: Math.round(40 * Theme.uiScale); height: width }
}
