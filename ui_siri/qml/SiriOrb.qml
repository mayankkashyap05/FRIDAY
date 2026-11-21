import QtQuick
import QtQuick.Effects

/*
    The Siri Orb — Apple-calibre volumetric glass sphere at bottom-centre.

    Visual anatomy (no shaders; pyside6-qsb is not shipped in this wheel):

      * outer ambient halo — soft luminous glow that floats over the desktop,
        driven by ``orb.energy`` and ``orb.smoothAmplitude``;
      * glass sphere — crisp circular silhouette, 1px hairline border,
        dark translucent interior with a suspended bottom shadow;
      * inner specular rim highlight — delicate glass glint at top-left;
      * six iridescent fluid ribbons drifting INSIDE the sphere — each an
        undulating rounded rectangle rotating around the centre, pulsing with
        voice amplitude; where ribbons intersect at the centre, alpha
        accumulates into luminous white-hot peaks;
      * every state interpolates through energy/amplitude/state colour —
        never a snap; strictly clipped to the circular glass edge.

    Colours (Apple Siri palette):
      pri  : cyan/aqua      (listening)
      acc2 : violet         (thinking/processing)
      acc  : magenta/rose   (attention/confirmation)
      mutcol: rose          (muted)
      irid_*: ribbon hues   (cyan → blue → magenta → violet → white-hot)
*/
Item {
    id: orb

    // ──────────────────────────────────────────────────────────────────────
    // Inputs (bound from SiriWindow / the bridge)
    // ──────────────────────────────────────────────────────────────────────
    required property string assistantState // LISTENING | THINKING | PROCESSING | SPEAKING | MUTED | BOOTING
    required property real energy           // 0..1 visual energy (state machine)
    required property bool muted
    property real amplitude: 0.0            // 0..1 live microphone level
    property bool attention: false          // confirmation / attention warm pulse

    readonly property real orbSize: Math.round(84 * Theme.uiScale)
    width: orb.orbSize
    height: orb.orbSize

    // Dormant = 0% desktop footprint: orb is invisible until summoned.
    scale: 0
    opacity: 0

    readonly property bool listening: orb.assistantState === "LISTENING"
    readonly property bool thinking: orb.assistantState === "THINKING"
                            || orb.assistantState === "PROCESSING"
    readonly property bool speaking: orb.assistantState === "SPEAKING"

    // State colour: cyan by default, violet while thinking, rose on attention,
    // muted rose when the mic is off.
    readonly property color stateColor: orb.attention ? Theme.acc
                                  : orb.muted ? Theme.mutcol
                                  : orb.thinking ? Theme.acc2
                                  : Theme.pri

    property var ribbonHues: [Theme.iridA, Theme.iridB, Theme.iridC,
                              Theme.iridD, Theme.iridE, Theme.iridA]

    // ──────────────────────────────────────────────────────────────────────
    // Ribbon drive — continuous rotation + smoothed mic amplitude
    // ──────────────────────────────────────────────────────────────────────
    property real phase
    NumberAnimation on phase {
        running: Theme.motion > 0
        loops: Animation.Infinite
        from: 0
        to: 1
        duration: 3500
    }

    // Smoothed amplitude: removes stepping from the 30 Hz mic feed so ribbons
    // expand/ripple organically instead of jumping.
    property real smoothAmplitude: 0.0
    Behavior on smoothAmplitude { NumberAnimation { duration: 80 } }

    onAmplitudeChanged: orb.smoothAmplitude = orb.amplitude

    // ──────────────────────────────────────────────────────────────────────
    // Outer ambient halo — floats directly over the desktop
    // ──────────────────────────────────────────────────────────────────────
    Item {
        id: halo
        width: orb.orbSize * 2.4
        height: halo.width
        anchors.centerIn: parent
        opacity: (0.08 + 0.55 * orb.energy + 0.35 * orb.smoothAmplitude)
                 * (orb.muted ? 0.30 : 1)
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        Rectangle {
            anchors.fill: parent
            radius: parent.width / 2
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.stateColor, 0.55) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha(orb.stateColor, 0.10) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
        MultiEffect {
            id: haloBlur
            source: halo
            visible: Theme.transparency
            blurEnabled: true
            blur: 20
            blurMax: 60
            saturation: 0.0
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Six iridescent fluid ribbons inside the sphere.
    // Each ribbon rotates around centre, undulates in scale/length, and
    // expands toward the rim with voice amplitude. Everything stays inside
    // the circular glass edge — nothing protrudes.
    // ──────────────────────────────────────────────────────────────────────
    Item {
        id: ribbons
        anchors.centerIn: parent
        width: orb.orbSize * 0.92
        height: ribbons.width

        // ── ribbon 1 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[0], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[0], 0.85) }
            }
        }

        // ── ribbon 2 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4 + 1))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: 60 + orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3 + 1))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[1], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[1], 0.85) }
            }
        }

        // ── ribbon 3 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4 + 2))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: 120 + orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3 + 2))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[2], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[2], 0.85) }
            }
        }

        // ── ribbon 4 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4 + 3))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: 180 + orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3 + 3))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[3], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[3], 0.85) }
            }
        }

        // ── ribbon 5 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4 + 4))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: 240 + orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3 + 4))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[4], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[4], 0.85) }
            }
        }

        // ── ribbon 6 ──
        Rectangle {
            width: orb.orbSize * (0.38 + 0.42 * orb.smoothAmplitude)
            height: orb.orbSize * (0.05 + 0.03 * Math.sin(orb.phase * 4 + 5))
            radius: height / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            transformOrigin: Item.Center
            rotation: 300 + orb.phase * 120
            scale: 0.55 + 0.45 * Math.abs(Math.sin(orb.phase * 3 + 5))
            opacity: (0.35 + 0.5 * orb.energy + 0.4 * orb.smoothAmplitude)
                     * (orb.muted ? 0.4 : 1) * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[5], 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#FFFFFF", 0.65) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[5], 0.85) }
            }
        }

        // White-hot intersection peaks at the centre where ribbons cross.
        Rectangle {
            width: orb.orbSize * 0.14
            height: orb.orbSize * 0.14
            radius: width / 2
            x: (ribbons.width - width) / 2
            y: (ribbons.height - height) / 2
            color: "#FFFFFF"
            opacity: 0.45 * Math.abs(Math.sin(orb.phase * 6))
                     * (0.5 + 0.5 * orb.smoothAmplitude)
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Glass sphere overlay — silhouette, depth, rim highlight
    // (drawn over the ribbons so they read as suspended inside the glass)
    // ──────────────────────────────────────────────────────────────────────
    Item {
        id: sphere
        anchors.centerIn: parent
        width: orb.orbSize
        height: orb.orbSize

        Rectangle {
            id: glass
            anchors.fill: parent
            radius: width / 2
            border.width: 1
            border.color: Theme.withAlpha(Theme.textStrong, 0.12)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha("#111A24", 0.85) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha("#0B141C", 0.92) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha("#070B10", 0.98) }
            }
        }

        // Delicate inner specular rim highlight (top-left glass glint).
        Rectangle {
            width: parent.width * 0.90
            height: parent.height * 0.28
            x: parent.width * 0.05
            y: parent.height * 0.04
            radius: height / 2
            rotation: -20
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(Theme.textStrong, 0.65) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha(Theme.textStrong, 0.05) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            opacity: 0.50 + 0.25 * orb.energy
        }

        // Suspended-depth shadow (bottom inside the glass).
        Rectangle {
            width: parent.width * 0.82
            height: parent.height * 0.30
            x: parent.width * 0.09
            y: parent.height * 0.62
            radius: height / 2
            color: "#000000"
            opacity: (0.14 + 0.12 * orb.energy) * Theme.glass
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Summoning (elastic spring bloom) and dismissal (reverse contraction)
    // ──────────────────────────────────────────────────────────────────────
    property bool summoning: false
    property bool dismissing: false

    onSummoningChanged: {
        if (orb.summoning) {
            orb.scale = 0.15
            orb.opacity = 0
            summoningAnimation.start()
        }
    }
    onDismissingChanged: {
        if (orb.dismissing) {
            orb.scale = 1
            orb.opacity = 1
            dismissalAnimation.start()
        }
    }

    SequentialAnimation {
        id: summoningAnimation
        ParallelAnimation {
            NumberAnimation { target: orb; property: "scale"; to: 1
                              duration: 700
                              easing.type: Easing.OutElastic
                              easing.amplitude: 1.15
                              easing.period: 0.32 }
            NumberAnimation { target: orb; property: "opacity"; to: 1
                              duration: 450 }
        }
    }

    SequentialAnimation {
        id: dismissalAnimation
        ParallelAnimation {
            NumberAnimation { target: orb; property: "scale"; to: 0.15
                              duration: 400
                              easing.type: Easing.InCubic }
            NumberAnimation { target: orb; property: "opacity"; to: 0
                              duration: 350 }
        }
    }
}
