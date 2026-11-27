"""Mandar tecla para várias janelas, offscreen e isolado (ver harness.py).

Três partes, sem KWin (o KWin de verdade fica no t_winsend_kwin.py):

* o hub segurando o teclado no foco emprestado: freeze() solta para o
  compositor o que estava apertado, guarda o que se digita no meio, e
  thaw() devolve na ordem, apertando de novo só o que continua apertado;
  sozinho depois do tempo, e contado (dois freeze pedem dois thaw);
* o WindowSender com um KWin de mentira: uma rodada só para as janelas que
  vencem juntas, um foco para a mesma janela em duas linhas, cada linha no
  intervalo dela, um alvo fechado não para os outros, a tecla só com o foco
  na janela e o foco de volta no fim, o teclado segurado só no meio;
* a aba: até 8 linhas, a última não sai, a config velha (uma tecla só) vira
  a primeira linha, a lista salva e relida, a janela guardada fechada fica
  escolhida (✕) e volta sozinha, o aviso do foco emprestado, o português.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen \\
        nice -n 19 dbus-run-session -- python3 -B tests/t_winsend_ui.py
"""
import json, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

a.CFG = "/tmp/wayclick-test-winsend-ui.json"
OLD = {"sound": False, "language": "en_US", "delay": 0,
       "click_enabled": False, "target_key": "E", "target_key_code": 18,
       "target_seconds": 7}
with open(a.CFG, "w") as fh:
    json.dump(OLD, fh)
app = QApplication(sys.argv)
K = a.KEYS
W, A, B, E, SPACE = K["W"], K["A"], K["B"], K["E"], K["Space"]


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def seq(rec):
    rec.poll()
    return [(c, v) for _t, c, v, _s in rec.keys() if v in (0, 1)]


# ============================================== o hub segura o teclado ==
kbd = h.fake_keyboard()
hub = a.HUB()
guard = a.KeyboardGuard()
hub.add(guard)
h.wait_grabbed(hub, [kbd])
ck = h.clone_of(hub, kbd)
check("the guard asks for keyboards only and blocks nothing",
      guard.wants_grab(hub.device(kbd.node))
      and not guard.on_key(None, A, 1))
check("keyboards(): one grabbed keyboard, nothing stuck",
      hub.keyboards() == (1, False), str(hub.keyboards()))

ck.clear()
kbd.key(W, True)
time.sleep(0.05)
n = hub.freeze()
time.sleep(0.05)
got = seq(ck)
check("freeze() holds the keyboard and lets go of W for the compositor at "
      "once", n == 1 and got == [(W, 1), (W, 0)], str(got))
ck.clear()
for c in (A, B):
    kbd.tap(c, 0.02)
time.sleep(0.1)
check("what is typed while frozen does not reach the compositor",
      seq(ck) == [], str(seq(ck)))
hub.thaw()
time.sleep(0.05)
got = seq(ck)
check("thaw() presses W again (still held) and then gives A and B in order",
      got == [(W, 1), (A, 1), (A, 0), (B, 1), (B, 0)], str(got))
ck.clear()
kbd.key(W, False)
time.sleep(0.05)
check("W lets go normally afterwards", seq(ck) == [(W, 0)], str(seq(ck)))

ck.clear()
kbd.key(E, True)
time.sleep(0.03)
hub.freeze()
time.sleep(0.03)
kbd.key(E, False)
kbd.tap(A, 0.02)
time.sleep(0.05)
hub.thaw()
time.sleep(0.05)
got = seq(ck)
check("a key let go during the freeze is not pressed twice (typing fast "
      "an E does not come out twice)",
      [x for x in got if x == (E, 1)] == [(E, 1)] and (A, 1) in got
      and got.index((E, 1)) < got.index((A, 1)), str(got))

ck.clear()
hub.freeze()
hub.freeze()
kbd.tap(B, 0.02)
time.sleep(0.05)
hub.thaw()
time.sleep(0.05)
mid = seq(ck)
hub.thaw()
time.sleep(0.05)
check("two freezes need two thaws", mid == [] and seq(ck) == [(B, 1), (B, 0)],
      f"{mid} {seq(ck)}")

ck.clear()
t0 = time.monotonic()
hub.freeze(0.3)
kbd.tap(A, 0.02)
time.sleep(0.15)
early = seq(ck)
time.sleep(0.35)
check("a freeze nobody thaws lets go by itself (the keyboard is never left "
      "mute)", early == [] and seq(ck) == [(A, 1), (A, 0)]
      and not hub._frozen, f"{early} {seq(ck)} {time.monotonic() - t0:.2f}")
hub.thaw()                      # sem efeito: já soltou
check("a late thaw() after the timeout does nothing", not hub._frozen)


