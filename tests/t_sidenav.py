"""A barra lateral que trocou as abas: a lista de páginas em grupos, a
busca, o modo estreito só com ícones, os atalhos de teclado e a API de
QTabWidget que o resto do app usa. Primeiro a SideNav sozinha, depois a
janela de verdade (sem mostrar nada no desktop: QT_QPA_PLATFORM=offscreen).

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_sidenav.py
"""
import json, os, shutil, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-sidenav-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
RT = os.path.join(TMP, "rt")
os.makedirs(RT, mode=0o700)
os.environ["XDG_RUNTIME_DIR"] = RT
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtCore import Qt                               # noqa: E402
from PySide6.QtGui import QIcon                             # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox,     # noqa: E402
                               QLabel, QVBoxLayout, QWidget)

a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US"}, fh)
app = QApplication(sys.argv)
a.LANG = "en_US"


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.005)


def page(*texts, width=300):
    w = QWidget()
    col = QVBoxLayout(w)
    for t in texts:
        col.addWidget(QCheckBox(t) if t.startswith("[") else QLabel(t))
    w.setMinimumWidth(width)
    return w


def rows(nav):
    """O que a lista mostra: (texto, escondida, título de grupo)."""
    out = []
    for r in range(nav.list.count()):
        it = nav.list.item(r)
        out.append((it.text(), it.isHidden(),
                    it.data(Qt.UserRole) == -1))
    return out


# ---------------------------------------------------- a SideNav sozinha --
host = QWidget()
lay = QVBoxLayout(host)
nav = a.SideNav()
lay.addWidget(nav)
changes = []
nav.currentChanged.connect(changes.append)
p_click = page("Interval of the clicks", "[Enable clicking")
p_keys = page("Key to press", "Ação do teclado")
p_dock = page("Size of the Dock window")
p_tds = page("Strategy and screens")
ic = QIcon()
check("addTab returns the index, like QTabWidget",
      [nav.addTab(p_click, "Click", ic, "Automation"),
       nav.addTab(p_keys, "Keyboard", ic, "Automation"),
       nav.addTab(p_dock, "Dock", ic, "Games"),
       nav.addTab(p_tds, "Tower Defense", ic, "Games")] == [0, 1, 2, 3])
check("the list has a title per group, then its pages",
      [(t, g) for t, _h, g in rows(nav)] ==
      [("Automation", True), ("Click", False), ("Keyboard", False),
       ("Games", True), ("Dock", False), ("Tower Defense", False)],
      str(rows(nav)))
heads = [nav.list.item(r) for r in range(nav.list.count())
         if nav.list.item(r).data(Qt.UserRole) == -1]
check("a group title cannot be selected",
      all(not (it.flags() & Qt.ItemIsSelectable)
          and not (it.flags() & Qt.ItemIsEnabled) for it in heads))
check("the first page is the current one, with no signal yet",
      nav.count() == 4 and nav.currentIndex() == 0
      and nav.currentWidget() is p_click and changes == [])
nav.setCurrentWidget(p_dock)
check("setCurrentWidget shows the page and says so",
      nav.currentWidget() is p_dock and nav.currentIndex() == 2
      and changes == [2] and nav.list.currentItem().text() == "Dock")
nav.setCurrentIndex(0)
check("setCurrentIndex too", nav.currentWidget() is p_click
      and changes == [2, 0])
nav.setCurrentIndex(9)
nav.setCurrentIndex(-1)
check("an index that does not exist changes nothing",
      nav.currentIndex() == 0 and changes == [2, 0])
check("indexOf, widget and tabText",
      nav.indexOf(p_tds) == 3 and nav.indexOf(host) == -1
      and nav.widget(1) is p_keys and nav.widget(7) is None
      and nav.tabText(3) == "Tower Defense" and nav.tabText(8) == "")
