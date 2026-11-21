import QtQuick

/*
    The Siri Orb — Apple-calibre volumetric glass sphere at bottom-centre.

    Entirely Canvas-driven (no ShaderEffect, no MultiEffect, no graphics
    shaders of any kind) so it renders identically on every Windows 10/11
    machine regardless of Qt Quick Effects availability.

    Optical anatomy, painted bottom-up every frame (the Canvas framebuffer is
    explicitly cleared first — it persists between paints):

      1. feathered multi-hued halo — a soft cyan → violet → rose bloom that has
         already fallen to a few percent when it leaves the glass, so there is
         no visible ring and no hard outer edge;
      2. deep translucent liquid-crystal core — sapphire-indigo-violet glass at
         ~90 % opacity (NEVER pitch black, never muddy grey), lifted toward the
         cool/warm end of the spectrum per state;
      3. iridescent silk bands — three overlapping horizontal ribbons that
         undulate across the sphere at independent angles, phases and
         harmonics. Each band is *stamped* from a soft radial brush along a
         tapered sine path (so nothing has a hard edge: no stroke, no polygon,
         no wireframe), and every stamp is accumulated with
         `globalCompositeOperation = "lighter"` — true additive light. Where
         bands cross, intensity accumulates to pure white-hot Siri silk;
      4. a vertical shading veil that darkens the poles so the bands read as
         *inside* a sphere, with a bounce of refracted colour along the lower
         contour;
      5. a breathing white-hot core at the crossing of the bands;
      6. a razor-thin full rim plus a crisp specular arc along the upper
         contour — Apple's unmistakable glass-edge signature, always strictly
         inside the silhouette;
      7. an upper-left crescent sheen, tucked inside the contour.

    State choreography (never an abrupt snap):

      summon     — elastic OutElastic bloom from micro-scale + luminous flash
      listening  — poised cyan/aqua breathing drift
      speaking   — voice-reactive: bands widen and lift toward the rim, the
                   intersections go white-hot, and they settle with liquid
                   inertia between words (critically damped spring, 60 FPS)
      thinking   — bands gather into a tighter, violet-dominant weave
      acting     — heightened iridescent shimmer (a tool / live action runs)
      muted      — dimmed, near-still
      attention  — warm magenta/rose pulse
      error      — brief rose chromium shift
      dismiss    — reverse-spring contraction to 0 % footprint

    Dormant = opacity 0, scale 0 and the repaint timer stopped: 0 % CPU and
    0 % desktop footprint. Nothing is ever drawn while Siri is asleep.
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
    property bool error: false              // brief chromatic slip
    property bool acting: false             // a tool / live action is running

    // Summon / dismiss drives (set by SiriWindow when `invoked` changes).
    property bool summoning: false
    property bool dismissing: false

    // `orbSize` is the glass *sphere* diameter (Apple's 76–96 px presence).
    // The item itself is larger so the feathered halo can fade to zero inside
    // the canvas instead of being clipped into a hard disc.
    readonly property real orbSize: Math.round(88 * Theme.uiScale)
    readonly property real haloPad: Math.round(orb.orbSize * 0.58)
    readonly property real canvasSize: orb.orbSize + haloPad * 2
    width: orb.canvasSize
    height: orb.canvasSize

    // Dormant = 0 % desktop footprint: invisible until summoned.
    scale: 0
    opacity: 0

    readonly property bool listening: orb.assistantState === "LISTENING"
    readonly property bool thinking: orb.assistantState === "THINKING"
                                     || orb.assistantState === "PROCESSING"
    readonly property bool speaking: orb.assistantState === "SPEAKING"

    // Muted collapses the whole optic to near-invisible stillness.
    readonly property real muteFactor: orb.muted ? 0.4 : 1

    // ── harmonic phase ──────────────────────────────────────────────────────
    // One wall-clock phase drives every ribbon, the chromatic drift and the
    // core pulse, so nothing can fall out of step. It integrates dt, which
    // lets the state change the *rate* without restarting the animation.
    property real phase: 0
    readonly property int phasePeriod: 11000
    readonly property real speedMul: orb.thinking ? 0.66
                                    : orb.speaking ? 1.30
                                    : orb.acting ? 1.12
                                    : 1.0

    Timer {
        id: phaseTimer
        interval: 16
        repeat: true
        running: orb.opacity > 0.01 && Theme.motion > 0
        onTriggered: {
            orb.phase += (0.016 / (orb.phasePeriod / 1000.0)) * orb.speedMul
            if (orb.phase >= 1.0)
                orb.phase -= 1.0
        }
    }

    // ── voice reactivity ────────────────────────────────────────────────────
    // A critically damped spring integrates the (throttled ~30 Hz) mic level
    // into a per-frame value, so the ribbons never step between discrete
    // updates. Tuned for a ~90 ms settle with zero overshoot: it tracks
    // speech cadence and relaxes with liquid inertia.
    property real smoothAmplitude: 0.0
    property real ampVelocity: 0.0
    readonly property real ampTarget: orb.muted ? 0.0 : orb.amplitude

    Timer {
        id: ampIntegrator
        interval: 16
        repeat: true
        running: orb.opacity > 0.01
        onTriggered: {
            if (Theme.motion <= 0) {
                orb.smoothAmplitude = orb.ampTarget
                orb.ampVelocity = 0
                return
            }
            var dt = 0.016
            var stiffness = 165.0
            var damping = 25.0
            var force = (orb.ampTarget - orb.smoothAmplitude) * stiffness
            orb.ampVelocity = (orb.ampVelocity + force * dt) * (1.0 - damping * dt)
            orb.smoothAmplitude = Math.max(0.0, Math.min(1.0,
                                        orb.smoothAmplitude + orb.ampVelocity * dt))
        }
    }

    // ── repaint ─────────────────────────────────────────────────────────────
    onPhaseChanged: canvas.requestPaint()
    onSmoothAmplitudeChanged: canvas.requestPaint()
    onEnergyChanged: canvas.requestPaint()
    onAttentionChanged: canvas.requestPaint()
    onErrorChanged: canvas.requestPaint()
    onActingChanged: canvas.requestPaint()

    // ──────────────────────────────────────────────────────────────────────
    // Canvas
    // ──────────────────────────────────────────────────────────────────────
    Canvas {
        id: canvas
        objectName: "canvas"
        anchors.fill: parent
        renderTarget: Canvas.FramebufferObject
        antialiasing: true

        Timer {
            id: paintTimer
            interval: 16
            running: orb.opacity > 0.01 && Theme.motion > 0
            repeat: true
            triggeredOnStart: true
            onTriggered: canvas.requestPaint()
        }

        onPaint: {
            var w = canvas.width
            var h = canvas.height
            if (w <= 0 || h <= 0 || !Number.isFinite(w) || !Number.isFinite(h))
                return

            var ctx = getContext("2d")
            if (!ctx)
                return

            // A Canvas framebuffer is NOT cleared between paints. Without this
            // every frame accumulates on the last one and the glass over-
            // exposes into a white ring. Reset the compositor too, because the
            // silk pass leaves it on "lighter".
            ctx.globalCompositeOperation = "source-over"
            ctx.globalAlpha = 1.0
            ctx.clearRect(0, 0, w, h)

            var tau = Math.PI * 2
            var cx = w / 2
            var cy = h / 2
            // Sphere radius, with the remainder of the canvas reserved for the
            // halo feathering out to zero.
            var r = Math.min(w, h) / 2 * (orb.orbSize / orb.canvasSize)

            var amp = orb.smoothAmplitude
            var voice = Math.min(1.0, amp * 1.4)
            var energy = Math.max(0.0, Math.min(1.0, orb.energy))
            var mute = orb.muteFactor
            var t = orb.phase

            // ── colour helpers ─────────────────────────────────────────────
            // QML exposes colour channels as 0..1 reals. Derived colours are
            // tagged `_255` and hold 0..255 channels; keeping the two spaces
            // apart in one place prevents the classic "everything is black"
            // bug (a 0..1 channel clamped to 255 is 0).
            function rgb(c) {
                if (c._255)
                    return [Math.round(Math.max(0, Math.min(255, c.r))),
                            Math.round(Math.max(0, Math.min(255, c.g))),
                            Math.round(Math.max(0, Math.min(255, c.b)))]
                return [Math.round(Math.max(0, Math.min(255, c.r * 255))),
                        Math.round(Math.max(0, Math.min(255, c.g * 255))),
                        Math.round(Math.max(0, Math.min(255, c.b * 255)))]
            }
            function css(c, a) {
                var p = rgb(c)
                var alpha = Math.max(0, Math.min(1, Number.isFinite(a) ? a : 1))
                return "rgba(" + p[0] + "," + p[1] + "," + p[2] + ","
                        + alpha.toFixed(3) + ")"
            }
            function mix(c1, c2, k) {
                var a = rgb(c1), b = rgb(c2)
                var kk = Math.max(0, Math.min(1, k))
                return { r: a[0] + (b[0] - a[0]) * kk,
                         g: a[1] + (b[1] - a[1]) * kk,
                         b: a[2] + (b[2] - a[2]) * kk,
                         a: 1.0,
                         _255: true }
            }
            function lighten(c, k) {
                return mix(c, { r: 255, g: 255, b: 255, a: 1, _255: true }, k)
            }
            function disc(x, y, rad) {
                ctx.beginPath()
                ctx.arc(x, y, rad, 0, tau)
                ctx.fill()
            }

            // Character per state: `warm` pushes toward rose/magenta,
            // `cool` toward amethyst (reasoning).
            var warm = orb.attention ? 0.85 : orb.error ? 0.60 : 0.0
            var cool = orb.thinking ? 0.60 : 0.0
            var accent = mix(mix(Theme.pri, Theme.acc2, 0.25 + cool * 0.55),
                             Theme.acc, warm * 0.55)

            // ── 1. feathered ambient halo (outside the clip) ──────────────
            var haloGain = (0.30 + 0.40 * energy) * mute
                     * (0.90 + 0.10 * Math.sin(tau * t * 1.3 + 0.8))
            var halo = ctx.createRadialGradient(cx, cy, r * 0.62, cx, cy, r * 1.72)
            halo.addColorStop(0.00, css(Theme.pri, 0.14 * haloGain))
            halo.addColorStop(0.26, css(mix(Theme.pri, Theme.acc2, 0.55),
                                        0.09 * haloGain))
            halo.addColorStop(0.50, css(Theme.acc2, 0.05 * haloGain))
            halo.addColorStop(0.72, css(mix(Theme.acc2, Theme.acc, 0.6),
                                        0.022 * haloGain))
            halo.addColorStop(1.00, css(Theme.acc, 0.0))
            ctx.fillStyle = halo
            ctx.fillRect(0, 0, w, h)

            // ── clip to the glass sphere ──────────────────────────────────
            ctx.save()
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, tau)
            ctx.clip()

            // ── 2. deep translucent liquid-crystal core ───────────────────
            var glassTop = mix(Theme.orbGlassA, Theme.acc2, cool * 0.25 + warm * 0.10)
            var glassMid = mix(Theme.orbGlassB, Theme.mid, 0.16)
            var glassLow = mix(Theme.orbGlassC, Theme.acc2, 0.10 + cool * 0.12)
            var glass = ctx.createRadialGradient(cx - r * 0.18, cy - r * 0.22, 0,
                                                 cx, cy, r * 1.04)
            glass.addColorStop(0.00, css(lighten(glassTop, 0.14), 0.90))
            glass.addColorStop(0.42, css(glassMid, 0.93))
            glass.addColorStop(0.78, css(glassLow, 0.92))
            glass.addColorStop(1.00, css(mix(glassLow, Theme.dimmer, 0.28), 0.96))
            ctx.fillStyle = glass
            disc(cx, cy, r)

            // ── 3. iridescent silk bands (true additive light) ────────────
            // Each band is a tapered sine ribbon swept across the sphere.
            // It is built from soft brush stamps, never a stroke, so no shape
            // can ever have a hard edge or protrude as a spike.
            ctx.globalCompositeOperation = "lighter"

            var bandCount = 3
            var stamps = 46
            var hueSteps = 11
            var fieldAlpha = (0.72 + 0.26 * energy) * mute
                             * (orb.thinking ? 0.90 : 1.0)
            // Voice widens the bands and lifts them toward the rim; thinking
            // gathers them into a tighter weave.
            var spread = (orb.thinking ? 0.76 : 1.0) * (1.0 + 0.12 * voice)
            var lift = (orb.thinking ? -0.05 : 0.04) * voice
            // Broad brush: the ribbon must read as a band of light about a
            // third of the sphere tall, not as a drawn cord.
            var stampBase = r * (0.30 + 0.04 * voice)

            for (var b = 0; b < bandCount; b++) {
                // Independent rate + irrational offsets: no two bands ever
                // meet at the same phase, so the field cannot settle into a
                // symmetric "lens" or a set of parallel stripes.
                var bp = t * tau * (0.42 + 0.13 * b) + b * 1.37
                // Band axis: spread across the sphere, each rocking slowly.
                var axis = -0.26 + b * 0.24 + 0.10 * Math.sin(bp * 0.7 + b * 1.9)
                var ampY = r * (0.20 + 0.05 * Math.sin(bp * 1.1)) * spread
                // Below one full wave across the sphere: the ribbon S-folds
                // gently instead of curling into a visible ring.
                // Below half a wave across the sphere: the ribbon arcs and
                // tilts instead of folding back through itself (a fold
                // reads as a dark lens at this size).
                var freq = 0.34 + 0.10 * b + 0.06 * Math.sin(bp * 0.5 + b)
                var phase0 = bp * 0.9 + b * 2.11
                var span = r * (0.99 + 0.02 * voice)
                var lean = 0.30 * Math.sin(bp * 0.6 + 1.1)
                var thick = 1.05 + 0.20 * Math.sin(bp * 1.4 + 0.6)

                // Per-band palette: rose at one end, white-hot through the
                // middle, aqua at the other — the real orb's iridescence, with
                // violet dominant while reasoning.
                var hueA = mix(mix(Theme.iridC, Theme.acc, warm * 0.5),
                               Theme.iridD, cool * 0.45)
                var hueB = mix(mix(Theme.iridA, Theme.iridD, 0.24 + cool * 0.60),
                               Theme.iridB, 0.22 + 0.16 * Math.sin(bp))

                // Pre-built gradient ramp: one gradient per hue step, reused by
                // every stamp. 33 gradients per frame instead of 138.
                var ramp = []
                for (var gst = 0; gst < hueSteps; gst++) {
                    var k = gst / (hueSteps - 1)
                    var hue = mix(hueA, hueB, k)
                    // Silk is whitest through the middle of the sphere.
                    var hot = 1.0 - Math.pow(Math.abs(k * 2.0 - 1.0), 1.8)
                    hue = mix(hue, Theme.iridE,
                              (0.28 + 0.10 * voice) * hot)
                    var grd = ctx.createRadialGradient(0, 0, 0, 0, 0, stampBase)
                    // Low per-stamp alpha is deliberate: dozens of stamps
                    // overlap, so the band's body accumulates from many faint
                    // passes (liquid) instead of arriving at full brightness in
                    // one hard slab.
                    grd.addColorStop(0.00, css(lighten(hue, 0.10),
                                               fieldAlpha * (0.085 + 0.05 * hot)))
                    grd.addColorStop(0.42, css(hue,
                                               fieldAlpha * (0.060 + 0.035 * hot)))
                    grd.addColorStop(0.74, css(hue, fieldAlpha * 0.022))
                    grd.addColorStop(1.00, css(hue, 0.0))
                    ramp.push(grd)
                }

                for (var st = 0; st < stamps; st++) {
                    var u = st / (stamps - 1) * 2.0 - 1.0        // −1 … 1
                    // Taper envelope: the ribbon dissolves inside the glass
                    // instead of cutting off at the rim.
                    var taper = Math.pow(Math.cos(Math.min(1, Math.abs(u)) * Math.PI / 2), 0.85)
                    if (taper <= 0.02)
                        continue
                    var yLocal = ampY * Math.sin(u * Math.PI * freq + phase0) * taper
                               + lean * u * r * 0.20 + lift * r
                    var xLocal = u * span
                    var ca = Math.cos(axis), sa = Math.sin(axis)
                    var px = cx + xLocal * ca - yLocal * sa
                    var py = cy + xLocal * sa + yLocal * ca

                    // Dense, heavily overlapped stamps: the individual brush
                    // marks fuse into one continuous silk ribbon.
                    var sc = (0.70 + 0.30 * taper) * thick
                    var slot = Math.round((u * 0.5 + 0.5) * (hueSteps - 1))
                    ctx.save()
                    ctx.translate(px, py)
                    ctx.scale(sc, sc)
                    ctx.fillStyle = ramp[Math.max(0, Math.min(hueSteps - 1, slot))]
                    disc(0, 0, stampBase)
                    ctx.restore()
                }
            }

            // ── 4. pole shading — the bands sit INSIDE a sphere ───────────
            var veil = ctx.createLinearGradient(0, cy - r, 0, cy + r)
            veil.addColorStop(0.00, css(Theme.dimmer, 0.62))
            veil.addColorStop(0.26, css(Theme.dimmer, 0.26))
            veil.addColorStop(0.44, css(Theme.dimmer, 0.0))
            veil.addColorStop(0.62, css(Theme.dimmer, 0.34))
            veil.addColorStop(0.82, css(Theme.dimmer, 0.58))
            veil.addColorStop(1.00, css(Theme.dimmer, 0.72))
            ctx.fillStyle = veil
            ctx.globalCompositeOperation = "source-over"
            disc(cx, cy, r)

            // ── 5. breathing white-hot core ───────────────────────────────
            ctx.globalCompositeOperation = "lighter"
            var corePulse = 0.55 + 0.45 * Math.sin(tau * (t * 2.2 + 0.1))
            var coreA = (0.20 + 0.16 * energy) * (0.55 + 0.45 * voice)
                        * (0.78 + 0.22 * corePulse) * mute
            // Wide and soft: the crossing of the bands is already hot, so the
            // core only has to add the last, largest breath of glow. A narrow
            // gradient here reads as a drawn ring.
            var coreR = r * (0.62 + 0.14 * voice + 0.04 * corePulse)
            var coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR)
            coreGrad.addColorStop(0.00, css(Theme.iridE, Math.min(1, coreA * 1.15)))
            coreGrad.addColorStop(0.30, css(lighten(accent, 0.85), coreA * 0.55))
            coreGrad.addColorStop(0.62, css(lighten(accent, 0.30), coreA * 0.18))
            coreGrad.addColorStop(0.85, css(accent, coreA * 0.04))
            coreGrad.addColorStop(1.00, css(accent, 0.0))
            ctx.fillStyle = coreGrad
            disc(cx, cy, coreR)

            // ── 6. refracted bounce along the lower contour ───────────────
            var bounce = ctx.createRadialGradient(cx, cy + r * 0.66, 0,
                                                  cx, cy + r * 0.66, r * 0.92)
            bounce.addColorStop(0.00, css(mix(accent, Theme.acc2, 0.45),
                                          0.22 * mute))
            bounce.addColorStop(0.50, css(accent, 0.075 * mute))
            bounce.addColorStop(1.00, css(accent, 0.0))
            ctx.fillStyle = bounce
            disc(cx, cy, r)

            // ── 7. optical rim + crisp specular arc ──────────────────────
            ctx.globalCompositeOperation = "source-over"
            // faint full rim: glass thickness
            ctx.lineWidth = Math.max(0.8, r * 0.010)
            ctx.strokeStyle = css(mix(accent, Theme.iridE, 0.55), 0.20 * mute)
            ctx.beginPath()
            ctx.arc(cx, cy, r - ctx.lineWidth * 0.8, 0, tau)
            ctx.stroke()

            // specular arc: the upper contour catches the light, exactly as a
            // filled glass sphere does. Ends fade so it never reads as a ring.
            var arc = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy - r * 0.2)
            arc.addColorStop(0.00, css(Theme.iridE, 0.0))
            arc.addColorStop(0.22, css(Theme.iridE, 0.30 * mute))
            arc.addColorStop(0.50, css(Theme.iridE, 0.72 * mute))
            arc.addColorStop(0.78, css(Theme.iridE, 0.26 * mute))
            arc.addColorStop(1.00, css(Theme.iridE, 0.0))
            ctx.strokeStyle = arc
            ctx.lineWidth = Math.max(1.2, r * 0.030)
            ctx.lineCap = "round"
            ctx.beginPath()
            ctx.arc(cx, cy, r - ctx.lineWidth * 0.9,
                    Math.PI * 1.14, Math.PI * 1.86)
            ctx.stroke()

            // ── 8. crescent sheen, tucked inside the contour ─────────────
            var sheenR = r * 0.58
            var sheenGrad = ctx.createRadialGradient(
                cx - r * 0.28, cy - r * 0.40, 0,
                cx - r * 0.28, cy - r * 0.40, sheenR)
            sheenGrad.addColorStop(0.00, css(Theme.iridE, 0.13 * mute))
            sheenGrad.addColorStop(0.40, css(Theme.iridE, 0.03 * mute))
            sheenGrad.addColorStop(1.00, css(Theme.iridE, 0.0))
            ctx.fillStyle = sheenGrad
            disc(cx - r * 0.28, cy - r * 0.40, sheenR)

            ctx.restore()

            // ── 9. summon bloom (outside the clip: a bloom, not a disc) ──
            if (orb.summoning && Theme.motion > 0) {
                var flash = Math.max(0.0, 1.0 - Math.min(1.0, t * 8.0))
                if (flash > 0.01) {
                    var fg = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 1.9)
                    fg.addColorStop(0.0, css(Theme.iridE, flash * 0.26))
                    fg.addColorStop(0.32, css(Theme.pri, flash * 0.16))
                    fg.addColorStop(1.0, css(Theme.pri, 0.0))
                    ctx.fillStyle = fg
                    ctx.fillRect(0, 0, w, h)
                }
            }
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Summoning (elastic spring bloom) and dismissal (reverse contraction)
    // ──────────────────────────────────────────────────────────────────────
    signal summoned()

    onSummoningChanged: {
        if (orb.summoning) {
            dismissalAnimation.stop()
            orb.scale = 0.15
            orb.opacity = 0
            summoningAnimation.restart()
        } else {
            // A dismissal or an explicit settle mid-bloom must never leave the
            // spring running against a newer target.
            summoningAnimation.stop()
        }
    }
    onDismissingChanged: {
        if (orb.dismissing) {
            summoningAnimation.stop()
            orb.scale = 1
            orb.opacity = 1
            dismissalAnimation.restart()
        } else {
            dismissalAnimation.stop()
        }
    }

    SequentialAnimation {
        id: summoningAnimation
        ScriptAction {
            script: {
                orb.scale = 0.15
                orb.opacity = 0
                orb.summoned()
            }
        }
        ParallelAnimation {
            NumberAnimation {
                target: orb; property: "scale"; to: 1
                duration: Theme.durSummon
                easing.type: Easing.OutElastic
                easing.amplitude: 1.12
                easing.period: 0.34
            }
            NumberAnimation {
                target: orb; property: "opacity"; to: 1
                duration: Theme.reducedMotion ? 160 : 420
                easing.type: Easing.OutCubic
            }
        }
    }

    SequentialAnimation {
        id: dismissalAnimation
        ParallelAnimation {
            NumberAnimation {
                target: orb; property: "scale"; to: 0.32
                duration: Theme.durDismiss
                easing.type: Easing.InCubic
            }
            NumberAnimation {
                target: orb; property: "opacity"; to: 0
                duration: Math.round(Theme.durDismiss * 0.85)
                easing.type: Easing.InQuad
            }
        }
    }
}
