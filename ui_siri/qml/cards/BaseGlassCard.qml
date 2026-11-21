pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*
    Shared frosted-glass surface for every snippet card.

    All typed cards (TextCard, WeatherBriefingCard, …) derive from this, so the
    stack has exactly one visual grammar:

      * dark frosted glass at ~62 % opacity with a *feathered* ambient shadow
        (concentric rounded rectangles at falling alpha — a real blur look,
        with no shader and no MultiEffect);
      * a 1px luminous specular border plus a 1px inner rim, which is what
        makes a translucent surface read as glass rather than a grey box;
      * a clipped top sheen and a severity-tinted left rail;
      * spring entrance (Easing.OutBack) and a tactile hover lift;
      * tabular numerals available to every derived card via `numFont()`.

    Layout contract
    ---------------
    The card owns three zones and nothing else:

        header   — title, pinned to the top
        content  — the default `content` alias (slot.data). A derived card
                   anchors its own body here, at `bodyInset` from the top.
        footer   — pinned to the bottom, collapsed when empty

    A derived card that renders structured data sets `customBody: true` and
    reports its own height through `contentBodyHeight`; the plain prose body is
    then suppressed so the two never overlap or leave an empty band.
*/
Item {
    id: card

    required property string title
    required property string body
    required property string footer
    required property string severity   // info|success|warn|error|security
    required property real cardTtl
    required property bool dismissible
    required property real age          // seconds since the card was created

    property string kind: "text"
    property bool stacked: false        // true when not the front card

    // Set by whichever card type needs a front-row highlight; keeps delegates
    // from capturing `index` implicitly.
    property int ctxIndex: 0

    signal dismissed()
    signal activated()

    // ── derived-card layout hooks ───────────────────────────────────────────
    // A typed card replaces the prose body with its own content.
    property bool customBody: false
    // Height of that content, reported by the derived card (prose measures
    // itself, so the default is the base's own body column).
    property real contentBodyHeight: bodyColumn.height

    // Measured insets inside the slot. `bodyInset` is the distance from the
    // slot's top edge to the top of the content zone.
    readonly property real bodyInset: header.height + Math.round(6 * Theme.uiScale)
    readonly property real footerInset: footerRow.height > 0
                                        ? footerRow.height + Math.round(10 * Theme.uiScale)
                                        : 0

    readonly property real chromeHeight: Theme.pad * 2 + bodyInset + footerInset
                                         + Math.round(4 * Theme.uiScale)

    // ── content geometry, in card-root coordinates ──────────────────────────
    // A child declared in a *derived* .qml file becomes a child of the card
    // root (not of `slot`, which belongs to this file). Rather than guess the
    // slot's own margins, every derived card anchors to these four numbers, so
    // the body is always aligned with the title and the footer by construction.
    readonly property real contentLeft: Theme.pad + Math.round(9 * Theme.uiScale)
    readonly property real contentRight: Theme.pad
    readonly property real contentTop: Theme.pad + bodyInset
    readonly property real contentBottom: Theme.pad + footerInset
    readonly property real contentWidth: Math.max(0, width - contentLeft - contentRight)

    implicitWidth: Math.round(340 * Theme.uiScale)
    implicitHeight: Math.max(Math.round(78 * Theme.uiScale),
                             chromeHeight + (customBody ? contentBodyHeight
                                                        : bodyColumn.height))

    // Measured content sizes so derived cards can size themselves without
    // reaching into private ids.
    readonly property real bodyHeight: Math.min(bodyColumn.height,
                                                Math.round(200 * Theme.uiScale))
    readonly property real titleHeight: Math.round(18 * Theme.uiScale)

    readonly property color tone: severity === "error" ? Theme.railError
                                : severity === "security" ? Theme.railSecurity
                                : severity === "warn" ? Theme.railWarn
                                : severity === "success" ? Theme.railSuccess
                                : Theme.railInfo

    // Metrics, prices and times: digit-uniform columns, no jitter. Returned as
    // a Qt font object so a single binding sets family, size, weight and the
    // OpenType `tnum` feature together.
    function numFont(size, bold) {
        return Qt.font({
            "family": Theme.fontNumeric,
            "pixelSize": size !== undefined ? size : Theme.fontS,
            "bold": bold === true,
            "features": Theme.tabularFeatures
        })
    }

    // ── entrance: springy settle, suppressed when reduced-motion ────────────
    property real entrance: 0
    // Stagger hook: a stack can delay deeper cards without touching the
    // animation itself (the module-level lint rule forbids a value source and
    // a binding on the same property).
    property int entranceDelay: 0
    // Baseline lift while the entrance animates; a derived card that wants a
    // different entrance curve may rebind this freely.
    property real entranceLift: 18

    Component.onCompleted: {
        if (Theme.motion > 0) {
            entranceAnim.from = 0
            entranceAnim.to = 1
            entranceAnim.start()
        } else {
            entrance = 1
        }
    }

    NumberAnimation {
        id: entranceAnim
        target: card
        property: "entrance"
        duration: Theme.durCard + card.entranceDelay
        easing.type: Easing.OutBack
        easing.overshoot: 1.12
    }
    // Entrance slide + a 2px tactile lift on hover, in one transform so the
    // two never fight over the same property.
    transform: Translate {
        y: (1 - card.entrance) * Math.round(card.entranceLift * Theme.uiScale) * Theme.motion
           - (hover.containsMouse && !card.stacked ? 2 * Theme.motion : 0)
        Behavior on y { NumberAnimation { duration: Theme.durMicro } }
    }

    // ── feathered ambient shadow ────────────────────────────────────────────
    // Two soft silhouettes *behind* the card, each a filled rounded rectangle
    // scaled out with a falling alpha. A stroked rectangle of width 0 paints a
    // 1px hairline on some renderers, which is exactly the grey outline this
    // must never show, so the shadow is filled-only.
    Repeater {
        model: Theme.glass > 0 ? 2 : 0
        delegate: Rectangle {
            required property int index
            z: -2
            anchors.centerIn: surface
            width: surface.width + (10 + index * 12) * Theme.uiScale
            height: surface.height + (10 + index * 12) * Theme.uiScale
            radius: surface.radius + (5 + index * 6) * Theme.uiScale
            color: Theme.shadowColor
            opacity: (0.30 - index * 0.13) * Theme.glass
        }
    }

    // ── glass surface ───────────────────────────────────────────────────────
    Rectangle {
        id: surface
        anchors.fill: parent
        radius: Theme.radiusCard
        color: Theme.glass > 0 ? Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
                               : Theme.panel
        border.width: 1
        border.color: hover.containsMouse
                      ? Theme.glassBorderHover
                      : Theme.glassBorder

        Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

        // Glass thickness. A real frosted pane catches light along its *top*
        // edge only — a full inner ring reads as a second border, which is the
        // "double outline" look this material must never have. So: one hairline
        // across the top, fading out before the corners.
        Rectangle {
            visible: Theme.glass > 0
            width: parent.width - 2 * parent.radius * 0.55
            height: 1
            x: (parent.width - width) / 2
            y: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop {
                    position: 0.22
                    color: Theme.withAlpha(Theme.glassInnerRim, 0.95 * Theme.glass)
                }
                GradientStop {
                    position: 0.78
                    color: Theme.withAlpha(Theme.glassInnerRim, 0.95 * Theme.glass)
                }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }

        // Clipped top sheen: a soft specular gradient, never a grey slab.
        Rectangle {
            width: parent.width - 2
            height: parent.height / 2.6
            x: 1; y: 1
            radius: parent.radius
            visible: Theme.glass > 0
            gradient: Gradient {
                GradientStop {
                    position: 0.0
                    color: Theme.withAlpha(Theme.glassSpecular,
                                           0.085 * Theme.glass)
                }
                GradientStop {
                    position: 0.5
                    color: Theme.withAlpha(Theme.glassSpecular,
                                           0.018 * Theme.glass)
                }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }

        // Severity accent — a slim inset capsule that fades out at both ends,
        // so it reads as a coloured mark on the glass rather than a HUD bar or
        // a second coloured border.
        Rectangle {
            width: Math.max(2, Math.round(2.5 * Theme.uiScale))
            height: Math.max(0, parent.height - Theme.pad * 2.4)
            radius: width / 2
            x: Math.round(9 * Theme.uiScale)
            anchors.verticalCenter: parent.verticalCenter
            gradient: Gradient {
                GradientStop { position: 0.0; color: Theme.withAlpha(card.tone, 0.0) }
                GradientStop { position: 0.18; color: Theme.withAlpha(card.tone, 0.85) }
                GradientStop { position: 0.82; color: Theme.withAlpha(card.tone, 0.85) }
                GradientStop { position: 1.0; color: Theme.withAlpha(card.tone, 0.0) }
            }
        }
    }

    // ── content ─────────────────────────────────────────────────────────────
    Item {
        id: slot
        anchors.fill: parent
        anchors.margins: Theme.pad
        // Leave room for the severity accent, which is inset from the glass
        // edge; content should never sit on top of it.
        anchors.leftMargin: Theme.pad + Math.round(9 * Theme.uiScale)

        Row {
            id: header
            width: parent.width

            Text {
                width: parent.width
                text: card.title
                color: Theme.textOnGlass
                font.pixelSize: Theme.fontS
                font.bold: true
                font.family: Theme.fontDisplay
                elide: Text.ElideRight
            }
        }

        Column {
            id: bodyColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: header.bottom
            anchors.topMargin: Math.round(6 * Theme.uiScale)
            width: parent.width
            spacing: 4
            visible: !card.customBody

            Text {
                id: prose
                width: parent.width
                text: card.customBody ? "" : card.body
                visible: text.length > 0
                color: Theme.withAlpha(Theme.textOnGlass, 0.82)
                font.pixelSize: Theme.fontS
                font.family: Theme.fontText
                wrapMode: Text.Wrap
                maximumLineCount: 6
                elide: Text.ElideRight
                lineHeight: 1.18
            }

            // A clamped reply dissolves into the glass instead of being cut off.
            // Inside the column it simply overlays the last lines of `prose`.
            Rectangle {
                visible: prose.truncated
                width: parent.width
                height: Math.round(22 * Theme.uiScale)
                y: prose.y + prose.height - height
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop {
                        position: 1.0
                        color: Theme.withAlpha(Theme.panel, 0.92 * Theme.glass)
                    }
                }
            }
        }

        Row {
            id: footerRow
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            spacing: 8

            Text {
                width: parent.width - (closeBtn.visible ? 22 : 0)
                text: card.footer
                visible: text.length > 0
                color: Theme.withAlpha(Theme.textOnGlass, 0.46)
                font.pixelSize: Theme.fontXS
                font.family: Theme.fontText
                elide: Text.ElideRight
            }
        }
    }

    // ── dismiss ─────────────────────────────────────────────────────────────
    Rectangle {
        id: closeBtn
        visible: card.dismissible && hover.containsMouse
        width: Math.round(18 * Theme.uiScale)
        height: width
        radius: width / 2
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 8
        anchors.bottomMargin: 8
        color: closeArea.containsMouse ? Theme.withAlpha(Theme.red, 0.22)
                                       : "transparent"
        Text {
            anchors.centerIn: parent
            text: "✕"
            color: Theme.withAlpha(Theme.textOnGlass, 0.7)
            font.pixelSize: Theme.fontXS
        }
        MouseArea {
            id: closeArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: card.dismissed()
        }
    }

    // ── TTL progress hairline ───────────────────────────────────────────────
    Rectangle {
        readonly property real life: card.cardTtl > 0
            ? Math.max(0, 1 - Math.min(1, card.age / card.cardTtl)) : 1
        visible: card.cardTtl > 0 && life > 0
        width: Math.max(0, (parent.width - 2) * life)
        height: Math.round(2 * Theme.uiScale)
        radius: height / 2
        x: 1
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        color: Theme.withAlpha(card.tone, 0.55)
    }

    // ── interaction ─────────────────────────────────────────────────────────
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: (mouse) => {
            if (mouse.button === Qt.LeftButton) card.activated()
            else card.dismissed()
        }
    }

}
