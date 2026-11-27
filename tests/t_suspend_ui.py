"""O Suspend do AutoHotkey na aba Atalhos, offscreen e isolado (ver
harness.py).

Importa o bhop_f7.ahk (F7::Suspend e segurar Espaço aperta F9) pela mesma
função do botão Importar AHK, e num teclado falso: segurar Espaço manda F9 a
cada 15,6 ms e o Espaço some; F7 desliga os macros e não chega ao programa;
desligado, o Espaço passa normal; F7 de novo liga. Depois o Suspend com Ctrl,
o aviso de tecla repetida, o botão de limpar, a config salva e relida, a
tradução e o --import da linha de comando.

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_suspend_ui.py
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AHK = os.path.join(HERE, "ahk", "bhop_f7.ahk")
a.CFG = "/tmp/wayclick-test-suspend-ui.json"
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US", "delay": 0,
               "click_enabled": False}, fh)
app = QApplication(sys.argv)
K = a.KEYS
F7, F9, SPACE, CTRL = K["F7"], K["F9"], K["Space"], K["Left Ctrl"]


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
check("no Suspend hotkey at first", w.suspend_key.code == 0
      and w.suspend_key.text() == "None" and not w.suspend_w._on
      and not w.suspend_clear.isEnabled())

n = w.import_ahk_path(AHK, ask=False)
check("importing bhop_f7.ahk adds the Space macro and sets F7 as Suspend",
      n == 1 and w.suspend_key.code == F7 and w.suspend_key.text() == "F7"
      and w.suspend_w._on and w.suspend_clear.isEnabled()
      and w.macro_box.isChecked(), w.suspend_key.text())
out = h.Recorder(w.keyboard.node)


def hold_space(seconds):
    out.clear()
    ck.clear()
    kbd.key(SPACE, True)
    pump(seconds)
    kbd.key(SPACE, False)
    pump(0.1)
    out.poll()
    ck.poll()
    return len(out.keys(F9, 1))


taps = hold_space(0.5)
f9 = [e[3] for e in out.keys(F9, 1)]
gaps = sorted((b - a_) * 1000 for a_, b in zip(f9, f9[1:]))
mid = gaps[len(gaps) // 2] if gaps else 0
check("holding Space presses F9 for 15.6 ms and waits 15.6 ms, about 32 a "
      "second like the Linux bhop script", 13 <= taps <= 17
      and 29.0 <= mid <= 34.0, f"{taps} in 0.5 s, median {mid:.1f} ms")
check("and Space is hidden from the app", not ck.keys(SPACE))

ck.clear()
kbd.tap(F7)
pump(0.15)
ck.poll()
check("F7 turns the hotkey macros off", not w.macro_box.isChecked()
      and not w.engine.active)
check("and F7 never reaches the app", not ck.keys(F7))
taps = hold_space(0.3)
check("off, Space passes like a normal key and no F9 goes out",
      taps == 0 and len(ck.keys(SPACE, 1)) == 1
      and len(ck.keys(SPACE, 0)) == 1, f"{taps} F9, {ck.keys(SPACE)}")

ck.clear()
kbd.tap(F7)
pump(0.15)
ck.poll()
check("F7 again turns them back on", w.macro_box.isChecked()
      and w.engine.active and not ck.keys(F7))
check("and the bhop works again", hold_space(0.3) >= 7)

# desligar no meio: o F9 para na hora e nada fica apertado
out.clear()
kbd.key(SPACE, True)
pump(0.25)
kbd.tap(F7)
pump(0.1)
out.poll()
before = len(out.keys(F9, 1))
pump(0.25)
out.poll()
after = len(out.keys(F9, 1))
kbd.key(SPACE, False)
pump(0.1)
out.poll()
f9 = out.keys(F9)
check("F7 while holding Space stops the F9 at once, nothing left pressed",
      before >= 4 and after == before and f9 and f9[-1][2] == 0,
      f"{before} then {after}")
kbd.tap(F7)
pump(0.15)

# com Ctrl: só Ctrl+F7 vale
w.set_suspend(F7, ["ctrl"])
pump(0.05)
check("Ctrl+F7 shows in the field", w.suspend_key.text() == "Ctrl+F7")
ck.clear()
kbd.tap(F7)
pump(0.15)
ck.poll()
check("plain F7 then passes to the app and switches nothing",
      w.macro_box.isChecked() and len(ck.keys(F7, 1)) == 1)
kbd.key(CTRL, True)
kbd.tap(F7)
kbd.key(CTRL, False)
pump(0.15)
check("Ctrl+F7 switches them", not w.macro_box.isChecked())
kbd.key(CTRL, True)
kbd.tap(F7)
kbd.key(CTRL, False)
pump(0.15)
check("and back on", w.macro_box.isChecked())

# avisos
w.set_suspend(SPACE)
pump(0.05)
check("a Suspend key that is also a macro's key gets a warning",
      not w.macro_clash.isHidden()
      and "Space is the Suspend hotkey" in w.macro_clash.text(),
      w.macro_clash.text())
check("and the macro does not run by it", not w.engine.claims(SPACE))
w.set_suspend(w.hk.code)
pump(0.05)
check("the arm/disarm hotkey as Suspend gets a warning too",
      "also the arm/disarm hotkey" in w.macro_clash.text(),
      w.macro_clash.text())
w.set_suspend(F7)
pump(0.05)
check("the warnings go away with another key", w.macro_clash.isHidden())

# limpar
w.suspend_clear.click()
pump(0.05)
check("the × button removes the Suspend hotkey",
      w.suspend_key.code == 0 and w.suspend_key.text() == "None"
      and not w.suspend_w._on)
ck.clear()
kbd.tap(F7)
pump(0.15)
ck.poll()
check("then F7 is a normal key again", w.macro_box.isChecked()
      and len(ck.keys(F7, 1)) == 1)
w.set_suspend(F7)

# tradução
w._set_language("pt_BR")
check("translated to Portuguese",
      any(src == "Suspend hotkey:" and lbl.text() == "Atalho de Suspend:"
          for lbl, src in w._labels)
      and "AutoHotkey" in w.suspend_key.toolTip(), w.suspend_key.toolTip())
w.set_suspend(0)
check("and None in Portuguese", w.suspend_key.text() == "Nenhum")
w._set_language("en_US")
w.set_suspend(F7, ["ctrl"])

# config salva e relida
w.cleanup()
cfg = json.load(open(a.CFG))
check("config keeps the Suspend hotkey",
      (cfg.get("suspend_code"), cfg.get("suspend_key"),
       cfg.get("suspend_mods")) == (F7, "F7", ["ctrl"]), str(cfg)[:200])
w2 = a.App()
w2.sound.setChecked(False)
check("a new start restores it", w2.suspend_key.code == F7
      and w2.suspend_key.text() == "Ctrl+F7" and w2.suspend_w._on
      and w2.suspend_w.mods == frozenset({"ctrl"}))
w2.cleanup()

# linha de comando
a.CFG = "/tmp/wayclick-test-suspend-cli.json"
if os.path.exists(a.CFG):
    os.remove(a.CFG)
a._other_instance = lambda: None
rc = a.cli_import(["--import", AHK, "--dry-run"])
check("--dry-run writes nothing", rc == 0 and not os.path.exists(a.CFG))
rc = a.cli_import(["--import", AHK])
cfg = json.load(open(a.CFG))
check("--import writes the macro and the Suspend hotkey",
      rc == 0 and len(cfg["macros"]) == 1 and cfg["suspend_code"] == F7
      and cfg["suspend_mods"] == [])
os.remove(a.CFG)

kbd.close()
sys.exit(h.finish())