# ======================================== o enviador, KWin de mentira ==
class FakeBridge:
    """O suficiente do KWin para o KWIN_FLASH_JS: quem está ativa, e o que
    cada pedido pediu. A tecla só "chega" se a janela dela estiver ativa."""
    ok = True

    def __init__(self, wins, listed=()):
        self.wins = dict(wins)          # id -> título
        self.listed = list(listed)      # o que windows() devolve
        self.active = "user"
        self.log = []
        self.lock = threading.Lock()

    def call(self, js, timeout=2.0, **fields):
        want = json.loads(fields["want"])
        with self.lock:
            self.log.append(want)
            out = {"active": self.active}
            if want.get("op") == "go":
                tid = want.get("id") or next(
                    (i for i, t in self.wins.items() if t == want.get("title")),
                    None)
                out["found"] = tid in self.wins
                if out["found"]:
                    self.active = tid
                    out.update(id=tid, title=self.wins[tid], now=True,
                               state={"id": tid, "moved": True, "min": False,
                                      "hid": False, "all": False, "desk": []})
            elif want.get("op") == "back":
                if want.get("expect") == self.active and want.get("id"):
                    self.active = want["id"]
                out["back"] = self.active == want.get("id")
            return json.dumps(out)

    def windows(self):
        return list(self.listed)

    def cleanup(self):
        pass


class FakeKeys:
    def __init__(self, bridge):
        self.bridge, self.taps = bridge, []

    def tap(self, code, hold=0.04):
        self.taps.append((code, self.bridge.active, time.monotonic(),
                          bool(hub._frozen)))
        time.sleep(hold)


br = FakeBridge({"w1": "Notes", "w2": "Game"})
fk = FakeKeys(br)
targets = [
    {"row": 0, "kind": "focus", "id": "w1", "cls": "kwrite",
     "title": "Notes", "code": A, "key": "A", "every": 0.25},
    {"row": 1, "kind": "focus", "id": "w2", "cls": "game", "title": "Game",
     "code": SPACE, "key": "Space", "every": 0.6},
    {"row": 2, "kind": "focus", "id": "w2", "cls": "game", "title": "Game",
     "code": E, "key": "E", "every": 0.6},
    {"row": 3, "kind": "focus", "id": "gone", "cls": "x", "title": "Closed",
     "code": B, "key": "B", "every": 0.6}]
s = a.WindowSender(targets, keyboard=fk, bridge=br, hub=hub)
s.start()
t0 = time.monotonic()
time.sleep(2.05)
s.stop()
s.join(3)
el = time.monotonic() - t0
hits = [t["hits"] for t in targets]
check("the sender thread stops", not s.is_alive())
check("each row sends at its own interval (0.25 s and 0.6 s over 2 s)",
      7 <= hits[0] <= 10 and 3 <= hits[1] <= 5 and hits[1] == hits[2],
      f"{hits} in {el:.2f} s")
check("every key arrived with the focus on its window, while the keyboard "
      "was held", fk.taps and all(
          (c == A and w == "w1") or (c in (SPACE, E) and w == "w2")
          for c, w, _t, _f in fk.taps) and all(f for *_x, f in fk.taps),
      str(fk.taps[:6]))
check("a closed window says so and does not stop the others",
      targets[3]["hits"] == 0 and targets[3]["error"] == "The window is closed."
      and not targets[0]["error"], targets[3]["error"])
gos = [w for w in br.log if w.get("op") == "go"]
backs = [w for w in br.log if w.get("op") == "back"]
check("the same window on two rows gets one focus a round, Space then E",
      not [1 for i in range(len(fk.taps) - 1)
           if fk.taps[i][0] == SPACE and fk.taps[i + 1][0] != E],
      str([c for c, *_ in fk.taps]))
check("one focus back a round, to the window that had it",
      len(backs) == s.rounds and all(b["id"] == "user" for b in backs)
      and br.active == "user", f"{len(backs)} {s.rounds} {br.active}")
first = [w for w in gos if w.get("stack")]
check("the stacking order is read once a round, on the first window",
      len(first) == s.rounds, f"{len(first)} {s.rounds}")
firstround = []
for x in br.log:
    if x.get("op") == "back":
        break
    firstround.append(x.get("id") or x.get("title"))
check("rows due together go in a single round (all four at the start, the "
      "Game rows in one focus)", firstround == ["w1", "w2", "gone"],
      str(firstround))
check("the keyboard is free again after the rounds", not hub._frozen)

# a pessoa clicou em outra janela no meio: o foco não é tomado de volta
br2 = FakeBridge({"w1": "Notes"})


