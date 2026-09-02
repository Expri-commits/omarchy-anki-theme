import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// The delivery half (wayfinder tickets 11/12/13, built by 20; the ask
// became a dialog in 29). Deliberately thin: run one decision pass
// (service/gate.py) and act on its single JSON line — log it, mount Sync as
// a subprocess, or show one modal dialog whose Allow button runs the
// decision's own exec argv. Every decision lives in the python helpers
// beside the payload, so a plugin update changes behavior at the next
// shell restart even when this component is re-instantiated from a stale
// compiled cache (basecamp/omarchy#6981); and before consent is granted
// nothing here writes anywhere at all.
Item {
    id: root

    readonly property string home: Quickshell.env("HOME")
    readonly property string selfDir: root.localPath(Qt.resolvedUrl(".")).replace(/\/+$/, "")
    readonly property string anki2Root: root.home + "/.local/share/Anki2"
    readonly property string stateDir: root.home + "/.local/state/omarchy/anki-theme"

    function localPath(url) {
        const s = url.toString();
        return decodeURIComponent(s.startsWith("file://") ? s.slice(7) : s);
    }

    function relay(text, tag, warn) {
        if (text.trim().length > 0) {
            if (warn)
                console.warn(tag + text.trim());
            else
                console.log(tag + text.trim());
        }
    }

    // The decision's exec is the only argv this file ever runs beyond the
    // fixed gate command, so it is validated before use (H9): an array of
    // strings or nothing runs. A decision failing the shape check lands in
    // the same silence as an unparsable gate line — the warn is the whole
    // action, the next start retries.
    function isExecArgv(argv) {
        return Array.isArray(argv) && argv.every((arg) => {
            return typeof arg === "string";
        });
    }

    function act(decision) {
        console.log(`[anki_theme] gate: ${decision.action} — ${decision.message}`);
        // inert and idle fall through: the log line above is the whole action
        const acting = decision.action === "sync" || decision.action === "ask_consent" || decision.action === "offer_reinstall";
        if (acting && !isExecArgv(decision.exec)) {
            console.warn(`[anki_theme] gate: '${decision.action}' exec is not an argv array of strings — doing nothing`);
        } else if (decision.action === "sync") {
            execProc.tag = "service sync";
            execProc.command = decision.exec;
            execProc.running = true;
        } else if (decision.action === "ask_consent" || decision.action === "offer_reinstall") {
            askDialog.present(decision.toast, decision.exec);
        }
    }

    Component.onCompleted: {
        console.log("[anki_theme] service mounted");
        gateProc.running = true;
    }

    Process {
        id: gateProc

        // A spawn failure of its own (a half-present tree mid-update) needs
        // no handler: stdout closes empty, the parse warning below fires,
        // and Quickshell's own error log carries the cause — the service
        // stays safely silent and the next start retries.
        command: ["/usr/bin/python", "-B", root.selfDir + "/service/gate.py", root.anki2Root, root.stateDir]
        onExited: (exitCode) => {
            if (exitCode !== 0)
                console.warn(`[anki_theme] gate: exited ${exitCode} — doing nothing`);

        }

        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    root.act(JSON.parse(this.text));
                } catch (error) {
                    console.warn(`[anki_theme] gate: no decision parsed (${error})`);
                }
            }
        }

        stderr: StdioCollector {
            onStreamFinished: root.relay(this.text, "[anki_theme] gate stderr: ", true)
        }

    }

    // Both actors run a decision's exec argv through this one Process
    // (audit ticket 31): the gate's sync mount and the dialog's Allow are
    // mutually exclusive by decision branch, so one block serves both.
    // The launch sets `tag`, and the journal prefixes and exit warnings
    // below key off it — the two legs stay exactly as distinguishable in
    // the journal as the two dedicated Processes were. The Allow argv is
    // the decision's own (grant helper for consent, Sync for reinstall);
    // a crash mid-run is safe downstream — consent lands atomically
    // inside grant.py before any install, and an unfinished converge is
    // exactly what the next service start's gate decision completes.
    Process {
        id: execProc

        property string tag: "service sync"
        // What a nonzero exit means, per leg.
        readonly property var exitNotes: ({
            "service sync": "next start retries",
            "allow": "the ask repeats at the next service start"
        })

        onExited: (exitCode) => {
            if (exitCode !== 0)
                console.warn(`[anki_theme] ${execProc.tag}: exited ${exitCode} — ${execProc.exitNotes[execProc.tag]}`);

        }

        stdout: StdioCollector {
            onStreamFinished: root.relay(this.text, `[anki_theme] ${execProc.tag}: `, false)
        }

        stderr: StdioCollector {
            onStreamFinished: root.relay(this.text, `[anki_theme] ${execProc.tag} log: `, false)
        }

    }

    // One modal for both asks (ticket 29). Deliberately dumb: headline,
    // body, and the Allow action all come from the gate's decision, and the
    // card styles itself — no shell-internal imports, so an Omarchy restyle
    // cannot break it. "Not now" — and any click outside the card, which
    // lands on the scrim — is the old ignore: nothing recorded, the ask
    // repeats at the next service start. Keyboard focus stays None, so
    // typing keeps going to the focused app while the dialog waits.
    PanelWindow {
        id: askDialog

        property string headline: ""
        property string body: ""
        property var allowArgv: []

        function present(toast, exec) {
            headline = toast.headline;
            body = toast.body;
            allowArgv = exec;
            visible = true;
        }

        function dismiss() {
            visible = false;
        }

        function allow() {
            execProc.tag = "allow";
            execProc.command = allowArgv;
            execProc.running = true;
            visible = false;
        }

        visible: false
        // screen left at the default: Quickshell.primaryScreen resolves to
        // undefined on Omarchy's pinned Quickshell (live-verified 2026-09-01),
        // and the default mapping is what showed the dialog on screen.
        WlrLayershell.namespace: "omarchy-anki-theme"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore
        color: "transparent"

        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }

        Rectangle {
            id: scrim

            anchors.fill: parent
            color: Qt.rgba(0, 0, 0, 0.55)

            MouseArea {
                anchors.fill: parent
                onClicked: askDialog.dismiss()
            }

            Rectangle {
                id: card

                readonly property int pad: 24

                anchors.centerIn: parent
                width: Math.min(parent.width - 64, 460)
                height: cardColumn.implicitHeight + card.pad * 2
                radius: 12
                color: "#2e3440"
                border.color: "#3b4252"
                border.width: 1

                // Clicks inside the card are not "Not now".
                MouseArea {
                    anchors.fill: parent
                }

                ColumnLayout {
                    id: cardColumn

                    anchors.fill: parent
                    anchors.margins: card.pad
                    spacing: 14

                    Text {
                        Layout.fillWidth: true
                        text: askDialog.headline
                        textFormat: Text.PlainText
                        color: "#eceff4"
                        font.pixelSize: 16
                        font.bold: true
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        Layout.fillWidth: true
                        text: askDialog.body
                        textFormat: Text.PlainText
                        color: "#d8dee9"
                        font.pixelSize: 13
                        lineHeight: 1.25
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        spacing: 10

                        Rectangle {
                            width: notNowLabel.implicitWidth + 28
                            height: 34
                            radius: 8
                            color: notNowMouse.containsMouse ? "#3b4252" : "transparent"
                            border.color: "#4c566a"
                            border.width: 1

                            Text {
                                id: notNowLabel

                                anchors.centerIn: parent
                                text: "Not now"
                                color: "#d8dee9"
                                font.pixelSize: 13
                            }

                            MouseArea {
                                id: notNowMouse

                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: askDialog.dismiss()
                            }

                        }

                        Rectangle {
                            width: allowLabel.implicitWidth + 28
                            height: 34
                            radius: 8
                            color: allowMouse.containsMouse ? "#5579c0" : "#476cb4"

                            Text {
                                id: allowLabel

                                anchors.centerIn: parent
                                text: "Allow"
                                color: "#ffffff"
                                font.pixelSize: 13
                                font.bold: true
                            }

                            MouseArea {
                                id: allowMouse

                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: askDialog.allow()
                            }

                        }

                    }

                }

            }

        }

    }

}
