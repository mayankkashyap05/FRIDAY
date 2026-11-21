import QtQml
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
    // Palette pushed from Python at construction (setInitialProperties).
    required property var themeData
    property var props: ({})

    // The palette is owned by Python (it mirrors the legacy THEMES table and
    // honours the OS light/dark, reduced-motion and transparency settings).
    // `bridge.theme` is a notifying QVariantMap property, so binding to it
    // keeps the singleton in sync across live theme cycles — and it is a typed
    // object member, so the analyser can resolve it.
    Binding {
        target: Theme
        property: "uiScale"
        value: window.bridge !== null && window.bridge !== undefined
               ? window.bridge.theme.uiScale || 1.0 : 1.0
        when: window.bridge !== null && window.bridge !== undefined
    }

    function applyTheme(data) {
        Theme.apply(data)
    }

    // Live: `bridge.theme` notifies on every apply_theme()/cycle_theme().
    onBridgeChanged: if (window.bridge) window.applyTheme(window.bridge.theme)

    // Initial: the palette handed to setInitialProperties before construction.
    Component.onCompleted: window.applyTheme(window.themeData)

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
