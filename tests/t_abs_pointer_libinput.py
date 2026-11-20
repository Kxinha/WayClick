"""O ponteiro absoluto visto pelo libinput, a biblioteca que o KWin usa.

O device é capturado pelo harness no instante em que nasce, e o libinput
deste teste o lê pelo mesmo descritor capturado: o compositor nunca recebe
um evento, e o cursor de verdade não se mexe. Confere o que o KWin faria com
ele (KWin 6.8, src/backends/libinput/connection.cpp: o movimento absoluto
vira posição global com libinput_event_pointer_get_absolute_x_transformed e
a largura da área de trabalho, que começa sempre em 0,0).

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_abs_pointer_libinput.py
"""
import ctypes, ctypes.util, math, os, select, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402

lib = ctypes.util.find_library("input")
if not lib:
    print("SKIP  needs libinput")
    sys.exit(0)
L = ctypes.CDLL(lib)
P = ctypes.c_void_p
OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, P)
CLOSE = ctypes.CFUNCTYPE(None, ctypes.c_int, P)


class Iface(ctypes.Structure):
    _fields_ = [("open_restricted", OPEN), ("close_restricted", CLOSE)]


for name, res, args in (
        ("libinput_path_create_context", P, [ctypes.POINTER(Iface), P]),
        ("libinput_path_add_device", P, [P, ctypes.c_char_p]),
        ("libinput_get_fd", ctypes.c_int, [P]),
        ("libinput_dispatch", ctypes.c_int, [P]),
        ("libinput_get_event", P, [P]),
        ("libinput_event_get_type", ctypes.c_int, [P]),
        ("libinput_event_get_pointer_event", P, [P]),
        ("libinput_event_pointer_get_absolute_x_transformed", ctypes.c_double,
         [P, ctypes.c_uint32]),
        ("libinput_event_pointer_get_absolute_y_transformed", ctypes.c_double,
         [P, ctypes.c_uint32]),
        ("libinput_event_pointer_get_button", ctypes.c_uint32, [P]),
        ("libinput_event_pointer_get_button_state", ctypes.c_int, [P]),
        ("libinput_event_destroy", None, [P]),
        ("libinput_device_has_capability", ctypes.c_int, [P, ctypes.c_int]),
        ("libinput_device_get_name", ctypes.c_char_p, [P]),
        ("libinput_unref", P, [P])):
    fn = getattr(L, name)
    fn.restype, fn.argtypes = res, args
DEVICE_ADDED, MOTION, MOTION_ABS, BUTTON = 1, 400, 401, 402
CAP = {"keyboard": 0, "pointer": 1, "touch": 2, "tablet tool": 3,
       "tablet pad": 4, "gesture": 5, "switch": 6}

ptr = a.AbsolutePointer()
grabbed = h.READERS.get(ptr.node)
check("the pointer is captured by the test, the compositor gets nothing",
      grabbed is not None)


def udev_props(node):
    out = subprocess.run(["udevadm", "info", "--query=property",
                          f"--name={node}"], capture_output=True, text=True)
    return dict(line.split("=", 1) for line in out.stdout.splitlines()
                if "=" in line)


props = {}
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    props = udev_props(ptr.node)
    if props.get("ID_INPUT_MOUSE") == "1":
        break
    time.sleep(0.05)
kinds = {k for k in ("ID_INPUT_MOUSE", "ID_INPUT_TABLET",
                     "ID_INPUT_TOUCHSCREEN", "ID_INPUT_TOUCHPAD",
                     "ID_INPUT_JOYSTICK", "ID_INPUT_KEYBOARD")
         if props.get(k) == "1"}
check("udev calls it a mouse, and nothing else (the VM absolute mouse "
      "path)", kinds == {"ID_INPUT_MOUSE"}, str(sorted(kinds)))

# o libinput abre o device pelo descritor já capturado: o mesmo arquivo
# aberto, então a captura vale para ele também
_callbacks = (OPEN(lambda path, flags, data: os.dup(grabbed)),
              CLOSE(lambda fd, data: os.close(fd)))     # vivos até o fim
