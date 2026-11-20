"""A parte de farm na interface: passos Vigiar, Janela e Contar no editor, o
limite de rodadas, a janela do gravador, o assistente de farm e os arquivos
.wayclick (exportar e importar). Offscreen e isolado (ver harness.py): os
diálogos são respondidos sozinhos e nada sai para a rede.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_farm_ui.py
"""
import base64, json, os, shutil, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-farmui-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,       # noqa: E402
                               QFileDialog, QMessageBox)

a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US"}, fh)
app = QApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
LEFT = a.BTN["Left"]
norm = a.normalize_step
rng = np.random.default_rng(20261120)


def png(name, w=40, h=20, folder=TMP):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    rgb = rng.integers(0, 256, (h, w, 3)).astype(np.uint8)
    with open(path, "wb") as fh:
        fh.write(a.png_bytes(rgb))
    return path


def shown(d, key):
    return not d.rows.rows[key][1].isHidden()


play, play2, again = png("Play.png"), png("Ready.png"), png("Again.png")
skip, recon = png("Skip.png"), png("Reconnect.png")

# ---------------------------------------------------- editor de passos --
for st in ({"op": "watch", "path": skip, "every": 2500, "act": "left",
            "ok": "jump", "ok_to": 3},
           {"op": "watch", "kind": "pixel", "x": 5, "y": 6,
            "color": "#ff8800", "every": 500, "ok": "stop"},
           {"op": "watch", "kind": "window", "match": "Sober", "want": "gone",
            "every": 5000, "ok": "restart"},
           {"op": "window", "match": "Sober", "act": "focus", "wait": 5000,
            "fail": "restart"},
           {"op": "window", "match": "Sober", "want": "gone", "wait": 0,
            "ok": "jump", "ok_to": 2, "fail": "next"},
           {"op": "count", "name": "matches", "every": 10, "ok": "stop"},
           {"op": "count", "name": "wins"}):
    d = a.StepDialog(None, norm(st))
    check(f"the step editor keeps a {st['op']} {st.get('kind', '')} step "
          "as it was", d.step() == norm(st), f"{d.step()} vs {norm(st)}")

d = a.StepDialog(None, norm({"op": "watch", "kind": "window",
                             "match": "Sober"}))
check("a window watch shows the window, not the image",
      shown(d, "match") and shown(d, "every") and not shown(d, "image")
      and not shown(d, "act"))
d.wkind.setCurrentIndex(d.wkind.findData("image"))
check("switching the watch to an image shows the image and the click",
      shown(d, "image") and shown(d, "act") and not shown(d, "match"))
d = a.StepDialog(None, {"op": "count", "name": "x"})
check("a count without every has no Then", not d.cok.isEnabled())
d.cevery.setValue(5)
check("and with every it has", d.cok.isEnabled())
d.counter.setText("Run")
d._ok()
check("{run} and {time} cannot be counter names",
      d.result() != QDialog.Accepted and "{run}" in d.error.text())

# ---------------------------------------------------- editor de macros --
m = a.normalize_macro({"mode": "toggle", "repeat": True, "limit": 5,
                       "steps": [{"op": "tap", "code": K["E"]}]})
d = a.MacroDialog(None, m)
check("Stop after shows the limit of a looping macro",
      d.limit.isEnabled() and d.macro()["limit"] == 5)
d.repeat.setChecked(False)
check("and does not apply without repeat",
      not d.limit.isEnabled() and d.macro()["limit"] == 0)
d.repeat.setChecked(True)
d.mode.setCurrentIndex(d.mode.findData("once"))
check("nor to a macro that runs once", not d.limit.isEnabled()
      and d.macro()["limit"] == 0)

recorded = [norm({"op": "tap", "code": K["A"]}),
            norm({"op": "wait", "ms": 120})]


def fake_record(self):
    self.steps = list(recorded)
    return QDialog.Accepted


a.RecordDialog.exec = fake_record
d = a.MacroDialog(None, a.normalize_macro({"steps": [
    {"op": "tap", "code": K["E"]},
    {"op": "image", "path": play, "fail": "jump", "fail_to": 3},
    {"op": "tap", "code": K["X"]}]}))
d.step_list.setCurrentRow(0)
d._record()
check("recorded steps go in after the selected step",
      [st["op"] for st in d.steps] == ["tap", "tap", "wait", "image", "tap"]
      and d.steps[1]["code"] == K["A"])
check("and a Go to step N after them still points at the same step",
      d.steps[3]["fail_to"] == 5)
check("the step list shows them", d.step_list.count() == 5)

# ------------------------------------------------------ janela do gravador --


class DummyHub:
    def __init__(self):
        self.subs = []

    def add(self, s):
        self.subs.append(s)

    def remove(self, s):
        if s in self.subs:
            self.subs.remove(s)


