import QtQml
import QtQuick

/*
    Root of the click-through overlay window (Layer 1 + 1b).

    Kept deliberately thin: the controller owns the window flags and the Win32
    calls; this file only composes the two effects and forwards the scalars.
*/
Item {
    id: overlay
    anchors.fill: parent

    required property var bridge
    required property bool reducedMotion
    // Palette pushed from Python at construction (setInitialProperties).
    required property var themeData

    // The palette is owned by Python (it mirrors the legacy THEMES table and
    // honours the OS light/dark, reduced-motion and transparency settings).
    // `bridge.theme` is a notifying QVariantMap property, so binding to it
    // keeps the singleton in sync across live theme cycles — and it is a typed
    // object member, so the analyser can resolve it.
    Binding {
        target: Theme
        property: "uiScale"
        value: overlay.bridge !== null && overlay.bridge !== undefined
               ? overlay.bridge.theme.uiScale || 1.0 : 1.0
        when: overlay.bridge !== null && overlay.bridge !== undefined
    }

    function applyTheme(data) {
        Theme.apply(data)
    }

    // Live: `bridge.theme` notifies on every apply_theme()/cycle_theme().
    onBridgeChanged: if (overlay.bridge) overlay.applyTheme(overlay.bridge.theme)

    // Initial: the palette handed to setInitialProperties before construction.
    Component.onCompleted: overlay.applyTheme(overlay.themeData)

    PerimeterGlow {
        anchors.fill: parent
        energy: overlay.bridge ? overlay.bridge.energy : 0.3
        assistantState: overlay.bridge ? overlay.bridge.state : "LISTENING"
        muted: overlay.bridge ? overlay.bridge.muted : false
        dnd: overlay.bridge ? overlay.bridge.dnd : false
        motion: overlay.reducedMotion ? 0 : 1
    }

    ScreenEffects {
        anchors.fill: parent
        scanActive: overlay.bridge ? overlay.bridge.scanActive : false
        reticle: overlay.bridge ? overlay.bridge.reticle : null
        reducedMotion: overlay.reducedMotion
    }
}
