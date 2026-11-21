import QtQuick
import QtQuick.Effects

/*
    The Siri Orb — Apple-calibre volumetric glass sphere.

    Visual anatomy (no shaders: everything is gradients, layers and blur):

      * outer ambient halo — soft luminous glow driven by ``orb.energy``;
      * glass sphere — razor-sharp circular silhouette, 1px hairline border,
        dark translucent interior with a suspended bottom shadow;
      * inner specular rim highlight — delicate glass glint at top-left;
      * eight iridescent fluid ribbons drifting on independent harmonic
        phases; every lobe is bounded inside the circle silhouette, so no
        clip mask or shader is required; where ribbons intersect, alpha
        accumulates into luminous white-hot peaks;
      * every state interpolates through ``energy`` / ``amplitude`` / state
        colour — never a snap.

    Summoning blooms elastically (OutElastic spring); dismissal contracts
    smoothly (InCubic). Both are driven imperatively so no ``PropertyChanges``
    is needed inside an animation.
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

    readonly property real orbSize: Math.round(128 * Theme.uiScale)
    width: orb.orbSize
    height: orb.orbSize

    readonly property bool listening: orb.assistantState === "LISTENING"
    readonly property bool thinking: orb.assistantState === "THINKING"
                            || orb.assistantState === "PROCESSING"
    readonly property bool speaking: orb.assistantState === "SPEAKING"

    // State colour: cyan by default, violet while thinking, rose on attention,
    // muted red when the mic is off.
    readonly property color stateColor: orb.attention ? Theme.acc
                                  : orb.muted ? Theme.mutcol
                                  : orb.thinking ? Theme.acc2
                                  : Theme.pri

    property var ribbonHues: [Theme.iridA, Theme.iridB, Theme.iridC,
                              Theme.iridD, Theme.iridE, Theme.iridA,
                              Theme.iridB, Theme.iridD]

    // ──────────────────────────────────────────────────────────────────────
    // Derived ribbon behaviour — all read-only, all smooth
    // ──────────────────────────────────────────────────────────────────────
    readonly property real spread: Math.min(1.3,
                    0.5 + 0.5 * orb.energy + 0.35 * orb.amplitude)
    readonly property real glow: 0.28 + 0.62 * orb.energy

    property real phase
    NumberAnimation on phase {
        running: Theme.motion > 0
        loops: Animation.Infinite
        from: 0
        to: 1
        duration: 4000
    }

    // Apparent ribbon speed: idle breath is slow, mic / thinking quickens it,
    // everything scales with visual energy.
    readonly property real speed: {
        var s = 0.45
        if (orb.thinking)
            s = 1.4
        else if (orb.speaking)
            s = 0.9
        if (orb.amplitude > 0.10)
            s = 2.2
        return s * (0.65 + 0.7 * orb.energy)
    }
    Behavior on speed { NumberAnimation { duration: Theme.durMed } }

    // ──────────────────────────────────────────────────────────────────────
    // Outer ambient halo
    // ──────────────────────────────────────────────────────────────────────
    Item {
        id: halo
        width: orb.orbSize * 2.1
        height: halo.width
        anchors.centerIn: parent
        opacity: (0.12 + 0.62 * orb.energy) * (orb.muted ? 0.35 : 1)
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        Rectangle {
            anchors.fill: parent
            radius: parent.width / 2
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.stateColor, 0.62) }
                GradientStop { position: 0.42
                               color: Theme.withAlpha(orb.stateColor, 0.12) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
        MultiEffect {
            id: haloBlur
            source: halo
            visible: Theme.transparency
            blurEnabled: true
            blur: 18
            blurMax: 56
            saturation: 0.05
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Iridescent ribbons suspended inside the sphere. Each ribbon's geometry
    // stays inside the circle silhouette (bounded by width/height and
    // centered x/y), so nothing ever leaks past the glass edge.
    // ──────────────────────────────────────────────────────────────────────
    Item {
        id: ribbons
        anchors.fill: parent

        // Slow orbital turn of the whole ribbon system.
        rotation: orb.phase * 120 * (0.35 + 0.25 * orb.energy)
        Behavior on rotation { NumberAnimation { duration: Theme.durLong } }

        // ── ribbon 1 ──
        Rectangle {
            width: orb.orbSize * 0.48
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(0))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (0 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 0 + orb.phase * 360 * orb.speed * 0.9
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 1.25 + 0)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 0.85 + 0 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[0], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[0],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 2 ──
        Rectangle {
            width: orb.orbSize * 0.54
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(1))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (1 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 24 + orb.phase * 360 * orb.speed * 1.05
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 1.45 + 1)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 0.98 + 1 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[1], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[1],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 3 ──
        Rectangle {
            width: orb.orbSize * 0.61
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(2))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (2 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 48 + orb.phase * 360 * orb.speed * 1.2
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 1.65 + 2)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.11 + 2 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[2], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[2],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 4 ──
        Rectangle {
            width: orb.orbSize * 0.68
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(3))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (3 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 72 + orb.phase * 360 * orb.speed * 1.35
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 1.85 + 3)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.24 + 3 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[3], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[3],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 5 ──
        Rectangle {
            width: orb.orbSize * 0.74
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(4))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (4 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 96 + orb.phase * 360 * orb.speed * 1.5
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 2.05 + 4)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.37 + 4 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[4], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[4],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 6 ──
        Rectangle {
            width: orb.orbSize * 0.81
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(5))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (5 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 120 + orb.phase * 360 * orb.speed * 1.65
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 2.25 + 5)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.50 + 5 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[5], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[5],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 7 ──
        Rectangle {
            width: orb.orbSize * 0.87
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(6))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (6 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 144 + orb.phase * 360 * orb.speed * 1.8
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 2.45 + 6)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.63 + 6 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[6], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[6],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }

        // ── ribbon 8 ──
        Rectangle {
            width: orb.orbSize * 0.94
            height: orb.orbSize * (0.07 + 0.05 * Math.sin(7))
            radius: height / 2
            x: (orb.orbSize - width) / 2
            y: (orb.orbSize - height) / 2 + (7 - 3.5) * 7 * Theme.uiScale
            transformOrigin: Item.Center
            rotation: 168 + orb.phase * 360 * orb.speed * 1.95
            scale: 0.5 + 0.55 * Math.abs(
                    Math.sin(orb.phase * 2.65 + 7)) * (0.55 + 0.5 * orb.spread)
            opacity: orb.glow * (0.3 + 0.55 * (
                    0.5 + 0.5 * Math.sin(orb.phase * 1.76 + 7 * 1.4)))
                    * (orb.muted ? 0.4 : 1)
                    * (orb.attention ? 1.15 : 1)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(orb.ribbonHues[7], orb.glow) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(orb.ribbonHues[7],
                                                      orb.glow * 0.25) }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.height / 2 - 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.42
                                   color: Theme.withAlpha("#FFFFFF", 0.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                opacity: 0.6
                visible: orb.glow > 0.5
            }
            Behavior on scale { NumberAnimation { duration: Theme.durMed } }
            Behavior on rotation { NumberAnimation { duration: Theme.durMed } }
            Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Glass sphere: silhouette, depth, rim highlight (drawn over the ribbons
    // so they read as suspended inside the glass)
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
            border.color: Theme.withAlpha(Theme.textStrong, 0.10)
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha("#111A24", 0.90) }
                GradientStop { position: 0.40
                               color: Theme.withAlpha("#0B141C", 0.94) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha("#070B10", 0.98) }
            }
        }

        // Delicate inner specular rim highlight (top-left glass glint).
        Rectangle {
            width: parent.width * 0.92
            height: parent.height * 0.26
            x: parent.width * 0.04
            y: parent.height * 0.05
            radius: height / 2
            rotation: -24
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(Theme.textStrong, 0.55) }
                GradientStop { position: 0.5
                               color: Theme.withAlpha(Theme.textStrong, 0.08) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            opacity: (0.55 + 0.35 * orb.energy)
        }

        // Suspended-depth shadow (bottom inside the glass).
        Rectangle {
            width: parent.width * 0.86
            height: parent.height * 0.32
            x: parent.width * 0.07
            y: parent.height * 0.64
            radius: height / 2
            color: "#000000"
            opacity: (0.16 + 0.14 * orb.energy) * Theme.glass
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Summoning (elastic spring bloom) and dismissal (reverse contraction)
    // ──────────────────────────────────────────────────────────────────────
    property bool summoning: false
    property bool dismissing: false

    onSummoningChanged: {
        if (orb.summoning) {
            orb.scale = 0.35
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
                              duration: 420 }
        }
    }

    SequentialAnimation {
        id: dismissalAnimation
        ParallelAnimation {
            NumberAnimation { target: orb; property: "scale"; to: 0.3
                              duration: 430
                              easing.type: Easing.InCubic }
            NumberAnimation { target: orb; property: "opacity"; to: 0
                              duration: 380 }
        }
    }
}
