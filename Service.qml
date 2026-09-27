import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

// Owns the polling loop. Runs `bin/unifi status` and keeps the last good
// snapshot, so a console that blips offline greys the widget out instead of
// blanking it. The API key never passes through here: the helper reads it
// from the keyring itself.
Item {
  id: root

  property var settings: ({})

  property var snapshot: null
  property bool stale: false
  property bool refreshing: false
  property string lastError: ""
  property double lastSuccessMs: 0

  readonly property string health: Model.overallState(snapshot, stale)
  readonly property string profile: String(setting("profile", "default"))
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 30, 10, 3600)
  readonly property bool notifyOnChange: setting("notify", true) !== false
  readonly property string consoleUrl: snapshot && snapshot.console ? String(snapshot.console.url || "") : ""

  readonly property string helperPath: {
    var url = String(Qt.resolvedUrl("bin/unifi"))
    return decodeURIComponent(url.replace(/^file:\/\//, ""))
  }

  property string _stdout: ""
  property string _stderr: ""
  property string _notifiedState: ""

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var n = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(n)) n = fallback
    return Math.max(min, Math.min(max, n))
  }

  function refresh() {
    if (statusProcess.running) return
    _stdout = ""
    _stderr = ""
    refreshing = true
    var skip = Model.skipArgs(setting("deviceStats", true) !== false, setting("legacyApi", true) !== false)
    statusProcess.command = [helperPath, "--profile", profile, "status"].concat(skip)
    statusProcess.running = true
    watchdog.restart()
  }

  function accept(parsed) {
    var next = parsed.snapshot
    // A failed run keeps the previous good data and marks it stale; setup
    // problems replace it, since the old data may be for another console.
    if (next.status === "down" && snapshot && snapshot.status !== "unconfigured" && snapshot.status !== "down") {
      stale = true
      var kept = snapshot
      kept.error = next.error
      kept.status = "down"
      snapshot = null
      snapshot = kept
    } else {
      stale = false
      snapshot = next
      if (next.status !== "down" && next.status !== "unconfigured") lastSuccessMs = Date.now()
    }
    lastError = ""
    maybeNotify()
  }

  // Tell the user once when things go from fine to broken, and once when
  // they recover. Warnings flapping inside the same bad state stay quiet.
  function maybeNotify() {
    var bad = Model.stateNeedsAttention(health) || health === "stale"
    var previous = _notifiedState
    _notifiedState = bad ? "bad" : (health === "ok" ? "ok" : previous)
    if (!notifyOnChange || previous === "" || previous === _notifiedState) return
    if (_notifiedState === "bad")
      notify(Model.heroTitle(snapshot), Model.heroMeta(snapshot, health), "normal")
    else if (_notifiedState === "ok")
      notify(Model.heroTitle(snapshot), "Network is back to normal", "low")
  }

  function notify(title, body, urgency) {
    Quickshell.execDetached(["omarchy-notification-send", "--app-name", "UniFi",
      "-g", Model.ICONS.network, "-u", urgency, title, body])
  }

  function openConsole() {
    var url = consoleUrl || String(setting("consoleUrl", ""))
    if (url === "") return
    Quickshell.execDetached(["omarchy-launch-webapp", url])
  }

  function runSetup() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation",
      Util.shellQuote(helperPath) + " --profile " + Util.shellQuote(profile) + " setup"])
  }

  function copy(text) {
    var value = String(text || "")
    if (value === "") return
    Quickshell.execDetached(["bash", "-c", "printf %s " + Util.shellQuote(value) + " | wl-copy"])
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // A console that accepts the TCP connection and then stalls would otherwise
  // hold the process forever and silently stop every later refresh.
  Timer {
    id: watchdog
    interval: 25000
    onTriggered: if (statusProcess.running) statusProcess.running = false
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector { id: statusStdout; waitForEnd: true; onStreamFinished: root._stdout = text }
    stderr: StdioCollector { id: statusStderr; waitForEnd: true; onStreamFinished: root._stderr = text }
    onExited: function(exitCode) {
      root.refreshing = false
      watchdog.stop()
      var parsed = Model.parseSnapshot(String(statusStdout.text || root._stdout || ""))
      if (parsed.ok) {
        root.accept(parsed)
        return
      }
      var stderr = String(statusStderr.text || root._stderr || "").trim()
      root.lastError = stderr !== "" ? stderr.split("\n").pop() : parsed.error
      if (root.snapshot) root.stale = true
      console.warn("unifi:", root.lastError)
    }
  }
}
