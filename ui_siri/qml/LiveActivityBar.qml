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

    // `countActive()` walks the model, so it is only ever called from
    // `refresh()`; every binding reads `agentCount`, the reactive mirror.
    // (Calling the function inside a binding looks fine but never updates:
    // a JS function call creates no dependency on the model's rows.)
    readonly property int activeCount: agentCount
    readonly property bool hasWork: agentCount > 0

    implicitWidth: Math.round(240 * Theme.uiScale)
    implicitHeight: expanded ? body.height + 16 : Math.round(30 * Theme.uiScale)
    // `agentCount` mirrors `activeCount` but re-evaluates on row churn, so the
    // bindings below stay *bindings*. An imperative `bar.visible = ...` in a
    // signal handler would silently break them after the first insert.
    property int agentCount: 0
    visible: agentCount > 0 || expanded
    // Zero height and zero hit area while asleep, so the column above the orb
    // carries no invisible band and no click-stealing region.
    height: visible ? implicitHeight : 0
    opacity: agentCount > 0 ? 1.0 : 0.0
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
    //
    // Each handler MUST declare the signal's parameters. Qt matches handlers by
    // name *and* arity: a zero-argument `onRowsInserted` matches no signal on
    // QAbstractListModel and prints
    //   'Detected function "onRowsInserted" ... no signal of the target matches'
    // on every startup. (A handler on a signal that does not exist also never
    // fires, so the bar simply never updated -- the warning was the only clue.)
    Connections {
        target: bar.model

        function onRowsInserted(parent, first, last) { bar.refresh() }
        function onRowsRemoved(parent, first, last) { bar.refresh() }
        function onDataChanged(topLeft, bottomRight, roles) { bar.refresh() }
        function onModelReset() { bar.refresh() }
    }

    // Re-reads the count into `agentCount`; the visual properties bind to that
    // rather than being assigned here.
    function refresh() { bar.agentCount = bar.countActive() }

    // The model is assigned by the stage *before* any rows exist, so the first
    // count has to happen on assignment as well as on row churn.
    onModelChanged: refresh()
    Component.onCompleted: refresh()

    // ── shell ───────────────────────────────────────────────────────────────
    Rectangle {
        id: shell
        anchors.fill: parent
        radius: height / 2
        color: Theme.glass > 0 ? Theme.withAlpha(Theme.panel, Theme.surfaceAlpha)
                               : Theme.panel
        border.width: 1
        border.color: bar.expanded ? Theme.glassBorderHover : Theme.glassBorder
        Behavior on border.color { ColorAnimation { duration: Theme.durShort } }

        // Inner rim: the second hairline that makes the capsule read as glass.
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            color: "transparent"
            border.width: 1
            border.color: Theme.glassInnerRim
        }

        // Specular top edge
        Rectangle {
            width: parent.width - 2
            height: parent.height / 2
            x: 1; y: 1
            radius: parent.radius
            opacity: 0.14 * Theme.glass
            gradient: Gradient {
                GradientStop {
                    position: 0.0
                    color: Theme.withAlpha(Theme.glassSpecular, 1.0)
                }
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
                border.color: Theme.withAlpha(Theme.dim, 0.55)
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
            // While asleep the collapsed row is not painted at all, so the
            // placeholder is never seen; it only keeps the binding typed.
            text: bar.agentCount > 0 ? bar.summary() : qsTr("Idle")
            color: Theme.withAlpha(Theme.text, 0.9)
            font.pixelSize: Theme.fontXS
            font.family: "Segoe UI Variable Text, Segoe UI, sans-serif"
            elide: Text.ElideRight
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: bar.agentCount > 1
            text: "+" + (bar.agentCount - 1)
            color: Theme.pri
            font.pixelSize: Theme.fontXS
            font.bold: true
        }
    }

    function summary() {
        if (!model || model.rowCount() === 0)
            return ""
        var idx = model.index(0, 0)
        // Qt.UserRole + 1 + index-of("title") in ActivityModel.ROLES.
        return String(model.data(idx, Qt.UserRole + 1 + roleIndex("title")) || "")
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
            // `&&` yields the *operand*, not a bool, so when `model` is
            // null/undefined this evaluated to undefined and Qt printed
            // "Unable to assign [undefined] to bool". `!!` forces a bool.
            visible: !!bar.model && bar.model.rowCount() > 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: Math.round(2 * Theme.uiScale)
            radius: height / 2
            color: Theme.withAlpha(Theme.dim, 0.42)
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
                color: Theme.pri
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