class Clicker(FakeKeys):
    def tap(self, code, hold=0.04):
        super().tap(code, hold)
        self.bridge.active = "other"    # clicou noutra janela


ck2 = Clicker(br2)
t2 = [{"row": 0, "kind": "focus", "id": "w1", "cls": "kwrite",
       "title": "Notes", "code": A, "key": "A", "every": 5}]
s2 = a.WindowSender(t2, keyboard=ck2, bridge=br2, hub=hub)
s2.start()
time.sleep(0.4)
s2.stop()
s2.join(3)
check("a click on another window in the middle wins: the focus is not "
      "taken back", br2.active == "other" and t2[0]["hits"] == 1,
      br2.active)

# sem KWin: erro em cada linha, nada de teclado preso
dead = FakeBridge({})
dead.ok = False
t3 = [{"row": 0, "kind": "focus", "id": "w1", "cls": "c", "title": "T",
       "code": A, "key": "A", "every": 5}]
s3 = a.WindowSender(t3, keyboard=fk, bridge=dead, hub=hub)
s3.start()
time.sleep(0.2)
s3.stop()
s3.join(3)
check("without KWin each row says so and the keyboard is never held",
      t3[0]["error"] == "KWin is not answering." and not hub._frozen,
      t3[0]["error"])

# a vez de um enviador novo: espera o velho terminar e segue a conta
br4 = FakeBridge({"w1": "Notes"})
fk4 = FakeKeys(br4)
t4 = [{"row": 0, "kind": "focus", "id": "w1", "cls": "c", "title": "Notes",
       "code": A, "key": "A", "every": 0.2}]
s4 = a.WindowSender(t4, keyboard=fk4, bridge=br4, hub=hub)
s4.start()
time.sleep(0.3)
s4.stop()
t5 = [dict(t4[0], hits=t4[0]["hits"], next=t4[0]["next"])]
s5 = a.WindowSender(t5, keyboard=fk4, bridge=br4, hub=hub, after=s4)
s5.start()
time.sleep(0.5)
s5.stop()
s5.join(3)
overlap = [i for i in range(len(fk4.taps) - 1)
           if fk4.taps[i + 1][2] - fk4.taps[i][2] < 0.1]
check("a new sender (the list changed) waits for the old one and keeps "
      "counting", not s4.is_alive() and t5[0]["hits"] >= t4[0]["hits"] + 2
      and not overlap, f"{t4[0]['hits']} {t5[0]['hits']} {overlap}")

# ================================================================ a aba ==
hub.remove(guard)
w = a.App()
w.sound.setChecked(False)
pump(0.2)
r0 = w.win_rows[0]
check("the old config (one key, no window) becomes the first row",
      len(w.win_rows) == 1 and r0.catcher.code == 18
      and r0.catcher.name == "E" and abs(r0.every.value() - 7) < 1e-9,
      f"{r0.catcher.name} {r0.every.value()}")
check("the only row cannot be removed", not r0.minus.isEnabledTo(r0))
ICON = a.QIcon()
items = [
    (ICON, "⌨ Notes — kwrite", {"id": "k1", "cls": "kwrite",
                                "title": "notes.txt — KWrite", "xid": 77,
                                "where": "desk"}),
    (ICON, "◐ Game — Sober", {"id": "s1", "cls": "org.vinegarhq.Sober",
                              "title": "Sober", "xid": None, "where": "desk"}),
    (ICON, "◐ Game — Sober", {"id": "s2", "cls": "org.vinegarhq.Sober",
                              "title": "Sober (2)", "xid": None,
                              "where": "desk"}),
    (ICON, "▣ Dock: Game", {"id": "d1", "cls": "org.vinegarhq.Sober",
                            "title": "Sober", "xid": None, "where": "dock"})]
w._win_items = items
for _i in range(10):
    w._add_win_row()
check("up to 8 rows, then + Add window turns off",
      len(w.win_rows) == a.WIN_TARGETS_MAX
      and not w.win_add.isEnabledTo(w.win_add.parentWidget())
      and all(r.minus.isEnabledTo(r) for r in w.win_rows),
      str(len(w.win_rows)))
check("new rows start on windows not in the list yet",
      [(r.data() or {}).get("id") for r in w.win_rows[1:5]]
      == ["k1", "s1", "s2", "d1"],
      str([(r.data() or {}).get("id") for r in w.win_rows]))
while len(w.win_rows) > 3:
    w._remove_win_row(w.win_rows[-1])
r0, r1, r2 = w.win_rows
for r in w.win_rows:
    r.fill(items)
