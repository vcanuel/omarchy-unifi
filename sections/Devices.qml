import QtQuick
import "../components"
import "../Model.js" as Model

// UniFi infrastructure: gateway, switches, access points. Offline devices are
// tinted urgent; devices with pending firmware carry an update glyph.
Section {
  id: root
  sectionId: "devices"

  readonly property var summary: snapshot ? snapshot.summary : null
  heading: summary ? "DEVICES · " + summary.devicesOnline + "/" + summary.devicesTotal + " ONLINE" : "DEVICES"

  rows: {
    var devices = snapshot ? snapshot.devices : []
    var list = []
    for (var i = 0; i < devices.length; i++) {
      var d = devices[i]
      var load = []
      if (d.cpuPct !== null && d.cpuPct !== undefined) load.push("cpu " + Model.formatPct(d.cpuPct))
      if (d.memPct !== null && d.memPct !== undefined) load.push("mem " + Model.formatPct(d.memPct))
      list.push({
        icon: Model.deviceIcon(d),
        title: d.name,
        subtitle: Model.deviceSubtitle(d),
        trailing: load.join(" "),
        trailingIcon: d.updateAvailable ? Model.ICONS.update : "",
        attention: d.state !== "online",
        copy: { ip: d.ip, name: d.name, mac: d.mac },
        action: "console"
      })
    }
    return list
  }
}
