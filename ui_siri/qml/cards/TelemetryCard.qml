pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    System vitals as horizontal gauges — instantly readable at a glance.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    customBody: true

    // Fixed catalogue of vitals. `key` is the payload field, so a missing
    // metric (e.g. no battery on a desktop) simply hides its row.
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

    readonly property int shownCount: {
        var n = 0
        for (var i = 0; i < metrics.length; ++i)
            if (metricValue(i) >= 0) n += 1
        return n
    }

    contentBodyHeight: body.implicitHeight

    Column {
        id: body
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(7 * Theme.uiScale)

        Repeater {
            model: card.metrics.length

            delegate: Item {
                id: metricRow
                required property int index

                readonly property real value: card.metricValue(index)
                readonly property string label: card.metrics[index].label
                readonly property real frac: Math.max(0, Math.min(1, value / 100))

                visible: value >= 0
                height: visible ? Math.round(15 * Theme.uiScale) : 0
                width: parent.width

                Text {
                    id: metricLabel
                    anchors.verticalCenter: parent.verticalCenter
                    text: metricRow.label
                    color: Theme.withAlpha(Theme.textOnGlass, 0.5)
                    font.pixelSize: Theme.fontXS
                    font.bold: true
                    font.family: Theme.fontText
                    font.letterSpacing: 0.6
                    width: Math.round(38 * Theme.uiScale)
                }

                Rectangle {
                    id: track
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: metricLabel.right
                    anchors.right: metricValue.left
                    anchors.rightMargin: Math.round(10 * Theme.uiScale)
                    height: Math.round(4 * Theme.uiScale)
                    radius: height / 2
                    color: Theme.withAlpha(Theme.textOnGlass, 0.10)

                    Rectangle {
                        width: Math.max(0, track.width * metricRow.frac)
                        height: parent.height
                        radius: height / 2
                        color: metricRow.frac > 0.88 ? Theme.railError
                             : metricRow.frac > 0.70 ? Theme.railWarn
                             : card.tone
                        opacity: 0.9
                        Behavior on width { NumberAnimation { duration: Theme.durMed } }
                    }
                }

                Text {
                    id: metricValue
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: metricRow.value >= 0 ? Math.round(metricRow.value) + "%" : ""
                    color: Theme.textOnGlass
                    font: card.numFont(Theme.fontXS)
                    width: Math.round(36 * Theme.uiScale)
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
    }
}
