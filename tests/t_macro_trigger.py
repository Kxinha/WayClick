"""O caso "segure X, repita Y a cada N ms", nas duas formas de gatilho.

1. gatilho no botao do mouse: segurar o esquerdo repete E, e o clique nao
   precisa estar ligado para isso;
2. gatilho numa tecla QUALQUER: segurar Q repete E. Antes o atalho global so
   aceitava 16 teclas fixas, entao esse arranjo nao existia.

O mouse e o teclado do gatilho sao falsos, criados via uinput, para nao
sequestrar os do usuario.
"""
import fcntl, os, struct, sys, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
a.SYSTEM_TRAY = False         # sem icone de bandeja nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

FAKE_MOUSE = b"wayclick-fake-physical-mouse"
FAKE_KB = b"wayclick-fake-trigger-keyboard"


def new_device(name, keys, rels=()):
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_KEY)
    for c in keys:
        fcntl.ioctl(fd, a.UI_SET_KEYBIT, c)
    if rels:
        fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_REL)
        for r in rels:
            fcntl.ioctl(fd, a.UI_SET_RELBIT, r)
    os.write(fd, struct.pack("80s", name) + struct.pack("HHHH", 3, 0xAAA, 0xBBB, 1)
             + struct.pack("i", 0) + b"\x00" * 1024)
    fcntl.ioctl(fd, a.UI_DEV_CREATE)
    return fd


mouse_fd = new_device(FAKE_MOUSE, a.BTN.values(), (0, 1, 8))
# teclado do gatilho: precisa passar por is_keyboard, que exige Esc ou uma das
# teclas de atalho conhecidas, entao declara Esc junto com o Q
kb_fd = new_device(FAKE_KB, (a.KEY_ESC, a.KEYS["Q"], a.KEYS["E"]))
time.sleep(0.6)

a.is_mouse = a.is_mouse_any = lambda f: FAKE_MOUSE in a.dev_name(f.fileno())


def send(fd, *events):
    buf = b"".join(struct.pack("llHHi", 0, 0, t, c, v) for t, c, v in events)
    os.write(fd, buf + struct.pack("llHHi", 0, 0, a.EV_SYN, 0, 0))


app = QApplication(sys.argv)
cat = QLabel("segure o gatilho, repete E")
cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#123;color:#fff;font-size:22px;")
got = {"E": 0}
cat.keyPressEvent = lambda e: (got.__setitem__("E", got["E"] + 1)
                               if e.key() == Qt.Key_E else None)
cat.showFullScreen()
cat.setFocus()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.click_box.setChecked(False)          # so a macro de teclado
w.key_box.setChecked(True)
row = w.key_rows[0]
row.catcher.set_key(a.KEYS["E"], "E")  # a tecla REPETIDA
row.interval.setValue(100.0)           # a cada 100 ms
row.mode.setCurrentText("Repeat")
w.delay.setValue(0); w.duration.setValue(0)

R = {}


def fase_mouse():
    """Gatilho: botao esquerdo do mouse."""
    w.mode.setCurrentText("Runs while the trigger is held")
    w.trigger.set_key(a.BTN["Left"])            # gatilho: botao esquerdo
    w.trig_boxes["key"].setChecked(True)      # a macro espera o gatilho
    w.set_running(True)
    base = dict(got)
    QTimer.singleShot(800, lambda: (
        R.__setitem__("mouse_idle", got["E"] - base["E"]),
        send(mouse_fd, (a.EV_KEY, a.BTN["Left"], 1)),
        QTimer.singleShot(1300, lambda: (
            R.__setitem__("mouse_held", got["E"] - base["E"] - R["mouse_idle"]),
            send(mouse_fd, (a.EV_KEY, a.BTN["Left"], 0)),
            QTimer.singleShot(700, fase_solto)))))


def fase_solto():
    base = dict(got)
    QTimer.singleShot(700, lambda: (
        R.__setitem__("mouse_after", got["E"] - base["E"]),
        w.set_running(False),
        QTimer.singleShot(500, fase_tecla)))


def fase_tecla():
    """Gatilho: a tecla Q, que a versao antiga nem oferecia."""
    w.trigger.set_key(a.KEYS["Q"], "Q")         # gatilho: a tecla Q
    R["hk_code"] = w.trigger.code
    w.mode.setCurrentText("Runs while the trigger is held")
    R["botao"] = w.btn.text()
    w.set_running(True)                         # arma; a tecla e que roda
    R["watcher_code"] = w.trig_watcher.keycode if w.trig_watcher else None
    base = dict(got)
    QTimer.singleShot(700, lambda: (
        R.__setitem__("key_idle", got["E"] - base["E"]),
        send(kb_fd, (a.EV_KEY, a.KEYS["Q"], 1)),
        QTimer.singleShot(1300, lambda: (
            R.__setitem__("key_held", got["E"] - base["E"] - R["key_idle"]),
            send(kb_fd, (a.EV_KEY, a.KEYS["Q"], 0)),
            QTimer.singleShot(800, fim)))))


def fim():
    base = dict(got)
    QTimer.singleShot(700, lambda: (
        R.__setitem__("key_after", got["E"] - base["E"]),
        R.__setitem__("running_after", w.running),
        w.set_running(False),
        QTimer.singleShot(400, app.quit)))


QTimer.singleShot(1000, fase_mouse)
QTimer.singleShot(22000, app.quit)
app.exec()

for fd in (mouse_fd, kb_fd):
    try:
        fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)
    except OSError:
        pass

print(f"gatilho BOTAO ESQUERDO -> repete E a cada 100 ms")
print(f"   armado, sem segurar ... {R.get('mouse_idle')}  (0)")
print(f"   segurando ............. {R.get('mouse_held')}  (~13)")
print(f"   depois de soltar ...... {R.get('mouse_after')}  (0)")
print(f"gatilho TECLA Q (codigo {R.get('hk_code')}, watcher {R.get('watcher_code')}, "
      f"botao diz {R.get('botao')!r})")
print(f"   armado, sem segurar ... {R.get('key_idle')}  (0)")
print(f"   segurando Q ........... {R.get('key_held')}  (~13)")
print(f"   depois de soltar ...... {R.get('key_after')}  (0)")
ok = (R.get("mouse_idle") == 0 and 8 <= (R.get("mouse_held") or 0) <= 18
      and (R.get("mouse_after") or 0) <= 1
      and R.get("hk_code") == a.KEYS["Q"]
      and R.get("watcher_code") == a.KEYS["Q"]
      and R.get("key_idle") == 0 and 8 <= (R.get("key_held") or 0) <= 18
      and R.get("botao") == "Arm"
      and (R.get("key_after") or 0) <= 1)
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
