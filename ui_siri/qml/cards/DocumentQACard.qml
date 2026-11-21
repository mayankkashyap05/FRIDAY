pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Document Q&A: a source line, then the quoted excerpt with a genuine hanging
    quote mark — the one place in the product where serif-italic prose is right.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    readonly property string excerpt: p.excerpt !== undefined ? String(p.excerpt) : ""
    readonly property string source: p.source !== undefined ? String(p.source) : ""
    readonly property var pages: p.pages !== undefined ? p.pages : 0

    readonly property string sourceLine: {
        var bits = []
        if (source.length) bits.push(source)
        if (pages) bits.push(pages + (pages === 1 ? " page" : " pages"))
        return bits.join(" · ")
    }

    customBody: true
    contentBodyHeight: bodyContent.implicitHeight

    Column {
        id: bodyContent
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(8 * Theme.uiScale)

        // Quoted excerpt. This is the one place in the product where a serif
        // italic voice is right: it reads as a citation, not as UI chrome.
        Text {
            id: quote
            width: parent.width
            text: card.excerpt.length ? "\u201c" + card.excerpt + "\u201d" : card.body
            color: Theme.withAlpha(Theme.textOnGlass, 0.9)
            font.pixelSize: Theme.fontS
            font.family: Theme.fontText
            font.italic: true
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
            lineHeight: 1.24
        }

        Text {
            visible: card.sourceLine.length > 0
            text: card.sourceLine
            color: Theme.withAlpha(Theme.textOnGlass, 0.45)
            font: card.numFont(Theme.fontXS)
            elide: Text.ElideMiddle
            width: parent.width
        }
    }
}
