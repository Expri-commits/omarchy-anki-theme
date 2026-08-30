import QtQuick
import Quickshell
import Quickshell.Io

// The delivery half (wayfinder tickets 11/12/13, built by 20). Deliberately
// thin: run one decision pass (service/gate.py) and act on its single JSON
// line — log it, mount Sync as a subprocess, or send one notification whose
// click action is the decision's own exec argv. Every decision lives in the
// python helpers beside the payload, so a plugin update changes behavior at
// the next shell restart even when this component is re-instantiated from a
// stale compiled cache (basecamp/omarchy#6981); and before consent is
// granted nothing here writes anywhere at all.
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

    function act(decision) {
        console.log(`[ankiya] gate: ${decision.action} — ${decision.message}`);
        // inert and idle fall through: the log line above is the whole action
        if (decision.action === "sync") {
            syncProc.command = decision.exec;
            syncProc.running = true;
        } else if (decision.action === "ask_consent" || decision.action === "offer_reinstall") {
            // --exec must come last and carries the click action as argv
            // (Omarchy 4.0.1 contract — the gate already version-floored us).
            toastProc.command = ["omarchy", "notification", "send", "--app-name", "Ankiya", decision.toast.headline, decision.toast.body, "--exec"].concat(decision.exec);
            toastProc.running = true;
        }
    }

    Component.onCompleted: {
        console.log("[ankiya] service mounted");
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
                console.warn(`[ankiya] gate: exited ${exitCode} — doing nothing`);

        }

        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    root.act(JSON.parse(this.text));
                } catch (error) {
                    console.warn(`[ankiya] gate: no decision parsed (${error})`);
                }
            }
        }

        stderr: StdioCollector {
            onStreamFinished: root.relay(this.text, "[ankiya] gate stderr: ", true)
        }

    }

    Process {
        id: syncProc

        onExited: (exitCode) => {
            if (exitCode !== 0)
                console.warn(`[ankiya] service sync: exited ${exitCode} — next start retries`);

        }

        stdout: StdioCollector {
            onStreamFinished: root.relay(this.text, "[ankiya] service sync: ", false)
        }

        stderr: StdioCollector {
            onStreamFinished: root.relay(this.text, "[ankiya] service sync log: ", false)
        }

    }

    Process {
        id: toastProc

        onExited: (exitCode) => {
            if (exitCode !== 0)
                console.warn(`[ankiya] notification: exited ${exitCode} — not asked again this start`);

        }

        stdout: StdioCollector {
            onStreamFinished: root.relay(this.text, "[ankiya] notification: ", false)
        }

        stderr: StdioCollector {
            onStreamFinished: root.relay(this.text, "[ankiya] notification stderr: ", true)
        }

    }

}
