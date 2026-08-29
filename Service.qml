import QtQuick
import Quickshell
import Quickshell.Io

// Dev-loop stub (wayfinder ticket 05). The real palette pipeline replaces
// this; today it exists to prove the plugin mounts and hot-reloads.
Item {
    id: root

    // Bumped by hand when editing the installed copy: watching this file
    // change without a shell restart is the hot-reload proof.
    readonly property string revision: "r1"
    readonly property string proofPath: Quickshell.env("HOME") + "/.local/state/omarchy/anki-theme/dev-loop.json"

    Component.onCompleted: {
        proofFile.setText(JSON.stringify({
            "revision": root.revision,
            "mountedAt": new Date().toISOString()
        }, null, 2) + "\n");
        console.log("[anki-theme] service mounted, revision " + root.revision);
    }

    FileView {
        id: proofFile

        path: root.proofPath
        printErrors: true
    }

}
