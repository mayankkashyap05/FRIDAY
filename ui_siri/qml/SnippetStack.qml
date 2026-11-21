pragma ComponentBehavior: Bound
import QtQuick
import "cards"

/*
    Layer 3 — transient frosted-glass card stack.

    Placement
    ---------
    Cards hang off the anchor pill. The controller pins this window to the
    right edge of the same monitor as the pill, so the stack never covers the
    thing the user is looking at (there is no scrim: this is ambient UI, not a
    modal).

    Physics
    -------
    Each card is a real delegate in a Repeater driven by `CardsModel`; the
    entrance uses Easing.OutBack (a spring approximation) and the stack
    *compresses* rather than scrolling — cards beyond `maxVisible` fade into
    the stack edge, which is what keeps the UI feeling like a notification
    surface instead of a list.

    No ShaderEffect is used anywhere (see docs/UI_AUDIT_CONTRACT.md §6).
*/
Item {
    id: stack

    required property var model          // CardsModel
    required property bool reducedMotion
    required property real uiScale

    property int maxVisible: 4
    property int spacing: Math.round(10 * Theme.uiScale)

    signal dismissed(string uid)
    signal activated(string uid, string kind, string body)
    signal command(string text)

    implicitWidth: Math.round(360 * Theme.uiScale)
    implicitHeight: column.height

    // Thin rail so the stack reads as an attached surface, not floating debris.
    Rectangle {
        id: rail
        visible: repeater.count > 0
        width: Math.round(2 * Theme.uiScale)
        height: Math.min(parent.height, Math.round(120 * Theme.uiScale))
        radius: width / 2
        x: -Math.round(8 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5
                           color: Theme.withAlpha(Theme.pri, 0.45) }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    Column {
        id: column
        width: parent.width
        spacing: stack.spacing

        Repeater {
            id: repeater
            model: stack.model

            delegate: BaseGlassCard {
                id: cardLoader
                // `kind`, `title`, `body`, `footer`, `severity`, `dismissible`,
                // `age` and `payload` are inherited from BaseGlassCard and are
                // fed straight from the model roles of the same name.
                required property int index
                required property string uid
                required property real ttl

                width: stack.width
                visible: index < stack.maxVisible || index === 0
                opacity: index < stack.maxVisible ? 1.0 : 0.0
                cardTtl: ttl

                // Only the top card is interactive; deeper ones are inert so a
                // stray click never dismisses something the user can't see.
                enabled: index === 0

                // kind / payload already map onto the base card's own
                // `kind` + a free-form payload object, so no Loader swapping
                // (and no per-card component cache to keep in sync) is needed.
                onDismissed: stack.dismissed(cardLoader.uid)
                onActivated: stack.activated(cardLoader.uid, cardLoader.kind,
                                             cardLoader.body)
            }
        }
    }

    // ── per-kind component cache ────────────────────────────────────────────
    Component { id: textComp;      TextCard { } }
    Component { id: weatherComp;   WeatherBriefingCard { } }
    Component { id: calendarComp;  CalendarCard { } }
    Component { id: emailComp;     EmailDigestCard { } }
    Component { id: flightComp;    FlightCard { } }
    Component { id: telemetryComp; TelemetryCard { } }
    Component { id: documentComp;  DocumentQACard { } }

    Component {
        id: confirmComp
        ConfirmationCard {
            onReplied: (text) => stack.command(text)
        }
    }

    Component {
        id: controlComp
        ControlPillCard {
            onCommand: (text) => stack.command(text)
        }
    }

    // ── empty state: a hairline hint, never a big empty box ─────────────────
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: column.bottom
        anchors.topMargin: Math.round(10 * Theme.uiScale)
        visible: repeater.count === 0
        text: qsTr("No cards right now")
        color: Theme.withAlpha(Theme.dim, 0.9)
        font.pixelSize: Theme.fontXS
        font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
    }
}
