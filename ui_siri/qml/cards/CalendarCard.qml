pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Up to four events; the next one is highlighted and time-aligned.
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

    implicitHeight: Math.round(130 * Theme.uiScale)
    // ── payload-specific body ───────────────────────────────────────────────
    Column {
        anchors.top: parent.top
        anchors.topMargin: Math.round(28 * Theme.uiScale)
        spacing: 5
        Repeater {
            model: card.p.events !== undefined ? Math.min(4, card.p.events.length) : 0
            delegate: Row {
                required property int index
                readonly property var ev: card.p.events[index]
                spacing: 8
                Rectangle {
                    width: Math.round(3 * Theme.uiScale)
                    height: Math.round(12 * Theme.uiScale)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: card.ctxIndex === 0 ? Theme.acc2 : Theme.withAlpha(Theme.pri, 0.5)
                }
                Text {
                    text: parent.ev && parent.ev.time !== undefined ? parent.ev.time : ""
                    color: Theme.withAlpha(Theme.text, 0.95)
                    font.pixelSize: Theme.fontXS
                    font.bold: true
                    width: Math.round(46 * Theme.uiScale)
                }
                Text {
                    text: parent.ev ? (parent.ev.title || parent.ev.summary || "") : ""
                    color: card.ctxIndex === 0 ? Theme.onGlass
                                       : Theme.withAlpha(Theme.text, 0.75)
                    font.pixelSize: Theme.fontS
                    elide: Text.ElideRight
                    width: Math.round(200 * Theme.uiScale)
                }
            }
        }
    }
}
