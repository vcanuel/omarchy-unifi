import QtQuick
import "../components"
import "../Model.js" as Model

// Internet uplink: public IP, ISP, latency, live throughput, last speed test.
// Comes from the controller's health endpoint, so it disappears cleanly when
// that endpoint is unavailable or skipped.
Section {
  id: root
  sectionId: "wan"
  heading: "INTERNET"

  readonly property var wan: snapshot ? snapshot.wan : null

  rows: {
    var w = wan
    if (!w) return []
    var down = w.status !== "ok" && w.status !== "unknown"
    var offline = down || (w.internet !== "" && w.internet !== "ok" && w.internet !== "unknown")
    var meta = []
    if (w.isp) meta.push(w.isp)
    if (w.latencyMs !== null && w.latencyMs !== undefined) meta.push(Math.round(w.latencyMs) + " ms")
    if (w.availabilityPct !== null && w.availabilityPct !== undefined) meta.push(Model.formatPct(w.availabilityPct) + " up")

    var list = [{
      icon: Model.ICONS.internet,
      title: offline ? "Internet is down" : (w.ip || "Connected"),
      subtitle: meta.join(" · "),
      trailing: offline ? w.status : Model.formatDuration(w.uptimeSec),
      attention: offline,
      copy: { ip: w.ip, name: w.isp },
      action: "copy:ip"
    }]

    if (w.rxBps !== null || w.txBps !== null) {
      list.push({
        icon: Model.ICONS.down,
        title: Model.ICONS.down + " " + Model.formatBps(w.rxBps) + "   " + Model.ICONS.up + " " + Model.formatBps(w.txBps),
        subtitle: w.speedtest
          ? "Speed test " + Model.formatMbps(w.speedtest.downMbps) + " / " + Model.formatMbps(w.speedtest.upMbps)
            + (w.speedtest.at ? " · " + Model.formatAge(new Date(w.speedtest.at * 1000).toISOString(), Date.now()) : "")
          : "Current throughput",
        action: "console"
      })
    }

    if (w.secondary) {
      var s = w.secondary
      var bad = s.status !== "ok" && s.status !== "unknown"
      list.push({
        icon: Model.ICONS.internet,
        title: "Backup WAN" + (s.ip ? " · " + s.ip : ""),
        subtitle: s.isp || "",
        trailing: s.status,
        attention: bad,
        copy: { ip: s.ip },
        action: "copy:ip"
      })
    }
    return list
  }
}
