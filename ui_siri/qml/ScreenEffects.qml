pragma ComponentBehavior: Bound
import QtQuick

/*
    Layer 1b — screen-scan wave + vision_click target reticle.

    Both effects live on the click-through fullscreen overlay so they can point
    at *anything* the user can see:

    * **Scan wave** — a single sweeping gradient band plus a faint grid that
      runs only while a screen read / vision capture is in flight. It is the
      only honest way to say "I am looking at your screen" without a modal.
    * **Reticle** — a Target-lock reticle that snaps to the coordinates
      `actions/vision_click.py` resolved, so the click is never a surprise.

    Everything animates from scalar properties; no polling, no shaders.
*/
Item {
    id: effects
    anchors.fill: parent

    required property bool scanActive
    required property var reticle          // {x, y, visible}
    required property bool reducedMotion

    property color accent: Theme.pri

    // ────────────────────────────────────────────────────────────────────────
    // Scan wave
    // ────────────────────────────────────────────────────────────────────────
    Item {
        id: scan
        anchors.fill: parent
        // Explicit alias: inner Items also use the id `effects`, so bind the
        // accent once at this level instead of repeatedly across scopes.
        readonly property color accentColor: effects.accent
        visible: effects.scanActive
        opacity: effects.scanActive ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        // Corner brackets frame the captured region.
        Repeater {
            model: [
                { hx: 0, hy: 0, sx: 1, sy: 1 },
                { hx: 1, hy: 0, sx: -1, sy: 1 },
                { hx: 0, hy: 1, sx: 1, sy: -1 },
                { hx: 1, hy: 1, sx: -1, sy: -1 }
            ]
            delegate: Item {
                id: corner
                // The static analyser cannot type a JS-array model; read the
                // two coordinates once and derive everything from them.
                readonly property bool isLeft: modelData.hx === 0
                readonly property bool isTop: modelData.hy === 0
                required property var modelData
                required property int index
                readonly property real m: Math.round(26 * Theme.uiScale)
                readonly property real len: Math.round(34 * Theme.uiScale)
                // Anchors below do the positioning; x/y keep the
                // delegate inside the item before the first layout.
                width: len
                height: len

                // Anchor to each screen corner.
                anchors.left: corner.isLeft ? parent.left : undefined
                anchors.right: corner.isLeft ? undefined : parent.right
                anchors.top: corner.isTop ? parent.top : undefined
                anchors.bottom: corner.isTop ? undefined : parent.bottom
                anchors.margins: Math.round(26 * Theme.uiScale)

                Rectangle {
                    width: parent.width; height: Math.round(2 * Theme.uiScale)
                    color: scan.accentColor
                    opacity: 0.75
                    anchors.top: corner.isTop ? parent.top : undefined
                    anchors.bottom: corner.isTop ? undefined : parent.bottom
                }
                Rectangle {
                    width: Math.round(2 * Theme.uiScale); height: parent.height
                    color: scan.accentColor
                    opacity: 0.75
                    anchors.left: corner.isLeft ? parent.left : undefined
                    anchors.right: corner.isLeft ? undefined : parent.right
                }
            }
        }

        // Sweeping band
        Rectangle {
            id: sweep
            width: parent.width
            height: Math.round(140 * Theme.uiScale)
            y: -height
            opacity: 0.55
            gradient: Gradient {
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.55
                               color: Theme.withAlpha(effects.accent, 0.10) }
                GradientStop { position: 0.92
                               color: Theme.withAlpha(effects.accent, 0.28) }
                GradientStop { position: 1.0
                               color: Theme.withAlpha(effects.accent, 0.55) }
            }

            SequentialAnimation on y {
                running: effects.scanActive && !effects.reducedMotion
                loops: Animation.Infinite
                NumberAnimation {
                    from: -sweep.height
                    to: effects.height
                    duration: 1750
                    easing.type: Easing.InOutSine
                }
                PauseAnimation { duration: 380 }
            }
        }

        // Faint measurement grid
        Canvas {
            id: grid
            anchors.fill: parent
            opacity: 0.22
            visible: !effects.reducedMotion
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                ctx.strokeStyle = Qt.rgba(Theme.pri.r, Theme.pri.g, Theme.pri.b, 0.28)
                ctx.lineWidth = 1
                var step = Math.round(64 * Theme.uiScale)
                for (var x = 0; x < width; x += step) {
                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke()
                }
                for (var y = 0; y < height; y += step) {
                    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke()
                }
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
        }

        // "READING SCREEN" caption so the effect is never ambiguous.
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: Math.round(54 * Theme.uiScale)
            width: caption.width + 26
            height: caption.height + 12
            radius: height / 2
            color: Theme.withAlpha(Theme.hdr, 0.86)
            border.width: 1
            border.color: Theme.withAlpha(effects.accent, 0.4)

            Row {
                id: caption
                anchors.centerIn: parent
                spacing: 7
                Rectangle {
                    width: Math.round(6 * Theme.uiScale)
                    height: width
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: scan.accentColor
                    SequentialAnimation on opacity {
                        running: !effects.reducedMotion
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.2; duration: 620 }
                        NumberAnimation { to: 1.0; duration: 620 }
                    }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("READING SCREEN")
                    color: Theme.withAlpha(Theme.text, 0.92)
                    font.pixelSize: Theme.fontXS
                    font.bold: true
                    font.letterSpacing: 1.4
                    font.family: "Segoe UI Variable Display, Segoe UI, sans-serif"
                }
            }
        }
    }

    // ────────────────────────────────────────────────────────────────────────
    // Target reticle
    // ────────────────────────────────────────────────────────────────────────
    Item {
        id: reticleRoot
        visible: effects.reticle && effects.reticle.visible === true
        opacity: visible ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: Theme.durShort } }

        readonly property real rx: effects.reticle ? Number(effects.reticle.x || 0) : 0
        readonly property real ry: effects.reticle ? Number(effects.reticle.y || 0) : 0

        x: rx - width / 2
        y: ry - height / 2
        width: Math.round(74 * Theme.uiScale)
        height: width

        Behavior on x { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }

        // outer ring
        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: "transparent"
            border.width: Math.round(2 * Theme.uiScale)
            border.color: Theme.withAlpha(Theme.acc2, 0.85)
        }
        // cross hairs
        Rectangle {
            width: Math.round(2 * Theme.uiScale); height: parent.height * 0.34
            anchors.horizontalCenter: parent.horizontalCenter
            y: 0
            color: Theme.withAlpha(Theme.acc2, 0.7)
        }
        Rectangle {
            width: Math.round(2 * Theme.uiScale); height: parent.height * 0.34
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            color: Theme.withAlpha(Theme.acc2, 0.7)
        }
        Rectangle {
            height: Math.round(2 * Theme.uiScale); width: parent.width * 0.34
            anchors.verticalCenter: parent.verticalCenter
            x: 0
            color: Theme.withAlpha(Theme.acc2, 0.7)
        }
        Rectangle {
            height: Math.round(2 * Theme.uiScale); width: parent.width * 0.34
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            color: Theme.withAlpha(Theme.acc2, 0.7)
        }
        // centre dot
        Rectangle {
            anchors.centerIn: parent
            width: Math.round(6 * Theme.uiScale)
            height: width
            radius: width / 2
            color: Theme.acc2
        }
        // pulsing halo
        Rectangle {
            anchors.centerIn: parent
            width: parent.width
            height: width
            radius: width / 2
            color: "transparent"
            border.width: 1
            border.color: Theme.withAlpha(Theme.acc2, 0.5)
            SequentialAnimation on scale {
                running: reticleRoot.visible && !effects.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { from: 1.0; to: 1.55; duration: 900
                                  easing.type: Easing.OutQuad }
                NumberAnimation { from: 1.55; to: 1.0; duration: 0 }
            }
            SequentialAnimation on opacity {
                running: reticleRoot.visible && !effects.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { from: 0.7; to: 0.0; duration: 900 }
                NumberAnimation { from: 0.0; to: 0.7; duration: 0 }
            }
        }
    }
}
