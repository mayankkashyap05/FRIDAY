pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Inbox digest: unread dot, sender, subject — three calm columns aligned on a
    single baseline grid, so a digest of four mails still reads as one object.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    customBody: true
    contentBodyHeight: bodyContent.implicitHeight

    readonly property int mailCount: p.emails !== undefined && p.emails.length
                                     ? Math.min(4, p.emails.length) : 0

    Column {
        id: bodyContent
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(5 * Theme.uiScale)

        Repeater {
            model: card.mailCount
            delegate: Item {
                id: row
                required property int index
                readonly property var mail: card.p.emails[index]
                readonly property bool unread: mail !== undefined
                                               && mail.unread !== undefined
                                               && mail.unread === true
                width: bodyContent.width
                height: Math.round(17 * Theme.uiScale)

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.round(5 * Theme.uiScale)
                    height: width
                    radius: width / 2
                    color: row.unread ? card.tone
                                      : Theme.withAlpha(Theme.textOnGlass, 0.22)
                }

                Text {
                    id: fromText
                    anchors.left: parent.left
                    anchors.leftMargin: Math.round(13 * Theme.uiScale)
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.round(104 * Theme.uiScale)
                    text: row.mail && row.mail.from !== undefined ? row.mail.from : ""
                    color: row.unread ? Theme.textOnGlass
                                      : Theme.withAlpha(Theme.textOnGlass, 0.6)
                    font.pixelSize: Theme.fontXS
                    font.family: Theme.fontText
                    font.bold: row.unread
                    elide: Text.ElideRight
                }

                Text {
                    anchors.left: fromText.right
                    anchors.leftMargin: Math.round(8 * Theme.uiScale)
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.mail ? (row.mail.subject || row.mail.title || "") : ""
                    color: row.unread ? Theme.textOnGlass
                                      : Theme.withAlpha(Theme.textOnGlass, 0.7)
                    font.pixelSize: Theme.fontS
                    font.family: Theme.fontText
                    elide: Text.ElideRight
                }
            }
        }
    }
}
