import QtQuick
import "../components"
import "../Model.js" as Model

// This machine: the Wi-Fi it is on, the access point serving it, signal,
// and its local address. Local data is always there, so this section stays
// useful even when the console is unreachable.
Section {
  id: root
  sectionId: "self"
  heading: "THIS DEVICE"

  readonly property var me: snapshot ? snapshot.self : null

  rows: {
    var s = me
    if (!s) return []
    var list = [{
      icon: Model.selfIcon(s),
      title: Model.selfTitle(s),
      subtitle: Model.selfSubtitle(s),
      trailing: s.ip || "",
      copy: { ip: s.ip, name: s.ssid || s.clientName, mac: s.mac },
      action: "copy:ip"
    }]
    if (s.satisfaction !== null && s.satisfaction !== undefined) {
      list.push({
        icon: Model.ICONS.network,
        title: "Experience " + Model.formatPct(s.satisfaction),
        subtitle: s.clientName ? "Known to UniFi as " + s.clientName : "",
        attention: s.satisfaction < 50,
        copy: { name: s.clientName, mac: s.mac },
        action: "console"
      })
    }
    return list
  }
}
