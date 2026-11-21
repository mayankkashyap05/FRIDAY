import QtQuick
import QtQuick.Effects
import "cards"

/*
    SiriWindow — the transient Siri stage.

    It exists only while Siri is invoked. When dormant (idle) the native
    window is hidden entirely — zero desktop footprint, nothing clickable,
    nothing intercepting the mouse. On invocation it pops into view with the
    orb's elastic spring bloom; on dismissal it contracts and fades back to
    invisibility.

    Composition
    -----------
      SiriOrb        (centred stage)
      SnippetStack   (floating glass cards beneath the orb)
      TypeToSiriPill (frosted typing field — appears only on explicit typing)
      SetupCard      (first-run onboarding — only before the API key exists)

    The window never steals focus, never dims the desktop, and is
    click-through outside the orb and active cards.
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
            // native window (dismissalAnimation runs ~430 ms).
            dismissalTimer.start()
        }
    }

    Timer {
        id: dismissalTimer
        interval: 500
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

    // ── stage ─────────────────────────────────────────────────────────────
    Column {
        id: stage
        anchors.centerIn: parent
        spacing: Math.round(14 * Theme.uiScale)

        SiriOrb {
            id: orb
            visible: !root.bridge || !root.bridge.setupVisible
            assistantState: root.bridge ? root.bridge.state : "LISTENING"
            energy: root.bridge ? root.bridge.energy : 0.3
            muted: root.bridge ? root.bridge.muted : false
            amplitude: root.micAmplitude
            attention: root.attention

            // Click the orb to enter Type-to-Siri; click again to leave it.
            MouseArea {
                id: orbTouch
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (!root.typing && (!root.bridge || !root.bridge.setupVisible)) {
                        root.typing = true
                    } else {
                        root.typing = false
                    }
                }
            }
        }

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

        SetupCard {
            id: setup
            visible: root.bridge ? root.bridge.setupVisible : false
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.round(360 * Theme.uiScale)
            reducedMotion: root.reducedMotion
            onSubmit: (key) => root.apiKeySubmitted(key)
        }
    }

    // Type-to-Siri pill — only while explicitly typing.
    TypeToSiriPill {
        id: typePill
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.round(24 * Theme.uiScale)
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

    property bool typing: false

}
