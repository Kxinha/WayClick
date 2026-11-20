"""Macros com atalho, no kernel, sem tocar no desktop (ver harness.py).

Roda o SpamE F1.ahk do jeito que o AutoHotkey rodaria no Windows: F1 liga, F1
desliga, E a cada 15,6 ms (o tique do Windows), e o F1 nunca chega ao
programa; editado para 1 ms, o mesmo macro roda a 1 kHz. Depois os outros
modos: enquanto segura (botão lateral), uma vez por aperto com Ctrl, atalho
que deixa a tecla passar (~), sempre ligado, remapeamento, o relay do hub e a
tecla de armar o clique quando um macro usa a mesma.

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_hotkey_macros.py
"""
import os, statistics, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
app = QCoreApplication(sys.argv)
K = a.KEYS
F1, F2, F3, F6 = K["F1"], K["F2"], K["F3"], K["F6"]
E, X, J, CTRL = K["E"], K["X"], K["J"], K["Left Ctrl"]
SIDE, LEFT = a.BTN["Side"], a.BTN["Left"]


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


kbd, mouse = h.fake_keyboard(), h.fake_mouse()
hub = a.HUB()
hub.add(h.KeepMice())
h.wait_grabbed(hub, [kbd, mouse])
ck, cm = h.clone_of(hub, kbd), h.clone_of(hub, mouse)
vk, vm = a.VirtualKeyboard(), a.VirtualMouse()
ok_, om = h.Recorder(vk.node), h.Recorder(vm.node)
engine = a.MacroEngine(lambda: (vk, vm), hub=hub)
check("every device the app created was captured by the test",
      {vk.node, vm.node, hub.device(kbd.node).out.node} <= set(h.READERS))

# ---------------------------------------------------------------- relay --
lat = []
for _ in range(20):
    ck.clear()
    t0 = time.time()
    kbd.key(X, True)
    ck.poll(0.05)
    downs = ck.keys(X, 1)
    if downs:
        lat.append((downs[0][3] - t0) * 1000)
    kbd.key(X, False)
    ck.poll(0.02)
check("keys are relayed through the clone", len(lat) == 20,
      f"{len(lat)}/20")
if lat:
    check("relay latency is under 2 ms", statistics.median(lat) < 2.0,
          f"median {statistics.median(lat):.3f} ms, max {max(lat):.3f} ms")

# --------------------------------------------- o script do usuário, F1 --
imp = a.import_ahk_file(os.path.join(HERE, "ahk", "SpamE F1.ahk"))
check("SpamE F1.ahk becomes one macro", len(imp.macros) == 1,
      a.macro_summary(imp.macros[0]) if imp.macros else "none")
m = imp.macros[0]
check("F1, toggle, every 15.6 ms like AutoHotkey on Windows, tap E, F1 hidden",
      (m["code"], m["mode"], m["repeat"], m["period_ms"], m["block"],
       [(s["op"], s["code"]) for s in m["steps"]])
      == (F1, "toggle", True, a.AHK_TICK_MS, True, [("tap", E)]))
engine.configure(imp.macros, True)
pump(0.1)
ok_.clear()
kbd.tap(F1)
t_on = time.time()
pump(0.6)
kbd.tap(F1)
t_off = time.time()
pump(0.1)
ok_.poll()
downs = ok_.keys(E, 1)
gaps = [(b[3] - a_[3]) * 1000 for a_, b in zip(downs, downs[1:])]
check("imported, it taps E 64 times a second, like the script on Windows",
      55 <= len(downs) / (t_off - t_on) <= 70 and gaps
      and 15.3 <= statistics.median(gaps) <= 15.9,
      f"{len(downs)} taps, median gap "
      f"{statistics.median(gaps) if gaps else 0:.2f} ms")
pump(0.1)

# editado para 1 ms no macro, roda a 1 ms mesmo
fast = a.normalize_macro(dict(m, period_ms=1.0))
engine.configure([fast], True)
pump(0.1)
ck.clear(); ok_.clear()
kbd.tap(F1)
t_on = time.time()
pump(0.3)
busy = []                          # digitar com o macro rodando
for _ in range(10):
    t0 = time.time()
    kbd.key(X, True)
    ck.poll(0.02)
    d = [e for e in ck.keys(X, 1) if e[3] >= t0]
    if d:
        busy.append((d[0][3] - t0) * 1000)
    kbd.key(X, False)
    pump(0.02)
