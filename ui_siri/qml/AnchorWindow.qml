import QtQuick

/*
    Root of the interactive anchor window (Layer 2 + 3 + 3b).

    This window is the only *interactive* surface in the ambient UI, so it is
    the only one that is neither click-through nor capture-excluded (it is
    excluded from capture, actually, but it does accept input).

    Composition
    -----------
    column!
      ├── AnchorPill      (orb <-> type pill)
      ├── SnippetStack    (cards, grows downward)
      └── LiveActivityBar (background jobs)
*/
Item {
    id: root
    anchors.fill: parent

    required property var bridge
    required property bool reducedMotion
    required property bool setupVisible

    signal apiKeySubmitted(string key)

    property alias stackHeight: stack.implicitHeight
    property alias barHeight: activity.implicitHeight

    // The window is a real (transparent) Win32 window, so it cannot auto-size
    // to its content: the controller must be told. Card arrival/expiry changes
    // `stackHeight`, which is the only thing that matters for clipping.
    onStackHeightChanged: root.contentHeightChanged()
    onBarHeightChanged: root.contentHeightChanged()

    // Pill is pinned top-centre; everything else flows beneath it.
    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        spacing: Math.round(8 * Theme.uiScale)

        SetupCard {
            id: setup
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.round(360 * Theme.uiScale)
            visible: root.setupVisible
            reducedMotion: root.reducedMotion
            onSubmit: (key) => root.apiKeySubmitted(key)
            onVisibleChanged: root.relayout()
        }

        AnchorPill {
            id: pill
            anchors.horizontalCenter: parent.horizontalCenter
            energy: root.bridge ? root.bridge.energy : 0.3
            assistantState: root.bridge ? root.bridge.state : "LISTENING"
            statusLabel: root.bridge ? root.bridge.statusLabel : ""
            muted: root.bridge ? root.bridge.muted : false
            silentMode: root.bridge ? root.bridge.silentMode : false
            connected: root.bridge ? root.bridge.connected : false
            connectionLabel: root.bridge ? root.bridge.connectionLabel : "OFFLINE"
            personaBadge: root.bridge ? root.bridge.personaBadge : ""
            clock: root.bridge ? root.bridge.clock : ""
            reducedMotion: root.reducedMotion

            onSubmit: (text) => { if (root.bridge) root.bridge.submitText(text) }
            onRequestMute: if (root.bridge) root.bridge.toggleMute()
            onRequestSilent: if (root.bridge) root.bridge.toggleSilent()
            onRequestCancel: if (root.bridge) root.bridge.cancel()
            onRequestInspector: if (root.bridge) root.bridge.openInspector("camera")

            onExpandedChanged: root.relayout()
        }

        LiveActivityBar {
            id: activity
            anchors.horizontalCenter: parent.horizontalCenter
            model: root.bridge ? root.bridge.activities : null
            reducedMotion: root.reducedMotion
            onCancel: (aid) => {
                if (root.bridge) root.bridge.toast("Cancel requested: " + aid, "warn")
            }
        }

        SnippetStack {
            id: stack
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.round(360 * Theme.uiScale)
            model: root.bridge ? root.bridge.cards : null
            reducedMotion: root.reducedMotion
            uiScale: Theme.uiScale
            onDismissed: (uid) => { if (root.bridge) root.bridge.dismissCard(uid) }
            onActivated: (uid, kind, body) => {
                // A click means "I have read this": clear the stack so the
                // desktop is given back immediately.
                if (root.bridge)
                    root.bridge.clearCards()
            }
            onCommand: (text) => {
                if (root.bridge) root.bridge.submitText(text)
            }
        }
    }

    // The controller reads these to size the window to its content.
    function contentHeight() {
        return column.height
    }

    function relayout() {
        // Emitted so the controller can re-size the transparent window only
        // when the pill morphs (never on card churn, which would flicker).
        root.contentHeightChanged()
    }

    signal contentHeightChanged()
}
