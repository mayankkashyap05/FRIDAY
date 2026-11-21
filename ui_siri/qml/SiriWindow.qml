import QtQuick

/*
    SiriWindow — the transient Siri stage at bottom-centre.

    The window itself is 100 % transparent: no background, no rectangle, no
    frame. Only the orb, the live transcript, the live-activity capsule, the
    floating snippet cards and the Type-to-Siri pill are ever painted, and the
    controller hides the native window entirely when Siri is dormant, so the
    desktop keeps a 0 % visual and input footprint when it is not summoned.

    Composition (a tight, disciplined column centred above the taskbar):

        SnippetStack         floating glass response cards      (top)
        LiveActivityBar      only while a live action runs
        transcript           the latest spoken/typed turn
        SiriOrb              the centrepiece
        TypeToSiriPill       only while the user is typing     (bottom)

    Nothing here steals focus: the window carries WindowDoesNotAcceptFocus, and
    keyboard focus is only ever given to the typing pill when the user
    explicitly asks to type.
*/
Item {
    id: root

    required property var bridge
    required property bool reducedMotion
    required property var themeData

    // True while Siri is present on screen. Driven by the controller
    // (show()/hide() / invoke()/dismiss()). Transitions are animated here.
    property bool invoked: false

    // ── model role helpers ──────────────────────────────────────────────────
    // CardsModel.ROLES order, in one place: a role reorder must show up here
    // and nowhere else. (Magic `Qt.UserRole + n` offsets scattered through QML
    // are exactly how the activity capsule once reported the wrong field.)
    readonly property var cardRoleOrder: ["uid", "kind", "title", "body", "footer",
                                          "payload", "severity", "ts", "ttl",
                                          "dismissible", "age"]

    function cardRole(name) {
        return Qt.UserRole + 1 + cardRoleOrder.indexOf(name)
    }

    // `bridge.cardCount` is a Q_PROPERTY with notify=cardsChanged, so reading it
    // inside a binding is what makes the orb's attention state reactive. A
    // method call (model.rowCount(), or anyCard() alone) creates no dependency
    // and would be evaluated exactly once.
    readonly property int cardCount: root.bridge ? root.bridge.cardCount : 0

    function anyCard(kindName, severityName) {
        if (!root.bridge || !root.bridge.cards)
            return false
        var model = root.bridge.cards
        for (var i = 0; i < model.rowCount(); ++i) {
            var idx = model.index(i, 0)
            var kind = String(model.data(idx, root.cardRole("kind")))
            var sev = String(model.data(idx, root.cardRole("severity")))
            if (kindName !== undefined && kindName !== null && kind !== kindName)
                continue
            if (severityName !== undefined && severityName !== null && sev !== severityName)
                continue
            return true
        }
        return false
    }

    // Live microphone level (0..1) fed from the audio pipeline by the bridge.
    readonly property real micAmplitude: root.bridge ? root.bridge.micAmplitude : 0
    property bool micActive: false
    // ── orb attention / error ───────────────────────────────────────────────
    // Siri's orb is a status light, not just decoration: a confirmation that
    // is waiting on the user warms it, and a failed turn slips it briefly
    // chromatic. Both are derived from the card model, so they are always in
    // step with what is actually on screen — never a timer that can desync.
    readonly property bool attention: {
        // Reading `cardCount` first is what makes this binding reactive: it is
        // a Q_PROPERTY with notify=cardsChanged, whereas the model scan itself
        // is a plain function call and would be evaluated exactly once.
        var n = root.cardCount
        return n > 0 && root.anyCard("confirmation")
    }

    property bool typing: false

    // A failed turn is a *brief chromatic slip*, never a red flash and never a
    // modal. The latch is driven by an error/security-severity card appearing.
    property bool errorFlash: false
    readonly property bool hasErrorCard: {
        var n = root.cardCount
        return n > 0 && (root.anyCard(undefined, "error")
                         || root.anyCard(undefined, "security"))
    }
    onHasErrorCardChanged: {
        if (hasErrorCard && Theme.motion > 0) {
            root.errorFlash = true
            errorLatch.restart()
        }
    }
    Timer {
        id: errorLatch
        interval: 900
        repeat: false
        onTriggered: root.errorFlash = false
    }

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
            dismissalTimer.stop()
            orb.dismissing = false

            // Explicitly clear the latch: after the first dismissal it would
            // otherwise still be true and no second summon would be emitted.
            orb.summoning = false
            orb.summoning = true
        } else {
            orb.summoning = false
            orb.dismissing = true
            root.typing = false
            interactiveRegions = []
            dismissalTimer.start()
        }

        Qt.callLater(root.refreshInteractiveRegions)
    }

    // Visible interactive geometry in stage/window coordinates. The controller
    // uses this to apply native click-through outside the actual Siri surfaces.
    property var interactiveRegions: []

    function appendRegion(list, item, shape) {
        if (!item || !item.visible || item.opacity <= 0.02
                || item.width <= 0 || item.height <= 0)
            return

        const p = item.mapToItem(root, 0, 0)
        list.push({
            "x": p.x,
            "y": p.y,
            "width": item.width,
            "height": item.height,
            "shape": shape || "rect"
        })
    }

    function refreshInteractiveRegions() {
        const next = []
        if (root.invoked) {
            appendRegion(next, orbHit, "ellipse")
            appendRegion(next, stack)
            appendRegion(next, transcriptRow)

            if (activityBar.visible)
                appendRegion(next, activityBar)
            if (typePill.visible)
                appendRegion(next, typePill)
            if (setup.visible)
                appendRegion(next, setup)
        }
        interactiveRegions = next
    }

    Timer {
        interval: 80
        repeat: true
        running: root.invoked
        triggeredOnStart: true
        onTriggered: root.refreshInteractiveRegions()
    }

    Timer {
        id: dismissalTimer
        interval: 480
        repeat: false
        onTriggered: {
            if (!root.invoked)
                root.dismissed()
        }
    }

    // Did the pointer touch anything interactive in the stage? Auto-dismissal
    // pauses while it is true, so a card being read or hovered never vanishes
    // under the user's cursor.
    readonly property bool pointerInside: stageHover.containsMouse
    onPointerInsideChanged: root.interaction()
    signal interaction()

    // ── stage geometry ── one source of truth ────────────────────────────
    // The window is a fixed-size transparent stage. Everything below derives
    // from these numbers, so the card stack can never outgrow the space the
    // window actually has: if the cards do not fit, the stack shows fewer of
    // them instead of letting the window clip the top one mid-glyph.
    readonly property real bottomMargin: Math.round(26 * Theme.uiScale)
    readonly property real topMargin: Math.round(10 * Theme.uiScale)
    readonly property real stageSpacing: Math.round(10 * Theme.uiScale)
    // A plain QQuickView does not resize its root item to the window (the
    // embedded QuickItems of the controller do), so the stage reads the
    // window's real size and never trusts `height` alone. Without this the
    // card budget collapses to 0 in a bare view and the stack shows one card.
    readonly property real windowWidth: Window.width > 0 ? Window.width : width
    readonly property real windowHeight: Window.height > 0 ? Window.height : height
    readonly property real stageMaxHeight: Math.max(0, windowHeight - bottomMargin - topMargin)
    // Space the rows *below* the cards actually need right now. A hidden
    // transcript or an idle activity capsule contributes neither height nor
    // spacing, so the stack gets the full remaining budget instead of paying
    // for rows that are not on screen.
    readonly property real reservedHeight: orbitLane.height
                                           + (transcriptRow.visible ? transcriptRow.height : 0)
                                           + (activityBar.visible ? activityBar.height : 0)
                                           + (setup.visible ? setup.height : 0)
    readonly property int reservedGaps: 1
                                         + (transcriptRow.visible ? 1 : 0)
                                         + (activityBar.visible ? 2 : 0)
                                         + (setup.visible ? 1 : 0)
    readonly property real cardBudget: Math.max(0, stageMaxHeight - reservedHeight
                                                   - reservedGaps * stageSpacing)

    // ── stage ── bottom-centre column ─────────────────────────────────────
    Item {
        id: stage
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: root.bottomMargin
        width: Math.max(stageColumn.implicitWidth, Math.round(340 * Theme.uiScale))
        // The column is bottom-pinned and grows *upward*: a tall card stack
        // extends towards the top of the screen instead of being clipped by
        // the bottom edge, exactly as a Siri snippet stack does.
        height: Math.min(stageColumn.implicitHeight, root.stageMaxHeight)
        clip: false

        Column {
            id: stageColumn
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            width: parent.width
            spacing: root.stageSpacing

            // ── SnippetStack ── floating glass cards above the orb ──────
            SnippetStack {
                id: stack
                visible: root.invoked && (!root.bridge || !root.bridge.setupVisible)
                width: Math.min(parent.width, Math.round(380 * Theme.uiScale))
                // The window is only as tall as the stage, and the stage only
                // as tall as the column; a card stack taller than the screen
                // would be clipped mid-glyph, so it is never allowed to grow
                // past the window. The controller sizes the window for the
                // tallest supported stack (see _layout_windows).
                model: root.bridge ? root.bridge.cards : null
                reducedMotion: root.reducedMotion
                uiScale: Theme.uiScale
                // Height left over above the orb for cards, derived from the
                // same numbers the stage is sized with.
                heightBudget: root.cardBudget
                onDismissed: (uid) => {
                    if (root.bridge) root.bridge.dismissCard(uid)
                    root.interaction()
                }
                onActivated: (uid, kind, body) => {
                    if (root.bridge) root.bridge.clearCards()
                    root.interaction()
                }
                onCommand: (text) => {
                    if (root.bridge) root.bridge.submitText(text)
                }
            }

            // ── LiveActivityBar ── only while background work runs ──────
            LiveActivityBar {
                id: activityBar
                presence: root.invoked
                // The bar owns its visibility (agentCount > 0); forcing it on
                // with `root.invoked` would paint an empty 30px glass slab
                // under the cards and steal clicks over a blank band.
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.round(280 * Theme.uiScale)
                model: root.bridge ? root.bridge.activities : null
                reducedMotion: root.reducedMotion
                onCancel: (aid) => {
                    if (root.bridge) root.bridge.submitText("stop " + aid)
                }
            }

            // ── live transcript ── the last thing said, above the orb ───
            Item {
                id: transcriptRow
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width, Math.round(460 * Theme.uiScale))
                height: visible ? transcript.paintedHeight
                                  + Math.round(16 * Theme.uiScale) : 0
                visible: root.invoked && transcript.text.length > 0
                         && !(root.bridge ? root.bridge.setupVisible : false)

                // A soft dark veil behind the glyphs keeps them legible over
                // any wallpaper — never a visible panel.
                Rectangle {
                    anchors.fill: parent
                    radius: height / 2
                    color: Theme.withAlpha(Theme.dimmer, 0.24 * Theme.glass)
                }

                Text {
                    id: transcript
                    anchors.centerIn: parent
                    width: parent.width - Math.round(24 * Theme.uiScale)
                    horizontalAlignment: Text.AlignHCenter
                    text: {
                        if (!root.bridge)
                            return ""
                        var line = String(root.bridge.lastEvent || "")
                        return line.length > 170 ? line.substring(0, 168) + "…" : line
                    }
                    color: Theme.withAlpha(Theme.text, 0.95)
                    font.pixelSize: Theme.fontM
                    font.family: Theme.fontText
                    font.weight: Font.Medium
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    lineHeight: 1.22
                }
            }

            // ── Siri Orb ── glass sphere + iridescent silk ──
            Item {
                id: orbitLane
                // A fixed-height lane keeps the orb optically centred even as
                // the text above and the pill below appear and disappear, so
                // nothing in the column jitters.
                //
                // While onboarding is up the orb is not painted, so the lane
                // gives its space back: the setup card then has the whole stage
                // to itself instead of being pushed off the bottom edge.
                readonly property bool onboarding: root.bridge
                                                   ? root.bridge.setupVisible : false
                width: orb.canvasSize
                height: onboarding ? 0 : orb.canvasSize
                anchors.horizontalCenter: parent.horizontalCenter

                SiriOrb {
                    id: orb
                    anchors.centerIn: parent
                    visible: !root.bridge || !root.bridge.setupVisible
                    assistantState: root.bridge ? root.bridge.state : "LISTENING"
                    energy: root.bridge ? root.bridge.energy : 0.3
                    muted: root.bridge ? root.bridge.muted : false
                    amplitude: root.micAmplitude
                    attention: root.attention
                    error: root.errorFlash
                    acting: root.bridge ? root.bridge.activeActivities > 0 : false

                    // Clicking the orb is the only way to ask for a keyboard:
                    // voice invocation never takes focus.
                    MouseArea {
                        id: orbHit
                        width: orb.orbSize
                        height: orb.orbSize
                        anchors.centerIn: parent
                        acceptedButtons: Qt.LeftButton
                        cursorShape: Qt.PointingHandCursor
                        hoverEnabled: true

                        onClicked: {
                            root.typing = !root.typing
                            root.interaction()
                        }
                    }
                }
            }

            // ── Type-to-Siri pill (bottom-most, only when typing) ────────
            TypeToSiriPill {
                id: typePill
                anchors.horizontalCenter: parent.horizontalCenter
                open: root.typing && root.invoked
                bridge: root.bridge
                reducedMotion: root.reducedMotion

                onSubmitted: (text) => {
                    root.typing = false
                    if (root.bridge) root.bridge.submitText(text)
                }
                onCanceled: () => { root.typing = false }
            }

            // ── SetupCard ── first-run onboarding (only before a key) ────
            SetupCard {
                id: setup
                visible: root.invoked
                         && (root.bridge ? root.bridge.setupVisible : false)
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.round(360 * Theme.uiScale)
                reducedMotion: root.reducedMotion
                onSubmit: (key) => root.apiKeySubmitted(key)
            }
        }
    }

    // Hover tracking for the auto-dismissal grace period. Inert (no cursor
    // change, no clicks) — it only feeds the controller's pause decision.
    MouseArea {
        id: stageHover
        anchors.fill: stage
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onContainsMouseChanged: {
            if (containsMouse)
                root.interaction()
        }
    }

    // Escape always collapses Siri immediately, from anywhere in the stage.
    Keys.enabled: true
    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Escape) {
            event.accepted = true
            root.typing = false
            root.invoked = false
            if (root.bridge) root.bridge.cancel()
        }
    }

    // The typing pill is the only widget that ever takes keyboard focus, and
    // only because the user clicked the orb to open it.
    onTypingChanged: {
        if (typing && typePill.inputItem)
            typePill.inputItem.forceActiveFocus()
    }

    function focusTyping() {
        root.typing = true
    }
}