iface = Iface(*_callbacks)
ctx = L.libinput_path_create_context(ctypes.byref(iface), None)
dev = L.libinput_path_add_device(ctx, ptr.node.encode())
lfd = L.libinput_get_fd(ctx)


def events(wait=0.3):
    """(tipo, dados) que o libinput entregou."""
    out = []
    end = time.monotonic() + wait
    first = True
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        # o que já está na fila (DEVICE_ADDED, por exemplo) não acorda o fd
        if not first and not select.select([lfd], [], [], left)[0]:
            break
        first = False
        L.libinput_dispatch(ctx)
        while True:
            ev = L.libinput_get_event(ctx)
            if not ev:
                break
            t = L.libinput_event_get_type(ev)
            pe = L.libinput_event_get_pointer_event(ev) if t in (
                MOTION, MOTION_ABS, BUTTON) else None
            out.append((t, pe, ev))
        if out and out[-1][0] in (MOTION_ABS, BUTTON):
            end = min(end, time.monotonic() + 0.03)
    return out


def positions(evs, size):
    res = []
    for t, pe, ev in evs:
        if t == MOTION_ABS:
            res.append((L.libinput_event_pointer_get_absolute_x_transformed(
                pe, size[0]),
                L.libinput_event_pointer_get_absolute_y_transformed(
                    pe, size[1])))
    return res


def free(evs):
    for _t, _pe, ev in evs:
        L.libinput_event_destroy(ev)


evs = events()
caps = {k for k, v in CAP.items()
        if dev and L.libinput_device_has_capability(dev, v)}
check("libinput takes it as a pointer, not a tablet or touchscreen",
      dev and any(t == DEVICE_ADDED for t, _p, _e in evs)
      and caps == {"pointer"}, f"{caps}, "
      f"{dev and L.libinput_device_get_name(dev).decode()}")
free(evs)

layouts = {"three 1920x1080 screens": (0, 0, 5760, 1080),
           "2560x1440 at 100% next to a 4K at 150%": (0, 0, 5120, 1440),
           "one 1366x768 screen": (0, 0, 1366, 768)}
for label, ws in layouts.items():
    W, H = ws[2], ws[3]
    targets = [(0, 0), (W - 1, H - 1), (W // 2, H // 3), (1234 % W, 567 % H),
               (W - 1, 0), (0, H - 1)]
    bad = []
    for x, y in targets:
        ptr.warp(x, y, ws)
        evs = events()
        got = positions(evs, (W, H))
        free(evs)
        if not got or (math.floor(got[-1][0]), math.floor(got[-1][1])) \
                != (x, y):
            bad.append(((x, y), got[-1:] if got else None))
    check(f"every spot lands on its exact pixel: {label}", not bad, str(bad))

ws = layouts["three 1920x1080 screens"]
ptr.warp(4000, 900, ws)
free(events())
ptr.warp(4000, 900, ws)
evs = events()
got = positions(evs, ws[2:])
free(evs)
check("the same spot twice still moves the cursor there (after the person "
      "moved the mouse, for example)",
      got and (math.floor(got[-1][0]), math.floor(got[-1][1])) == (4000, 900)
      and all(math.floor(x) == 4000 for x, _y in got), str(got))

ptr.button(a.BTN["Left"], True)
ptr.button(a.BTN["Left"], False)
ptr.button(a.BTN["Right"], True)
ptr.button(a.BTN["Right"], False)
evs = events()
btns = [(L.libinput_event_pointer_get_button(pe),
         L.libinput_event_pointer_get_button_state(pe))
        for t, pe, _e in evs if t == BUTTON]
free(evs)
check("its buttons are mouse buttons",
      btns == [(a.BTN["Left"], 1), (a.BTN["Left"], 0),
               (a.BTN["Right"], 1), (a.BTN["Right"], 0)], str(btns))

L.libinput_unref(ctx)
ptr.close()
sys.exit(h.finish())
