import QtQuick
import "../components"
import "../Model.js" as Model

// Connected clients, this machine first. Long lists are capped by the
// `maxClients` setting unless the panel's `/` filter is active.
Section {
  id: root
  sectionId: "clients"

  readonly property var summary: snapshot ? snapshot.summary : null
  readonly property string query: panel ? panel.clientFilter : ""
  readonly property int limit: panel ? panel.maxClients : 12
  readonly property var matches: Model.filterClients(snapshot ? snapshot.clients : [], query)

  heading: {
    if (!summary) return "CLIENTS"
    var text = "CLIENTS · " + summary.clientsTotal
    if (summary.clientsWireless || summary.clientsWired)
      text += " (" + summary.clientsWireless + " WI-FI, " + summary.clientsWired + " WIRED)"
    if (query !== "") text += " · \"" + query.toUpperCase() + "\""
    return text
  }

  rows: {
    var list = []
    var shown = query !== "" ? matches.length : Math.min(matches.length, limit)
    for (var i = 0; i < shown; i++) {
      var c = matches[i]
      list.push({
        icon: Model.clientIcon(c),
        title: c.name + (c.self ? " (this device)" : ""),
        subtitle: Model.clientSubtitle(c),
        trailing: c.kind === "wireless" && c.uplink ? c.uplink : "",
        copy: { ip: c.ip, name: c.name, mac: c.mac },
        action: "copy:ip"
      })
    }
    if (matches.length > shown) {
      list.push({
        icon: "…",
        title: (matches.length - shown) + " more",
        subtitle: "Press / to search, or open the console",
        muted: true,
        action: "filter"
      })
    }
    return list
  }
}
