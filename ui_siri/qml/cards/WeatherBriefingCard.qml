pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Weather at a glance: big temperature, condition, humidity/wind chips and
    an optional hourly strip.
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
    // ── payload-specific body ───────────────────────────────────────────────
    Row {
        anchors.top: parent.top
        anchors.topMargin: Math.round(28 * Theme.uiScale)
        spacing: 12

        Text {
            text: card.p.temp_c !== undefined ? Math.round(card.p.temp_c) + "°"
                                              : "--"
            color: card.tone
            font.pixelSize: Math.round(40 * Theme.uiScale)
            font.bold: true
            font.family: "Segoe UI Variable Display, Segoe UI, sans-serif"
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text {
                text: card.p.condition !== undefined ? card.p.condition : ""
                color: Theme.onGlass
                font.pixelSize: Theme.fontM
                font.capitalization: Font.Capitalize
            }
            Text {
                text: (card.p.humidity !== undefined
                       ? card.p.humidity + "% humidity" : "")
                      + (card.p.wind !== undefined ? " · wind " + card.p.wind : "")
                color: Theme.withAlpha(Theme.mid, 1)
                font.pixelSize: Theme.fontXS
                visible: text.length > 0
            }
        }
    }

    // Simple inline sparkline of hourly temps when the backend supplies them.
    Row {
        visible: card.p.hours !== undefined
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.round(24 * Theme.uiScale)
        spacing: 6
        Repeater {
            model: card.p.hours !== undefined ? Math.min(8, card.p.hours.length) : 0
            delegate: Column {
                required property int index
                readonly property var hour: card.p.hours[index]
                spacing: 2
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: parent.hour && parent.hour.temp !== undefined ? Math.round(parent.hour.temp) + "°" : ""
                    color: Theme.withAlpha(Theme.text, 0.8)
                    font.pixelSize: Theme.fontXS
                }
                Rectangle {
                    width: Math.round(14 * Theme.uiScale)
                    height: Math.round(3 * Theme.uiScale)
                    radius: height / 2
                    color: Theme.withAlpha(Theme.pri, 0.55)
                }
            }
        }
    }
}
