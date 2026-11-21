pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Safety gate: a high-risk action needs explicit approval. Renders the
    Approve / Deny pair and emits the natural-language reply so `main.py`'s
    existing safety flow (verbal consent + token) keeps working untouched.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    signal replied(string text)

    // A confirmation is never auto-dismissed while it is unresolved; the
    // controller's grace timer honours this through `busy`.
    customBody: true
    contentBodyHeight: body.implicitHeight

    Row {
        id: body
        x: card.contentLeft
        y: card.contentTop
        spacing: Math.round(8 * Theme.uiScale)

        // Primary action: filled, high-emphasis.
        Rectangle {
            width: Math.max(Math.round(96 * Theme.uiScale),
                            approveLabel.width + Math.round(26 * Theme.uiScale))
            height: Math.round(32 * Theme.uiScale)
            radius: height / 2
            color: approve.containsMouse ? Theme.withAlpha(card.tone, 0.34)
                                         : Theme.withAlpha(card.tone, 0.20)
            border.width: 1
            border.color: Theme.withAlpha(card.tone, 0.55)
            Behavior on color { ColorAnimation { duration: Theme.durMicro } }
            Text {
                id: approveLabel
                anchors.centerIn: parent
                text: card.p.confirm_label !== undefined ? card.p.confirm_label : "Approve"
                color: Theme.textOnGlass
                font.pixelSize: Theme.fontS
                font.bold: true
                font.family: Theme.fontText
            }
            MouseArea {
                id: approve
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: card.replied("yes, I approve")
            }
        }

        // Secondary action: quiet glass, same footprint.
        Rectangle {
            width: Math.max(Math.round(78 * Theme.uiScale),
                            denyLabel.width + Math.round(26 * Theme.uiScale))
            height: Math.round(32 * Theme.uiScale)
            radius: height / 2
            color: deny.containsMouse ? Theme.withAlpha(Theme.textOnGlass, 0.12)
                                      : Theme.withAlpha(Theme.textOnGlass, 0.05)
            border.width: 1
            border.color: Theme.withAlpha(Theme.textOnGlass, 0.14)
            Behavior on color { ColorAnimation { duration: Theme.durMicro } }
            Text {
                id: denyLabel
                anchors.centerIn: parent
                text: card.p.deny_label !== undefined ? card.p.deny_label : "Deny"
                color: Theme.withAlpha(Theme.textOnGlass, 0.8)
                font.pixelSize: Theme.fontS
                font.family: Theme.fontText
            }
            MouseArea {
                id: deny
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: card.replied("no, cancel that")
            }
        }
    }
}
