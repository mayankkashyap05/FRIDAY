pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Safety gate: a high-risk action needs explicit approval. Renders the
    Approve / Deny pair and emits the natural-language reply so `main.py`'s
    existing safety flow (verbal consent + token) keeps working untouched.
    Derived from BaseGlassCard: inherits the glass surface, severity rail,
    spring entrance and TTL hairline. Only the payload-specific body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    // Convenience alias so bodies read `card.p.temp_c` instead of
    // `card.payload.temp_c` (and tolerate a null payload).
    readonly property var p: payload !== undefined && payload !== null ? payload : ({})
    // Set by whichever card type needs the front-row highlight; keeps
    // delegates from capturing `index` implicitly.
    property int ctxIndex: 0
    readonly property real titleHeight: Math.round(18 * Theme.uiScale)

    implicitHeight: Math.round(150 * Theme.uiScale)

    signal replied(string text)
    // ── payload-specific body ───────────────────────────────────────────────
    Row {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.round(22 * Theme.uiScale)
        spacing: 8

        Rectangle {
            width: Math.round(96 * Theme.uiScale)
            height: Math.round(30 * Theme.uiScale)
            radius: height / 2
            color: approve.containsMouse ? Theme.withAlpha(Theme.green, 0.30)
                                         : Theme.withAlpha(Theme.green, 0.14)
            border.width: 1
            border.color: Theme.withAlpha(Theme.green, 0.55)
            Text {
                anchors.centerIn: parent
                text: card.p.confirm_label !== undefined ? card.p.confirm_label : "Approve"
                color: Theme.green
                font.pixelSize: Theme.fontS
                font.bold: true
            }
            MouseArea {
                id: approve
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: card.replied("yes, I approve")
            }
        }

        Rectangle {
            width: Math.round(80 * Theme.uiScale)
            height: Math.round(30 * Theme.uiScale)
            radius: height / 2
            color: deny.containsMouse ? Theme.withAlpha(Theme.red, 0.26)
                                      : "transparent"
            border.width: 1
            border.color: Theme.withAlpha(Theme.red, 0.45)
            Text {
                anchors.centerIn: parent
                text: card.p.deny_label !== undefined ? card.p.deny_label : "Deny"
                color: Theme.red
                font.pixelSize: Theme.fontS
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