nav.setTabText(2, "● Dock")
check("setTabText changes the name in the list",
      nav.tabText(2) == "● Dock" and rows(nav)[4][0] == "● Dock")

host.resize(900, 500)
host.show()
pump(0.1)
check("wide enough, the list shows names, the search field and no button",
      not nav._compact and nav.side.width() == nav.WIDE
      and not nav.search.isHidden() and nav.find_btn.isHidden(),
      f"{nav.width()} need {nav._need()}")

# ------------------------------------------------------------- a busca --
QTest.keyClicks(nav.search, "acao")
pump()
vis = [t for t, hid, g in rows(nav) if not hid]
check("the search finds a page by its texts, without accents",
      vis == ["Automation", "Keyboard"], str(vis))
nav.search.setText("DOCK window")
pump()
vis = [t for t, hid, g in rows(nav) if not hid]
check("every word has to match, in any case",
      vis == ["Games", "● Dock"], str(vis))
nav.search.setText("games")
pump()
vis = [t for t, hid, g in rows(nav) if not hid]
check("the name of a group finds its pages",
      vis == ["Games", "● Dock", "Tower Defense"], str(vis))
nav.search.setText("enable clicking")
pump()
check("so does the text of a checkbox",
      [t for t, hid, g in rows(nav) if not hid] == ["Automation", "Click"])
nav.search.setText("zzz nothing")
pump()
check("no match says so, and hides the titles too",
      all(hid for _t, hid, _g in rows(nav)) and not nav.empty.isHidden()
      and nav.empty.text() == "Nothing found.")
nav.search.setText("strategy")
pump()
changes.clear()
QTest.keyClick(nav.search, Qt.Key_Return)
pump()
check("Enter opens the first page found",
      nav.currentWidget() is p_tds and changes == [3])
nav.search.setText("dock")
pump()
nav.setCurrentWidget(p_click)
check("showing a page the search hides clears the search",
      nav.search.text() == "" and nav.currentWidget() is p_click
      and not any(hid for _t, hid, _g in rows(nav)))

# ---------------------------------------------------- teclado e setas --
nav.setCurrentIndex(1)
changes.clear()
nav.step(1)
check("the next page skips the group title",
      nav.currentIndex() == 2 and changes == [2])
nav.step(1)
nav.step(1)
check("and goes around at the end", nav.currentIndex() == 0)
nav.step(-1)
check("back goes around too", nav.currentIndex() == 3)
nav.list.setFocus()
nav.setCurrentIndex(1)
QTest.keyClick(nav.list, Qt.Key_Down)
pump()
check("arrow down in the list skips the group title as well",
      nav.currentIndex() == 2, nav.list.currentItem().text())
nav.search.setText("key")
pump()
nav.setCurrentIndex(1)
nav.step(1)
check("with a search, the next page is the next one found",
      nav.currentIndex() == 1)
nav.search.clear()

# --------------------------------------------- estreita: só os ícones --
host.resize(380, 500)
pump(0.1)
names = [t for t, _hid, _g in rows(nav)]
tips = [nav.list.item(r).toolTip() for r in range(nav.list.count())
        if nav.list.item(r).data(Qt.UserRole) != -1]
check("narrow, the list is only icons, the names in tooltips",
      nav._compact and nav.narrow() and nav.side.width() == nav.NARROW
      and all(t == "" for t, _h, g in rows(nav) if not g)
      and tips == ["Click", "Keyboard", "● Dock", "Tower Defense"],
      f"{nav.width()} {names} {tips}")
check("the group titles are hidden and the search is a button",
      all(hid for _t, hid, g in rows(nav) if g)
      and nav.search.isHidden() and not nav.find_btn.isHidden())
check("the page itself keeps its width",
      nav.stack.width() >= p_click.minimumWidth(), str(nav.stack.width()))
icon_on = nav.list.item(4).icon()           # o ● Dock
icon_off = nav.list.item(5).icon()          # o Tower Defense
check("a page that is on gets the dot on its icon",
      not icon_on.isNull() and icon_off.isNull())
