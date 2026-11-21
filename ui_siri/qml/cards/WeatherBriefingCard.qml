pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Weather at a glance: the hero temperature, the condition, a quiet metric
    line and an optional hourly strip.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. The body is one Column anchored to the
    base's content geometry, and the card reports that column's *measured*
    height, so the glass is always exactly as tall as its content — no empty
    band, no clipped row.
*/
BaseGlassCard {
    id: card

    required property var payload

    // Payload alias: tolerate a null payload without every binding testing it.
    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    customBody: true
    contentBodyHeight: bodyContent.implicitHeight

    readonly property bool hasTemp: p.temp_c !== undefined || p.temperature !== undefined
    readonly property real temp: p.temp_c !== undefined ? p.temp_c
                               : p.temperature !== undefined ? p.temperature : 0

    readonly property string condition: p.condition !== undefined ? p.condition
                                     : p.summary !== undefined ? p.summary : ""
    readonly property string meta: {
        var bits = []
        if (p.humidity !== undefined)
            bits.push(p.humidity + "% humidity")
        if (p.wind !== undefined)
            bits.push("wind " + p.wind)
        return bits.join(" · ")
    }
    readonly property int hourCount: p.hours !== undefined && p.hours.length
                                     ? Math.min(6, p.hours.length) : 0

    Column {
        id: bodyContent
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(9 * Theme.uiScale)

        Row {
            spacing: Math.round(13 * Theme.uiScale)

            // Hero value. Digit-uniform, so a temperature update never shifts
            // the layout; the degree ring is drawn, not typed, so it keeps the
            // Apple optical weight at every UI scale.
            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: Math.round(2 * Theme.uiScale)

                Text {
                    text: card.hasTemp ? String(Math.round(card.temp)) : "--"
                    color: Theme.textOnGlass
                    font: card.numFont(Math.round(40 * Theme.uiScale), true)
                }
                Rectangle {
                    width: Math.round(7 * Theme.uiScale)
                    height: width
                    radius: width / 2
                    color: "transparent"
                    border.width: Math.max(1, Math.round(1.5 * Theme.uiScale))
                    border.color: Theme.withAlpha(Theme.textOnGlass, 0.75)
                    anchors.top: parent.top
                    anchors.topMargin: Math.round(4 * Theme.uiScale)
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1

                Text {
                    text: card.condition
                    color: Theme.textOnGlass
                    font.pixelSize: Theme.fontM
                    font.family: Theme.fontText
                    font.capitalization: Font.Capitalize
                    elide: Text.ElideRight
                    width: Math.min(implicitWidth, Math.round(168 * Theme.uiScale))
                }
                Text {
                    text: card.meta
                    visible: text.length > 0
                    color: Theme.withAlpha(Theme.textOnGlass, 0.5)
                    font.pixelSize: Theme.fontXS
                    font.family: Theme.fontText
                    elide: Text.ElideRight
                    width: Math.min(implicitWidth, Math.round(184 * Theme.uiScale))
                }
            }
        }

        // Hourly strip: evenly spaced forecast beads, digits on the tabular grid.
        Row {
            visible: card.hourCount > 0
            spacing: Math.round(12 * Theme.uiScale)

            Repeater {
                model: card.hourCount
                delegate: Column {
                    required property int index
                    readonly property var hour: card.p.hours[index]
                    spacing: 3
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: parent.hour !== undefined && parent.hour.temp !== undefined
                              ? Math.round(parent.hour.temp) + "°" : ""
                        color: Theme.withAlpha(Theme.textOnGlass, 0.62)
                        font: card.numFont(Theme.fontXS)
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: Math.round(16 * Theme.uiScale)
                        height: Math.round(2 * Theme.uiScale)
                        radius: height / 2
                        color: Theme.withAlpha(card.tone, 0.45)
                    }
                }
            }
        }
    }
}
