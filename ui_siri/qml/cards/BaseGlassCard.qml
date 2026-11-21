pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Shared frosted-glass surface for every snippet card.

    All typed cards (TextCard, WeatherBriefingCard, …) derive from this so the
    stack has one visual grammar: hairline border, 20px radius, 1px inner
    highlight, severity-tinted left rail, spring entrance, and a hover lift.

    Content is injected via the default property alias `content`.
*/
Item {
    id: card

    required property string title
    required property string body
    required property string footer
    required property string severity   // info|success|warn|error|security
    required property real cardTtl
    required property bool dismissible
    required property real age            // seconds since the card was created

    property string kind: "text"
    property bool stacked: false          // true when not the front card

    signal dismissed()
    signal activated()

    default property alias content: slot.data

    implicitWidth: Math.round(340 * Theme.uiScale)
    implicitHeight: Math.max(Math.round(96 * Theme.uiScale),
                             header.height + bodyColumn.height + footerRow.height
                             + Theme.pad * 2 + 8)

    // Measured content sizes so derived cards can size themselves without
    // reaching into private ids.
    readonly property real bodyHeight: Math.min(bodyColumn.height,
                                                Math.round(200 * Theme.uiScale))

    readonly property color tone: severity === "error" ? Theme.red
                                : severity === "security" ? Theme.mutcol
                                : severity === "warn" ? Theme.acc2
                                : severity === "success" ? Theme.green
                                : Theme.pri

    opacity: stacked ? 0.0 : 1.0
    scale: stacked ? 0.94 : 1.0

    // ── entrance: springy settle, suppressed when reduced-motion ────────────
    property real entrance
    NumberAnimation on entrance {
        running: Theme.motion > 0 && !card.stacked
        from: 0; to: 1
        duration: Theme.durMed
        easing.type: Easing.OutBack
        easing.overshoot: 1.12
    }
    transform: Translate { y: (1 - card.entrance) * 18 * Theme.motion }
    Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
    Behavior on scale   { NumberAnimation { duration: Theme.durShort } }

    // ── shadow ──────────────────────────────────────────────────────────────
    Rectangle {
        anchors.fill: surface
        anchors.margins: 2
        radius: surface.radius
        color: Theme.shadowColor
        opacity: 0.55
        visible: Theme.glass > 0
    }

    // ── glass surface ───────────────────────────────────────────────────────
    Rectangle {
        id: surface
        anchors.fill: parent
        radius: Theme.radiusCard
        color: Theme.glass > 0 ? Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
                               : Theme.panel
        border.width: 1
        border.color: Theme.withAlpha(card.tone, hover.containsMouse ? 0.55 : 0.26)

        Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            color: "transparent"
            border.width: 1
            border.color: Theme.withAlpha(Theme.textStrong, 0.05)
        }

        // severity rail
        Rectangle {
            width: Math.round(3 * Theme.uiScale)
            height: parent.height - Theme.pad * 2
            radius: width / 2
            x: 1
            anchors.verticalCenter: parent.verticalCenter
            color: card.tone
            opacity: 0.85
        }

        // top sheen
        Rectangle {
            width: parent.width - 2
            height: parent.height / 2.4
            x: 1; y: 1
            radius: parent.radius
            opacity: 0.10 * Theme.glass
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(Theme.textStrong, 0.55) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // ── content ─────────────────────────────────────────────────────────────
    Item {
        id: slot
        anchors.fill: parent
        anchors.margins: Theme.pad
        anchors.leftMargin: Theme.pad + 4

        Column {
            id: bodyColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: header.bottom
            anchors.topMargin: 6
            width: parent.width
            spacing: 4

            Text {
                width: parent.width
                text: card.body
                visible: text.length > 0
                color: Theme.glass > 0 ? Theme.withAlpha(Theme.text, 0.95) : Theme.onGlass
                font.pixelSize: Theme.fontS
                font.family: "Segoe UI Variable Text, Segoe UI, Inter, sans-serif"
                wrapMode: Text.Wrap
                maximumLineCount: 6
                elide: Text.ElideRight
                lineHeight: 1.18
            }
        }

        Row {
            id: header
            width: parent.width
            spacing: 6

            Text {
                width: parent.width - (kindChip.visible ? kindChip.width + 10 : 0) - 4
                text: card.title
                color: card.tone
                font.pixelSize: Theme.fontS
                font.bold: true
                font.letterSpacing: 0.7
                font.family: "Segoe UI Variable Display, Segoe UI, sans-serif"
                elide: Text.ElideRight
            }

            Rectangle {
                id: kindChip
                visible: card.kind !== "text"
                width: chipText.width + 12
                height: chipText.height + 4
                radius: Theme.radiusChip
                color: Theme.withAlpha(card.tone, 0.14)
                border.width: 1
                border.color: Theme.withAlpha(card.tone, 0.30)
                Text {
                    id: chipText
                    anchors.centerIn: parent
                    text: card.kind.toUpperCase()
                    color: card.tone
                    font.pixelSize: Theme.fontXS - 1
                    font.bold: true
                    font.letterSpacing: 0.9
                    font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                }
            }
        }

        Row {
            id: footerRow
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            spacing: 8

            Text {
                width: parent.width - (closeBtn.visible ? 22 : 0)
                text: card.footer
                visible: text.length > 0
                color: Theme.withAlpha(Theme.mid, 0.95)
                font.pixelSize: Theme.fontXS
                font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
                elide: Text.ElideRight
            }
        }
    }

    // ── dismiss ─────────────────────────────────────────────────────────────
    Rectangle {
        id: closeBtn
        visible: card.dismissible && hover.containsMouse
        width: Math.round(18 * Theme.uiScale)
        height: width
        radius: width / 2
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 8
        anchors.bottomMargin: 8
        color: closeArea.containsMouse ? Theme.withAlpha(Theme.red, 0.22) : "transparent"
        Text {
            anchors.centerIn: parent
            text: "✕"
            color: Theme.withAlpha(Theme.text, 0.7)
            font.pixelSize: Theme.fontXS
        }
        MouseArea {
            id: closeArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: card.dismissed()
        }
    }

    // ── TTL progress hairline ───────────────────────────────────────────────
    Rectangle {
        readonly property real life: card.cardTtl > 0
            ? Math.max(0, 1 - Math.min(1, card.age / card.cardTtl)) : 1
        visible: card.cardTtl > 0 && life > 0
        width: (parent.width - 2) * life
        height: Math.round(2 * Theme.uiScale)
        radius: height / 2
        x: 1
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        color: Theme.withAlpha(card.tone, 0.55)
    }

    // ── interaction ─────────────────────────────────────────────────────────
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: (mouse) => {
            if (mouse.button === Qt.LeftButton) card.activated()
            else card.dismissed()
        }
    }
}
