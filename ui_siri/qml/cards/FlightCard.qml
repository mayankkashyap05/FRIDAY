pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Flight status: an origin → destination route plate with a drawn flight path,
    plus gate and status chips. Boarding-pass geometry, deliberately flat.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    readonly property string origin: p.origin !== undefined ? String(p.origin)
                                   : p.from !== undefined ? String(p.from) : ""
    readonly property string destination: p.destination !== undefined ? String(p.destination)
                                        : p.to !== undefined ? String(p.to) : ""
    readonly property string gate: p.gate !== undefined ? String(p.gate) : ""
    readonly property string status: p.status !== undefined ? String(p.status) : ""

    customBody: true
    contentBodyHeight: bodyContent.implicitHeight

    Column {
        id: bodyContent
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(9 * Theme.uiScale)

        Row {
            spacing: Math.round(10 * Theme.uiScale)

            Text {
                text: card.origin.length ? card.origin : "—"
                color: Theme.textOnGlass
                font: card.numFont(Math.round(23 * Theme.uiScale), true)
                anchors.verticalCenter: parent.verticalCenter
            }

            // Route line with an aircraft glyph at its midpoint — drawn, so it
            // stays crisp at every DPI and never depends on an emoji font.
            Item {
                width: Math.round(58 * Theme.uiScale)
                height: Math.round(14 * Theme.uiScale)
                anchors.verticalCenter: parent.verticalCenter

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    height: 1
                    color: Theme.withAlpha(card.tone, 0.3)
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: 3; height: 3; radius: 1.5
                    color: Theme.withAlpha(card.tone, 0.65)
                }
                Rectangle {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 3; height: 3; radius: 1.5
                    color: Theme.withAlpha(card.tone, 0.65)
                }
                Text {
                    anchors.centerIn: parent
                    text: "\u2708"
                    color: card.tone
                    font.pixelSize: Math.round(12 * Theme.uiScale)
                }
            }

            Text {
                text: card.destination.length ? card.destination : "—"
                color: Theme.textOnGlass
                font: card.numFont(Math.round(23 * Theme.uiScale), true)
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        Row {
            spacing: Math.round(6 * Theme.uiScale)

            Rectangle {
                visible: card.gate.length > 0
                width: gateLabel.width + Math.round(16 * Theme.uiScale)
                height: gateLabel.height + Math.round(7 * Theme.uiScale)
                radius: height / 2
                color: Theme.withAlpha(card.tone, 0.14)
                Text {
                    id: gateLabel
                    anchors.centerIn: parent
                    text: "Gate " + card.gate
                    color: card.tone
                    font: card.numFont(Theme.fontXS, true)
                }
            }

            Rectangle {
                visible: card.status.length > 0
                width: statusLabel.width + Math.round(16 * Theme.uiScale)
                height: statusLabel.height + Math.round(7 * Theme.uiScale)
                radius: height / 2
                color: Theme.withAlpha(Theme.textOnGlass, 0.08)
                Text {
                    id: statusLabel
                    anchors.centerIn: parent
                    text: card.status
                    color: Theme.withAlpha(Theme.textOnGlass, 0.82)
                    font.pixelSize: Theme.fontXS
                    font.family: Theme.fontText
                    font.capitalization: Font.Capitalize
                }
            }
        }
    }
}
