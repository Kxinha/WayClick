"""Ambiente isolado para testar o que mexe com teclado e mouse de verdade.

Nada do que estes testes fazem chega ao desktop de quem os roda, mesmo com a
sessão gráfica aberta e um jogo em primeiro plano:

* o WayClick só enxerga devices cujo nome contém "wctest" (DEVICE_FILTER):
  teclado e mouse reais nunca são abertos;
* os devices falsos que o teste cria (wctest-kbd, wctest-mouse) são capturados
  pelo próprio hub do WayClick antes do primeiro evento (GRAB_EVERYTHING), e o
  teste espera essa captura antes de apertar qualquer coisa;
* todo device virtual que o WayClick cria (clone de relay, teclado e mouse
  virtuais) é capturado aqui no instante em que nasce (NEW_NODE_HOOK). É dessa
  captura que o teste lê o que o WayClick mandaria ao compositor.

Precisa de acesso a /dev/uinput e /dev/input (grupo input), e de nada mais:
sem root, sem regra de udev. Rode com QT_QPA_PLATFORM=offscreen.
"""
import fcntl, os, select, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WAYCLICK_DEVICE_FILTER"] = "wctest"
import wayclick as a                                        # noqa: E402

a.DEVICE_FILTER = b"wctest"
a.GRAB_EVERYTHING = True
a.AUTO_UPDATE_CHECK = False
a.SYSTEM_TRAY = False

EV_KEY, EV_REL, EV_SYN = 1, 2, 0
FMT, SIZE = a.EVENT_FMT, a.EVENT_SIZE
READERS = {}            # node -> fd capturado de um device do WayClick


def _hook(node):
    deadline = time.monotonic() + 3.0
    while True:
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            break
        except OSError:
            # o devtmpfs cria o nó já, mas só root lê até o udev ajustar o
            # grupo; o libinput só abre depois do udev, então chegamos antes
            if time.monotonic() > deadline:
                raise
            time.sleep(0.0005)
    fcntl.ioctl(fd, a.EVIOCGRAB, 1)
    READERS[node] = fd


a.NEW_NODE_HOOK = _hook


class Fake:
    """Device falso no kernel. Só emite depois de capturado pelo hub."""

    def __init__(self, name, keys=(), rels=()):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        if keys:
            fcntl.ioctl(self.fd, a.UI_SET_EVBIT, EV_KEY)
            for c in keys:
                fcntl.ioctl(self.fd, a.UI_SET_KEYBIT, c)
        if rels:
            fcntl.ioctl(self.fd, a.UI_SET_EVBIT, EV_REL)
            for c in rels:
                fcntl.ioctl(self.fd, a.UI_SET_RELBIT, c)
        os.write(self.fd, struct.pack("80s", name.encode())
                 + struct.pack("HHHH", 0x03, 0x7777, 0x0001, 1)
                 + struct.pack("i", 0) + b"\x00" * (4 * 64 * 4))
        fcntl.ioctl(self.fd, a.UI_DEV_CREATE)
        self.node = a.uinput_node(self.fd)
        deadline = time.monotonic() + 3.0
        while not os.access(self.node, os.R_OK | os.W_OK):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{self.node} never became readable")
            time.sleep(0.005)

    def ev(self, etype, code, value):
        os.write(self.fd, struct.pack(FMT, 0, 0, etype, code, value)
                 + struct.pack(FMT, 0, 0, EV_SYN, 0, 0))

    def key(self, code, down):
        self.ev(EV_KEY, code, 1 if down else 0)

    def tap(self, code, hold=0.03):
        self.key(code, True)
        time.sleep(hold)
        self.key(code, False)

    def move(self, dx):
        self.ev(EV_REL, 0, dx)

    def close(self):
        try:
            fcntl.ioctl(self.fd, a.UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


def fake_keyboard():
    return Fake("wctest-kbd", keys=[c for c in range(1, 249)
                                    if c not in a.KB_SKIP])


def fake_mouse():
    return Fake("wctest-mouse", keys=range(0x110, 0x115), rels=(0, 1, 8))


class KeepMice:
    """Inscrito que só pede os mouses: com GRAB_EVERYTHING, o mouse falso fica
    capturado o tempo todo, e nenhum evento dele chega ao desktop."""
    mice = "all"


def wait_grabbed(hub, fakes, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hub.scan()
        ok = True
        for f in fakes:
            d = hub.device(f.node)
            if d is None or not d.grabbed:
                ok = False
        if ok:
            return True
        time.sleep(0.05)
    raise RuntimeError("the hub never grabbed the fake devices")


def drain(fd):
    """Eventos (tipo, código, valor, instante) já disponíveis num reader."""
    out = []
    while True:
        try:
            data = os.read(fd, 24 * 512)
        except BlockingIOError:
            return out
        for i in range(0, len(data) - SIZE + 1, SIZE):
            sec, usec, t, c, v = struct.unpack_from(FMT, data, i)
            if t != EV_SYN:
                out.append((t, c, v, sec + usec / 1e6))


_READER = r"""
import os, select, sys
fd, out = int(sys.argv[1]), open(sys.argv[2], "ab", buffering=0)
while True:
    try:
        data = os.read(fd, 24 * 512)
    except BlockingIOError:
        select.select([fd], [], [])
        continue
    except OSError:
        break
    out.write(data)
"""


class Recorder:
    """Guarda tudo o que sai de um device do WayClick.

    Quem lê é um processo à parte, com o fd herdado: o buffer do evdev cabe
    poucas dezenas de eventos, e um macro de 1 ms o enche em 10 ms. Lendo
    daqui, um atraso qualquer do teste viraria SYN_DROPPED e eventos perdidos.
    """
    _procs = []

    def __init__(self, node):
        import subprocess, tempfile
        self.path = tempfile.mktemp(prefix="wctest-", suffix=".ev")
        open(self.path, "wb").close()
        fd = READERS[node]
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", _READER,
                                      str(fd), self.path], pass_fds=[fd])
        Recorder._procs.append(self)
        self.pos = 0
        self.events = []

    def poll(self, seconds=0.0):
        if seconds:
            time.sleep(seconds)
        time.sleep(0.01)              # o leitor alcança o que acabou de sair
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        usable = len(data) - len(data) % SIZE
        self.pos += usable
        for i in range(0, usable, SIZE):
            sec, usec, t, c, v = struct.unpack_from(FMT, data, i)
            if t != EV_SYN:
                self.events.append((t, c, v, sec + usec / 1e6))
        return self.events

    def keys(self, code=None, value=None):
        return [e for e in self.events if e[0] == EV_KEY
                and (code is None or e[1] == code)
                and (value is None or e[2] == value)]

    def clear(self):
        self.poll()
        self.events = []

    @classmethod
    def stop_all(cls):
        for r in cls._procs:
            r.proc.kill()
            r.proc.wait()
            try:
                os.remove(r.path)
            except OSError:
                pass
        cls._procs = []


def clone_of(hub, fake):
    d = hub.device(fake.node)
    return Recorder(d.out.node)


RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail
                                                     else ""))
    sys.stdout.flush()


def finish():
    Recorder.stop_all()
    n, bad = len(RESULTS), RESULTS.count(False)
    print(f"\n{n - bad}/{n} passed")
    for fd in READERS.values():
        try:
            os.close(fd)
        except OSError:
            pass
    return 1 if bad else 0
