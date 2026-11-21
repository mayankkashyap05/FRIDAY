pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Inbox digest: sender + subject per row, unread count as a chip.
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
        spacing: 4
        Repeater {
            model: card.p.emails !== undefined ? Math.min(4, card.p.emails.length) : 0
            delegate: Row {
                required property int index
                readonly property var mail: card.p.emails[index]
                spacing: 6
                Rectangle {
                    width: Math.round(5 * Theme.uiScale)
                    height: width
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.acc
                }
                Text {
                    text: parent.mail && parent.mail.from !== undefined ? parent.mail.from : ""
                    color: Theme.withAlpha(Theme.text, 0.82)
                    font.pixelSize: Theme.fontXS
                    width: Math.round(96 * Theme.uiScale)
                    elide: Text.ElideRight
                }
                Text {
                    text: parent.mail ? (parent.mail.subject || parent.mail.title || "") : ""
                    color: Theme.onGlass
                    font.pixelSize: Theme.fontS
                    elide: Text.ElideRight
                    width: Math.round(160 * Theme.uiScale)
                }
            }
        }
    }
}
