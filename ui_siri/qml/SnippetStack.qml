pragma ComponentBehavior: Bound
import QtQuick
import "cards"

/*
    Transient frosted-glass card stack, floating directly above the orb.

    Physics
    -------
    Each row is a *host* that owns the model roles for one card and loads the
    matching typed card (`WeatherBriefingCard`, `CalendarCard`, …) into a
    Loader. The typed card is what renders the payload; the host only forwards
    roles and sizing, so there is exactly one place where a card type is
    chosen and no duplication of the glass surface.

    Why a Loader and not a switch of `sourceComponent` on the stack: a
    `required property` must be initialised by its creator. Declaring the
    per-kind `Component`s *inside* the delegate means each one can bind the
    host's roles directly, so nothing is ever constructed with an unset
    required property (which Qt reports as a console warning).

    The entrance is an Easing.OutBack spring, staggered per row so the column
    blooms top-down instead of appearing all at once. The stack *compresses*
    rather than scrolling — cards beyond `maxVisible` fade into the stack edge,
    which keeps the surface feeling like Siri's own snippets rather than a list.

    Only the front card is interactive; deeper ones are inert so a stray click
    can never dismiss something the user cannot see.

    No ShaderEffect and no MultiEffect is used anywhere (see
    docs/UI_AUDIT_CONTRACT.md §6).
*/
Item {
    id: stack

    required property var model          // CardsModel
    required property bool reducedMotion
    required property real uiScale

    // Hard ceiling on simultaneous cards, and a height budget supplied by the
    // stage. A card that would not fit is not shown at all: a snippet stack
    // must never be clipped mid-glyph, so it drops depth instead of overflowing.
    property int maxVisible: 3
    property real heightBudget: 0        // 0 = unbounded

    // Measured height of each loaded card, keyed by index. Reassigned (never
    // mutated in place) so the visibility bindings above re-evaluate.
    property var cardHeights: ({})

    function noteHeight(index, h) {
        if (!isFinite(h) || h <= 0)
            return
        if (Math.abs((cardHeights[index] || 0) - h) < 0.5)
            return
        var next = {}
        for (var k in cardHeights)
            next[k] = cardHeights[k]
        next[index] = h
        cardHeights = next
    }

    // Does card `index` fit, together with every card above it?
    function fits(index) {
        if (heightBudget <= 0)
            return index < maxVisible
        if (index >= maxVisible)
            return false
        var used = 0
        for (var i = 0; i < index; ++i)
            used += (cardHeights[i] || 0) + Math.round(10 * Theme.uiScale)
        return used + (cardHeights[index] || 0) <= heightBudget
    }

    signal dismissed(string uid)
    signal activated(string uid, string kind, string body)
    signal command(string text)

    implicitWidth: Math.round(360 * Theme.uiScale)
    implicitHeight: column.height

    // Dormant with no cards: zero height, zero paint, zero hit area.
    visible: repeater.count > 0
    height: visible ? implicitHeight : 0

    Column {
        id: column
        width: parent.width
        spacing: Math.round(10 * Theme.uiScale)

        Repeater {
            id: repeater
            model: stack.model

            delegate: Item {
                id: host

                // ── model roles ─────────────────────────────────────────────
                required property int index
                required property string uid
                required property string kind
                required property string title
                required property string body
                required property string footer
                required property var payload
                required property string severity
                required property bool dismissible
                required property real ttl
                required property real age

                readonly property bool front: index === 0
                readonly property bool inView: index < stack.maxVisible || index === 0

                width: stack.width
                height: loader.height

                // Report the measured height so the stack can decide what fits.
                onHeightChanged: stack.noteHeight(host.index, host.height)
                Component.onCompleted: stack.noteHeight(host.index, host.height)

                // The front card is always present; deeper cards only if they
                // fit inside the height budget the stage hands down.
                readonly property bool shown: front || (inView && stack.fits(host.index))

                // Deeper cards sit slightly back so the stack reads as depth.
                visible: shown
                opacity: shown ? 1 - Math.min(0.28, index * 0.14) : 0
                enabled: front
                Behavior on opacity { NumberAnimation { duration: Theme.durShort } }

                signal cardDismissed()
                signal cardActivated()
                signal cardCommand(string text)

                Loader {
                    id: loader
                    width: host.width
                    sourceComponent: host.kind === "weather" ? weatherComp
                                   : host.kind === "calendar" ? calendarComp
                                   : host.kind === "email" ? emailComp
                                   : host.kind === "flight" ? flightComp
                                   : host.kind === "telemetry" ? telemetryComp
                                   : host.kind === "document" ? documentComp
                                   : host.kind === "confirmation" ? confirmComp
                                   : host.kind === "control" ? controlComp
                                   : textComp
                }

                // ── per-kind cards ──────────────────────────────────────────
                // Declared inside the delegate so each can initialise the
                // card's required properties from the host's roles.
                Component {
                    id: textComp
                    TextCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: weatherComp
                    WeatherBriefingCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: calendarComp
                    CalendarCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: emailComp
                    EmailDigestCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: flightComp
                    FlightCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: telemetryComp
                    TelemetryCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: documentComp
                    DocumentQACard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                    }
                }

                Component {
                    id: confirmComp
                    ConfirmationCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                        onReplied: (text) => host.cardCommand(text)
                    }
                }

                Component {
                    id: controlComp
                    ControlPillCard {
                        width: host.width
                        title: host.title
                        body: host.body
                        footer: host.footer
                        severity: host.severity
                        payload: host.payload
                        dismissible: host.dismissible
                        cardTtl: host.ttl
                        age: host.age
                        kind: host.kind
                        stacked: !host.front
                        ctxIndex: host.index
                        entranceDelay: host.index * 70
                        onDismissed: host.cardDismissed()
                        onActivated: host.cardActivated()
                        onCommand: (text) => host.cardCommand(text)
                    }
                }

                onCardDismissed: stack.dismissed(host.uid)
                onCardActivated: stack.activated(host.uid, host.kind, host.body)
                onCardCommand: (text) => stack.command(text)
            }
        }
    }
}
