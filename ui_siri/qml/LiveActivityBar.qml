pragma ComponentBehavior: Bound
import QtQuick

/*
    Layer 3b — collapsible Dynamic-Island style capsule for background work.

    Collapsed it is a 22px pill showing an icon, the job name and a hairline
    progress bar. Hover expands it to reveal every active job plus a cancel
    affordance. It deliberately has **no** close button per row: background
    jobs are owned by the backend, so the capsule only *reports* and offers
    "stop" for the ones the backend supports.

    Nothing here polls: the model is driven by ActivityModel on the GUI thread.
*/
Item {
    id: bar

    required property var model        // ActivityModel
    required property bool reducedMotion

    property bool expanded: false
    property int maxRows: 4

    signal cancel(string aid)

    readonly property int activeCount: countActive()
    readonly property bool hasWork: activeCount > 0

    implicitWidth: Math.round(240 * Theme.uiScale)
    implicitHeight: expanded ? body.height + 16 : Math.round(30 * Theme.uiScale)
    visible: hasWork || expanded
    opacity: hasWork ? 1.0 : 0.0
    Behavior on opacity { NumberAnimation { duration: Theme.durShort } }
    Behavior on implicitHeight {
        NumberAnimation {
            duration: Theme.durMed
            easing.type: Easing.OutCubic
        }
    }

    function countActive() {
        if (!model)
            return 0
        var total = 0
        for (var i = 0; i < model.rowCount(); ++i) {
            var idx = model.index(i, 0)
            if (model.data(idx, Qt.UserRole + 6))  // "active"
                total++
        }
        return total
    }

    // Keeps `hasWork` fresh when rows change.
    Connections {
        target: bar.model
        function onRowsInserted() { bar.refresh() }
        function onRowsRemoved() { bar.refresh() }
        function onDataChanged() { bar.refresh() }
        function onModelReset() { bar.refresh() }
    }
    function refresh() { bar.visible = bar.hasWork || bar.expanded }

    // ── shell ───────────────────────────────────────────────────────────────
    Rectangle {
        id: shell
        anchors.fill: parent
        radius: height / 2
        color: Theme.glass > 0 ? Theme.withAlpha(Theme.hdr, 0.82) : Theme.hdr
        border.width: 1
        border.color: Theme.withAlpha(Theme.pri, bar.expanded ? 0.5 : 0.28)
        Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

        // Specular top edge
        Rectangle {
            width: parent.width - 2
            height: parent.height / 2
            x: 1; y: 1
            radius: parent.radius
            opacity: 0.10 * Theme.glass
            gradient: Gradient {
                GradientStop { position: 0.0
                               color: Theme.withAlpha(Theme.textStrong, 0.6) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // ── collapsed single row ────────────────────────────────────────────────
    Row {
        id: collapsed
        visible: !bar.expanded
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Math.round(12 * Theme.uiScale)
        anchors.rightMargin: Math.round(10 * Theme.uiScale)
        anchors.verticalCenter: parent.verticalCenter
        spacing: 7

        Item {
            width: Math.round(14 * Theme.uiScale)
            height: width
            anchors.verticalCenter: parent.verticalCenter

            // spinner
            Rectangle {
                anchors.centerIn: parent
                width: Math.round(12 * Theme.uiScale)
                height: width
                radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: Theme.withAlpha(Theme.dim, 0.9)
            }
            Rectangle {
                anchors.centerIn: parent
                width: Math.round(12 * Theme.uiScale)
                height: width
                radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: "transparent"
                Rectangle {
                    width: 3; height: 3
                    radius: 1.5
                    color: Theme.pri
                    x: parent.width / 2 - 1
                    y: 0
                }
                RotationAnimator on rotation {
                    running: !bar.reducedMotion
                    loops: Animation.Infinite
                    from: 0; to: 360
                    duration: 1200
                }
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - Math.round(60 * Theme.uiScale)
            text: bar.activeCount > 0
                  ? bar.summary()
                  : qsTr("Idle")
            color: Theme.withAlpha(Theme.text, 0.9)
            font.pixelSize: Theme.fontXS
            font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
            elide: Text.ElideRight
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: bar.activeCount > 1
            text: "+" + (bar.activeCount - 1)
            color: Theme.pri
            font.pixelSize: Theme.fontXS
            font.bold: true
        }
    }

    function summary() {
        if (!model || model.rowCount() === 0)
            return ""
        var idx = model.index(0, 0)
        return String(model.data(idx, Qt.UserRole + 2) || "")   // "title"
    }

    // ── expanded rows ───────────────────────────────────────────────────────
    Item {
        id: body
        visible: bar.expanded
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        anchors.topMargin: 8
        height: rows.height + Math.round(18 * Theme.uiScale)

        Column {
            id: rows
            width: parent.width
            spacing: 5
            Repeater {
                model: bar.model ? Math.min(bar.maxRows, bar.model.rowCount()) : 0
                delegate: Row {
                    id: rowItem
                    required property int index
                    width: rows.width
                    spacing: 6

                    function role(name, fallback) {
                        var id = bar.model.index(index, 0)
                        var v = bar.model.data(id, Qt.UserRole + 1 + bar.roleIndex(name))
                        return v === undefined || v === null ? fallback : v
                    }

                    Rectangle {
                        width: Math.round(4 * Theme.uiScale)
                        height: Math.round(12 * Theme.uiScale)
                        radius: width / 2
                        anchors.verticalCenter: parent.verticalCenter
                        color: parent.role("active", false) ? Theme.acc : Theme.dim
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.round(74 * Theme.uiScale)
                        text: parent.role("title", "")
                        color: Theme.withAlpha(Theme.text, 0.92)
                        font.pixelSize: Theme.fontXS
                        font.bold: true
                        elide: Text.ElideRight
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: rows.width - Math.round(140 * Theme.uiScale)
                        text: parent.role("detail", "")
                        color: Theme.withAlpha(Theme.mid, 0.95)
                        font.pixelSize: Theme.fontXS
                        elide: Text.ElideRight
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.round(14 * Theme.uiScale)
                        height: width
                        radius: width / 2
                        color: cancelArea.containsMouse
                               ? Theme.withAlpha(Theme.red, 0.3) : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "✕"
                            color: Theme.withAlpha(Theme.text, 0.65)
                            font.pixelSize: Theme.fontXS - 1
                        }
                        MouseArea {
                            id: cancelArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                var id = bar.model.index(rowItem.index, 0)
                                bar.cancel(String(bar.model.data(id, Qt.UserRole + 1)))
                            }
                        }
                    }
                }
            }
        }

        // hairline progress for the first active row
        Rectangle {
            visible: bar.model && bar.model.rowCount() > 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: Math.round(2 * Theme.uiScale)
            radius: height / 2
            color: Theme.withAlpha(Theme.dim, 0.5)
            Rectangle {
                readonly property real frac: {
                    if (!bar.model || bar.model.rowCount() === 0) return 0
                    var id = bar.model.index(0, 0)
                    var v = bar.model.data(id, Qt.UserRole + 5)  // "progress"
                    return v === undefined || v < 0 ? 0.0 : v
                }
                width: parent.width * frac
                height: parent.height
                radius: height / 2
                color: Theme.acc
                visible: frac > 0
                Behavior on width { NumberAnimation { duration: Theme.durMed } }
            }
        }
    }

    // Role-name → role-offset helper (kept explicit so a role reorder in
    // ActivityModel shows up as a test failure rather than a silent bug).
    function roleIndex(name) {
        var roles = ["aid", "title", "kind", "detail", "progress", "active", "ts"]
        return roles.indexOf(name)
    }

    // ── hover: expand on enter, collapse a beat after leave ─────────────────
    // Declared *after* the content so row hit-areas win the click when the
    // capsule is expanded; `propagateComposedEvents` keeps the toggle working.
    MouseArea {
        id: hoverArea
        z: -1
        anchors.fill: parent
        hoverEnabled: true
        onClicked: bar.expanded = !bar.expanded
        onContainsMouseChanged: {
            if (containsMouse) {
                bar.expanded = true
                collapseTimer.stop()
            } else if (hoverArea.containsMouse === false) {
                collapseTimer.restart()
            }
        }
    }

    Timer {
        id: collapseTimer
        interval: 1400
        repeat: false
        onTriggered: if (!hoverArea.containsMouse) bar.expanded = false
    }
}
