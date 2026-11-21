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
