// Pure display logic for the UniFi widget. No QML, no I/O: every function
// takes plain values and returns plain values, so tests/model.test.js can run
// it under node against the same snapshots the helper produces.

var SNAPSHOT_SCHEMA = 1

var ICONS = {
  network: String.fromCodePoint(0xF048D),     // md-server_network
  offline: String.fromCodePoint(0xF0164),     // md-cloud_off_outline
  alert: String.fromCodePoint(0xF0026),       // md-alert
  key: String.fromCodePoint(0xF0306),         // md-key
  wifi: String.fromCodePoint(0xF05A9),
  wifiOff: String.fromCodePoint(0xF05AA),
  ethernet: String.fromCodePoint(0xF0200),
  internet: String.fromCodePoint(0xF059F),    // md-web
  gateway: String.fromCodePoint(0xF1087),     // md-router_network
  console: String.fromCodePoint(0xF048D),
  switch: String.fromCodePoint(0xF04E4),
  ap: String.fromCodePoint(0xF0002),          // md-access_point_network
  apOff: String.fromCodePoint(0xF0BE1),
  other: String.fromCodePoint(0xF0FB0),       // md-devices
  vpn: String.fromCodePoint(0xF088F),         // md-shield_account
  update: String.fromCodePoint(0xF005F),      // md-arrow_up_bold_circle
  copy: String.fromCodePoint(0xF018F),
  open: String.fromCodePoint(0xF03CC),
  refresh: String.fromCodePoint(0xF0450),
  speed: String.fromCodePoint(0xF04C5),       // md-speedometer
  down: String.fromCodePoint(0xF0045),
  up: String.fromCodePoint(0xF005D)
}

var WIFI_STRENGTH = [0xF092F, 0xF091F, 0xF0922, 0xF0925, 0xF0928].map(function(c) { return String.fromCodePoint(c) })

// ---- Snapshot intake -------------------------------------------------------

// Parse the helper's stdout. Returns { ok, snapshot, error } and never throws,
// so a garbled run leaves the last good snapshot on screen.
function parseSnapshot(raw) {
  var text = String(raw || "").trim()
  if (text === "") return { ok: false, snapshot: null, error: "empty output from unifi helper" }
  var data
  try {
    data = JSON.parse(text)
  } catch (e) {
    return { ok: false, snapshot: null, error: "unifi helper returned invalid JSON" }
  }
  if (!data || typeof data !== "object") return { ok: false, snapshot: null, error: "unexpected helper output" }
  if (data.schema !== SNAPSHOT_SCHEMA)
    return { ok: false, snapshot: null, error: "unsupported snapshot schema " + data.schema + "; update the plugin" }
  data.devices = data.devices || []
  data.clients = data.clients || []
  data.issues = data.issues || []
  data.warnings = data.warnings || []
  return { ok: true, snapshot: data, error: "" }
}

// ---- Overall state ---------------------------------------------------------

// One word the bar and hero reason about. "stale" means the last run failed
// but an older good snapshot is still being shown.
function overallState(snapshot, stale) {
  if (!snapshot) return "loading"
  if (snapshot.status === "unconfigured") return "unconfigured"
  if (snapshot.status === "down") return stale ? "stale" : "down"
  return snapshot.status || "ok"
}

function barIcon(state) {
  if (state === "unconfigured") return ICONS.key
  if (state === "down" || state === "stale") return ICONS.offline
  return ICONS.network
}

function stateNeedsAttention(state) {
  return state === "error" || state === "warning" || state === "down"
}

function heroTitle(snapshot) {
  if (!snapshot) return "UniFi"
  if (snapshot.site && snapshot.site.name && snapshot.site.name !== "Default") return snapshot.site.name
  if (snapshot.wan && snapshot.wan.gatewayName) return snapshot.wan.gatewayName
  return "UniFi"
}

function heroMeta(snapshot, state) {
  if (state === "loading") return "Checking the network…"
  if (state === "unconfigured") return "Not set up yet"
  if (state === "down" || state === "stale") return errorMessage(snapshot && snapshot.error)
  var issues = (snapshot && snapshot.issues) || []
  var first = null
  for (var i = 0; i < issues.length; i++) {
    if (issues[i].severity === "error" || issues[i].severity === "warning") { first = issues[i]; break }
  }
  if (first) {
    var extra = countSeverity(issues, "error") + countSeverity(issues, "warning") - 1
    return first.message + (extra > 0 ? " (+" + extra + ")" : "")
  }
  return "All systems normal"
}

function countSeverity(issues, severity) {
  var n = 0
  for (var i = 0; i < (issues || []).length; i++) if (issues[i].severity === severity) n++
  return n
}

function errorMessage(error) {
  if (!error) return "Console unreachable"
  if (error.kind === "auth") return "API key rejected — run setup again"
  if (error.kind === "tls") return "Certificate changed — run setup again"
  if (error.kind === "unreachable") return "Console unreachable"
  return String(error.message || "Console error")
}

// Short text beside the bar icon, chosen by the `barLabel` setting.
function barLabel(snapshot, mode) {
  if (!snapshot || !snapshot.summary) return ""
  var s = snapshot.summary
  if (mode === "clients") return String(s.clientsTotal)
  if (mode === "devices") return s.devicesOnline + "/" + s.devicesTotal
  if (mode === "latency") return snapshot.wan && snapshot.wan.latencyMs !== null && snapshot.wan.latencyMs !== undefined
    ? Math.round(snapshot.wan.latencyMs) + "ms" : ""
  return ""
}