pump(0.3)
kbd.tap(F1)
t_off = time.time()
pump(0.1)
ok_.poll(); ck.poll()
downs = [e for e in ok_.keys(E, 1)]
rate = len(downs) / (t_off - t_on)
gaps = [(b[3] - a_[3]) * 1000 for a_, b in zip(downs, downs[1:])]
check("F1 toggles E on and off", 800 <= rate <= 1100,
      f"{len(downs)} taps in {t_off - t_on:.2f} s = {rate:.0f}/s")
if gaps:
    check("taps are 1 ms apart", 0.9 <= statistics.median(gaps) <= 1.1,
          f"median {statistics.median(gaps):.3f} ms")
check("F1 never reaches the app", not ck.keys(F1))
check("other keys still reach the app while it runs, without lag",
      len(busy) == 10 and statistics.median(busy) < 2.0,
      f"median {statistics.median(busy):.3f} ms, max {max(busy):.2f} ms"
      if busy else "none")
evs = ok_.keys(E)
check("E is released when it stops", evs and evs[-1][2] == 0)
ok_.clear()
pump(0.1)
check("nothing more after stopping", not ok_.poll(0.05))
check("no macro left running", not engine.running())

# ------------------------------------------ enquanto segura, no mouse --
hold = a.normalize_macro({"code": SIDE, "mode": "hold", "repeat": True,
                          "period_ms": 20, "steps": [{"op": "tap",
                                                      "code": E}]})
engine.configure([hold], True)
pump(0.1)
cm.clear(); ok_.clear()
mouse.key(SIDE, True)
pump(0.5)
mouse.move(5)
mouse.key(SIDE, False)
t_up = time.time()
pump(0.1)
ok_.poll(); cm.poll()
n = len(ok_.keys(E, 1))
check("side button held: E repeats every 20 ms", 22 <= n <= 27, f"{n} taps")
last = ok_.keys(E)[-1] if ok_.keys(E) else None
check("stops within one period of letting go",
      last and last[2] == 0 and last[3] - t_up < 0.025,
      f"{(last[3] - t_up) * 1000:.1f} ms" if last else "")
check("the side button is hidden, motion still passes",
      not cm.keys(SIDE) and any(e[0] == 2 and e[2] == 5 for e in cm.events))

# --------------------------------------- uma vez, com Ctrl, digitando --
typ = a.normalize_macro({"code": J, "mods": ["ctrl"], "mode": "once",
                         "steps": [{"op": "text", "text": "hi"}]})
engine.configure([typ], True)
pump(0.1)
ck.clear(); ok_.clear()
kbd.key(CTRL, True)
pump(0.03)
kbd.tap(J)
pump(0.2)
kbd.key(CTRL, False)
pump(0.05)
ck.poll(); ok_.poll()
typed = [e[1] for e in ok_.keys(value=1)]
check("Ctrl+J types h, i", typed == [K["H"], K["I"]], str(typed))
ctrl = [e[2] for e in ck.keys(CTRL)]
check("Ctrl is let go before typing, like AutoHotkey, and only once",
      ctrl == [1, 0], str(ctrl))
check("J never reaches the app", not ck.keys(J))

# --------------------------------------------- o mais específico vence --
plain = a.normalize_macro({"code": F2, "mode": "once",
                           "steps": [{"op": "tap", "code": K["1"]}]})
ctrl2 = a.normalize_macro({"code": F2, "mods": ["ctrl"], "mode": "once",
                           "steps": [{"op": "tap", "code": K["2"]}]})
engine.configure([plain, ctrl2], True)
pump(0.1)
ok_.clear()
kbd.tap(F2)
pump(0.1)
kbd.key(CTRL, True)
pump(0.02)
kbd.tap(F2)
pump(0.1)
kbd.key(CTRL, False)
pump(0.05)
got = [e[1] for e in ok_.poll() if e[0] == 1 and e[2] == 1]
check("F2 runs the plain one, Ctrl+F2 only the Ctrl one",
      got == [K["1"], K["2"]], str(got))

# ------------------------------------------------- ~ deixa a tecla passar --
tilde = a.normalize_macro({"code": F3, "mode": "once", "block": False,
                           "steps": [{"op": "tap", "code": K["3"]}]})
engine.configure([tilde], True)
pump(0.1)
ck.clear(); ok_.clear()
kbd.tap(F3)
pump(0.1)
ck.poll(); ok_.poll()
check("a pass-through hotkey reaches the app and runs",
      len(ck.keys(F3)) == 2 and len(ok_.keys(K["3"], 1)) == 1)