r0.win.setCurrentIndex(0)
r1.win.setCurrentIndex(2)
r2.win.setCurrentIndex(3)
r1.catcher.set_key(SPACE, "Space")
r1.every.setValue(0.5)
check("each row shows how its key arrives: ⌨ X11, ◐ focus, ▣ Dock",
      [r.mark for r in w.win_rows] == ["⌨", "◐", "▣"],
      str([r.mark for r in w.win_rows]))
check("the focus note shows up with a Wayland window in the list",
      w.win_warn.text().startswith("◐"), w.win_warn.text()[:40])
st = w._cfg_state()
check("the list is saved by app and title, with key and interval",
      st["targets"][1] == {"cls": "org.vinegarhq.Sober", "title": "Sober (2)",
                           "where": "desk", "key": "Space", "code": SPACE,
                           "every": 0.5}
      and st["targets"][2]["where"] == "dock"
      and st["target_key"] == "E" and st["target_seconds"] == 7,
      json.dumps(st["targets"])[:300])
tg = w._win_targets()
check("the sender gets one target per row, X11 with its keysym",
      [t["kind"] for t in tg] == ["x11", "focus", "dock"]
      and tg[0]["xid"] == 77 and tg[0]["keysym"] in ("e", "E")
      and tg[1]["every"] == 0.5, str([(t["kind"], t.get("keysym"))
                                      for t in tg]))

# relida: a janela fechada fica escolhida, e volta quando reabre
with open(a.CFG, "w") as fh:
    json.dump(dict(OLD, targets=st["targets"]), fh)
w2 = a.App()
w2.sound.setChecked(False)
pump(0.2)
check("the saved list comes back, three rows",
      [r.spec() for r in w2.win_rows] == st["targets"],
      json.dumps([r.spec() for r in w2.win_rows])[:300])
w2._add_win_row({"cls": "org.vinegarhq.Sober", "title": "Sober",
                 "key": "W", "code": W, "every": 3}, fill=False)
w2._win_items = items[:2]               # a "Sober (2)" fechou
w2._fill_win_rows()
rb, rs = w2.win_rows[1], w2.win_rows[3]
d = rb.win.currentData() or {}
check("a saved window that is closed stays picked, marked ✕, and the row "
      "of the other window of the same game keeps its own",
      d.get("gone") and rb.win.currentText().startswith("✕")
      and rb.data() is None and (rs.data() or {}).get("id") == "s1",
      f"{rb.win.currentText()} | {rs.win.currentText()}")
w2._win_items = items
w2._fill_win_rows()
check("and it comes back when the window is open again",
      (rb.data() or {}).get("id") == "s2", rb.win.currentText())
w2._remove_win_row(rs)
rb.want["title"] = "renamed"
w2._win_items = [items[0], items[2], items[3]]
w2._fill_win_rows()
check("a window whose title changed is found by its app when it is the "
      "only one", (rb.data() or {}).get("id") == "s2")
w2._win_items = items[:3]
w2._fill_win_rows()
check("a Dock window is not taken for the desktop one of the same app",
      w2.win_rows[2].data() is None)

# ligado com um KWin de mentira: as linhas mostram a conta
br6 = FakeBridge({"k1": "notes.txt — KWrite", "s2": "Sober (2)"},
                 [{"id": d["id"], "cls": d["cls"], "title": d["title"],
                   "pid": 1, "active": False} for _i, _l, d in items[:3]])
w2.bridge = br6
w2._win_items = items[:3]
for r in w2.win_rows:
    r.fill(items[:3])
w2._remove_win_row(w2.win_rows[2])
w2._remove_win_row(w2.win_rows[0])
w2.win_rows[0].every.setValue(0.3)
w2.target_box.setChecked(True)
w2.set_running(True)
pump(1.2)
rec = h.Recorder(w2.keyboard.node)
pump(1.0)
s6 = w2.win_sender
rec.poll()
spaces = rec.keys(SPACE, 1)
check("Start sends the key through the virtual keyboard with the focus "
      "borrowed", s6 is not None and len(spaces) >= 2
      and w2.win_guard is not None, f"{len(spaces)}")
check("the row and the status line show the count",
      w2.win_rows[0].state.text().startswith("×")
      and "Space ×" in w2.status.text(), w2.status.text())
w2.set_running(False)
pump(0.3)
check("Stop stops the sender and lets go of the keyboard guard",
      w2.win_sender is None and w2.win_guard is None
      and w2.win_rows[0].state.text() == "")

w2._set_language("pt_BR")
check("Portuguese", w2.win_add.text() == "+ Adicionar janela"
      and w2.win_rows[0].minus.toolTip() == "Tirar esta janela"
      and w2.win_warn.text().startswith("◐ Janelas Wayland"),
      w2.win_add.text())
w2._set_language("en_US")
for x in (w, w2):
    x.cleanup()
kbd.close()
sys.exit(h.finish())
