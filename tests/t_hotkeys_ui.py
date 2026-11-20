"""A aba Atalhos de ponta a ponta, offscreen e isolada (ver harness.py).

Importa o SpamE F1.ahk pela mesma função do botão Importar AHK, aperta F1 num
teclado falso, confere o E saindo pelo teclado virtual, a linha de status, a
tradução, o editor, o atalho de armar escondido, a config salva e relida, e o
--import da linha de comando.

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_hotkeys_ui.py
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AHK = os.path.join(HERE, "ahk", "SpamE F1.ahk")
a.CFG = "/tmp/wayclick-test-hotkeys-ui.json"
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US", "delay": 0,
               "click_enabled": False}, fh)
app = QApplication(sys.argv)
K = a.KEYS


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


kbd = h.fake_keyboard()
h.wait_grabbed(a.HUB(), [kbd])
w = a.App()
w.sound.setChecked(False)
pump(0.2)
ck = h.clone_of(w.watcher.hub, kbd)

n = w.import_ahk_path(AHK, ask=False)
check("Import AHK adds the script's macro", n == 1 and len(w.macros) == 1
      and w.macro_list.count() == 1, w.macro_list.item(0).text()
      if w.macro_list.count() else "")
check("the Hotkeys tab is marked as active",
      w.tabs.tabText(2).startswith("● ") and "Hotkeys" in w.tabs.tabText(2))
check("the virtual keyboard was created for it", w.keyboard is not None)
out = h.Recorder(w.keyboard.node)

kbd.tap(K["F1"])
pump(0.4)
status = w.status.text()
state = w.macro_state.text()
kbd.tap(K["F1"])
pump(0.15)
out.poll()
taps = len(out.keys(K["E"], 1))
check("F1 on the keyboard starts and stops it, every 15.6 ms like on "
      "Windows", 20 <= taps <= 33, f"{taps} taps")
check("status line and tab show it running",
      "⚡ SpamE" in status and "Running: SpamE" in state, f"{status!r}")
check("and stop showing it after", "⚡" not in w.status.text()
      and not w.macro_state.text())
ck.poll()
check("F1 never reached the app", not ck.keys(K["F1"]))

# editor: período e passo, sem mostrar a janela
dlg = a.MacroDialog(w, w.macros[0])
dlg.period.setValue(50)
dlg.mods["ctrl"].setChecked(True)
m = dlg.macro()
check("the editor keeps the hotkey and steps, and changes the rest",
      (m["code"], m["mods"], m["period_ms"], m["steps"][0]["code"], m["id"])
      == (K["F1"], ["ctrl"], 50.0, K["E"], w.macros[0]["id"]))
dlg.deleteLater()
sd = a.StepDialog(w, {"op": "wait", "ms": 20, "max": 40})
check("the step editor reads a random wait",
      sd.step() == {"op": "wait", "ms": 20.0, "max": 40.0})
sd.op.setCurrentIndex(sd.op.findData("text"))
sd.text.setText("gg")
check("and a text step", sd.step() == {"op": "text", "text": "gg"})
sd.deleteLater()

# editar pausa os macros
w._macro_editing = True
w._apply_macros()
paused = not w.engine.active
w._macro_editing = False
w._apply_macros()
check("editing pauses the macros and closing resumes them",
      paused and w.engine.active)

# desmarcar o item desliga só ele
w.macro_list.item(0).setCheckState(a.Qt.Unchecked)
pump(0.05)
out.clear()
kbd.tap(K["F1"])
pump(0.2)
ck.poll()
check("an unchecked macro does nothing and F1 passes again",
      not out.poll() and len(ck.keys(K["F1"])) == 2)
w.macro_list.item(0).setCheckState(a.Qt.Checked)
pump(0.05)

# atalho de armar escondido (a macro de teclado só para ter o que armar:
# ela tecla no teclado virtual, que o teste captura)
w.key_box.setChecked(True)
w.hk_block.setChecked(True)
pump(0.1)
ck.clear()
kbd.tap(w.hk.code)
pump(0.2)
armed = w.running
kbd.tap(w.hk.code)
pump(0.2)
ck.poll()
check("hidden arm hotkey still arms and disarms", armed and not w.running,
      f"armed={armed} warn={w.warn.text()[:60]!r}")
w.key_box.setChecked(False)
check("and never reaches the app", not ck.keys(w.hk.code))

# a tecla de armar igual à do macro: a aba avisa, e o aperto é do macro
check("no clash warning while the keys differ", w.macro_clash.isHidden()
      and not w.macro_clash.text())
arm = (w.hk.code, w.hk.name)
w.hk.set_key(K["F1"], "F1")
pump(0.05)
check("the Hotkeys tab warns when the arm hotkey is a macro's key",
      not w.macro_clash.isHidden()
      and "F1 is also the arm/disarm hotkey" in w.macro_clash.text(),
      w.macro_clash.text()[:60])
out.clear()
kbd.tap(K["F1"])
pump(0.3)
armed = w.running
kbd.tap(K["F1"])
pump(0.15)
out.poll()
check("then F1 runs the macro and the clicker stays off",
      not armed and not w.running and out.keys(K["E"], 1),
      f"armed={armed} taps={len(out.keys(K['E'], 1))}")
w.hk.set_key(*arm)
pump(0.05)
check("and the warning goes away with another arm hotkey",
      w.macro_clash.isHidden())

# tradução
w._set_language("pt_BR")
check("translated to Portuguese", "Atalhos" in w.tabs.tabText(2)
      and "liga e desliga" in w.macro_list.item(0).text()
      and w.m_import.text() == "Importar AHK…", w.tabs.tabText(2))
w._set_language("en_US")

# config salva e relida
w.cleanup()
cfg = json.load(open(a.CFG))
check("config keeps macros, the switch and the hidden hotkey",
      len(cfg.get("macros", [])) == 1 and cfg.get("macros_enabled") is True
      and cfg.get("hotkey_block") is True)
w2 = a.App()
w2.sound.setChecked(False)
check("a new start restores them", len(w2.macros) == 1
      and w2.macros[0]["period_ms"] == a.AHK_TICK_MS
      and w2.hk_block.isChecked()
      and w2.engine.active)
w2.cleanup()

# linha de comando
a.CFG = "/tmp/wayclick-test-cli.json"
if os.path.exists(a.CFG):
    os.remove(a.CFG)
a._other_instance = lambda: None
rc = a.cli_import(["--import", AHK])
cfg = json.load(open(a.CFG))
check("--import writes the macro to the config",
      rc == 0 and len(cfg["macros"]) == 1 and cfg["macros_enabled"])
rc = a.cli_import(["--import", AHK, "--dry-run"])
check("--dry-run changes nothing",
      rc == 0 and len(json.load(open(a.CFG))["macros"]) == 1)
os.remove(a.CFG)

kbd.close()
sys.exit(h.finish())
