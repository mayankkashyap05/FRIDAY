pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      System vitals as horizontal gauges — instantly readable at a glance.
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

    implicitHeight: Math.round(140 * Theme.uiScale)

    readonly property var metrics: [
        { "label": "CPU",  "key": "cpu" },
        { "label": "RAM",  "key": "ram" },
        { "label": "DISK", "key": "disk" },
        { "label": "BATT", "key": "battery" }
    ]

    // -1 means "this metric was not supplied by the backend".
    function metricValue(i) {
        const key = metrics[i].key
        return p[key] !== undefined && p[key] !== null ? p[key] : -1
    }
    // ── payload-specific body ───────────────────────────────────────────────
    Column {
        anchors.top: parent.top
        anchors.topMargin: Math.round(28 * Theme.uiScale)
        spacing: 6
        width: parent.width - Math.round(8 * Theme.uiScale)
        Repeater {
            model: 4
            delegate: Row {
                required property int index
                readonly property int rowIndex: index
                readonly property var metric: card.metrics[index]
                visible: card.metricValue(index) >= 0
                spacing: 8
                width: parent.width
                Text {
                    text: parent.metric.label
                    color: Theme.withAlpha(Theme.mid, 1)
                    font.pixelSize: Theme.fontXS
                    width: Math.round(34 * Theme.uiScale)
                    font.bold: true
                }
                Rectangle {
                    width: parent.width - Math.round(92 * Theme.uiScale)
                    height: Math.round(6 * Theme.uiScale)
                    radius: height / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.withAlpha(Theme.dim, 0.55)
                    Rectangle {
                        readonly property real frac:
                            Math.max(0, Math.min(1, metricRow.value / 100))
                        width: parent.width * frac
                        height: parent.height
                        radius: height / 2
                        color: frac > 0.85 ? Theme.red
                             : frac > 0.65 ? Theme.acc2 : Theme.green
                        Behavior on width { NumberAnimation { duration: Theme.durMed } }
                    }
                }
                Text {
                    text: metricRow.value >= 0 ? Math.round(metricRow.value) + "%" : ""
                    color: Theme.onGlass
                    font.pixelSize: Theme.fontXS
                    width: Math.round(34 * Theme.uiScale)
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
    }
}