class Dev:
    kbd = True

    def has(self, code):
        return True


hub, dev = DummyHub(), Dev()
d = a.RecordDialog(None, hub=hub, cursor=lambda: (10, 20))
d.delay.setValue(1)
d._go()
check("recording starts after a countdown",
      d.rec is None and d._left == 1 and "1 s" in d.state.text())
d._go()
check("which can be cancelled", d.rec is None and d._left == 0
      and not hub.subs and d.b_go.text() == "Start recording")
d.delay.setValue(0)
d._go()
check("with no countdown it records right away",
      d.rec is not None and hub.subs == [d.rec]
      and d.b_go.text() == "Stop recording")
d.rec.on_key(dev, K["A"], 1)
time.sleep(0.03)
d.rec.on_key(dev, K["A"], 0)
d.rec.on_key(dev, LEFT, 1)            # o clique no próprio botão Parar
d.rec.on_key(dev, LEFT, 0)
d._go()
check("stopping with the button leaves that click out",
      d.steps == [norm({"op": "tap", "code": K["A"]})] and not hub.subs
      and d.add.isEnabled(), str(d.steps))
d._go()
d.rec.on_key(dev, LEFT, 1)
d.rec.on_key(dev, LEFT, 0)
d.rec.on_key(dev, K["F10"], 1)        # a tecla de parar
app.processEvents()
check("the stop key ends it too, and keeps the click with its position",
      d.rec is None and d.steps == [norm({"op": "point", "x": 10, "y": 20,
                                          "click": "left"})], str(d.steps))
d.pauses.setChecked(False)
check("the pauses can be left out after recording",
      not [st for st in d.steps if st["op"] == "wait"])
d._go()
d.reject()
check("closing while recording stops it", d.rec is None and not hub.subs)
d = a.RecordDialog(None, hub=hub)
check("without KDE there are no positions, and the dialog says so",
      d._where()[0] is None and "KDE" in d._where()[1])
d.stop_key.set_key(LEFT)
d._go()
check("a mouse button cannot be the stop key",
      d.rec is None and d.error.text() != "")

# ------------------------------------------------------ assistente de farm --
check("Roblox game pages become roblox://placeId links", [a.farm_link(x) for x in (
    "https://www.roblox.com/games/3260590327/Tower-Defense-Simulator",
    "https://web.roblox.com/games/start?placeId=123&launchData=x",
    "roblox://placeId=5", "  ", "steam://run/1")] == [
    "roblox://placeId=3260590327", "roblox://placeId=123",
    "roblox://placeId=5", "", "steam://run/1"])

strat = a.normalize_macro({"name": "Towers", "press_ms": 30.0, "steps": [
    {"op": "tap", "code": K["X"]},
    {"op": "image", "path": play, "wait": 1000, "fail": "restart"},
    {"op": "wait", "ms": 500},
    {"op": "tap", "code": K["Y"]}]})
d = a.FarmDialog(None, [strat, a.normalize_macro({"steps": []})])
check("the strategy list has the macros with steps",
      d.strategy.count() == 2 and d.strategy.itemData(1) == strat["id"])
d._ok()
check("the end of the match button is required",
      d.result() != QDialog.Accepted and "end of a match" in d.error.text())
d.window_name.setEditText("  Sober ")
d.link.setText("https://www.roblox.com/games/3260590327/Tower-Defense")
d.lobby[0].set_path(play)
d._add_lobby(play2)
d.strategy.setCurrentIndex(1)
d.skip.set_path(skip)
d.reconnect.set_path(recon)
d.over.set_path(again)
d.hook.setText("https://discord.com/api/webhooks/1/x")
d.limit.setValue(20)
d._ok()
check("with it, the farm is made", d.result() == QDialog.Accepted,
      d.error.text())
fm = d.macro()
ops = [(st["op"], st.get("kind") if st["op"] == "watch" else
        st.get("path") and os.path.basename(st["path"])) for st in fm["steps"]]
check("it opens the game, focuses it, clicks the lobby, watches, plays and "
      "ends", ops == [
          ("window", None), ("open", None), ("window", None),
          ("window", None), ("watch", "window"), ("watch", "image"),
          ("image", "Play.png"), ("image", "Ready.png"), ("watch", "image"),
          ("watch", "image"), ("tap", None), ("image", "Play.png"),
          ("wait", None), ("tap", None), ("image", "Again.png"),
          ("notify", None)], "\n" + "\n".join(map(str, ops)))
st = fm["steps"]
check("its jumps land on the right steps",
      (st[0]["ok"], st[0]["ok_to"]) == ("jump", 4)
      and (st[6]["fail"], st[6]["fail_to"]) == ("jump", 9)
      and st[7]["fail"] == "restart"
      and (st[9]["ok"], st[9]["ok_to"]) == ("jump", 15)
      and st[3]["act"] == "focus" and st[3]["fail"] == "restart",
      str([(x.get("ok"), x.get("ok_to"), x.get("fail"), x.get("fail_to"))
           for x in st]))
