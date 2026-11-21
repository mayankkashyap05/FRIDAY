pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      A single controllable value (volume, brightness, playback). The pills emit
    `command` so the controller can route it back through `on_text_command`.
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

    implicitHeight: Math.round(104 * Theme.uiScale)

    signal command(string text)

    readonly property var actions: p.actions !== undefined
        ? p.actions
        : [{ label: "−", cmd: "reduce" }, { label: "+", cmd: "increase" }]
    // ── payload-specific body ───────────────────────────────────────────────
    Row {
        anchors.top: parent.top
        anchors.topMargin: Math.round(30 * Theme.uiScale)
        spacing: 8

        Rectangle {
            width: Math.round(46 * Theme.uiScale)
            height: Math.round(46 * Theme.uiScale)
            radius: width / 2
            color: Theme.withAlpha(card.tone, 0.16)
            border.width: 1
            border.color: Theme.withAlpha(card.tone, 0.4)
            Text {
                anchors.centerIn: parent
                text: card.p.icon !== undefined ? card.p.icon : "◐"
                color: card.tone
                font.pixelSize: Theme.fontL
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text {
                text: card.p.label !== undefined ? card.p.label : ""
                color: Theme.withAlpha(Theme.mid, 1)
                font.pixelSize: Theme.fontXS
                font.bold: true
                font.letterSpacing: 0.8
            }
            Text {
                text: card.p.value !== undefined
                      ? card.p.value + (card.p.unit !== undefined ? card.p.unit : "")
                      : "--"
                color: Theme.onGlass
                font.pixelSize: Theme.fontXL
                font.bold: true
            }
        }
    }

    Row {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.round(24 * Theme.uiScale)
        spacing: 6
        Repeater {
            model: card.actions
            delegate: Rectangle {
                id: actionPill
                required property var modelData
                required property int index
                // The static analyser cannot type dynamic list models, so
                // role access is funnelled through two helpers.
                readonly property string pillLabel:
                    modelData && modelData.label !== undefined ? modelData.label : ""
                readonly property string pillCommand:
                    modelData && modelData.cmd !== undefined ? modelData.cmd : ""
                width: Math.round(30 * Theme.uiScale)
                height: Math.round(24 * Theme.uiScale)
                radius: Theme.radiusChip
                color: tap.containsMouse ? Theme.withAlpha(card.tone, 0.24)
                                         : Theme.withAlpha(card.tone, 0.10)
                border.width: 1
                border.color: Theme.withAlpha(card.tone, 0.3)
                Text {
                    anchors.centerIn: parent
                    text: actionPill.pillLabel
                    color: card.tone
                    font.pixelSize: Theme.fontS
                    font.bold: true
                }
                MouseArea {
                    id: tap
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: card.command(actionPill.pillCommand)
                }
            }
        }
    }
}
