pragma ComponentBehavior: Bound
import QtQuick
import ".."

/*      Plain prose answer. Long replies are clamped with a fade so the stack
    never grows a wall of text.
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

    implicitHeight: Math.min(
        Math.round(320 * Theme.uiScale),
        Math.max(Math.round(96 * Theme.uiScale),
                 card.titleHeight + card.bodyHeight
                 + Theme.pad * 2 + 26))
    // ── payload-specific body ───────────────────────────────────────────────
    Item {
        // body is rendered by the base card; this extra only adds the fade.
        anchors.fill: parent
        Rectangle {
            visible: card.bodyHeight >= Math.round(190 * Theme.uiScale)
            width: parent.width
            height: Math.round(28 * Theme.uiScale)
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.round(26 * Theme.uiScale)
            gradient: Gradient {
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(Theme.panel, 0.85 * Theme.glass) }
            }
        }
    }
}
