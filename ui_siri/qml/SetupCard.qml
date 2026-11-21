pragma ComponentBehavior: Bound
import QtQuick

/*
    First-run setup card.

    The legacy UI showed a Tk dialog over the main window. In an ambient
    interface there is no main window, so the request appears as a card in the
    snippet stack: the overlay is already on top, already focused, and the user
    is not confronted with a modal they might dismiss into a broken app.

    The worker thread blocks in `wait_for_api_key()` until a key lands, so this
    is the only thing standing between the user and a working FRIDAY.
*/
Rectangle {
    id: setup

    required property bool reducedMotion
    signal submit(string key)
    signal dismiss()

    implicitWidth: Math.round(360 * Theme.uiScale)
    implicitHeight: content.height + Theme.pad * 2

    radius: Theme.radiusCard
    color: Theme.glass > 0 ? Theme.withAlpha(Theme.panel, 0.97) : Theme.panel
    border.width: 1
    border.color: Theme.withAlpha(Theme.pri, 0.55)

    // gentle attention pulse on the border only
    SequentialAnimation on border.color {
        running: !setup.reducedMotion
        loops: Animation.Infinite
        ColorAnimation { to: Theme.withAlpha(Theme.pri, 0.25); duration: 1600 }
        ColorAnimation { to: Theme.withAlpha(Theme.pri, 0.65); duration: 1600 }
    }

    property bool revealed: false
    focus: visible

    // Whether "Initialise" is currently held down (avoids stealing focus from
    // the key field while the card first appears).
    property bool submitPressed: false

    Column {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: Theme.pad
        spacing: 8

        Row {
            spacing: 7
            Text {
                text: "◈"
                color: Theme.pri
                font.pixelSize: Theme.fontL
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("INITIALISATION REQUIRED")
                color: Theme.pri
                font.pixelSize: Theme.fontS
                font.bold: true
                font.letterSpacing: 1.1
                font.family: "Segoe UI Variable Display, Segoe UI, sans-serif"
            }
        }

        Text {
            width: parent.width
            text: qsTr("Paste your Gemini API key to boot FRIDAY. It is stored "
                       + "locally in config/api_keys.json and never leaves "
                       + "your machine except to call Google.")
            color: Theme.glass > 0 ? Theme.withAlpha(Theme.text, 0.85) : Theme.onGlass
            font.pixelSize: Theme.fontXS
            wrapMode: Text.WordWrap
            lineHeight: 1.2
        }

        Rectangle {
            width: parent.width
            height: Math.round(36 * Theme.uiScale)
            radius: Theme.radiusChip
            color: Theme.withAlpha(Theme.input, 0.9)
            border.width: 1
            border.color: keyInput.activeFocus ? Theme.pri
                                               : Theme.withAlpha(Theme.inpBorder, 0.9)

            TextInput {
                id: keyInput
                anchors.fill: parent
                anchors.margins: 9
                verticalAlignment: TextInput.AlignVCenter
                color: Theme.text
                selectionColor: Theme.withAlpha(Theme.pri, 0.5)
                selectedTextColor: Theme.bg
                font.pixelSize: Theme.fontS
                font.family: "Cascadia Mono, Consolas, monospace"
                echoMode: setup.revealed ? TextInput.Normal : TextInput.Password
                passwordCharacter: "•"
                clip: true
                focus: setup.visible
                onAccepted: setup.apply()

                Text {
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    visible: keyInput.text.length === 0
                    text: "AIza…"
                    color: Theme.withAlpha(Theme.text, 0.35)
                    font.pixelSize: Theme.fontS
                    font.family: keyInput.font.family
                }
            }

            Text {
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: setup.revealed ? "◉" : "◎"
                color: Theme.withAlpha(Theme.mid, 1)
                font.pixelSize: Theme.fontS
                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -6
                    cursorShape: Qt.PointingHandCursor
                    onClicked: setup.revealed = !setup.revealed
                }
            }
        }

        Row {
            spacing: 8

            Rectangle {
                id: submitButton
                width: Math.round(132 * Theme.uiScale)
                height: Math.round(34 * Theme.uiScale)
                radius: height / 2
                property bool pressed: setup.submitPressed
                color: pressed ? Theme.withAlpha(Theme.pri, 0.34)
                               : Theme.withAlpha(Theme.pri, 0.18)
                border.width: 1
                border.color: Theme.withAlpha(Theme.pri, 0.6)
                Text {
                    anchors.centerIn: parent
                    text: qsTr("▸  INITIALISE SYSTEMS")
                    color: Theme.pri
                    font.pixelSize: Theme.fontXS
                    font.bold: true
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onPressed: setup.submitPressed = true
                    onReleased: setup.submitPressed = false
                    onClicked: setup.apply()
                }
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("or set FRIDAY_GEMINI_API_KEY")
                color: Theme.withAlpha(Theme.dim, 1)
                font.pixelSize: Theme.fontXS - 1
            }
        }

        Text {
            id: error
            width: parent.width
            visible: text.length > 0
            color: Theme.red
            font.pixelSize: Theme.fontXS - 1
            wrapMode: Text.WordWrap
        }
    }

    function apply() {
        const value = keyInput.text.trim()
        if (value.length < 8) {
            error.text = qsTr("That does not look like an API key.")
            return
        }
        error.text = ""
        setup.submit(value)
    }

    // Called by the controller when the key was rejected.
    function showError(message) {
        error.text = message
    }
}