# ---------------------------------------------------------- sempre ligado --
always = a.normalize_macro({"mode": "always", "period_ms": 50,
                            "steps": [{"op": "tap", "code": K["5"]}]})
ok_.clear()
engine.configure([always], True)
pump(0.32)
engine.configure([always], False)
pump(0.05)
ok_.poll()
n = len(ok_.keys(K["5"], 1))
check("an Always macro runs while enabled", 6 <= n <= 8, f"{n} taps")
ok_.clear()
pump(0.15)
check("and stops when disabled", not ok_.poll())

# ------------------------------------------------------- remapeamento --
remap = a.AhkImport("CapsLock::Ctrl\n").macros
engine.configure(remap, True)
pump(0.1)
ck.clear(); ok_.clear()
kbd.key(K["Caps Lock"], True)
pump(0.1)
mid = [e[2] for e in ok_.poll() if e[1] == CTRL]
kbd.key(K["Caps Lock"], False)
pump(0.05)
after = [e[2] for e in ok_.poll() if e[1] == CTRL]
ck.poll()
check("CapsLock::Ctrl holds Ctrl while CapsLock is held",
      mid == [1] and after == [1, 0] and not ck.keys(K["Caps Lock"]),
      f"{mid} {after}")

# --------------------------------------------- atalho de armar escondido --
seen = []
w = a.HotkeyWatcher(F6, block=True, hub=hub)
w.pressed.connect(lambda: seen.append("p"))
w.released.connect(lambda: seen.append("r"))
engine.configure([], False)
w.start()
pump(0.1)
ck.clear()
kbd.tap(F6)
pump(0.1)
ck.poll()
check("arm hotkey fires and is hidden",
      seen == ["p", "r"] and not ck.keys(F6), str(seen))
w.set_block(False)
pump(0.05)
ck.clear()
kbd.tap(F6)
pump(0.1)
ck.poll()
check("and passes again when not hidden", len(ck.keys(F6)) == 2)

# ------------------------- macro na tecla de armar: o aperto é do macro --
# um .ahk com F8 (a tecla de armar de fábrica) armava o clique junto com o
# macro, e o clique saía sozinho no jogo
w.shadow = engine.claims
same = a.normalize_macro({"code": F6, "mode": "once",
                          "steps": [{"op": "tap", "code": X}]})
engine.configure([same], True)
pump(0.1)
seen.clear(); ok_.clear(); ck.clear()
kbd.tap(F6)
pump(0.1)
ok_.poll(); ck.poll()
check("a macro on the arm hotkey runs, and the clicker does not arm",
      seen == [] and len(ok_.keys(X, 1)) == 1 and not ck.keys(F6),
      f"arm {seen}, macro taps {len(ok_.keys(X, 1))}")
engine.configure([same], False)
pump(0.1)
seen.clear()
kbd.tap(F6)
pump(0.1)
check("with hotkey macros off, the key arms the clicker again",
      seen == ["p", "r"], str(seen))
held = a.normalize_macro({"code": F6, "mods": ["ctrl"], "mode": "once",
                          "steps": [{"op": "tap", "code": X}]})
engine.configure([held], True)
pump(0.1)
seen.clear(); ok_.clear()
kbd.tap(F6)
pump(0.1)
kbd.key(CTRL, True)
pump(0.02)
kbd.tap(F6)
pump(0.1)
kbd.key(CTRL, False)
pump(0.05)
ok_.poll()
check("a Ctrl+F6 macro takes Ctrl+F6 and leaves plain F6 to the clicker",
      seen == ["p", "r"] and len(ok_.keys(X, 1)) == 1,
      f"arm {seen}, macro taps {len(ok_.keys(X, 1))}")
engine.configure([], False)
w.stop()

# --------------------------------- MouseHold pelo hub (modo segurar) --
pressed = []
holder = a.MouseHold(vm, LEFT, swallow=True, hub=hub)
holder.pressed.connect(lambda: pressed.append(1))
check("hold mode grabs the mouse at once", holder.start())
om.clear()
mouse.key(LEFT, True)
mouse.move(3)
pump(0.05)
mouse.key(LEFT, False)
pump(0.05)
om.poll()
check("hold mode sees the trigger and swallows it",
      pressed and not om.keys(LEFT))
check("and relays motion through the app's mouse",
      any(e[0] == 2 and e[2] == 3 for e in om.events))
holder.stop()

engine.close()
hub.stop()
vk.close(); vm.close()
kbd.close(); mouse.close()
sys.exit(h.finish())
