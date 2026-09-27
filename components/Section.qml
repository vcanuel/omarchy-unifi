import QtQuick
import qs.Commons
import qs.Ui

// Base for every panel section. A section is a heading plus a list of row
// descriptors; subclasses only compute `rows` from the snapshot:
//
//   {
//     icon, title, subtitle, trailing, trailingIcon,   // what the row shows
//     attention: bool, muted: bool,                   // urgent tint / dimmed
//     copy: { ip: "…", name: "…", mac: "…" },         // c / n / m keys
//     action: "copy:ip" | "console" | "setup"         // Enter and click
//   }
//
// The panel drives keyboard navigation through rowCount, rowItem(), and
// activate(), so a new section never touches cursor code. Set `sectionId` to
// the name used in the `sections` setting.
Column {
  id: section

  property var panel: null
  property var snapshot: null
  property string sectionId: ""
  property string heading: ""
  property var rows: []
  readonly property int rowCount: rows.length
  readonly property bool available: rows.length > 0

  function rowItem(index) { return repeater.itemAt(index) }

  function row(index) {
    return index >= 0 && index < rows.length ? rows[index] : null
  }

  function copyValue(index, kind) {
    var r = row(index)
    return r && r.copy && r.copy[kind] ? String(r.copy[kind]) : ""
  }

  function activate(index) {
    var r = row(index)
    if (!r || !panel) return
    var action = String(r.action || "")
    if (action.indexOf("copy:") === 0) panel.copyFromRow(section, index, action.slice(5))
    else if (action !== "") panel.runAction(action)
  }

  visible: available
  width: parent ? parent.width : implicitWidth
  spacing: Style.space(8)

  PanelSeparator {
    foreground: section.panel ? section.panel.foreground : Color.foreground
  }

  PanelSectionHeader {
    text: section.heading
    foreground: section.panel ? section.panel.foreground : Color.foreground
    fontFamily: section.panel ? section.panel.fontFamily : Style.font.family
  }

  Column {
    width: parent.width
    spacing: Style.space(4)

    Repeater {
      id: repeater
      model: section.rows

      StatusRow {
        required property var modelData
        required property int index
        panel: section.panel
        sectionId: section.sectionId
        rowIndex: index
        icon: modelData.icon || ""
        title: modelData.title || ""
        subtitle: modelData.subtitle || ""
        trailing: modelData.trailing || ""
        trailingIcon: modelData.trailingIcon || ""
        attention: modelData.attention === true
        muted: modelData.muted === true
        onActivated: section.activate(index)
      }
    }
  }
}