function tooltip(snapshot, state) {
  if (state === "unconfigured") return "UniFi: click to set up"
  var line = heroTitle(snapshot) + " — " + heroMeta(snapshot, state)
  if (snapshot && snapshot.summary) {
    var s = snapshot.summary
    line += "\n" + s.devicesOnline + "/" + s.devicesTotal + " devices · " + s.clientsTotal + " clients"
  }
  return line
}

// ---- Formatting ------------------------------------------------------------

function formatBps(bps) {
  if (bps === null || bps === undefined) return "—"
  var n = Number(bps)
  if (!isFinite(n) || n < 0) return "—"
  var units = ["bps", "Kbps", "Mbps", "Gbps"]
  var i = 0
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++ }
  return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + units[i]
}

function formatMbps(mbps) {
  var n = Number(mbps)
  if (!isFinite(n) || n <= 0) return "—"
  return formatBps(n * 1e6)
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return ""
  var s = Number(seconds)
  if (!isFinite(s) || s < 0) return ""
  var d = Math.floor(s / 86400)
  var h = Math.floor((s % 86400) / 3600)
  var m = Math.floor((s % 3600) / 60)
  if (d > 0) return d + "d " + h + "h"
  if (h > 0) return h + "h " + m + "m"
  return m + "m"
}

// "3m ago" style age of an ISO timestamp relative to nowMs.
function formatAge(iso, nowMs) {
  var t = Date.parse(String(iso || ""))
  if (isNaN(t)) return ""
  var seconds = Math.max(0, Math.round((nowMs - t) / 1000))
  if (seconds < 45) return "just now"
  return formatDuration(seconds) + " ago"
}

function formatPct(value) {
  var n = Number(value)
  return isFinite(n) && value !== null && value !== undefined ? Math.round(n) + "%" : ""
}

// ---- Rows ------------------------------------------------------------------

function deviceIcon(device) {
  if (!device) return ICONS.other
  if (device.kind === "ap") return device.state === "online" ? ICONS.ap : ICONS.apOff
  return ICONS[device.kind] || ICONS.other
}

function deviceSubtitle(device) {
  if (!device) return ""
  var parts = []
  if (device.state !== "online") parts.push(device.state.replace(/_/g, " "))
  if (device.model && device.model !== device.name) parts.push(device.model)
  if (device.ip) parts.push(device.ip)
  if (device.clients > 0) parts.push(device.clients + (device.clients === 1 ? " client" : " clients"))
  var up = formatDuration(device.uptimeSec)
  if (device.state === "online" && up) parts.push("up " + up)
  return parts.join(" · ")
}

function clientIcon(client) {
  if (!client) return ICONS.other
  if (client.kind === "wireless") return ICONS.wifi
  if (client.kind === "wired") return ICONS.ethernet
  if (client.kind === "vpn" || client.kind === "teleport") return ICONS.vpn
  return ICONS.other
}

function clientSubtitle(client) {
  if (!client) return ""
  var parts = []
  if (client.ip) parts.push(client.ip)
  if (client.ssid) parts.push(client.ssid)
  else if (client.uplink) parts.push(client.uplink)
  if (client.signalDbm !== null && client.signalDbm !== undefined) parts.push(client.signalDbm + " dBm")
  if (client.guest) parts.push("guest")
  return parts.join(" · ")
}

// Case-insensitive match on name, IP, MAC, SSID, or uplink.
function filterClients(clients, query) {
  var q = String(query || "").trim().toLowerCase()
  if (q === "") return clients || []
  return (clients || []).filter(function(c) {
    return [c.name, c.ip, c.mac, c.ssid, c.uplink].some(function(v) {
      return String(v || "").toLowerCase().indexOf(q) !== -1
    })
  })
}

// ---- This machine ----------------------------------------------------------

function wifiStrengthIcon(signalPct) {
  var n = Number(signalPct)
  if (!isFinite(n)) return ICONS.wifi
  return WIFI_STRENGTH[Math.max(0, Math.min(4, Math.round(n / 25)))]
}

function selfIcon(self) {
  if (!self) return ICONS.wifiOff
  return self.type === "wireless" ? wifiStrengthIcon(self.signal) : ICONS.ethernet
}

function selfTitle(self) {
  if (!self) return "Offline"
  if (self.type === "wireless") return self.ssid || "Wi-Fi"
  return "Ethernet"
}

function selfSubtitle(self) {
  if (!self) return "No default route"
  var parts = []
  if (self.uplink && self.uplink.name) parts.push("via " + self.uplink.name)
  if (self.band) parts.push(self.band + (self.channel ? " ch " + self.channel : ""))
  if (self.signalDbm !== null && self.signalDbm !== undefined) parts.push(self.signalDbm + " dBm")
  else if (self.signal !== null && self.signal !== undefined && self.type === "wireless") parts.push(self.signal + "%")
  return parts.join(" · ")
}

// ---- Settings --------------------------------------------------------------

var KNOWN_SECTIONS = ["wan", "self", "devices", "clients"]

// The `sections` setting orders and filters panel sections. Unknown names are
// dropped so a typo cannot blank the panel.
function sectionList(setting) {
  var list = Array.isArray(setting) ? setting : KNOWN_SECTIONS
  var out = []
  for (var i = 0; i < list.length; i++) {
    var name = String(list[i] || "")
    if (KNOWN_SECTIONS.indexOf(name) !== -1 && out.indexOf(name) === -1) out.push(name)
  }
  return out.length > 0 ? out : KNOWN_SECTIONS.slice()
}

// Map the `skip` setting to the helper's --skip argument.
function skipArgs(showDeviceStats, useLegacy) {
  var skip = []
  if (!showDeviceStats) skip.push("stats")
  if (!useLegacy) skip.push("legacy")
  return skip.length > 0 ? ["--skip", skip.join(",")] : []
}
