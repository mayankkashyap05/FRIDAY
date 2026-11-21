import QtQuick

/*
    The Siri Orb — Apple-calibre volumetric glass sphere at bottom-centre.

    Entirely Canvas-driven (no ShaderEffect, no MultiEffect, no graphics
    shaders of any kind) so it renders identically on every Windows 10/11
    machine regardless of Qt Quick Effects availability.

    Visual anatomy:

      * outer ambient halo — soft luminous glow that floats over the desktop,
        a multi-stop radial gradient fading to full transparency well inside
        the Canvas padding (zero hard outer edge);
      * glass sphere — deep translucent sapphire-indigo core (~88% opacity,
        NEVER pitch black), crisp 1px inner glass rim highlight, delicate
        upper-left crescent sheen;
      * six iridescent fluid ribbons inside the sphere, drawn with
        ``globalCompositeOperation = "lighter"`` (additive) so every overlap
        and crest blooms into pure white-hot Siri silk;
      * white-hot intersection peak at the centre where ribbons cross;
      * every ribbon is strictly clipped to the circular glass edge — nothing
        ever bleeds outside the silhouette;
      * state colours (cyan listening / violet thinking / rose attention /
        muted) and smoothed mic amplitude drive organic ribbon motion;
      * elastic OutElastic spring bloom on summon, InCubic contraction on
        dismiss; dormant = opacity 0 / timer stopped = 0% CPU, 0% footprint.

    Colours (Apple Siri palette, from Theme):
      pri  : cyan/aqua      (listening)
      mid  : sapphire/cobalt
      acc  : magenta/rose   (attention)
      acc2 : iridescent violet (thinking)
      iridE: white-hot (intersection peaks)
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
                                  : orb.speaking ? Theme.pri
                                  : Theme.pri

    // ── ribbon hues that drift inside the orb (cyan → blue → magenta → violet → white-hot) ──
    property var ribbonHues: [Theme.pri, Theme.mid, Theme.acc, Theme.acc2,
                              Theme.pri, Theme.mid]

    // ──────────────────────────────────────────────────────────────────────
    // Ribbon drive — smoothed mic amplitude + continuous phase
    // ──────────────────────────────────────────────────────────────────────
    property real smoothAmplitude: 0.0
    Behavior on smoothAmplitude { NumberAnimation { duration: 80 } }

    onAmplitudeChanged: orb.smoothAmplitude = orb.amplitude

    property real phase: 0
    NumberAnimation on phase {
        running: orb.opacity > 0.01 && Theme.motion > 0
        loops: Animation.Infinite
        from: 0
        to: 1
        duration: 3000
    }

    // Repaint the Canvas whenever the animated inputs change so the ribbons
    // flow silky-smooth at the Canvas timer's 60 FPS.
    onSmoothAmplitudeChanged: canvas.requestPaint()
    onPhaseChanged: canvas.requestPaint()
    onEnergyChanged: canvas.requestPaint()
    onStateColorChanged: canvas.requestPaint()

    // ──────────────────────────────────────────────────────────────────────
    // Canvas — true pixel-accurate circular clipping, radial gradients,
    // curved Bezier fluid waves, and real additive ("lighter") blending.
    // ──────────────────────────────────────────────────────────────────────
    Canvas {
        id: canvas
        anchors.fill: parent
        renderTarget: Canvas.FramebufferObject
        antialiasing: true

        property real paintTime: 0

        // 60+ FPS repaint loop — runs ONLY while the orb is visible
        // (opacity > 0.01). Dormant = timer stopped = 0% CPU.
        Timer {
            id: paintTimer
            interval: 16
            running: orb.opacity > 0.01
            repeat: true
            triggeredOnStart: true
            onTriggered: {
                canvas.paintTime += 0.016
                canvas.requestPaint()
            }
        }

        onPaint: {
            var ctx = getContext("2d")
            var w = canvas.width
            var h = canvas.height
            var cx = w / 2
            var cy = h / 2
            var r = Math.min(w, h) / 2 * 0.90   // sphere radius inside canvas

            var amp = orb.smoothAmplitude
            var energy = orb.energy
            var color = orb.stateColor
            var muted = orb.muted
            var muteFactor = muted ? 0.4 : 1

            // ── QML color → CSS "rgba(r,g,b,a)" helper ──────────────────
            function toCss(c, a) {
                return "rgba(" + c.r + ", " + c.g + ", " + c.b + ", " + a + ")"
            }

            // ────────────────────────────────────────────────────────────
            // 1. Outer ambient halo — OUTSIDE the circle clip.
            //    Multi-stop radial gradient from r*0.7 to r*1.6, fading to
            //    fully transparent well inside the canvas padding.
            // ────────────────────────────────────────────────────────────
            var haloRad = r * 1.6
            var haloGrad = ctx.createRadialGradient(cx, cy, r * 0.7,
                                                    cx, cy, haloRad)
            haloGrad.addColorStop(0.0,
                toCss(color, 0.25 * energy * muteFactor))
            haloGrad.addColorStop(0.45,
                toCss(color, 0.06 * energy * muteFactor))
            haloGrad.addColorStop(1.0, toCss(color, 0.0))
            ctx.fillStyle = haloGrad
            ctx.fillRect(0, 0, w, h)

            // ────────────────────────────────────────────────────────────
            // 2. Clip everything to the glass sphere's silhouette.
            //    Nothing may ever bleed outside this circle.
            // ────────────────────────────────────────────────────────────
            ctx.save()
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, Math.PI * 2)
            ctx.clip()

            // ────────────────────────────────────────────────────────────
            // 3. Deep translucent glass core — sapphire-indigo, ~88% opacity,
            //    NEVER pitch black.
            // ────────────────────────────────────────────────────────────
            var glassGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
            glassGrad.addColorStop(0.0, "rgba(22, 18, 59, 0.88)")
            glassGrad.addColorStop(0.5, "rgba(14, 26, 74, 0.92)")
            glassGrad.addColorStop(1.0, "rgba(30, 15, 61, 0.88)")
            ctx.fillStyle = glassGrad
            ctx.fillRect(0, 0, w, h)

            // ────────────────────────────────────────────────────────────
            // 4. Iridescent fluid ribbons — additive "lighter" compositing.
            //    Six overlapping undulating loops in Apple Siri hues; where
            //    they cross, alpha accumulates into white-hot peaks.
            // ────────────────────────────────────────────────────────────
            ctx.globalCompositeOperation = "lighter"

            var ribbonCount = 6
            var twoPi = Math.PI * 2

            for (var i = 0; i < ribbonCount; i++) {
                var baseAngle = i * (twoPi / ribbonCount)
                var swirl = canvas.paintTime * 0.5
                var angle = baseAngle + swirl + i * 0.4

                // Voice energy pushes ribbons toward the rim.
                var reach = 0.30 + 0.50 * amp + 0.20 *
                            Math.sin(canvas.paintTime * 2.0 + i)
                if (reach > 0.95)
                    reach = 0.95

                var waveAmp = 0.08 + 0.10 * amp
                var waveFreq = 3.0 + i * 0.5

                ctx.save()
                ctx.translate(cx, cy)
                ctx.rotate(angle)

                ctx.beginPath()
                ctx.moveTo(0, 0)

                // Outbound curved ribbon edge.
                ctx.bezierCurveTo(
                    r * 0.15, -r * 0.04,
                    r * reach * 0.5,
                        -r * 0.02 +
                        Math.sin(canvas.paintTime * waveFreq + i) * r * 0.06,
                    r * reach,
                        Math.sin(canvas.paintTime * waveFreq + i * 1.3) *
                        r * 0.10)

                // Inbound edge — offset curve back toward centre.
                ctx.bezierCurveTo(
                    r * reach * 0.55,
                        Math.sin(canvas.paintTime * waveFreq + i) * r * 0.08,
                    r * 0.25, r * 0.06,
                    r * 0.10, r * 0.02)

                ctx.bezierCurveTo(r * 0.02, 0, 0, 0, 0, 0)
                ctx.closePath()

                var grad = ctx.createLinearGradient(0, -r * 0.15, 0, r * 0.15)
                grad.addColorStop(0.0,
                    toCss(orb.ribbonHues[i], 0.5 * muteFactor))
                grad.addColorStop(0.35,
                    toCss("#FFFFFF", 0.8 * muteFactor))
                grad.addColorStop(0.65,
                    toCss("#FFFFFF", 0.8 * muteFactor))
                grad.addColorStop(1.0,
                    toCss(orb.ribbonHues[i], 0.4 * muteFactor))
                ctx.fillStyle = grad
                ctx.fill()

                ctx.restore()
            }

            // ────────────────────────────────────────────────────────────
            // 5. White-hot intersection peak at centre (source-over).
            // ────────────────────────────────────────────────────────────
            ctx.globalCompositeOperation = "source-over"
            var peakOp = (0.5 + 0.5 * Math.sin(canvas.paintTime * 8)) *
                         (0.6 + 0.4 * amp) * muteFactor
            var peakGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy,
                                                    r * 0.14)
            peakGrad.addColorStop(0.0, toCss("#FFFFFF", peakOp))
            peakGrad.addColorStop(1.0, toCss("#FFFFFF", 0.0))
            ctx.fillStyle = peakGrad
            ctx.beginPath()
            ctx.arc(cx, cy, r * 0.14, 0, Math.PI * 2)
            ctx.fill()

            // ────────────────────────────────────────────────────────────
            // 6. Glass rim highlight — crisp 1.5px inner edge stroke.
            // ────────────────────────────────────────────────────────────
            ctx.strokeStyle = toCss("#FFFFFF", 0.25 * muteFactor)
            ctx.lineWidth = 1.5
            ctx.beginPath()
            ctx.arc(cx, cy, r * 0.93, 0, Math.PI * 2)
            ctx.stroke()

            // ────────────────────────────────────────────────────────────
            // 7. Inner crescent sheen — upper-left, soft gradient to 0 alpha.
            //    NEVER a flat opaque grey pill.
            // ────────────────────────────────────────────────────────────
            var sheenGrad = ctx.createRadialGradient(
                cx - r * 0.30, cy - r * 0.30, 0,
                cx - r * 0.30, cy - r * 0.30, r * 0.45)
            sheenGrad.addColorStop(0.0, toCss("#FFFFFF", 0.18 * muteFactor))
            sheenGrad.addColorStop(0.5, toCss("#FFFFFF", 0.02 * muteFactor))
            sheenGrad.addColorStop(1.0, toCss("#FFFFFF", 0.0))
            ctx.fillStyle = sheenGrad
            ctx.beginPath()
            ctx.arc(cx - r * 0.30, cy - r * 0.30, r * 0.45, 0, Math.PI * 2)
            ctx.fill()

            ctx.restore()
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
