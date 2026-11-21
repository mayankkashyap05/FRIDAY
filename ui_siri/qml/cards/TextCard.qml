pragma ComponentBehavior: Bound
import QtQuick

/*
    Plain prose answer.

    The base card renders the body and fades a clamped reply into the glass
    (Text.truncated is exact, whereas a character-count estimate is not), so
    this type needs no body of its own — it exists to give text snippets a
    nameable type in the snippet stack and a place to grow.
*/
BaseGlassCard {
    id: card

    required property var payload

    customBody: false
}