check("a strategy that starts over starts the strategy over, not the farm",
      (st[11]["fail"], st[11]["fail_to"]) == ("jump", 11))
check("the game link, the watches and the report are as asked",
      st[1]["url"] == "roblox://placeId=3260590327"
      and st[2]["match"] == "Sober" and st[4]["want"] == "gone"
      and st[5]["act"] == "left" and st[5]["ok"] == "restart"
      and st[8]["act"] == "left" and st[8]["ok"] == "next"
      and st[9]["act"] == "none" and st[14]["wait"] == 30 * 60000.0
      and st[15]["url"].startswith("https://discord.com/")
      and st[15]["text"] == "Match {run} done, farming for {time}."
      and st[15]["shot"])
check("and the macro loops on F6 and stops after 20 matches",
      (fm["code"], fm["mode"], fm["repeat"], fm["period_ms"], fm["limit"],
       fm["press_ms"]) == (K["F6"], "toggle", True, 0.0, 20, 30.0))
check("its list entry reads well", a.macro_summary(fm).startswith(
    "F6, toggle, stop after 20 runs: Check the “Sober” window is open"),
    a.macro_summary(fm))
lean = a.farm_macro({"over": again})
check("without a link, the lobby or a webhook the farm is short",
      [x["op"] for x in lean["steps"]] == ["window", "watch", "image"]
      and lean["steps"][0]["fail"] == "stop")
try:
    a.farm_macro({"window": "Sober"})
    refused = False
except ValueError:
    refused = True
check("and it never makes a farm without the end of the match", refused)
for field, value, words in (("link", "file:///etc/passwd", "link"),
                            ("hook", "http://example.com/x", "webhook")):
    d = a.FarmDialog(None, [])
    d.over.set_path(again)
    getattr(d, field).setText(value)
    d._ok()
    check(f"a {field} that is not https is refused",
          d.result() != QDialog.Accepted and words in d.error.text(),
          d.error.text())
d = a.FarmDialog(None, [])
n = len(d.lobby)
d._add_lobby("")
d.lobby[-1].b_clear.click()
check("lobby buttons can be added and removed", len(d.lobby) == n
      and d.b_lobby.isEnabled())
for _i in range(10):
    d._add_lobby("")
check("up to 8", len(d.lobby) == 8 and not d.b_lobby.isEnabled())

# ------------------------------------------------- compartilhar (arquivo) --
src = os.path.join(TMP, "src")
img1 = png("button.png", folder=src)
img2 = png("button.png", folder=os.path.join(src, "other"))
m1 = a.normalize_macro({"name": "Farm", "steps": [
    {"op": "image", "path": img1, "fail": "jump", "fail_to": 4},
    {"op": "open", "url": "/usr/bin/xterm"},
    {"op": "image", "path": img2},
    {"op": "notify", "url": "https://discord.com/api/webhooks/1/secret",
     "shot": True},
    {"op": "open", "url": "roblox://placeId=1"}]})
data, missing, hooks = a.export_macros([m1])
blob = json.dumps(data)
check("export puts the images in the file, and two with the same name stay "
      "apart", sorted(data["images"]) == ["button-2.png", "button.png"]
      and not missing)
check("the webhook never goes into the file",
      hooks == 1 and "secret" not in blob
      and data["macros"][0]["steps"][3] == {"op": "notify",
                                            "text": "Run {run}"})
path = os.path.join(TMP, "farm" + a.MACRO_FILE_EXT)
with open(path, "w") as fh:
    fh.write(blob)
dest = os.path.join(TMP, "imported")
dry, _n = a.import_macros(a.read_macro_file(path), dest, dry=True)
check("a dry run writes nothing", not os.path.exists(dest) and len(dry) == 1)
got, notes = a.import_macros(a.read_macro_file(path), dest)
g = got[0]["steps"]
check("import brings the macro back, turned off, with a new id",
      len(got) == 1 and got[0]["id"] != m1["id"] and not got[0]["enabled"]
      and got[0]["name"] == "Farm")
check("with its images saved next to the others, byte for byte",
      all(os.path.dirname(x["path"]) == dest for x in (g[0], g[2]))
      and open(g[0]["path"], "rb").read() == open(img1, "rb").read()
      and open(g[2]["path"], "rb").read() == open(img2, "rb").read())
check("a step that opens a program becomes an empty wait, so jumps still "
      "land right", g[1] == {"op": "wait", "ms": 0.0}
      and g[0]["fail_to"] == 4 and g[4]["url"] == "roblox://placeId=1")
check("and the notes say what changed",
      any("turned off" in x for x in notes)
      and any("/usr/bin/xterm" in x for x in notes), "\n".join(notes))
