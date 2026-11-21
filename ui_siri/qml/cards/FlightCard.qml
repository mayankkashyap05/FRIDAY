pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Flight status: route, gate and status badge.
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

    implicitHeight: Math.round(118 * Theme.uiScale)
    // ── payload-specific body ───────────────────────────────────────────────
    Row {
        anchors.top: parent.top
        anchors.topMargin: Math.round(30 * Theme.uiScale)
        spacing: 10

        Text {
            text: card.p.origin !== undefined ? card.p.origin
                : card.p.from !== undefined ? card.p.from : "???"
            color: Theme.onGlass
            font.pixelSize: Theme.fontL
            font.bold: true
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "✈"
            color: Theme.withAlpha(Theme.pri, 0.8)
            font.pixelSize: Theme.fontM
        }
        Text {
            text: card.p.destination !== undefined ? card.p.destination
                : card.p.to !== undefined ? card.p.to : "???"
            color: Theme.onGlass
            font.pixelSize: Theme.fontL
            font.bold: true
        }
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: card.p.gate !== undefined
            width: gateText.width + 14
            height: gateText.height + 6
            radius: Theme.radiusChip
            color: Theme.withAlpha(Theme.acc2, 0.16)
            border.width: 1
            border.color: Theme.withAlpha(Theme.acc2, 0.45)
            Text {
                id: gateText
                anchors.centerIn: parent
                text: card.p.gate !== undefined ? "GATE " + card.p.gate : ""
                color: Theme.acc2
                font.pixelSize: Theme.fontXS
                font.bold: true
            }
        }
    }
}
