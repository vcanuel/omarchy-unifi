import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// One keyboard-and-mouse navigable row: glyph, title over subtitle, and an
// optional trailing value. Follows the CursorSurface contract — visuals come
// from `hasCursor`, and hovering reports back to the panel instead of
// painting its own highlight.
CursorSurface {
  id: row

  property var panel: null
  property string sectionId: ""
  property int rowIndex: 0

  property string icon: ""
  property string title: ""
  property string subtitle: ""
  property string trailing: ""
  property bool attention: false
  property bool muted: false
  property string trailingIcon: ""

  signal activated()

  readonly property color tone: attention && panel ? panel.urgent : (panel ? panel.foreground : Color.foreground)

  hasCursor: panel ? panel.hasCursor(sectionId, rowIndex) : false
  foreground: panel ? panel.foreground : Color.foreground
  width: parent ? parent.width : implicitWidth
  implicitHeight: Math.max(content.implicitHeight, Style.spacing.popupRowHeight) + Style.spacing.rowPaddingX
  opacity: muted ? 0.55 : 1.0

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onContainsMouseChanged: if (containsMouse && row.panel) row.panel.setCursor(row.sectionId, row.rowIndex)
    onClicked: row.activated()
  }

  RowLayout {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    anchors.leftMargin: Style.space(10)
    anchors.rightMargin: Style.space(10)
    spacing: Style.space(10)

    Text {
      textFormat: Text.PlainText
      text: row.icon
      color: row.tone
      font.family: row.panel ? row.panel.fontFamily : Style.font.family
      font.pixelSize: Style.font.icon
      Layout.alignment: Qt.AlignVCenter
      Layout.preferredWidth: Style.font.icon + Style.space(4)
    }

    ColumnLayout {
      id: content
      Layout.fillWidth: true
      spacing: Style.space(1)

      Text {
        textFormat: Text.PlainText
        Layout.fillWidth: true
        text: row.title
        color: row.tone
        font.family: row.panel ? row.panel.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
      }

      Text {
        textFormat: Text.PlainText
        Layout.fillWidth: true
        visible: text !== ""
        text: row.subtitle
        color: row.panel ? row.panel.dim : Color.muted
        font.family: row.panel ? row.panel.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }
    }

    Text {
      textFormat: Text.PlainText
      visible: row.trailingIcon !== ""
      text: row.trailingIcon
      color: row.panel ? row.panel.foreground : Color.foreground
      font.family: row.panel ? row.panel.fontFamily : Style.font.family
      font.pixelSize: Style.font.icon
      Layout.alignment: Qt.AlignVCenter
    }

    Text {
      textFormat: Text.PlainText
      visible: text !== ""
      text: row.trailing
      color: row.panel ? row.panel.dim : Color.muted
      font.family: row.panel ? row.panel.fontFamily : Style.font.family
      font.pixelSize: Style.font.bodySmall
      Layout.alignment: Qt.AlignVCenter
    }
  }
}