nav.start_search()
pump()
check("the search button opens the list with names, focused on search",
      nav._peek and not nav.narrow() and nav.side.width() == nav.WIDE
      and not nav.search.isHidden() and nav.search.hasFocus()
      and rows(nav)[1][0] == "Click")
QTest.keyClicks(nav.search, "tower")
pump()
QTest.keyClick(nav.search, Qt.Key_Return)
pump()
check("picking a page closes it again",
      nav.currentWidget() is p_tds and not nav._peek and nav.narrow()
      and nav.side.width() == nav.NARROW)
nav.search.clear()
nav.start_search()
pump()
stops = []
esc = a.QShortcut(a.QKeySequence("Esc"), host)
esc.activated.connect(lambda: stops.append(1))
QTest.keyClick(nav.search, Qt.Key_Escape)
pump()
check("Esc in the search closes it, and is not the Esc of the window",
      not nav._peek and nav.narrow() and stops == [])
host.resize(900, 500)
pump(0.1)
check("wide again, the names come back",
      not nav._compact and nav.side.width() == nav.WIDE
      and rows(nav)[1][0] == "Click" and rows(nav)[4][0] == "● Dock")
check("the minimum width is that of the narrow list",
      nav.minimumSizeHint().width()
      == nav.NARROW + nav.layout().spacing()
      + nav.stack.minimumSizeHint().width())
host.close()

# ---------------------------------------------------------- a janela --
w = a.App()
w.sound.setChecked(False)
names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
check("the window has the eight pages in the sidebar",
      [n.replace("● ", "") for n in names] ==
      ["Click", "Keyboard", "Hotkeys", "Window", "Trigger", "Dock",
       "Tower Defense", "Sober accounts"], str(names))
groups = [t for t, _h, g in rows(w.tabs) if g]
check("in two groups", groups == ["Automation", "Games"], str(groups))
check("every page has an icon, even without the icon theme",
      all(not w.tabs.tabIcon(i).isNull() for i in range(w.tabs.count())))
w.show()
pump(0.2)
check("it opens wide enough for the names",
      not w.tabs._compact and w.width() >= 600, f"{w.width()}x{w.height()}")
w.resize(470, 500)
pump(0.2)
check("and can shrink to the narrow list",
      w.tabs.narrow() and w.width() < 520, str(w.width()))
w.resize(660, 500)
pump(0.2)
w.tabs.setCurrentWidget(w.sober_page)
pump(0.1)
check("the Sober accounts page opens from the list",
      w.tabs.currentWidget() is w.sober_page
      and w.tabs.list.currentItem().text() == "Sober accounts")
w.tabs.search.setText("jogos")
pump()
check("the search sees only the language on screen",
      [t for t, hid, g in rows(w.tabs) if not hid and not g] == [],
      str([t for t, hid, g in rows(w.tabs) if not hid]))
w.tabs.search.setText("anti-afk")
pump()
check("and finds the Trigger page by its anti-AFK box",
      [t for t, hid, g in rows(w.tabs) if not hid and not g]
      == ["Trigger"], str([t for t, hid, g in rows(w.tabs) if not hid]))
w.tabs.search.clear()
a.LANG = "pt_BR"
w.retranslate()
pump()
check("translated: pages, groups and the search",
      w.tabs.tabText(w.tabs.indexOf(w.sober_page)) == "Contas do Sober"
      and [t for t, _h, g in rows(w.tabs) if g] == ["Automação", "Jogos"]
      and w.tabs.search.placeholderText() == "Buscar…")
w.tabs.search.setText("jogos")
pump()
check("and the search follows the language",
      "Contas do Sober" in [t for t, hid, g in rows(w.tabs) if not hid])
w.tabs.search.clear()
a.LANG = "en_US"
w.retranslate()
w.cleanup()

code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
