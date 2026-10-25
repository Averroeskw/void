import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: summon
    property bool shown: false
    property bool calm: false
    property string agentName: "Archie"
    property string detail: ""
    property string state: "offline"
    property color tint: "#c9ab78"
    property double lastUse: Date.now()
    readonly property bool taskActive: ["working", "thinking", "waiting", "upload", "restart"].indexOf(state) >= 0
    Timer {
        interval: 1000; running: summon.shown; repeat: true
        onTriggered: {
            if (cardHover.hovered || summon.taskActive) summon.lastUse = Date.now()
            else if (Date.now() - summon.lastUse >= 30000) summon.dismiss()
        }
    }
    signal openRequested()
    signal dismissed()
    function reveal() {
        exitMotion.stop()
        lastUse = Date.now()
        shown = true
        card.y = -330
        entrance.restart()
    }
    function dismiss() { entrance.stop(); exitMotion.restart() }
    visible: shown
    anchors.top: true
    implicitWidth: 440
    implicitHeight: 460
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "archie-summoned-agent"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region { item: card }

    NumberAnimation { id: entrance; target: card; property: "y"; to: 94; duration: summon.calm ? 1 : 740; easing.type: Easing.OutBounce }
    NumberAnimation { id: exitMotion; target: card; property: "y"; to: -330; duration: summon.calm ? 1 : 300; easing.type: Easing.InCubic; onFinished: { summon.shown = false; summon.dismissed() } }
    Item {
        id: card
        HoverHandler { id: cardHover; onHoveredChanged: summon.lastUse = Date.now() }
        x: 25; y: -330; width: 390; height: 310
        AgentFace {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 0; size: 104
            tint: summon.tint
            faceStyle: "orb"
            interactive: false
            live: summon.shown && !summon.calm
            mood: summon.state === "working" || summon.state === "thinking" ? "thinking" : summon.state === "error" ? "sad" : "idle"
        }
        Rectangle {
            y: 112; width: parent.width; height: 185; radius: 24
            color: "#f0181c1b"; border.color: Qt.alpha(summon.tint, 0.55); border.width: 1
            Column {
                x: 22; y: 18; width: parent.width - 44; spacing: 9
                Text { text: summon.agentName; color: "#f2eee5"; font.pixelSize: 23; font.bold: true }
                Text { text: summon.state.toUpperCase(); color: summon.tint; font.pixelSize: 11; font.letterSpacing: 2 }
                Text { width: parent.width; text: summon.detail || "Status unavailable"; color: "#ccc9c1"; font.pixelSize: 13; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; height: 34 }
                Row {
                    spacing: 10
                    Rectangle {
                        width: 218; height: 36; radius: 12; color: summon.tint
                        Text { anchors.centerIn: parent; text: "Open agent controls"; color: "#181c1b"; font.pixelSize: 13; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { summon.dismiss(); summon.openRequested() } }
                    }
                    Rectangle {
                        width: 116; height: 36; radius: 12; color: "#303632"
                        Text { anchors.centerIn: parent; text: "↑ Agents"; color: "#ede8df"; font.pixelSize: 13 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: summon.dismiss() }
                    }
                }
            }
        }
    }
}
