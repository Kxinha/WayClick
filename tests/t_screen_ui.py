"""A interface dos passos de tela: diálogo de passo, seletor na tela, saltos
que acompanham a lista e o ponteiro que a janela cria. Offscreen e isolado
(ver harness.py): a captura é falsa, nada pede para ver a tela de verdade.

    QT_QPA_PLATFORM=offscreen nice -n 19 dbus-run-session -- \\
        python3 tests/t_screen_ui.py
"""
import json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-screenui-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt                # noqa: E402
from PySide6.QtGui import QImage                            # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,       # noqa: E402
                               QDialogButtonBox)

app = QApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
rng = np.random.default_rng(7)
rgb1 = rng.integers(0, 256, (300, 400, 3), dtype=np.uint8)
rgb2 = rng.integers(0, 256, (400, 600, 3), dtype=np.uint8)
frames = [a.Frame(rgb1, 0, 0), a.Frame(rgb2, 400, 0, 300, 200)]  # 2ª a 200%
png = os.path.join(TMP, "Play.png")
with open(png, "wb") as fh:
    fh.write(a.png_bytes(rgb1[10:40, 20:80]))

# --------------------------------------------------- ida e volta --
cases = {
    "point": {"op": "point", "x": 640, "y": 360, "click": "double",
              "human": True, "ms": 450.0, "spread": 5},
    "image": {"op": "image", "path": png, "conf": 0.9,
              "region": [10, 20, 300, 200], "want": "seen", "wait": 12000.0,
              "act": "right", "human": True, "ms": 200.0, "ok": "jump",
              "ok_to": 4, "fail": "restart"},
    "pixel": {"op": "pixel", "x": 5, "y": 6, "color": "#12ab34", "tol": 30,
              "want": "seen", "wait": 0.0, "act": "left", "ok": "stop",
              "fail": "jump", "fail_to": 2},
    "notify": {"op": "notify", "text": "Done",
               "url": "https://discord.com/api/webhooks/1/x", "shot": True},
    "open": {"op": "open", "url": "steam://run/1"},
}
for op, st in cases.items():
    d = a.StepDialog(None, st)
    got = d.step()
    check(f"step dialog keeps a {op} step as it was", got ==
          a.normalize_step(st), f"{got}")
rows = {op: a.StepDialog(None, st) for op, st in cases.items()}


def shown(d, key):
    return d.rows.rows[key][1].isVisibleTo(d)


check("each kind shows its own fields",
      shown(rows["point"], "pos") and not shown(rows["point"], "image")
      and shown(rows["image"], "conf") and not shown(rows["image"], "pos")
      and shown(rows["pixel"], "color") and shown(rows["pixel"], "pos")
      and shown(rows["notify"], "hook") and not shown(rows["notify"], "wait")
      and shown(rows["open"], "link") and not shown(rows["open"], "key"))
d = a.StepDialog(None, dict(cases["image"], want="gone"))
check("waiting for it to disappear: no click and no movement to set",
      not d.act.isEnabled() and not d.human.isEnabled()
      and "human" not in d.step())
d = a.StepDialog(None, cases["pixel"])
d.want.setCurrentIndex(d.want.findData("gone"))
check("the pixel wait list has its own words",
      [d.want.itemText(i) for i in range(d.want.count())]
      == ["has the color", "loses the color"]
      and d.step()["want"] == "gone")
d = a.StepDialog(None, cases["notify"])
d.hook.setText("")
check("a screenshot needs a webhook", not d.shot.isEnabled()
      and "shot" not in d.step() and "url" not in d.step())

errors = {}
for op, st in (("image", {"op": "image"}),
               ("pixel", {"op": "pixel", "color": "red"}),
               ("notify", {"op": "notify"}), ("open", {"op": "open"})):
    d = a.StepDialog(None, st)
    d.op.setCurrentIndex(d.op.findData(op))
    d._ok()
    errors[op] = (d.error.text(), d.result())
check("incomplete steps are refused with a reason",
      all(t and r == 0 for t, r in errors.values()), str(errors))
a.LANG = "pt_BR"
d = a.StepDialog(None, {"op": "image"})
d.op.setCurrentIndex(d.op.findData("image"))
d._ok()
check("and the reason is translated",
      d.error.text() == a.TRANSLATIONS["pt_BR"]["Pick an image first."]
      != "Pick an image first.", d.error.text())
a.LANG = "en_US"

# ------------------------------------------------------- seletor --
pk = a.ScreenPicker(None, frames, "point")
pk.show()
pk.screen.setCurrentIndex(1)
pk.zoom.setCurrentIndex(pk.zoom.findData(2.0))
app.processEvents()
QTest.mouseMove(pk.canvas, QPoint(83, 43))
QTest.mousePress(pk.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(83, 43))
QTest.mouseRelease(pk.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(83, 43))
app.processEvents()
check("a click at 200% zoom on the 200% screen picks the logical point",
      pk.result() == QDialog.Accepted and pk.point == (420, 10),
      str(pk.point))
check("and keeps the color the macro will read there",
      pk.color == a.pixel_at(frames, *pk.point), f"{pk.color}")
pk.close()

pk = a.ScreenPicker(None, frames, "area")
pk.screen.setCurrentIndex(1)
pk.canvas.start, pk.canvas.end = (10, 20), (11, 21)
pk._picked()
small = pk.rect is None and not pk.buttons.button(
    QDialogButtonBox.Ok).isEnabled()
pk.canvas.start, pk.canvas.end = (109, 59), (10, 20)
pk._picked()
check("an area needs a real drag, and maps to logical coordinates",
      small and pk.rect == [405, 10, 50, 20]
      and pk.crop.shape == (40, 100, 3)
      and np.array_equal(pk.crop, rgb2[20:60, 10:110])
      and pk.buttons.button(QDialogButtonBox.Ok).isEnabled(), str(pk.rect))


