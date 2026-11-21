pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

/*
    Layer 4 — on-demand inspector drawer.

    Page-object style: the controller sets the root object's properties from
    Python (`props`) instead of the QML reaching into Python globals. That keeps
    the QML free of backend knowledge and makes the whole drawer testable with
    plain dicts.

    Tabs
    ----
    camera    Picture-in-picture privacy-blurred camera feed
    arcade    Vision Arcade (10 games) with live readouts
    wake      Wake-word wizard (engine status, phrase, threshold)
    history   Transcript history with channel filters
    settings  Persona, theme, motion/transparency, diagnostics

    The drawer is **not** click-through and **not** capture-excluded: it is a
    real window the user interacts with, so it must behave like one.
*/
Rectangle {
    id: drawer

    required property var props          // dict pushed from Python
    required property var bridge         // UiBridge (for transcript model)
    required property bool reducedMotion

    property string tab: props && props.tab ? props.tab : "camera"

    readonly property var tabs: [
        { id: "camera",   label: "Camera",   glyph: "◉" },
        { id: "arcade",   label: "Arcade",   glyph: "◈" },
        { id: "wake",     label: "Wake",     glyph: "🎙" },
        { id: "history",  label: "History",  glyph: "☰" },
        { id: "settings", label: "Settings", glyph: "⚙" }
    ]

    signal closeRequested()
    signal visionAction(string action, string mode)
    signal arcadeGame(string game)
    signal command(string text)
    signal openExternal(string url)

    // One material across the product: the same panel glass, hairline specular
    // border and top-edge rim the snippet cards and the pill use. The drawer is
    // a *tool*, so it is allowed to sit a little more opaque (it carries dense
    // text), but it must never look like a different application.
    color: Theme.glass > 0 ? Theme.withAlpha(Theme.panel, 0.90) : Theme.panel
    radius: Theme.radiusCard
    border.width: 1
    border.color: Theme.glassBorder

    implicitWidth: Math.round(720 * Theme.uiScale)
    implicitHeight: Math.round(520 * Theme.uiScale)

    // Glass thickness: one hairline across the top edge, fading at the corners.
    Rectangle {
        visible: Theme.glass > 0
        width: parent.width - 2 * parent.radius * 0.55
        height: 1
        x: (parent.width - width) / 2
        y: 1
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop {
                position: 0.18
                color: Theme.withAlpha(Theme.glassInnerRim, 0.95 * Theme.glass)
            }
            GradientStop {
                position: 0.82
                color: Theme.withAlpha(Theme.glassInnerRim, 0.95 * Theme.glass)
            }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    // Soft clipped sheen down from the header, never a grey slab.
    Rectangle {
        visible: Theme.glass > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * 0.34
        radius: parent.radius
        gradient: Gradient {
            GradientStop {
                position: 0.0
                color: Theme.withAlpha(Theme.glassSpecular, 0.06 * Theme.glass)
            }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    // ── header ──────────────────────────────────────────────────────────────
    Rectangle {
        id: header
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Math.round(52 * Theme.uiScale)
        color: "transparent"

        Row {
            anchors.left: parent.left
            anchors.leftMargin: Theme.pad
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6

            Repeater {
                model: drawer.tabs
                delegate: Rectangle {
                    id: tabChip
                    required property var modelData
                    // The static analyser cannot type a JS-array model, so the
                    // role is read once here and everything below uses helpers.
                    readonly property string tabId: modelData.id
                    readonly property string tabText: modelData.glyph + "  "
                                                      + modelData.label
                    readonly property bool active: drawer.tab === tabChip.tabId
                    width: Math.max(Math.round(74 * Theme.uiScale),
                                    tabLabel.width + 20)
                    height: Math.round(30 * Theme.uiScale)
                    radius: Theme.radiusChip
                    color: tabChip.active
                           ? Theme.withAlpha(Theme.pri, 0.18) : "transparent"
                    border.width: tabChip.active ? 1 : 0
                    border.color: Theme.withAlpha(Theme.pri, 0.45)
                    Behavior on color { ColorAnimation { duration: Theme.durMicro } }

                    Text {
                        id: tabLabel
                        anchors.centerIn: parent
                        text: tabChip.tabText
                        color: tabChip.active
                               ? Theme.pri : Theme.withAlpha(Theme.text, 0.62)
                        font.pixelSize: Theme.fontXS
                        font.bold: tabChip.active
                        font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: drawer.tab = tabChip.tabId
                    }
                }
            }
        }

        Rectangle {
            width: Math.round(26 * Theme.uiScale)
            height: width
            radius: width / 2
            anchors.right: parent.right
            anchors.rightMargin: Theme.pad
            anchors.verticalCenter: parent.verticalCenter
            color: closeArea.containsMouse ? Theme.withAlpha(Theme.red, 0.25) : "transparent"
            Text {
                anchors.centerIn: parent
                text: "✕"
                color: Theme.withAlpha(Theme.text, 0.8)
                font.pixelSize: Theme.fontM
            }
            MouseArea {
                id: closeArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: drawer.closeRequested()
            }
        }

        Rectangle {
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5
                               color: Theme.withAlpha(Theme.glassInnerRim, 0.7) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // ── body ────────────────────────────────────────────────────────────────
    StackLayout {
        id: pages
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        anchors.margins: Theme.pad
        currentIndex: {
            for (var i = 0; i < drawer.tabs.length; i++)
                if (drawer.tabs[i].id === drawer.tab) return i
            return 0
        }

        // ── CAMERA ──────────────────────────────────────────────────────────
        Item {
            ColumnLayout {
                anchors.fill: parent
                spacing: 10

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: Theme.radiusCard
                    color: Theme.withAlpha(Theme.dimmer, 0.55)
                    border.width: 1
                    border.color: Theme.withAlpha(Theme.glassInnerRim, 0.6)
                    clip: true

                    Image {
                        id: cam
                        anchors.fill: parent
                        anchors.margins: 6
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        asynchronous: true
                        // QQuickImageProvider URLs are revision-based, so the
                        // source must be rebuilt every time a frame lands or
                        // the preview freezes on the first one. `framesChanged`
                        // is bound here, not polled.
                        source: {
                            if (!drawer.props || !drawer.props.cameraSource)
                                return ""
                            const rev = drawer.bridge ? drawer.bridge.frameRevision : 0
                            return drawer.props.cameraSource + "&f=" + rev
                        }
                        visible: status === Image.Ready

                        BusyIndicator {
                            anchors.centerIn: parent
                            running: cam.status !== Image.Ready
                            visible: running
                        }
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: cam.status !== Image.Ready
                        text: qsTr("Camera idle — start a vision mode")
                        color: Theme.withAlpha(Theme.text, 0.5)
                        font.pixelSize: Theme.fontS
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: 6

                    Repeater {
                        model: [
                            { label: "GESTURE", action: "start", mode: "gesture" },
                            { label: "OBJECTS", action: "start", mode: "objects" },
                            { label: "AIR MOUSE", action: "start", mode: "air_mouse" },
                            { label: "VOLUME", action: "start", mode: "volume" },
                            { label: "POSTURE", action: "start", mode: "posture" },
                            { label: "MOTION", action: "start", mode: "motion" },
                            { label: "STOP", action: "stop", mode: "" }
                        ]
                        delegate: Rectangle {
                            id: visionChip
                            required property var modelData
                            property var entry: modelData
                            width: Math.max(Math.round(78 * Theme.uiScale),
                                            chipText.width + 18)
                            height: Math.round(28 * Theme.uiScale)
                            radius: height / 2
                            color: chipArea.containsMouse
                                   ? Theme.withAlpha(Theme.pri, 0.24)
                                   : Theme.withAlpha(Theme.pri, 0.08)
                            border.width: 1
                            border.color: Theme.withAlpha(Theme.pri, 0.35)
                            Text {
                                id: chipText
                                anchors.centerIn: parent
                                text: visionChip.entry.label
                                color: Theme.pri
                                font.pixelSize: Theme.fontXS
                                font.bold: true
                                font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                            }
                            MouseArea {
                                id: chipArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: drawer.visionAction(visionChip.entry.action,
                                                               visionChip.entry.mode)
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: drawer.props && drawer.props.visionSignal
                          ? drawer.props.visionSignal : ""
                    color: Theme.withAlpha(Theme.acc2, 0.9)
                    font.pixelSize: Theme.fontXS
                    elide: Text.ElideRight
                }
            }
        }

        // ── ARCADE ──────────────────────────────────────────────────────────
        Item {
            ColumnLayout {
                anchors.fill: parent
                spacing: 8

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: Theme.radiusCard
                    color: "#050a0e"
                    border.width: 1
                    border.color: Theme.withAlpha(Theme.acc2, 0.3)
                    clip: true

                    Image {
                        anchors.fill: parent
                        anchors.margins: 4
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        asynchronous: true
                        source: {
                            if (!drawer.props || !drawer.props.arcadeSource)
                                return ""
                            const rev = drawer.bridge ? drawer.bridge.frameRevision : 0
                            return drawer.props.arcadeSource + "&f=" + rev
                        }
                    }

                    // Live readouts: quiet glass chips, tabular digits
                    Row {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.margins: 10
                        spacing: 6
                        Rectangle {
                            width: arcadeGameLabel.width + 18
                            height: Math.round(22 * Theme.uiScale)
                            radius: height / 2
                            color: Theme.withAlpha(Theme.panel, 0.72)
                            Text {
                                id: arcadeGameLabel
                                anchors.centerIn: parent
                                text: drawer.props && drawer.props.arcadeGame
                                      ? drawer.props.arcadeGame.replace(/_/g, " ") : ""
                                color: Theme.withAlpha(Theme.text, 0.9)
                                font.pixelSize: Theme.fontXS
                                font.bold: true
                                font.family: Theme.fontDisplay
                                font.capitalization: Font.Capitalize
                            }
                        }
                        Rectangle {
                            width: scoreLabel.width + 16
                            height: Math.round(22 * Theme.uiScale)
                            radius: height / 2
                            color: Theme.withAlpha(Theme.panel, 0.72)
                            Text {
                                id: scoreLabel
                                anchors.centerIn: parent
                                text: qsTr("Score %1").arg(drawer.props
                                  && drawer.props.arcadeScore !== undefined
                                  ? drawer.props.arcadeScore : 0)
                                color: Theme.withAlpha(Theme.text, 0.9)
                                font.pixelSize: Theme.fontXS
                                font.family: Theme.fontNumeric
                            }
                        }
                        Rectangle {
                            width: livesLabel.width + 16
                            height: Math.round(22 * Theme.uiScale)
                            radius: height / 2
                            color: Theme.withAlpha(Theme.panel, 0.72)
                            Text {
                                id: livesLabel
                                anchors.centerIn: parent
                                text: qsTr("Lives %1").arg(drawer.props
                                  && drawer.props.arcadeLives !== undefined
                                  ? drawer.props.arcadeLives : 0)
                                color: Theme.withAlpha(Theme.text, 0.9)
                                font.pixelSize: Theme.fontXS
                                font.family: Theme.fontNumeric
                            }
                        }
                    }

                    Text {
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                        anchors.margins: 12
                        width: parent.width - 24
                        text: drawer.props && drawer.props.arcadeMessage
                              ? drawer.props.arcadeMessage : ""
                        color: Theme.acc2
                        font.pixelSize: Theme.fontS
                        elide: Text.ElideRight
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: 5
                    Repeater {
                        model: [
                            { id: "face_filter", label: "FACE FILTER" },
                            { id: "gesture_pong", label: "GESTURE PONG" },
                            { id: "orb_hunt", label: "ORB HUNT" },
                            { id: "rps", label: "ROCK PAPER SCISSORS" },
                            { id: "shape_creator", label: "SHAPE CREATOR" },
                            { id: "floor_lava", label: "FLOOR IS LAVA" },
                            { id: "planet_explorer", label: "PLANET EXPLORER" },
                            { id: "whirlpool", label: "WHIRLPOOL" },
                            { id: "laser_defense", label: "LASER DEFENSE" },
                            { id: "arpeggiator", label: "ARPEGGIATOR" }
                        ]
                        delegate: Rectangle {
                            id: gameChip
                            required property var modelData
                            property var entry: modelData
                            width: Math.max(Math.round(84 * Theme.uiScale),
                                            gameLabel.width + 16)
                            height: Math.round(26 * Theme.uiScale)
                            radius: height / 2
                            readonly property bool current:
                                drawer.props && drawer.props.arcadeGame === gameChip.entry.id
                            readonly property color chipTone:
                                current ? Theme.acc2 : Theme.withAlpha(Theme.text, 0.78)
                            color: current ? Theme.withAlpha(Theme.acc2, 0.24)
                                           : gameArea.containsMouse
                                             ? Theme.withAlpha(Theme.acc2, 0.16)
                                             : Theme.withAlpha(Theme.dimmer, 0.9)
                            border.width: current ? 1 : 0
                            border.color: Theme.withAlpha(Theme.acc2, 0.6)
                            Text {
                                id: gameLabel
                                anchors.centerIn: parent
                                text: gameChip.entry.label
                                color: gameChip.chipTone
                                font.pixelSize: Theme.fontXS - 1
                                font.bold: gameChip.current
                            }
                            MouseArea {
                                id: gameArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: drawer.arcadeGame(gameChip.entry.id)
                            }
                        }
                    }
                }
            }
        }

        // ── WAKE WORD WIZARD ────────────────────────────────────────────────
        Item {
            ColumnLayout {
                anchors.fill: parent
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(96 * Theme.uiScale)
                    radius: Theme.radiusCard
                    color: Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
                    border.width: 1
                    border.color: Theme.withAlpha(
                        drawer.props && drawer.props.wakeActive
                        ? Theme.green : Theme.dim, 0.5)

                    Column {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.pad
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 5
                        Text {
                            text: qsTr("Wake word")
                            color: Theme.withAlpha(Theme.mid, 1)
                            font.pixelSize: Theme.fontXS
                            font.bold: true
                            font.letterSpacing: 1
                        }
                        Text {
                            text: drawer.props && drawer.props.wakePhrase
                                  ? "\"" + drawer.props.wakePhrase + "\""
                                  : "\"Hey Friday\""
                            color: Theme.textStrong
                            font.pixelSize: Theme.fontL
                            font.bold: true
                        }
                        Text {
                            text: drawer.props && drawer.props.wakeStatus
                                  ? drawer.props.wakeStatus : qsTr("Engine idle")
                            color: Theme.withAlpha(Theme.text, 0.7)
                            font.pixelSize: Theme.fontXS
                        }
                    }

                    Rectangle {
                        anchors.right: parent.right
                        anchors.rightMargin: Theme.pad
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.round(104 * Theme.uiScale)
                        height: Math.round(32 * Theme.uiScale)
                        radius: height / 2
                        color: wakeArea.containsMouse
                               ? Theme.withAlpha(Theme.pri, 0.3)
                               : Theme.withAlpha(Theme.pri, 0.14)
                        border.width: 1
                        border.color: Theme.withAlpha(Theme.pri, 0.5)
                        Text {
                            anchors.centerIn: parent
                            text: drawer.props && drawer.props.wakeActive
                                  ? qsTr("STOP") : qsTr("ENABLE")
                            color: Theme.pri
                            font.pixelSize: Theme.fontXS
                            font.bold: true
                        }
                        MouseArea {
                            id: wakeArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: drawer.command("toggle wake word")
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "The wake word runs fully on-device with openWakeWord. "
                          + "When it fires, FRIDAY barges in: any speech stops, "
                          + "the microphone unmutes, and the orb switches to listening."
                    color: Theme.withAlpha(Theme.text, 0.65)
                    font.pixelSize: Theme.fontXS
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(44 * Theme.uiScale)
                    radius: Theme.radiusChip
                    color: Theme.withAlpha(Theme.dimmer, 0.7)
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 24
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        text: qsTr("Retrain: python -m actions.friday_wake --train hey_friday")
                        color: Theme.withAlpha(Theme.text, 0.55)
                        font.pixelSize: Theme.fontXS - 1
                        font.family: "Cascadia Mono, Consolas, monospace"
                    }
                }
            }
        }

        // ── HISTORY ─────────────────────────────────────────────────────────
        Item {
            ColumnLayout {
                anchors.fill: parent
                spacing: 8

                ListView {
                    id: history
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: drawer.bridge ? drawer.bridge.transcript : null
                    spacing: 3
                    boundsBehavior: Flickable.StopAtBounds

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                        width: 6
                    }

                    delegate: Item {
                        id: msg
                        required property int index
                        required property string text
                        required property string channel
                        required property string clock
                        required property string severity

                        width: history.width
                        height: bubble.height + 6

                        readonly property color tone:
                            channel === "you" ? Theme.textStrong
                            : channel === "friday" ? Theme.pri
                            : channel === "error" ? Theme.red
                            : channel === "security" ? Theme.mutcol
                            : channel === "alert" ? Theme.acc2
                            : channel === "vision" ? Theme.green
                            : Theme.withAlpha(Theme.text, 0.72)

                        Rectangle {
                            id: bubble
                            x: msg.channel === "you" ? msg.width - width - 8 : 8
                            width: Math.min(msg.width * 0.78,
                                            label.implicitWidth + 22)
                            height: label.implicitHeight + 14
                            radius: Theme.radiusChip
                            color: msg.channel === "you"
                                   ? Theme.withAlpha(Theme.pri, 0.14)
                                   : Theme.withAlpha(Theme.panel, 0.7)
                            border.width: 1
                            border.color: Theme.withAlpha(msg.tone, 0.28)

                            Text {
                                id: label
                                anchors.fill: parent
                                anchors.margins: 8
                                text: msg.text
                                color: msg.tone
                                font.pixelSize: Theme.fontXS
                                wrapMode: Text.Wrap
                                font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                            }
                        }

                        Text {
                            anchors.right: bubble.left
                            anchors.rightMargin: 6
                            anchors.bottom: bubble.bottom
                            text: parent.clock
                            color: Theme.withAlpha(Theme.dim, 0.9)
                            font.pixelSize: Theme.fontXS - 2
                            visible: msg.channel !== "you"
                        }
                    }

                    onCountChanged: if (count > 0) positionViewAtEnd()
                }

                Row {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        width: 90; height: 26; radius: height / 2
                        color: clearArea.containsMouse
                               ? Theme.withAlpha(Theme.red, 0.24) : "transparent"
                        border.width: 1
                        border.color: Theme.withAlpha(Theme.red, 0.4)
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("CLEAR")
                            color: Theme.red
                            font.pixelSize: Theme.fontXS - 1
                            font.bold: true
                        }
                        MouseArea {
                            id: clearArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: if (drawer.bridge) drawer.bridge.clearTranscript()
                        }
                    }
                }
            }
        }

        // ── SETTINGS ────────────────────────────────────────────────────────
        Item {
            ScrollView {
                anchors.fill: parent
                clip: true
                contentWidth: availableWidth

                Column {
                    width: parent.width
                    spacing: 14

                    // design — Apple Siri is the sole visual identity
                    Column {
                        width: parent.width
                        spacing: 6
                        Text {
                            text: qsTr("DESIGN")
                            color: Theme.withAlpha(Theme.mid, 1)
                            font.pixelSize: Theme.fontXS
                            font.bold: true
                            font.letterSpacing: 1
                        }
                        Rectangle {
                            width: parent.width
                            height: Math.round(48 * Theme.uiScale)
                            radius: Theme.radiusChip
                            color: Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
                            border.width: 1
                            border.color: Theme.glassBorder
                            Row {
                                anchors.left: parent.left
                                anchors.leftMargin: Theme.pad
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Math.round(10 * Theme.uiScale)
                                Rectangle {
                                    width: Math.round(20 * Theme.uiScale)
                                    height: width
                                    radius: width / 2
                                    border.width: Math.round(1.5 * Theme.uiScale)
                                    border.color: Theme.withAlpha(Theme.pri, 0.55)
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: Theme.pri }
                                        GradientStop { position: 0.5; color: Theme.acc2 }
                                        GradientStop { position: 1.0; color: Theme.acc }
                                    }
                                }
                                Column {
                                    spacing: 1
                                    Text {
                                        text: qsTr("Apple Siri")
                                        color: Theme.textOnGlass
                                        font.pixelSize: Theme.fontS
                                        font.bold: true
                                        font.family: Theme.fontDisplay
                                    }
                                    Text {
                                        text: qsTr("The sole visual identity of FRIDAY")
                                        color: Theme.withAlpha(Theme.textOnGlass, 0.55)
                                        font.pixelSize: Theme.fontXS
                                        font.family: Theme.fontText
                                    }
                                }
                            }
                        }
                    }

                    // persona
                    Column {
                        width: parent.width
                        spacing: 6
                        Text {
                            text: qsTr("PERSONA")
                            color: Theme.withAlpha(Theme.mid, 1)
                            font.pixelSize: Theme.fontXS
                            font.bold: true
                            font.letterSpacing: 1
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: drawer.props && drawer.props.personaBadge
                                  ? drawer.props.personaBadge : ""
                            color: Theme.withAlpha(Theme.acc2, 0.9)
                            font.pixelSize: Theme.fontS
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: "Change it by voice: \"switch to butler mode\", "
                                  + "\"set your voice to Puck\", \"be more playful\"."
                            color: Theme.withAlpha(Theme.text, 0.6)
                            font.pixelSize: Theme.fontXS
                        }
                    }

                    // accessibility
                    Column {
                        width: parent.width
                        spacing: 6
                        Text {
                            text: qsTr("ACCESSIBILITY")
                            color: Theme.withAlpha(Theme.mid, 1)
                            font.pixelSize: Theme.fontXS
                            font.bold: true
                            font.letterSpacing: 1
                        }
                        Repeater {
                            model: [
                                { label: "Reduced motion", key: "reducedMotion" },
                                { label: "Transparency",   key: "transparency" },
                                { label: "Always on top",  key: "alwaysOnTop" },
                                { label: "Hide from capture", key: "captureExcluded" }
                            ]
                            delegate: Row {
                                id: toggleRow
                                required property var modelData
                                property var entry: modelData
                                width: parent.width
                                height: 30
                                spacing: 8
                                Rectangle {
                                    width: 34; height: 18; radius: 9
                                    anchors.verticalCenter: parent.verticalCenter
                                    readonly property bool on:
                                        drawer.props && drawer.props[toggleRow.entry.key] === true
                                    color: on ? Theme.withAlpha(Theme.green, 0.5)
                                              : Theme.withAlpha(Theme.dim, 0.6)
                                    border.width: 1
                                    border.color: Theme.withAlpha(Theme.mid, 0.6)
                                    Rectangle {
                                        width: 14; height: 14; radius: 7
                                        y: 2
                                        x: parent.on ? parent.width - 16 : 2
                                        color: Theme.textStrong
                                        Behavior on x {
                                            NumberAnimation { duration: Theme.durShort }
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: drawer.command("toggle " + toggleRow.entry.key)
                                    }
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: toggleRow.entry.label
                                    color: Theme.withAlpha(Theme.text, 0.85)
                                    font.pixelSize: Theme.fontS
                                }
                            }
                        }
                    }

                    // diagnostics
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: drawer.props && drawer.props.diagnostics
                              ? JSON.stringify(drawer.props.diagnostics, null, 2) : ""
                        color: Theme.withAlpha(Theme.dim, 1)
                        font.pixelSize: Theme.fontXS - 2
                        font.family: "Cascadia Mono, Consolas, monospace"
                    }
                }
            }
        }
    }
}
