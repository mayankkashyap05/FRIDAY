import QtQuick

/*
    Root of the inspector window (Layer 4).

    A dedicated window rather than a panel inside the pill, because the user
    needs a real, resizable, focus-accepting surface for the camera preview,
    the arcade, and history — none of which belong in an ambient overlay.

    The controller pushes everything through `props`, so this file contains no
    backend knowledge at all.
*/
Item {
    id: window
    anchors.fill: parent

    required property var bridge
    required property bool reducedMotion
    property var props: ({})

    InspectorDrawer {
        anchors.fill: parent
        anchors.margins: Math.round(8 * Theme.uiScale)
        bridge: window.bridge
        reducedMotion: window.reducedMotion
        props: window.props
        tab: window.props.tab !== undefined ? window.props.tab : "camera"

        onCloseRequested: {
            if (window.bridge)
                window.bridge.closeInspector()
        }
        onVisionAction: (action, mode) => {
            if (!window.bridge)
                return
            window.bridge.submitText(mode ? ("start vision " + mode)
                                          : "stop vision")
        }
        onArcadeGame: (game) => {
            if (!window.bridge)
                return
            window.bridge.setArcade(game, true)
            window.bridge.submitText("start vision gesture")
        }
        onCommand: (text) => {
            if (window.bridge)
                window.bridge.submitText(text)
        }
        onOpenExternal: (url) => Qt.openUrlExternally(url)
    }
}
