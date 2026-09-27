import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "components"
import "Model.js" as Model

// Bar button plus popup panel. The popup is a stack of sections; which ones
// appear, and in what order, comes from the `sections` setting. Each section
// is a file under sections/ built on components/Section.qml.
Panel {
  id: root
  moduleName: "vcanuel.unifi"
  ipcTarget: "vcanuel.unifi"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var snapshot: service.snapshot
  readonly property string health: service.health
  readonly property var sectionNames: Model.sectionList(setting("sections", null))
  readonly property int maxClients: Math.max(1, parseInt(setting("maxClients", 12), 10) || 12)
  readonly property string barLabelMode: String(setting("barLabel", "none"))
  readonly property bool needsSetup: health === "unconfigured"
    || (snapshot && snapshot.error && (snapshot.error.kind === "auth" || snapshot.error.kind === "tls"))

  // Keyboard cursor: a section id plus a row index inside it.
  property bool cursorActive: false
  property string cursorSection: ""
  property int cursorIndex: 0
  property string clientFilter: ""
  property bool filtering: false
  property string flash: ""
  property double nowMs: Date.now()

  function hasCursor(sectionId, index) {
    return cursorActive && cursorSection === sectionId && cursorIndex === index
  }

  function setCursor(sectionId, index) {
    cursorActive = true
    cursorSection = sectionId
    cursorIndex = index
  }

  // Sections in on-screen order that currently have rows.
  function navigableSections() {
    var list = []
    if (setupSection.available) list.push(setupSection)
    for (var i = 0; i < sectionRepeater.count; i++) {
      var loader = sectionRepeater.itemAt(i)
      if (loader && loader.item && loader.item.available) list.push(loader.item)
    }
    return list
  }

  function currentSection() {
    var list = navigableSections()
    for (var i = 0; i < list.length; i++) if (list[i].sectionId === cursorSection) return list[i]
    return null
  }

  function moveCursor(dy) {
    var list = navigableSections()
    if (list.length === 0) return
    var at = -1
    for (var i = 0; i < list.length; i++) if (list[i].sectionId === cursorSection) at = i
    if (!cursorActive || at === -1) {
      setCursor(list[0].sectionId, 0)
    } else {
      var next = cursorIndex + dy
      if (next < 0) {
        if (at > 0) setCursor(list[at - 1].sectionId, list[at - 1].rowCount - 1)
      } else if (next >= list[at].rowCount) {
        if (at < list.length - 1) setCursor(list[at + 1].sectionId, 0)
      } else {
        cursorIndex = next
      }
    }
    scrollCursorIntoView()
  }

  // Rows come and go on every refresh; keep the cursor on something real.
  function clampCursor() {
    if (!cursorActive) return
    var section = currentSection()
    if (!section) {
      var list = navigableSections()
      if (list.length === 0) { cursorActive = false; return }
      setCursor(list[0].sectionId, 0)
      return
    }
    if (cursorIndex >= section.rowCount) cursorIndex = Math.max(0, section.rowCount - 1)
  }

  function scrollCursorIntoView() {
    var section = currentSection()
    var item = section ? section.rowItem(cursorIndex) : null
    if (!item) return
    Qt.callLater(function() {
      var margin = Style.space(6)
      var top = item.mapToItem(flick.contentItem, 0, 0).y
      var bottom = top + item.height
      var maxY = Math.max(0, flick.contentHeight - flick.height)
      if (top < flick.contentY + margin) flick.contentY = Math.max(0, top - margin)
      else if (bottom > flick.contentY + flick.height - margin) flick.contentY = Math.min(maxY, bottom + margin - flick.height)
    })
  }

  function activateCursor() {
    var section = currentSection()
    if (section) section.activate(cursorIndex)
  }

  function copySelected(kind) {
    var section = currentSection()
    if (section) copyFromRow(section, cursorIndex, kind)
  }

  function copyFromRow(section, index, kind) {
    var value = section.copyValue(index, kind)
    if (value === "") return
    service.copy(value)
    showFlash("Copied " + value)
  }

  function runAction(action) {
    if (action === "console") service.openConsole()
    else if (action === "setup") { service.runSetup(); close() }
    else if (action === "refresh") service.refresh()
    else if (action === "filter") startFilter()
  }

  function startFilter() {
    filtering = true
    Qt.callLater(function() { filterField.forceActiveFocus(); filterField.selectAll() })
  }

  function stopFilter(keep) {
    filtering = false
    if (!keep) clientFilter = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function showFlash(text) {
    flash = text
    flashTimer.restart()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    nowMs = Date.now()
    flick.contentY = 0
    service.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  } else if (filtering) {
    stopFilter(false)
  }
  onSnapshotChanged: Qt.callLater(clampCursor)

  Service {
    id: service
    settings: root.settings
  }

  Timer {
    id: flashTimer
    interval: 1800
    onTriggered: root.flash = ""
  }

  // Keeps "updated 20s ago" honest while the panel is open.
  Timer {
    interval: 10000
    repeat: true
    running: root.opened
    onTriggered: root.nowMs = Date.now()
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { service.refresh(); return "ok" }
    function status(): string { return root.health }
    function snapshot(): string { return JSON.stringify(service.snapshot) }
    function settings(): string { return JSON.stringify(root.settings) }
  }

  readonly property string barText: Model.barLabel(snapshot, barLabelMode)
  readonly property color barTone: Model.stateNeedsAttention(health) ? urgent
    : (health === "stale" || health === "loading" ? Qt.darker(barForeground, 1.55) : barForeground)
  readonly property Item button: barText === "" ? iconButton : textButton

  function handlePress(buttonCode) {
    if (buttonCode === Qt.RightButton) service.openConsole()
    else if (buttonCode === Qt.MiddleButton) service.refresh()
    else if (health === "unconfigured") service.runSetup()
    else toggle()
  }

  // Icon alone by default; icon plus a short value when `barLabel` asks for one.
  BarIconButton {
    id: iconButton
    anchors.fill: parent
    visible: root.barText === ""
    bar: root.bar
    text: Model.barIcon(root.health)
    foreground: root.barTone
    tooltipText: root.opened ? "" : Model.tooltip(root.snapshot, root.health)
    onPressed: function(buttonCode) { root.handlePress(buttonCode) }
  }

  WidgetButton {
    id: textButton
    anchors.fill: parent
    visible: root.barText !== ""
    bar: root.bar
    text: Model.barIcon(root.health) + " " + root.barText
    foreground: root.barTone
    tooltipText: root.opened ? "" : Model.tooltip(root.snapshot, root.health)
    onPressed: function(buttonCode) { root.handlePress(buttonCode) }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.filtering
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy) }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var key = t.toLowerCase()
        if (key === "r") service.refresh()
        else if (key === "o") service.openConsole()
        else if (key === "s") root.runAction("setup")
        else if (key === "c") root.copySelected("ip")
        else if (key === "n") root.copySelected("name")
        else if (key === "m") root.copySelected("mac")
        else if (t === "/") root.startFilter()
      }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: flick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: Model.heroTitle(root.snapshot)
            meta: Model.heroMeta(root.snapshot, root.health)
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.health === "stale" || root.health === "loading" ? 0.5 : 1.0
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Model.barIcon(root.health)
                color: Model.stateNeedsAttention(root.health) ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              Row {
                spacing: Style.space(4)
                PanelActionButton {
                  iconText: Model.ICONS.refresh
                  tooltipText: service.refreshing ? "Refreshing…" : "Refresh (r)"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  opacity: service.refreshing ? 0.5 : 1.0
                  onClicked: service.refresh()
                }
                PanelActionButton {
                  visible: service.consoleUrl !== ""
                  iconText: Model.ICONS.open
                  tooltipText: "Open UniFi console (o)"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: service.openConsole()
                }
              }
            }
          }

          // Errors and warnings beyond the one in the hero line.
          Column {
            width: parent.width
            spacing: Style.space(2)
            visible: issueRepeater.count > 0

            Repeater {
              id: issueRepeater
              model: {
                var issues = root.snapshot ? root.snapshot.issues : []
                var shown = []
                for (var i = 0; i < issues.length; i++) {
                  if (issues[i].severity === "info" || i === 0) continue
                  shown.push(issues[i])
                }
                for (var j = 0; j < issues.length; j++) if (issues[j].severity === "info") shown.push(issues[j])
                return shown
              }
              Text {
                required property var modelData
                textFormat: Text.PlainText
                width: parent.width
                text: (modelData.severity === "info" ? "· " : Model.ICONS.alert + " ") + modelData.message
                color: modelData.severity === "info" ? root.dim : root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }
          }

          Section {
            id: setupSection
            panel: root
            snapshot: root.snapshot
            sectionId: "setup"
            heading: "SETUP"
            rows: root.needsSetup ? [{
              icon: Model.ICONS.key,
              title: root.health === "unconfigured" ? "Connect to your UniFi console" : Model.errorMessage(root.snapshot.error),
              subtitle: "Opens a terminal to enter the console address and API key",
              attention: root.health !== "unconfigured",
              action: "setup"
            }] : []
          }

          Repeater {
            id: sectionRepeater
            model: root.sectionNames

            Loader {
              id: sectionLoader
              required property string modelData
              width: column.width
              // Column only skips invisible children; an empty section's
              // Loader would otherwise keep its height and leave a gap.
              visible: item !== null && item.available
              source: Qt.resolvedUrl("sections/" + modelData.charAt(0).toUpperCase() + modelData.slice(1) + ".qml")
              onLoaded: item.panel = root
              onStatusChanged: if (status === Loader.Error) console.warn("unifi: cannot load section", modelData)

              Binding {
                target: sectionLoader.item
                property: "snapshot"
                value: root.snapshot
                when: sectionLoader.item !== null
              }
            }
          }

          TextField {
            id: filterField
            visible: root.filtering
            width: parent.width
            foreground: root.foreground
            placeholderText: "Filter clients by name, IP, MAC, SSID"
            text: root.clientFilter
            onTextChanged: root.clientFilter = text
            onAccepted: root.stopFilter(true)
            Keys.onEscapePressed: root.stopFilter(false)
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: {
              if (root.flash !== "") return root.flash
              if (service.lastError !== "") return service.lastError
              var parts = []
              if (root.snapshot && root.snapshot.console && root.snapshot.console.networkVersion)
                parts.push("Network " + root.snapshot.console.networkVersion)
              if (service.lastSuccessMs > 0)
                parts.push("updated " + Model.formatAge(new Date(service.lastSuccessMs).toISOString(), root.nowMs))
              return parts.join(" · ")
            }
            visible: text !== ""
            color: service.lastError !== "" && root.flash === "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
          }
        }
      }
    }
  }
}
