pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Document Q&A: source pages, a highlighted excerpt and jump chips.
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

    // -1 means "this metric was not supplied by the backend".
    function metricValue(i) {
        var key = metrics[i].key
        return p[key] !== undefined && p[key] !== null ? p[key] : -1
    }

    readonly property var metrics: [
        { label: "CPU",  key: "cpu" },
        { label: "RAM",  key: "ram" },
        { label: "DISK", key: "disk" },
        { label: "BATT", key: "battery" }
    ]
    // ── payload-specific body ───────────────────────────────────────────────
    Column {
        anchors.top: parent.top
        anchors.topMargin: Math.round(28 * Theme.uiScale)
        spacing: 6
        width: parent.width

        Row {
            spacing: 6
            Rectangle {
                visible: card.p.pages !== undefined
                width: pagesText.width + 12
                height: pagesText.height + 5
                radius: Theme.radiusChip
                color: Theme.withAlpha(Theme.pri, 0.12)
                border.width: 1
                border.color: Theme.withAlpha(Theme.pri, 0.3)
                Text {
                    id: pagesText
                    anchors.centerIn: parent
                    text: card.p.pages !== undefined ? card.p.pages + " pages" : ""
                    color: Theme.pri
                    font.pixelSize: Theme.fontXS
                }
            }
            Rectangle {
                visible: card.p.source !== undefined
                width: srcText.width + 12
                height: srcText.height + 5
                radius: Theme.radiusChip
                color: Theme.withAlpha(Theme.dim, 0.4)
                Text {
                    id: srcText
                    anchors.centerIn: parent
                    text: card.p.source !== undefined ? card.p.source : ""
                    color: Theme.withAlpha(Theme.text, 0.7)
                    font.pixelSize: Theme.fontXS
                    elide: Text.ElideMiddle
                }
            }
        }

        Text {
            width: card.width - Math.round(40 * Theme.uiScale)
            text: card.p.excerpt !== undefined ? card.p.excerpt : card.body
            color: Theme.withAlpha(Theme.text, 0.85)
            font.pixelSize: Theme.fontXS
            font.italic: true
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
            lineHeight: 1.2
        }
    }
}
