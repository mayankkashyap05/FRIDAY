pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    A single controllable value (volume, brightness, playback): a glass knob, a
    tabular readout, and a pair of stepper capsules that emit `command` so the
    controller can route it back through `on_text_command`.

    Derived from BaseGlassCard: inherits the glass surface, severity accent,
    spring entrance and TTL hairline. Only the payload body is new.
*/
BaseGlassCard {
    id: card

    required property var payload

    readonly property var p: payload !== undefined && payload !== null ? payload : ({})

    signal command(string text)

    readonly property var actions: p.actions !== undefined && p.actions.length
        ? p.actions
        : [{ label: "−", cmd: "reduce" }, { label: "+", cmd: "increase" }]

    readonly property string valueText: p.value !== undefined
        ? String(p.value) + (p.unit !== undefined ? p.unit : "")
        : "—"

    customBody: true
    contentBodyHeight: bodyContent.implicitHeight

    Column {
        id: bodyContent
        x: card.contentLeft
        y: card.contentTop
        width: card.contentWidth
        spacing: Math.round(9 * Theme.uiScale)

        Row {
            spacing: Math.round(12 * Theme.uiScale)

            // Glass knob.
            Rectangle {
                width: Math.round(42 * Theme.uiScale)
                height: width
                radius: width / 2
                color: Theme.withAlpha(card.tone, 0.12)
                border.width: 1
                border.color: Theme.withAlpha(card.tone, 0.3)

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    radius: parent.radius - 1
                    gradient: Gradient {
                        GradientStop {
                            position: 0.0
                            color: Theme.withAlpha(Theme.glassSpecular, 0.10)
                        }
                        GradientStop { position: 0.6; color: "transparent" }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    text: card.p.icon !== undefined ? card.p.icon : "◐"
                    color: card.tone
                    font.pixelSize: Theme.fontL
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0

                Text {
                    text: card.p.label !== undefined
                          ? String(card.p.label).toUpperCase() : ""
                    color: Theme.withAlpha(Theme.textOnGlass, 0.5)
                    font.pixelSize: Theme.fontXS - 1
                    font.bold: true
                    font.family: Theme.fontText
                    font.letterSpacing: 0.9
                }
                Text {
                    text: card.valueText
                    color: Theme.textOnGlass
                    font: card.numFont(Math.round(24 * Theme.uiScale), true)
                }
            }
        }

        Row {
            spacing: Math.round(6 * Theme.uiScale)

            Repeater {
                model: card.actions
                delegate: Rectangle {
                    id: actionPill
                    required property var modelData
                    required property int index
                    // The static analyser cannot type dynamic list models, so
                    // role access is funnelled through two helpers.
                    readonly property string pillLabel:
                        modelData && modelData.label !== undefined ? modelData.label : ""
                    readonly property string pillCommand:
                        modelData && modelData.cmd !== undefined ? modelData.cmd : ""
                    width: Math.round(34 * Theme.uiScale)
                    height: Math.round(26 * Theme.uiScale)
                    radius: height / 2
                    color: tap.containsMouse ? Theme.withAlpha(Theme.textOnGlass, 0.16)
                                             : Theme.withAlpha(Theme.textOnGlass, 0.07)
                    border.width: 1
                    border.color: Theme.withAlpha(Theme.textOnGlass, 0.10)
                    Text {
                        anchors.centerIn: parent
                        text: actionPill.pillLabel
                        color: Theme.textOnGlass
                        font.pixelSize: Theme.fontS
                        font.bold: true
                    }
                    MouseArea {
                        id: tap
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: card.command(actionPill.pillCommand)
                    }
                }
            }
        }
    }
}