again_got, _n = a.import_macros(a.read_macro_file(path), dest)
check("importing the same file twice does not copy the images twice",
      sorted(os.listdir(dest)) == ["button-2.png", "button.png"]
      and again_got[0]["steps"][0]["path"] == g[0]["path"])
evil_png = base64.b64encode(a.png_bytes(np.zeros((4, 4, 3), np.uint8)))
evil = {"wayclick": 1, "macros": [{"steps": [
    {"op": "image", "path": "../../evil.sh"},
    {"op": "image", "path": "fake.png"},
    {"op": "notify", "text": "hi", "url": "https://evil.example/hook"},
    {"op": "tap", "code": K["E"]}]}],
    "images": {"../../evil.sh": evil_png.decode(),
               "fake.png": base64.b64encode(b"#!/bin/sh\nrm -rf ~\n").decode(),
               ".hidden": "not base64 !!"}}
got, notes = a.import_macros(evil, dest)
g = got[0]["steps"]
check("an image name cannot leave the images folder",
      os.path.dirname(g[0]["path"]) == dest
      and os.path.basename(g[0]["path"]) == "evil.png"
      and not os.path.exists(os.path.join(TMP, "evil.sh")))
check("what is not an image is not saved, and its step becomes a wait",
      g[1] == {"op": "wait", "ms": 0.0}
      and not [x for x in os.listdir(dest) if x.startswith(("fake", "."))]
      and any("could not be read" in x for x in notes))
check("a webhook in someone else's file is dropped",
      g[2] == {"op": "notify", "text": "hi"}
      and any("Webhooks are not imported (1)" in x for x in notes))
bad = 0
for junk in ({"wayclick": 2, "macros": []}, {"macros": []}, [], "x"):
    try:
        a.import_macros(junk, dest)
    except ValueError:
        bad += 1
with open(os.path.join(TMP, "x.wayclick"), "w") as fh:
    fh.write("not json")
try:
    a.read_macro_file(os.path.join(TMP, "x.wayclick"))
except ValueError:
    bad += 1
check("anything else is refused", bad == 5)

# -------------------------------------------------------------- no app --
QMessageBox.information = lambda *x, **k: None
QMessageBox.warning = lambda *x, **k: None
w = a.App()
w.sound.setChecked(False)
check("the Hotkeys tab has Farm assistant, Export and Import",
      w.m_farm.text() == "Farm assistant…" and w.m_export.text() == "Export…"
      and w.m_load.text() == "Import…" and w.m_import.text() == "Import AHK…"
      and not w.m_export.isEnabled())


def farm_exec(self):
    self.over.set_path(again)
    self.lobby[0].set_path(play)
    self.window_name.setEditText("Sober")
    paused.append(w._macro_editing and not w.engine.active)
    self._ok()
    return self.result()


paused = []
a.FarmDialog.exec = farm_exec
w._farm()
check("the farm assistant adds its macro, turned on and selected",
      len(w.macros) == 1 and w.macros[0]["name"] == "Farm"
      and w.macro_list.currentRow() == 0 and w.macro_box.isChecked()
      and w.m_export.isEnabled())
check("and the other macros paused while it was open", paused == [True])
kw = a.KWIN()
check("a macro that looks at windows gets KWin",
      kw.ok and w.engine.windows is kw, str(kw.error))
out = os.path.join(TMP, "shared" + a.MACRO_FILE_EXT)
QFileDialog.getSaveFileName = lambda *x, **k: (out[:-len(a.MACRO_FILE_EXT)],
                                               "")
w._export_macro()
check("Export saves a .wayclick file", os.path.exists(out))
QFileDialog.getOpenFileName = lambda *x, **k: (out, "")
a.MacroFileDialog.exec = lambda self: QDialog.Accepted
w._import_macros()
imported = [x["path"] for x in w.macros[-1]["steps"] if x.get("path")]
check("Import adds what is in it, turned off, with its images",
      len(w.macros) == 2 and not w.macros[1]["enabled"]
      and w.macros[1]["name"] == "Farm" and imported
      and all(os.path.dirname(x) == a.images_dir() and os.path.exists(x)
              for x in imported), str(imported))
check("a file that is not a macro file is reported, not raised",
      w.import_macro_path(os.path.join(TMP, "x.wayclick"), ask=False) == 0
      and w.import_macro_path("/nonexistent.wayclick", ask=False) == 0)
a.LANG = "pt_BR"
w.retranslate()
check("and the new buttons are translated",
      w.m_farm.text() == "Assistente de farm…"
      and w.m_load.text() == "Importar…"
      and w.m_import.text() == "Importar AHK…")
a.LANG = "en_US"
w.cleanup()
with open(a.CFG) as fh:
    cfg = json.load(fh)
check("saved", len(cfg["macros"]) == 2)
code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
