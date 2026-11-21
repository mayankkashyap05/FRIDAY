import QtQuick
import QtQuick.Effects
import "cards"

/*
    SiriWindow — the transient Siri stage at bottom-centre.

    The window itself is 100% transparent (no background, no rectangle). Only
    the Siri Orb, floating snippet cards and the Type-to-Siri pill are
    visible — everything else is click-through, invisible, zero footprint.

    When dormant (invoked = false) the orb is scale 0 / opacity 0 and the
    native window can be hidden entirely by the controller.

    Composition (bottom-centre column):
      TypeToSiriPill   (bottom, only when typing)
      SiriOrb          (centrepiece — glass sphere + iridescent ribbons)
      SnippetStack     (floating glass cards above the orb)

    The window never steals focus (WS_EX_NOACTIVATE), never dims the desktop,
    and is click-through outside the orb and active cards.
*/
Item {
    id: root

    required property var bridge
    required property bool reducedMotion
    required property var themeData

    // True while Siri is present on screen. Driven by the controller
    // (show()/hide() / invoke()/dismiss()). Transitions are animated here.
    property bool invoked: false

    // Live microphone level (0..1) fed from the audio pipeline by the bridge.
    // Bound to bridge.micAmplitude so live mic energy drives the orb ribbons.
    readonly property real micAmplitude: root.bridge ? root.bridge.micAmplitude : 0
    property bool micActive: false
    property bool attention: false

    signal apiKeySubmitted(string key)
    // Fired once the dismissal animation has finished and the native window
    // may be hidden; connected to the controller from Python (no context
    // property is needed on the QML side, which keeps qmllint clean).
    signal dismissed()

    // ── lifecycle ─────────────────────────────────────────────────────────
    Component.onCompleted: {
        // Start fully invisible even if the native window is shown while idle.
        root.invoked = false
        applyTheme(themeData)
    }

    function applyTheme(data) {
        Theme.apply(data)
    }

    onInvokedChanged: {
        if (invoked) {
            orb.summoning = true
            orb.dismissing = false
        } else {
            orb.dismissing = true
            // Let the contraction finish before the controller hides the
            // native window (dismissalAnimation runs ~400 ms).
            dismissalTimer.start()
        }
    }

    Timer {
        id: dismissalTimer
        interval: 450
        repeat: false
        onTriggered: {
            if (!root.invoked)
                root.dismissed()
        }
    }

    // Escape always collapses Siri immediately.
    Keys.enabled: true
    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Escape) {
            event.accepted = true
            root.invoked = false
            if (root.bridge) root.bridge.cancel()
        }
    }

    // ── stage ── bottom-centre column ─────────────────────────────────────
    Column {
        id: stage
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.round(48 * Theme.uiScale)
        spacing: Math.round(10 * Theme.uiScale)

        // ── Type-to-Siri pill (bottom-most, only when explicitly typing) ──
        TypeToSiriPill {
            id: typePill
            visible: root.typing
            opacity: root.typing ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
            bridge: root.bridge
            reducedMotion: root.reducedMotion

            onSubmitted: (text) => {
                root.typing = false
                if (root.bridge) root.bridge.submitText(text)
            }
            onCanceled: () => { root.typing = false }
        }

        // ── Siri Orb ── glass sphere + iridescent ribbons ──
        SiriOrb {
            id: orb
            visible: !root.bridge || !root.bridge.setupVisible
            assistantState: root.bridge ? root.bridge.state : "LISTENING"
            energy: root.bridge ? root.bridge.energy : 0.3
            muted: root.bridge ? root.bridge.muted : false
            amplitude: root.micAmplitude
            attention: root.attention
        }

        // ── SnippetStack ── floating glass response cards above the orb ──
        SnippetStack {
            id: stack
            visible: !root.bridge || !root.bridge.setupVisible
            width: Math.round(380 * Theme.uiScale)
            model: root.bridge ? root.bridge.cards : null
            reducedMotion: root.reducedMotion
            uiScale: Theme.uiScale
            onDismissed: (uid) => { if (root.bridge) root.bridge.dismissCard(uid) }
            onActivated: (uid, kind, body) => {
                if (root.bridge) root.bridge.clearCards()
            }
            onCommand: (text) => {
                if (root.bridge) root.bridge.submitText(text)
            }
        }

        // ── SetupCard ── first-run onboarding (only before API key exists) ──
        SetupCard {
            id: setup
            visible: root.bridge ? root.bridge.setupVisible : false
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.round(360 * Theme.uiScale)
            reducedMotion: root.reducedMotion
            onSubmit: (key) => root.apiKeySubmitted(key)
        }
    }

    property bool typing: false

}
