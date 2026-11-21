pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Up to four calendar events: the next one is highlighted, every row is
    time-aligned, and the times use tabular digits so the column never shifts.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    customBody: true
    contentBodyHeight: body.implicitHeight

    readonly property int eventCount: p.events !== undefined && p.events.length
                                      ? Math.min(4, p.events.length) : 0

    Column {
        id: body
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(6 * Theme.uiScale)

        Repeater {
            model: card.eventCount
            delegate: Item {
                id: row
                required property int index
                readonly property var ev: card.p.events[index]
                readonly property bool next: index === 0
                width: body.width
                height: Math.round(17 * Theme.uiScale)

                // "Next up" marker: a small filled dot, not a rail.
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.round(5 * Theme.uiScale)
                    height: width
                    radius: width / 2
                    color: row.next ? card.tone : Theme.withAlpha(Theme.textOnGlass, 0.22)
                }

                Text {
                    id: timeText
                    anchors.left: parent.left
                    anchors.leftMargin: Math.round(13 * Theme.uiScale)
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.ev && row.ev.time !== undefined ? row.ev.time : ""
                    color: row.next ? Theme.textOnGlass
                                    : Theme.withAlpha(Theme.textOnGlass, 0.6)
                    font: card.numFont(Theme.fontXS, row.next)
                    width: Math.round(48 * Theme.uiScale)
                }

                Text {
                    anchors.left: timeText.right
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.ev ? (row.ev.title || row.ev.summary || "") : ""
                    color: row.next ? Theme.textOnGlass
                                    : Theme.withAlpha(Theme.textOnGlass, 0.66)
                    font.pixelSize: Theme.fontS
                    font.family: Theme.fontText
                    elide: Text.ElideRight
                }
            }
        }
    }
}