# ------------------------------------------- capturar pelo diálogo --
class FakeCap:
    def __init__(self, frames, state="on", error=""):
        self._frames, self.state, self.error = frames, state, error
        self.users = self.starts = 0

    def start(self):
        self.starts += 1

    def acquire(self):
        self.users += 1

    def release(self):
        self.users -= 1

    def frames(self):
        return self._frames

    def stop(self, join=True):
        self.state = "off"


PICK = {}


def fake_exec(self):
    self.screen.setCurrentIndex(PICK["screen"])
    self.canvas.start, self.canvas.end = PICK["start"], PICK["end"]
    self._picked()
    if self.mode == "area" and self.rect:
        self.accept()
    return self.result()


a.ScreenPicker.exec = fake_exec
cap = FakeCap(frames)
a.SCREEN = lambda: cap
d = a.StepDialog(None, {"op": "pixel", "x": 0, "y": 0, "color": "#000000"})
d.delay.setValue(0)
PICK.update(screen=1, start=(41, 21), end=(41, 21))
d._capture("point")
want = a.color_hex(a.pixel_at(frames, 420, 10))
check("Pick on screen fills the position and the color",
      (d.px.value(), d.py.value()) == (420, 10) and d.color.text() == want
      and cap.users == 0 and d.b_pick.isEnabled(),
      f"{d.px.value()}, {d.py.value()} {d.color.text()}")
d = a.StepDialog(None, {"op": "image", "path": ""})
d.op.setCurrentIndex(d.op.findData("image"))
d.delay.setValue(0)
PICK.update(screen=0, start=(20, 10), end=(79, 39))
d._capture("image")
saved = d.img_path
ok = bool(saved) and os.path.dirname(saved) == a.images_dir()
if ok:
    back = a.qimage_rgb(QImage(saved))
    ok = np.array_equal(back, rgb1[10:40, 20:80])
check("Capture from screen saves the cut as a PNG in the data folder", ok,
      saved)
PICK.update(screen=1, start=(0, 0), end=(199, 99))
d._capture("region")
check("Pick area sets where to search", d.region == [400, 0, 100, 50]
      and d.step()["region"] == [400, 0, 100, 50], str(d.region))
cap.state, cap.error = "failed", "Screen sharing was not allowed."
d._capture("point")
check("a refused screen share is shown, and the buttons come back",
      d.error.text() == "Screen sharing was not allowed."
      and d.b_shot.isEnabled() and d.op.isEnabled())
cap.state, cap.error, cap._frames = "on", "", []
d._capture("image")
check("no picture: says so", "No picture" in d.error.text(), d.error.text())

# ------------------------------------------------------------ saltos --
A_, B_, C_ = K["A"], K["B"], K["C"]
md = a.MacroDialog(None, {"steps": [
    {"op": "tap", "code": A_},
    {"op": "image", "path": png, "fail": "jump", "fail_to": 4},
    {"op": "tap", "code": B_},
    {"op": "tap", "code": C_}]})
a.StepDialog.exec = lambda self: QDialog.Accepted
md.step_list.setCurrentRow(0)
md._add_step()
s1 = [st.get("code") for st in md.steps], md.steps[2]["fail_to"]
md.step_list.setCurrentRow(4)
md._move_step(-1)
s2 = [st.get("code") for st in md.steps], md.steps[2]["fail_to"]
md.step_list.setCurrentRow(1)
md._del_step()
s3 = [st.get("code") for st in md.steps], md.steps[1]["fail_to"]
check("Go to step N follows inserts, moves and deletes",
      s1 == ([A_, A_, None, B_, C_], 5) and s2 == ([A_, A_, None, C_, B_], 4)
      and s3 == ([A_, None, C_, B_], 3), f"{s1} {s2} {s3}")
check("and the list says where it goes",
      "go to step 3" in md.step_list.item(1).text(),
      md.step_list.item(1).text())

# ------------------------------------------------------------ janela --
a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US", "macros": [
        {"name": "keys", "code": K["F8"], "mode": "once",
         "steps": [{"op": "tap", "code": K["E"]}]}]}, fh)
seen = []
orig_changed = a.App._screens_changed


def counting(self, *x):
    seen.append(1)
    return orig_changed(self, *x)


a.App._screens_changed = counting
a.SCREEN = lambda: FakeCap([])
w = a.App()
w.sound.setChecked(False)
check("macros without screen steps create no pointer", w.pointer is None)
ws = None
for sc in QApplication.screens():
    g = sc.geometry()
    ws = g if ws is None else ws.united(g)
check("the workspace is all screens together",
      w.engine.workspace == (ws.x(), ws.y(), ws.width(), ws.height()),
      str(w.engine.workspace))
for _i in range(5):
    w._screens_changed()
seen.clear()
QApplication.screens()[0].geometryChanged.emit(QRect(0, 0, 10, 10))
check("a screen change is handled once, however many came before",
      len(seen) == 1, f"{len(seen)} calls")
w.macros.append(a.normalize_macro({
    "name": "go", "code": K["F9"], "mode": "once",
    "steps": [{"op": "point", "x": 10, "y": 10, "click": "left"}]}))
w._apply_macros()
check("a macro that points creates the absolute pointer, captured here",
      w.pointer is not None and w.engine.pointer is w.pointer
      and w.pointer.node in h.READERS)
ptr = w.pointer
w.cleanup()
check("closing the window removes the pointer", ptr.fd == -1
      and w.pointer is None)
saved = json.load(open(a.CFG))
check("and the point step is saved", saved["macros"][1]["steps"][0] ==
      {"op": "point", "x": 10, "y": 10, "click": "left"})

code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
