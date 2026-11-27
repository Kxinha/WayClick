#!/usr/bin/env python3
"""WayClick — autoclicker para Wayland via /dev/uinput (kernel), sem X11."""
import base64, binascii, ctypes, fcntl, glob, grp, json, math, os, pwd
import random, re, select
import shlex, shutil, socket, struct
import signal, subprocess, sys, tempfile, threading, time, unicodedata
import urllib.request, wave

try:
    from PySide6.QtCore import (Qt, QEvent, QObject, QPointF, QRectF, QSize,
                                QTimer, QUrl, Signal, Slot)
    from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QIcon,
                               QImage, QLinearGradient,
                               QKeySequence, QPainter, QPainterPath, QPalette,
                               QPen, QPixmap,
                               QShortcut)
    from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView,
                                   QApplication,
                                   QCheckBox, QComboBox, QDialog,
                                   QDialogButtonBox, QFileDialog,
                                   QFormLayout, QDoubleSpinBox, QGridLayout,
                                   QGroupBox,
                                   QHBoxLayout, QInputDialog, QLabel,
                                   QLineEdit,
                                   QListWidget, QListWidgetItem, QMenu,
                                   QMenuBar, QMessageBox, QPlainTextEdit,
                                   QPushButton, QScrollArea, QSizePolicy,
                                   QSpinBox,
                                   QStackedWidget, QSystemTrayIcon,
                                   QToolButton, QVBoxLayout, QWidget,
                                   QFrame, QListView, QTabWidget)
except ImportError:
    sys.exit("PySide6 is required.\n"
             "  Fedora/RHEL:   sudo dnf install python3-pyside6\n"
             "  Arch:          sudo pacman -S pyside6\n"
             "  Debian/Ubuntu: sudo apt install python3-pyside6.qtwidgets "
             "python3-pyside6.qtmultimedia\n"
             "  any distro:    pip install --user PySide6")

VERSION = "1.12.0"
HOMEPAGE = "https://github.com/gabrielmf1998/WayClick"

# ------------------------------------------------------------ updates ----
# Quem instalou por pacote ou pelo install.sh não descobre que saiu versão nova
# a não ser abrindo o repositório. O projeto vive no GitHub e no GitLab, e os
# dois respondem "qual é a última release" sem autenticação — tenta um, cai no
# outro, porque nem sempre a release sai nos dois ao mesmo tempo.
UPDATE_SOURCES = (
    "https://api.github.com/repos/gabrielmf1998/WayClick/releases/latest",
    "https://gitlab.com/api/v4/projects/gabriel17166%2FWayClick/"
    "releases/permalink/latest",
)
UPDATE_EVERY = 24 * 3600      # a resposta fica em cache; o aviso, esse é toda vez
AUTO_UPDATE_CHECK = True      # os testes desligam: a suíte não pode depender de rede
SYSTEM_TRAY = True            # idem: cada App monta um ícone, e a suíte monta vários


def parse_version(text):
    """'v1.3.2' -> (1, 3, 2). Serve para comparar, não para exibir."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(x) for x in nums[:3]) or (0,)


def newer_version(tag):
    """A tag do repositório contra a local, '' quando não há novidade — o que
    inclui o repositório estar atrás, que é o normal rodando do git."""
    return tag if tag and parse_version(tag) > parse_version(VERSION) else ""


class UpdateCheck(QObject):
    """Pergunta ao repositório qual é a última release, fora da thread da UI.

    Uma requisição HTTP na thread do Qt congelaria a janela pelo tempo do
    timeout sempre que a rede estivesse ruim — e a janela abre antes de a
    resposta chegar, de propósito: a checagem nunca atrasa o app.
    """
    done = Signal(str, str)          # tag, link ("" e "" se não deu ou não há)
    TIMEOUT = 6

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        for url in UPDATE_SOURCES:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": f"WayClick/{VERSION}",
                                  "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.TIMEOUT) as fh:
                    data = json.loads(fh.read().decode("utf-8", "replace"))
            except Exception:
                continue                 # fonte fora do ar: tenta a próxima
            if isinstance(data, list):
                data = data[0] if data else {}
            tag = (data.get("tag_name") or "").strip()
            link = (data.get("html_url")
                    or (data.get("_links") or {}).get("self") or HOMEPAGE)
            if tag:
                self.done.emit(tag, link)
                return
        self.done.emit("", "")


# ---------------------------------------------------------------- uinput ----
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_SET_MSCBIT = 0x40045568
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_GET_SYSNAME = (2 << 30) | (64 << 16) | (ord("U") << 8) | 44
EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_MSC = 0, 1, 2, 3, 4
SYN_REPORT = 0
BTN = {"Left": 0x110, "Right": 0x111, "Middle": 0x112,
       "Side": 0x113, "Extra": 0x114}   # os dois do polegar
BTN_ALL = range(0x110, 0x118)           # left..task
REL_ALL = (0, 1, 6, 7, 8, 9, 0x0b, 0x0c)  # x,y,hwheel,dial,wheel,misc,hi-res
EVENT_FMT = "llHHi"                      # struct input_event
EVENT_SIZE = struct.calcsize(EVENT_FMT)  # 24 em 64 bits, 16 em 32 bits
DEV_NAME = b"wayclick-virtual-mouse"


def _ior(nr, size):                      # _IOR('E', nr, size)
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


EVIOCGNAME = _ior(0x06, 256)
EVIOCGID = _ior(0x02, 8)                                     # struct input_id
EVIOCGRAB = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x90   # _IOW('E',0x90,int)

OWN_NODES = set()     # /dev/input/eventN dos nossos devices virtuais


def eviocgbit(evtype, nbytes):
    return _ior(0x20 + evtype, nbytes)


def _ioctl_buf(fd, request, nbytes):
    buf = bytearray(nbytes)
    try:
        fcntl.ioctl(fd, request, buf)
    except OSError:
        return bytearray(nbytes)
    return buf


def dev_name(fd):
    return bytes(_ioctl_buf(fd, EVIOCGNAME, 256)).split(b"\x00", 1)[0]


def dev_bits(fd, evtype, nbytes=96):
    return _ioctl_buf(fd, eviocgbit(evtype, nbytes), nbytes)


def has_bit(bits, code):
    return code // 8 < len(bits) and bits[code // 8] >> (code % 8) & 1


def uinput_node(fd):
    """/dev/input/eventN do device que ACABAMOS de criar — é assim que a gente
    se reconhece: o clone tem o mesmo nome do mouse real, filtrar por nome não
    serviria."""
    buf = bytearray(64)
    try:
        fcntl.ioctl(fd, UI_GET_SYSNAME, buf)
    except OSError:
        return None
    sysname = bytes(buf).split(b"\x00", 1)[0].decode()
    for p in glob.glob(f"/sys/class/input/{sysname}/event*"):
        return "/dev/input/" + os.path.basename(p)
    return None


class VirtualMouse:
    """Mouse virtual no kernel: o compositor Wayland o trata como hardware.

    Se `clone_fd` for o mouse físico do usuário, o device virtual nasce com o
    mesmo nome e os mesmos vendor/product/version. Isso importa de verdade: o
    KDE guarda velocidade e perfil de aceleração por dispositivo
    ([Libinput][vendor][product][nome] no kcminputrc), e o hwdb casa o DPI pelo
    mesmo modalias — sem clonar, o ponteiro cai no default e a sensibilidade
    parece ter "resetado" quando o mouse real está capturado.
    """

    def __init__(self, clone_fd=None):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        name, ident = DEV_NAME, (0x03, 0x1234, 0x5678, 1)
        if clone_fd is not None:
            name, ident = self._identity(clone_fd)
        self.name = name
        self.cloned = clone_fd is not None
        if clone_fd is None or not self._copy_caps(clone_fd):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for code in BTN_ALL:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            for rel in REL_ALL:  # REL_X/Y fazem o libinput classificar como mouse
                fcntl.ioctl(self.fd, UI_SET_RELBIT, rel)
        dev = (struct.pack("80s", name)
               + struct.pack("HHHH", *ident)
               + struct.pack("i", 0)
               + b"\x00" * (4 * 64 * 4))          # struct uinput_user_dev
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = self._own_node()
        _own(self.node)
        time.sleep(SETTLE_S)  # deixa udev/libinput enumerarem o device

    @staticmethod
    def _identity(fd):
        ident = struct.unpack("HHHH", bytes(_ioctl_buf(fd, EVIOCGID, 8)))
        return dev_name(fd)[:79] or DEV_NAME, ident

    def _copy_caps(self, fd):
        """Espelha as capacidades do mouse físico (botões, eixos, MSC_SCAN)."""
        copied = False
        for evtype, setter, nbytes in ((EV_KEY, UI_SET_KEYBIT, 96),
                                       (EV_REL, UI_SET_RELBIT, 8),
                                       (EV_MSC, UI_SET_MSCBIT, 8)):
            bits = dev_bits(fd, evtype, nbytes)
            codes = [c for c in range(nbytes * 8) if has_bit(bits, c)]
            if not codes:
                continue
            fcntl.ioctl(self.fd, UI_SET_EVBIT, evtype)
            for c in codes:
                fcntl.ioctl(self.fd, setter, c)
            copied = True
        return copied

    def _own_node(self):
        return uinput_node(self.fd)

    @staticmethod
    def edge(code, value):
        """Um flanco do botão (press ou release) + SYN, em um write só."""
        return (struct.pack(EVENT_FMT, 0, 0, EV_KEY, code, value)
                + struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))

    @staticmethod
    def packet(code):
        """press + release colados. Serve para quem escuta evento, mas some
        para quem consulta estado — ver o comentário em HOLD_S."""
        return VirtualMouse.edge(code, 1) + VirtualMouse.edge(code, 0)

    def _emit(self, etype, code, value):
        os.write(self.fd, struct.pack(EVENT_FMT, 0, 0, etype, code, value))

    def click(self, code):
        os.write(self.fd, self.packet(code))

    def move(self, dx, dy=0):
        self._emit(EV_REL, 0, dx)
        if dy:
            self._emit(EV_REL, 1, dy)
        self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self.fd < 0:                 # já fechado
            return
        if _HUB is not None:            # quem repassava por aqui solta antes
            _HUB.forget(self)
        OWN_NODES.discard(self.node)
        fd, self.fd = self.fd, -1
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(fd)


ABS_X, ABS_Y = 0, 1
ABS_RANGE = 65535
PTR_NAME = b"wayclick-absolute-pointer"


class AbsolutePointer:
    """Ponteiro absoluto: põe o cursor num ponto exato da área de trabalho.

    O Wayland não deixa um programa posicionar o cursor, e o mouse relativo
    não chega lá (aceleração, borda da tela). Um device com ABS_X/ABS_Y e
    botões, sem BTN_TOUCH, é o mouse absoluto das VMs (o tablet USB do QEMU e
    do VMware): o udev o marca como mouse, o libinput entrega movimento
    absoluto e o compositor estica a faixa 0..65535 sobre a área de trabalho
    inteira, todas as telas juntas. É assim que o cursor de uma VM acompanha o
    do host.
    """
    BUTTONS = (BTN["Left"], BTN["Right"], BTN["Middle"])

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for code in self.BUTTONS:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        for axis in (ABS_X, ABS_Y):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, axis)
        absmax = [0] * 64
        absmax[ABS_X] = absmax[ABS_Y] = ABS_RANGE
        os.write(self.fd, struct.pack("80s", PTR_NAME)
                 + struct.pack("HHHH", 0x03, 0x1234, 0x5679, 1)
                 + struct.pack("i", 0) + struct.pack("64i", *absmax)
                 + b"\x00" * (3 * 64 * 4))     # absmin, absfuzz, absflat
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = uinput_node(self.fd)
        _own(self.node)
        self.pos = None          # onde pusemos o cursor da última vez
        self._sent = (0, 0)      # valores dos eixos no kernel; nascem em 0
        time.sleep(SETTLE_S)

    @staticmethod
    def axis(v, origin, size):
        """Coordenada lógica -> valor do eixo. O libinput converte com
        valor * tamanho / 65536; mirar no meio do pixel evita que o
        arredondamento caia no vizinho."""
        if size <= 0:
            return 0
        return max(0, min(ABS_RANGE, int((v - origin + 0.5)
                                         * (ABS_RANGE + 1) / size)))

    def warp(self, x, y, ws):
        """Cursor em (x, y), nas coordenadas lógicas do compositor (as da
        Configuração de Telas). ws: retângulo da área de trabalho.

        O kernel descarta um valor absoluto igual ao anterior, e aí não sai
        evento nenhum: se a pessoa mexeu o mouse desde o último warp para o
        mesmo ponto, o cursor ficaria onde ela deixou e o clique cairia lá.
        Por isso o mesmo ponto vai precedido de um vizinho no eixo X."""
        ax, ay = self.axis(x, ws[0], ws[2]), self.axis(y, ws[1], ws[3])
        data = b""
        if (ax, ay) == self._sent:
            data = _ev(EV_ABS, ABS_X, ax + 1 if ax < ABS_RANGE else ax - 1) \
                + SYN
        os.write(self.fd, data + _ev(EV_ABS, ABS_X, ax)
                 + _ev(EV_ABS, ABS_Y, ay) + SYN)
        self._sent = (ax, ay)
        self.pos = (x, y)

    def button(self, code, down):
        os.write(self.fd, _ev(EV_KEY, code, 1 if down else 0) + SYN)

    def close(self):
        if self.fd < 0:
            return
        OWN_NODES.discard(self.node)
        fd, self.fd = self.fd, -1
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(fd)


def human_path(start, end, n, rnd=random):
    """n pontos de start a end numa curva de Bézier cúbica, acelerando no
    começo e freando no fim, como a mão faz. O último é exatamente end."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 2 or n < 2:
        return [end]
    nx, ny = -dy / dist, dx / dist          # perpendicular ao caminho
    bend = dist * rnd.uniform(0.04, 0.18) * rnd.choice((-1, 1))
    a, b = rnd.uniform(0.2, 0.4), rnd.uniform(0.6, 0.8)
    c1 = (x0 + dx * a + nx * bend, y0 + dy * a + ny * bend)
    k = rnd.uniform(0.2, 0.9)
    c2 = (x0 + dx * b + nx * bend * k, y0 + dy * b + ny * bend * k)
    pts = []
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3 - 2 * t)
        u = 1 - t
        pts.append((u * u * u * x0 + 3 * u * u * t * c1[0]
                    + 3 * u * t * t * c2[0] + t * t * t * x1,
                    u * u * u * y0 + 3 * u * u * t * c1[1]
                    + 3 * u * t * t * c2[1] + t * t * t * y1))
    pts[-1] = end
    return pts


# teclado completo para a macro (códigos de linux/input-event-codes.h).
# Nomes em inglês de propósito: rótulo de tecla é o mesmo em qualquer idioma.
KEYS = {
    "Space": 57, "Enter": 28, "Tab": 15, "Backspace": 14, "Esc": 1,
    "Delete": 111, "Insert": 110, "Home": 102, "End": 107,
    "Page Up": 104, "Page Down": 109,
    "Up": 103, "Down": 108, "Left": 105, "Right": 106,
    "Left Shift": 42, "Right Shift": 54, "Left Ctrl": 29, "Right Ctrl": 97,
    "Left Alt": 56, "Right Alt": 100, "Left Super": 125, "Right Super": 126,
    "Caps Lock": 58, "Num Lock": 69, "Scroll Lock": 70, "Menu": 127,
    "Print Screen": 99, "Pause": 119,
    "A": 30, "B": 48, "C": 46, "D": 32, "E": 18, "F": 33, "G": 34, "H": 35,
    "I": 23, "J": 36, "K": 37, "L": 38, "M": 50, "N": 49, "O": 24, "P": 25,
    "Q": 16, "R": 19, "S": 31, "T": 20, "U": 22, "V": 47, "W": 17, "X": 45,
    "Y": 21, "Z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65,
    "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "F13": 183, "F14": 184, "F15": 185, "F16": 186, "F17": 187, "F18": 188,
    "F19": 189, "F20": 190, "F21": 191, "F22": 192, "F23": 193, "F24": 194,
    "Numpad 0": 82, "Numpad 1": 79, "Numpad 2": 80, "Numpad 3": 81,
    "Numpad 4": 75, "Numpad 5": 76, "Numpad 6": 77, "Numpad 7": 71,
    "Numpad 8": 72, "Numpad 9": 73, "Numpad .": 83, "Numpad +": 78,
    "Numpad -": 74, "Numpad *": 55, "Numpad /": 98, "Numpad Enter": 96,
    "- (minus)": 12, "= (equal)": 13, "[": 26, "]": 27, "\\": 43,
    "; (semicolon)": 39, "' (apostrophe)": 40, "` (grave)": 41,
    ", (comma)": 51, ". (period)": 52, "/ (slash)": 53,
    "Volume Up": 115, "Volume Down": 114, "Mute": 113,
    "Play/Pause": 164, "Next Track": 163, "Previous Track": 165,
}
KB_NAME = b"wayclick-virtual-keyboard"


# Energia, suspender, dormir e acordar ficam de fora do teclado virtual: um
# macro importado com o código errado não pode desligar a máquina.
KB_SKIP = {116, 142, 143, 205}


class VirtualKeyboard:
    """Teclado virtual no kernel, para a macro de teclas e os macros com atalho.

    Declara de uma vez todas as teclas comuns (1 a 248), então trocar a tecla
    escolhida não exige recriar o device, e um macro importado do AutoHotkey
    com uma tecla fora da lista da UI ainda funciona.
    """

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, 249):
            if code not in KB_SKIP:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        os.write(self.fd, struct.pack("80s", KB_NAME)
                 + struct.pack("HHHH", 0x03, 0x1234, 0x5679, 1)
                 + struct.pack("i", 0) + b"\x00" * (4 * 64 * 4))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = uinput_node(self.fd)
        _own(self.node)                   # o watcher de atalho não pode nos ouvir
        time.sleep(SETTLE_S)

    @staticmethod
    def packet(code, value):
        return (struct.pack(EVENT_FMT, 0, 0, EV_KEY, code, value)
                + struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))

    def tap(self, code, hold=0.04):
        """Aperta, segura e solta. O hold não é enfeite: com press e release
        colados a tecla não existe para quem consulta estado por quadro."""
        os.write(self.fd, self.packet(code, 1))
        time.sleep(hold)
        os.write(self.fd, self.packet(code, 0))

    def set(self, code, down):
        os.write(self.fd, self.packet(code, 1 if down else 0))

    def close(self):
        if self.fd < 0:                 # já fechado
            return
        if _HUB is not None:            # quem repassava por aqui solta antes
            _HUB.forget(self)
        OWN_NODES.discard(self.node)
        fd, self.fd = self.fd, -1
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(fd)


class AntiAfk(threading.Thread):
    """Move 1 pixel e volta, periodicamente.

    Os dois movimentos vão separados por GAP de propósito: mandados no mesmo
    instante, o compositor somaria +1 e -1 no mesmo quadro e o jogo não veria
    deslocamento nenhum — que é justamente o que precisa ser visto para o
    contador de inatividade zerar.

    E o sentido inverte a cada ciclo. A aceleração do libinput escala cada
    evento pela velocidade estimada, e a ida (depois de 1 s parado) recebe um
    fator diferente da volta (50 ms depois), então elas não se anulam sozinhas.
    Alternando o sentido, o resíduo de um ciclo cancela o do seguinte e a
    posição fica presa entre dois valores em vez de derivar pela tela —
    medido: 0,0000 px de deriva.

    STEP é 4 e não 1 porque a aceleração encolhe o deslocamento: com o perfil
    flat a -0,55 daqui, 1 unidade virou 0,402 px de tela, ou seja, nem um pixel
    inteiro, e um jogo que lê coordenadas inteiras não veria movimento nenhum.
    4 unidades dão ~3 px, o bastante para registrar em qualquer configuração e
    ainda assim imperceptível, já que volta em 50 ms.
    """
    GAP = 0.05
    STEP = 4

    def __init__(self, mouse, seconds):
        super().__init__(daemon=True)
        self.mouse, self.seconds = mouse, max(seconds, 1)
        # not "_stop": up to Python 3.12 that is a Thread method join() and
        # is_alive() call, and shadowing it makes them raise TypeError
        self._halt = threading.Event()
        self.count = 0

    def run(self):
        step = self.STEP
        while not self._halt.wait(self.seconds):
            try:
                self.mouse.move(step)
                stopping = self._halt.wait(self.GAP)
                self.mouse.move(-step)
                if stopping:
                    return
                self.count += 1
                step = -step
            except OSError:
                return                      # device recriado/fechado

    def stop(self):
        self._halt.set()


class KeyMacro(threading.Thread):
    """Repete uma tecla no intervalo dado, ou a mantém pressionada."""

    HOLD_S = 0.04

    def __init__(self, kb, interval_ms, keycode, hold=False):
        super().__init__(daemon=True)
        self.kb, self.code, self.hold = kb, keycode, hold
        self.interval = max(interval_ms, 1) / 1000.0
        self.press_time = min(self.HOLD_S, self.interval * 0.5)
        self._halt = threading.Event()
        self.count = 0

    def run(self):
        try:
            if self.hold:
                self.kb.set(self.code, True)
                self._halt.wait()
            else:
                nxt = time.perf_counter()
                while not self._halt.is_set():
                    self.kb.set(self.code, True)
                    self._halt.wait(self.press_time)
                    self.kb.set(self.code, False)
                    self.count += 1
                    nxt += self.interval
                    rest = nxt - time.perf_counter()
                    if rest > 0:
                        self._halt.wait(rest)
                    else:
                        nxt = time.perf_counter()
        except OSError:
            pass
        finally:
            if self.hold:                   # nunca deixar tecla presa
                try:
                    self.kb.set(self.code, False)
                except OSError:
                    pass

    def stop(self):
        self._halt.set()


class Clicker(threading.Thread):
    """Loop de cliques com deadline absoluto.

    Acima de ~1 kHz o sleep do SO (granularidade ~50-100 us) não dá conta, então
    o loop dorme só o "grosso" do intervalo e queima os últimos SPIN_S em
    busy-wait — é o que permite chegar em 0,1 ms (10.000 cliques/s).
    """
    SPIN_S = 0.0006  # busy-wait nos últimos 600 us
    HOLD_S = 0.04    # ver abaixo

    def __init__(self, mouse, interval_ms, button, limit=0):
        super().__init__(daemon=True)
        self.mouse, self.limit = mouse, limit
        self.interval = max(interval_ms, 0.1) / 1000.0
        # busy-wait curto: só o suficiente pra cobrir a granularidade do sleep,
        # e só abaixo de 1 ms. Ele segura a GIL, e a thread que repassa o
        # teclado capturado precisa dela: de 1 ms para cima o sleep sozinho já
        # acerta (mediana 1,000 ms, p99 1,013 ms) sem atrasar ninguém.
        self.spin = 0.0 if self.interval >= 0.001 else \
            min(self.SPIN_S, self.interval * 0.3)
        # O botão precisa ficar baixo por um tempo real. Jogo não escuta evento,
        # ele pergunta "o botão está apertado?" a cada quadro; com press e
        # release colados, medimos 0 de 126 quadros pegando o botão baixo — o
        # clique simplesmente não existia para ele. Metade do intervalo, no
        # máximo 40 ms: a 100 ms dá 40 ms (uns 2,7 quadros a 60 fps) e a 0,1 ms
        # dá 50 us, preservando os 10.000 cliques/s.
        self.hold = min(self.HOLD_S, self.interval * 0.5)
        self.press = VirtualMouse.edge(button, 1)
        self.release = VirtualMouse.edge(button, 0)
        self._halt = threading.Event()
        self.count = 0

    def _until(self, deadline):
        """Dorme o grosso e queima o resto em busy-wait."""
        rest = deadline - time.perf_counter() - self.spin
        if rest > 0:
            self._halt.wait(rest)
        while time.perf_counter() < deadline:
            pass

    def run(self):
        try:  # ajuda a estabilizar o jitter; falha silenciosa sem privilégio
            os.nice(-5)
        except OSError:
            pass
        # o busy-wait segura a GIL; encurtar o switch interval mantém a UI e o
        # atalho global respondendo enquanto clicamos em alta frequência
        sys.setswitchinterval(0.002)
        write, fd = os.write, self.mouse.fd
        clock, stop = time.perf_counter, self._halt
        interval, hold = self.interval, self.hold
        nxt = clock()
        while not stop.is_set():
            write(fd, self.press)
            self._until(nxt + hold)
            write(fd, self.release)
            self.count += 1
            if self.limit and self.count >= self.limit:
                break
            nxt += interval
            self._until(nxt)
            if clock() - nxt > 0.05:  # atrasou demais (suspensão, carga): ressincroniza
                nxt = clock()

    def stop(self):
        self._halt.set()


# ------------------------------------------------------- hotkey global ------
# Lê /dev/input/event* direto: é o único jeito de atalho global no Wayland
# sem cooperação do compositor. Exige o usuário no grupo 'input'.
KEYNAMES = {"F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87,
            "F12": 88, "Insert": 110, "Pause": 119, "ScrollLock": 70,
            "KP_Add": 78, "KP_Sub": 74}
KEY_ESC = 1
BTN_TOOL_FINGER = 0x145

# O atalho global vale em QUALQUER janela: ele é lido direto do /dev/input e
# não há como "escapar" dele digitando. Uma tecla que também escreve algo, no
# modo que liga e desliga, vira uma armadilha — cada 'e' digitado num chat
# ligaria a macro. Estas não escrevem nada, então são seguras; as outras a UI
# aceita, mas avisando.
SAFE_HOTKEYS = set(KEYNAMES.values())


def is_uinput_device(path):
    """Devices criados via uinput (os nossos, os de outra instância, sobras)
    ficam em /sys/devices/virtual/input/. Mouse USB fica sob o barramento e
    mouse Bluetooth sob virtual/misc/uhid/, então nenhum real é excluído —
    importante porque o nosso clone tem o mesmo nome do mouse do usuário."""
    real = os.path.realpath(f"/sys/class/input/{os.path.basename(path)}/device")
    return real.startswith("/sys/devices/virtual/input/")


def is_pointer_shaped(fd):
    return has_bit(dev_bits(fd, EV_KEY), BTN["Left"]) \
        and has_bit(dev_bits(fd, EV_REL, 8), 0)


def is_keyboard(f):
    """Só teclados interessam. Devices uinput NÃO são descartados aqui: quem usa
    keyd, kmonad ou input-remapper digita por um teclado virtual, e ele precisa
    valer como fonte de atalho. Só o que é uinput E tem cara de ponteiro fica de
    fora — esse é um clone do WayClick, e a 10 kHz custaria caro acompanhá-lo."""
    if is_uinput_device(f.name) and is_pointer_shaped(f.fileno()):
        return False
    keys = dev_bits(f.fileno(), EV_KEY)
    return has_bit(keys, KEY_ESC) or any(has_bit(keys, c)
                                         for c in KEYNAMES.values())


def has_mouse_caps(fd):
    """Botão + eixo relativo, sem eixo absoluto (o que descarta touchpad)."""
    keys, rels = dev_bits(fd, EV_KEY), dev_bits(fd, EV_REL, 8)
    if not (has_bit(keys, BTN["Left"]) and has_bit(rels, 0)):
        return False
    return not has_bit(dev_bits(fd, EV_ABS, 8), 0) \
        and not has_bit(keys, BTN_TOOL_FINGER)


def is_mouse(f):
    """Mouse físico."""
    return not is_uinput_device(f.name) and has_mouse_caps(f.fileno())


def is_mouse_any(f):
    """Inclui ponteiros virtuais — quem usa remapeador de mouse só tem esses."""
    return has_mouse_caps(f.fileno())


def event_paths():
    """/dev/input/event* em ordem numérica (event2 antes de event10)."""
    paths = glob.glob("/dev/input/event*")
    return sorted(paths, key=lambda p: int(re.sub(r"\D", "", p) or 0))


def open_devices(match, only=None):
    """Abre /dev/input/event* que passem em `match`. Devolve (abertos, negados).
    `only` restringe a caminhos específicos (usado pra capturar só o mouse
    escolhido, em vez de sequestrar todos os apontadores da máquina)."""
    files, denied = [], 0
    for path in event_paths():
        if path in OWN_NODES or (only is not None and path not in only):
            continue
        try:
            f = open(path, "rb", buffering=0)
        except OSError:
            denied += 1
            continue
        if not _filtered_out(f.fileno()) and match(f):
            files.append(f)
        else:
            f.close()
    return files, denied


def list_mice():
    """[(caminho, nome)] de todo mouse legível — qualquer marca, quantos forem."""
    found = []
    files = open_devices(is_mouse)[0] or open_devices(is_mouse_any)[0]
    for f in files:
        found.append((f.name, dev_name(f.fileno()).decode(errors="replace")
                      or os.path.basename(f.name)))
        f.close()
    return found


def input_access():
    """(pode_ler, e_membro_do_grupo_input) — os dois divergem até relogar."""
    can_read = any(os.access(p, os.R_OK)
                   for p in glob.glob("/dev/input/event*"))
    try:
        gid = grp.getgrnam("input").gr_gid
        user = pwd.getpwuid(os.getuid()).pw_name
        member = gid in os.getgrouplist(user, os.getgid())
    except (KeyError, OSError):
        member = False
    return can_read, member


def reexec_with_input_group():
    """Ganhar o grupo 'input' normalmente exige relogar. `sg` faz na hora, sem
    senha, quando o usuário já é membro — então reexecutamos por baixo dele."""
    if os.environ.get("WAYCLICK_SG") or not shutil.which("sg"):
        return
    can_read, member = input_access()
    if can_read or not member:
        return
    env = dict(os.environ, WAYCLICK_SG="1")
    cmd = shlex.join([sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
    try:
        os.execvpe("sg", ["sg", "input", "-c", cmd], env)
    except OSError:
        pass


# ------------------------------------------------------------- input hub ----
# Um leitor só para todo teclado e mouse.
#
# Antes cada função abria os devices por conta própria, e isso funcionava
# enquanto ninguém capturava teclado. EVIOCGRAB é exclusivo: quem captura vira
# o ÚNICO a receber eventos daquele device. Bloquear a tecla de um atalho, que
# é o que o AutoHotkey faz por padrão (F1:: some com o F1 do jogo), exige
# capturar o teclado, e aí o atalho de armar, lendo o mesmo teclado por outro
# fd, ficaria surdo. Com o hub, quem captura é ele, e todo mundo continua
# ouvindo por ele.
#
# Quem quer algo se inscreve (HotkeyWatcher, MouseHold, MacroEngine) e
# responde três perguntas: quer os mouses? quer capturar este device? e, a
# cada tecla, se ela deve sumir. O que não some segue para o compositor por um
# clone virtual do device capturado, com o mesmo nome e a mesma identidade.

EVIOCGKEY = _ior(0x18, 96)               # bitmap das teclas apertadas agora
EV_LED = 0x11
UI_SET_LEDBIT = 0x40045569
SYN_DROPPED = 3
MSC_SCAN = 4
SETTLE_S = 0.4                 # udev + libinput precisam disso para ver o device
MODIFIERS = {"ctrl": (29, 97), "shift": (42, 54), "alt": (56, 100),
             "super": (125, 126)}
MOD_OF = {c: m for m, cs in MODIFIERS.items() for c in cs}
MOD_LABEL = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "super": "Super"}

# Para a suíte de testes: só devices cujo nome contém isto são abertos, e cada
# device virtual que o app cria é entregue a NEW_NODE_HOOK assim que nasce (o
# teste o captura antes de qualquer evento, então nada chega ao desktop).
DEVICE_FILTER = os.environ.get("WAYCLICK_DEVICE_FILTER", "").encode() or None
NEW_NODE_HOOK = None
GRAB_EVERYTHING = False        # testes: tudo passa pelo relay, nada vaza

# Um campo de tecla esperando o aperto: atalho nenhum dispara nem some, senão
# escolher F1 como tecla de um macro que já usa F1 seria impossível.
CAPTURE = threading.Event()


def _ev(etype, code, value):
    return struct.pack(EVENT_FMT, 0, 0, etype, code, value)


SYN = _ev(EV_SYN, SYN_REPORT, 0)


def _own(node):
    """Registra um device virtual nosso. O hub não o lê (o que a macro digita
    não pode disparar atalho) e, nos testes, o gancho o captura na hora."""
    if node:
        OWN_NODES.add(node)
        if NEW_NODE_HOOK is not None:
            NEW_NODE_HOOK(node)


def _filtered_out(fd):
    return DEVICE_FILTER is not None and DEVICE_FILTER not in dev_name(fd)


def keys_down(fd):
    """Alguma tecla ou botão apertado agora neste device?"""
    return any(_ioctl_buf(fd, EVIOCGKEY, 96))


def key_state(fd):
    bits = _ioctl_buf(fd, EVIOCGKEY, 96)
    return {i * 8 + b for i, byte in enumerate(bits) if byte
            for b in range(8) if byte >> b & 1}


class CloneDevice:
    """Cópia virtual de um device capturado: mesmo nome, mesma identidade,
    mesmas teclas, botões, eixos e LEDs.

    A identidade importa pelo mesmo motivo do clone do mouse: o KDE guarda
    configuração por device. Os LEDs importam porque, com o teclado capturado,
    é no clone que o compositor acende o Caps Lock; o hub repassa ao físico.
    """

    def __init__(self, src_fd):
        self.fd = os.open("/dev/uinput", os.O_RDWR | os.O_NONBLOCK)
        try:
            name, ident = VirtualMouse._identity(src_fd)
            for evtype, setter, nbytes in ((EV_KEY, UI_SET_KEYBIT, 96),
                                           (EV_REL, UI_SET_RELBIT, 8),
                                           (EV_MSC, UI_SET_MSCBIT, 8),
                                           (EV_LED, UI_SET_LEDBIT, 8)):
                bits = dev_bits(src_fd, evtype, nbytes)
                codes = [c for c in range(nbytes * 8) if has_bit(bits, c)]
                if not codes:
                    continue
                fcntl.ioctl(self.fd, UI_SET_EVBIT, evtype)
                for c in codes:
                    fcntl.ioctl(self.fd, setter, c)
            os.write(self.fd, struct.pack("80s", name)
                     + struct.pack("HHHH", *ident) + struct.pack("i", 0)
                     + b"\x00" * (4 * 64 * 4))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.name = name
        self.node = uinput_node(self.fd)
        _own(self.node)
        self.ready_at = time.monotonic() + SETTLE_S

    def close(self):
        OWN_NODES.discard(self.node)
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


class HubDevice:
    def __init__(self, path, f, kbd, phys, anym):
        self.path, self.f, self.fd = path, f, f.fileno()
        self.kbd = kbd                  # passa por is_keyboard
        self.phys, self.anym = phys, anym   # mouse físico / qualquer ponteiro
        self.mouse = False              # aberto porque alguém quer mouses
        self.keybits = dev_bits(self.fd, EV_KEY)
        self.writable = "+" in getattr(f, "mode", "")
        self.grabbed = False
        self.pending = False            # quer captura, esperando teclas soltas
        self.out = None                 # para onde vai o que não for bloqueado
        self.clone = None               # CloneDevice nosso, quando não há saída
        self.frame = bytearray()
        self.down = set()               # o que está apertado neste device
        self.swallowed = set()          # apertos bloqueados: o release some junto
        self.dropping = False
        self.dead = False
        self.grab_error = 0.0

    def has(self, code):
        return has_bit(self.keybits, code)


class InputHub:
    RESCAN_S = 3.0
    PENDING_S = 0.03

    def __init__(self):
        self.lock = threading.RLock()
        self.subs = []
        self.devs = {}
        self.outputs = {}               # path -> saída fixa (o mouse do app)
        self.denied = 0
        self.mod_held = {}              # código do modificador -> {paths}
        self._rejected = {}
        self._closing = []
        self._mice_spec = (False, frozenset())
        self._rp, self._wp = os.pipe()
        os.set_blocking(self._rp, False)
        os.set_blocking(self._wp, False)
        self._run = False
        self._thread = None
        self._next_scan = 0.0

    # ------------------------------------------------------ inscrições --
    def start(self):
        if self._thread:
            return
        self._run = True
        self.scan()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="wayclick-input")
        self._thread.start()

    def add(self, sub):
        with self.lock:
            if sub not in self.subs:
                self.subs.append(sub)
        self.scan()

    def remove(self, sub):
        with self.lock:
            if sub in self.subs:
                self.subs.remove(sub)
        self.scan()

    def changed(self):
        self.scan()

    def set_output(self, path, out):
        with self.lock:
            if out is None:
                self.outputs.pop(path, None)
            elif path:
                self.outputs[path] = out
            self._apply_grabs()
        self._wake()

    def forget(self, out):
        """Uma saída vai ser fechada. Quem escrevia nela solta a captura na
        hora: escrever num fd fechado, que o kernel pode reaproveitar para
        outro arquivo, seria muito pior do que um mouse sem captura."""
        with self.lock:
            for path in [p for p, o in self.outputs.items() if o is out]:
                del self.outputs[path]
            for d in self.devs.values():
                if d.grabbed and d.out is out:
                    self._ungrab(d)
            self._apply_grabs()
        self._wake()

    def lift_mods(self, mods):
        """Solta, para o compositor, os modificadores do atalho que o usuário
        está segurando. O AutoHotkey faz o mesmo antes de mandar as teclas:
        sem isso, Alt+A que manda Ctrl+C chegaria ao programa como
        Ctrl+Alt+C. O release físico, quando vier, some, porque já foi."""
        codes = {c for m in mods for c in MODIFIERS.get(m, ())}
        with self.lock:
            for d in self.devs.values():
                if not d.grabbed or d.out is None or d.dead:
                    continue
                up = sorted(c for c in d.down & codes if c not in d.swallowed)
                if not up:
                    continue
                try:
                    os.write(d.out.fd, b"".join(_ev(EV_KEY, c, 0) for c in up)
                             + SYN)
                except OSError:
                    continue
                d.swallowed.update(up)

    # ------------------------------------------------------- consultas --
    def keyboard_files(self):
        with self.lock:
            return [d.f for d in self.devs.values() if d.kbd and not d.dead]

    def mods_active(self):
        with self.lock:
            return {MOD_OF[c] for c, paths in self.mod_held.items() if paths}

    def mice_for(self, spec):
        with self.lock:
            return [d for d in self.devs.values()
                    if not d.dead and self._mouse_match(d, spec)]

    def device(self, path):
        with self.lock:
            return self.devs.get(path)

    def _have_phys(self):
        return any(d.phys for d in self.devs.values() if not d.dead)

    def _mouse_match(self, d, spec):
        if spec == "all":
            return d.phys or (d.anym and not self._have_phys())
        return bool(spec) and d.path in spec and d.anym

    # ----------------------------------------------------------- devices --
    def _mice_wanted(self):
        all_, paths = False, set()
        for s in self.subs:
            m = getattr(s, "mice", None)
            if m == "all":
                all_ = True
            elif m:
                paths |= set(m)
        return all_, frozenset(paths)

    @staticmethod
    def _open(path):
        for mode in ("r+b", "rb"):
            try:
                return open(path, mode, buffering=0)
            except OSError:
                continue
        return None

    def scan(self):
        """Abre o que interessa e ainda não está aberto, fecha o que deixou de
        interessar e reaplica as capturas. Roda no início, a cada inscrição e
        a cada RESCAN_S: teclado ou mouse plugado depois também vale."""
        with self.lock:
            spec = self._mice_wanted()
            if spec != self._mice_spec:
                self._mice_spec = spec
                self._rejected.clear()
            all_mice, mouse_paths = spec
            present = event_paths()
            for path in list(self.devs):
                if path not in present:
                    self._drop(self.devs[path])
            denied, virtual = 0, []
            for path in present:
                if path in self.devs or path in OWN_NODES:
                    continue
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                key = (st.st_rdev, st.st_ctime_ns)
                if self._rejected.get(path) == key:
                    continue
                f = self._open(path)
                if f is None:
                    denied += 1
                    continue
                try:
                    if _filtered_out(f.fileno()):
                        raise ValueError
                    kbd = is_keyboard(f)
                    phys = is_mouse(f)
                    anym = phys or is_mouse_any(f)
                except (OSError, ValueError):
                    f.close()
                    self._rejected[path] = key
                    continue
                if kbd or (all_mice and phys) or (path in mouse_paths and anym):
                    self.devs[path] = HubDevice(path, f, kbd, phys, anym)
                elif all_mice and anym:
                    virtual.append((path, f, key, kbd, phys, anym))
                else:
                    f.close()
                    self._rejected[path] = key
            # ponteiro virtual só entra quando não há mouse físico nenhum:
            # quem usa remapeador de mouse só tem esses
            for path, f, key, kbd, phys, anym in virtual:
                if not self._have_phys():
                    self.devs[path] = HubDevice(path, f, kbd, phys, anym)
                else:
                    f.close()
                    self._rejected[path] = key
            for d in list(self.devs.values()):
                d.mouse = self._mouse_match(d, "all") if all_mice else False
                d.mouse = d.mouse or self._mouse_match(d, mouse_paths)
                if not d.kbd and not d.mouse and not d.grabbed:
                    self._drop(d, unplugged=False)
            self.denied = denied
            self._apply_grabs()
        self._wake()

    def _drop(self, d, unplugged=True):
        for code in list(d.down):
            self._key(d, code, 0)
        if d.grabbed or d.clone or d.pending:
            self._ungrab(d)
        d.dead = True
        self.devs.pop(d.path, None)
        for paths in self.mod_held.values():
            paths.discard(d.path)
        self._closing.append(d.f)

    # ---------------------------------------------------------- capturas --
    def _wants(self, d):
        wanters = [s for s in self.subs if self._ask(s, "wants_grab", d)]
        if GRAB_EVERYTHING and not wanters:
            return [None]
        return wanters

    @staticmethod
    def _ask(sub, name, *args):
        fn = getattr(sub, name, None)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception:
            return None

    def _output_for(self, d, wanters):
        for s in wanters:
            out = self._ask(s, "output_for", d) if s is not None else None
            if out is not None:
                return out
        return self.outputs.get(d.path)

    def _apply_grabs(self):
        for d in list(self.devs.values()):
            if d.dead:
                continue
            wanters = self._wants(d)
            if not wanters:
                if d.grabbed or d.clone or d.pending:
                    self._ungrab(d)
                continue
            out = self._output_for(d, wanters)
            now = any(getattr(s, "grab_now", False) for s in wanters if s)
            if d.grabbed:
                if out is not None and out is not d.out:
                    self._swap(d, out)
                elif out is None and d.out is not d.clone:
                    self._ungrab(d)         # a saída fixa saiu: refaz com clone
                    d.pending = True
                continue
            if out is not None and now:
                self._grab(d, out)
            else:
                d.pending = True

    def _try_pending(self):
        now = time.monotonic()
        for d in list(self.devs.values()):
            if not d.pending or d.dead or d.grabbed:
                continue
            if d.grab_error and now - d.grab_error < self.RESCAN_S:
                continue
            out = self._output_for(d, self._wants(d))
            if out is None:
                if d.clone is None:
                    try:
                        d.clone = CloneDevice(d.fd)
                    except OSError:
                        d.grab_error = now
                        continue
                if now < d.clone.ready_at:
                    continue
                out = d.clone
            # Capturar com tecla apertada deixaria essa tecla presa no
            # compositor: ele viu o aperto pelo device físico, e o release
            # chegaria só a nós. Com repetição de tecla, seria "wwwwww" sem fim.
            if keys_down(d.fd):
                continue
            self._grab(d, out)

    def _grab(self, d, out):
        try:
            fcntl.ioctl(d.fd, EVIOCGRAB, 1)
        except OSError:
            d.grab_error = time.monotonic()
            d.pending = True
            return False
        d.grabbed, d.pending, d.out = True, False, out
        d.frame = bytearray()
        d.swallowed.clear()
        return True

    def _release_out(self, d):
        relayed = d.down - d.swallowed
        if relayed and d.out is not None:
            try:
                os.write(d.out.fd, b"".join(_ev(EV_KEY, c, 0) for c in relayed)
                         + SYN)
            except OSError:
                pass

    def _swap(self, d, out):
        self._release_out(d)
        d.out = out
        if d.clone is not None and out is not d.clone:
            self._closing.append(d.clone)
            d.clone = None

    def _ungrab(self, d):
        if d.grabbed:
            self._release_out(d)
            try:
                fcntl.ioctl(d.fd, EVIOCGRAB, 0)
            except OSError:
                pass
        d.grabbed = d.pending = False
        d.out = None
        d.swallowed.clear()
        d.frame = bytearray()
        if d.clone is not None:
            self._closing.append(d.clone)
            d.clone = None

    # ----------------------------------------------------------- eventos --
    def _key(self, d, code, value):
        """Uma tecla: avisa cada inscrito e decide se ela some."""
        if code in MOD_OF:
            held = self.mod_held.setdefault(code, set())
            if value:
                held.add(d.path)
            else:
                held.discard(d.path)
        if value == 1:
            d.down.add(code)
        elif value == 0:
            d.down.discard(code)
        swallow = code in d.swallowed and value != 1
        if value == 0:
            d.swallowed.discard(code)
        for s in list(self.subs):
            if self._ask(s, "on_key", d, code, value) and value == 1:
                swallow = True
        if value == 1 and swallow and d.grabbed:
            d.swallowed.add(code)
        return swallow and d.grabbed

    def _read(self, d):
        try:
            data = os.read(d.fd, 4096)
        except BlockingIOError:
            return
        except OSError:
            self._drop(d)
            return
        grabbed, out = d.grabbed, bytearray()
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, i)
            raw = data[i:i + EVENT_SIZE]
            if d.dropping:
                if etype == EV_SYN and code == SYN_REPORT:
                    d.dropping = False
                    self._resync(d, out)
                continue
            if etype == EV_SYN:
                if code == SYN_DROPPED:
                    d.dropping = True
                    d.frame = bytearray()
                    continue
                if code == SYN_REPORT:
                    if grabbed and d.frame:
                        out += d.frame
                        out += raw
                    d.frame = bytearray()
                    continue
            elif etype == EV_REL and code in (REL_WHEEL, REL_HWHEEL):
                for s in list(self.subs):        # o gravador conta a roda
                    if getattr(s, "on_rel", None) is not None:
                        self._ask(s, "on_rel", d, code, value)
            elif etype == EV_KEY:
                if self._key(d, code, value):
                    # bloqueada: o MSC_SCAN que a anunciava some junto
                    if len(d.frame) >= EVENT_SIZE:
                        _, _, pt, pc, _ = struct.unpack_from(
                            EVENT_FMT, d.frame, len(d.frame) - EVENT_SIZE)
                        if pt == EV_MSC and pc == MSC_SCAN:
                            del d.frame[-EVENT_SIZE:]
                    continue
            if grabbed:
                d.frame += raw
        if out and d.out is not None:
            try:
                os.write(d.out.fd, bytes(out))
            except OSError:
                pass

    def _resync(self, d, out):
        """Depois de um SYN_DROPPED o kernel jogou eventos fora: o estado
        verdadeiro vem do EVIOCGKEY, e o clone é acertado por ele."""
        now = key_state(d.fd)
        fix = bytearray()
        for code in sorted(d.down - now):
            if not self._key(d, code, 0) and d.grabbed:
                fix += _ev(EV_KEY, code, 0)
        for code in sorted(now - d.down):
            if not self._key(d, code, 1) and d.grabbed:
                fix += _ev(EV_KEY, code, 1)
        if fix:
            out += fix + SYN

    def _leds(self, d):
        try:
            data = os.read(d.clone.fd, 4096)
        except OSError:
            return
        buf = bytearray()
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, i)
            if etype == EV_LED:
                buf += _ev(EV_LED, code, value)
        if buf and d.writable:
            try:
                os.write(d.fd, bytes(buf) + SYN)
            except OSError:
                pass

    def _wake(self):
        try:
            os.write(self._wp, b"x")
        except OSError:
            pass

    def _flush_closing(self):
        items, self._closing = self._closing, []
        for item in items:
            try:
                item.close()
            except OSError:
                pass

    def _loop(self):
        while self._run:
            with self.lock:
                self._flush_closing()
                live = [d for d in self.devs.values() if not d.dead]
                rl = [self._rp] + [d.fd for d in live]
                rl += [d.clone.fd for d in live
                       if d.clone is not None and d.writable]
                pending = any(d.pending for d in live)
            wait = self.PENDING_S if pending else \
                max(0.05, self._next_scan - time.monotonic())
            try:
                ready, _, _ = select.select(rl, [], [], wait)
            except (OSError, ValueError):
                ready = []
                time.sleep(0.02)
            with self.lock:
                byfd = {d.fd: d for d in self.devs.values() if not d.dead}
                clones = {d.clone.fd: d for d in self.devs.values()
                          if d.clone is not None}
                for fd in ready:
                    if fd == self._rp:
                        try:
                            os.read(self._rp, 4096)
                        except OSError:
                            pass
                    elif fd in byfd:
                        self._read(byfd[fd])
                    elif fd in clones:
                        self._leds(clones[fd])
                self._try_pending()
            if time.monotonic() >= self._next_scan:
                self._next_scan = time.monotonic() + self.RESCAN_S
                self.scan()

    def stop(self):
        global _HUB
        if _HUB is self:
            _HUB = None              # o próximo HUB() nasce limpo
        self._run = False
        self._wake()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self.lock:
            self.subs = []
            for d in list(self.devs.values()):
                self._ungrab(d)
                self._closing.append(d.f)
                d.dead = True
            self.devs.clear()
            self._flush_closing()


_HUB = None


def HUB():
    """O hub do processo, criado no primeiro uso."""
    global _HUB
    if _HUB is None:
        _HUB = InputHub()
        _HUB.start()
    return _HUB


class HotkeyWatcher(QObject):
    """Uma tecla vista em qualquer teclado, com o foco onde estiver. `block`
    faz ela sumir para os outros programas, como um atalho do AutoHotkey."""
    pressed = Signal()
    released = Signal()
    mice = None

    def __init__(self, keycode, block=False, hub=None):
        super().__init__()
        self.keycode, self.block = keycode, block
        self.hub = hub or HUB()
        self.error = None
        self._on = False
        # (código) -> bool: um macro ligado responde a esta tecla agora. Aí o
        # aperto é dele, e armar o clique junto (o F8 padrão de um .ahk
        # importado) clicaria sozinho no jogo
        self.shadow = None
        self._shadowed = False
        if not self.files:
            self.error = ("sem acesso a /dev/input" if self.hub.denied
                          else "nenhum teclado encontrado")

    @property
    def files(self):
        return self.hub.keyboard_files()

    @property
    def ok(self):
        return bool(self.files)

    def start(self):
        self._on = True
        self.hub.add(self)

    def set_key(self, keycode):
        self.keycode = keycode
        self.hub.changed()

    def set_block(self, on):
        self.block = bool(on)
        self.hub.changed()

    def wants_grab(self, d):
        return self._on and self.block and d.kbd and d.has(self.keycode)

    def on_key(self, d, code, value):
        if not self._on or code != self.keycode or not d.kbd:
            return False
        if CAPTURE.is_set() and value == 1:
            return False
        if value == 1:
            self._shadowed = bool(self.shadow and self.shadow(code))
            if self._shadowed:
                return False
            self.pressed.emit()
        elif value == 0:
            if self._shadowed:
                self._shadowed = False
                return False
            self.released.emit()
        return self.block and not self._shadowed

    def stop(self):
        self._on = False
        self.hub.remove(self)


class SuspendWatcher(HotkeyWatcher):
    """O Suspend do AutoHotkey (F7::Suspend): uma tecla, com ou sem
    modificadores, que liga e desliga os macros de atalho. Vale mesmo com os
    macros desligados (é ela que liga de volta) e some para o jogo."""

    def __init__(self, keycode=0, mods=(), hub=None):
        super().__init__(keycode, block=True, hub=hub)
        self.mods = frozenset(mods)
        self._down = False

    def set_key(self, keycode, mods=()):
        self.mods = frozenset(mods)
        self._down = False
        super().set_key(keycode)

    def matches(self, code):
        """A tecla e os modificadores de agora são os do Suspend. Roda na
        thread do hub."""
        return bool(self._on and self.keycode and code == self.keycode
                    and self.mods <= self.hub.mods_active())

    def wants_grab(self, d):
        return self._on and bool(self.keycode) and d.kbd \
            and d.has(self.keycode)

    def on_key(self, d, code, value):
        if not self._on or not self.keycode or code != self.keycode \
                or not d.kbd:
            return False
        if value == 1:
            if CAPTURE.is_set() or not self.mods <= self.hub.mods_active():
                return False
            self._down = True
            self.pressed.emit()
            return True
        if value == 0:
            was, self._down = self._down, False
            return was
        return self._down                # repetição automática do aperto


# --------------------------------------------------- segurar o botão --------
class MouseHold(QObject):
    """Modo "clicar enquanto segura": captura o mouse físico e o retransmite.

    O compositor agrega o estado dos botões por seat, então enquanto o botão
    físico está pressionado ele DESCARTA qualquer clique que a gente injete —
    medido: 0 de 20. Por isso a captura (EVIOCGRAB): o compositor deixa de ver
    o mouse real e passa a ver só o nosso, e nós repassamos tudo (movimento,
    roda, outros botões) menos o botão-gatilho, que vira o autoclick.

    `swallow` é o que decide isso: engolir o gatilho só faz sentido quando o
    Clicker vai reemitir no lugar dele. Com o clique desligado — segurar o
    botão para disparar só a macro de teclado, por exemplo — ninguém reemite,
    e engolir deixaria o botão do usuário morto enquanto estivesse armado.
    Nesse caso o gatilho é repassado normalmente, além de acionar a macro.

    A captura em si é do InputHub; o relay sai pelo `mouse` dado, que é o
    mesmo device por onde o Clicker clica.
    """
    pressed = Signal()
    released = Signal()
    failed = Signal(str)
    grab_now = True               # armar e já estar capturado, sem espera

    def __init__(self, mouse, button_code, only=None, swallow=True, hub=None):
        super().__init__()
        self.mouse, self.button = mouse, button_code
        self.only = only              # captura só o mouse escolhido na UI
        self.swallow = swallow        # lido a cada evento: muda junto com a UI
        self.hub = hub or HUB()
        self.files = []
        self._on = False

    @property
    def mice(self):
        if not self._on:
            return None
        return frozenset(self.only) if self.only else "all"

    def start(self):
        self._on = True
        self.hub.add(self)
        mine = self.hub.mice_for(self.mice)
        if not mine:
            self._on = False
            self.hub.remove(self)
            self.failed.emit("no mouse found" if not self.hub.denied
                             else "no access to /dev/input")
            return False
        grabbed = [d for d in mine if d.grabbed]
        if not grabbed:
            self._on = False
            self.hub.remove(self)
            self.failed.emit("could not grab the mouse")
            return False
        self.files = [d.f for d in grabbed]
        return True

    def wants_grab(self, d):
        return self._on and d.anym and self.hub._mouse_match(d, self.mice)

    def output_for(self, d):
        return self.mouse if self.wants_grab(d) else None

    def on_key(self, d, code, value):
        if not self._on or code != self.button or not self.wants_grab(d):
            return False
        if value == 1:
            self.pressed.emit()
        elif value == 0:
            self.released.emit()
        return self.swallow

    def stop(self):
        if not self._on:
            return
        self._on = False
        self.files = []
        self.hub.remove(self)


# ------------------------------------------------- tela: captura e imagens --
# Passos que olham a tela (achar uma imagem, conferir a cor de um ponto)
# precisam de quadros dela. No Wayland só o compositor tem a imagem, e o
# caminho de um programa comum é o portal ScreenCast: o sistema pergunta uma
# vez quais telas compartilhar e, com persist_mode=2, devolve um token que
# pula a pergunta nas vezes seguintes. Os quadros vêm pelo PipeWire, e o
# GStreamer (pipewiresrc -> appsink) os entrega ao numpy. Tudo opcional: sem
# numpy, PyGObject ou o plugin PipeWire do GStreamer, só esses passos param,
# dizendo o que instalar.
SCREEN_FPS = 10          # quadros por segundo pedidos ao compositor
SCREEN_IDLE_S = 30.0     # sem uso por esse tempo, a captura fecha
SCREEN_ASK_S = 120.0     # quanto esperar a pessoa responder ao portal
DEPS_HINT = ("Arch: sudo pacman -S python-numpy python-gobject "
             "gst-plugin-pipewire gst-plugins-base\n"
             "Fedora: sudo dnf install python3-numpy python3-gobject "
             "pipewire-gstreamer gstreamer1-plugins-base\n"
             "Debian/Ubuntu: sudo apt install python3-numpy python3-gi "
             "gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 "
             "gstreamer1.0-pipewire gstreamer1.0-plugins-base")


class VisionError(Exception):
    """Falta algo para olhar a tela; a mensagem diz o quê."""


_NP = None


def _numpy():
    global _NP
    if _NP is None:
        try:
            import numpy
        except ImportError:
            raise VisionError(_("Image and color steps need numpy.") + "\n"
                              + DEPS_HINT) from None
        _NP = numpy
    return _NP


class Frame:
    """Um quadro de uma tela. rgb: array (altura, largura, 3) uint8; x, y, w,
    h: o retângulo lógico que ele cobre, nas coordenadas do compositor. Com
    escala de 150%, o quadro tem 1,5 pixel por unidade lógica."""
    __slots__ = ("rgb", "x", "y", "w", "h")

    def __init__(self, rgb, x=0, y=0, w=0, h=0):
        self.rgb = rgb
        self.x, self.y = int(x), int(y)
        self.w = int(w) or rgb.shape[1]
        self.h = int(h) or rgb.shape[0]

    def to_global(self, px, py):
        return (self.x + px * self.w / self.rgb.shape[1],
                self.y + py * self.h / self.rgb.shape[0])

    def to_pixel(self, gx, gy):
        px = math.floor((gx - self.x) * self.rgb.shape[1] / self.w)
        py = math.floor((gy - self.y) * self.rgb.shape[0] / self.h)
        if 0 <= px < self.rgb.shape[1] and 0 <= py < self.rgb.shape[0]:
            return px, py
        return None

    def crop(self, rect):
        """A parte do quadro dentro de rect (lógico): (pixels, x0, y0)."""
        x, y, w, h = rect
        sx, sy = self.rgb.shape[1] / self.w, self.rgb.shape[0] / self.h
        x0 = max(0, math.floor((x - self.x) * sx))
        y0 = max(0, math.floor((y - self.y) * sy))
        x1 = min(self.rgb.shape[1], math.ceil((x + w - self.x) * sx))
        y1 = min(self.rgb.shape[0], math.ceil((y + h - self.y) * sy))
        if x1 <= x0 or y1 <= y0:
            return None, 0, 0
        return self.rgb[y0:y1, x0:x1], x0, y0


def _fast_len(n):
    """Menor 2^a 3^b 5^c >= n: a FFT do numpy é rápida nesses tamanhos."""
    best = 1 << max(0, (n - 1).bit_length())
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            q = p35
            while q < n:
                q *= 2
            best = min(best, q)
            p35 *= 3
        p5 *= 5
    return best


def _box(np, a, h, w):
    """Soma de cada janela h x w, por imagem integral (float64)."""
    c = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    np.cumsum(a, 0, out=c[1:, 1:])
    np.cumsum(c[1:, 1:], 1, out=c[1:, 1:])
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def _ncc(np, img, tpl):
    """Correlação normalizada (o TM_CCOEFF_NORMED do OpenCV) de tpl em cada
    posição de img, as duas em cinza. A correlação sai pela FFT e as somas
    de cada janela pela imagem integral: custa o mesmo para qualquer tamanho
    de template."""
    H, W = img.shape
    h, w = tpl.shape
    t = tpl.astype(np.float64)
    t -= t.mean()
    tn = float(np.sqrt((t * t).sum()))
    fs = (_fast_len(H), _fast_len(W))
    corr = np.fft.irfft2(np.fft.rfft2(img, fs)
                         * np.fft.rfft2(t[::-1, ::-1].astype(np.float32), fs),
                         fs)[h - 1:H, w - 1:W]
    i64 = img.astype(np.float64)
    s1 = _box(np, i64, h, w)
    var = _box(np, i64 * i64, h, w) - s1 * s1 / (h * w)
    den = np.sqrt(np.maximum(var, 0.0)) * tn
    out = np.zeros(corr.shape, np.float32)
    # janela quase lisa não casa com nada: dividir ali só amplifica ruído
    ok = den > max(1e-6, tn * 0.5 * math.sqrt(h * w))
    out[ok] = corr[ok] / den[ok]
    return out


def _pool(np, a, f):
    """Média de cada bloco f x f (f = 1, 2 ou 4), em cinza ou RGB. Somas com
    passo 2 em vez de mean() sobre eixos: umas 5 vezes mais rápido."""
    H, W = a.shape[0] // f * f, a.shape[1] // f * f
    out = a[:H, :W].astype(np.float32)
    while f > 1:
        out = (out[0::2, 0::2] + out[1::2, 0::2] + out[0::2, 1::2]
               + out[1::2, 1::2]) * 0.25
        f //= 2
    return out


def _gray(np, rgb):
    return rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114],
                                             np.float32)


def _peaks(np, score, k, floor, sh, sw):
    """Até k máximos acima de floor, afastados uns dos outros. Primeiro o
    melhor de cada bloco sh x sw, depois os k melhores blocos: um platô de
    valores altos (textura repetida, degradê) não ocupa a lista inteira."""
    H, W = score.shape
    bh, bw = max(1, sh), max(1, sw)
    Hb, Wb = -(-H // bh), -(-W // bw)
    pad = np.full((Hb * bh, Wb * bw), -np.inf, np.float32)
    pad[:H, :W] = score
    blocks = pad.reshape(Hb, bh, Wb, bw).transpose(0, 2, 1, 3).reshape(
        Hb, Wb, bh * bw)
    arg = blocks.argmax(-1)
    top = np.take_along_axis(blocks, arg[..., None], -1)[..., 0].ravel()
    n = min(top.size, k * 4)
    idx = np.argpartition(top, -n)[-n:]
    idx = idx[np.argsort(top[idx])[::-1]]
    out = []
    for i in idx:
        if top[i] < floor or len(out) >= k:
            break
        by, bx = divmod(int(i), Wb)
        a = int(arg.flat[i])
        y, x = by * bh + a // bw, bx * bw + a % bw
        if all(abs(y - py) >= sh or abs(x - px) >= sw for py, px in out):
            out.append((y, x))
    return out


FLAT_STD = 3.0           # template mais liso que isso é comparado por cor
PHASE_MIN = 0.8          # abaixo disso a busca reduzida perderia o encaixe
POOL2_MIN = 24           # lado menor do template para buscar a 1/2
POOL4_MIN = 96           # e a 1/4


def _phase_worst(np, tg, f):
    """O score que um encaixe perfeito teria na busca reduzida, no pior caso.
    A grade 1/f da tela cai em qualquer fase do template, e textura fina
    (texto miúdo, ruído) muda muito de uma fase para outra: um template de
    ruído a uma posição ímpar cai de 1,0 para 0,4 e some dos candidatos.
    Formas e áreas de cor quase não mudam."""
    h, w = tg.shape
    hp, wp = (h - f + 1) // f, (w - f + 1) // f
    if hp < 2 or wp < 2:
        return 0.0
    base = None
    worst = 1.0
    for dy in range(f):
        for dx in range(f):
            b = _pool(np, tg[dy:dy + hp * f, dx:dx + wp * f], f).ravel()
            b = b - b.mean()
            if base is None:
                base, bn = b, float(np.sqrt((b * b).sum()))
                continue
            den = bn * float(np.sqrt((b * b).sum()))
            worst = min(worst, float((base * b).sum()) / den
                        if den > 1e-9 else 0.0)
    return worst


def _color_gate(np, diff):
    """O quanto a cor média afasta do template tira do score: até 10 níveis
    num canal não contam, depois cada nível tira 1/255."""
    return 1.0 - np.maximum(0.0, diff - 10.0) / 255.0


def _rgb_score(np, win, tc, tcn, tmean):
    """Correlação nas três cores (o TM_CCOEFF_NORMED do OpenCV em imagem
    colorida) de uma janela com o template já centrado (tc, norma tcn),
    vezes o peso da cor média."""
    a = win.reshape(-1, 3).astype(np.float64)
    mean = a.mean(0)
    a -= mean
    den = tcn * float(np.sqrt((a * a).sum()))
    s = float((a * tc).sum()) / den if den > 1e-9 else 0.0
    return s * float(_color_gate(np, float(np.abs(mean - tmean).max())))


def find_image(img, tpl, conf=0.85):
    """Melhor lugar de tpl dentro de img (arrays RGB): (x, y, score) do
    canto de cima à esquerda, ou None abaixo de conf.

    Os candidatos saem da correlação em cinza, numa versão reduzida (1/2 ou
    1/4) quando o template aguenta a redução, e são refinados no tamanho
    real. A correlação ignora brilho e contraste, então a cor média de cada
    janela já pesa na escolha dos candidatos, e o score final é a
    correlação nas três cores (o TM_CCOEFF_NORMED do OpenCV em imagem
    colorida) vezes essa cor média: o mesmo botão apagado, de outra cor ou
    com o texto de outra cor perde pontos."""
    np = _numpy()
    H, W = img.shape[:2]
    h, w = tpl.shape[:2]
    if h < 1 or w < 1 or h > H or w > W:
        return None
    tg = _gray(np, tpl)
    tmean = tpl.reshape(-1, 3).mean(0)
    if float(tg.std()) < FLAT_STD:
        # template liso (um pedaço de cor só): distância RMS da cor, por janela
        ms = np.zeros((H - h + 1, W - w + 1))
        for c in range(3):
            ch = img[..., c].astype(np.float64)
            m = float(tmean[c])
            ms += (_box(np, ch * ch, h, w) - 2 * m * _box(np, ch, h, w)) \
                / (h * w) + m * m
        score = 1.0 - np.sqrt(np.maximum(ms / 3, 0)) / 100.0
        y, x = np.unravel_index(int(np.argmax(score)), score.shape)
        s = float(score[y, x])
        return (int(x), int(y), s) if s >= conf else None

    def centered(rgb):
        c = rgb.reshape(-1, 3).astype(np.float64)
        c -= c.mean(0)
        return c, float(np.sqrt((c * c).sum()))

    f = 4 if min(h, w) >= POOL4_MIN else 2 if min(h, w) >= POOL2_MIN else 1
    stable = 1.0
    while f > 1 and H * W >= 250000:
        stable = _phase_worst(np, tg, f)
        if stable >= PHASE_MIN:
            break
        f //= 2                 # textura fina: menos redução, ou nenhuma
    if f == 1 or H * W < 250000:
        f, stable = 1, 1.0
        rgb = img
        src, tsrc = _gray(np, img), tg
    else:
        rgb = _pool(np, img, f)
        src, tsrc = _gray(np, rgb), _pool(np, tg, f)
    th, tw = tsrc.shape
    score = _ncc(np, src, tsrc)
    # a correlação em cinza acha a forma e não vê cor: o mesmo ícone em outra
    # cor, ou um traço igual de outra cor, dá 1,0 também, e esses empates
    # enchem a lista. Cada candidato é conferido nas três cores ainda nesta
    # escala, quantos couberem em ~2 milhões de contas
    tq, tqn = centered(tpl if f == 1 else _pool(np, tpl, f))
    budget = int(min(512, max(16, 2_000_000 // (th * tw * 3))))
    ranked = sorted(((_rgb_score(np, rgb[cy:cy + th, cx:cx + tw], tq, tqn,
                                 tmean), cy, cx)
                     for cy, cx in _peaks(np, score, budget,
                                          min(conf - 0.25,
                                              conf * stable - 0.15),
                                          max(1, th // 2), max(1, tw // 2))),
                    reverse=True)
    if f == 1:
        if ranked and ranked[0][0] >= conf:
            return (ranked[0][2], ranked[0][1], ranked[0][0])
        return None
    tc, tcn = centered(tpl)
    best = None
    for _s, cy, cx in ranked[:16]:
        y0, x0 = max(0, cy * f - f), max(0, cx * f - f)
        y1, x1 = min(H, cy * f + h + f), min(W, cx * f + w + f)
        loc = _ncc(np, _gray(np, img[y0:y1, x0:x1]), tg)
        ly, lx = np.unravel_index(int(np.argmax(loc)), loc.shape)
        y, x = y0 + int(ly), x0 + int(lx)
        sc = _rgb_score(np, img[y:y + h, x:x + w], tc, tcn, tmean)
        if sc >= conf and (best is None or sc > best[2]):
            best = (x, y, sc)
    return best


def locate(frames, tpl, conf, region=None):
    """tpl em todas as telas: centro lógico (x, y) do melhor lugar e o
    score, ou None. region (x, y, w, h) lógico limita a busca."""
    best = None
    th, tw = tpl.shape[:2]
    for f in frames:
        sub, ox, oy = f.crop(region) if region else (f.rgb, 0, 0)
        if sub is None:
            continue
        hit = find_image(sub, tpl, conf)
        if hit and (best is None or hit[2] > best[2]):
            gx, gy = f.to_global(ox + hit[0] + tw / 2.0, oy + hit[1] + th / 2.0)
            best = (gx, gy, hit[2])
    return best


def pixel_at(frames, x, y):
    for f in frames:
        p = f.to_pixel(x + 0.5, y + 0.5)
        if p:
            return tuple(int(c) for c in f.rgb[p[1], p[0]])
    return None


def pixel_search(frames, region, rgb, tol, cols=False, rtl=False,
                 btt=False):
    """A cor em algum ponto de region (lógico), como o PixelSearch do
    AutoHotkey: o centro lógico do primeiro ponto achado, ou None. A ordem
    é a dele, linha por linha (cols: coluna por coluna, o modo padrão do
    v1), da direita para a esquerda com rtl e de baixo para cima com btt."""
    np = _numpy()
    want = np.array(rgb, np.int16)
    for f in frames:
        sub, ox, oy = f.crop(region)
        if sub is None:
            continue
        mask = np.abs(sub.astype(np.int16) - want).max(axis=2) <= tol
        if not mask.any():
            continue
        if rtl:
            mask = mask[:, ::-1]
        if btt:
            mask = mask[::-1]
        h, w = mask.shape
        if cols:
            px, py = divmod(int(np.flatnonzero(mask.T)[0]), h)
        else:
            py, px = divmod(int(np.flatnonzero(mask)[0]), w)
        if rtl:
            px = w - 1 - px
        if btt:
            py = h - 1 - py
        return f.to_global(ox + px + 0.5, oy + py + 0.5)
    return None


def parse_color(text):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(text or "").strip())
    if not m:
        return None
    v = int(m.group(1), 16)
    return (v >> 16, (v >> 8) & 255, v & 255)


def color_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def color_close(a, b, tol):
    return a is not None and max(abs(a[i] - b[i]) for i in range(3)) <= tol


def qimage_rgb(img):
    """QImage -> array RGB (altura, largura, 3)."""
    np = _numpy()
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    raw = np.frombuffer(bytes(img.constBits())[:bpl * h], np.uint8)
    return raw.reshape(h, bpl)[:, :w * 3].reshape(h, w, 3).copy()


def rgb_qimage(rgb):
    np = _numpy()
    h, w = rgb.shape[:2]
    data = np.ascontiguousarray(rgb, np.uint8).tobytes()
    return QImage(data, w, h, 3 * w, QImage.Format_RGB888).copy()


def png_bytes(rgb):
    """Array RGB -> PNG, só com zlib: serve em qualquer thread."""
    import zlib
    np = _numpy()
    h, w = rgb.shape[:2]
    raw = np.zeros((h, w * 3 + 1), np.uint8)       # filtro 0 em cada linha
    raw[:, 1:] = np.ascontiguousarray(rgb, np.uint8).reshape(h, w * 3)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + chunk(b"IEND", b""))


_IMAGES = {}


def load_image(path):
    """Template de um passo, em cache até o arquivo mudar."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _IMAGES.get(path)
    if hit and hit[0] == key:
        return hit[1]
    img = QImage(path)
    if img.isNull():
        return None
    rgb = qimage_rgb(img)
    _IMAGES[path] = (key, rgb)
    return rgb


def images_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    return os.path.join(base, "wayclick", "images")


_GST = None


def _gst_modules():
    global _GST
    if _GST is None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst
        except (ImportError, ValueError):
            raise VisionError(_("Seeing the screen needs PyGObject and "
                                "GStreamer.") + "\n" + DEPS_HINT) from None
        Gst.init(None)
        for name in ("pipewiresrc", "videoconvert", "appsink"):
            if Gst.ElementFactory.find(name) is None:
                raise VisionError(_("Seeing the screen needs the GStreamer "
                                    "element {name}.", name=name) + "\n"
                                  + DEPS_HINT)
        try:
            gi.require_version("GstVideo", "1.0")
            from gi.repository import GstVideo
        except (ImportError, ValueError):
            GstVideo = None
        _GST = (Gio, GLib, Gst, GstVideo)
    return _GST


class _Stream:
    """Uma tela do portal: pipewiresrc -> appsink, e o último quadro."""

    def __init__(self, gst, fd, node, props):
        _Gio, _GLib, self.Gst, self.GstVideo = gst
        pos = props.get("position") or (0, 0)
        size = props.get("size") or (0, 0)
        self.x, self.y = int(pos[0]), int(pos[1])
        self.w, self.h = int(size[0]), int(size[1])
        self.last = None
        self.pipe = self.sink = None
        err = ""
        # o limite de quadros alivia o compositor (no jogo a tela muda a cada
        # quadro); se a negociação não aceitar, vai sem ele
        for rate in (f"video/x-raw,max-framerate={SCREEN_FPS}/1 ! ", ""):
            pipe = self.Gst.parse_launch(
                f"pipewiresrc fd={fd} path={int(node)} do-timestamp=true "
                f"! {rate}videoconvert "
                "! video/x-raw,format={BGRx,BGRA,RGBx,RGBA} "
                "! appsink name=sink max-buffers=1 drop=true sync=false")
            sink = pipe.get_by_name("sink")
            pipe.set_state(self.Gst.State.PLAYING)
            sample = sink.emit("try-pull-sample", 5 * self.Gst.SECOND)
            if sample is not None:
                self.pipe, self.sink = pipe, sink
                self.last = self._convert(sample)
                return
            msg = pipe.get_bus().pop_filtered(self.Gst.MessageType.ERROR)
            if msg is not None:
                err = msg.parse_error()[0].message
            pipe.set_state(self.Gst.State.NULL)
        raise VisionError(_("No picture came from the screen.")
                          + (f" ({err})" if err else ""))

    def latest(self):
        sample = self.sink.emit("try-pull-sample", 0)
        if sample is not None:
            f = self._convert(sample)
            if f is not None:
                self.last = f
        return self.last

    def _convert(self, sample):
        np = _numpy()
        caps = sample.get_caps()
        buf = sample.get_buffer()
        if caps is None or buf is None:
            return None
        s = caps.get_structure(0)
        fmt = s.get_value("format")
        w, h = s.get_value("width"), s.get_value("height")
        data = buf.extract_dup(0, buf.get_size())
        stride, offset = w * 4, 0
        meta = None
        if self.GstVideo is not None:
            try:
                meta = self.GstVideo.buffer_get_video_meta(buf)
            except (AttributeError, TypeError):
                meta = None
        if meta is not None:
            stride, offset = int(meta.stride[0]), int(meta.offset[0])
        elif h and len(data) % h == 0 and len(data) // h >= w * 4:
            stride = len(data) // h
        need = stride * h
        raw = np.frombuffer(data, np.uint8)[offset:offset + need]
        if raw.size < need - (stride - w * 4):
            return None
        if raw.size < need:
            raw = np.concatenate([raw, np.zeros(need - raw.size, np.uint8)])
        px = raw.reshape(h, stride)[:, :w * 4].reshape(h, w, 4)
        rgb = px[..., [2, 1, 0]] if fmt.startswith("BGR") else px[..., :3]
        return Frame(np.ascontiguousarray(rgb), self.x, self.y, self.w or w,
                     self.h or h)

    def broken(self):
        msg = self.pipe.get_bus().pop_filtered(
            self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS)
        return msg is not None

    def close(self):
        if self.pipe is not None:
            self.pipe.set_state(self.Gst.State.NULL)
            self.pipe = self.sink = None


class ScreenCapture:
    """Quadros das telas pelo portal ScreenCast; uma instância para o app.

    ensure() liga a captura (na primeira vez o sistema pergunta quais telas
    compartilhar) e espera; frames() devolve o quadro mais recente de cada
    tela. Serve a qualquer thread: os macros pedem quadros das deles. Sessão
    do portal e GStreamer vivem numa thread própria, com GLib.MainContext
    próprio, sem depender do laço do Qt. Sem uso por SCREEN_IDLE_S, fecha, e
    o aviso de compartilhamento do sistema some.
    """
    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, token_file=None):
        self.token_file = token_file
        self.token = ""
        if token_file:
            try:
                with open(token_file) as fh:
                    self.token = fh.read().strip()[:200]
            except OSError:
                pass
        self.lock = threading.RLock()
        self.state = "off"           # off, starting, on, failed
        self.error = ""
        self.users = 0
        self.used = 0.0
        self.streams = []
        self._ready = threading.Event()
        self._halt = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ uso --
    def acquire(self):
        with self.lock:
            self.users += 1
            self.used = time.monotonic()

    def release(self):
        with self.lock:
            self.users = max(0, self.users - 1)
            self.used = time.monotonic()

    def start(self):
        """Pede a captura e volta na hora; ensure() é quem espera."""
        with self.lock:
            self.used = time.monotonic()
            if self.state in ("starting", "on"):
                return
            self.state, self.error = "starting", ""
            self._ready.clear()
            self._halt.clear()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="wayclick-screen")
            self._thread.start()

    def ensure(self, timeout=SCREEN_ASK_S):
        self.start()
        self._ready.wait(timeout)
        return self.state == "on"

    def frames(self):
        with self.lock:
            if self.state != "on":
                return []
            self.used = time.monotonic()
            out = []
            for st in self.streams:
                f = st.latest()
                if f is not None:
                    out.append(f)
            return out

    def stop(self, join=True):
        self._halt.set()
        t = self._thread
        if join and t is not None and t is not threading.current_thread():
            t.join(timeout=3.0)

    # --------------------------------------------------------- portal --
    def _save_token(self, token):
        self.token = token
        if not self.token_file:
            return
        try:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT
                         | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(token)
        except OSError:
            pass

    def _prop(self, bus, gst, name):
        Gio, GLib = gst[0], gst[1]
        try:
            v = bus.call_sync(self.BUS, self.PATH,
                              "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (self.IFACE, name)),
                              GLib.VariantType.new("(v)"),
                              Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            return None
        return v.unpack()[0]

    def _request(self, bus, ctx, gst, method, sig, values, opts, wait_s):
        """Método do portal que responde pelo sinal Request.Response."""
        Gio, GLib = gst[0], gst[1]
        token = "wayclick_" + os.urandom(6).hex()
        sender = bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        box = {}

        def got(_conn, _snd, _path, _iface, _sig, params, *_x):
            box["r"] = params.unpack()
        sid = bus.signal_subscribe(None, "org.freedesktop.portal.Request",
                                   "Response", path, None,
                                   Gio.DBusSignalFlags.NONE, got)
        try:
            opts = dict(opts, handle_token=GLib.Variant("s", token))
            bus.call_sync(self.BUS, self.PATH, self.IFACE, method,
                          GLib.Variant("(" + sig + "a{sv})",
                                       tuple(values) + (opts,)),
                          GLib.VariantType.new("(o)"),
                          Gio.DBusCallFlags.NONE, 30000, None)
            deadline = time.monotonic() + wait_s
            while "r" not in box:
                if self._halt.is_set() or time.monotonic() > deadline:
                    try:
                        bus.call_sync(self.BUS, path,
                                      "org.freedesktop.portal.Request",
                                      "Close", None, None,
                                      Gio.DBusCallFlags.NONE, 3000, None)
                    except GLib.Error:
                        pass
                    raise VisionError(_("Stopped.") if self._halt.is_set()
                                      else _("The system did not answer the "
                                             "screen sharing request."))
                while ctx.pending():
                    ctx.iteration(False)
                if "r" not in box:
                    self._halt.wait(0.02)
        finally:
            bus.signal_unsubscribe(sid)
        code, results = box["r"]
        if code == 1:
            raise VisionError(_("Screen sharing was not allowed."))
        if code != 0:
            raise VisionError(_("The screen sharing request failed."))
        return results

    def _handshake(self, bus, ctx, gst):
        Gio, GLib = gst[0], gst[1]
        V = GLib.Variant
        version = self._prop(bus, gst, "version")
        if version is None:
            raise VisionError(_("No screen sharing portal answered. "
                                "xdg-desktop-portal and the one for your "
                                "desktop (KDE, GNOME, wlr) must be "
                                "installed."))
        cursors = self._prop(bus, gst, "AvailableCursorModes") or 0
        res = self._request(bus, ctx, gst, "CreateSession", "", (), {
            "session_handle_token": V("s", "wayclick_"
                                      + os.urandom(6).hex())}, 30)
        session = str(res.get("session_handle") or "")
        if not session:
            raise VisionError(_("The screen sharing request failed."))
        self._session = session
        opts = {"types": V("u", 1), "multiple": V("b", True)}
        if cursors & 1:                  # sem o cursor: ele atrapalha a busca
            opts["cursor_mode"] = V("u", 1)
        if version >= 4:
            opts["persist_mode"] = V("u", 2)
            if self.token:
                opts["restore_token"] = V("s", self.token)
        self._request(bus, ctx, gst, "SelectSources", "o", (session,), opts,
                      30)
        res = self._request(bus, ctx, gst, "Start", "os", (session, ""), {},
                            SCREEN_ASK_S)
        if res.get("restore_token"):
            # o token vale uma vez só: cada Start devolve outro
            self._save_token(str(res["restore_token"]))
        streams = res.get("streams") or []
        if not streams:
            raise VisionError(_("No screen was shared."))
        out, fds = bus.call_with_unix_fd_list_sync(
            self.BUS, self.PATH, self.IFACE, "OpenPipeWireRemote",
            V("(oa{sv})", (session, {})), GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE, 10000, None, None)
        return session, streams, fds.get(out.unpack()[0])

    def _run(self):
        bus = ctx = None
        fd = None
        self._session = None
        streams = []
        failed = ""
        try:
            gst = _gst_modules()
            Gio, GLib = gst[0], gst[1]
            ctx = GLib.MainContext.new()
            ctx.push_thread_default()
            try:
                bus = Gio.DBusConnection.new_for_address_sync(
                    Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION,
                                                      None),
                    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                    None, None)
            except GLib.Error as e:
                raise VisionError(_("No session D-Bus: {err}",
                                    err=e.message)) from None
            session, nodes, fd = self._handshake(bus, ctx, gst)
            for node, props in nodes:
                streams.append(_Stream(gst, fd, node, props))
            with self.lock:
                self.streams = streams
                self.state = "on"
                # o ócio conta daqui: a pessoa pode ter levado minutos no
                # diálogo, e quem pediu ainda nem pegou a captura
                self.used = time.monotonic()
            self._ready.set()
            self._watch(bus, ctx, gst, session)
        except VisionError as e:
            failed = str(e)
        except Exception as e:          # GLib.Error e afins: vira mensagem
            failed = _("Screen capture failed: {err}",
                       err=getattr(e, "message", None) or str(e))
        finally:
            with self.lock:
                self.streams = []
                self.state = "failed" if failed else "off"
                self.error = failed
            for st in streams:
                st.close()
            if bus is not None:
                if self._session:
                    try:
                        bus.call_sync(self.BUS, self._session,
                                      "org.freedesktop.portal.Session",
                                      "Close", None, None,
                                      _GST[0].DBusCallFlags.NONE, 3000, None)
                    except Exception:
                        pass
                try:
                    bus.close_sync(None)
                except Exception:
                    pass
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if ctx is not None:
                ctx.pop_thread_default()
            self._ready.set()

    def _watch(self, bus, ctx, gst, session):
        Gio = gst[0]
        closed = []
        sid = bus.signal_subscribe(None, "org.freedesktop.portal.Session",
                                   "Closed", session, None,
                                   Gio.DBusSignalFlags.NONE,
                                   lambda *a: closed.append(1))
        try:
            while not self._halt.is_set():
                while ctx.pending():
                    ctx.iteration(False)
                if closed:
                    self._session = None
                    break
                with self.lock:
                    if any(st.broken() for st in self.streams):
                        break
                    if self.users == 0 and \
                            time.monotonic() - self.used > SCREEN_IDLE_S:
                        break
                self._halt.wait(0.25)
        finally:
            bus.signal_unsubscribe(sid)


_SCREEN = None


def SCREEN():
    """A captura do app, criada na primeira vez que alguém precisa."""
    global _SCREEN
    if _SCREEN is None:
        _SCREEN = ScreenCapture(os.path.join(
            os.environ.get("XDG_CONFIG_HOME")
            or os.path.expanduser("~/.config"), "wayclick", "screen-token"))
    return _SCREEN


def notify_desktop(title, body):
    """Notificação do sistema pelo D-Bus, sem depender de bandeja."""
    def lit(text):
        return "'" + str(text).replace("\\", "\\\\").replace(
            "'", "\\'").replace("\n", "\\n") + "'"
    try:
        subprocess.Popen(
            ["gdbus", "call", "--session", "--dest",
             "org.freedesktop.Notifications", "--object-path",
             "/org/freedesktop/Notifications", "--method",
             "org.freedesktop.Notifications.Notify", lit("WayClick"), "0",
             lit("wayclick"), lit(title), lit(body), "[]", "{}", "8000"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def post_webhook(url, text, images=()):
    """Mensagem num webhook do Discord (ou compatível), com prints. Devolve o
    status HTTP, ou a exceção."""
    payload = {"content": text[:2000], "username": "WayClick"}
    headers = {"User-Agent": f"WayClick/{VERSION} (+{HOMEPAGE})"}
    if images:
        bound = "wayclick" + os.urandom(12).hex()
        parts = [(f'--{bound}\r\nContent-Disposition: form-data; '
                  f'name="payload_json"\r\nContent-Type: application/json'
                  f'\r\n\r\n').encode() + json.dumps(payload).encode()]
        for i, png in enumerate(images[:10]):
            parts.append((f'--{bound}\r\nContent-Disposition: form-data; '
                          f'name="files[{i}]"; filename="screen{i + 1}.png"'
                          f'\r\nContent-Type: image/png\r\n\r\n').encode()
                         + png)
        body = b"\r\n".join(parts) + f"\r\n--{bound}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={bound}"
    else:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception as e:
        return e


def open_link(url):
    """Abre link ou arquivo com o programa padrão (xdg-open), sem esperar."""
    if not url or url.startswith("-"):
        return
    try:
        proc = subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return
    threading.Thread(target=proc.wait, daemon=True).start()


OPENER = open_link       # os testes trocam: nada se abre de verdade
WEBHOOK = post_webhook   # idem, a suíte não pode depender de rede



# ------------------------------------------------------ macros com atalho --
# O que o AutoHotkey faz e o Linux não tinha: cada macro com o seu atalho,
# independente do Start. "F1 liga e desliga um E a cada 1 ms" é um macro:
# atalho F1, modo liga/desliga, repetir a cada 1 ms, passo "Toque E". Vários
# convivem, como vários scripts .ahk rodando juntos.
MACRO_MODES = ("toggle", "hold", "once", "always")
MACRO_MODE_LABELS = {"toggle": "Toggle: press to start, again to stop",
                     "hold": "While held",
                     "once": "Once per press",
                     "always": "Always, while enabled"}
MACRO_MODE_SHORT = {"toggle": "toggle", "hold": "while held",
                    "once": "once", "always": "always"}
STEP_OPS = ("tap", "down", "up", "wait", "text", "wheel", "move",
            "point", "image", "pixel", "notify", "open", "watch", "count",
            "window")
POINT_CLICKS = ("none", "left", "right", "middle", "double")
SEEN_ACTS = ("none", "move", "left", "right", "middle", "double")
WATCH_KINDS = ("image", "pixel", "window")
WINDOW_ACTS = ("check", "focus")
FLOWS = ("next", "stop", "restart", "jump")
COORD = 100000                   # |x|, |y| máximos de uma posição na tela


def _whole_screen(region):
    """Uma área que cobre qualquer tela: o "0, 0, A_ScreenWidth,
    A_ScreenHeight" de um .ahk vira isso."""
    return bool(region) and region[0] <= 0 and region[1] <= 0 \
        and region[2] >= COORD and region[3] >= COORD


MAX_MACROS = 50
MAX_STEPS = 2000
MAX_RUNS = 1000000               # "parar depois de N" e "a cada N"
# campos que todo texto de aviso entende; contadores entram com o nome deles
TEXT_FIELDS = ("run", "time")
FIELD_RE = re.compile(r"\{(\w{1,24})\}")
REL_WHEEL, REL_HWHEEL = 8, 6
REL_WHEEL_HI, REL_HWHEEL_HI = 0x0b, 0x0c

# texto digitado passo a passo, posição física de um teclado US
CHAR_KEYS = {" ": (57, False), "\n": (28, False), "\t": (15, False)}
for _c in "abcdefghijklmnopqrstuvwxyz":
    CHAR_KEYS[_c] = (KEYS[_c.upper()], False)
    CHAR_KEYS[_c.upper()] = (KEYS[_c.upper()], True)
for _d, _s in zip("1234567890", "!@#$%^&*()"):
    CHAR_KEYS[_d] = (KEYS[_d], False)
    CHAR_KEYS[_s] = (KEYS[_d], True)
for _plain, _shifted, _code in (("-", "_", 12), ("=", "+", 13), ("[", "{", 26),
                                ("]", "}", 27), ("\\", "|", 43), (";", ":", 39),
                                ("'", '"', 40), ("`", "~", 41), (",", "<", 51),
                                (".", ">", 52), ("/", "?", 53)):
    CHAR_KEYS[_plain] = (_code, False)
    CHAR_KEYS[_shifted] = (_code, True)


def _num(value, default, lo, hi, kind=float):
    try:
        v = kind(value)
    except (TypeError, ValueError):
        return default
    if v != v:                       # NaN
        return default
    return max(lo, min(hi, v))


def new_macro_id():
    return os.urandom(4).hex()


def normalize_step(s):
    """Um passo vindo da config ou do importador, validado. None se inválido."""
    if not isinstance(s, dict):
        return None
    op = s.get("op")
    if op not in STEP_OPS:
        return None
    if op in ("tap", "down", "up"):
        code = _num(s.get("code"), 0, 0, 0x2ff, int)
        if not code:
            return None
        step = {"op": op, "code": code}
        if not is_button(code):          # botão tem nome traduzido na hora
            step["name"] = str(s.get("name") or key_label(code))[:40]
        if op == "tap":
            step["times"] = _num(s.get("times", 1), 1, 1, 1000, int)
        return step
    if op == "wait":
        ms = _num(s.get("ms"), 100.0, 0.0, 3600000.0)
        mx = _num(s.get("max", 0), 0.0, 0.0, 3600000.0)
        step = {"op": "wait", "ms": ms}
        if mx > ms:
            step["max"] = mx
        return step
    if op == "text":
        text = str(s.get("text") or "")[:500]
        return {"op": "text", "text": text} if text else None
    if op == "wheel":
        d = s.get("dir") if s.get("dir") in ("up", "down", "left", "right") \
            else "down"
        return {"op": "wheel", "dir": d,
                "times": _num(s.get("times", 1), 1, 1, 100, int)}
    if op == "move":
        dx = _num(s.get("dx", 0), 0, -5000, 5000, int)
        dy = _num(s.get("dy", 0), 0, -5000, 5000, int)
        return {"op": "move", "dx": dx, "dy": dy} if (dx or dy) else None
    if op == "point":
        step = {"op": "point", "x": _num(s.get("x"), 0, -COORD, COORD, int),
                "y": _num(s.get("y"), 0, -COORD, COORD, int),
                "click": s.get("click") if s.get("click") in POINT_CLICKS
                else "none"}
        _human(step, s)
        spread = _num(s.get("spread", 0), 0, 0, 500, int)
        if spread:
            step["spread"] = spread
        return step
    if op in ("image", "pixel", "window"):
        sight = _sight(op, s)
        if sight is None:
            return None
        step = {"op": op,
                "want": s.get("want") if s.get("want") in ("seen", "gone")
                else "seen",
                "wait": _num(s.get("wait", 5000.0), 5000.0, 0.0, 3600000.0)}
        step.update(sight)
        if op == "window":
            step["act"] = s.get("act") if s.get("act") in WINDOW_ACTS \
                else "check"
        else:
            default = "left" if op == "image" else "none"
            step["act"] = s.get("act") if s.get("act") in SEEN_ACTS \
                else default
            _human(step, s)
        _flows(step, s, (("ok", "next"), ("fail", "stop")))
        return step
    if op == "watch":
        kind = s.get("kind") if s.get("kind") in WATCH_KINDS else "image"
        sight = _sight(kind, s)
        if sight is None:
            return None
        step = {"op": "watch", "kind": kind,
                "want": s.get("want") if s.get("want") in ("seen", "gone")
                else "seen",
                "every": _num(s.get("every", 1000.0), 1000.0, 100.0,
                              3600000.0)}
        step.update(sight)
        if kind == "window":
            step["act"] = "none"
        else:
            default = "left" if kind == "image" else "none"
            step["act"] = s.get("act") if s.get("act") in SEEN_ACTS \
                else default
            _human(step, s)
        _flows(step, s, (("ok", "next"),))
        return step
    if op == "count":
        name = counter_name(s.get("name"))
        if not name:
            return None
        step = {"op": "count", "name": name,
                "every": _num(s.get("every", 0), 0, 0, MAX_RUNS, int)}
        if step["every"]:
            _flows(step, s, (("ok", "stop"),))
        return step
    if op == "notify":
        text = str(s.get("text") or "")[:1000]
        url = str(s.get("url") or "").strip()[:500]
        step = {"op": "notify", "text": text}
        if url.startswith(("https://", "http://")):
            step["url"] = url
        if s.get("shot"):
            step["shot"] = True
        return step if text or step.get("shot") else None
    url = str(s.get("url") or "").strip()[:2000]
    return {"op": "open", "url": url} if url and not url.startswith("-") \
        else None


def _human(step, s):
    """Movimento em curva, de quem mexe a mão, e quanto ele dura."""
    if s.get("human"):
        step["human"] = True
        step["ms"] = _num(s.get("ms", 300.0), 300.0, 20.0, 10000.0)


def _rect(r):
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        return None
    x, y, w, h = (_num(v, 0, -COORD, COORD, int) for v in r)
    return [x, y, w, h] if w > 0 and h > 0 else None


def _sight(kind, s):
    """O que um passo procura: a imagem, a cor num ponto ou a janela."""
    if kind == "image":
        path = str(s.get("path") or "")[:4096]
        if not path:
            return None
        out = {"path": path,
               "conf": round(_num(s.get("conf", 0.85), 0.85, 0.5, 1.0), 3)}
        region = _rect(s.get("region"))
        if region:
            out["region"] = region
        return out
    if kind == "pixel":
        color = parse_color(s.get("color"))
        if color is None:
            return None
        out = {"x": _num(s.get("x"), 0, -COORD, COORD, int),
               "y": _num(s.get("y"), 0, -COORD, COORD, int),
               "color": color_hex(color),
               "tol": _num(s.get("tol", 16), 16, 0, 255, int)}
        # com uma área, procura a cor nela toda (o PixelSearch); a ordem da
        # busca só existe para o que veio de um .ahk
        region = _rect(s.get("region"))
        if region:
            out["region"] = region
            if s.get("scan") == "cols":
                out["scan"] = "cols"
            for key in ("rtl", "btt"):
                if s.get(key) is True:
                    out[key] = True
        return out
    match = " ".join(str(s.get("match") or "").split())[:100]
    return {"match": match} if match else None


def _flows(step, s, keys):
    for key, dflt in keys:
        step[key] = s.get(key) if s.get(key) in FLOWS else dflt
        if step[key] == "jump":
            step[key + "_to"] = _num(s.get(key + "_to", 1), 1, 1,
                                     MAX_STEPS, int)


def counter_name(name):
    """Nome de contador, que vira {nome} nos textos: letras, dígitos e _."""
    name = str(name or "").strip().lower()
    return name if re.fullmatch(r"\w{1,24}", name) else ""


def clock_text(seconds):
    """1:02:03, ou 2:03 abaixo de uma hora."""
    s = max(0, int(seconds))
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def normalize_macro(m):
    m = m if isinstance(m, dict) else {}
    code = _num(m.get("code", 0), 0, 0, 0x2ff, int)
    mode = m.get("mode") if m.get("mode") in MACRO_MODES else "toggle"
    steps = [x for x in (normalize_step(s) for s in (m.get("steps") or [])
                         if isinstance(m.get("steps"), list)) if x]
    out = {
        "id": str(m.get("id") or new_macro_id())[:16],
        "name": str(m.get("name") or "")[:60],
        "enabled": bool(m.get("enabled", True)),
        "code": code,
        "key": "" if not code or is_button(code)
        else str(m.get("key") or key_label(code))[:40],
        "mods": sorted({x for x in (m.get("mods") or []) if x in MODIFIERS}),
        "mode": mode,
        "repeat": bool(m.get("repeat", True)),
        "period_ms": _num(m.get("period_ms", 100.0), 100.0, 0.0, 3600000.0),
        "press_ms": _num(m.get("press_ms", 0.0), 0.0, 0.0, 10000.0),
        "block": bool(m.get("block", True)),
        "limit": _num(m.get("limit", 0), 0, 0, MAX_RUNS, int),
        "steps": steps[:MAX_STEPS],
    }
    # joga dentro da Doca; a chave só existe quando ligada, então os macros
    # de antes ficam iguais byte a byte
    if m.get("dock") is True:
        out["dock"] = True
    return out


def hotkey_label(m):
    if not m.get("code"):
        return _("no hotkey")
    mods = "+".join(MOD_LABEL[x] for x in ("ctrl", "shift", "alt", "super")
                    if x in m.get("mods", []))
    code = m["code"]
    key = trigger_label(code) if is_button(code) else \
        (m.get("key") or key_label(code))
    return f"{mods}+{key}" if mods else key


def _ms(v):
    return f"{v:g}"


def step_label(s):
    op = s["op"]
    if op in ("tap", "down", "up"):
        code = s["code"]
        if is_button(code):
            btn = _(BTN_NAMES[code]) if code in BTN_NAMES else f"button {code}"
            text = {"tap": _("Click {btn}", btn=btn),
                    "down": _("Hold {btn} button", btn=btn),
                    "up": _("Release {btn} button", btn=btn)}[op]
        else:
            key = s.get("name") or key_label(code)
            text = {"tap": _("Tap {key}", key=key),
                    "down": _("Hold {key}", key=key),
                    "up": _("Release {key}", key=key)}[op]
        if op == "tap" and s.get("times", 1) > 1:
            text += f" ×{s['times']}"
        return text
    if op == "wait":
        if s.get("max"):
            return _("Wait {a} to {b} ms", a=_ms(s["ms"]), b=_ms(s["max"]))
        return _("Wait {ms} ms", ms=_ms(s["ms"]))
    if op == "text":
        return _("Type “{t}”", t=s["text"].replace("\n", "⏎"))
    if op == "wheel":
        return _({"up": "Wheel up", "down": "Wheel down", "left": "Wheel left",
                  "right": "Wheel right"}[s["dir"]]) + f" ×{s['times']}"
    if op == "move":
        return _("Move pointer by {dx}, {dy}", dx=s["dx"], dy=s["dy"])
    if op == "point":
        text = {"none": _("Move to {x}, {y}", x=s["x"], y=s["y"]),
                "left": _("Click at {x}, {y}", x=s["x"], y=s["y"]),
                "right": _("Right click at {x}, {y}", x=s["x"], y=s["y"]),
                "middle": _("Middle click at {x}, {y}", x=s["x"], y=s["y"]),
                "double": _("Double click at {x}, {y}", x=s["x"], y=s["y"])
                }[s["click"]]
        if s.get("spread"):
            text += f" ±{s['spread']}"
        return text + (", " + _("curved") if s.get("human") else "")
    if op in ("image", "pixel", "window"):
        seen = s["want"] == "seen"
        if op == "image":
            name = image_name(s["path"])
            text = (_("Find “{img}”", img=name) if seen else
                    _("Wait until “{img}” is gone", img=name))
        elif op == "pixel" and _whole_screen(s.get("region")):
            text = (_("Find {color} anywhere on the screen", color=s["color"])
                    if seen else
                    _("No {color} anywhere on the screen", color=s["color"]))
        elif op == "pixel" and s.get("region"):
            x, y, w, h = s["region"]
            text = (_("Find {color} in {w}×{h} at {x}, {y}", color=s["color"],
                      w=w, h=h, x=x, y=y) if seen else
                    _("No {color} in {w}×{h} at {x}, {y}", color=s["color"],
                      w=w, h=h, x=x, y=y))
        elif op == "pixel":
            where = f"{s['x']}, {s['y']}"
            text = (_("Pixel {at} is {color}", at=where, color=s["color"])
                    if seen else
                    _("Pixel {at} is not {color}", at=where,
                      color=s["color"]))
        else:
            win = s["match"]
            text = (_("Wait until the “{win}” window closes", win=win)
                    if not seen else
                    _("Focus the “{win}” window", win=win)
                    if s["act"] == "focus" else
                    _("Check the “{win}” window is open", win=win))
        parts = [text, _("up to {s} s", s=_ms(s["wait"] / 1000.0))
                 if s["wait"] else _("once")]
        if seen and op != "window" and s["act"] != "none":
            parts.append(_act_label(s["act"]))
        if s["ok"] != "next":
            parts.append(_("then {what}", what=flow_label(s, "ok")))
        text = ", ".join(parts)
        if s["fail"] != "next":
            text += "; " + _("else {what}", what=flow_label(s, "fail"))
        return text
    if op == "watch":
        seen, kind = s["want"] == "seen", s["kind"]
        if kind == "image":
            name = image_name(s["path"])
            text = (_("Watch for “{img}”", img=name) if seen else
                    _("Watch for “{img}” to go away", img=name))
        elif kind == "pixel" and _whole_screen(s.get("region")):
            text = (_("Watch for {color} anywhere on the screen",
                      color=s["color"]) if seen else
                    _("Watch for {color} to go away from the screen",
                      color=s["color"]))
        elif kind == "pixel" and s.get("region"):
            x, y, w, h = s["region"]
            text = (_("Watch for {color} in {w}×{h} at {x}, {y}",
                      color=s["color"], w=w, h=h, x=x, y=y) if seen else
                    _("Watch for {color} to go away from {w}×{h} at {x}, {y}",
                      color=s["color"], w=w, h=h, x=x, y=y))
        elif kind == "pixel":
            where = f"{s['x']}, {s['y']}"
            text = (_("Watch pixel {at} for {color}", at=where,
                      color=s["color"]) if seen else
                    _("Watch pixel {at} losing {color}", at=where,
                      color=s["color"]))
        else:
            text = (_("Watch for the “{win}” window", win=s["match"]) if seen
                    else _("Watch for the “{win}” window to close",
                           win=s["match"]))
        parts = [text, _("every {s} s", s=_ms(s["every"] / 1000.0))]
        if seen and kind != "window" and s["act"] != "none":
            parts.append(_act_label(s["act"]))
        if s["ok"] != "next":
            parts.append(_("then {what}", what=flow_label(s, "ok")))
        return ", ".join(parts)
    if op == "count":
        text = _("Count {name}", name="{" + s["name"] + "}")
        if s["every"]:
            text += ", " + _("every {n} then {what}", n=s["every"],
                             what=flow_label(s, "ok"))
        return text
    if op == "notify":
        text = _("Notify “{t}”", t=s["text"]) if s["text"] else _("Notify")
        if s.get("shot"):
            text += " + " + _("screenshot")
        return text + (" (webhook)" if s.get("url") else "")
    return _("Open {url}", url=s["url"])


def image_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def _act_label(act):
    return {"move": _("point at it"), "left": _("click it"),
            "right": _("right click it"), "middle": _("middle click it"),
            "double": _("double click it")}[act]


def flow_label(s, key):
    how = s[key]
    if how == "jump":
        return _("go to step {n}", n=s[key + "_to"])
    return {"next": _("continue"), "stop": _("stop"),
            "restart": _("start over")}[how]


def macro_summary(m, limit=4):
    """'F1, toggle, every 1 ms: Tap E' para a lista e o relatório."""
    parts = [hotkey_label(m), _(MACRO_MODE_SHORT[m["mode"]])]
    if m["mode"] != "once":
        if not m["repeat"]:
            parts.append(_("no repeat"))
        elif m["period_ms"]:
            parts.append(_("every {ms} ms", ms=_ms(m["period_ms"])))
        if m["repeat"] and m.get("limit"):
            parts.append(_("stop after {n} runs", n=m["limit"])
                         if m["limit"] > 1 else _("stop after 1 run"))
    if m.get("dock"):
        parts.append(_("in the Dock"))
    steps = [step_label(s) for s in m["steps"][:limit]]
    if len(m["steps"]) > limit:
        steps.append("…")
    return ", ".join(parts) + ": " + (", ".join(steps) or _("no steps"))


class CompiledMacro:
    """Um macro pronto para o motor: campos já convertidos, imutável."""

    def __init__(self, m):
        self.id, self.name = m["id"], m["name"] or hotkey_label(m)
        self.code, self.mods = m["code"], frozenset(m["mods"])
        self.mode, self.repeat, self.block = m["mode"], m["repeat"], m["block"]
        self.period = m["period_ms"] / 1000.0
        self.steps = tuple(m["steps"])
        self.spec = json.dumps(m, sort_keys=True)
        loops = self.repeat and self.mode != "once"
        if m["press_ms"]:
            self.press = m["press_ms"] / 1000.0
        elif loops and self.period > 0:
            # metade do período, no máximo 40 ms: o mesmo acerto do Clicker e
            # da macro de teclado (jogo consulta estado por quadro)
            self.press = min(KeyMacro.HOLD_S, self.period * 0.5)
        else:
            self.press = KeyMacro.HOLD_S
        self.uses_keys = any((st["op"] in ("tap", "down", "up")
                              and not is_button(st["code"]))
                             or st["op"] == "text" for st in self.steps)
        self.clicks = {st["code"] for st in self.steps
                       if st["op"] in ("tap", "down", "up")
                       and is_button(st["code"])}
        # ponteiro absoluto e tela, que a App só cria para quem usa
        self.uses_pointer = any(
            st["op"] == "point" or (_sees(st) and st["act"] != "none")
            for st in self.steps)
        self.uses_screen = any(
            _sees(st) or (st["op"] == "notify" and st.get("shot"))
            for st in self.steps)
        self.uses_windows = any(
            st["op"] == "window"
            or (st["op"] == "watch" and st["kind"] == "window")
            for st in self.steps)
        self.limit = m.get("limit", 0) if loops else 0
        self.counters = tuple(dict.fromkeys(
            st["name"] for st in self.steps if st["op"] == "count"))
        self.dock = bool(m.get("dock"))


def _sees(st):
    """O passo olha a tela (imagem ou cor), seja esperando ou vigiando."""
    return st["op"] in ("image", "pixel") or (
        st["op"] == "watch" and st["kind"] != "window")


class _Jump(Exception):
    """Um vigia disparou no meio de um passo e o macro vai para outro lugar
    (to: índice, ou MacroRunner.STOP)."""

    def __init__(self, to):
        super().__init__(to)
        self.to = to


class MacroRunner(threading.Thread):
    """Executa a sequência de um macro: uma vez, ou em laço até parar.

    O período é contado de início a início (como o SetTimer do AutoHotkey),
    com deadline absoluto: o erro de um ciclo não se acumula no seguinte.

    Só dorme, nunca fica girando à espera. Busy-wait segura a GIL, e com o
    teclado capturado é a thread do hub que repassa cada tecla do usuário:
    medido com um macro de 1 ms girando, a thread principal ficou até 2 s sem
    rodar. Dormindo, o período mediu 1,000 ms (p99 1,013 ms) e nada atrasou.
    Tudo o que a sequência apertou e não soltou é solto ao parar, em
    qualquer caminho de saída.
    """
    MIN_LOOP_S = 0.001           # laço sem espera nenhuma não vira enxurrada
    STOP = -1
    HOVER_S = 0.03               # o jogo vê o cursor chegar antes do clique
    POLL_S = 0.1                 # de quanto em quanto olha a tela, esperando
    MIN_JUMP_S = 0.005           # voltar passos sem espera não gira solto

    def __init__(self, macro, kb, mouse, loop, keep, on_done=None, env=None):
        super().__init__(daemon=True, name=f"macro-{macro.id}")
        self.m, self.kb, self.mouse = macro, kb, mouse
        self.loop, self.keep = loop, keep
        self.on_done = on_done
        self.env = env       # o motor: ponteiro absoluto, tela, avisos
        self._halt = threading.Event()
        self.stopping = False
        self.count = 0
        self.held = []
        self.pheld = []      # botões apertados no ponteiro absoluto
        self._seeing = False
        self._blind = False  # a tela falhou: não pergunta de novo nesta vez
        self._told = set()
        self.watches = {}    # índice do passo Vigiar -> [passo, a cada, vence]
        self.counters = dict.fromkeys(macro.counters, 0)
        self.began = time.perf_counter()

    def stop(self):
        self.stopping = True
        self._halt.set()

    def _until(self, deadline):
        rest = deadline - time.perf_counter()
        if rest > 0:
            self._halt.wait(rest)

    def _sleep(self, seconds):
        if seconds > 0:
            self._until(time.perf_counter() + seconds)

    def _set(self, code, down):
        dev = self.mouse if is_button(code) else self.kb
        if dev is None:
            return
        os.write(dev.fd, _ev(EV_KEY, code, 1 if down else 0) + SYN)
        if down:
            self.held.append(code)
        elif code in self.held:
            self.held.remove(code)

    def _tap(self, code, times=1):
        for i in range(times):
            if i and self._halt.is_set():
                return
            self._set(code, True)
            self._sleep(self.m.press)
            self._set(code, False)
            if i + 1 < times:
                self._rest(self.m.press)

    def _wheel(self, direction, times):
        if self.mouse is None:
            return
        lo, hi = (REL_HWHEEL, REL_HWHEEL_HI) if direction in ("left", "right") \
            else (REL_WHEEL, REL_WHEEL_HI)
        sign = 1 if direction in ("up", "right") else -1
        for i in range(times):
            if i:
                self._sleep(0.01)
            os.write(self.mouse.fd, _ev(EV_REL, lo, sign)
                     + _ev(EV_REL, hi, 120 * sign) + SYN)

    def _open(self, url):
        OPENER(url)

    # ------------------------------------------- ponteiro absoluto e tela --
    def _report(self, text):
        """Uma vez por problema: um macro em laço não enche a tela de aviso."""
        if text in self._told:
            return
        self._told.add(text)
        report = getattr(self.env, "report", None)
        if report:
            report(f"{self.m.name}: {text}")

    def _pointer(self):
        return getattr(self.env, "pointer", None)

    def _go(self, x, y, human=False, ms=0.0):
        """Cursor até (x, y); em curva e no tempo pedido, se human."""
        p, ws = self._pointer(), getattr(self.env, "workspace", None)
        if p is None or not ws:
            self._report(_("there is no absolute pointer, so moving to a "
                           "screen position was skipped."))
            return False
        if human and p.pos is not None and ms > 0:
            path = human_path(p.pos, (x, y), max(2, min(500, int(ms / 8))))
            t0 = time.perf_counter()
            for k, (px, py) in enumerate(path, 1):
                if self._halt.is_set():
                    return False
                p.warp(px, py, ws)
                self._until(t0 + ms / 1000.0 * k / len(path))
        else:
            p.warp(x, y, ws)
        return True

    def _pset(self, code, down):
        p = self._pointer()
        if p is None:
            return
        p.button(code, down)
        if down:
            self.pheld.append(code)
        elif code in self.pheld:
            self.pheld.remove(code)

    def _pclick(self, how):
        code = {"left": BTN["Left"], "right": BTN["Right"],
                "middle": BTN["Middle"], "double": BTN["Left"]}.get(how)
        if code is None or self._pointer() is None:
            return
        self._sleep(self.HOVER_S)
        double = how == "double"
        press = min(self.m.press, 0.05) if double else self.m.press
        for i in range(2 if double else 1):
            if self._halt.is_set():
                return
            if i:
                self._sleep(0.06)
            self._pset(code, True)
            self._sleep(press)
            self._pset(code, False)

    def _screen(self):
        """A captura, ligada na primeira vez que um passo precisa dela."""
        cap = getattr(self.env, "screen", None)
        if cap is None or self._blind:
            return None
        if not self._seeing:
            try:
                if not cap.ensure():
                    raise VisionError(cap.error
                                      or _("The screen is not available."))
            except VisionError as e:
                self._blind = True
                self._report(str(e))
                return None
            cap.acquire()
            self._seeing = True
        return cap

    def _check(self, st, frames):
        """Onde está agora o que o passo procura (ponto lógico), ou None."""
        if st.get("kind", st["op"]) == "image":
            tpl = load_image(st["path"])
            if tpl is None:
                raise VisionError(_("The image {path} could not be read.",
                                    path=st["path"]))
            hit = locate(frames, tpl, st["conf"], st.get("region"))
            return hit[:2] if hit else None
        if st.get("region"):
            return pixel_search(frames, st["region"], parse_color(st["color"]),
                                st["tol"], st.get("scan") == "cols",
                                bool(st.get("rtl")), bool(st.get("btt")))
        if color_close(pixel_at(frames, st["x"], st["y"]),
                       parse_color(st["color"]), st["tol"]):
            return (st["x"], st["y"])
        return None

    def _look(self, st):
        """Espera a condição do passo: (aconteceu, ponto achado)."""
        cap = self._screen()
        if cap is None:
            return False, None
        want = st["want"] == "seen"
        deadline = time.perf_counter() + st["wait"] / 1000.0
        while True:
            frames = cap.frames()
            if not frames:
                # compartilhamento encerrado no meio: nada de "sumiu" falso
                self._blind = True
                self._report(cap.error or _("Screen sharing stopped."))
                return False, None
            try:
                hit = self._check(st, frames)
            except VisionError as e:
                self._report(str(e))
                return False, None
            if (hit is not None) == want:
                return True, hit
            left = deadline - time.perf_counter()
            if left <= 0 or self._halt.is_set():
                return False, None
            self._rest(min(self.POLL_S, left))

    # ----------------------------------------------------------- janelas --
    WINDOW_POLL_S = 1.0          # cada pergunta ao KWin custa uns 30 ms

    def _find_window(self, st, focus=False):
        """{"found": ...} da janela do passo, ou None sem como perguntar."""
        kw = getattr(self.env, "windows", None)
        info = None
        if kw is not None:
            try:
                info = kw.find(st["match"], focus)
            except Exception:                      # noqa: BLE001
                info = None
        if not isinstance(info, dict):
            self._report(_("windows can only be checked on KDE Plasma, "
                           "through KWin, so the window step took its else "
                           "path."))
            return None
        return info

    def _window(self, st):
        """Espera a janela abrir (ou fechar) no tempo do passo; foca."""
        want = st["want"] == "seen"
        focus = want and st["act"] == "focus"
        deadline = time.perf_counter() + st["wait"] / 1000.0
        while True:
            info = self._find_window(st, focus)
            if info is None:
                return False
            if bool(info.get("found")) == want:
                return True
            left = deadline - time.perf_counter()
            if left <= 0 or self._halt.is_set():
                return False
            self._rest(min(self.WINDOW_POLL_S, left))

    # ------------------------------------------------------------ vigias --
    # Um passo Vigiar arma uma checagem que corre por baixo dos passos
    # seguintes, como um SetTimer com ImageSearch no AutoHotkey: a cada tanto,
    # olha; se viu, age (clica) e segue, ou leva o macro para outro passo.
    # Só olha quando o macro está esperando (passo Esperar, procura na tela,
    # intervalo entre toques) e entre um passo e outro, nunca com tecla ou
    # botão no meio do aperto.
    def _rest(self, seconds):
        if seconds > 0:
            self._rest_until(time.perf_counter() + seconds)

    def _rest_until(self, deadline):
        """Dorme até o deadline atendendo aos vigias que vencerem no meio.
        Um vigia que leva a outro passo sai daqui por _Jump."""
        clock = time.perf_counter
        while not self._halt.is_set():
            now = clock()
            due = min((w[2] for w in self.watches.values()), default=None)
            if due is not None and due <= now:
                self._watch(now)
                continue
            if now >= deadline:
                return
            self._halt.wait((deadline if due is None else min(deadline, due))
                            - now)

    def _watch(self, now=None):
        """Os vigias vencidos olham agora, em ordem de passo."""
        now = time.perf_counter() if now is None else now
        frames = None
        for idx in sorted(self.watches):
            w = self.watches.get(idx)
            if w is None or w[2] > now or self._halt.is_set():
                continue
            st = w[0]
            w[2] = now + w[1]
            if st["kind"] == "window":
                info = self._find_window(st)
                if info is None:
                    w[2] = math.inf                # sem KWin: desarma
                    continue
                hit = (0, 0) if info.get("found") else None
            else:
                if frames is None:
                    cap = self._screen()
                    frames = cap.frames() if cap is not None else []
                    if cap is not None and not frames:
                        self._blind = True
                        self._report(cap.error or _("Screen sharing stopped."))
                if not frames:
                    w[2] = math.inf
                    continue
                try:
                    hit = self._check(st, frames)
                except VisionError as e:
                    self._report(str(e))
                    w[2] = math.inf
                    continue
            if (hit is not None) != (st["want"] == "seen"):
                continue
            if st["want"] == "seen" and st["act"] != "none" \
                    and st["kind"] != "window":
                if self._go(hit[0], hit[1], st.get("human"),
                            st.get("ms", 0.0)):
                    self._pclick(st["act"])
                frames = None              # a tela mudou com o clique
            to = self._flow(st, True)
            if to is not None:
                raise _Jump(to)

    def _let_go(self):
        """Solta tudo o que a sequência apertou e não soltou."""
        for code in reversed(self.held[:]):
            try:
                self._set(code, False)
            except OSError:
                pass
        self.held = []
        for code in reversed(self.pheld[:]):
            try:
                self._pset(code, False)
            except OSError:
                pass
        self.pheld = []

    def fill(self, text):
        """{run}, {time} e os contadores do macro, num texto de aviso."""
        def sub(mo):
            key = mo.group(1).lower()
            if key == "run":
                return str(self.count + 1)
            if key == "time":
                return clock_text(time.perf_counter() - self.began)
            if key in self.counters:
                return str(self.counters[key])
            return mo.group(0)
        return FIELD_RE.sub(sub, text)

    def _flow(self, st, ok):
        key = "ok" if ok else "fail"
        how = st[key]
        if how == "stop":
            return self.STOP
        if how == "restart":
            return 0
        if how == "jump":
            return st[key + "_to"] - 1
        return None

    def _notify(self, st):
        text = self.fill(st["text"]) if st["text"] else self.m.name
        if not st.get("url"):
            say = getattr(self.env, "say", None)
            if say:
                say(self.m.name, text)
            return
        shots = []
        if st.get("shot"):
            cap = self._screen()
            if cap is not None:
                try:
                    shots = [png_bytes(f.rgb) for f in cap.frames()]
                except VisionError as e:
                    self._report(str(e))

        def send():
            r = WEBHOOK(st["url"], text, shots)
            if not (isinstance(r, int) and 200 <= r < 300):
                self._report(_("the webhook did not take the message ({err}).",
                               err=getattr(r, "reason", None) or r))
        threading.Thread(target=send, daemon=True,
                         name="wayclick-webhook").start()

    def _step(self, st, i=0):
        """Faz o passo. Devolve para onde ir (índice) quando não é o
        próximo, ou STOP."""
        op = st["op"]
        if op == "tap":
            self._tap(st["code"], st.get("times", 1))
        elif op == "down":
            self._set(st["code"], True)
        elif op == "up":
            self._set(st["code"], False)
        elif op == "wait":
            ms = st["ms"]
            if st.get("max"):
                ms = random.uniform(ms, st["max"])
            self._rest(ms / 1000.0)
        elif op == "text":
            for ch in st["text"]:
                if self._halt.is_set():
                    return
                code, shift = CHAR_KEYS.get(ch, (0, False))
                if not code:
                    continue
                if shift:
                    self._set(KEYS["Left Shift"], True)
                self._tap(code)
                if shift:
                    self._set(KEYS["Left Shift"], False)
        elif op == "wheel":
            self._wheel(st["dir"], st["times"])
        elif op == "move" and self.mouse is not None:
            self.mouse.move(st["dx"], st["dy"])
        elif op == "point":
            x, y, r = st["x"], st["y"], st.get("spread", 0)
            if r:
                x, y = x + random.randint(-r, r), y + random.randint(-r, r)
            if self._go(x, y, st.get("human"), st.get("ms", 0.0)):
                self._pclick(st["click"])
        elif op in ("image", "pixel"):
            ok, at = self._look(st)
            if ok and at is not None and st["want"] == "seen" \
                    and st["act"] != "none" and not self._halt.is_set():
                if self._go(at[0], at[1], st.get("human"),
                            st.get("ms", 0.0)):
                    self._pclick(st["act"])
            return self._flow(st, ok)
        elif op == "notify":
            self._notify(st)
        elif op == "open":
            self._open(st["url"])
        elif op == "watch":
            every = st["every"] / 1000.0
            if st["kind"] == "window":
                every = max(every, self.WINDOW_POLL_S)
            self.watches[i] = [st, every, time.perf_counter()]
        elif op == "count":
            n = self.counters.get(st["name"], 0) + 1
            self.counters[st["name"]] = n
            if st["every"] and n % st["every"] == 0:
                return self._flow(st, True)
        elif op == "window":
            return self._flow(st, self._window(st))
        return None

    def run(self):
        clock = time.perf_counter
        steps = self.m.steps
        try:
            nxt = clock()
            while not self._halt.is_set():
                began = clock()
                i = 0
                while i < len(steps) and not self._halt.is_set():
                    try:
                        if self.watches:
                            self._watch()
                        to = self._step(steps[i], i)
                    except _Jump as j:
                        # um vigia interrompeu: o que estava apertado é
                        # solto, e os vigias valem até ele levar a outro lugar
                        to = j.to
                        self.watches.clear()
                        self._let_go()
                    if to is None:
                        i += 1
                    elif to == self.STOP:
                        self.stop()
                    else:
                        if to <= i:
                            self._sleep(self.MIN_JUMP_S)
                        i = max(0, to)
                        # vigia que fica depois do destino volta a ser
                        # armado quando a sequência passar por ele de novo
                        for idx in [k for k in self.watches if k >= i]:
                            del self.watches[idx]
                self.watches.clear()
                self.count += 1
                if not self.loop or self._halt.is_set():
                    break
                if self.m.limit and self.count >= self.m.limit:
                    break
                if self.m.period > 0:
                    nxt += self.m.period
                    self._until(nxt)
                    if clock() - nxt > 0.05:      # atrasou demais: ressincroniza
                        nxt = clock()
                elif clock() - began < self.MIN_LOOP_S:
                    self._until(began + self.MIN_LOOP_S)
            if self.keep:
                self._halt.wait()      # sem repetir: segura até mandarem parar
        except OSError:
            pass                         # device fechado no meio
        finally:
            self._let_go()
            if self._seeing:
                self._seeing = False
                self.env.screen.release()
            if self.on_done:
                self.on_done(self)


class MacroEngine(QObject):
    """Escuta os atalhos de todos os macros, pelo hub, e roda cada um.

    A decisão acontece na thread do hub, sem passar pela da UI: apertar F1 com
    a janela travada num diálogo ainda liga o macro, e a latência é a do
    relay. Os sinais só avisam a UI para redesenhar.
    """
    changed = Signal()
    toggled = Signal(bool)
    message = Signal(str, str)      # (título, texto) de um passo Notificar
    problem = Signal(str)           # algo impediu um passo de acontecer

    def __init__(self, outputs, hub=None):
        super().__init__()
        self.outputs = outputs          # () -> (teclado virtual, mouse virtual)
        self.pointer = None             # AbsolutePointer, que a App cria
        self.screen = None              # ScreenCapture
        self.workspace = None           # (x, y, w, h) lógico de todas as telas
        self.windows = None             # KWinBridge: find(texto, focar)
        self.dock = None                # Dock: macros que jogam dentro dela
        self.hub = hub or HUB()
        self.lock = threading.Lock()
        self.active = False
        self.macros = ()
        self.by_code = {}
        self.runners = {}
        self.held = {}
        self.mice = None
        # (código) -> bool: é a tecla do Suspend agora. Ela é do Suspend, e um
        # macro com a mesma tecla não roda junto (se rodasse, desligar os
        # macros ligaria esse no mesmo aperto)
        self.exempt = None
        self.hub.add(self)

    def configure(self, macros, active):
        compiled = tuple(CompiledMacro(m) for m in macros
                         if m.get("enabled") and m.get("steps")
                         and (m.get("code") or m.get("mode") == "always"))
        new = {c.id: c for c in compiled}
        with self.lock:
            stop = [r for mid, r in self.runners.items()
                    if not active or mid not in new
                    or new[mid].spec != r.m.spec]
            self.active = bool(active)
            self.macros = compiled
            by_code = {}
            for c in compiled:
                if c.code and c.mode != "always":
                    by_code.setdefault(c.code, []).append(c)
            self.by_code = by_code
            self.mice = "all" if self.active and any(
                is_button(code) for code in by_code) else None
            always = [c for c in compiled if c.mode == "always"] \
                if self.active else []
        for r in stop:
            r.stop()
        for c in always:
            self._start(c, only_if_idle=True)
        self.hub.changed()
        self.changed.emit()

    def needs_keyboard(self):
        return self.active and any(c.uses_keys for c in self.macros)

    def claims(self, code):
        """Um macro ligado dispara com esta tecla, com os modificadores que
        estão seguros agora (a mesma conta de on_key). Roda na thread do hub."""
        if not self.active:
            return False
        ms = self.by_code.get(code)
        if not ms or (self.exempt and self.exempt(code)):
            return False
        mods = self.hub.mods_active()
        return any(m.mods <= mods for m in ms)

    # chamados das threads dos macros; os sinais chegam na da UI
    def report(self, text):
        self.problem.emit(text)

    def say(self, title, text):
        self.message.emit(title, text)

    def wants_grab(self, d):
        if not self.active:
            return False
        for code, ms in self.by_code.items():
            if d.has(code) and any(m.block for m in ms):
                if is_button(code) or d.kbd:
                    return True
        return False

    def on_key(self, d, code, value):
        if not self.active:
            return False
        ms = self.by_code.get(code)
        if not ms:
            return False
        if value == 1:
            if CAPTURE.is_set() or (self.exempt and self.exempt(code)):
                return False
            mods = self.hub.mods_active()
            hit = [m for m in ms if m.mods <= mods]
            # Ctrl+F1 e F1 no mesmo teclado: com Ctrl, vale só o mais específico
            hit = [m for m in hit if not any(m.mods < o.mods for o in hit)]
            if not hit:
                return False
            lift = set()
            for m in hit:
                if m.block and m.mods and (m.uses_keys or m.clicks):
                    lift |= m.mods
            if lift:
                self.hub.lift_mods(lift)
            for m in hit:
                self._press(m)
            self.held[code] = [m.id for m in hit if m.mode == "hold"]
            return any(m.block for m in hit)
        if value == 0:
            for mid in self.held.pop(code, []):
                with self.lock:
                    r = self.runners.get(mid)
                if r:
                    r.stop()
        return False

    def _alive(self, mid):
        r = self.runners.get(mid)
        return r if r is not None and r.is_alive() and not r.stopping else None

    def _press(self, m):
        with self.lock:
            r = self._alive(m.id)
        if m.mode == "toggle":
            if r:
                r.stop()
                self.toggled.emit(False)
            elif self._start(m):
                self.toggled.emit(True)
        elif not r:
            self._start(m)

    def _start(self, m, only_if_idle=False):
        dock = self.dock if m.dock else None
        if m.dock and (dock is None or not dock.alive()):
            # o "sempre" tenta de novo sozinho quando a Doca abre; o atalho
            # avisa, já que alguém apertou e nada aconteceu
            if not only_if_idle:
                self.report(f"{m.name}: " + _(
                    "the Dock is not open, so the macro did not start."))
            return None
        kb, mouse = (None, None) if dock else self.outputs()
        loop = m.repeat and m.mode != "once"
        keep = not m.repeat and m.mode in ("toggle", "hold", "always")
        with self.lock:
            if only_if_idle and self._alive(m.id):
                return None
            if dock:
                r = DockRunner(m, dock, loop, keep, self._done,
                               env=DockEnv(self, dock))
            else:
                r = MacroRunner(m, kb, mouse, loop, keep, self._done,
                                env=self)
            self.runners[m.id] = r
        r.start()
        self.changed.emit()
        return r

    def stop_dock(self):
        """A Doca fechou: os macros dela param (o que dormia não ia notar
        sozinho até o próximo passo)."""
        with self.lock:
            runners = [r for r in self.runners.values()
                       if isinstance(r, DockRunner)]
        for r in runners:
            r.stop()

    def _done(self, r):
        with self.lock:
            if self.runners.get(r.m.id) is r:
                del self.runners[r.m.id]
        self.changed.emit()

    def running(self):
        with self.lock:
            return [r.m for r in self.runners.values()
                    if r.is_alive() and not r.stopping]

    def stop_all(self, join=True):
        with self.lock:
            runners = list(self.runners.values())
        for r in runners:
            r.stop()
        if join:
            for r in runners:
                r.join(timeout=0.5)

    def close(self):
        self.active = False
        self.stop_all()
        self.hub.remove(self)


# ---------------------------------------------------------- gravador ----
# Gravar em vez de montar passo a passo: a pessoa joga, o WayClick anota, e o
# resultado vira passos comuns, que dá para editar. Movimento do mouse sem
# clique não entra; o clique entra com a posição, que só o KWin sabe.
TAP_HOLD_S = 0.25        # apertou e soltou antes disso, sem nada no meio: toque
CLICK_MOVE_PX = 6        # andou menos que isso entre apertar e soltar: clique
DRAG_ASK_S = 0.15        # segurou mais que isso: pergunta onde soltou
WHEEL_JOIN_S = 0.25      # giros seguidos da roda viram um passo só
POINT_OF = {BTN["Left"]: "left", BTN["Right"]: "right",
            BTN["Middle"]: "middle"}


class MacroRecorder(QObject):
    """Anota teclas, botões (com a posição do clique) e a roda, para virar
    passos com recorded_steps().

    Inscrito no hub como os macros, mas só olha: nada some, a não ser a
    tecla de parar. A posição vem do KWin, pedida no instante do aperto
    por uma thread própria: a do hub repassa as teclas de todo mundo e não
    pode ficar esperando o compositor.
    """
    finished = Signal()             # a tecla de parar foi apertada

    def __init__(self, hub=None, stop_code=0, cursor=None):
        super().__init__()
        self.hub = hub or HUB()
        self.stop_code = stop_code
        self.cursor = cursor        # () -> (x, y) ou None; None: sem posição
        self.events = []            # [t, "key" | "wheel", código, valor, pos]
        self.active = False
        self.mice = None
        self._t0 = 0.0
        self._down_at = {}
        self._asks = []
        self._ask_cv = threading.Condition()
        self._asker = None

    def start(self):
        self.events = []
        self._down_at = {}
        self._t0 = time.perf_counter()
        self.active = True
        self.mice = "all"
        if self.cursor is not None:
            self._asker = threading.Thread(target=self._ask_loop, daemon=True,
                                           name="wayclick-recorder")
            self._asker.start()
        self.hub.add(self)

    def stop(self):
        """Para de anotar e espera as posições que faltam chegar."""
        self.active = False
        self.mice = None
        self.hub.remove(self)
        if self._asker is not None:
            with self._ask_cv:
                self._asks.append(None)
                self._ask_cv.notify()
            self._asker.join(timeout=5.0)
            self._asker = None
        return list(self.events)

    def _ask_loop(self):
        while True:
            with self._ask_cv:
                while not self._asks:
                    self._ask_cv.wait()
                ev = self._asks.pop(0)
            if ev is None:
                return
            try:
                ev[4] = self.cursor()
            except Exception:                      # noqa: BLE001
                ev[4] = None

    def _ask(self, ev):
        with self._ask_cv:
            self._asks.append(ev)
            self._ask_cv.notify()

    # ---------------------------------------- inscrito do hub (thread dele)
    def wants_grab(self, d):
        return (self.active and bool(self.stop_code) and d.kbd
                and d.has(self.stop_code))

    def on_key(self, d, code, value):
        if not self.active or CAPTURE.is_set():
            return False
        if code == self.stop_code:
            if value == 1:
                self.active = False
                self.finished.emit()
            return True
        if value not in (0, 1):
            return False                 # repetição automática do teclado
        t = time.perf_counter() - self._t0
        ev = [t, "key", code, value, None]
        self.events.append(ev)
        if is_button(code) and self.cursor is not None:
            if value == 1:
                self._down_at[code] = t
                self._ask(ev)
            elif t - self._down_at.pop(code, t) > DRAG_ASK_S:
                self._ask(ev)            # segurou: pode ter arrastado
        return False

    def on_rel(self, d, code, value):
        if self.active and not CAPTURE.is_set() and value:
            self.events.append([time.perf_counter() - self._t0, "wheel", code,
                                value, None])


def recorded_steps(events, pauses=True):
    """Eventos do gravador -> passos. Tecla que desce e sobe logo, sem nada
    no meio, é um toque; botão com posição vira clique naquela posição (ou
    arrasto, se soltou longe); giros seguidos da roda, um passo; e entre uma
    ação e outra, a espera que houve, em passos de 10 ms."""
    steps, last = [], [None]
    n = len(events)
    used = set()
    pressed = {}                 # código -> posição onde apertou (ou None)

    def gap(t):
        if pauses and last[0] is not None:
            ms = round((t - last[0]) * 100) * 10.0
            if ms >= 10:
                steps.append({"op": "wait", "ms": ms})
        last[0] = t

    for i, (t, kind, code, value, pos) in enumerate(events):
        if i in used:
            continue
        if kind == "wheel":
            times, tl, j = 1, t, i + 1
            while (j < n and events[j][1] == "wheel" and events[j][2] == code
                   and (events[j][3] > 0) == (value > 0)
                   and events[j][0] - tl <= WHEEL_JOIN_S):
                used.add(j)
                times, tl, j = times + 1, events[j][0], j + 1
            gap(t)
            if code == REL_HWHEEL:
                way = "right" if value > 0 else "left"
            else:
                way = "up" if value > 0 else "down"
            steps.append({"op": "wheel", "dir": way, "times": min(100, times)})
            continue
        if value == 1:
            nxt = events[i + 1] if i + 1 < n else None
            quick = (nxt is not None and nxt[1] == "key" and nxt[2] == code
                     and nxt[3] == 0 and nxt[0] - t <= TAP_HOLD_S)
            gap(t)
            if is_button(code) and pos is not None:
                far = quick and nxt[4] is not None and math.hypot(
                    nxt[4][0] - pos[0], nxt[4][1] - pos[1]) > CLICK_MOVE_PX
                if quick and not far and code in POINT_OF:
                    steps.append({"op": "point", "x": pos[0], "y": pos[1],
                                  "click": POINT_OF[code]})
                    used.add(i + 1)
                    continue
                steps.append({"op": "point", "x": pos[0], "y": pos[1],
                              "click": "none"})
                if quick and not far:
                    steps.append({"op": "tap", "code": code})
                    used.add(i + 1)
                    continue
                steps.append({"op": "down", "code": code})
                pressed[code] = pos
                continue
            if quick:
                steps.append({"op": "tap", "code": code})
                used.add(i + 1)
                continue
            steps.append({"op": "down", "code": code})
            pressed[code] = pos
            continue
        if code not in pressed:
            continue                     # soltou o que já estava apertado
        at = pressed.pop(code)
        gap(t)
        if pos is not None and at is not None and math.hypot(
                pos[0] - at[0], pos[1] - at[1]) > CLICK_MOVE_PX:
            steps.append({"op": "point", "x": pos[0], "y": pos[1],
                          "click": "none"})
        steps.append({"op": "up", "code": code})
    for code in pressed:                 # ainda apertado quando parou
        steps.append({"op": "up", "code": code})
    return [x for x in (normalize_step(st) for st in steps) if x][:MAX_STEPS]


# ------------------------------------------------------------- farm -----
# O que um macro de farm de tower defense faz, com os passos comuns: focar o
# jogo (e reabrir, se fechou), clicar os botões do lobby em ordem, jogar a
# estratégia com vigias por baixo (pular onda, reconectar, fim da partida),
# clicar o botão do fim, avisar, e de novo. O assistente só monta a lista;
# depois é um macro como qualquer outro, que dá para editar.
ROBLOX_GAME_RE = re.compile(
    r"^https?://(?:www\.|web\.)?roblox\.com/games/(?:start\?(?:.*&)?"
    r"placeId=)?(\d+)", re.I)
ROBLOX_PRIVATE_RE = re.compile(
    r"[?&]privateServerLinkCode=([\w-]{1,200})(?:&|#|$)", re.I)


def farm_link(text):
    """O link que reabre o jogo. Página de jogo do Roblox
    (roblox.com/games/ID/...) vira roblox://placeId=ID, o formato que a
    página de deep links do Roblox documenta para abrir direto no app, e o
    link de um servidor privado (...?privateServerLinkCode=CÓDIGO) leva o
    código junto, como linkCode, o parâmetro da mesma página para isso; o
    resto fica como veio."""
    text = (text or "").strip()
    m = ROBLOX_GAME_RE.match(text)
    if not m:
        return text
    code = ROBLOX_PRIVATE_RE.search(text)
    return f"roblox://placeId={m.group(1)}" + (
        f"&linkCode={code.group(1)}" if code else "")


def farm_macro(spec):
    """O macro que o assistente monta. spec: name, code, key, window, link,
    lobby (imagens, em ordem), lobby_wait (s), strategy (passos), press_ms,
    skip, reconnect, over (imagem do fim, obrigatória), over_wait (s), hook,
    report, shot, limit. ValueError se não der um macro inteiro."""
    win = spec.get("window") or "Sober"
    link = farm_link(spec.get("link"))
    if not spec.get("over"):
        raise ValueError("the end of match image is required")
    steps, at = [], {}

    def mark(label):
        at[label] = len(steps) + 1

    def put(st):
        steps.append(st)

    if link:
        put({"op": "window", "match": win, "wait": 0, "ok": "jump",
             "ok_to": "@focus", "fail": "next"})
        put({"op": "open", "url": link})
        put({"op": "window", "match": win, "wait": 120000, "fail": "stop"})
    mark("@focus")
    put({"op": "window", "match": win, "act": "focus", "wait": 5000,
         "fail": "restart" if link else "stop"})
    if link:
        put({"op": "watch", "kind": "window", "match": win, "want": "gone",
             "every": 5000, "ok": "restart"})
    if spec.get("reconnect"):
        put({"op": "watch", "path": spec["reconnect"], "every": 2000,
             "act": "left", "ok": "restart"})
    wait = max(1, int(spec.get("lobby_wait") or 60)) * 1000.0
    for k, path in enumerate(p for p in spec.get("lobby") or () if p):
        # o primeiro botão do lobby que não aparece quer dizer que a partida
        # já começou (Jogar de novo cai direto nela): pula para a estratégia
        put({"op": "image", "path": path, "wait": wait, "act": "left",
             "fail": "jump" if k == 0 else "restart", "fail_to": "@match"})
    mark("@match")
    if spec.get("skip"):
        put({"op": "watch", "path": spec["skip"], "every": 2000,
             "act": "left"})
    over = spec.get("over") or ""
    put({"op": "watch", "path": over, "every": 2000, "act": "none",
         "ok": "jump", "ok_to": "@end"})
    base = len(steps)
    for st in spec.get("strategy") or ():
        st = dict(st)
        for key in ("ok", "fail"):
            if st.get(key) == "jump":
                st[key + "_to"] = st.get(key + "_to", 1) + base
            elif st.get(key) == "restart":
                # recomeçar a estratégia, não o farm inteiro
                st[key], st[key + "_to"] = "jump", base + 1
        put(st)
    mark("@end")
    put({"op": "image", "path": over,
         "wait": max(1, int(spec.get("over_wait") or 30)) * 60000.0,
         "act": "left", "fail": "restart"})
    if spec.get("hook"):
        put({"op": "notify", "url": spec["hook"],
             "text": spec.get("report") or "{run}", "shot": spec.get("shot")})
    for st in steps:
        for key in ("ok_to", "fail_to"):
            if isinstance(st.get(key), str):
                st[key] = at[st[key]]
    if len(steps) > MAX_STEPS:
        raise ValueError("too many steps")
    m = normalize_macro({
        "name": spec.get("name") or "Farm", "enabled": True,
        "code": spec.get("code") or KEYS["F6"],
        "key": spec.get("key") or "F6", "mods": spec.get("mods") or [],
        "mode": "toggle", "repeat": True, "period_ms": 0.0,
        "press_ms": spec.get("press_ms", 0.0), "block": True,
        "limit": spec.get("limit", 0), "steps": steps,
        "dock": spec.get("dock") is True})
    if len(m["steps"]) != len(steps):
        # um passo incompleto sumiria e os saltos apontariam para o vizinho
        raise ValueError("a step is not complete")
    return m


# ---------------------------------------------------- compartilhar --------
# Um macro num arquivo, com as imagens dentro (base64): quem recebe abre e
# usa, sem pasta de imagens para copiar junto. Webhook não vem junto: um
# arquivo de outra pessoa mandaria prints da sua tela para o canal dela.
MACRO_FILE_EXT = ".wayclick"
MACRO_FILE_MAX = 64 << 20          # bytes; o arquivo é lido inteiro
MAX_FILE_IMAGES = 200
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
SAFE_LINKS = ("https://", "http://", "roblox://", "roblox-player:",
              "steam://")


def _no_hook(st):
    """Um passo Notificar sem o webhook: vira notificação da área de
    trabalho. Texto vazio (só o print ia para o canal) ganha um, senão o
    passo deixaria de existir e os saltos apontariam para o vizinho."""
    st.pop("url", None)
    st.pop("shot", None)
    st["text"] = st.get("text") or "Run {run}"
    return st


class _Packer:
    """Imagens de um arquivo .wayclick: cada caminho vira um nome, e o
    conteúdo vai em base64 (o mesmo arquivo uma vez só)."""

    def __init__(self):
        self.images, self.names, self.missing = {}, {}, []

    def __call__(self, path):
        if not path:
            return ""
        if path in self.names:
            return self.names[path] or ""
        try:
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
        except OSError:
            self.missing.append(path)
            self.names[path] = None
            return ""
        stem, ext = os.path.splitext(os.path.basename(path))
        ext = ext.lower() if ext.lower() in IMAGE_EXTS else ".png"
        name, n = stem + ext, 2
        while name in self.images and self.images[name] != data:
            name, n = f"{stem}-{n}{ext}", n + 1
        self.images[name] = data
        self.names[path] = name
        return name


def export_macros(macros):
    """(dados para json.dump, caminhos de imagem que não deu para ler,
    quantos webhooks ficaram de fora)."""
    pack, out, hooks = _Packer(), [], 0
    for m in macros:
        m = normalize_macro(m)
        for st in m["steps"]:
            if st["op"] == "notify" and st.get("url"):
                hooks += 1
                _no_hook(st)
            name = pack(st.get("path"))
            if name:
                st["path"] = name
        out.append(m)
    return ({"wayclick": 1, "app": f"WayClick {VERSION}", "macros": out,
             "images": pack.images}, pack.missing, hooks)


def _safe_image_name(name):
    stem, ext = os.path.splitext(os.path.basename(str(name)))
    stem = re.sub(r"[^\w.-]+", "-", stem).strip(".-")[:80] or "image"
    ext = ext.lower() if ext.lower() in IMAGE_EXTS else ".png"
    return stem, ext


def _save_file_images(images, folder, dry=False):
    """As imagens de um arquivo .wayclick (nome -> base64) na pasta de
    imagens: {nome no arquivo: caminho}. A mesma imagem não é copiada duas
    vezes, outra com o mesmo nome ganha um número, e o que não é imagem de
    verdade fica de fora. dry só confere, sem gravar nada."""
    saved = {}
    for name, b64 in list(images.items())[:MAX_FILE_IMAGES]:
        if not isinstance(name, str) or not isinstance(b64, str):
            continue
        try:
            raw = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error):
            continue
        if QImage.fromData(raw).isNull():
            continue                     # só imagem de verdade entra
        if dry:
            saved[name] = name
            continue
        stem, ext = _safe_image_name(name)
        os.makedirs(folder, exist_ok=True)
        path, n = os.path.join(folder, stem + ext), 2
        while os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    if fh.read() == raw:
                        break
            except OSError:
                pass
            path, n = os.path.join(folder, f"{stem}-{n}{ext}"), n + 1
        else:
            with open(path, "wb") as fh:
                fh.write(raw)
        saved[name] = path
    return saved


def import_macros(data, folder=None, dry=False):
    """Lê o que export_macros gravou. As imagens vão para a pasta de imagens
    (a mesma imagem não é copiada duas vezes; outra com o mesmo nome ganha
    um número), cada macro ganha id novo e chega desligado, webhooks saem,
    e um passo que abriria algo que não é web, Roblox ou Steam vira uma
    espera vazia (os saltos continuam apontando para o passo certo).
    Devolve (macros, avisos); dry só confere, sem gravar nada. ValueError
    se não for um arquivo de macros."""
    if not isinstance(data, dict) or data.get("wayclick") != 1 \
            or not isinstance(data.get("macros"), list):
        raise ValueError("not a WayClick macro file")
    notes = []
    images = data.get("images") if isinstance(data.get("images"), dict) \
        else {}
    saved = _save_file_images(images, folder or images_dir(), dry)
    macros, hooks, links, broken = [], 0, [], 0
    for m in data["macros"][:MAX_MACROS]:
        if not isinstance(m, dict):
            continue
        m = dict(m, id=new_macro_id(), enabled=False)
        steps = []
        raw_steps = m.get("steps") if isinstance(m.get("steps"), list) else []
        for st in raw_steps[:MAX_STEPS]:
            st = dict(st) if isinstance(st, dict) else {}
            if isinstance(st.get("path"), str):
                st["path"] = saved.get(st["path"], "")
            if st.get("op") == "notify" and st.get("url"):
                hooks += 1
                _no_hook(st)
            if st.get("op") == "open" and not str(
                    st.get("url") or "").lower().startswith(SAFE_LINKS):
                links.append(str(st.get("url"))[:80])
                st = {"op": "wait", "ms": 0.0}
            if normalize_step(st) is None:
                broken += 1
                st = {"op": "wait", "ms": 0.0}
            steps.append(st)
        m["steps"] = steps
        nm = normalize_macro(m)
        if nm["steps"]:
            macros.append(nm)
    if macros:
        notes.append(_("The macros come turned off: tick one in the list "
                       "to turn it on."))
    if hooks:
        notes.append(_("Webhooks are not imported ({n}): a file from someone "
                       "else could send pictures of your screen to their "
                       "channel. Add your own in the Notify steps.", n=hooks))
    for url in links:
        notes.append(_("A step that opens “{url}” became an empty wait: only "
                       "web, roblox:// and steam:// links are imported.",
                       url=url))
    if broken:
        notes.append(_("{n} steps could not be read (an image missing from "
                       "the file, for instance) and became empty waits.",
                       n=broken))
    return macros, notes


def read_macro_file(path):
    """O JSON de um arquivo .wayclick. OSError, ou ValueError se não for."""
    with open(path, "rb") as fh:
        raw = fh.read(MACRO_FILE_MAX + 1)
    if len(raw) > MACRO_FILE_MAX:
        raise ValueError("file too big")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, RecursionError) as e:
        raise ValueError(str(e)) from None


# ------------------------------------------------- importar AutoHotkey ----
# Lê um script .ahk (v1 ou v2) e transforma cada atalho num macro. Não é um
# interpretador: reconhece os padrões que quase todo script de jogo usa, e diz
# com número de linha o que ficou de fora, em vez de adivinhar.
#
#   F1:: toggle := !toggle, SetTimer Label, N / Off   -> liga/desliga, a cada N
#   F1:: toggle := !toggle, while toggle { ... }      -> liga/desliga, em laço
#   X:: while GetKeyState("X", "P") { ... }            -> enquanto segura
#   X:: Send ..., Click, Sleep                         -> uma vez por aperto
#   a::b                                              -> remapeia (enquanto segura)
#   SetTimer no início do script                      -> "sempre"
#
# Tempo como no Windows: lá o AutoHotkey arredonda SetTimer e Sleep para cima,
# para um múltiplo de 10 ou 15,6 ms ("Timer precision" nas páginas SetTimer e
# Sleep da documentação v1 e v2). Abaixo de um tique, então, o script rodava
# de verdade a cada 15,6 ms, e é assim que ele vem. SetTimer 1 tomado ao pé
# da letra manda mil teclas por segundo, e o jogo que lê menos que isso não as
# perde, enfileira: no Roblox (Sober), 0,5 s de "SpamE F1" a 1 ms virou 18 s
# de E chegando depois de desligar (tests/t_sober_kwin.py).
AHK_TICK_MS = 15.6
AHK_KEYS = {
    "space": 57, "enter": 28, "return": 28, "tab": 15, "esc": 1, "escape": 1,
    "backspace": 14, "bs": 14, "delete": 111, "del": 111, "insert": 110,
    "ins": 110, "home": 102, "end": 107, "pgup": 104, "pgdn": 109, "up": 103,
    "down": 108, "left": 105, "right": 106, "lshift": 42, "rshift": 54,
    "shift": 42, "lcontrol": 29, "lctrl": 29, "rcontrol": 97, "rctrl": 97,
    "control": 29, "ctrl": 29, "lalt": 56, "ralt": 100, "alt": 56,
    "lwin": 125, "rwin": 126, "capslock": 58, "numlock": 69, "scrolllock": 70,
    "appskey": 127, "printscreen": 99, "pause": 119,
    "numpad0": 82, "numpad1": 79, "numpad2": 80, "numpad3": 81, "numpad4": 75,
    "numpad5": 76, "numpad6": 77, "numpad7": 71, "numpad8": 72, "numpad9": 73,
    "numpaddot": 83, "numpaddel": 83, "numpadadd": 78, "numpadsub": 74,
    "numpadmult": 55, "numpaddiv": 98, "numpadenter": 96, "numpadins": 82,
    "numpadend": 79, "numpaddown": 80, "numpadpgdn": 81, "numpadleft": 75,
    "numpadclear": 76, "numpadright": 77, "numpadhome": 71, "numpadup": 72,
    "numpadpgup": 73,
    "volume_up": 115, "volume_down": 114, "volume_mute": 113,
    "media_play_pause": 164, "media_next": 163, "media_prev": 165,
    "media_stop": 166, "browser_back": 158, "browser_forward": 159,
    "browser_refresh": 173, "browser_home": 172,
    "lbutton": BTN["Left"], "rbutton": BTN["Right"], "mbutton": BTN["Middle"],
    "xbutton1": BTN["Side"], "xbutton2": BTN["Extra"],
}
for _i in range(1, 25):
    AHK_KEYS[f"f{_i}"] = KEYS[f"F{_i}"]
AHK_WHEEL = {"wheelup": "up", "wheeldown": "down", "wheelleft": "left",
             "wheelright": "right", "wu": "up", "wd": "down"}
# scancodes estendidos (sc1xx) que não coincidem com o código evdev
SC_EXT = {0x11C: 96, 0x11D: 97, 0x135: 98, 0x137: 99, 0x138: 100, 0x145: 69,
          0x147: 102, 0x148: 103, 0x149: 104, 0x14B: 105, 0x14D: 106,
          0x14F: 107, 0x150: 108, 0x151: 109, 0x152: 110, 0x153: 111,
          0x15B: 125, 0x15C: 126, 0x15D: 127}
VK_KEYS = {0x08: 14, 0x09: 15, 0x0D: 28, 0x10: 42, 0x11: 29, 0x12: 56,
           0x13: 119, 0x14: 58, 0x1B: 1, 0x20: 57, 0x21: 104, 0x22: 109,
           0x23: 107, 0x24: 102, 0x25: 105, 0x26: 103, 0x27: 106, 0x28: 108,
           0x2C: 99, 0x2D: 110, 0x2E: 111, 0x5B: 125, 0x5C: 126, 0x5D: 127,
           0x6A: 55, 0x6B: 78, 0x6D: 74, 0x6E: 83, 0x6F: 98, 0x90: 69,
           0x91: 70, 0xA0: 42, 0xA1: 54, 0xA2: 29, 0xA3: 97, 0xA4: 56,
           0xA5: 100, 0x01: BTN["Left"], 0x02: BTN["Right"],
           0x04: BTN["Middle"], 0x05: BTN["Side"], 0x06: BTN["Extra"]}
for _i, _n in enumerate((82, 79, 80, 81, 75, 76, 77, 71, 72, 73)):
    VK_KEYS[0x60 + _i] = _n
for _i in range(24):
    VK_KEYS[0x70 + _i] = KEYS[f"F{_i + 1}"]
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK_KEYS[ord(_c)] = KEYS[_c]
AHK_MODS = {"^": "ctrl", "!": "alt", "+": "shift", "#": "super"}
MOD_KEY = {"ctrl": 29, "alt": 56, "shift": 42, "super": 125}


def ahk_key(name):
    """Nome de tecla do AutoHotkey -> código evdev (None se desconhecido)."""
    n = (name or "").strip()
    low = n.lower()
    if low in AHK_KEYS:
        return AHK_KEYS[low]
    if len(n) == 1:
        hit = CHAR_KEYS.get(n.lower()) or CHAR_KEYS.get(n)
        return hit[0] if hit else None
    m = re.fullmatch(r"sc([0-9a-f]{1,3})", low)
    if m:
        sc = int(m.group(1), 16)
        if sc == 0x45:
            return 119                  # Pause é sc045 para o AHK; NumLock, sc145
        return SC_EXT.get(sc) or (sc if 1 <= sc <= 0x58 else None)
    m = re.fullmatch(r"vk([0-9a-f]{1,2})(?:sc[0-9a-f]{1,3})?", low)
    if m:
        return VK_KEYS.get(int(m.group(1), 16))
    return None


def _strip_comment(line):
    if line.lstrip().startswith(";"):
        return ""
    quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            quote = not quote
        elif ch == ";" and not quote and i and line[i - 1] in " \t":
            return line[:i].rstrip()
    return line


HOTKEY_RE = re.compile(
    r"^([#!^+<>*~$]*(?:[^\s:\"'(){}]+\s+&\s+[^\s:\"'(){}]+|[^\s\"'(){}]+?|.)"
    r"(?:\s+up)?)::(.*)$", re.I)
LABEL_RE = re.compile(r"^([A-Za-z_][\w]*):$")
FUNC_RE = re.compile(r"^([A-Za-z_]\w*)\(([^()]*)\)$")
CONTROL_RE = re.compile(r"^(if|else|while|loop|for|try|catch|finally|switch)"
                        r"\b", re.I)


def _ahk_tokens(text):
    """Linhas lógicas, sem comentários, com { e } em linhas próprias quando
    abrem ou fecham um bloco (o estilo "if (x) {" e "} else {")."""
    out, block = [], False
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for ln, raw in enumerate(text.split("\n"), 1):
        s = raw.strip()
        if block:
            if s.startswith("*/") or s.endswith("*/"):
                block = False
            continue
        if s.startswith("/*"):
            block = not s.endswith("*/") or s == "/*"
            continue
        s = _strip_comment(s).strip()
        while s:
            if s.startswith("}"):
                out.append((ln, "}"))
                s = s[1:].strip()
                continue
            if s.endswith("{") and s != "{":
                head = s[:-1].rstrip()
                if (CONTROL_RE.match(head) or head.endswith("::")
                        or FUNC_RE.match(head) or head.endswith(")")):
                    out.append((ln, head))
                    out.append((ln, "{"))
                    break
            out.append((ln, s))
            break
    return out


def _unquote(a):
    a = a.strip()
    if a.startswith("%") and not a.endswith("%"):
        a = a[1:].strip()               # v1 "% expressão": só a string simples
    if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'":
        body = a[1:-1]
        return body.replace('""', '"').replace('`"', '"').replace("`'", "'")
    return a


def _split_args(s):
    """Separa argumentos por vírgula, respeitando aspas e parênteses."""
    args, cur, depth, quote = [], "", 0, None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip() or args:
        args.append(cur.strip())
    return args


def _escape_text(s):
    """Sequências de escape do AHK: `n, `t, `;, ``."""
    return (s.replace("``", "\x00").replace("`n", "\n").replace("`t", "\t")
            .replace("`r", "").replace("`;", ";").replace("`,", ",")
            .replace("`%", "%").replace("\x00", "`"))


class AhkImport:
    """Resultado da importação: `macros` (normalizados) e `notes`, cada nota
    um (linha, texto-fonte, campos), traduzida só na hora de mostrar."""

    def __init__(self, text, folder=None):
        self.toks = _ahk_tokens(text)
        self.folder = folder            # onde o .ahk está (A_ScriptDir)
        self.notes = []
        self.macros = []
        self.suspend = None             # (código, mods, atalho) do F7::Suspend
        self.v2 = bool(re.search(r"^\s*#Requires\s+AutoHotkey\s+v?2", text,
                                 re.I | re.M))
        self._label_n = 0
        self._conf_noted = False
        self.labels, self.funcs = {}, {}
        self.hotkeys = []       # (linha, spec, corpo | None, remap)
        self.auto = []
        self.press_ms = 0.0
        self.send_mode = "input"
        self._ifwin_noted = False
        self._tick_noted = False
        # Click X, Y e MouseMove X, Y contam da janela ativa no AutoHotkey,
        # a não ser com CoordMode Mouse, Screen
        self.screen_coords = bool(re.search(
            r"^\s*CoordMode\b[\s,(]*[\"']?Mouse[\"']?\s*,\s*[\"']?Screen",
            text, re.I | re.M))
        # o mesmo para PixelGetColor, PixelSearch e ImageSearch
        self.pixel_screen = bool(re.search(
            r"^\s*CoordMode\b[\s,(]*[\"']?Pixel[\"']?\s*,\s*[\"']?Screen",
            text, re.I | re.M))
        try:
            self._parse_top()
            self._analyze()
        except RecursionError:          # aninhamento absurdo: fica o que deu
            self.note(0, "the script is nested too deeply, the rest was "
                         "skipped")
            self.macros = self.macros[:MAX_MACROS]

    # ------------------------------------------------------ parsing --
    def note(self, ln, text, **fmt):
        item = (ln, text, fmt)
        if item not in self.notes:
            self.notes.append(item)

    def _is_def(self, t):
        if t.startswith(":") and "::" in t[1:]:
            return True
        return bool(HOTKEY_RE.match(t) or LABEL_RE.match(t))

    def _add_hotkey(self, ln, spec, body, remap, stack):
        """Atalhos empilhados (F1:: e F2:: na linha de baixo) dividem o corpo
        do último, como no AutoHotkey."""
        for sln, sspec in stack:
            self.hotkeys.append((sln, sspec, body, remap))
        stack.clear()
        self.hotkeys.append((ln, spec, body, remap))

    def _parse_top(self):
        toks, i = self.toks, 0
        stack = []
        while i < len(toks):
            ln, t = toks[i]
            if t.startswith("#"):
                self._directive(ln, t)
                i += 1
                continue
            if t.startswith(":") and "::" in t[1:]:
                self.note(ln, "hotstrings are not supported")
                i += 1
                continue
            m = HOTKEY_RE.match(t)
            if m and not t.lower().startswith(("if", "while")):
                spec, rest = m.group(1).strip(), m.group(2).strip()
                if not rest and i + 1 < len(toks) and toks[i + 1][1] == "{":
                    node, i = self._block(i + 1)
                    if i < len(toks) and toks[i][1].lower() == "return":
                        i += 1
                    self._add_hotkey(ln, spec, [node], None, stack)
                elif not rest:
                    body, j = self._until_return(i + 1)
                    if not body and j == i + 1 and j < len(toks) \
                            and HOTKEY_RE.match(toks[j][1]):
                        stack.append((ln, spec))
                        i = j
                        continue
                    i = j
                    self._add_hotkey(ln, spec, body, None, stack)
                else:
                    remap = self._remap_target(rest)
                    if remap:
                        self._add_hotkey(ln, spec, None, remap, stack)
                    else:
                        node, _j = self._stmt_from([(ln, rest)], 0)
                        self._add_hotkey(ln, spec, [node], None, stack)
                    i += 1
                continue
            m = LABEL_RE.match(t)
            if m:
                body, i = self._until_return(i + 1)
                self.labels[m.group(1).lower()] = body
                continue
            m = FUNC_RE.match(t)
            if m and i + 1 < len(toks) and toks[i + 1][1] == "{":
                node, i = self._block(i + 1)
                self.funcs[m.group(1).lower()] = [node]
                continue
            node, i = self._stmt(i)
            self.auto.append(node)

    def _directive(self, ln, t):
        low = t.lower()
        if low.startswith(("#if", "#hotif")) and len(low.split()) > 1:
            if not self._ifwin_noted:
                self._ifwin_noted = True
                self.note(ln, "window-specific hotkeys (#If) work in every "
                              "window here")

    def _remap_target(self, rest):
        """'a::b' remapeia; 'a::Send b' não. O alvo é um nome de tecla só,
        com modificadores na frente, e sem nada depois."""
        m = re.fullmatch(r"([#!^+]*)([^\s,(){}\"']+)", rest)
        if not m:
            return None
        name = m.group(2)
        if name.lower() in ("return", "reload", "exitapp", "suspend", "pause",
                            "click", "send", "sendinput"):
            return None
        code = ahk_key(name)
        if code is None:
            return None
        mods = [AHK_MODS[c] for c in m.group(1)]
        return code, mods, name

    def _until_return(self, i):
        body = []
        while i < len(self.toks):
            ln, t = self.toks[i]
            if self._is_def(t) or (FUNC_RE.match(t) and i + 1 < len(self.toks)
                                   and self.toks[i + 1][1] == "{"):
                break
            node, i = self._stmt(i)
            if node[0] == "return":
                break
            body.append(node)
        return body, i

    def _block(self, i):
        """tokens[i] é '{': lê até o '}' que fecha."""
        ln = self.toks[i][0]
        i += 1
        nodes = []
        while i < len(self.toks) and self.toks[i][1] != "}":
            node, i = self._stmt(i)
            nodes.append(node)
        return ("block", nodes, ln), i + 1

    def _stmt_from(self, toks, i):
        saved = self.toks
        self.toks = toks
        try:
            return self._stmt(i)
        finally:
            self.toks = saved

    def _stmt(self, i):
        ln, t = self.toks[i]
        low = t.lower()
        if t == "{":
            return self._block(i)
        if t == "}":
            return ("noop", None, ln), i + 1
        m = re.match(r"^(if|while)\b\s*(.*)$", t, re.I) \
            or re.match(r"^(if|while)(\(.*)$", t, re.I)
        if m:
            kind, cond = m.group(1).lower(), self._cond(m.group(2))
            body, i = self._sub(i + 1)
            if kind == "while":
                return ("while", cond, body, ln), i
            other = None
            if i < len(self.toks) and re.match(r"^else\b", self.toks[i][1], re.I):
                rest = self.toks[i][1][4:].strip()
                if rest:
                    toks = list(self.toks)
                    toks[i] = (self.toks[i][0], rest)
                    saved, self.toks = self.toks, toks
                    try:
                        other, i = self._stmt(i)
                    finally:
                        self.toks = saved
                else:
                    other, i = self._sub(i + 1)
            return ("if", cond, body, other, ln), i
        m = re.match(r"^loop\b\s*,?\s*(.*)$", t, re.I)
        if m:
            arg = m.group(1).strip()
            if arg and not re.fullmatch(r"\d+", arg):
                self.note(ln, "{what} is not supported, skipped", what=t)
                body, i = self._sub(i + 1)
                return ("noop", None, ln), i
            body, i = self._sub(i + 1)
            return ("loop", int(arg) if arg else None, body, ln), i
        if low in ("break", "continue"):
            return (low, None, ln), i + 1
        if low.startswith("return") or low in ("exit",):
            return ("return", None, ln), i + 1
        return ("cmd", self._cmd(ln, t), ln), i + 1

    def _sub(self, i):
        if i >= len(self.toks):
            return ("block", [], 0), i
        return self._stmt(i)

    @staticmethod
    def _cond(expr):
        e = expr.strip()
        while e.startswith("(") and e.endswith(")") and \
                e.count("(") == e.count(")"):
            e = e[1:-1].strip()
        neg = False
        m = re.fullmatch(r"(?:!|not\s+)\s*(.+)", e, re.I)
        if m:
            neg, e = True, m.group(1).strip()
            while e.startswith("(") and e.endswith(")"):
                e = e[1:-1].strip()
        m = re.fullmatch(r"GetKeyState\(\s*[\"']?([^\"',)]+)[\"']?\s*"
                         r"(?:,\s*[\"']?(\w*)[\"']?)?\s*\)", e, re.I)
        if m:
            return ("key", m.group(1).strip(), neg)
        # v2: if ImageSearch(&x, &y, ...) e if PixelSearch(...)
        m = re.fullmatch(r"(PixelSearch|ImageSearch)\s*\((.*)\)", e, re.I)
        if m:
            return ("scall", m.group(1).lower(), _split_args(m.group(2)),
                    not neg, expr.strip())
        # v2: if PixelGetColor(x, y) = 0xFF0000
        m = re.fullmatch(r"PixelGetColor\s*\((.*)\)\s*(=|==|!=|<>)\s*(.+)",
                         e, re.I)
        if m:
            want = m.group(2) in ("=", "==")
            return ("pgc", _split_args(m.group(1)), want != neg,
                    _unquote(m.group(3)), expr.strip())
        if re.fullmatch(r"(?i:true|1)", e):
            return ("const", not neg)
        if re.fullmatch(r"(?i:false|0)", e):
            return ("const", neg)
        m = re.fullmatch(r"([A-Za-z_]\w*)", e)
        if m:
            return ("var", m.group(1).lower(), not neg)
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*(=|==|!=|<>)\s*[\"']?(\w+)[\"']?",
                         e)
        if m:
            var, op, val = m.group(1).lower(), m.group(2), m.group(3).lower()
            want = op in ("=", "==")
            if neg:
                want = not want
            if val in ("u", "d"):
                return ("state", var, val == "d" if want else val != "d")
            if val in ("1", "true"):
                return ("var", var, want)
            if val in ("0", "false"):
                return ("var", var, not want)
            return ("cmp", var, want, val, expr.strip())
        return ("unknown", expr.strip())

    def _cmd(self, ln, t):
        m = re.fullmatch(r"(?:static\s+|global\s+|local\s+)?([A-Za-z_]\w*)\s*"
                         r"(:=|=|\^=)\s*(.*)", t, re.I)
        if m:
            var, op, val = m.group(1).lower(), m.group(2), m.group(3).strip()
            if op == "^=" or re.fullmatch(
                    rf"(?:!|not\s+)\s*\(?\s*{var}\s*\)?", val, re.I) \
                    or re.fullmatch(rf"{var}\s*\?\s*(0|false)\s*:\s*(1|true)",
                                    val, re.I):
                return ("flip", var)
            r = re.fullmatch(r"Random\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", val, re.I)
            if r:
                return ("random", var, int(r.group(1)), int(r.group(2)))
            r = re.fullmatch(r"(PixelGetColor|PixelSearch|ImageSearch)\s*"
                             r"\((.*)\)", val, re.I)
            if r:
                return ("screen", var, r.group(1).lower(),
                        _split_args(r.group(2)), True)
            return ("assign", var, val)
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)", t)
        called = bool(m)
        if m:
            name, args = m.group(1), _split_args(m.group(2))
        else:
            m = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:,\s*(.*)|\s+(.*))?", t)
            if not m:
                return ("unknown", t)
            name = m.group(1)
            rest = m.group(2) if m.group(2) is not None else (m.group(3) or "")
            rest = rest.strip()
            if rest.startswith('"') or rest.startswith("'") \
                    or rest.startswith("% "):
                args = _split_args(rest)
            elif name.lower() in ("send", "sendinput", "sendevent", "sendplay",
                                  "sendraw", "sendtext"):
                args = [rest]               # v1: o texto vai cru, vírgula e tudo
            else:
                args = _split_args(rest) if rest else []
        low = name.lower()
        if low in ("send", "sendinput", "sendevent", "sendplay", "sendraw",
                   "sendtext", "controlsend"):
            if low == "controlsend":
                args = args[1:] if len(args) > 1 else args
            text = _unquote(args[0]) if args else ""
            return ("send", text, low in ("sendraw", "sendtext"))
        if low in ("click", "mouseclick"):
            return (low, [_unquote(a) for a in args])
        if low == "sleep":
            return ("sleep", args[0].strip() if args else "")
        if low == "settimer":
            return ("settimer", _unquote(args[0]) if args else "",
                    args[1].strip() if len(args) > 1 else "")
        if low in ("gosub", "goto"):
            return ("call", _unquote(args[0]).lower() if args else "")
        if low == "setkeydelay":
            return ("keydelay", args)
        if low == "sendmode":
            return ("sendmode", _unquote(args[0]).lower() if args else "")
        if low == "random":
            if len(args) >= 3:
                try:
                    return ("random", args[0].strip().lower(),
                            int(args[1]), int(args[2]))
                except ValueError:
                    pass
            return ("unknown", t)
        if low == "getkeystate" and len(args) >= 2:
            return ("gks", args[0].strip().lower(), _unquote(args[1]))
        if low == "keywait":
            return ("keywait", _unquote(args[0]) if args else "")
        if low == "mousemove":
            return ("move", args)
        if low in ("pixelgetcolor", "pixelsearch", "imagesearch"):
            v2 = called or self.v2 or any(a.strip().startswith("&")
                                          for a in args)
            if low == "pixelgetcolor":
                if v2:                  # o v2 devolve a cor; sozinho é nada
                    return ("ignore", name)
                return ("screen", _unquote(args[0]).lower() if args else "",
                        low, args[1:], False)
            return ("screen", None, low, args, v2)
        if low in ("tooltip", "traytip", "soundbeep", "soundplay", "msgbox",
                   "setbatchlines", "setworkingdir", "setmousedelay",
                   "coordmode", "settitlematchmode", "detecthiddenwindows",
                   "setcapslockstate", "setnumlockstate", "setscrolllockstate",
                   "settimer_", "critical", "thread", "processsetpriority",
                   "process", "menu", "a_traymenu", "persistent",
                   "installkeybdhook", "installmousehook", "keyhistory",
                   "listlines", "sendlevel"):
            return ("ignore", name)
        if low in ("pause", "suspend", "reload", "exitapp"):
            return ("control", name, args)
        if not args and (low in self.labels or low in self.funcs
                         or m and t.endswith(")")):
            return ("call", low)
        return ("unknown", t)

    # ---------------------------------------------------- análise --
    def _body(self, name, depth=0):
        return self.labels.get(name) or self.funcs.get(name) or []

    def _walk(self, nodes, depth=0):
        """Todos os nós, entrando em blocos, ifs, laços e chamadas."""
        for n in nodes:
            yield n
            kind = n[0]
            if kind == "block":
                yield from self._walk(n[1], depth)
            elif kind == "if":
                yield from self._walk([n[2]] + ([n[3]] if n[3] else []), depth)
            elif kind in ("while", "loop"):
                yield from self._walk([n[2]], depth)
            elif kind == "cmd" and n[1][0] == "call" and depth < 4:
                yield from self._walk(self._body(n[1][1]), depth + 1)

    @staticmethod
    def _flat(nodes):
        out = []
        for n in nodes:
            if n[0] == "block":
                out.extend(AhkImport._flat(n[1]))
            elif n[0] != "noop":
                out.append(n)
        return out

    def _analyze(self):
        for n in self._walk(self.auto):
            if n[0] != "cmd":
                continue
            c = n[1]
            if c[0] == "keydelay" and len(c[1]) >= 2:
                try:
                    self.press_ms = max(0.0, float(c[1][1]))
                except ValueError:
                    pass
            elif c[0] == "sendmode":
                self.send_mode = c[1]
        if self.send_mode in ("input", "play"):
            self.press_ms = 0.0           # SetKeyDelay não vale no SendInput
        for ln, spec, body, remap in self.hotkeys:
            m = self._hotkey(ln, spec, body, remap)
            if m:
                self.macros.append(normalize_macro(m))
        for n in self._flat(self.auto):
            if n[0] == "cmd" and n[1][0] == "settimer":
                m = self._timer_macro(n[2], n[1], startup=True)
                if m:
                    m["mode"] = "always"
                    self.note(n[2], "{name} starts with the script, imported "
                                    "as an Always macro", name=m["name"])
                    self.macros.append(normalize_macro(m))
        self.macros = self.macros[:MAX_MACROS]

    def _parse_spec(self, ln, spec):
        s = spec.strip()
        if re.search(r"\s+up$", s, re.I):
            self.note(ln, "{hk}: release hotkeys (Up) are not supported",
                      hk=spec)
            return None
        if " & " in s:
            self.note(ln, "{hk}: two-key combinations are not supported",
                      hk=spec)
            return None
        mods, block, i = set(), True, 0
        while i < len(s) - 1 and s[i] in "#!^+<>*~$":
            ch = s[i]
            if ch in AHK_MODS:
                mods.add(AHK_MODS[ch])
            elif ch == "~":
                block = False
            i += 1
        name = s[i:]
        low = name.lower()
        if low in AHK_WHEEL:
            self.note(ln, "{hk}: the mouse wheel cannot be a hotkey here",
                      hk=spec)
            return None
        code = ahk_key(name)
        if code is None:
            self.note(ln, "{hk}: key {name} is unknown", hk=spec, name=name)
            return None
        label = trigger_label(code) if is_button(code) else \
            (CODE_NAMES.get(code) or name.upper())
        # sem nome: a lista mostra o atalho ("Ctrl+J"), mais claro que "^j"
        return {"code": code, "key": label, "mods": sorted(mods),
                "block": block, "name": ""}

    def _suspend_key(self, ln, spec, hk, nodes):
        """F7::Suspend vira a tecla do Suspend do WayClick. True quando o
        atalho era isso (e não vira macro)."""
        first = next((n for n in nodes if n[0] != "noop"), None)
        if not first or first[0] != "cmd" or first[1][0] != "control" \
                or first[1][1].lower() != "suspend":
            return False
        args = first[1][2] if len(first[1]) > 2 else []
        mode = _unquote(args[0]).strip().lower() if args else ""
        if mode == "permit":
            return False
        if is_button(hk["code"]):
            self.note(ln, "{hk}: a mouse button cannot be the Suspend hotkey "
                          "here", hk=spec)
            return True
        if self.suspend:
            self.note(ln, "{hk}: the Suspend hotkey is already {key}, this "
                          "one was left out", hk=spec, key=self.suspend[2])
            return True
        if mode in ("on", "1", "true", "off", "0", "false"):
            self.note(ln, "{hk}: Suspend {mode} became the Suspend hotkey, "
                          "which switches the macros on and off", hk=spec,
                      mode=args[0].strip())
        if len([n for n in nodes if n[0] != "noop"]) > 1:
            self.note(ln, "{hk}: only Suspend was imported, the rest of it "
                          "was left out", hk=spec)
        self.suspend = (hk["code"], hk["mods"], hotkey_label(hk))
        return True

    def _hotkey(self, ln, spec, body, remap):
        hk = self._parse_spec(ln, spec)
        if hk is None:
            return None
        if not remap and self._suspend_key(ln, spec, hk, self._flat(body)):
            return None
        base = dict(hk, press_ms=self.press_ms)
        if remap:
            code, mods, name = remap
            steps = [{"op": "down", "code": MOD_KEY[x]} for x in mods]
            steps.append({"op": "down", "code": code})
            target = hotkey_label({"code": code, "mods": mods,
                                   "key": key_label(code)})
            return dict(base, mode="hold", repeat=False, block=True,
                        steps=steps, name=f"{hotkey_label(hk)} → {target}")
        nodes = self._flat(body)
        flips = {n[1][1] for n in self._walk(nodes)
                 if n[0] == "cmd" and n[1][0] == "flip"}
        timers = [n for n in self._walk(nodes)
                  if n[0] == "cmd" and n[1][0] == "settimer"]
        m = self._from_timers(ln, spec, base, timers, flips)
        if m is None:
            m = self._from_loops(ln, spec, base, nodes, flips, hk)
        if m is None:
            m = self._from_toggle_if(ln, base, nodes, flips)
        if m is None:
            m = self._from_keywait(ln, base, nodes, hk)
        if m is None:
            steps = self._steps(nodes, ln)
            m = dict(base, mode="once", repeat=False, steps=steps)
        if m.get("steps"):
            m["steps"] = self._resolve(m["steps"])
        if not m.get("steps"):
            self.note(ln, "{hk}: nothing WayClick can do", hk=spec)
            return None
        clicks = {s["code"] for s in m["steps"]
                  if s["op"] in ("tap", "down") and is_button(s.get("code", 0))}
        if not m["block"] and hk["code"] in clicks:
            m["block"] = True
            self.note(ln, "{hk} clicks its own button, so it is hidden from "
                          "the app even with ~ (otherwise the clicks would be "
                          "lost)", hk=spec)
        return m

    def _timer_macro(self, ln, cmd, startup=False):
        _kind, label, period = cmd
        label = label.strip()
        arrow = re.fullmatch(r"\(\s*\)\s*=>\s*(.+)", label)
        if arrow:
            node, _j = self._stmt_from([(ln, arrow.group(1))], 0)
            body, name = [node], "timer"
        else:
            body, name = self._body(label.lower()), label
        p = self._period(period, ln)
        if p is None or p == "off":
            return None
        if not body:
            self.note(ln, "SetTimer: {name} was not found", name=label)
            return None
        steps = self._steps(self._flat(body), ln, ctx={"macro_loop": p > 0})
        if p < 0:
            return {"name": name, "mode": "once", "repeat": False,
                    "steps": self._resolve([{"op": "wait", "ms": -p}] + steps),
                    "press_ms": self.press_ms, "block": True}
        return {"name": name, "mode": "toggle", "repeat": True,
                "period_ms": float(p), "steps": self._resolve(steps),
                "press_ms": self.press_ms, "block": True}

    def _period(self, text, ln):
        """'50' -> 50, 'Off'/'0'/'Delete' -> 'off', '' ou 'On' -> 250 (o
        padrão do AHK), ternário 'toggle ? 1 : 0' -> o lado ligado."""
        t = _unquote(text.strip()).strip()
        m = re.fullmatch(r"\(?\s*(?:\(\s*\w+\s*:=\s*!\s*\w+\s*\)|\w+)\s*\)?\s*\?"
                         r"\s*[\"']?(-?\w+)[\"']?\s*:\s*[\"']?(-?\w+)[\"']?", t)
        if m:
            t = m.group(1)
        low = t.lower()
        if low in ("off", "delete", "0"):
            return "off"
        if low in ("", "on"):
            return 250
        try:
            p = int(float(t))
        except ValueError:
            self.note(ln, "SetTimer period {p} is not a number, used 250 ms",
                      p=text)
            return 250
        if 0 < p < AHK_TICK_MS:
            self.note(ln, "SetTimer {ms} ms: on Windows AutoHotkey rounds "
                          "timers up to 10 or 15.6 ms, so the script really "
                          "ran every 15.6 ms, and so does the macro. Keys "
                          "sent faster than the game reads them pile up and "
                          "keep arriving after the macro stops.", ms=p)
            return AHK_TICK_MS
        return p

    def _from_timers(self, ln, spec, base, timers, flips):
        starts = [t for t in timers if self._period(t[1][2], t[2]) != "off"]
        if not starts:
            if timers:
                self.note(ln, "{hk} only stops a timer; in WayClick the hotkey "
                              "that starts a macro also stops it", hk=spec)
                return {}
            return None
        t = starts[0]
        m = self._timer_macro(t[2], t[1])
        if not m:
            return {}
        toggled = bool(flips) or len(timers) > len(starts) \
            or "?" in t[1][2]
        if not toggled and m["mode"] == "toggle":
            self.note(ln, "{hk} only starts the timer; here the same hotkey "
                          "also stops it", hk=spec)
        return dict(base, mode=m["mode"], repeat=m["repeat"],
                    period_ms=m.get("period_ms", 0.0), steps=m["steps"],
                    name=m["name"] if m["name"] != "timer" else "")

    def _is_break_if(self, node, flips, hk):
        """'if !toggle break' ou 'if !GetKeyState(...) break' (ou o estilo v1,
        'GetKeyState, state, X, P' seguido de 'if state = U break')."""
        if node[0] != "if":
            return None
        inner = self._flat([node[2]])
        if not inner or inner[0][0] != "break":
            return None
        cond = node[1]
        if cond[0] == "var" and cond[1] in flips and not cond[2]:
            return "toggle"
        if cond[0] == "key" and cond[2]:
            return "hold"
        if cond[0] == "state" and not cond[2]:
            return "hold"
        return None

    def _from_loops(self, ln, spec, base, nodes, flips, hk):
        idx = next((i for i, n in enumerate(nodes)
                    if n[0] in ("while", "loop")), None)
        if idx is None:
            return None
        loop = nodes[idx]
        before = [n for n in nodes[:idx]
                  if not (n[0] == "cmd" and n[1][0] in ("flip", "assign"))]
        body = self._flat([loop[2]])
        mode = None
        if loop[0] == "while":
            cond = loop[1]
            if cond[0] == "var" and cond[2]:
                mode = "toggle"
            elif cond[0] == "key" and not cond[2]:
                mode = "hold"
            elif cond[0] == "const" and cond[1]:
                mode = "toggle"
            else:
                self.note(loop[3], "while {c} is not understood, skipped",
                          c=str(cond[-1]))
                return {}
        elif loop[1] is not None:
            reps = max(1, min(loop[1], 1000))
            steps = self._steps(before, ln)
            one = self._steps(body, loop[3])
            if one and len(one) * reps > MAX_STEPS:
                reps = max(1, MAX_STEPS // len(one))
                self.note(loop[3], "Loop {n} shortened to {m} repetitions",
                          n=loop[1], m=reps)
            steps += self._times(one, reps)
            steps += self._steps(nodes[idx + 1:], ln)
            return dict(base, mode="once", repeat=False, steps=steps)
        else:
            body2 = []
            for n in body:
                kind = self._is_break_if(n, flips, hk)
                if kind and mode is None:
                    mode = kind
                    continue
                if n[0] == "cmd" and n[1][0] == "gks":
                    continue
                body2.append(n)
            body = body2
            if mode is None:
                mode = "toggle"
                self.note(loop[3], "{hk}: an endless loop; here the hotkey "
                                   "turns it on and off", hk=spec)
        pre = self._steps(before, ln)
        dropped = [s for s in pre if s["op"] != "down"]
        if dropped:
            self.note(ln, "{hk}: what runs before the loop was left out",
                      hk=spec)
        steps = [s for s in pre if s["op"] == "down"] + \
            self._steps(body, loop[3], ctx={"macro_loop": True})
        waits = [s["ms"] for s in steps if s["op"] == "wait" and s["ms"] > 0]
        if waits and not base.get("press_ms") and any(
                s["op"] in ("tap", "text") for s in steps):
            # o SendInput aperta e solta de uma vez; aqui cada aperto fica
            # seguro pela menor espera do laço (no máximo 40 ms), para o jogo
            # ver a tecla sem o laço perder o ritmo do script
            press = round(min(KeyMacro.HOLD_S * 1000.0, min(waits)), 1)
            base = dict(base, press_ms=press)
            self.note(loop[3], "{hk}: each press is held {ms} ms, as long as "
                               "the shortest wait of the loop, so the game "
                               "sees it and the loop keeps the script's pace",
                      hk=spec, ms=_ms(press))
        return dict(base, mode=mode, repeat=True, period_ms=0.0, steps=steps)

    def _from_toggle_if(self, ln, base, nodes, flips):
        for n in nodes:
            if n[0] == "if" and n[1][0] == "var" and n[1][1] in flips:
                branch = n[2] if n[1][2] else n[3]
                steps = self._steps(self._flat([branch]) if branch else [], ln)
                if steps:
                    return dict(base, mode="toggle", repeat=False, steps=steps)
        return None

    def _from_keywait(self, ln, base, nodes, hk):
        for i, n in enumerate(nodes):
            if n[0] == "cmd" and n[1][0] == "keywait":
                steps = self._steps(nodes[:i], ln)
                if steps:
                    return dict(base, mode="hold", repeat=False, steps=steps)
        return None

    # ------------------------------------------------------- passos --
    def _steps(self, nodes, ln, depth=0, rand=None, ctx=None):
        rand = {} if rand is None else rand
        # o que as buscas na tela deixaram: variável -> o que ela olha, e
        # "errorlevel" -> a última busca do v1
        ctx = {} if ctx is None else ctx
        out = []
        nodes = list(nodes)
        k = 0
        while k < len(nodes):
            n = nodes[k]
            k += 1
            kind = n[0]
            if kind == "block":
                out += self._steps(n[1], ln, depth, rand, ctx)
            elif kind == "cmd":
                out += self._cmd_steps(n[1], n[2], depth, rand, ctx)
            elif kind == "loop" and n[1]:
                inner = dict(ctx, macro_loop=False)
                one = self._steps(self._flat([n[2]]), n[3], depth, rand, inner)
                reps = min(n[1], max(1, MAX_STEPS // max(1, len(one))))
                out += self._times(one, reps)
            elif kind == "return":
                break
            elif kind == "if":
                got = self._screen_guard(n, nodes[k:], ctx)
                if got is not None:
                    got, used = got
                    k += used
                else:
                    got = self._screen_if(n, depth, rand, ctx)
                if got is None:
                    self.note(n[-1], "a condition inside the actions was "
                                     "skipped")
                else:
                    out += got
            elif kind in ("while", "loop"):
                self.note(n[-1], "a condition inside the actions was skipped")
            if len(out) > MAX_STEPS:
                return out[:MAX_STEPS]
        return out

    # ------------------------------------------- buscas na tela ----
    # PixelGetColor, PixelSearch e ImageSearch viram os passos Cor e Imagem,
    # e o if que olha o resultado vira o "se acontecer / se não" deles. Os
    # saltos são marcados com rótulos ("@") enquanto o macro é montado, e
    # _resolve troca cada um pelo número do passo no fim.
    def _label(self):
        self._label_n += 1
        return self._label_n

    def _times(self, one, reps):
        """reps cópias dos passos, cada uma com rótulos próprios."""
        if not any(st.get("op") == "@" for st in one):
            return one * reps
        out = []
        for _r in range(reps):
            ids = {st["id"]: self._label() for st in one if st.get("op") == "@"}
            for st in one:
                st = dict(st)
                if st.get("op") == "@":
                    st["id"] = ids[st["id"]]
                for key in ("ok_to", "fail_to"):
                    v = st.get(key)
                    if isinstance(v, tuple) and v[1] in ids:
                        st[key] = ("@", ids[v[1]])
                out.append(st)
        return out

    @staticmethod
    def _resolve(steps):
        """Rótulos viram números de passo. Um salto para o fim ganha um
        passo de espera de 0 ms lá, para ter onde cair."""
        if not any(isinstance(st.get("ok_to"), tuple) or
                   isinstance(st.get("fail_to"), tuple) or
                   st.get("op") == "@" for st in steps):
            return steps
        out, at = [], {}
        for st in steps:
            if st.get("op") == "@":
                at[st["id"]] = len(out) + 1
            else:
                out.append(dict(st))
        end = False
        for st in out:
            for key in ("ok_to", "fail_to"):
                v = st.get(key)
                if isinstance(v, tuple):
                    to = at.get(v[1], len(out) + 1)
                    end = end or to > len(out)
                    st[key] = to
        if end:
            out.append({"op": "wait", "ms": 0.0})
        return out

    @staticmethod
    def _coord(text):
        """Uma coordenada: número, ou A_ScreenWidth/A_ScreenHeight (a tela
        inteira; o recorte para nas bordas)."""
        t = _unquote(text or "").strip().strip("%").strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
            return int(float(t))
        if re.fullmatch(r"(?i:A_Screen(?:Width|Height)|A_VirtualScreen"
                        r"(?:Width|Height))(?:\s*-\s*\d+)?", t):
            return COORD
        return None

    @staticmethod
    def _color_value(text, bgr):
        t = _unquote(text or "").strip().strip("%").strip()
        try:
            v = int(t, 16) if t.lower().startswith("0x") else int(t)
        except ValueError:
            return None
        if not 0 <= v <= 0xFFFFFF:
            return None
        r, g, b = v >> 16, (v >> 8) & 255, v & 255
        return (b, g, r) if bgr else (r, g, b)

    def _pixel_note(self, ln):
        if not self.pixel_screen and not getattr(self, "_pix_noted", False):
            self._pix_noted = True
            self.note(ln, "positions are used as screen positions; "
                          "AutoHotkey measured them from the active window "
                          "(the same for a fullscreen game on the leftmost "
                          "screen)")

    def _image_path(self, spec, ln):
        """O arquivo de ImageSearch, sem as opções (*50 *TransBlack...).
        Devolve (caminho, variação) ou (None, 0)."""
        t = spec.strip()
        t = re.sub(r"(?i)%A_ScriptDir%[\\/]?", "", t)
        t = re.sub(r"(?i)A_ScriptDir\s*\.?\s*", "", t)
        t = _unquote(t)
        var, opts = 0, []
        while True:
            m = re.match(r"\*(\S+)\s+", t)
            if not m:
                break
            opts.append(m.group(1).lower())
            t = t[m.end():]
        for o in opts:
            if o.isdigit():
                var = min(255, int(o))
            elif o.startswith("trans"):
                self.note(ln, "ImageSearch *Trans: WayClick compares the whole "
                              "picture, with no see-through color")
            elif re.fullmatch(r"[wh]-?\d+", o):
                self.note(ln, "ImageSearch *w/*h: the picture is used at its "
                              "own size")
        t = t.strip().strip("\"'").replace("\\", "/")
        if not t or "%" in t:
            return None, 0
        name = t
        if re.match(r"^[A-Za-z]:/", t) or not os.path.isabs(t):
            # caminho do Windows ou relativo: o arquivo ao lado do script
            rel = t.split(":/", 1)[-1] if re.match(r"^[A-Za-z]:/", t) else t
            cands = [rel, os.path.basename(rel)]
            path = None
            if self.folder:
                for c in cands:
                    p = os.path.join(self.folder, c)
                    if os.path.isfile(p):
                        path = p
                        break
                path = path or os.path.join(self.folder,
                                            os.path.basename(rel))
            else:
                path = os.path.basename(rel)
        else:
            path = t
        if not os.path.isfile(path):
            self.note(ln, "image {file} was not found next to the script; "
                          "pick it in the step", file=os.path.basename(name))
        return path, var

    def _sight_of(self, ln, fname, args, v2):
        """O que um PixelGetColor/PixelSearch/ImageSearch olha, para virar
        passo: dict com "step" (sem want/fluxos), "outs" (as variáveis da
        posição achada) e "color" (PixelGetColor: a cor sai depois)."""
        if fname == "pixelgetcolor":
            if len(args) < 2:
                return None
            x, y = self._coord(args[0]), self._coord(args[1])
            if x is None or y is None:
                self.note(ln, "{what}: the position is not a number, skipped",
                          what="PixelGetColor")
                return None
            mode = _unquote(args[2]).lower().split() if len(args) > 2 else []
            self._pixel_note(ln)
            return {"kind": "color", "x": x, "y": y,
                    "bgr": not v2 and "rgb" not in mode}
        if len(args) < 7:
            return None
        outs = tuple(_unquote(a).strip().lstrip("&").strip().lower()
                     for a in args[:2])
        rect = [self._coord(a) for a in args[2:6]]
        what = "PixelSearch" if fname == "pixelsearch" else "ImageSearch"
        if None in rect:
            self.note(ln, "{what}: the area is not made of numbers, skipped",
                      what=what)
            return None
        self._pixel_note(ln)
        x1, y1, x2, y2 = rect
        region = [min(x1, x2), min(y1, y2), min(COORD, abs(x2 - x1) + 1),
                  min(COORD, abs(y2 - y1) + 1)]
        if fname == "pixelsearch":
            mode = _unquote(args[8]).lower().split() \
                if len(args) > 8 and not v2 else []
            color = self._color_value(args[6], not v2 and "rgb" not in mode)
            if color is None:
                self.note(ln, "{what}: the color is not a number, skipped",
                          what=what)
                return None
            tol = self._coord(args[7]) if len(args) > 7 and args[7].strip() \
                else 0
            step = {"op": "pixel", "x": region[0], "y": region[1],
                    "color": color_hex(color),
                    "tol": max(0, min(255, tol or 0)), "region": region}
            # v1 sem Fast procura coluna por coluna; X1 > X2 e Y1 > Y2
            # invertem o sentido
            if not v2 and "fast" not in mode:
                step["scan"] = "cols"
            if x1 > x2:
                step["rtl"] = True
            if y1 > y2:
                step["btt"] = True
            return {"kind": "search", "step": step, "outs": outs,
                    "center": False}
        path, var = self._image_path(args[6], ln)
        if path is None:
            self.note(ln, "{what}: the image file is not a plain name, "
                          "skipped", what=what)
            return None
        conf = round(max(0.6, 0.95 - var / 255.0 * 0.5), 2)
        if not self._conf_noted:
            self._conf_noted = True
            self.note(ln, "ImageSearch: WayClick compares pictures by "
                          "similarity, so *{n} became {pct}% similar; when "
                          "found, the click goes to the middle of the "
                          "picture (AutoHotkey gives its top left corner)",
                      n=var, pct=int(round(conf * 100)))
        step = {"op": "image", "path": path, "conf": conf}
        whole = x1 <= 0 and y1 <= 0 and x2 >= COORD and y2 >= COORD
        if not whole:
            step["region"] = region
        return {"kind": "search", "step": step, "outs": outs, "center": True}

    def _screen_cond(self, cond, ctx, ln):
        """(passo sem want, quer ver, sight) para um if, ou None."""
        kind = cond[0]
        if kind == "scall":
            sight = self._sight_of(ln, cond[1], cond[2], True)
            if sight and sight["kind"] == "search":
                return dict(sight["step"]), cond[3], sight
            return None
        if kind == "pgc":
            sight = self._sight_of(ln, "pixelgetcolor", cond[1], True)
            color = self._color_value(cond[3], False) if sight else None
            if color is None:
                return None
            return ({"op": "pixel", "x": sight["x"], "y": sight["y"],
                     "color": color_hex(color), "tol": 0}, cond[2], None)
        if kind == "cmp":
            sight = ctx.get(cond[1])
            if not sight or sight["kind"] != "color":
                return None
            color = self._color_value(cond[3], sight["bgr"])
            if color is None:
                return None
            return ({"op": "pixel", "x": sight["x"], "y": sight["y"],
                     "color": color_hex(color), "tol": 0}, cond[2], None)
        if kind == "var":
            sight = ctx.get(cond[1])
            if not sight or sight["kind"] != "search":
                return None
            want = cond[2]
            if cond[1] == "errorlevel":          # ErrorLevel 0: achou
                want = not want
            return dict(sight["step"]), want, sight
        return None

    def _fold_click(self, nodes, sight, ln):
        """Click FoundX, FoundY logo depois da busca vira o "quando achar"
        do passo: (ação, nós que sobram)."""
        if not sight or not nodes or nodes[0][0] != "cmd":
            return "none", nodes
        c = nodes[0][1]
        if c[0] not in ("click", "mouseclick", "move"):
            return "none", nodes
        outs = [o for o in sight["outs"] if o]
        toks = []
        for a in c[1]:
            toks += [x for x in re.split(r"[\s,]+", _unquote(a).strip()) if x]
        low = [t.strip("%").lower() for t in toks]
        if not outs or not any(t.startswith(tuple(outs)) for t in low):
            return "none", nodes
        if any(t not in outs and t.startswith(tuple(outs)) for t in low) or \
                any(t in ("+", "-") for t in low):
            self.note(ln, "the offset added to the position found was left "
                          "out")
        if c[0] == "move":
            return "move", nodes[1:]
        rest = [t for t in low if not t.startswith(tuple(outs))]
        if any(t in ("down", "d", "up", "u") for t in rest):
            return "none", nodes
        act = "left"
        for t in rest:
            if t in ("right", "r"):
                act = "right"
            elif t in ("middle", "m"):
                act = "middle"
            elif t == "2":
                act = "double" if act == "left" else act
        return act, nodes[1:]

    def _screen_guard(self, n, rest, ctx):
        """O jeito v2 de escrever: 'if !ImageSearch(&x, &y, ...) return' e
        depois 'Click x, y'. Vira: achou, clica e segue; senão acaba esta
        volta. Devolve (passos, nós usados de rest) ou None."""
        cond, then, other, ln = n[1], n[2], n[3], n[4]
        nodes = self._flat([then])
        if other or len(nodes) != 1 or nodes[0][0] != "return":
            return None
        got = self._screen_cond(cond, ctx, ln)
        if got is None:
            return None
        st, want, sight = got
        if sight:
            ctx["_last"] = sight         # a posição achada vale depois
        go = not want                     # segue quando o if não volta
        act, left = self._fold_click(list(rest), sight, ln) if go \
            else ("none", rest)
        st.update(want="seen" if go else "gone", wait=0.0, act=act,
                  ok="next", fail="jump", fail_to=("@", "END"))
        return [st], len(rest) - len(left)

    def _screen_if(self, n, depth, rand, ctx):
        """if de uma busca na tela -> passos com "se não" pulando o bloco."""
        cond, then, other, ln = n[1], n[2], n[3], n[4]
        got = self._screen_cond(cond, ctx, ln)
        if got is None:
            return None
        st, want, sight = got
        if sight and cond[0] == "scall":
            ctx["_last"] = sight
        nodes = self._flat([then])
        stops = bool(nodes) and nodes[-1][0] == "return"
        breaks = len(nodes) == 1 and nodes[0][0] == "break"
        if want:
            act, nodes = self._fold_click(nodes, sight, ln)
        else:
            act = "none"
        st.update(want="seen" if want else "gone", wait=0.0, act=act,
                  ok="next")
        end = self._label()
        if breaks:
            if not ctx.get("macro_loop"):
                return None
            # "if achou, break" no laço do macro: achou, o macro para;
            # não achou, segue
            st["ok"], st["fail"] = "stop", "next"
            return [st]
        a = self._steps(nodes, ln, depth, rand, ctx)
        b = self._steps(self._flat([other]), ln, depth, rand, ctx) \
            if other else []
        out = [st] + a
        if stops:
            # return no meio: acaba esta volta do macro
            out.append({"op": "count", "name": "else", "every": 1,
                        "ok": "jump", "ok_to": ("@", "END")})
        if b:
            els = self._label()
            st["fail"], st["fail_to"] = "jump", ("@", els)
            if not stops:
                out.append({"op": "count", "name": "else", "every": 1,
                            "ok": "jump", "ok_to": ("@", end)})
            out += [{"op": "@", "id": els}] + b
        else:
            st["fail"], st["fail_to"] = "jump", ("@", end)
        out.append({"op": "@", "id": end})
        return out

    def _cmd_steps(self, c, ln, depth, rand, ctx=None):
        kind = c[0]
        if kind == "screen":
            _k, var, fname, args, v2 = c
            sight = self._sight_of(ln, fname, args, v2)
            if sight and ctx is not None:
                if fname == "pixelgetcolor" or var:
                    if var:
                        ctx[var] = sight
                else:
                    ctx["errorlevel"] = sight
            return []
        if kind in ("click", "mouseclick", "move") and ctx:
            outs = {o for sg in ctx.values() if isinstance(sg, dict)
                    for o in sg.get("outs") or () if o}
            low = " ".join(_unquote(a) for a in c[1]).replace("%", "").lower()
            if outs and any(re.search(rf"\b{re.escape(o)}\b", low)
                            for o in outs):
                self.note(ln, "the position found is used only by a click "
                              "right after the search, skipped")
                return []
        if kind == "send":
            return self._send(c[1], c[2], ln)
        if kind in ("click", "mouseclick"):
            return self._click(c[1], ln, kind == "mouseclick")
        if kind == "sleep":
            return self._sleep(c[1], ln, rand)
        if kind == "random":
            lo, hi = sorted((c[2], c[3]))
            rand[c[1]] = (lo, hi)
            return []
        if kind == "call":
            if depth < 4 and (c[1] in self.labels or c[1] in self.funcs):
                return self._steps(self._flat(self._body(c[1])), ln,
                                   depth + 1, rand, ctx)
            if c[1]:
                self.note(ln, "{name} was not found", name=c[1])
            return []
        if kind == "move":
            return self._move(c[1], ln)
        if kind in ("flip", "assign", "settimer", "keydelay", "sendmode",
                    "gks", "ignore", "keywait"):
            return []
        if kind == "control":
            self.note(ln, "{what} is not supported, skipped", what=c[1])
            return []
        self.note(ln, "{what} is not supported, skipped", what=c[1])
        return []

    def _sleep(self, arg, ln, rand):
        a = arg.strip().strip("%").strip()
        m = re.fullmatch(r"Random\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", a, re.I)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            return [self._wait(lo, hi, ln)]
        try:
            return [self._wait(float(a), None, ln)]
        except ValueError:
            pass
        if a.lower() in rand:
            lo, hi = rand[a.lower()]
            return [self._wait(lo, hi, ln)]
        self.note(ln, "Sleep {a} is not a number, skipped", a=arg)
        return []

    def _tick(self, ms, ln):
        """Sleep curto como no Windows: abaixo de um tique, um tique. Sleep 0
        e -1 só cedem a vez no AutoHotkey, e aqui não esperam nada."""
        if 0 < ms < AHK_TICK_MS:
            if not self._tick_noted:
                self._tick_noted = True
                self.note(ln, "Sleep {ms}: on Windows AutoHotkey waits at "
                              "least 10 or 15.6 ms, so the script really "
                              "waited 15.6 ms, and so do the macros (every "
                              "wait shorter than that)", ms=_ms(ms))
            return AHK_TICK_MS
        return max(0.0, float(ms))

    def _wait(self, lo, hi, ln):
        lo = self._tick(lo, ln)
        if hi is None:
            return {"op": "wait", "ms": lo}
        return {"op": "wait", "ms": lo, "max": max(lo, self._tick(hi, ln))}

    def _move(self, args, ln):
        rel = any(a.strip().lower() in ("r", "rel", "relative") for a in args)
        nums = []
        for a in args[:2]:
            try:
                nums.append(int(float(a)))
            except ValueError:
                break
        if rel and len(nums) == 2:
            return [{"op": "move", "dx": nums[0], "dy": nums[1]}]
        if len(nums) == 2:
            return [self._point(nums, ln)]
        self.note(ln, "MouseMove without a position, skipped")
        return []

    def _point(self, xy, ln):
        if not self.screen_coords:
            self.note(ln, "positions are used as screen positions; "
                          "AutoHotkey measured them from the active window "
                          "(the same for a fullscreen game on the leftmost "
                          "screen)")
        return {"op": "point", "x": xy[0], "y": xy[1]}

    def _click(self, args, ln, mouseclick=False):
        toks = []
        for a in args:
            toks += [x for x in re.split(r"[\s,]+", a.strip()) if x]
        button, count, state, nums, rel = "left", 1, None, [], False
        names = {"left": "left", "l": "left", "right": "right", "r": "right",
                 "middle": "middle", "m": "middle", "x1": "x1", "x2": "x2"}
        for tk in toks:
            low = tk.lower()
            if low in names and not (low == "r" and nums and mouseclick):
                button = names[low]
            elif low in AHK_WHEEL:
                button = AHK_WHEEL[low]
            elif low in ("down", "d"):
                state = "down"
            elif low in ("up", "u"):
                state = "up"
            elif low in ("rel", "relative"):
                rel = True
            elif re.fullmatch(r"-?\d+", low):
                nums.append(int(low))
        steps = []
        if mouseclick:
            xy, count = nums[:2], (nums[2] if len(nums) > 2 else 1)
        else:
            xy = nums[:2] if len(nums) >= 2 else []
            count = nums[2] if len(nums) >= 3 else (nums[0] if len(nums) == 1
                                                    else 1)
        if len(xy) == 2:
            if rel:
                steps.append({"op": "move", "dx": xy[0], "dy": xy[1]})
            else:
                steps.append(self._point(xy, ln))
        if button in ("up", "down", "left", "right") and button not in \
                ("left", "right") or button in ("up", "down"):
            pass
        if button in AHK_WHEEL.values() and button in ("up", "down") \
                and state is None and button != "left":
            steps.append({"op": "wheel", "dir": button, "times": max(1, count)})
            return steps
        code = {"left": BTN["Left"], "right": BTN["Right"],
                "middle": BTN["Middle"], "x1": BTN["Side"],
                "x2": BTN["Extra"]}.get(button)
        if code is None:
            steps.append({"op": "wheel", "dir": button, "times": max(1, count)})
            return steps
        if count == 0 and not xy:
            return steps
        if state:
            steps.append({"op": state, "code": code})
        elif count > 0:
            steps.append({"op": "tap", "code": code, "times": count})
        return steps

    def _send(self, text, raw, ln):
        steps, buf, mods, i = [], "", [], 0

        def flush():
            nonlocal buf
            if buf:
                steps.extend(self._literal(buf))
                buf = ""

        text = _escape_text(text)
        while i < len(text):
            ch = text[i]
            if raw:
                buf += ch
                i += 1
                continue
            if ch in AHK_MODS:
                flush()
                mods.append(AHK_MODS[ch])
                i += 1
                continue
            if ch == "{":
                j = text.find("}", i + 2 if text[i + 1:i + 2] == "}" else i + 1)
                if j < 0:
                    buf += text[i:]
                    break
                inner = text[i + 1:j]
                i = j + 1
                if inner.lower() in ("raw", "text"):
                    flush()
                    raw = True
                    continue
                if inner.lower() in ("blind", ""):
                    continue
                if len(inner) == 1 and not mods:
                    buf += inner
                    continue
                flush()
                key_steps = self._brace(inner, ln)
                steps.extend(self._wrap(mods, key_steps))
                mods = []
                continue
            if mods:
                flush()
                key_steps = self._literal(ch, single=True)
                steps.extend(self._wrap(mods, key_steps))
                mods = []
            else:
                buf += ch
            i += 1
        flush()
        return steps

    @staticmethod
    def _wrap(mods, steps):
        if not mods:
            return steps
        downs = [{"op": "down", "code": MOD_KEY[m]} for m in mods]
        ups = [{"op": "up", "code": MOD_KEY[m]} for m in reversed(mods)]
        return downs + steps + ups

    @staticmethod
    def _literal(text, single=False):
        """Uma letra ou dígito vira "Toque E" (editável pelo campo de tecla);
        texto de verdade vira um passo "Digitar"."""
        if len(text) == 1 and (text.islower() or text.isdigit()) \
                and text in CHAR_KEYS:
            return [{"op": "tap", "code": CHAR_KEYS[text][0]}]
        if single and text in CHAR_KEYS:
            code, shift = CHAR_KEYS[text]
            if shift:
                return [{"op": "down", "code": 42}, {"op": "tap", "code": code},
                        {"op": "up", "code": 42}]
            return [{"op": "tap", "code": code}]
        return [{"op": "text", "text": text}]

    def _brace(self, inner, ln):
        parts = inner.split()
        name, arg = parts[0], " ".join(parts[1:]).lower()
        low = name.lower()
        if low == "click":
            return self._click([" ".join(parts[1:])], ln)
        if low in AHK_WHEEL:
            n = int(arg) if arg.isdigit() else 1
            return [{"op": "wheel", "dir": AHK_WHEEL[low], "times": n}]
        code = ahk_key(name)
        if code is None:
            self.note(ln, "key {name} is unknown", name=name)
            return []
        if arg in ("down", "downr", "downtemp", "d"):
            return [{"op": "down", "code": code}]
        if arg in ("up", "u"):
            return [{"op": "up", "code": code}]
        n = int(arg) if arg.isdigit() else 1
        if n <= 0:
            return []
        return [{"op": "tap", "code": code, "times": n}]


def import_ahk_file(path):
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as fh:
        raw = fh.read(2 * 1024 * 1024)
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if "\x00" not in text:
                break
        except UnicodeDecodeError:
            continue
    return AhkImport(text, folder)


def note_text(note):
    ln, text, fmt = note
    return _("line {ln}", ln=ln) + ": " + _(text, **fmt)


# ------------------------------------------------------------------- som ----
class Beeper:
    """Dois blips curtos (agudo = ligou, grave = desligou). Sem arquivo externo:
    os WAVs são sintetizados no primeiro uso."""
    RATE = 44100

    def __init__(self):
        self.effects = {}
        self.player = None
        self.paths = {}
        d = os.path.join(os.environ.get("XDG_RUNTIME_DIR")
                         or tempfile.gettempdir(), f"wayclick-{os.getuid()}")
        try:
            os.makedirs(d, exist_ok=True)
            for name, freq in (("on", 1046.5), ("off", 622.25)):
                path = os.path.join(d, f"{name}.wav")
                if not os.path.exists(path):
                    self._write_wav(path, freq)
                self.paths[name] = path
        except OSError:
            return
        try:                     # QtMultimedia é pacote separado em algumas distros
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            self.player = (shutil.which("paplay") or shutil.which("pw-play")
                           or shutil.which("aplay"))
            return
        for name, path in self.paths.items():
            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(path))
            eff.setVolume(0.35)
            self.effects[name] = eff

    @classmethod
    def _write_wav(cls, path, freq, ms=70):
        n = int(cls.RATE * ms / 1000)
        fade = int(cls.RATE * 0.006)      # rampa: sem ela o blip estala
        frames = bytearray()
        for i in range(n):
            amp = min(1.0, i / fade, (n - i) / fade)
            v = int(20000 * amp * math.sin(2 * math.pi * freq * i / cls.RATE))
            frames += struct.pack("<h", v)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(cls.RATE)
            w.writeframes(bytes(frames))

    def play(self, name):
        eff = self.effects.get(name)
        if eff is not None:
            eff.play()
        elif self.player and name in self.paths:
            subprocess.Popen([self.player, self.paths[name]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -------------------------------------- injeção direcionada a uma janela ----
class XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
                ("same_screen", ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", XKeyEvent),
                ("pad", ctypes.c_long * 24)]


class XClassHint(ctypes.Structure):
    # c_void_p e não c_char_p: com c_char_p o ctypes entrega bytes já copiados e
    # o XFree acabaria liberando o ponteiro errado, corrompendo o heap
    _fields_ = [("res_name", ctypes.c_void_p), ("res_class", ctypes.c_void_p)]



# No Wayland não existe "entregar este evento naquela janela": o input pertence
# ao seat e vai para quem está em foco — nem o fake_input do KWin, único
# protocolo de injeção que ele implementa, tem argumento de surface.
#
# O caminho que funciona é outro: teclado segue o foco. Damos foco à janela
# alvo por um instante, mandamos a tecla pelo nosso teclado uinput (evento de
# kernel, que app nenhum pode ignorar, ao contrário de XSendEvent sintético) e
# devolvemos o foco. Medido: 12 ms para ativar, 12 ms para devolver, a tecla
# cai na alvo e zero vaza para a janela que o usuário estava usando.
#
# Só vale para teclado. Clique segue o cursor, não o foco, então exigiria
# teleportar o ponteiro do usuário — aí sim atrapalharia.
KWIN_LIST_JS = """
var out = [];
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!w.normalWindow || w.skipTaskbar) continue;
  out.push({id: String(w.internalId), cls: String(w.resourceClass),
            pid: w.pid, title: String(w.caption), active: w.active === true});
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "reply", JSON.stringify(out));
"""

KWIN_ACTIVATE_JS = """
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
var prev = "";
for (var i = 0; i < ws.length; i++) if (ws[i].active) prev = String(ws[i].internalId);
for (var i = 0; i < ws.length; i++) {
  if (String(ws[i].internalId) === "%(target)s") workspace.activeWindow = ws[i];
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "reply", prev);
"""

# Passo Janela e vigia de janela: a melhor janela cujo nome do app (classe,
# como org.vinegarhq.Sober) contém o texto, ou cujo título é o texto inteiro.
# Título que só contém não vale: com o jogo fechado, um terminal que rodou
# "flatpak run org.vinegarhq.Sober" ou uma aba do navegador sobre o Sober
# passariam por ele, e o macro digitaria a estratégia lá.
KWIN_FIND_JS = """
var want = %(match)s;
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
var best = null, score = 0;
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!w.normalWindow) continue;
  var cls = String(w.resourceClass).toLowerCase();
  var nm = String(w.resourceName).toLowerCase();
  var title = String(w.caption).toLowerCase().replace(/\\s+/g, " ").trim();
  var s = (cls === want || nm === want) ? 3
        : (cls.indexOf(want) >= 0 || nm.indexOf(want) >= 0) ? 2
        : title === want ? 1 : 0;
  if (s > score) { best = w; score = s; }
}
var out = {found: best !== null};
if (best) {
  out.title = String(best.caption);
  out.cls = String(best.resourceClass);
  out.minimized = best.minimized === true;
  if (%(focus)s) {
    if (best.minimized) best.minimized = false;
    workspace.activeWindow = best;
  }
  out.active = best.active === true;
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "answer",
         "%(token)s " + JSON.stringify(out));
"""

# Onde está o cursor: o gravador pergunta a cada clique.
KWIN_CURSOR_JS = """
var p = workspace.cursorPos;
callDBus("%(svc)s", "%(path)s", "%(iface)s", "answer",
         "%(token)s " + JSON.stringify({x: p.x, y: p.y}));
"""


class XInject:
    """Injeção de tecla numa janela X11 específica, via XSendEvent.

    Este é o único caminho que entrega input a uma janela sem mexer no foco do
    usuário: o X11 endereça o evento à janela, e o cliente processa mesmo sem
    foco e mesmo minimizado — medido, 3 de 3 nas duas situações. É o oposto do
    Wayland, onde o evento vai para quem tem foco e ponto.

    Vale só para clientes X11 (sob Xwayland). App Wayland nativo não tem window
    id para endereçar. E o evento vai marcado como sintético, então app que só
    aceita input "de verdade" pode ignorar — por isso a UI testa antes.
    """
    KEYPRESS, KEYRELEASE = 2, 3
    PRESS_MASK, RELEASE_MASK = 1 << 0, 1 << 1

    def __init__(self):
        self.ok = False
        self.error = None
        self.dpy = None
        if not os.environ.get("DISPLAY"):
            self.error = "no X11 display (Xwayland not running)"
            return
        try:
            import ctypes.util
            lib = ctypes.util.find_library("X11")
            self.x = ctypes.CDLL(lib) if lib else None
        except OSError:
            self.x = None
        if not self.x:
            self.error = "libX11 not found"
            return
        self._declare()
        self.dpy = self.x.XOpenDisplay(None)
        if not self.dpy:
            self.error = "could not open the X11 display"
            return
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.ok = True

    def _declare(self):
        """ctypes sem argtypes trunca ponteiro para int e derruba o processo."""
        x, D, W = self.x, ctypes.c_void_p, ctypes.c_ulong
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = D
        x.XDefaultRootWindow.argtypes = [D]; x.XDefaultRootWindow.restype = W
        x.XInternAtom.argtypes = [D, ctypes.c_char_p, ctypes.c_int]
        x.XInternAtom.restype = W
        x.XGetWindowProperty.argtypes = [
            D, W, W, ctypes.c_long, ctypes.c_long, ctypes.c_int, W,
            ctypes.POINTER(W), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        x.XGetWindowProperty.restype = ctypes.c_int
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XStringToKeysym.restype = W
        x.XKeysymToKeycode.argtypes = [D, W]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XSendEvent.argtypes = [D, W, ctypes.c_int, ctypes.c_long,
                                 ctypes.POINTER(XEvent)]
        x.XSendEvent.restype = ctypes.c_int
        x.XFlush.argtypes = [D]; x.XFlush.restype = ctypes.c_int
        x.XGetClassHint.argtypes = [D, W, ctypes.POINTER(XClassHint)]
        x.XGetClassHint.restype = ctypes.c_int
        x.XkbKeycodeToKeysym.argtypes = [D, ctypes.c_ubyte, ctypes.c_uint,
                                         ctypes.c_uint]
        x.XkbKeycodeToKeysym.restype = W
        x.XKeysymToString.argtypes = [W]
        x.XKeysymToString.restype = ctypes.c_char_p
        x.XQueryTree.argtypes = [D, W, ctypes.POINTER(W), ctypes.POINTER(W),
                                 ctypes.POINTER(ctypes.POINTER(W)),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XQueryTree.restype = ctypes.c_int

    def _prop(self, win, name, prop_type):
        actual_type = ctypes.c_ulong()
        actual_fmt = ctypes.c_int()
        nitems = ctypes.c_ulong()
        after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        atom = self.x.XInternAtom(self.dpy, name.encode(), 0)
        r = self.x.XGetWindowProperty(
            self.dpy, win, atom, 0, 1024, 0, prop_type,
            ctypes.byref(actual_type), ctypes.byref(actual_fmt),
            ctypes.byref(nitems), ctypes.byref(after), ctypes.byref(data))
        if r != 0 or not data:
            return None, 0
        return data, nitems.value

    def windows(self):
        """[(xid, pid, classe)] das janelas X11 gerenciadas agora."""
        out = []
        data, n = self._prop(self.root, "_NET_CLIENT_LIST", 33)   # XA_WINDOW
        if not data:
            return out
        ids = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
        wins = [ids[i] for i in range(n)]
        self.x.XFree(data)
        for w in wins:
            pid = 0
            d, cnt = self._prop(w, "_NET_WM_PID", 6)              # XA_CARDINAL
            if d and cnt:
                pid = ctypes.cast(d, ctypes.POINTER(ctypes.c_ulong))[0]
                self.x.XFree(d)
            hint = XClassHint()
            cls = ""
            if self.x.XGetClassHint(self.dpy, w, ctypes.byref(hint)):
                if hint.res_class:
                    cls = ctypes.string_at(hint.res_class).decode(errors="replace")
                    self.x.XFree(hint.res_class)
                if hint.res_name:
                    self.x.XFree(hint.res_name)
            out.append((w, int(pid), cls))
        return out

    def keysym_name(self, evdev_code):
        """Código evdev -> nome de keysym do X, direto do mapa de teclado.
        Evita manter uma tabela de tradução à mão."""
        if not self.ok:
            return None
        ks = self.x.XkbKeycodeToKeysym(self.dpy, evdev_code + 8, 0, 0)
        if not ks:
            return None
        name = self.x.XKeysymToString(ks)
        return name.decode() if name else None

    def children(self, win, depth=2, limit=6):
        """Subjanelas do alvo. Muita aplicação recebe o input numa janela
        filha, não na de topo que o gerenciador lista."""
        out = []
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids = ctypes.POINTER(ctypes.c_ulong)()
        nkids = ctypes.c_uint()
        if not self.x.XQueryTree(self.dpy, win, ctypes.byref(root),
                                 ctypes.byref(parent), ctypes.byref(kids),
                                 ctypes.byref(nkids)):
            return out
        for i in range(min(nkids.value, limit)):
            child = kids[i]
            out.append(child)
            if depth > 1:
                out.extend(self.children(child, depth - 1, limit))
        if kids:
            self.x.XFree(kids)
        return out[:limit]

    def send_key(self, win, keysym_name, hold=0.05):
        """press + release endereçados à janela. O hold importa: sem ele, quem
        consulta estado por quadro não vê nada."""
        if not self.ok:
            return False
        ks = self.x.XStringToKeysym(keysym_name.encode())
        if not ks:
            return False
        kc = self.x.XKeysymToKeycode(self.dpy, ks)
        targets = [win] + self.children(win)
        stamp = int(time.time() * 1000) & 0xFFFFFFFF   # app costuma olhar time
        for etype, mask in ((self.KEYPRESS, self.PRESS_MASK),
                            (self.KEYRELEASE, self.RELEASE_MASK)):
            for target in targets:
                ev = XEvent()
                ev.type = etype
                k = ev.xkey
                k.type, k.display = etype, self.dpy
                k.window, k.root, k.subwindow = target, self.root, 0
                k.time = stamp
                k.x = k.y = k.x_root = k.y_root = 1
                k.state, k.keycode, k.same_screen = 0, kc, 1
                self.x.XSendEvent(self.dpy, target, 0, mask, ctypes.byref(ev))
            self.x.XFlush(self.dpy)
            stamp += int(hold * 1000)
            if etype == self.KEYPRESS:
                time.sleep(hold)
        return True


CODE_NAMES = {}
for _n, _c in KEYS.items():
    CODE_NAMES.setdefault(_c, _n)

# Código de botão começa em 0x100; tecla nenhuma chega perto (a maior aqui é
# 194). Então o próprio número diz se o gatilho é tecla ou botão, sem precisar
# guardar um sinalizador junto.
BTN_NAMES = {code: name for name, code in BTN.items()}
QT_BUTTONS = {Qt.LeftButton: BTN["Left"], Qt.RightButton: BTN["Right"],
              Qt.MiddleButton: BTN["Middle"], Qt.BackButton: BTN["Side"],
              Qt.ForwardButton: BTN["Extra"]}


def is_button(code):
    return code >= 0x100


def trigger_label(code, event=None):
    """Nome do gatilho, seja tecla ou botão do mouse."""
    if code in BTN_NAMES:
        return _("{btn} click", btn=_(BTN_NAMES[code]))
    return key_label(code, event)


def key_label(code, event=None):
    """Nome amigável para um código evdev capturado do teclado."""
    if code in CODE_NAMES:
        return CODE_NAMES[code]
    if event is not None:
        txt = QKeySequence(event.key()).toString()
        if txt:
            return txt
    return f"key {code}"


class KeyCatcher(QPushButton):
    """Clique e aperte a tecla. Melhor que caçar numa lista de 122 itens, e
    pega qualquer tecla do teclado, não só as que estão na lista: o Qt entrega
    o scancode nativo, que é o código evdev + 8 (conferido em letra, função,
    modificador, numérico e seta)."""
    changed = Signal()

    def __init__(self, code=57, name="Space", allow_mouse=False):
        super().__init__()
        self.code, self.name = code, name
        self.allow_mouse = allow_mouse
        self._arming = False
        self.clicked.connect(self._arm)
        self.setToolTip(_("Click, then press the key you want")
                        if not allow_mouse else
                        _("Click, then press a key or a mouse button"))
        self._refresh()

    def _refresh(self):
        if not self._arming:
            self.setText(self.name)
        else:
            self.setText(_("Press a key or a mouse button…") if self.allow_mouse
                         else _("Press a key…"))

    def _arm(self):
        self._arming = True
        CAPTURE.set()
        self._refresh()
        self.setFocus()
        self.grabKeyboard()

    def _disarm(self):
        self._arming = False
        CAPTURE.clear()
        self.releaseKeyboard()
        self._refresh()

    def focusOutEvent(self, event):
        if self._arming:             # clicou fora: desiste, nada fica preso
            self._disarm()
        super().focusOutEvent(event)

    def set_key(self, code, name=None):
        self.code = code
        self.name = name or trigger_label(code)
        self._arming = False
        CAPTURE.clear()
        self._refresh()
        self.changed.emit()

    def keyPressEvent(self, event):
        if not self._arming:
            return          # sem isso Space e Enter re-disparariam o botão
        code = event.nativeScanCode() - 8
        if event.key() == Qt.Key_Escape:
            self._disarm()
            return
        self.releaseKeyboard()
        self.set_key(code, trigger_label(code, event))

    def mousePressEvent(self, event):
        """Armado e com allow_mouse, o clique no campo vira a escolha. É o que
        faltava para "segurar o botão esquerdo e repetir E": o gatilho é um
        botão, e não havia onde dizer isso sem passar pelo modo certo."""
        if not (self._arming and self.allow_mouse):
            return super().mousePressEvent(event)
        code = QT_BUTTONS.get(event.button())
        if code is None:
            return super().mousePressEvent(event)
        self.releaseKeyboard()
        self.set_key(code, trigger_label(code))
        event.accept()

    def mouseReleaseEvent(self, event):
        # sem isso o release do clique que acabou de ser capturado re-arma o
        # campo, porque QPushButton emite clicked() no release
        if self.allow_mouse and not self._arming:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class KeyRow(QWidget):
    """Uma tecla da macro: qual, de quanto em quanto, e o que fazer com ela.

    A macro nasceu com uma tecla só, e é a pergunta óbvia de quem usa: andar e
    atacar ao mesmo tempo são duas teclas. Cada linha carrega o seu próprio
    intervalo porque o caso real é justamente assíncrono — W a cada 1 s e
    Espaço a cada 50 ms não cabe num intervalo compartilhado.
    """
    removed = Signal(object)
    changed = Signal()

    def __init__(self, code=57, name="Space", interval_ms=200.0, mode="Repeat"):
        super().__init__()
        self.catcher = KeyCatcher(code, name)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(1.0, 10000.0)
        self.interval.setDecimals(0)
        self.interval.setSingleStep(50.0)
        self.interval.setSuffix(" ms")
        self.interval.setValue(interval_ms)
        self.interval.setFixedWidth(96)
        self.mode = QComboBox()
        self.mode.setFixedWidth(96)
        for label in ("Repeat", "Hold"):
            self.mode.addItem(_(label), label)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.minus = QPushButton("−")
        self.minus.setFixedWidth(30)
        self.minus.setToolTip(_("Remove this key"))
        self.minus.clicked.connect(lambda: self.removed.emit(self))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.catcher, 1)
        for wdg in (self.interval, self.mode, self.minus):
            row.addWidget(wdg)
        self.interval.setEnabled(self.mode.currentData() == "Repeat")
        self.catcher.changed.connect(self.changed)
        self.interval.valueChanged.connect(lambda _v: self.changed.emit())

    def _mode_changed(self, _i=0):
        self.interval.setEnabled(self.mode.currentData() == "Repeat")
        self.changed.emit()

    def spec(self):
        return {"key": self.catcher.name, "key_code": self.catcher.code,
                "interval_ms": self.interval.value(),
                "mode": self.mode.currentData()}

    def retranslate(self):
        keep = self.mode.currentIndex()
        self.mode.blockSignals(True)
        for i, src in enumerate(("Repeat", "Hold")):
            self.mode.setItemText(i, _(src))
        self.mode.setCurrentIndex(keep)
        self.mode.blockSignals(False)
        self.minus.setToolTip(_("Remove this key"))
        self.catcher.setToolTip(_("Click, then press the key you want"))


# nomes de keysym do X para as teclas que a macro oferece
X_KEYSYM = {"Space": "space", "Enter": "Return", "Tab": "Tab", "Esc": "Escape",
            "Backspace": "BackSpace", "Delete": "Delete", "Insert": "Insert",
            "Home": "Home", "End": "End", "Page Up": "Prior",
            "Page Down": "Next", "Up": "Up", "Down": "Down", "Left": "Left",
            "Right": "Right", "Left Shift": "Shift_L",
            "Right Shift": "Shift_R", "Left Ctrl": "Control_L",
            "Right Ctrl": "Control_R", "Left Alt": "Alt_L",
            "Right Alt": "Alt_R", "Left Super": "Super_L",
            "Right Super": "Super_R"}


def x_keysym(name):
    """Nome da tecla na UI -> keysym do X."""
    if name in X_KEYSYM:
        return X_KEYSYM[name]
    if len(name) == 1:
        return name.lower() if name.isalpha() else name
    if name.startswith("F") and name[1:].isdigit():
        return name
    if name.startswith("Numpad "):
        tail = name.split(" ", 1)[1]
        return {"+": "KP_Add", "-": "KP_Subtract", "*": "KP_Multiply",
                "/": "KP_Divide", ".": "KP_Decimal",
                "Enter": "KP_Enter"}.get(tail, "KP_" + tail)
    return None


class KWinBridge(QObject):
    """Conversa com o KWin via scripting: só ele enxerga as janelas no Wayland.

    O script JS roda dentro do compositor e devolve o resultado chamando um
    serviço D-Bus nosso — scripts do KWin podem fazer callDBus, mas não podem
    escrever em arquivo nem receber chamadas.

    address: o D-Bus de outro KWin, o da Doca, em vez do da sessão. O script
    dele chama callDBus no barramento dele, então o serviço nosso é
    registrado lá também.
    """
    IFACE = "org.wayclick.Bridge"

    def __init__(self, address=None):
        super().__init__()
        self.ok = False
        self.error = None
        self._reply = None
        self._calls = {}                # token -> [Event, resposta]
        self._calls_lock = threading.Lock()
        self.address = address
        self._bus_args = ("--address", address) if address else ("--session",)
        self._conn_name = None
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            self.error = "PySide6.QtDBus not available"
            return
        self.service = f"org.wayclick.Bridge{os.getpid()}"
        self.path = "/bridge"
        if address:
            self._conn_name = "wayclick-" + os.urandom(4).hex()
            bus = QDBusConnection.connectToBus(address, self._conn_name)
            if not bus.isConnected():
                self.error = "could not reach the D-Bus at " + address
                return
        else:
            bus = QDBusConnection.sessionBus()
        self.bus = bus
        if not (bus.registerService(self.service)
                and bus.registerObject(self.path, self.IFACE, self,
                                       QDBusConnection.ExportAllSlots)):
            self.error = "could not register the D-Bus service"
            return
        self.script = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
            f"wayclick-kwin-{'dock-' if address else ''}{os.getpid()}.js")
        self.ok = True

    def has_kwin(self):
        """O KWin deste barramento já está nele (a Doca leva um instante)."""
        try:
            r = self.bus.interface().isServiceRegistered("org.kde.KWin")
            return bool(r.value() if hasattr(r, "value") else r)
        except Exception:                          # noqa: BLE001
            return False

    def close(self):
        """Sai do barramento da Doca (o da sessão fica com o app)."""
        self.ok = False
        if self._conn_name:
            from PySide6.QtDBus import QDBusConnection
            try:
                self.bus.unregisterObject(self.path)
                self.bus.unregisterService(self.service)
            except Exception:                      # noqa: BLE001
                pass
            QDBusConnection.disconnectFromBus(self._conn_name)
            self._conn_name = None
        self.cleanup()

    @Slot(str)
    def reply(self, payload):
        self._reply = payload

    @Slot(str)
    def answer(self, payload):
        """Resposta de um script de call(), marcada com o token dele."""
        token, _sep, data = payload.partition(" ")
        with self._calls_lock:
            box = self._calls.get(token)
        if box is not None:
            box[1] = data
            box[0].set()

    def call(self, js, timeout=2.0, **fields):
        """Roda um script no KWin e devolve o texto que ele responde, ou
        None. Serve a qualquer thread, ao contrário de _run: quem espera é um
        Event, e a resposta chega pelo slot answer, na thread da UI. Cada
        chamada tem nome e token próprios, então duas ao mesmo tempo (um
        macro e a lista de janelas) não se confundem."""
        if not self.ok:
            return None
        token = os.urandom(6).hex()
        box = [threading.Event(), None]
        with self._calls_lock:
            self._calls[token] = box
        name = f"wayclick-{token}"
        path = f"{self.script[:-3]}-{token}.js"

        def gdbus(*args):
            try:
                return subprocess.run(
                    ("gdbus", "call") + self._bus_args
                    + ("-d", "org.kde.KWin") + args,
                    capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return None
        loaded = False
        try:
            with open(path, "w") as fh:
                fh.write(js % dict(fields, svc=self.service, path=self.path,
                                   iface=self.IFACE, token=token))
            r = gdbus("-o", "/Scripting", "-m",
                      "org.kde.kwin.Scripting.loadScript", path, name)
            m = re.search(r"-?\d+", r.stdout) if r is not None else None
            sid = int(m.group()) if m else -1
            if sid < 0:
                return None
            loaded = True
            gdbus("-o", f"/Scripting/Script{sid}", "-m",
                  "org.kde.kwin.Script.run")
            app = QApplication.instance()
            if app is not None and \
                    threading.current_thread() is threading.main_thread():
                # na thread da UI a resposta só chega se os eventos andarem
                end = time.perf_counter() + timeout
                while not box[0].is_set() and time.perf_counter() < end:
                    app.processEvents()
                    box[0].wait(0.004)
            else:
                box[0].wait(timeout)
            return box[1]
        except OSError:
            return None
        finally:
            if loaded:
                gdbus("-o", "/Scripting", "-m",
                      "org.kde.kwin.Scripting.unloadScript", name)
            with self._calls_lock:
                self._calls.pop(token, None)
            try:
                os.remove(path)
            except OSError:
                pass

    def find(self, match, focus=False):
        """A janela cujo nome do app contém o texto, ou cujo título é ele:
        {"found", "title", "active"...}; com focus, ela vem para a frente.
        None se o KWin não respondeu."""
        want = " ".join(str(match).lower().split())
        if not want:
            return {"found": False}
        raw = self.call(KWIN_FIND_JS, match=json.dumps(want),
                        focus="true" if focus else "false")
        try:
            info = json.loads(raw) if raw else None
        except ValueError:
            return None
        return info if isinstance(info, dict) else None

    def cursor(self):
        """Posição do cursor em coordenadas lógicas, ou None."""
        raw = self.call(KWIN_CURSOR_JS)
        try:
            p = json.loads(raw)
            return int(math.floor(p["x"])), int(math.floor(p["y"]))
        except (TypeError, ValueError, KeyError):
            return None

    def _run(self, js, timeout=2.0):
        if not self.ok:
            return None
        self._reply = None
        try:
            with open(self.script, "w") as fh:
                fh.write(js % {"svc": self.service, "path": self.path,
                               "iface": self.IFACE, "target": self._target})
        except OSError:
            return None
        bus = self._bus_args
        run = lambda *a: subprocess.run(a, capture_output=True, text=True)
        run("gdbus", "call", *bus, "-d", "org.kde.KWin", "-o",
            "/Scripting", "-m", "org.kde.kwin.Scripting.unloadScript", "wayclick")
        r = run("gdbus", "call", *bus, "-d", "org.kde.KWin", "-o",
                "/Scripting", "-m", "org.kde.kwin.Scripting.loadScript",
                self.script, "wayclick")
        sid = "".join(c for c in r.stdout if c.isdigit())
        if not sid:
            self.error = "KWin refused the script (is this KWin?)"
            return None
        run("gdbus", "call", *bus, "-d", "org.kde.KWin", "-o",
            f"/Scripting/Script{sid}", "-m", "org.kde.kwin.Script.run")
        deadline = time.perf_counter() + timeout
        app = QApplication.instance()
        while self._reply is None and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.004)
        return self._reply

    _target = ""

    def windows(self):
        self._target = ""
        raw = self._run(KWIN_LIST_JS)
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []

    def activate(self, internal_id):
        """Ativa a janela e devolve quem estava ativa antes."""
        self._target = internal_id
        return self._run(KWIN_ACTIVATE_JS)

    def cleanup(self):
        try:
            os.remove(self.script)
        except (OSError, AttributeError):
            pass


_KWIN = None


def KWIN():
    """A ponte do app com o KWin, uma só: o serviço D-Bus dela tem o pid no
    nome, e uma segunda não conseguiria registrar o mesmo. Criada na thread
    da UI, na primeira vez que alguém precisa (lista de janelas, passo
    Janela, gravador)."""
    global _KWIN
    if _KWIN is None:
        _KWIN = KWinBridge()
    return _KWIN


# ------------------------------------------------------------- a Doca --
# O jogo numa tela só do WayClick, como o Tower Defense Macro do Windows faz
# ao encaixar a janela do Roblox dentro da dele. No Wayland um programa não
# entra na janela de outro, então a Doca é um compositor inteiro: um
# kwin_wayland em janela (o backend Wayland do KWin), com D-Bus e
# configuração próprios, e o jogo aberto dentro dele. Os macros da Doca
# entram pelo fake_input do KWin dela e olham a tela pelo ScreenShot2 dela:
# o cursor e o teclado de verdade ficam livres, não há pergunta de
# compartilhar a tela, e a janela da Doca pode ficar atrás das outras ou
# minimizada. Medido no KWin 6.8.3: o jogo dentro seguiu a 60 quadros por
# segundo coberto e minimizado, recebeu teclas e cliques com o foco do
# desktop em outra janela, e cada captura de 1280x720 levou uns 7 ms.
DOCK_SOCKET = "wayland-wayclick-dock"
DOCK_GAME = "flatpak run org.vinegarhq.Sober"
DOCK_SIZES = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080),
              (1024, 576), (800, 600))
DOCK_START_S = 20.0          # quanto esperar o KWin da Doca subir
DOCK_FIX_MS = 700            # redimensionada, a janela volta ao tamanho
DOCK_FIX_TRIES = 3           # depois disso desiste (tela menor que a Doca)
# kwinrc da Doca: janela maximizada sem borda (o jogo enche a tela dela),
# janela nova já maximizada, e nada de canto quente nem Visão geral, que um
# macro com o cursor no canto abriria em vez de clicar
DOCK_KWINRC = """[Windows]
BorderlessMaximizedWindows=true
Placement=Maximizing

[Plugins]
overviewEnabled=false
windowviewEnabled=false

[Effect-overview]
BorderActivate=9

[ElectricBorders]
Bottom=None
BottomLeft=None
BottomRight=None
Left=None
Right=None
Top=None
TopLeft=None
TopRight=None
"""
DOCK_KDEGLOBALS = "[KDE]\nAnimationDurationFactor=0\n"
# o layout do teclado, a repetição e o tema do cursor são os de quem usa:
# só estes arquivos vêm copiados da configuração do desktop
DOCK_COPY = ("kxkbrc", "kcminputrc")
# D-Bus da Doca: a política do session.conf (manda, recebe e registra
# qualquer nome), sem pasta de serviços, então nada da sessão sobe nele
DOCK_BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <keep_umask/>
  <listen>unix:path=%s</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
# a janela da Doca no desktop de quem usa, achada pelo pid do KWin dela:
# volta ao tamanho escolhido e, com show, vem para a frente
DOCK_HOST_JS = """
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
var out = {found: false};
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (w.pid !== %(pid)s) continue;
  out.found = true;
  if (%(fix)s) {
    if (w.fullScreen) w.fullScreen = false;
    if (w.setMaximize) w.setMaximize(false, false);
    var f = w.frameGeometry, c = w.clientGeometry;
    w.frameGeometry = {x: f.x, y: f.y, width: %(w)s + f.width - c.width,
                       height: %(h)s + f.height - c.height};
  }
  if (%(show)s) {
    if (w.minimized) w.minimized = false;
    workspace.activeWindow = w;
  }
  if (%(hide)s) w.minimized = true;
  out.w = w.clientGeometry.width;
  out.h = w.clientGeometry.height;
  out.minimized = w.minimized === true;
  break;
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "answer",
         "%(token)s " + JSON.stringify(out));
"""


SOBER_APP = "org.vinegarhq.Sober"
SOBER_TWICE = ("Sober is already open outside the Dock, and it opens one "
               "copy at a time. Close it, or pick one of your Sober accounts "
               "in Account, then press Start the game.")


class DockError(Exception):
    """A Doca não abriu; a mensagem diz por quê."""


def _is_wayclick(pid):
    """O processo existe e é um WayClick (a linha de comando diz)."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            return b"wayclick" in fh.read().lower()
    except (OSError, ValueError):
        return False


def _ppid(pid):
    """O pai de um processo, pelo /proc (None se ele já saiu)."""
    try:
        with open(f"/proc/{int(pid)}/stat") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


class WaylandLink:
    """Cliente Wayland mínimo, só socket e struct (sem libwayland): registro,
    as telas em coordenadas lógicas (xdg_output) e pedidos crus. Uma thread
    lê os eventos; enviar serve a qualquer thread. A Doca usa um para o
    fake_input do KWin dela e para saber quando a tela dela mudou de
    tamanho."""

    def __init__(self, path, on_output=None, on_close=None):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(5.0)
        self.s.connect(path)
        self.lock = threading.Lock()
        self._next = 2
        self.globals = {}            # nome -> (interface, versão)
        self.handlers = {1: self._display_ev}
        self.errors = []
        self.outputs = {}            # id do wl_output -> {"x","y","w","h"}
        self.on_output, self.on_close = on_output, on_close
        self.closed = False
        self._xom = None
        self.reg = self.new_id()
        self.handlers[self.reg] = self._registry_ev
        self.send(1, 1, self.u(self.reg))
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="wayclick-dock-wayland")
        self._reader.start()
        if not self.sync():
            self.close()
            raise OSError("the compositor did not answer")
        self._xom, _v = self.bind("zxdg_output_manager_v1", 3)
        for gname, (iface, _v) in list(self.globals.items()):
            if iface == "wl_output":
                self._add_output(gname)
        self.sync()

    # ---- formato do fio
    @staticmethod
    def u(v):
        return struct.pack("<I", v & 0xffffffff)

    @staticmethod
    def fixed(v):
        return struct.pack("<i", int(round(v * 256)))

    @staticmethod
    def string(t):
        b = t.encode() + b"\0"
        return struct.pack("<I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)

    @staticmethod
    def _str_at(p, off):
        n = struct.unpack_from("<I", p, off)[0]
        return p[off + 4:off + 4 + n - 1].decode("utf-8", "replace")

    def new_id(self):
        with self.lock:
            n = self._next
            self._next += 1
        return n

    def send(self, obj, opcode, payload=b""):
        if self.closed:
            raise BrokenPipeError("the Dock is closed")
        msg = struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) \
            + payload
        with self.lock:
            try:
                self.s.sendall(msg)
            except OSError:
                self.closed = True
                raise

    def sync(self, timeout=5.0):
        """Espera o compositor ter lido tudo o que foi mandado antes."""
        done = threading.Event()
        cb = self.new_id()
        self.handlers[cb] = lambda op, p: done.set()
        try:
            self.send(1, 0, self.u(cb))
        except OSError:
            return False
        ok = done.wait(timeout)
        self.handlers.pop(cb, None)
        return ok

    def bind(self, iface, version=None, name=None):
        for gname, (i, v) in list(self.globals.items()):
            if i == iface and (name is None or gname == name):
                ver = min(v, version or v)
                oid = self.new_id()
                self.send(self.reg, 0, self.u(gname) + self.string(iface)
                          + self.u(ver) + self.u(oid))
                return oid, ver
        return None, 0

    def close(self):
        self.closed = True
        try:
            self.s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.s.close()
        except OSError:
            pass

    # ---- eventos (thread de leitura)
    def _display_ev(self, op, p):
        if op == 0:
            oid, code = struct.unpack_from("<II", p)
            self.errors.append((oid, code, self._str_at(p, 8)))

    def _registry_ev(self, op, p):
        if op == 0:
            name = struct.unpack_from("<I", p)[0]
            iface = self._str_at(p, 4)
            n = struct.unpack_from("<I", p, 4)[0]
            ver = struct.unpack_from("<I", p, 8 + n + (4 - n % 4) % 4)[0]
            self.globals[name] = (iface, ver)
            if iface == "wl_output" and self._xom:
                self._add_output(name)
        elif op == 1:
            name = struct.unpack_from("<I", p)[0]
            self.globals.pop(name, None)
            for oid, o in list(self.outputs.items()):
                if o.get("global") == name:
                    del self.outputs[oid]

    def _add_output(self, gname):
        out, _v = self.bind("wl_output", 4, name=gname)
        if not out:
            return
        rec = {"global": gname, "x": 0, "y": 0, "w": 0, "h": 0}
        self.outputs[out] = rec
        self.handlers[out] = lambda op, p: None
        if not self._xom:
            return
        xo = self.new_id()
        self.send(self._xom, 1, self.u(xo) + self.u(out))

        def ev(op, p):
            if op == 0:
                rec["x"], rec["y"] = struct.unpack_from("<ii", p)
            elif op == 1:
                size = struct.unpack_from("<ii", p)
                if size != (rec["w"], rec["h"]):
                    rec["w"], rec["h"] = size
                    if self.on_output:
                        self.on_output(*size)
        self.handlers[xo] = ev

    def size(self):
        """Largura e altura lógicas da tela (a Doca tem uma só)."""
        outs = [o for o in self.outputs.values() if o["w"] and o["h"]]
        if not outs:
            return None
        return (max(o["x"] + o["w"] for o in outs)
                - min(o["x"] for o in outs),
                max(o["y"] + o["h"] for o in outs)
                - min(o["y"] for o in outs))

    def _read_loop(self):
        buf = b""
        try:
            while not self.closed:
                try:
                    data = self.s.recv(1 << 16)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while len(buf) >= 8:
                    obj, word = struct.unpack_from("<II", buf)
                    size = word >> 16
                    if size < 8 or len(buf) < size:
                        break
                    p, buf = buf[8:size], buf[size:]
                    h = self.handlers.get(obj)
                    if h:
                        try:
                            h(word & 0xffff, p)
                        except (struct.error, OSError):
                            pass
        except OSError:
            pass
        self.closed = True
        if self.on_close:
            self.on_close()


class DockInput:
    """Teclas, botões, roda e movimento para dentro da Doca, pelo
    org_kde_kwin_fake_input do KWin dela. Os códigos são os do evdev, como
    os do resto do app. O cursor e o teclado de verdade não mexem."""
    AUTH, REL, BUTTON, AXIS, ABS, KEY = 0, 1, 2, 3, 9, 10

    def __init__(self, link):
        self.link = link
        self.id, self.version = link.bind("org_kde_kwin_fake_input", 6)
        if not self.id:
            raise OSError("the Dock compositor has no fake input")
        link.send(self.id, self.AUTH, link.string("WayClick")
                  + link.string("macros that play in the Dock"))

    def key(self, code, down):
        self.link.send(self.id, self.KEY, self.link.u(code)
                       + self.link.u(1 if down else 0))

    def button(self, code, down):
        self.link.send(self.id, self.BUTTON, self.link.u(code)
                       + self.link.u(1 if down else 0))

    def axis(self, axis, value):
        """axis 0 é a roda vertical, 1 a horizontal; positivo desce ou vai
        para a direita."""
        self.link.send(self.id, self.AXIS, self.link.u(axis)
                       + self.link.fixed(value))

    def move(self, dx, dy):
        """Movimento relativo, sem aceleração: dx pixels são dx pixels. É o
        que move a câmera de um jogo com o ponteiro travado."""
        self.link.send(self.id, self.REL, self.link.fixed(dx)
                       + self.link.fixed(dy))

    def absolute(self, x, y):
        self.link.send(self.id, self.ABS, self.link.fixed(x)
                       + self.link.fixed(y))


class DockPointer:
    """O ponteiro absoluto dos passos de tela, dentro da Doca: o mesmo
    papel do AbsolutePointer, com as coordenadas da tela dela."""

    def __init__(self, inp):
        self.inp = inp
        self.pos = None

    def warp(self, x, y, ws=None):
        # no meio do pixel, como o AbsolutePointer mira
        self.inp.absolute(x + 0.5, y + 0.5)
        self.pos = (x, y)

    def button(self, code, down):
        self.inp.button(code, down)


class DockScreen:
    """A tela da Doca para os passos de imagem e cor: um ScreenShot2 do KWin
    dela a cada pedido, sem portal e sem pergunta. A mesma cara da
    ScreenCapture (ensure, acquire, release, frames), então o MacroRunner
    não sabe a diferença."""
    CACHE_S = 0.03           # vigias que olham juntos dividem uma captura

    def __init__(self, dock):
        self.dock = dock
        self.lock = threading.Lock()
        self.state, self.error = "off", ""
        self.users = 0
        self._bus = None
        self._cache = (0.0, [])

    def _connect(self, np=True):
        if not self.dock.alive():
            raise VisionError(_("The Dock is not open."))
        if np:
            _numpy()
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            raise VisionError(_("Image and color steps need PyGObject.")
                              + "\n" + DEPS_HINT) from None
        if self._bus is None or self._bus.is_closed():
            self._bus = Gio.DBusConnection.new_for_address_sync(
                self.dock.bus_address(),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        return Gio, GLib

    def ensure(self, timeout=None):
        with self.lock:
            try:
                self._connect()
            except VisionError as e:
                self.state, self.error = "failed", str(e)
                return False
            except Exception as e:                 # noqa: BLE001
                self.state = "failed"
                self.error = _("Screen capture failed: {err}",
                               err=getattr(e, "message", None) or str(e))
                return False
            self.state, self.error = "on", ""
            return True

    def start(self):
        self.ensure()

    def acquire(self):
        with self.lock:
            self.users += 1

    def release(self):
        with self.lock:
            self.users = max(0, self.users - 1)

    def stop(self, join=True):
        with self.lock:
            bus, self._bus = self._bus, None
            self.state = "off"
            self._cache = (0.0, [])
        if bus is not None:
            try:
                bus.close_sync(None)
            except Exception:                      # noqa: BLE001
                pass

    def frames(self):
        with self.lock:
            now = time.monotonic()
            if self._cache[1] and now - self._cache[0] < self.CACHE_S:
                return list(self._cache[1])
            try:
                frame = self._shot()
            except VisionError as e:
                self.state, self.error = "failed", str(e)
                return []
            except Exception as e:                 # noqa: BLE001
                self.state = "failed"
                self.error = _("Screen capture failed: {err}",
                               err=getattr(e, "message", None) or str(e))
                return []
            self.state, self.error = "on", ""
            self._cache = (now, [frame])
            return [frame]

    def qimage(self):
        """A tela da Doca como QImage, para a visão da aba Tower Defense: o
        mesmo ScreenShot2, sem passar por numpy. QImage() vazia se não
        deu."""
        with self.lock:
            try:
                meta, data = self._capture(np=False)
            except VisionError as e:
                self.state, self.error = "failed", str(e)
                return QImage()
            except Exception as e:                 # noqa: BLE001
                self.state = "failed"
                self.error = _("Screen capture failed: {err}",
                               err=getattr(e, "message", None) or str(e))
                return QImage()
            self.state, self.error = "on", ""
        W, H, stride = int(meta["width"]), int(meta["height"]), \
            int(meta["stride"])
        fmt = QImage.Format_RGB32 if meta.get("format") in (4, 5, 6) \
            else QImage.Format_RGBX8888
        return QImage(data, W, H, stride, fmt).copy()

    def _capture(self, np=True):
        """Uma captura do KWin da Doca: (metadados, bytes)."""
        Gio, GLib = self._connect(np)
        r, w = os.pipe()
        fdl = Gio.UnixFDList.new()
        idx = fdl.append(w)
        os.close(w)
        opts = {"include-cursor": GLib.Variant("b", False),
                "native-resolution": GLib.Variant("b", True)}
        try:
            try:
                res, _out = self._bus.call_with_unix_fd_list_sync(
                    "org.kde.KWin", "/org/kde/KWin/ScreenShot2",
                    "org.kde.KWin.ScreenShot2", "CaptureWorkspace",
                    GLib.Variant("(a{sv}h)", (opts, idx)),
                    GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE,
                    10000, fdl, None)
            finally:
                for fd in fdl.steal_fds():
                    os.close(fd)
            meta = res.unpack()[0]
            chunks, end = [], time.monotonic() + 10.0
            while True:
                if not select.select([r], [], [], max(0.0, end
                                                      - time.monotonic()))[0]:
                    raise VisionError(_("The Dock did not send its screen."))
                c = os.read(r, 1 << 20)
                if not c:
                    break
                chunks.append(c)
        finally:
            os.close(r)
        W, H, stride = int(meta["width"]), int(meta["height"]), \
            int(meta["stride"])
        data = b"".join(chunks)
        if W <= 0 or H <= 0 or len(data) < stride * H:
            raise VisionError(_("The Dock did not send its screen."))
        return meta, data

    def _shot(self):
        np = _numpy()
        meta, data = self._capture()
        W, H, stride = int(meta["width"]), int(meta["height"]), \
            int(meta["stride"])
        a = np.frombuffer(data, np.uint8)[:stride * H]
        a = a.reshape(H, stride)[:, :W * 4].reshape(H, W, 4)
        # QImage: RGB32 e ARGB32 (4, 5, 6) são B, G, R, A na memória;
        # RGBX8888 e RGBA8888 (16, 17, 18), R, G, B, A
        rgb = a[..., [2, 1, 0]] if meta.get("format") in (4, 5, 6) \
            else a[..., :3]
        lw, lh = self.dock.out_size or (W, H)
        return Frame(np.ascontiguousarray(rgb), 0, 0, lw, lh)


def _desktop_entry_field(path, key):
    """Um campo do grupo [Desktop Entry] (não o de uma ação)."""
    group = None
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    group = line
                elif group == "[Desktop Entry]" and line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def desktop_argv(exec_line, url):
    """A linha Exec de um .desktop com o link no lugar de %u, como o
    lançador do desktop faria; None se não der para ler."""
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        return None
    out = []
    for part in parts:
        if part in ("%u", "%U", "%f", "%F"):
            out.append(url)
            continue
        if part in ("%i", "%c", "%k"):
            continue
        part = re.sub(r"%[a-zA-Z]", "", part.replace("%%", "\0"))
        part = part.replace("\0", "%")
        if part:
            out.append(part)
    return out or None


def scheme_handler(url):
    """Quem o desktop usa para abrir um link roblox:// (ou outro esquema): a
    linha de comando, já com o link, ou None."""
    scheme = url.split(":", 1)[0].lower()
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme):
        return None
    try:
        r = subprocess.run(["xdg-mime", "query", "default",
                            "x-scheme-handler/" + scheme],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    name = r.stdout.strip()
    if not name.endswith(".desktop") or "/" in name:
        return None
    dirs = [os.environ.get("XDG_DATA_HOME")
            or os.path.expanduser("~/.local/share")]
    dirs += (os.environ.get("XDG_DATA_DIRS")
             or "/usr/local/share:/usr/share").split(":")
    for d in dirs:
        path = os.path.join(d, "applications", name)
        if d and os.path.isfile(path):
            line = _desktop_entry_field(path, "Exec")
            return desktop_argv(line, url) if line else None
    return None


class Dock(QObject):
    """A Doca: um KWin só do WayClick, numa janela, com o jogo dentro.

    start() sobe um D-Bus próprio, o kwin_wayland em janela (tamanho fixo,
    configuração numa pasta própria: ele grava a configuração de telas lá, e
    não na do desktop) e, se houver comando, o jogo, com WAYLAND_DISPLAY
    apontando para a Doca e o resto do ambiente de quem usa (o D-Bus da
    sessão inclusive, então portais e chaveiro funcionam como sempre).
    stop() fecha o KWin dela, e com ele o jogo. Vive na thread da UI; os
    macros só usam input, pointer, screen, windows e open, de qualquer
    thread.
    """
    changed = Signal()
    _sized = Signal(int, int)
    _lost = Signal()

    def __init__(self):
        super().__init__()
        self.size = DOCK_SIZES[0]
        self.command = DOCK_GAME
        self.state, self.error = "off", ""
        self.note = ""                   # aviso que não impede a Doca
        self.kwin = self.bus_proc = None
        self.games = []
        self.link = self.input = self.pointer = self.windows = None
        self.screen = DockScreen(self)
        self.out_size = None
        self._fixes = 0
        self._log = None
        self._own = False                # a pasta da Doca é desta vez
        self._sized.connect(self._on_sized, Qt.QueuedConnection)
        self._lost.connect(self._check, Qt.QueuedConnection)
        self._fix_timer = QTimer(self)
        self._fix_timer.setSingleShot(True)
        self._fix_timer.setInterval(DOCK_FIX_MS)
        self._fix_timer.timeout.connect(self._fix)
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._check)
        self._game_alive = False
        self.account = None              # SoberAccount: o Sober daqui é dela
        self._accounts = {}              # id da conta -> Popen que abriu aqui

    # ---------------------------------------------------------- lugares --
    @staticmethod
    def runtime():
        return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()

    def folder(self):
        return os.path.join(self.runtime(), "wayclick-dock")

    def bus_address(self):
        return "unix:path=" + os.path.join(self.folder(), "bus")

    def socket_path(self):
        return os.path.join(self.runtime(), DOCK_SOCKET)

    # ------------------------------------------------------------ estado --
    def alive(self):
        # os macros perguntam das threads deles: nada de ler o atributo duas
        # vezes enquanto a UI fecha a Doca
        kwin, link = self.kwin, self.link
        return (self.state == "on" and kwin is not None
                and kwin.poll() is None and link is not None
                and not link.closed)

    def game_running(self):
        self.games = [p for p in self.games if p.poll() is None]
        return bool(self.games)

    def missing(self):
        """O que falta neste computador para a Doca, ou ""."""
        if not os.environ.get("WAYLAND_DISPLAY"):
            return _("The Dock needs a Wayland session of KDE Plasma.")
        if not shutil.which("kwin_wayland"):
            return _("The Dock needs KWin (kwin_wayland), the compositor of "
                     "KDE Plasma.")
        if not shutil.which("dbus-daemon"):
            return _("The Dock needs dbus-daemon (the dbus or dbus-daemon "
                     "package).")
        return ""

    # ------------------------------------------------------------- abrir --
    def start(self, launch=True):
        """Abre a Doca (e o jogo, com launch). Na thread da UI; volta em
        menos de um segundo, quando o KWin dela responde."""
        if self.state == "starting":
            return False
        if self.alive():
            if launch and not self.game_running():
                self.launch()
            return True
        self._teardown()
        why = self.missing()
        if why:
            self.state, self.error = "failed", why
            self.changed.emit()
            return False
        self.state, self.error, self.note = "starting", "", ""
        self.changed.emit()
        try:
            self._spawn()
        except (DockError, OSError) as e:
            log = self._log_tail()
            self._teardown()
            self.state = "failed"
            self.error = str(e) + (("\n" + log) if log else "")
            self.changed.emit()
            return False
        self.state = "on"
        self._fixes = 0
        self._poll.start()
        self.changed.emit()
        if launch and self.command.strip():
            self.launch()
        return True

    def _write(self, path, text):
        with open(path, "w") as fh:
            fh.write(text)

    def _spawn(self):
        d = self.folder()
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        self._reap_stale()
        self._own = True
        cfg = os.path.join(d, "config")
        os.makedirs(cfg, exist_ok=True)
        self._write(os.path.join(cfg, "kwinrc"), DOCK_KWINRC)
        self._write(os.path.join(cfg, "kdeglobals"), DOCK_KDEGLOBALS)
        for name in DOCK_COPY:
            src, dst = os.path.join(XDG_CONFIG, name), os.path.join(cfg, name)
            try:
                shutil.copyfile(src, dst)
            except OSError:
                try:
                    os.remove(dst)
                except OSError:
                    pass
        bus_path = os.path.join(d, "bus")
        try:
            os.remove(bus_path)
        except OSError:
            pass
        esc = bus_path.replace("&", "&amp;").replace("<", "&lt;") \
            .replace(">", "&gt;")
        conf = os.path.join(d, "bus.conf")
        self._write(conf, DOCK_BUS_CONF % esc)
        self._log = open(os.path.join(d, "dock.log"), "wb")
        self.bus_proc = subprocess.Popen(
            ["dbus-daemon", "--config-file=" + conf, "--nofork",
             "--nopidfile", "--nosyslog"], stdin=subprocess.DEVNULL,
            stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        if not self._wait(lambda: os.path.exists(bus_path), 5.0,
                          self.bus_proc):
            raise DockError(_("The D-Bus of the Dock did not start."))
        env = dict(os.environ)
        for k in ("DISPLAY", "WAYLAND_SOCKET", "XDG_ACTIVATION_TOKEN",
                  "DESKTOP_STARTUP_ID", "QT_QPA_PLATFORM"):
            env.pop(k, None)
        env.update(DBUS_SESSION_BUS_ADDRESS=self.bus_address(),
                   XDG_CONFIG_HOME=cfg,
                   XDG_STATE_HOME=os.path.join(d, "state"),
                   XDG_CACHE_HOME=os.path.join(d, "cache"))
        w, h = self.size
        self.kwin = subprocess.Popen(
            ["kwin_wayland", "--wayland-display",
             os.environ["WAYLAND_DISPLAY"], "--socket", DOCK_SOCKET,
             "--width", str(int(w)), "--height", str(int(h)),
             "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities"],
            env=env, stdin=subprocess.DEVNULL, stdout=self._log,
            stderr=subprocess.STDOUT, start_new_session=True)
        self._write(os.path.join(d, "pids"), f"{self.kwin.pid} "
                    f"{self.bus_proc.pid} {os.getpid()}\n")
        self.windows = KWinBridge(self.bus_address())
        if not self.windows.ok:
            raise DockError(self.windows.error or "D-Bus")
        sock = self.socket_path()
        if not self._wait(lambda: os.path.exists(sock)
                          and self.windows.has_kwin(), DOCK_START_S,
                          self.kwin):
            raise DockError(_("The KWin of the Dock did not start."))
        self.link = WaylandLink(sock, on_output=self._sized.emit,
                                on_close=self._lost.emit)
        self.input = DockInput(self.link)
        self.pointer = DockPointer(self.input)
        self.out_size = self.link.size() or (int(w), int(h))

    def _wait(self, cond, seconds, proc=None):
        app = QApplication.instance()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return True
            if proc is not None and proc.poll() is not None:
                return False
            if app is not None and \
                    threading.current_thread() is threading.main_thread():
                app.processEvents()
            time.sleep(0.02)
        return cond()

    def _log_tail(self, n=600):
        try:
            with open(os.path.join(self.folder(), "dock.log"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - n))
                return fh.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def _reap_stale(self):
        """Fecha a Doca que um WayClick que caiu deixou aberta. Só mata o que
        é dela: o pid gravado, se a linha de comando ainda for a da Doca. A
        de um WayClick que ainda está aberto fica, e esta não abre."""
        path = os.path.join(self.folder(), "pids")
        try:
            with open(path) as fh:
                nums = [int(x) for x in fh.read().split()[:3]]
        except (OSError, ValueError):
            return
        pids, owner = nums[:2], (nums[2] if len(nums) > 2 else 0)
        if owner and owner != os.getpid() and _is_wayclick(owner):
            raise DockError(_("The Dock is already open in another WayClick "
                              "window."))
        conf = os.path.join(self.folder(), "bus.conf").encode()
        for pid in pids:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    argv = fh.read().split(b"\0")
            except OSError:
                continue
            ours = (b"--socket" in argv and DOCK_SOCKET.encode() in argv) or \
                (b"--config-file=" + conf) in argv
            if not ours:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
            end = time.monotonic() + 3.0
            while time.monotonic() < end and os.path.exists(f"/proc/{pid}"):
                time.sleep(0.05)
            if os.path.exists(f"/proc/{pid}"):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------- o jogo --
    def game_env(self):
        e = dict(os.environ)
        for k in ("DISPLAY", "WAYLAND_SOCKET", "XDG_ACTIVATION_TOKEN",
                  "DESKTOP_STARTUP_ID", "QT_QPA_PLATFORM"):
            e.pop(k, None)
        e["WAYLAND_DISPLAY"] = DOCK_SOCKET
        e["XDG_SESSION_TYPE"] = "wayland"
        return e

    def _run(self, argv, account=None):
        if account is not None:
            # a conta abre direto (sem escopo próprio): o Sober dela, como
            # qualquer jogo da Doca, fecha junto com a Doca
            p = account.launch(argv, env=self.game_env(), prefix=[])
            self._accounts[account.id] = p
        else:
            out = self._log if self._log is not None else subprocess.DEVNULL
            p = subprocess.Popen(argv, env=self.game_env(),
                                 stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True)
            threading.Thread(target=p.wait, daemon=True,
                             name="wayclick-dock-game").start()
        self.games.append(p)
        return p

    SOBER = SOBER_APP

    def is_sober(self, argv):
        return any(self.SOBER in a for a in argv)

    def account_here(self, account):
        """A conta está aberta e foi a Doca que abriu."""
        p = self._accounts.get(account.id)
        return p is not None and p.poll() is None and account.running()

    def sober_outside(self, argv):
        """O comando abre o Sober e já há um Sober aberto fora da Doca, na
        mesma rede. Ele roda uma cópia só por rede (segura uma porta fixa do
        127.0.0.1), então o da Doca não chegaria a abrir. O que a Doca abriu
        não conta, nem o de uma conta do Sober, que tem a rede dela."""
        if not self.is_sober(argv):
            return False
        mine = {p.pid for p in self.games if p.poll() is None}
        here = netns_of()
        for line in self._flatpak_ps().splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1] != self.SOBER:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            # o flatpak run vira o bwrap (mesmo pid) ou é o pai dele
            if pid in mine or _ppid(pid) in mine:
                continue
            there = netns_of(pid)
            if here and there and there != here:
                continue
            return True
        return False

    @staticmethod
    def _flatpak_ps():
        try:
            return subprocess.run(
                ["flatpak", "ps", "--columns=pid,application"],
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def launch(self, extra=()):
        """Abre o comando do jogo dentro da Doca. False se não deu."""
        try:
            argv = shlex.split(self.command) + list(extra)
        except ValueError:
            argv = []
        if not argv or not self.alive():
            return False
        acc = self.account if self.is_sober(argv) else None
        if acc is not None and acc.running():
            self.note = _(SOBER_ACCOUNT_HERE if self.account_here(acc)
                          else SOBER_TWICE_ACCOUNT, name=acc.name)
            self.changed.emit()
            return False
        if acc is None and self.sober_outside(argv):
            self.note = _(SOBER_TWICE)
            self.changed.emit()
            return False
        self.note = ""
        try:
            if acc is not None:
                self._run(argv, account=acc)
            else:
                self._run(argv)
        except OSError as e:
            self.note = _("The game did not open: {err}", err=e)
            self.changed.emit()
            return False
        self._game_alive = True
        self.changed.emit()
        return True

    def open(self, url, report=None):
        """O passo Abrir de um macro da Doca. Link da web (e steam://, que o
        Steam aberto no desktop atende) abre no desktop, como sempre; um
        link de app, como roblox://, abre o app dele dentro da Doca."""
        url = (url or "").strip()
        if not url or url.startswith("-"):
            return
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme in ("", "http", "https", "steam", "file", "mailto"):
            OPENER(url)
            return
        if not self.alive():
            if report:
                report(_("the Dock is not open, so {url} was not opened.",
                         url=url))
            return
        argv = scheme_handler(url)
        if not argv:
            try:
                base = shlex.split(self.command)
            except ValueError:
                base = []
            argv = base + [url] if base else None
        if not argv:
            if report:
                report(_("nothing on this computer opens {url}.", url=url))
            return
        acc = self.account if self.is_sober(argv) else None
        if acc is not None and acc.running():
            # o Sober só recebe um link ao abrir: um segundo na mesma conta
            # para em "An instance of Sober is already running" (medido em
            # tests/t_sober_accounts_kwin.py), então nada abre aqui
            if report:
                report(_(SOBER_ACCOUNT_LINK, name=acc.name, url=url)
                       if self.account_here(acc)
                       else _(SOBER_TWICE_ACCOUNT, name=acc.name))
            return
        if acc is None and self.sober_outside(argv):
            if report:
                report(_(SOBER_TWICE))
            return
        try:
            if acc is not None:
                self._run(argv, account=acc)
            else:
                self._run(argv)
        except OSError as e:
            if report:
                report(_("{url} did not open: {err}", url=url, err=e))

    def cursor(self):
        """Onde está o cursor dentro da Doca (o gravador pergunta)."""
        kw = self.windows
        return kw.cursor() if kw is not None and kw.ok else None

    # ---------------------------------------------- a janela no desktop --
    def _host(self, fix=False, show=False, hide=False):
        if self.kwin is None or self.kwin.poll() is not None:
            return None
        kw = KWIN()
        if not kw.ok:
            return None
        w, h = self.size
        raw = kw.call(DOCK_HOST_JS, pid=int(self.kwin.pid),
                      fix="true" if fix else "false",
                      show="true" if show else "false",
                      hide="true" if hide else "false", w=int(w), h=int(h))
        try:
            info = json.loads(raw) if raw else None
        except ValueError:
            return None
        return info if isinstance(info, dict) else None

    def show(self):
        """Traz a janela da Doca para a frente, mesmo minimizada."""
        return self._host(show=True)

    def hide(self):
        """Minimiza a janela da Doca no desktop (o jogo segue, e a aba Tower
        Defense mostra ele)."""
        return self._host(hide=True)

    def set_size(self, w, h):
        self.size = (int(w), int(h))
        if self.alive():
            self._fixes = 0
            self._host(fix=True)

    def _on_sized(self, w, h):
        self.out_size = (w, h)
        if self.state != "on":
            return
        if (w, h) == tuple(self.size):
            self._fixes = 0
            if self.note:
                self.note = ""
            self.changed.emit()
            return
        self._fix_timer.start()
        self.changed.emit()

    def _fix(self):
        if not self.alive() or self.out_size == tuple(self.size):
            return
        if self._fixes >= DOCK_FIX_TRIES:
            w, h = self.out_size or (0, 0)
            self.note = _("The Dock window is {w}×{h}, not {W}×{H}: does "
                          "it fit on the screen? Macros made at {W}×{H} "
                          "may miss.", w=w, h=h, W=self.size[0],
                          H=self.size[1])
            self.changed.emit()
            return
        self._fixes += 1
        self._host(fix=True)
        self._fix_timer.start()

    # ------------------------------------------------------------ fechar --
    def _check(self):
        """O KWin da Doca saiu (a janela dela foi fechada) ou o jogo
        fechou: o estado acompanha."""
        if self.state == "on" and not self.alive():
            self._teardown()
            self.state, self.error = "off", ""
            self.changed.emit()
            return
        running = self.game_running()
        if running != self._game_alive:
            self._game_alive = running
            self.changed.emit()

    def stop(self):
        was = self.state
        self._teardown()
        self.state, self.error, self.note = "off", "", ""
        if was != "off":
            self.changed.emit()

    def _teardown(self):
        self._poll.stop()
        self._fix_timer.stop()
        self.screen.stop()
        if self.link is not None:
            self.link.on_close = None
            self.link.close()
        self.link = self.input = self.pointer = None
        if self.windows is not None:
            self.windows.close()
            self.windows = None
        for p in (self.kwin, self.bus_proc):
            if p is None or p.poll() is not None:
                continue
            p.terminate()
            try:
                p.wait(3.0)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(2.0)
                except subprocess.TimeoutExpired:
                    pass
        self.kwin = self.bus_proc = None
        self.out_size = None
        self._game_alive = False
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
        if self._own:
            # só a Doca que abriu limpa: a de outro WayClick fica como está
            self._own = False
            for name in ("pids", "bus"):
                try:
                    os.remove(os.path.join(self.folder(), name))
                except OSError:
                    pass


# ------------------------------------------------- as contas do Sober ----
# O Sober roda uma cópia por vez: ele segura a porta 54587 do 127.0.0.1, e o
# segundo, na mesma rede, para em "There's a frozen instance of this app
# running in the background". Cada conta daqui é um Sober com a rede própria
# (o pasta, do passt, monta uma rede só dele, com saída para a internet e sem
# enxergar o 127.0.0.1 do computador) e a pasta própria (a da conta, montada
# por cima de ~/.var/app/org.vinegarhq.Sober só para ele, então o login, as
# configurações e os downloads são dela). O Sober de sempre e os arquivos
# dele ficam como estão; o binário do Sober não muda.
SOBER_ACCOUNTS_MAX = 4
SOBER_DNS = "169.254.1.1"     # o pasta atende o DNS aqui, dentro da rede
SOBER_BUSY = 75               # o flock sai com isto: a conta já está aberta
# Já como root do namespace do pasta, na rede e no namespace de montagem
# novos: o DNS do pasta no lugar do resolv.conf (o do computador costuma ser
# o 127.0.0.53 do systemd-resolved, que dali não se alcança), a pasta da
# conta por cima da do Sober e, antes do flatpak, de volta ao usuário de
# verdade. O arquivo "run" guarda o pid que vira o flatpak e a rede dele.
# Sem "$$" nem "${": o systemd-run do escopo troca "$$" por "$" e expande
# "${X}" nos argumentos (o pid sai do /proc/self/stat do próprio sh).
# O unshare também dá ao Sober um namespace de tempo dele (os relógios ficam
# iguais, sem deslocamento): o Sober trava trechos do /proc/self/ns/time (no
# /proc/locks, umas cem travas READ em 00:07:<inode do namespace de tempo>),
# e com o mesmo namespace do Sober de sempre o da conta parava entre 1 e 3
# minutos depois de abrir em "status != SL_RESULT_SUCCESS" (o áudio dele).
SOBER_INNER = r'''set -e
data=$1; target=$2; resolv=$3; run=$4; uid=$5; gid=$6; shift 6
real=$(readlink -f /etc/resolv.conf) || real=/etc/resolv.conf
if [ -e "$real" ]; then mount --bind "$resolv" "$real"; fi
mkdir -p "$target"
mount --bind "$data" "$target"
read -r pid rest < /proc/self/stat
printf '%s\n%s\n' "$pid" "$(readlink /proc/self/ns/net)" > "$run.tmp"
mv -f "$run.tmp" "$run"
exec unshare --user --time --map-user="$uid" --map-group="$gid" -- "$@"
'''
SOBER_TWICE_ACCOUNT = ("{name} is already open outside the Dock. Close it, "
                       "then press Start the game.")
SOBER_ACCOUNT_HERE = "{name} is already open in the Dock."
SOBER_ACCOUNT_LINK = ("Sober is already open in {name}, and it takes a link "
                      "only when it opens, so {url} was not opened. Close it "
                      "first.")
_HELP_TEXT = {}


def _help_of(tool):
    """O --help de uma ferramenta (guardado): diz que opções ela tem."""
    if tool not in _HELP_TEXT:
        try:
            r = subprocess.run([tool, "--help"], capture_output=True,
                               text=True, timeout=5)
            _HELP_TEXT[tool] = (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.SubprocessError):
            _HELP_TEXT[tool] = ""
    return _HELP_TEXT[tool]


def sober_pasta():
    """O pasta da conta: rede configurada e nenhuma porta aberta, nem de
    fora para dentro nem de dentro para fora. Com -T auto (o padrão) ele
    traria para dentro as portas do 127.0.0.1 do computador, a 54587 do
    Sober de sempre inclusive, e o da conta não abriria. Sem namespace de
    pid (o pid do flatpak tem que ser o de fora) e sem o 127.0.0.1 do
    computador atrás do gateway, nas versões do pasta que têm as opções."""
    h = _help_of("pasta")
    argv = ["pasta", "--config-net", "--quiet", "-t", "none", "-u", "none",
            "-T", "none", "-U", "none", "--dns-forward", SOBER_DNS]
    if "--no-pidns" in h:
        argv.append("--no-pidns")
    if "--map-host-loopback" in h:
        argv += ["--map-host-loopback", "none"]
    elif "--no-map-gw" in h:
        argv.append("--no-map-gw")
    return argv + ["--"]


def sober_accounts_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    return os.path.join(base, "wayclick", "sober-accounts")


def sober_data_dir(home=None):
    """A pasta do Sober de sempre, a que a conta cobre (só para ela)."""
    return os.path.join(home or os.path.expanduser("~"), ".var", "app",
                        SOBER_APP)


def netns_of(pid="self"):
    """A rede de um processo ("net:[4026531840]"), ou None."""
    try:
        return os.readlink(f"/proc/{pid}/ns/net")
    except (OSError, ValueError):
        return None


def cgroup_unit(pid):
    """A unidade do systemd em que o processo está (o último .scope ou
    .service do cgroup dele), ou ""."""
    try:
        with open(f"/proc/{int(pid)}/cgroup") as fh:
            text = fh.read()
    except (OSError, ValueError):
        return ""
    for line in text.splitlines():
        path = line.split(":", 2)[-1]
        for part in reversed(path.split("/")):
            if part.endswith((".scope", ".service")):
                return part
    return ""


def flatpak_instances(app=SOBER_APP, env=None):
    """[(instância, pid)] das cópias abertas do app, pelo flatpak ps."""
    try:
        out = subprocess.run(
            ["flatpak", "ps", "--columns=instance,pid,application"],
            capture_output=True, text=True, timeout=5, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == app:
            try:
                found.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return found


def userns_allowed():
    """O kernel deixa um usuário comum criar namespaces de usuário (o pasta
    e o unshare precisam)."""
    for path in ("/proc/sys/kernel/unprivileged_userns_clone",
                 "/proc/sys/user/max_user_namespaces"):
        try:
            with open(path) as fh:
                if fh.read().strip() == "0":
                    return False
        except OSError:
            pass
    return True


def sober_tools_missing():
    """O que falta neste computador para as contas do Sober, ou ""."""
    if not shutil.which("flatpak"):
        return _("Sober accounts need Flatpak, with Sober from Flathub.")
    if not shutil.which("pasta"):
        return _("Sober accounts need pasta, from the passt package. "
                 "Arch: sudo pacman -S passt. Fedora: sudo dnf install "
                 "passt. Debian and Ubuntu: sudo apt install passt.")
    for tool in ("unshare", "flock", "mount"):
        if not shutil.which(tool):
            return _("Sober accounts need {tool}, from util-linux.",
                     tool=tool)
    if "--map-user" not in _help_of("unshare"):
        return _("Sober accounts need util-linux 2.38 or newer (unshare "
                 "--map-user).")
    if not userns_allowed():
        return _("Sober accounts need user namespaces, and this system "
                 "turns them off for users.")
    if not os.path.exists("/proc/self/ns/time"):
        return _("Sober accounts need time namespaces (Linux 5.6 or newer).")
    return ""


def sober_installed():
    try:
        return subprocess.run(["flatpak", "info", SOBER_APP],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def normalize_sober_accounts(raw):
    """A lista de contas da configuração: id de 8 hex, nome curto, sem
    repetir, no máximo SOBER_ACCOUNTS_MAX."""
    out, seen = [], set()
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        acc_id = str(a.get("id") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{8}", acc_id) or acc_id in seen:
            continue
        name = " ".join(str(a.get("name") or "").split())[:40]
        seen.add(acc_id)
        out.append({"id": acc_id, "name": name or acc_id})
        if len(out) >= SOBER_ACCOUNTS_MAX:
            break
    return out


class SoberAccount:
    """Uma conta do Sober: a pasta dela, a trava, e o que abre e fecha o
    Sober dela.

    Aberto, o processo é flock (segura a trava enquanto vive) → pasta (a
    rede) → sh com SOBER_INNER (as montagens) → unshare → flatpak run, e o
    flatpak põe o Sober no escopo dele, como sempre. A trava diz se a conta
    está aberta, mesmo depois de o WayClick fechar e abrir de novo; a rede
    gravada no "run" acha a cópia dela no flatpak ps, e nunca a do Sober de
    sempre, que está na rede do computador.
    """

    def __init__(self, acc_id, name):
        self.id, self.name = acc_id, name
        self.proc = None                 # o Popen do último launch()
        self.started = 0.0

    def __repr__(self):
        return f"SoberAccount({self.id!r}, {self.name!r})"

    # ---------------------------------------------------------- lugares --
    def folder(self):
        return os.path.join(sober_accounts_dir(), self.id)

    def data(self):
        """O que o Sober da conta vê como ~/.var/app/org.vinegarhq.Sober."""
        return os.path.join(self.folder(), "app")

    def lock_path(self):
        return os.path.join(self.folder(), "lock")

    def run_path(self):
        return os.path.join(self.folder(), "run")

    def log_path(self):
        return os.path.join(self.folder(), "launch.log")

    def resolv_path(self):
        return os.path.join(self.folder(), "resolv.conf")

    def prepare(self):
        os.makedirs(self.data(), mode=0o700, exist_ok=True)
        os.chmod(self.folder(), 0o700)
        with open(self.resolv_path(), "w") as fh:
            fh.write(f"nameserver {SOBER_DNS}\n")
        with open(self.lock_path(), "a"):
            pass

    # ----------------------------------------------------------- estado --
    def running(self):
        """O Sober da conta está aberto: alguém segura a trava (o flock que
        abriu, que vive enquanto ele vive)."""
        try:
            fd = os.open(self.lock_path(), os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def run_info(self):
        """(pid, rede) que o script de dentro gravou, ou (None, None). A
        rede do computador nunca vale: com ela, a cópia achada seria o Sober
        de sempre."""
        try:
            with open(self.run_path()) as fh:
                pid, net = fh.read().split()[:2]
            pid = int(pid)
        except (OSError, ValueError):
            return None, None
        if not net.startswith("net:[") or net == netns_of():
            return None, None
        return pid, net

    def instance(self, env=None):
        """(instância, pid) da cópia do Sober desta conta, ou None."""
        if not self.running():
            return None
        _pid, net = self.run_info()
        if not net:
            return None
        for inst, pid in flatpak_instances(env=env):
            if netns_of(pid) == net:
                return inst, pid
        return None

    def _member(self):
        """Um processo vivo da conta, na rede dela (para fechar quando o
        flatpak ainda não listou a cópia), ou None."""
        pid, net = self.run_info()
        if pid is not None and net and netns_of(pid) == net:
            return pid
        found = self.instance()
        return found[1] if found else None

    # ------------------------------------------------------------ abrir --
    def command(self, argv, home=None):
        """O argv que roda `argv` (o flatpak run do Sober) nesta conta."""
        return (["flock", "--nonblock", "--close", "--conflict-exit-code",
                 str(SOBER_BUSY), self.lock_path()] + sober_pasta()
                + ["sh", "-c", SOBER_INNER, "wayclick-sober", self.data(),
                   sober_data_dir(home), self.resolv_path(), self.run_path(),
                   str(os.getuid()), str(os.getgid())] + list(argv))

    def scope_prefix(self):
        """Um escopo do systemd só para ela, quando há systemd do usuário:
        fechar o WayClick, ou o serviço que abriu ele, não leva junto a rede
        da conta, que segue aberta."""
        rt = os.environ.get("XDG_RUNTIME_DIR") or ""
        if shutil.which("systemd-run") and rt and \
                os.path.exists(os.path.join(rt, "systemd", "private")):
            argv = ["systemd-run", "--user", "--scope", "--quiet",
                    "--collect", f"--unit=wayclick-sober-{self.id}-"
                    f"{int(time.time() * 1000) % 10 ** 10}"]
            # os argumentos vão como estão (o systemd 254 em diante tem a
            # opção; os de antes expandem "$$" e "${X}", e o script não usa)
            if "--expand-environment" in _help_of("systemd-run"):
                argv.append("--expand-environment=no")
            return argv
        return []

    def launch(self, argv, env=None, prefix=None, home=None):
        """Abre o Sober da conta (argv é o flatpak run dele) e volta o
        Popen. prefix None é o escopo próprio; [] roda direto (a Doca)."""
        if self.running():
            # e o "run" da aberta fica: é por ele que ela é achada e fechada
            raise OSError(_("{name} is already open.", name=self.name))
        self.prepare()
        try:
            os.remove(self.run_path())
        except OSError:
            pass
        cmd = (self.scope_prefix() if prefix is None else list(prefix)) \
            + self.command(argv, home)
        with open(self.log_path(), "wb") as out:
            p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        self.proc, self.started = p, time.monotonic()
        threading.Thread(target=p.wait, daemon=True,
                         name="wayclick-sober-account").start()
        return p

    def log_tail(self, n=400):
        try:
            with open(self.log_path(), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - n))
                return fh.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    # ----------------------------------------------------------- fechar --
    def close(self, env=None):
        """Fecha o Sober da conta, e só ele: o escopo da cópia dele (achada
        pela rede da conta), ou um TERM no processo da conta. True se mandou
        fechar."""
        if not self.running():
            return False
        found = self.instance(env)
        pid = found[1] if found else self._member()
        if pid is None:
            return False
        unit = cgroup_unit(pid)
        if (unit.startswith(f"app-flatpak-{SOBER_APP}-")
                or unit.startswith(f"wayclick-sober-{self.id}-")) \
                and shutil.which("systemctl"):
            try:
                r = subprocess.run(["systemctl", "--user", "stop",
                                    "--no-block", unit],
                                   capture_output=True, timeout=10)
                if r.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------ pasta --
    def disk_bytes(self):
        total = 0
        for root, _dirs, files in os.walk(self.folder()):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total

    def remove_data(self):
        """Apaga a pasta da conta (login, configurações, downloads). Nunca
        com ela aberta, e nunca fora da pasta das contas."""
        if self.running():
            raise OSError(_("Close it first."))
        root = os.path.realpath(sober_accounts_dir())
        path = os.path.realpath(self.folder())
        if os.path.dirname(path) != root or not re.fullmatch(
                r"[0-9a-f]{8}", os.path.basename(path)):
            raise OSError("not an account folder: " + path)
        if os.path.isdir(path):
            shutil.rmtree(path)


def human_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


_DOCK = None


def DOCK():
    """A Doca do app, uma só (o socket dela tem nome fixo). Criada na thread
    da UI."""
    global _DOCK
    if _DOCK is None:
        _DOCK = Dock()
    return _DOCK


class DockEnv:
    """O que um macro da Doca usa no lugar das peças do desktop: o ponteiro,
    a tela e as janelas dela. Avisos vão para o motor, como os dos outros."""

    def __init__(self, engine, dock):
        self.engine, self.dock = engine, dock
        self.pointer = dock.pointer
        self.screen = dock.screen
        self.windows = dock.windows

    @property
    def workspace(self):
        w, h = self.dock.out_size or self.dock.size
        return (0, 0, w, h)

    def report(self, text):
        self.engine.report(text)

    def say(self, title, text):
        self.engine.say(title, text)


class DockRunner(MacroRunner):
    """O mesmo macro, jogado dentro da Doca: teclas, botões, roda e
    movimento vão pelo fake_input do KWin dela, e o passo Abrir abre o app
    do link lá dentro. O resto (tela, janelas, ponteiro) vem do DockEnv."""

    def __init__(self, macro, dock, loop, keep, on_done=None, env=None):
        super().__init__(macro, dock.input, dock.input, loop, keep, on_done,
                         env)
        self.dock, self.link = dock, dock.link

    def _set(self, code, down):
        inp = self.kb
        if inp is None:
            raise BrokenPipeError("the Dock is closed")
        if is_button(code):
            inp.button(code, down)
        else:
            inp.key(code, down)
        if down:
            self.held.append(code)
        elif code in self.held:
            self.held.remove(code)

    # um dente da roda: o fake_input não diz que é um dente (não tem o
    # value120 de uma roda de verdade), e aí o app conta pela distância. O
    # Qt conta 10 como um dente: medido na caixa, 15 (o que o KWin manda
    # junto do dente de uma roda real) virou um dente e meio
    WHEEL_STEP = 10.0

    def _wheel(self, direction, times):
        inp = self.kb
        if inp is None:
            raise BrokenPipeError("the Dock is closed")
        axis = 1 if direction in ("left", "right") else 0
        sign = 1 if direction in ("down", "right") else -1
        for i in range(times):
            if i:
                self._sleep(0.01)
            inp.axis(axis, self.WHEEL_STEP * sign)

    def _gone(self):
        return self.link is None or self.link.closed

    def _step(self, st, i=0):
        if self._gone():
            raise BrokenPipeError("the Dock is closed")
        return super()._step(st, i)

    def _report(self, text):
        # sem a Doca, cada passo acharia um problema diferente (tela, janela,
        # ponteiro); o aviso é um só, e nenhum se foi a pessoa que parou
        if self._gone():
            if self.stopping:
                return
            text = _("the Dock closed, so the macro stopped.")
        super()._report(text)

    def _let_go(self):
        if self._gone():
            # com o KWin dela fechado não há o que soltar: o jogo saiu junto
            self._report("")
            self.held, self.pheld = [], []
            return
        super()._let_go()

    def _open(self, url):
        self.dock.open(url, report=self._report)


# ------------------------------------------------- Tower Defense --------
# A aba Tower Defense: o jeito do NoName Macro (o jogo encaixado na janela,
# as 5 torres com os lugares de cada uma, gravar jogando, F4 começa e F5
# para), só que olhando a tela da Doca: nada lê a memória do Roblox. Sem ler
# dinheiro nem onda, colocar uma torre é tentar até aparecer a imagem de
# "colocou" (o painel da torre), e cada ação tem a hora dela, contada do
# começo da partida. Os lugares são posições na tela da Doca, por isso a
# câmera é alinhada do mesmo jeito em toda partida e na gravação.
TOWER_SLOTS = 5
TOWER_MAX_POINTS = 30          # lugares de uma torre
TOWER_MAX_ACTIONS = 500
TOWER_MAX_STRATS = 50
TOWER_MAX_LOBBY = 8
TOWER_MAX_AFTER = 4
TOWER_ACTS = ("place", "upgrade", "sell", "key", "image")
TOWER_SPEEDS = {"fast": 0.6, "balanced": 1.0, "slow": 1.8}
TOWER_PRESS_MS = {"fast": 40.0, "balanced": 50.0, "slow": 80.0}
TOWER_SCREENS = ("start", "placed", "skip", "victory", "defeat",
                 "reconnect", "sell")
# teclas do TDS segundo o guia do Tower Defense Macro: 1 a 5 as torres, Q
# cancela, E melhora o caminho de cima e Z o de baixo, O afasta a câmera.
# Vender não tem tecla conhecida: fica sem, até a pessoa escolher
TOWER_KEYS = {"top": "E", "bottom": "Z", "sell": "", "cancel": "Q",
              "zoom": "O"}
TOWER_HOTKEYS = {"start": "F4", "stop": "F5", "pause": "F6"}
TOWER_NEAR = 24                # px: um clique a isso de um lugar é a torre dele
TOWER_SAME = 8                 # px: colocar a isso de um lugar é o mesmo lugar
TOWER_COLORS = ("#3daee9", "#f67400", "#27ae60", "#da4453", "#9b59b6")
TOWER_REPORT = ("Match {run}: {result}. {wins} wins, {losses} losses, "
                "farming for {time}.")
TOWER_FILE_KIND = "tower"      # o .wayclick de uma estratégia leva isto


def _tower_point(p):
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        return None
    x, y = (_num(v, -1, -1, COORD, int) for v in p)
    return [x, y] if x >= 0 and y >= 0 else None


def _tower_action(a, towers):
    """Uma ação da linha do tempo, conferida contra as torres (o lugar tem
    que existir). None se não vale."""
    if not isinstance(a, dict) or a.get("do") not in TOWER_ACTS:
        return None
    out = {"t": round(_num(a.get("t"), 0.0, 0.0, 86400.0), 2),
           "do": a["do"]}
    if a["do"] in ("place", "upgrade", "sell"):
        k = _num(a.get("tower"), -1, -1, TOWER_SLOTS - 1, int)
        j = _num(a.get("point"), -1, -1, TOWER_MAX_POINTS - 1, int)
        if k < 0 or j < 0 or j >= len(towers[k]["points"]):
            return None
        out["tower"], out["point"] = k, j
        if a["do"] == "upgrade":
            out["path"] = "bottom" if a.get("path") == "bottom" else "top"
        return out
    if a["do"] == "key":
        code = _num(a.get("code"), 0, 0, 0x2ff, int)
        if not code or is_button(code):
            return None
        out["code"] = code
        out["name"] = str(a.get("name") or key_label(code))[:40]
        return out
    path = str(a.get("path") or "")[:4096]
    if not path:
        return None
    out["path"] = path
    out["click"] = a.get("click", True) is not False
    out["wait"] = round(_num(a.get("wait", 10.0), 10.0, 0.0, 3600.0), 1)
    return out


def normalize_tower_strategy(s):
    """Uma estratégia: as 5 torres (nome e lugares), a linha do tempo, o
    caminho do lobby e o tamanho da Doca em que foi feita."""
    s = s if isinstance(s, dict) else {}
    raw = s.get("towers") if isinstance(s.get("towers"), list) else []
    towers, remap = [], []
    for k in range(TOWER_SLOTS):
        t = raw[k] if k < len(raw) and isinstance(raw[k], dict) else {}
        pts, where = [], {}
        given = t.get("points") if isinstance(t.get("points"), list) else []
        for j, p in enumerate(given[:TOWER_MAX_POINTS]):
            p = _tower_point(p)
            if p is not None:
                where[j] = len(pts)
                pts.append(p)
        towers.append({"name": " ".join(str(t.get("name") or "").split())
                       [:40], "points": pts})
        remap.append(where)
    acts = []
    given = s.get("actions") if isinstance(s.get("actions"), list) else []
    for a in given[:TOWER_MAX_ACTIONS]:
        if isinstance(a, dict) and a.get("do") in ("place", "upgrade",
                                                    "sell"):
            # um lugar que não valeu sumiu: a ação aponta para o certo
            k = _num(a.get("tower"), -1, -1, TOWER_SLOTS - 1, int)
            j = _num(a.get("point"), -1, -1, TOWER_MAX_POINTS - 1, int)
            if k < 0 or j not in remap[k]:
                continue
            a = dict(a, point=remap[k][j])
        a = _tower_action(a, towers)
        if a:
            acts.append(a)
    acts.sort(key=lambda a: a["t"])
    size = s.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        size = [_num(v, 0, 0, COORD, int) for v in size]
        size = size if size[0] > 0 and size[1] > 0 else None
    else:
        size = None
    lobby = [str(p)[:4096] for p in (s.get("lobby") or [])
             if isinstance(p, str) and p][:TOWER_MAX_LOBBY] \
        if isinstance(s.get("lobby"), list) else []
    return {"id": str(s.get("id") or new_macro_id())[:16],
            "name": " ".join(str(s.get("name") or "").split())[:60]
            or "Strategy",
            "notes": str(s.get("notes") or "")[:2000],
            "size": size, "towers": towers, "actions": acts, "lobby": lobby,
            "lobby_macro": str(s.get("lobby_macro") or "")[:16]}


def _tower_key(value, default):
    code = _num(value, 0, 0, 0x2ff, int)
    if code and not is_button(code):
        return code
    return KEYS.get(default, 0) if default else 0


def normalize_tower(c):
    """A configuração inteira da aba (cfg["tower"]): as estratégias, a
    escolhida, as imagens do jogo e os ajustes."""
    c = c if isinstance(c, dict) else {}
    strats, seen = [], set()
    given = c.get("strategies") if isinstance(c.get("strategies"), list) \
        else []
    for s in given[:TOWER_MAX_STRATS]:
        if isinstance(s, dict):
            s = normalize_tower_strategy(s)
            if s["id"] in seen:
                s["id"] = new_macro_id()
            seen.add(s["id"])
            strats.append(s)
    cur = c.get("current")
    if cur not in seen:
        cur = strats[0]["id"] if strats else ""
    sc = c.get("screens") if isinstance(c.get("screens"), dict) else {}
    screens = {k: str(sc.get(k) or "")[:4096] for k in TOWER_SCREENS}
    after = sc.get("after") if isinstance(sc.get("after"), list) else []
    screens["after"] = [str(p)[:4096] for p in after
                        if isinstance(p, str) and p][:TOWER_MAX_AFTER]
    kc = c.get("keys") if isinstance(c.get("keys"), dict) else {}
    keys = {}
    for name, dflt in TOWER_KEYS.items():
        keys[name] = _tower_key(kc.get(name, KEYS.get(dflt, 0)), dflt) \
            if name in kc or dflt else 0
    hc = c.get("hotkeys") if isinstance(c.get("hotkeys"), dict) else {}
    hot = {n: _tower_key(hc.get(n, KEYS[d]), d)
           for n, d in TOWER_HOTKEYS.items()}
    return {
        "strategies": strats, "current": cur, "screens": screens,
        "window": " ".join(str(c.get("window") or "").split())[:100]
        or "Sober",
        "link": str(c.get("link") or "").strip()[:2000],
        "lobby_wait": _num(c.get("lobby_wait", 60), 60, 1, 3600, int),
        "start_wait": _num(c.get("start_wait", 180), 180, 1, 3600, int),
        "match_max": _num(c.get("match_max", 40), 40, 1, 600, int),
        "limit": _num(c.get("limit", 0), 0, 0, MAX_RUNS, int),
        "speed": c.get("speed") if c.get("speed") in TOWER_SPEEDS
        else "balanced",
        "place_for": _num(c.get("place_for", 120), 120, 5, 3600, int),
        "retry": round(_num(c.get("retry", 2.0), 2.0, 0.2, 60.0), 1),
        "skip_until": _num(c.get("skip_until", 0), 0, 0, 600, int),
        "camera": bool(c.get("camera", True)),
        "zoom_ms": _num(c.get("zoom_ms", 1500), 1500, 0, 10000, int),
        "drag": _num(c.get("drag", 300), 300, -3000, 3000, int),
        "keys": keys, "hotkeys": hot,
        "hook": str(c.get("hook") or "").strip()[:500],
        "shot": bool(c.get("shot", True)),
        "report": str(c.get("report") or TOWER_REPORT)[:1000],
        "conf": round(_num(c.get("conf", 0.85), 0.85, 0.5, 1.0), 3),
    }


def tower_link(text):
    """O link que abre o jogo dentro da Doca, ou "" se ele abriria fora
    dela. roblox:// fica como veio; a página de um jogo do Roblox vira
    roblox://placeId=ID e o link de um servidor privado
    (privateServerLinkCode) roblox://placeId=ID&linkCode=CÓDIGO, os dois
    parâmetros que a página de deep links do Roblox documenta. Link de
    compartilhar (roblox.com/share) não tem forma documentada no app e
    abriria no navegador, fora da Doca."""
    link = farm_link(text)
    return link if link.lower().startswith("roblox://") else ""


def tower_name(s, k):
    name = s["towers"][k]["name"]
    return f"{k + 1} {name}" if name else _("tower {n}", n=k + 1)


def tower_action_text(a, s):
    """'Place 1 Scout at spot 2' para a lista e o registro."""
    do = a["do"]
    if do in ("place", "upgrade", "sell"):
        who = tower_name(s, a["tower"])
        spot = a["point"] + 1
        if do == "place":
            return _("Place {tower} at spot {n}", tower=who, n=spot)
        if do == "sell":
            return _("Sell {tower} at spot {n}", tower=who, n=spot)
        return (_("Upgrade {tower} at spot {n}, top path", tower=who, n=spot)
                if a["path"] == "top" else
                _("Upgrade {tower} at spot {n}, bottom path", tower=who,
                  n=spot))
    if do == "key":
        return _("Press {key}", key=a["name"])
    name = os.path.basename(a["path"])
    return (_("Click the picture {name}", name=name) if a["click"]
            else _("Wait for the picture {name}", name=name))


def tower_time(t):
    """1:02.5, a hora de uma ação contada do começo da partida."""
    m, s = divmod(max(0.0, float(t)), 60.0)
    return f"{int(m)}:{s:04.1f}"


def tower_uses(s, k, j=None):
    """Ações da linha do tempo que usam a torre k (no lugar j)."""
    return [a for a in s["actions"] if a.get("tower") == k
            and (j is None or a.get("point") == j)]


def tower_drop_point(s, k, j):
    """Tira o lugar j da torre k e as ações que usavam ele; as dos lugares
    seguintes passam a apontar um a menos."""
    s["towers"][k]["points"].pop(j)
    keep = []
    for a in s["actions"]:
        if a.get("tower") == k and "point" in a:
            if a["point"] == j:
                continue
            if a["point"] > j:
                a = dict(a, point=a["point"] - 1)
        keep.append(a)
    s["actions"] = keep


def export_tower(strategy, screens):
    """(dados para json.dump, caminhos que não deu para ler). A estratégia
    com as imagens dentro, e as telas do jogo junto, para quem recebe não
    precisar capturar tudo de novo. O macro do lobby não vai: ele é um
    macro da aba Atalhos, e o webhook nem entra aqui."""
    s = normalize_tower_strategy(strategy)
    pack = _Packer()
    s["lobby_macro"] = ""
    s["lobby"] = [x for x in (pack(p) for p in s["lobby"]) if x]
    for a in s["actions"]:
        if a["do"] == "image":
            a["path"] = pack(a["path"])
    s["actions"] = [a for a in s["actions"] if a.get("path", "x")]
    sc = {k: pack(screens.get(k) or "") for k in TOWER_SCREENS}
    sc["after"] = [x for x in (pack(p) for p in screens.get("after") or ())
                   if x]
    return ({"wayclick": 1, "app": f"WayClick {VERSION}",
             "kind": TOWER_FILE_KIND,
             "tower": {"strategy": s, "screens": sc},
             "images": pack.images}, pack.missing)


def import_tower(data, screens, folder=None, dry=False):
    """Lê o que export_tower gravou: (estratégia, telas a preencher,
    avisos). As imagens vão para a pasta de imagens (a mesma não é copiada
    duas vezes); das telas do jogo, só as que estão vazias aqui ganham as
    do arquivo. ValueError se não for um arquivo de estratégia."""
    if not isinstance(data, dict) or data.get("wayclick") != 1 \
            or data.get("kind") != TOWER_FILE_KIND \
            or not isinstance(data.get("tower"), dict):
        raise ValueError("not a WayClick Tower Defense file")
    images = data.get("images") if isinstance(data.get("images"), dict) \
        else {}
    saved = _save_file_images(images, folder or images_dir(), dry)
    t = data["tower"]
    raw = t.get("strategy") if isinstance(t.get("strategy"), dict) else {}
    s = dict(raw, id=new_macro_id(), lobby_macro="")
    s["lobby"] = [saved.get(p, "") for p in (raw.get("lobby") or [])
                  if isinstance(p, str)] \
        if isinstance(raw.get("lobby"), list) else []
    acts = []
    for a in raw.get("actions") or [] if isinstance(raw.get("actions"),
                                                    list) else []:
        if isinstance(a, dict) and a.get("do") == "image":
            a = dict(a, path=saved.get(str(a.get("path")), ""))
        acts.append(a)
    s["actions"] = acts
    s = normalize_tower_strategy(s)
    given = t.get("screens") if isinstance(t.get("screens"), dict) else {}
    fill, kept = {}, 0
    for k in TOWER_SCREENS:
        got = saved.get(str(given.get(k) or ""), "")
        if got and not screens.get(k):
            fill[k] = got
        elif got:
            kept += 1
    after = [saved.get(p, "") for p in (given.get("after") or [])
             if isinstance(p, str)] if isinstance(given.get("after"),
                                                  list) else []
    after = [p for p in after if p][:TOWER_MAX_AFTER]
    if after and not screens.get("after"):
        fill["after"] = after
    elif after:
        kept += 1
    notes = []
    if fill:
        notes.append(_("{n} empty pictures of the Screens tab come from the "
                       "file.", n=len(fill)))
    if kept:
        notes.append(_("{n} pictures of the Screens tab were kept as you "
                       "captured them.", n=kept))
    if s["size"]:
        notes.append(_("It was made with the Dock at {w}×{h}.",
                       w=s["size"][0], h=s["size"][1]))
    return s, fill, notes


class _TowerStop(Exception):
    """O farm acabou: mandaram parar, chegou no limite ou não tem como."""


class TowerEnv(DockEnv):
    """O DockEnv do farm: avisos e o registro vão para a fila que a aba
    lê, em vez do motor dos macros."""

    def __init__(self, dock):
        super().__init__(None, dock)
        self.lines = []
        self.lock = threading.Lock()

    def log(self, text, warn=False):
        with self.lock:
            self.lines.append((time.time(), bool(warn), str(text)))
            del self.lines[:-500]

    def take(self):
        with self.lock:
            out, self.lines = self.lines, []
        return out

    def report(self, text):
        self.log(text, warn=True)

    def say(self, title, text):
        self.log(text)


class TowerRunner(DockRunner):
    """O farm da aba Tower Defense, na Doca. Um laço de partidas: o jogo
    aberto (pelo link, se fechou), o lobby (o macro dele e os botões, em
    ordem), o começo da partida, a câmera, a estratégia na hora de cada ação
    e o fim (vitória, derrota ou o botão de depois), contando e avisando.
    Teclas, cliques, tela e vigias são os do DockRunner; os vigias do farm
    (jogo fechado, reconectar, fim, pular onda) têm índice negativo e
    convivem com os de um macro de lobby."""
    W_CLOSED, W_RECONNECT, W_VICTORY, W_DEFEAT, W_AFTER, W_SKIP = range(-6, 0)
    SLICE_S = 0.25           # esperas em fatias: pausa e parar valem logo
    OPEN_S = 120.0           # quanto esperar o jogo abrir pelo link
    MAX_MISSES = 5           # rodadas seguidas sem partida: algo não bate

    def __init__(self, plan, dock, env, on_done=None, camera_only=False):
        m = normalize_macro({"name": "Tower Defense", "dock": True,
                             "mode": "once", "repeat": False,
                             "press_ms": TOWER_PRESS_MS[plan["speed"]],
                             "steps": [{"op": "wait", "ms": 0}]})
        super().__init__(CompiledMacro(m), dock, False, False, on_done, env)
        self.plan = plan
        self.k = TOWER_SPEEDS[plan["speed"]]
        self.camera_only = camera_only
        self.counters = {"matches": 0, "wins": 0, "losses": 0}
        self.result = ""
        self.paused = False
        self.paused_s = 0.0      # tempo total em pausa
        self.t0 = None           # começo da partida (perf_counter)
        self._skip_held = None   # o vigia de pular onda, tirado numa ação
        self.phase = ""
        self._phase_back = ""

    # ------------------------------------------------------- o registro --
    def _log(self, text):
        self.env.log(text)

    def _say(self, text):
        self.phase = text

    def _report(self, text):
        # um aviso por problema, como nos macros, mas sem o nome na frente:
        # o registro já é só do farm
        if self._gone():
            if self.stopping:
                return
            text = _("the Dock closed, so the farm stopped.")
        if not text or text in self._told:
            return
        self._told.add(text)
        self.env.report(text[:1].upper() + text[1:])

    def fill(self, text):
        c = self.counters
        vals = {"run": str(c["matches"]), "matches": str(c["matches"]),
                "wins": str(c["wins"]), "losses": str(c["losses"]),
                "result": self.result,
                "time": clock_text(time.perf_counter() - self.began)}
        return FIELD_RE.sub(lambda mo: vals.get(mo.group(1).lower(),
                                                mo.group(0)), text)

    def _flow(self, st, ok):
        tag = st.get("tag")
        if tag:
            return tag if ok else None
        return super()._flow(st, ok)

    # ------------------------------------------------------- as esperas --
    def _hold(self):
        """Em pausa: nada fica apertado e o relógio da partida para.
        Devolve quanto tempo ficou parado."""
        if not self.paused or self._halt.is_set():
            return 0.0
        self._let_go()
        t = time.perf_counter()
        self._phase_back, self.phase = self.phase, _("Paused")
        self._log(_("Paused."))
        while self.paused and not self._halt.is_set():
            self._halt.wait(0.1)
        took = time.perf_counter() - t
        self.paused_s += took
        if self.t0 is not None:
            self.t0 += took
        for w in self.watches.values():
            w[2] += took
        if not self._halt.is_set():
            self.phase = self._phase_back
            self._log(_("Resumed."))
        return took

    def _rest(self, seconds):
        self._hold()
        super()._rest(seconds)

    def _nap(self, seconds):
        """Uma fatia de espera com os vigias, sem a pausa (quem chama
        cuida dela)."""
        MacroRunner._rest(self, seconds)

    def _wait(self, seconds):
        end = time.perf_counter() + seconds
        while True:
            if self._halt.is_set():
                raise _TowerStop()
            end += self._hold()
            left = end - time.perf_counter()
            if left <= 0:
                return
            self._nap(min(left, self.SLICE_S))

    def _img(self, path, want="seen"):
        return {"op": "image", "kind": "image", "path": path,
                "conf": self.plan["conf"], "want": want}

    def _seek(self, path, wait, want="seen"):
        """Espera a imagem aparecer (ou sumir) por até wait segundos, com
        vigias e pausa: (aconteceu, ponto achado)."""
        if not path:
            return False, None
        cap = self._screen()
        if cap is None:
            return False, None
        st = self._img(path, want)
        end = time.perf_counter() + wait
        while True:
            if self._halt.is_set():
                raise _TowerStop()
            end += self._hold()
            frames = cap.frames()
            if not frames:
                self._blind = True
                self._report(cap.error or _("Screen sharing stopped."))
                return False, None
            try:
                hit = self._check(st, frames)
            except VisionError as e:
                self._report(str(e))
                return False, None
            if (hit is not None) == (want == "seen"):
                return True, hit
            left = end - time.perf_counter()
            if left <= 0:
                return False, None
            self._nap(min(self.POLL_S, left))

    def _click(self, at):
        if at is not None and self._go(int(at[0]), int(at[1])):
            self._pclick("left")

    # -------------------------------------------------------- os vigias --
    def _arm(self, *names):
        sc, now = self.plan["screens"], time.perf_counter()
        for name in names:
            if name == "closed":
                st = {"op": "watch", "kind": "window", "want": "gone",
                      "match": self.plan["window"], "act": "none",
                      "tag": "closed"}
                self.watches[self.W_CLOSED] = [st, 5.0, now + 5.0]
                continue
            if name == "skip" and not self._skipping():
                continue
            if name == "after":
                path = sc["after"][0] if sc["after"] else ""
            else:
                path = sc.get(name) or ""
            if not path:
                continue
            st = dict(self._img(path), op="watch",
                      act="left" if name in ("skip", "reconnect") else "none")
            if name == "skip":
                st["ok"] = "next"
            else:
                st["tag"] = "over" if name == "after" else name
            key = {"reconnect": self.W_RECONNECT, "victory": self.W_VICTORY,
                   "defeat": self.W_DEFEAT, "after": self.W_AFTER,
                   "skip": self.W_SKIP}[name]
            every = 1.0 if name == "skip" else 1.5
            self.watches[key] = [st, every, now + every]

    def _skipping(self):
        until = self.plan["skip_until"]
        return bool(self.plan["screens"]["skip"]) and (
            not until or self.t0 is None
            or time.perf_counter() - self.t0 < until * 60.0)

    def _pass(self, steps):
        """Uma volta pelos passos de um macro (o do lobby), como no laço do
        MacroRunner. Os vigias dos passos valem só nela; um vigia do farm
        (índice negativo) que dispara sobe."""
        mine = {k: w for k, w in self.watches.items() if k < 0}
        i = 0
        while i < len(steps):
            if self._halt.is_set():
                raise _TowerStop()
            self._hold()
            try:
                if self.watches:
                    self._watch()
                to = self._step(steps[i], i)
            except _Jump as j:
                if not isinstance(j.to, int):
                    raise
                to = j.to
                self.watches = dict(mine)
                self._let_go()
            if to is None:
                i += 1
            elif to == self.STOP:
                break
            else:
                if to <= i:
                    self._sleep(self.MIN_JUMP_S)
                i = max(0, to)
                for idx in [k for k in self.watches if k >= i]:
                    del self.watches[idx]
        self._let_go()
        for idx in [k for k in self.watches if k >= 0]:
            del self.watches[idx]

    # ---------------------------------------------------------- o farm --
    def run(self):
        try:
            if self.camera_only:
                self._camera()
            else:
                self._farm()
        except _TowerStop:
            pass
        except OSError:
            pass                       # a Doca fechou no meio
        except Exception as e:         # noqa: BLE001
            self._report(_("the farm stopped on an error: {err}", err=e))
        finally:
            self.watches.clear()
            try:
                self._let_go()
            except OSError:
                pass
            if self._seeing:
                self._seeing = False
                self.env.screen.release()
            self.t0 = None
            if self.on_done:
                self.on_done(self)

    def _farm(self):
        s = self.plan["strategy"]
        self._log(_("Farm started with {name}.", name=s["name"]))
        if not s["actions"]:
            self._log(_("This strategy has no actions yet: the farm only "
                        "plays the matches."))
        misses = 0
        while True:
            if self._halt.is_set():
                raise _TowerStop()
            self._blind = False
            self.watches.clear()
            self.t0 = None
            if not self._game():
                raise _TowerStop()
            self._arm("closed", "reconnect")
            try:
                played = self._round()
            except _Jump as j:
                self.watches.clear()
                self._let_go()
                self.t0 = None
                if j.to == "reconnect":
                    self._log(_("Disconnected: clicked Reconnect, starting "
                                "over."))
                    self._wait(5.0 * self.k)
                elif j.to == "closed":
                    self._log(_("The game window closed."))
                    self._wait(2.0)
                continue
            misses = 0 if played else misses + 1
            if misses >= self.MAX_MISSES:
                self._log(_("{n} rounds in a row without a match: the "
                            "pictures on the Screens tab may not match the "
                            "game. Stopping.", n=misses))
                raise _TowerStop()

    def _has_game(self):
        info = self._find_window({"match": self.plan["window"]})
        return bool(info and info.get("found"))

    def _game(self):
        """A janela do jogo existe na Doca; abre pelo link quando não."""
        if self._has_game():
            return True
        link = tower_link(self.plan["link"])
        if not link:
            self._log(_("The game is not open in the Dock. Open it there, or "
                        "put the game link in Settings, then press Start "
                        "again."))
            return False
        self._say(_("Opening the game…"))
        self._log(_("Opening the game in the Dock…"))
        self.dock.open(link, report=self._report)
        end = time.perf_counter() + self.OPEN_S
        while time.perf_counter() < end:
            self._wait(2.0)
            if self._has_game():
                self._log(_("The game is open."))
                self._wait(3.0 * self.k)
                return True
        self._log(_("The game did not open in {n} s.", n=int(self.OPEN_S)))
        return False

    def _round(self):
        """Lobby, partida e fim. False quando a partida não chegou a
        começar."""
        after = self.plan["screens"]["after"]
        if after and self._seek(after[0], 0)[0]:
            self._log(_("An end of match screen is open: leaving it first."))
            self._after()
        if not self._lobby():
            return False
        try:
            result = self._match()
        except _Jump as j:
            if j.to not in ("victory", "defeat", "over"):
                raise
            result = j.to
        finally:
            for key in (self.W_VICTORY, self.W_DEFEAT, self.W_AFTER,
                        self.W_SKIP):
                self.watches.pop(key, None)
        self._let_go()
        if result is None:
            return False
        self._end(result)
        return True

    def _lobby(self):
        s, steps = self.plan["strategy"], self.plan.get("lobby_steps") or ()
        if steps:
            self._say(_("Lobby: playing the lobby macro…"))
            self._log(_("Lobby: playing the lobby macro."))
            self._pass(steps)
        lobby = s["lobby"]
        for k, path in enumerate(lobby):
            self._say(_("Lobby: waiting for button {n} of {total}…",
                        n=k + 1, total=len(lobby)))
            ok, at = self._seek(path, self.plan["lobby_wait"])
            if not ok:
                if k == 0:
                    self._log(_("The first lobby button did not show up: "
                                "taking it that the match already started."))
                    return True
                self._log(_("Lobby button {n} did not show up, starting "
                            "over.", n=k + 1))
                return False
            self._click(at)
            self._log(_("Lobby: clicked button {n}.", n=k + 1))
            self._wait(0.5 * self.k)
        return True

    def _match(self):
        p, s = self.plan, self.plan["strategy"]
        n = self.counters["matches"] + 1
        if p["screens"]["start"]:
            self._say(_("Waiting for match {n} to start…", n=n))
            if not self._seek(p["screens"]["start"], p["start_wait"])[0]:
                self._log(_("The match did not start in {n} s, starting "
                            "over.", n=p["start_wait"]))
                return None
        self.t0 = time.perf_counter()
        self._log(_("Match {n} started.", n=n))
        self._arm("victory", "defeat", "after", "skip")
        if p["camera"]:
            self._camera()
        limit = p["match_max"] * 60.0
        for a in s["actions"]:
            self._say(_("Match {n}: next, {what} at {t}.", n=n,
                        what=tower_action_text(a, s), t=tower_time(a["t"])))
            if not self._at(min(a["t"], limit)) or a["t"] > limit:
                break
            skip = self._skip_held = self.watches.pop(self.W_SKIP, None)
            try:
                self._do(a)
            finally:
                self._skip_held = None
                if skip is not None and self._skipping():
                    self.watches[self.W_SKIP] = skip
        self._say(_("Match {n}: the strategy is done, waiting for the end "
                    "of the match…", n=n))
        self._at(limit)
        self._log(_("Match {n} took longer than {m} min.", n=n,
                    m=p["match_max"]))
        return "timeout"

    def _at(self, t):
        """Espera a hora t (s) da partida, com os vigias e a pausa. False
        se a partida passou do tempo máximo antes."""
        limit = self.plan["match_max"] * 60.0
        while True:
            if self._halt.is_set():
                raise _TowerStop()
            self._hold()
            if self.W_SKIP in self.watches and not self._skipping():
                del self.watches[self.W_SKIP]
                self._log(_("Stopped skipping waves, as set in Settings."))
            now = time.perf_counter() - self.t0
            if now >= limit and t < limit:
                return False
            left = t - now
            if left <= 0:
                return True
            self._nap(min(left, self.SLICE_S))

    # ------------------------------------------------------- as ações --
    def _do(self, a):
        s, keys = self.plan["strategy"], self.plan["keys"]
        what = tower_action_text(a, s)
        if a["do"] == "place":
            self._place(a)
        elif a["do"] == "upgrade":
            self._select(a)
            self._tap(keys["top" if a["path"] == "top" else "bottom"])
            self._log(_("{what}.", what=what))
            self._wait(0.2 * self.k)
        elif a["do"] == "sell":
            self._select(a)
            sell = self.plan["screens"]["sell"]
            if sell:
                ok, at = self._seek(sell, 2.0 * self.k)
                if ok:
                    self._click(at)
                    self._log(_("{what}.", what=what))
                else:
                    self._log(_("{what}: the Sell button did not show up.",
                                what=what))
            elif keys["sell"]:
                self._tap(keys["sell"])
                self._log(_("{what}.", what=what))
            else:
                self._log(_("{what}: choose the sell key in Settings or "
                            "capture the Sell button.", what=what))
            self._wait(0.2 * self.k)
        elif a["do"] == "key":
            self._tap(a["code"])
            self._log(_("{what}.", what=what))
        else:
            ok, at = self._seek(a["path"], a["wait"])
            if ok and a["click"]:
                self._click(at)
            self._log(_("{what}.", what=what) if ok else
                      _("{what}: it did not show up.", what=what))

    def _spot(self, a):
        return self.plan["strategy"]["towers"][a["tower"]]["points"][
            a["point"]]

    def _close_panel(self):
        """O painel de uma torre está aberto: fecha, senão ele confirmaria
        a próxima."""
        placed = self.plan["screens"]["placed"]
        if placed and self._seek(placed, 0)[0]:
            self._tap(self.plan["keys"]["cancel"])
            self._seek(placed, 1.0 * self.k, want="gone")

    def _place(self, a):
        p, s = self.plan, self.plan["strategy"]
        x, y = self._spot(a)
        slot = KEYS[str(a["tower"] + 1)]
        cancel = p["keys"]["cancel"]
        placed = p["screens"]["placed"]
        what = tower_action_text(a, s)
        start, paused = time.perf_counter(), self.paused_s
        tries = 0
        while True:
            tries += 1
            self._close_panel()
            self._tap(slot)
            # sem vigia entre a tecla e o clique: um clique no pular onda
            # com a torre na mão poderia largar ela lá
            self._sleep(0.15 * self.k)
            if self._go(x, y):
                self._pclick("left")
            if not placed:
                self._log(_("{what} (not confirmed: there is no Tower placed "
                            "picture).", what=what))
                self._wait(0.3 * self.k)
                return True
            if self._seek(placed, 1.5 * self.k)[0]:
                self._log(_("{what}.", what=what) if tries == 1 else
                          _("{what}, after {n} tries.", what=what, n=tries))
                return True
            self._tap(cancel)
            spent = time.perf_counter() - start - (self.paused_s - paused)
            if spent >= p["place_for"]:
                self._log(_("{what}: it did not go down in {n} s, going on.",
                            what=what, n=p["place_for"]))
                return False
            if tries == 1:
                self._log(_("{what}: not yet, maybe not enough cash. Trying "
                            "again every {n} s.", what=what, n=p["retry"]))
            self._idle(p["retry"])

    def _idle(self, seconds):
        """Uma espera sem torre na mão no meio de uma ação: o vigia de pular
        onda volta a valer nela, senão juntar dinheiro para uma torre
        deixaria passar o botão."""
        skip = self._skip_held
        if skip is not None and self._skipping():
            self.watches[self.W_SKIP] = skip
        try:
            self._wait(seconds)
        finally:
            self.watches.pop(self.W_SKIP, None)

    def _select(self, a):
        x, y = self._spot(a)
        placed = self.plan["screens"]["placed"]
        self._close_panel()
        if self._go(x, y):
            self._pclick("left")
        if not placed:
            self._wait(0.3 * self.k)
            return True
        if self._seek(placed, 1.5 * self.k)[0]:
            return True
        self._log(_("{tower} at spot {n} did not open its panel when "
                    "clicked.", tower=tower_name(self.plan["strategy"],
                                                 a["tower"]),
                    n=a["point"] + 1))
        return False

    def _camera(self):
        """Afasta a câmera (segura a tecla) e arrasta com o botão direito
        para baixo: a mesma vista em toda partida, que os lugares precisam."""
        p = self.plan
        zoom = p["keys"]["zoom"]
        if zoom and p["zoom_ms"]:
            self._set(zoom, True)
            self._sleep(p["zoom_ms"] / 1000.0)
            self._set(zoom, False)
        if p["drag"] and self.mouse is not None:
            w, h = self.env.workspace[2:]
            if self._go(w // 2, h // 2):
                self._sleep(0.05)
                self._set(BTN["Right"], True)
                n = max(1, abs(p["drag"]) // 20)
                for _i in range(n):
                    if self._halt.is_set():
                        break
                    self.mouse.move(0, p["drag"] / n)
                    self._sleep(0.012)
                self._set(BTN["Right"], False)
        self._let_go()
        self._log(_("Camera lined up."))
        self._sleep(0.3)

    def _end(self, result):
        c, p = self.counters, self.plan
        c["matches"] += 1
        if result == "victory":
            c["wins"] += 1
        elif result == "defeat":
            c["losses"] += 1
        self.result = {"victory": _("victory"), "defeat": _("defeat"),
                       "over": _("over"), "timeout": _("too long")}[result]
        took = time.perf_counter() - self.t0 if self.t0 else 0.0
        self.t0 = None
        self._log(_("Match {n}: {result}, {time}.", n=c["matches"],
                    result=self.result, time=clock_text(took)))
        if p["hook"]:
            self._notify({"op": "notify", "url": p["hook"],
                          "text": p["report"], "shot": p["shot"]})
        self._after()
        if p["limit"] and c["matches"] >= p["limit"]:
            self._log(_("Done: {n} matches, as set in Settings.",
                        n=c["matches"]))
            raise _TowerStop()

    def _after(self):
        after, p = self.plan["screens"]["after"], self.plan
        for k, path in enumerate(after):
            self._say(_("After the match: waiting for button {n}…", n=k + 1))
            ok, at = self._seek(path, 60.0 if k == 0 else p["lobby_wait"])
            if not ok:
                self._log(_("The button {n} after the match did not show "
                            "up.", n=k + 1))
                return False
            self._click(at)
            self._wait(0.5 * self.k)
        if after:
            self._log(_("Left the match screen."))
        self._wait(2.0 * self.k)
        return True


def tower_near(s, x, y, only=None, radius=TOWER_NEAR):
    """O lugar mais perto de (x, y), (torre, lugar), dentro do raio; com
    only, só os daquela torre."""
    best = None
    for k, t in enumerate(s["towers"]):
        if only is not None and k != only:
            continue
        for j, (px, py) in enumerate(t["points"]):
            d = (px - x) ** 2 + (py - y) ** 2
            if d <= radius ** 2 and (best is None or d < best[0]):
                best = (d, k, j)
    return best[1:] if best else None


class TowerRecorder:
    """Gravar jogando pela visão da Doca: 1 a 5 escolhe a torre e o clique
    seguinte coloca ela (o lugar entra na torre, ou é um que já existia);
    clicar numa torre colocada e apertar a tecla de melhorar ou de vender
    anota isso. A hora de cada ação conta de quando a gravação começou."""

    def __init__(self, strategy, keys, clock=time.monotonic):
        self.s, self.keys, self.clock = strategy, keys, clock
        self.t0 = clock()
        self.pending = None          # torre na mão (0 a 4)
        self.selected = None         # (torre, lugar) com o painel aberto
        self.undos = []              # (ação, lugar que ela criou ou None)

    def now(self):
        return round(self.clock() - self.t0, 2)

    def key(self, code):
        """Uma tecla apertada no jogo: a ação anotada, ou None."""
        for k in range(TOWER_SLOTS):
            if code == KEYS[str(k + 1)]:
                self.pending, self.selected = k, None
                return None
        if code == self.keys["cancel"]:
            self.pending = None
            return None
        if self.selected is None:
            return None
        k, j = self.selected
        if code in (self.keys["top"], self.keys["bottom"]):
            return self._add({"t": self.now(), "do": "upgrade", "tower": k,
                              "point": j, "path": "top"
                              if code == self.keys["top"] else "bottom"})
        if self.keys["sell"] and code == self.keys["sell"]:
            self.selected = None
            return self._add({"t": self.now(), "do": "sell", "tower": k,
                              "point": j})
        return None

    def click(self, x, y, button=BTN["Left"]):
        """Um clique no jogo: a ação anotada (colocar), ou None."""
        if button != BTN["Left"]:
            return None
        if self.pending is None:
            self.selected = self.near(x, y)
            return None
        k, self.pending = self.pending, None
        pts = self.s["towers"][k]["points"]
        j = self.near(x, y, k, TOWER_SAME)
        new = None
        if j is None:
            if len(pts) >= TOWER_MAX_POINTS:
                return None
            pts.append([int(x), int(y)])
            j, new = len(pts) - 1, k
        else:
            j = j[1]
        self.selected = (k, j)
        return self._add({"t": self.now(), "do": "place", "tower": k,
                          "point": j}, new)

    def near(self, x, y, only=None, radius=TOWER_NEAR):
        return tower_near(self.s, x, y, only, radius)

    def _add(self, act, new=None):
        if len(self.s["actions"]) >= TOWER_MAX_ACTIONS:
            return None
        self.s["actions"].append(act)
        self.undos.append((act, new))
        return act

    def undo(self):
        """Desfaz a última ação gravada (e o lugar novo que ela criou)."""
        while self.undos:
            act, new = self.undos.pop()
            idx = next((i for i, a in enumerate(self.s["actions"])
                        if a is act), None)
            if idx is None:
                continue               # já tirada à mão
            del self.s["actions"][idx]
            if new is not None:
                pts = self.s["towers"][new]["points"]
                j = act["point"]
                if j == len(pts) - 1 and not tower_uses(self.s, new, j):
                    pts.pop()
            self.selected = self.pending = None
            return act
        return None


_ICON_CACHE = {}


def app_icon(resource_class):
    """Ícone do app a partir da classe da janela, via arquivos .desktop."""
    if resource_class in _ICON_CACHE:
        return _ICON_CACHE[resource_class]
    name = None
    dirs = [os.path.join(d, "applications") for d in
            [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
            + (os.environ.get("XDG_DATA_DIRS") or "/usr/share").split(":")]
    wanted = resource_class.lower()
    for d in dirs:
        for cand in (f"{resource_class}.desktop", f"{wanted}.desktop"):
            path = os.path.join(d, cand)
            if os.path.exists(path):
                name = _desktop_icon(path)
                break
        if name:
            break
    if not name:                       # último recurso: casar por StartupWMClass
        for d in dirs:
            for f in glob.glob(os.path.join(d, "*.desktop")):
                if _desktop_wmclass(f) == wanted:
                    name = _desktop_icon(f)
                    break
            if name:
                break
    icon = QIcon.fromTheme(name or resource_class)
    if icon.isNull():
        icon = QIcon.fromTheme("application-x-executable")
    _ICON_CACHE[resource_class] = icon
    return icon


def _desktop_field(path, key):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _desktop_icon(path):
    return _desktop_field(path, "Icon")


def _desktop_wmclass(path):
    v = _desktop_field(path, "StartupWMClass")
    return v.lower() if v else None


# ------------------------------------------------------------- idioma ------
# Dicionário simples em vez de .ts/.qm: o app é um arquivo só, e assim quem for
# traduzir para outro idioma só precisa copiar um bloco aqui.
TRANSLATIONS = {
    "pt_BR": {
        "WayClick": "WayClick",
        "File": "Arquivo", "Settings": "Configurações", "Help": "Ajuda",
        "Theme": "Tema", "Language": "Idioma",
        "Tray icon": "Ícone da bandeja", "Shape": "Formato", "Color": "Cor",
        "Cursor": "Cursor", "Mouse": "Mouse", "Dot": "Ponto", "Ring": "Anel",
        "Match state": "Conforme o estado", "Green": "Verde", "Blue": "Azul",
        "Purple": "Roxo", "Orange": "Laranja", "Red": "Vermelho",
        "Teal": "Verde-azulado", "Grey": "Cinza",
        "System": "Sistema", "Dark": "Escuro", "Light": "Claro",
        "Start": "Iniciar", "Stop": "Parar",
        "Hide to tray": "Esconder na bandeja",
        "Show window": "Mostrar janela", "Hide window": "Esconder janela",
        "Quit": "Sair", "About": "Sobre",
        "Project on GitHub": "Projeto no GitHub",
        "Press a key…": "Aperte uma tecla…",
        "Click, then press the key you want":
            "Clique e depois aperte a tecla que quiser",
        "Send key to a window": "Mandar tecla para uma janela",
        "Window:": "Janela:", "Every:": "A cada:",
        "no window found": "nenhuma janela encontrada",
        "Targeted key unavailable: {msg}": "Tecla direcionada indisponível: {msg}",
        "Lost the target window.": "Perdi a janela alvo.",
        "Pick a window first (↻ to rescan).":
            "Escolha uma janela primeiro (↻ para reprocurar).",
        "That key has no X11 equivalent.": "Essa tecla não tem equivalente no X11.",
        "⚠ Pure Wayland: reachable only by stealing focus for an instant. "
        "Reopen this program in X11 mode and it takes the key directly, even "
        "minimized — SDL_VIDEODRIVER=x11, GDK_BACKEND=x11 or "
        "QT_QPA_PLATFORM=xcb.":
            "⚠ Wayland puro: só dá para alcançar roubando o foco por um "
            "instante. Reabra este programa em modo X11 e ele recebe a tecla "
            "direto, mesmo minimizado — SDL_VIDEODRIVER=x11, GDK_BACKEND=x11 "
            "ou QT_QPA_PLATFORM=xcb.",
        "Nothing to run: enable Click, Keyboard macro or Send key to a window.":
            "Nada para executar: habilite Clique, Macro de teclado ou Mandar "
            "tecla para uma janela.",
        "Click": "Clique", "Keyboard": "Teclado", "Window": "Janela",
        "Trigger": "Acionamento",
        "Enable clicking": "Habilitar o clique",
        "Enable the keyboard macro": "Habilitar a macro de teclado",
        "Enable sending the key": "Habilitar o envio da tecla",
        "1000 ms is 1 click/s, 0.1 ms is 10,000. Lower is faster.":
            "1000 ms é 1 clique/s, 0,1 ms é 10.000. Menor é mais rápido.",
        "Click the key field and press the key you want.":
            "Clique no campo da tecla e aperte a tecla que quiser.",
        "Mouse:": "Mouse:", "Interval:": "Intervalo:", "Button:": "Botão:",
        "Key:": "Tecla:", "Action:": "Ação:", "Mode:": "Modo:",
        "Start delay:": "Atraso ao iniciar:",
        "Auto-stop after:": "Parar sozinho após:",
        "Arm/disarm hotkey:": "Atalho de armar/desarmar:",
        "Trigger:": "Gatilho:",
        "{btn} click": "clique {btn}",
        "Runs while the trigger is held": "Age enquanto o gatilho é segurado",
        "Press a key or a mouse button…": "Aperte uma tecla ou um botão…",
        "Click, then press a key or a mouse button":
            "Clique e aperte a tecla ou o botão que quiser",
        "ARMED — hold {trig}": "ARMADO — segure {trig}",
        "Arm, then hold {trig}: it is captured and becomes the click stream. "
        "{hk} arms/disarms; Esc disarms.":
            "Arme e segure {trig}: ele é capturado e vira o fluxo de cliques. "
            "{hk} arma/desarma; Esc desarma.",
        "Arm, then hold {trig} and it runs; let go and it stops. "
        "{hk} arms/disarms; Esc disarms.":
            "Arme e segure {trig}: ele roda; solte e para. "
            "{hk} arma/desarma; Esc desarma.",
        "{hk} also types: it arms and disarms every time you press it in any "
        "window. Pick a key that types nothing (F6-F12, Insert, Pause, "
        "ScrollLock).":
            "{hk} também digita: ela arma e desarma toda vez que for apertada, "
            "em qualquer janela. Escolha uma tecla que não escreve nada "
            "(F6-F12, Insert, Pause, ScrollLock).",
        "Global hotkey:": "Atalho global:",
        "Left": "Esquerdo", "Right": "Direito", "Middle": "Meio",
        "Side": "Lateral", "Extra": "Lateral 2",
        "Mouse button:": "Botão do mouse:",
        "Hold {hk} and it runs; let go and it stops. No need to press Start "
        "first — the hotkey is watched the whole time.":
            "Segure {hk} e ele roda; solte e ele para. Não precisa apertar "
            "Start antes: o atalho fica sendo ouvido o tempo todo.",
        "{hk} also types: as a toggle it fires every time you press it in any "
        "window. Use it with a 'while held' mode, or pick a key that types "
        "nothing (F6-F12, Insert, Pause, ScrollLock).":
            "{hk} também digita: como liga/desliga ela dispara toda vez que "
            "você a aperta, em qualquer janela. Use com um modo 'enquanto "
            "segura', ou escolha uma tecla que não escreve nada (F6-F12, "
            "Insert, Pause, ScrollLock).",
        "Repeat": "Repetir", "Hold": "Segurar",
        "Arm": "Armar",
        "Trigger is the {btn} mouse button: arming runs nothing until you "
        "hold it. To run the moment you press Arm, set Mode to 'No trigger' "
        "— or untick it under 'Trigger holds'.":
            "O gatilho é o botão {btn} do mouse: armar não roda nada até você "
            "segurá-lo. Para rodar assim que apertar Armar, mude o Modo para "
            "'Sem gatilho' — ou desmarque em 'O gatilho segura'.",
        "Trigger is {hk} held down: arming runs nothing until you hold it. To "
        "run the moment you press Arm, set Mode to 'No trigger'.":
            "O gatilho é o {hk} segurado: armar não roda nada até você "
            "segurá-lo. Para rodar assim que apertar Armar, mude o Modo para "
            "'Sem gatilho'.",
        "Trigger holds:": "O gatilho segura:",
        "Check for updates": "Procurar atualizações",
        "Check for updates on start": "Procurar atualizações ao abrir",
        "WayClick {v} is available. You have {cur}.":
            "WayClick {v} disponível. Você tem a {cur}.",
        "Download": "Baixar",
        "Dismiss until next launch": "Esconder até a próxima abertura",
        "You are up to date ({v}).": "Você já está na versão mais recente ({v}).",
        "Could not check for updates.":
            "Não foi possível procurar atualizações.",
        "+ Add key": "+ Adicionar tecla",
        "Remove this key": "Remover esta tecla",
        "Two rows use the same key; only the first one runs.":
            "Duas linhas usam a mesma tecla; só a primeira roda.",
        "No trigger (hotkey toggles)": "Sem gatilho (o atalho liga/desliga)",
        "never": "nunca", "Rescan mice": "Reprocurar mouses",
        "Sound feedback on hotkey": "Som ao acionar o atalho",
        "Start with system": "Iniciar com o sistema",
        "Anti-AFK: nudge the cursor every":
            "Anti-AFK: mexer o cursor a cada",
        "Stopped": "Parado", "Starting in {n}s…": "Começa em {n}s…",
        "Stopped (time is up)": "Parado (tempo esgotado)",
        "RUNNING": "RODANDO", "ARMED — hold {btn} mouse button":
            "ARMADO — segure o botão {btn} do mouse",
        "held": "segurada", "nothing enabled": "nada habilitado",
        "stops in {n}s": "para em {n}s", "anti-AFK": "anti-AFK",
        "clicks/s": "cliques/s",
        "Move the cursor off this window before starting — otherwise it clicks "
        "itself. That is what the {d}s start delay is for. {hk} toggles; Esc stops.":
            "Tire o cursor desta janela antes de iniciar — senão ele clica em si "
            "mesmo. É para isso que serve o atraso de {d}s. {hk} liga e desliga; "
            "Esc para.",
        "Hold mode: while armed, your {btn} mouse button is captured and turned "
        "into the click stream — hold it to autoclick, release to stop. "
        "{hk} arms/disarms; Esc disarms.":
            "Modo segurar: enquanto armado, o botão {btn} do mouse é capturado e "
            "vira o fluxo de cliques — segure para clicar, solte para parar. "
            "{hk} arma e desarma; Esc desarma.",
        "Global hotkey OFF: you are in the 'input' group but this session "
        "started before that, so it has no access yet. Log out and back in, "
        "or run:  sg input -c '{cmd}'":
            "Atalho global DESLIGADO: você está no grupo 'input', mas esta "
            "sessão começou antes disso e ainda não tem acesso. Saia e entre de "
            "novo, ou rode:  sg input -c '{cmd}'",
        "Global hotkey OFF (no access to /dev/input). Run this, then log out "
        "and back in:\nsudo usermod -aG input $USER\nUntil then the hotkey "
        "only works with this window focused.":
            "Atalho global DESLIGADO (sem acesso a /dev/input). Rode isto e "
            "depois saia e entre de novo:\nsudo usermod -aG input $USER\nAté lá "
            "o atalho só funciona com esta janela em foco.",
        "no mouse detected": "nenhum mouse detectado",
        "Hold mode unavailable: {msg}": "Modo segurar indisponível: {msg}",
        "no mouse found": "nenhum mouse encontrado",
        "no access to /dev/input": "sem acesso a /dev/input",
        "could not grab the mouse": "não foi possível capturar o mouse",
        "Autoclicker for Wayland using /dev/uinput.":
            "Autoclicker para Wayland usando /dev/uinput.",
        # ---- macros com atalho e importação do AutoHotkey (1.6.0) ----
        "Hotkeys": "Atalhos",
        "Enable hotkey macros": "Habilitar os macros com atalho",
        "Each macro has its own hotkey and works in any window, even with "
        "WayClick stopped, like an AutoHotkey script. Import AHK converts the "
        "hotkeys of a .ahk file.":
            "Cada macro tem o seu atalho e funciona em qualquer janela, mesmo "
            "com o WayClick parado, como um script do AutoHotkey. Importar AHK "
            "converte os atalhos de um arquivo .ahk.",
        "New": "Novo", "Edit": "Editar", "Remove": "Remover",
        "Import AHK…": "Importar AHK…",
        "Turn the hotkeys of an AutoHotkey script (.ahk) into macros":
            "Transforma os atalhos de um script do AutoHotkey (.ahk) em macros",
        "Hide it from other apps": "Esconder dos outros programas",
        "The hotkey still arms and disarms, but the game or app in front "
        "never receives it.":
            "O atalho continua armando e desarmando, mas o jogo ou programa "
            "em primeiro plano nunca o recebe.",
        "Toggle: press to start, again to stop":
            "Liga e desliga: aperte para começar, de novo para parar",
        "While held": "Enquanto segura",
        "Once per press": "Uma vez por aperto",
        "Always, while enabled": "Sempre, enquanto habilitado",
        "toggle": "liga e desliga", "while held": "enquanto segura",
        "once": "uma vez", "always": "sempre",
        "no hotkey": "sem atalho",
        "no repeat": "sem repetir",
        "every {ms} ms": "a cada {ms} ms",
        "no steps": "sem passos",
        "Click {btn}": "Clicar {btn}",
        "Hold {btn} button": "Segurar o botão {btn}",
        "Release {btn} button": "Soltar o botão {btn}",
        "Tap {key}": "Tocar {key}",
        "Hold {key}": "Segurar {key}",
        "Release {key}": "Soltar {key}",
        "Wait {a} to {b} ms": "Esperar de {a} a {b} ms",
        "Wait {ms} ms": "Esperar {ms} ms",
        "Type “{t}”": "Digitar “{t}”",
        "Wheel up": "Roda para cima", "Wheel down": "Roda para baixo",
        "Wheel left": "Roda para a esquerda",
        "Wheel right": "Roda para a direita",
        "Move pointer by {dx}, {dy}": "Mover o ponteiro {dx}, {dy}",
        "Step": "Passo", "Macro": "Macro",
        "Tap a key or click": "Tocar uma tecla ou clicar",
        "Hold down": "Segurar", "Release": "Soltar", "Wait": "Esperar",
        "Type text": "Digitar texto", "Scroll the wheel": "Girar a roda",
        "Move the pointer": "Mover o ponteiro",
        "Up": "Cima", "Down": "Baixo",
        "Key or button:": "Tecla ou botão:", "Times:": "Vezes:",
        "Wait:": "Esperar:", "Random, up to:": "Aleatório, até:",
        "Text:": "Texto:", "Direction:": "Direção:",
        "Right (px):": "Direita (px):", "Down (px):": "Baixo (px):",
        "no, exact": "não, exato",
        "Typed with a US keyboard layout: letters, digits and symbols land "
        "on the same physical keys.":
            "Digitado no layout de teclado americano: letras, números e "
            "símbolos caem nas mesmas teclas físicas.",
        "Moves by an amount, from where the pointer is. To go to an exact "
        "position, use Go to a screen position.":
            "Move uma distância, a partir de onde o ponteiro está. Para ir a "
            "uma posição exata, use Ir a uma posição da tela.",
        "Random, up to: each run waits a different time between the two "
        "values.":
            "Aleatório, até: cada vez espera um tempo diferente entre os dois "
            "valores.",
        "Stays pressed until a Release step, or until the macro stops, which "
        "releases everything it held.":
            "Fica apertado até um passo Soltar, ou até o macro parar, o que "
            "solta tudo o que ele segurou.",
        "Name:": "Nome:", "optional": "opcional", "Hotkey:": "Atalho:",
        "With:": "Com:",
        "Hide the hotkey from other apps":
            "Esconder o atalho dos outros programas",
        "Like AutoHotkey: the game never sees the hotkey itself. Without "
        "this, the key does its normal job too (AutoHotkey's ~ prefix).":
            "Como no AutoHotkey: o jogo nunca vê a tecla do atalho. Sem isso, "
            "a tecla também faz o que faria normalmente (o prefixo ~ do "
            "AutoHotkey).",
        "Repeat the steps": "Repetir os passos", "every": "a cada",
        "as soon as they end": "assim que acabarem",
        "Counted from the start of one run to the start of the next, like "
        "AutoHotkey's SetTimer. 1000 ms is once a second.":
            "Contado do início de uma rodada ao início da próxima, como o "
            "SetTimer do AutoHotkey. 1000 ms é uma vez por segundo.",
        "auto": "automático",
        "How long each tap stays down. Auto is 40 ms, or half the repeat "
        "time when that is shorter: games read keys once per frame and miss "
        "a tap that is too short.":
            "Quanto tempo cada toque fica apertado. Automático é 40 ms, ou "
            "metade do tempo de repetição quando ele é menor: jogos leem as "
            "teclas uma vez por quadro e perdem um toque curto demais.",
        "Repeat:": "Repetir:", "Key press time:": "Tempo de cada toque:",
        "Steps:": "Passos:", "+ Add": "+ Adicionar",
        "Move up": "Subir", "Move down": "Descer",
        "Press the hotkey to start, press it again to stop. This is what "
        "F1:: with a SetTimer does in AutoHotkey.":
            "Aperte o atalho para começar e de novo para parar. É o que um "
            "F1:: com SetTimer faz no AutoHotkey.",
        "Runs while the hotkey is held and stops the moment you let go. "
        "Without repeat, keys held by the steps stay down until you let go, "
        "which makes a remap.":
            "Roda enquanto o atalho está apertado e para assim que você "
            "solta. Sem repetir, as teclas seguradas pelos passos ficam "
            "apertadas até você soltar, o que faz um remapeamento.",
        "Runs the steps once for every press.":
            "Roda os passos uma vez a cada aperto.",
        "Runs on its own whenever hotkey macros are enabled, no hotkey "
        "needed. Use a long repeat time, like an anti idle key.":
            "Roda sozinho sempre que os macros com atalho estão habilitados, "
            "sem atalho. Use um tempo de repetição longo, como uma tecla "
            "contra inatividade.",
        "Add at least one step.": "Adicione pelo menos um passo.",
        "An Always macro needs a repeat time of at least 10 ms.":
            "Um macro Sempre precisa de um tempo de repetição de pelo menos "
            "10 ms.",
        "Remove macro": "Remover macro", "Remove {name}?": "Remover {name}?",
        "Import AutoHotkey": "Importar AutoHotkey",
        "AutoHotkey scripts": "Scripts do AutoHotkey",
        "All files": "Todos os arquivos",
        "1 macro found in {file}:": "1 macro encontrado em {file}:",
        "{n} macros found in {file}:": "{n} macros encontrados em {file}:",
        "Notes:": "Observações:",
        "Only {n} fit: the list holds {max} macros.":
            "Só cabem {n}: a lista comporta {max} macros.",
        "Add these macros": "Adicionar estes macros",
        "Running: {names}": "Rodando: {names}",
        "Could not read {file}: {err}": "Não foi possível ler {file}: {err}",
        "WayClick is running (pid {pid}). Import from its Hotkeys tab, or "
        "quit it and run this again.":
            "O WayClick está aberto (pid {pid}). Importe pela aba Atalhos, ou "
            "feche o programa e rode isto de novo.",
        "Added {n} macros to {cfg}.": "{n} macros adicionados em {cfg}.",
        "Added 1 macro to {cfg}.": "1 macro adicionado em {cfg}.",
        "line {ln}": "linha {ln}",
        "hotstrings are not supported": "hotstrings não são suportadas",
        "window-specific hotkeys (#If) work in every window here":
            "atalhos só para uma janela (#If) valem em todas as janelas aqui",
        "{name} starts with the script, imported as an Always macro":
            "{name} começa junto com o script, importado como macro Sempre",
        "{hk}: release hotkeys (Up) are not supported":
            "{hk}: atalhos de soltar (Up) não são suportados",
        "{hk}: two-key combinations are not supported":
            "{hk}: combinações de duas teclas não são suportadas",
        "{hk}: the mouse wheel cannot be a hotkey here":
            "{hk}: a roda do mouse não pode ser atalho aqui",
        "{hk}: key {name} is unknown": "{hk}: tecla {name} desconhecida",
        "{hk}: nothing WayClick can do": "{hk}: nada que o WayClick consiga "
                                         "fazer",
        "{hk} clicks its own button, so it is hidden from the app even with "
        "~ (otherwise the clicks would be lost)":
            "{hk} clica o próprio botão, então fica escondido do programa "
            "mesmo com ~ (senão os cliques se perderiam)",
        "SetTimer: {name} was not found": "SetTimer: {name} não foi "
                                          "encontrado",
        "SetTimer period {p} is not a number, used 250 ms":
            "o período {p} do SetTimer não é um número, usei 250 ms",
        "SetTimer {ms} ms: on Windows AutoHotkey rounds timers up to 10 or "
        "15.6 ms, so the script really ran every 15.6 ms, and so does the "
        "macro. Keys sent faster than the game reads them pile up and keep "
        "arriving after the macro stops.":
            "SetTimer {ms} ms: no Windows o AutoHotkey arredonda os timers "
            "para 10 ou 15,6 ms, então o script rodava de verdade a cada "
            "15,6 ms, e o macro também. Teclas mandadas mais rápido do que o "
            "jogo lê se acumulam e continuam chegando depois que o macro "
            "para.",
        "Sleep {ms}: on Windows AutoHotkey waits at least 10 or 15.6 ms, so "
        "the script really waited 15.6 ms, and so do the macros (every wait "
        "shorter than that)":
            "Sleep {ms}: no Windows o AutoHotkey espera pelo menos 10 ou "
            "15,6 ms, então o script esperava de verdade 15,6 ms, e os macros "
            "também (toda espera mais curta que isso)",
        "{key} is also the arm/disarm hotkey. While hotkey macros are on, "
        "{key} runs the macro and does not arm the clicker; pick another "
        "arm/disarm hotkey to use both.":
            "{key} também é o atalho de armar/desarmar. Com os macros de "
            "atalho ligados, {key} roda o macro e não arma o clique; escolha "
            "outro atalho de armar/desarmar para usar os dois.",
        "{hk} only stops a timer; in WayClick the hotkey that starts a macro "
        "also stops it":
            "{hk} só para um timer; no WayClick o atalho que começa um macro "
            "também o para",
        "{hk} only starts the timer; here the same hotkey also stops it":
            "{hk} só liga o timer; aqui o mesmo atalho também desliga",
        "while {c} is not understood, skipped":
            "while {c} não foi entendido, ignorado",
        "Loop {n} shortened to {m} repetitions":
            "Loop {n} encurtado para {m} repetições",
        "{hk}: an endless loop; here the hotkey turns it on and off":
            "{hk}: um laço sem fim; aqui o atalho liga e desliga",
        "{hk}: what runs before the loop was left out":
            "{hk}: o que roda antes do laço ficou de fora",
        "a condition inside the actions was skipped":
            "uma condição no meio das ações foi ignorada",
        "{name} was not found": "{name} não foi encontrado",
        "{what} is not supported, skipped": "{what} não é suportado, ignorado",
        "Sleep {a} is not a number, skipped":
            "Sleep {a} não é um número, ignorado",
        "MouseMove without a position, skipped":
            "MouseMove sem posição, ignorado",
        "positions are used as screen positions; AutoHotkey measured them "
        "from the active window (the same for a fullscreen game on the "
        "leftmost screen)":
            "as posições valem como posições da tela; o AutoHotkey as media a "
            "partir da janela ativa (dá no mesmo para um jogo em tela cheia "
            "na tela mais à esquerda)",
        # passos que olham a tela, ponteiro absoluto, avisos
        "Go to a screen position": "Ir a uma posição da tela",
        "Find an image on screen": "Achar uma imagem na tela",
        "Check a pixel color": "Conferir a cor de um pixel",
        "Send a notification": "Enviar um aviso",
        "Open a link or file": "Abrir um link ou arquivo",
        "Only move": "Só mover", "Left click": "Clique esquerdo",
        "Right click": "Clique direito", "Middle click": "Clique do meio",
        "Double click": "Clique duplo", "Nothing": "Nada",
        "Point at it": "Apontar", "Click it": "Clicar",
        "Right click it": "Clique direito", "Middle click it": "Clique do meio",
        "Double click it": "Clique duplo", "Continue": "Seguir",
        "Stop the macro": "Parar o macro",
        "Start over from step 1": "Recomeçar do passo 1",
        "Go to step": "Ir ao passo", "appears": "aparecer",
        "disappears": "sumir", "has the color": "ter a cor",
        "loses the color": "perder a cor",
        "Move to {x}, {y}": "Ir até {x}, {y}",
        "Click at {x}, {y}": "Clicar em {x}, {y}",
        "Right click at {x}, {y}": "Clique direito em {x}, {y}",
        "Middle click at {x}, {y}": "Clique do meio em {x}, {y}",
        "Double click at {x}, {y}": "Clique duplo em {x}, {y}",
        "curved": "em curva", "Find “{img}”": "Achar “{img}”",
        "Wait until “{img}” is gone": "Esperar “{img}” sumir",
        "Pixel {at} is {color}": "Pixel {at} é {color}",
        "Pixel {at} is not {color}": "Pixel {at} não é {color}",
        "up to {s} s": "até {s} s", "point at it": "apontar ali",
        "click it": "clicar ali", "right click it": "clique direito ali",
        "middle click it": "clique do meio ali",
        "double click it": "clique duplo ali", "then {what}": "depois {what}",
        "else {what}": "senão {what}", "Notify “{t}”": "Avisar “{t}”",
        "Notify": "Avisar", "screenshot": "print da tela",
        "Open {url}": "Abrir {url}", "go to step {n}": "ir ao passo {n}",
        "continue": "seguir", "stop": "parar", "start over": "recomeçar",
        "there is no absolute pointer, so moving to a screen position was "
        "skipped.":
            "não há ponteiro absoluto, então ir a uma posição da tela foi "
            "pulado.",
        "The screen is not available.": "A tela não está disponível.",
        "The image {path} could not be read.":
            "Não deu para ler a imagem {path}.",
        "Screen sharing stopped.": "O compartilhamento da tela parou.",
        "the webhook did not take the message ({err}).":
            "o webhook não aceitou a mensagem ({err}).",
        "Image and color steps need numpy.":
            "Passos de imagem e de cor precisam do numpy.",
        "Seeing the screen needs PyGObject and GStreamer.":
            "Ver a tela precisa do PyGObject e do GStreamer.",
        "Seeing the screen needs the GStreamer element {name}.":
            "Ver a tela precisa do elemento {name} do GStreamer.",
        "No picture came from the screen.": "Nenhuma imagem veio da tela.",
        "Stopped.": "Parado.",
        "The system did not answer the screen sharing request.":
            "O sistema não respondeu ao pedido de compartilhar a tela.",
        "Screen sharing was not allowed.":
            "O compartilhamento da tela não foi permitido.",
        "The screen sharing request failed.":
            "O pedido de compartilhar a tela falhou.",
        "No screen sharing portal answered. xdg-desktop-portal and the one "
        "for your desktop (KDE, GNOME, wlr) must be installed.":
            "Nenhum portal de compartilhamento de tela respondeu. O "
            "xdg-desktop-portal e o do seu desktop (KDE, GNOME, wlr) precisam "
            "estar instalados.",
        "No screen was shared.": "Nenhuma tela foi compartilhada.",
        "No session D-Bus: {err}": "Sem D-Bus de sessão: {err}",
        "Screen capture failed: {err}": "A captura da tela falhou: {err}",
        "Position:": "Posição:", "Then:": "Depois:", "Image:": "Imagem:",
        "Similarity:": "Semelhança:", "Search in:": "Procurar em:",
        "Color:": "Cor:", "Tolerance:": "Tolerância:",
        "Wait until it:": "Esperar até:", "Wait up to:": "Esperar no máximo:",
        "When found:": "Ao achar:", "Movement:": "Movimento:",
        "Random offset:": "Desvio aleatório:",
        "If it happens:": "Se acontecer:", "If not:": "Se não:",
        "Capture after:": "Capturar após:", "Message:": "Mensagem:",
        "Discord webhook:": "Webhook do Discord:",
        "Link or file:": "Link ou arquivo:",
        "Pick on screen…": "Escolher na tela…",
        "Curved, like a hand": "Em curva, como a mão", "none": "nenhum",
        "Capture from screen…": "Capturar da tela…",
        "Open file…": "Abrir arquivo…",
        "How alike the screen must be to the picture. Lower finds more, and "
        "also finds wrong places.":
            "O quanto a tela precisa parecer com a imagem. Mais baixo acha "
            "mais, e também acha lugares errados.",
        "Pick area…": "Marcar área…", "Whole screen": "Tela inteira",
        "How far red, green and blue may each be from the color, 0 to 255.":
            "O quanto vermelho, verde e azul podem se afastar da cor, de 0 a "
            "255.",
        "check once": "olhar uma vez",
        "Time to switch to the game window before the screen is captured.":
            "Tempo para trocar para a janela do jogo antes de capturar a "
            "tela.",
        "Attach a screenshot": "Anexar print da tela",
        "No image yet": "Nenhuma imagem ainda", "Image": "Imagem",
        "Images": "Imagens",
        "That file is not an image.": "Esse arquivo não é uma imagem.",
        "Waiting for screen sharing…": "Esperando o compartilhamento da tela…",
        "Capturing in {n} s, switch to the game window…":
            "Capturando em {n} s, vá para a janela do jogo…",
        "Could not save {path}.": "Não deu para salvar {path}.",
        "Pick an image first.": "Escolha uma imagem antes.",
        "The color must look like #ff8800.":
            "A cor precisa estar no formato #ff8800.",
        "Type a message, or attach a screenshot to a webhook.":
            "Digite uma mensagem, ou anexe um print a um webhook.",
        "Type a link or a file.": "Digite um link ou um arquivo.",
        "Type the text.": "Digite o texto.",
        "Right or down must not be zero.":
            "Direita ou baixo precisa ser diferente de zero.",
        "This step is not complete.": "Este passo não está completo.",
        "Puts the pointer at an exact position. 0, 0 is the top left corner "
        "of the leftmost screen, and all screens count together, as in "
        "Display Configuration.":
            "Põe o ponteiro numa posição exata. 0, 0 é o canto de cima à "
            "esquerda da tela mais à esquerda, e todas as telas contam "
            "juntas, como na Configuração de Telas.",
        "Looks for the picture on screen, like ImageSearch in AutoHotkey. "
        "The first time, the system asks which screens WayClick may see, and "
        "remembers the answer.":
            "Procura a imagem na tela, como o ImageSearch do AutoHotkey. Na "
            "primeira vez o sistema pergunta quais telas o WayClick pode ver, "
            "e guarda a resposta.",
        "Without a webhook it shows a desktop notification. With a Discord "
        "webhook (channel settings, Integrations) it posts in that channel. "
        "{run} in the text becomes the number of the run, {time} how long "
        "the macro has been running, and {matches} the value of a counter "
        "named matches.":
            "Sem webhook, mostra uma notificação do sistema. Com um webhook "
            "do Discord (configurações do canal, Integrações), posta naquele "
            "canal. No texto, {run} vira o número da rodada, {time} há "
            "quanto tempo o macro está rodando, e {partidas} o valor de um "
            "contador chamado partidas.",
        "Opens with the default program. Web pages, roblox:// and steam:// "
        "links, files.":
            "Abre com o programa padrão. Páginas da web, links roblox:// e "
            "steam://, arquivos.",
        "Pick on screen": "Escolher na tela", "Screen {n}": "Tela {n}",
        "{w}×{h} at {x}, {y}": "{w}×{h} em {x}, {y}", "Fit": "Ajustar",
        "Click the point.": "Clique no ponto.",
        "Drag a rectangle around it. Ctrl and the wheel zoom.":
            "Arraste um retângulo em volta. Ctrl e a roda dão zoom.",
        "Screen:": "Tela:", "Zoom:": "Zoom:",
        "Selected {w}×{h}": "Selecionado {w}×{h}",
        "key {name} is unknown": "tecla {name} desconhecida",
        "the script is nested too deeply, the rest was skipped":
            "o script tem aninhamento demais, o resto foi ignorado",
        # vigiar, janela, contar, gravar, farm, compartilhar (1.8.0)
        "Wait until the “{win}” window closes":
            "Esperar a janela “{win}” fechar",
        "Focus the “{win}” window": "Trazer a janela “{win}” para a frente",
        "Check the “{win}” window is open":
            "Conferir se a janela “{win}” está aberta",
        "Watch for “{img}”": "Vigiar “{img}”",
        "Watch for “{img}” to go away": "Vigiar “{img}” sumir",
        "Watch pixel {at} for {color}": "Vigiar o pixel {at} ficar {color}",
        "Watch pixel {at} losing {color}":
            "Vigiar o pixel {at} perder {color}",
        "Watch for the “{win}” window": "Vigiar a janela “{win}” abrir",
        "Watch for the “{win}” window to close":
            "Vigiar a janela “{win}” fechar",
        "every {s} s": "a cada {s} s", "Count {name}": "Contar {name}",
        "every {n} then {what}": "a cada {n} vezes, {what}",
        "stop after {n} runs": "parar após {n} rodadas",
        "stop after 1 run": "parar após 1 rodada",
        "windows can only be checked on KDE Plasma, through KWin, so the "
        "window step took its else path.":
            "janelas só podem ser conferidas no KDE Plasma, pelo KWin, então "
            "o passo de janela seguiu pelo senão.",
        "The macros come turned off: tick one in the list to turn it on.":
            "Os macros chegam desligados: marque um na lista para ligar.",
        "Webhooks are not imported ({n}): a file from someone else could "
        "send pictures of your screen to their channel. Add your own in the "
        "Notify steps.":
            "Webhooks não são importados ({n}): um arquivo de outra pessoa "
            "poderia mandar prints da sua tela para o canal dela. Ponha o seu "
            "nos passos de aviso.",
        "A step that opens “{url}” became an empty wait: only web, roblox:// "
        "and steam:// links are imported.":
            "Um passo que abriria “{url}” virou uma espera vazia: só links da "
            "web, roblox:// e steam:// são importados.",
        "{n} steps could not be read (an image missing from the file, for "
        "instance) and became empty waits.":
            "{n} passos não puderam ser lidos (uma imagem que faltou no "
            "arquivo, por exemplo) e viraram esperas vazias.",
        "Watch the screen or a window": "Vigiar a tela ou uma janela",
        "Check or focus a window": "Conferir ou focar uma janela",
        "Count": "Contar", "is open": "abrir", "is closed": "fechar",
        "An image": "Uma imagem", "A pixel color": "A cor de um pixel",
        "A window": "Uma janela", "Only check": "Só conferir",
        "Bring it to the front": "Trazer para a frente",
        "How often it looks. Every look at the screen costs a little CPU; a "
        "window is checked at most once a second.":
            "De quanto em quanto tempo ele olha. Cada olhada na tela custa um "
            "pouco de CPU; uma janela é conferida no máximo uma vez por "
            "segundo.",
        "matches": "partidas", "never, only count": "nunca, só contar",
        "Open windows…": "Janelas abertas…",
        "List the windows that are open now":
            "Listar as janelas abertas agora",
        "Watch for:": "Vigiar:", "When it is open:": "Se estiver aberta:",
        "React when it:": "Reagir quando:", "Check every:": "Olhar a cada:",
        "Counter:": "Contador:",
        "{run} and {time} are already taken; pick another name.":
            "{run} e {time} já estão em uso; escolha outro nome.",
        "Keeps looking while the next steps run, like a SetTimer with "
        "ImageSearch in AutoHotkey. It looks while the macro waits (Wait "
        "steps, screen searches) and between steps, never in the middle of a "
        "key press. Use it for what can show up at any time, like a skip "
        "button, a reconnect button or the end of a match. When it takes the "
        "macro to another step, every watch stops until the macro passes "
        "over it again.":
            "Continua olhando enquanto os próximos passos rodam, como um "
            "SetTimer com ImageSearch no AutoHotkey. Ele olha enquanto o "
            "macro espera (passos de espera, buscas na tela) e entre um passo "
            "e outro, nunca no meio de uma tecla apertada. Use para o que "
            "pode aparecer a qualquer hora, como um botão de pular, de "
            "reconectar ou o fim da partida. Quando ele leva o macro para "
            "outro passo, todos os vigias param até o macro passar por eles "
            "de novo.",
        "Finds a window by app name, like Sober, or by its whole title, and "
        "can bring it to the front. Needs KDE Plasma. To reopen a game that "
        "closed, send the else path to an Open a link step.":
            "Acha uma janela pelo nome do app, como Sober, ou pelo título "
            "inteiro, e pode trazê-la para a frente. Precisa do KDE Plasma. "
            "Para reabrir um jogo que fechou, mande o senão para um passo "
            "Abrir um link.",
        "Adds 1 each time the macro passes here. Write the name in braces, "
        "like {matches}, in a notification to show the number. Every N can "
        "stop the macro or go to a step, like every 10 matches.":
            "Soma 1 cada vez que o macro passa aqui. Escreva o nome entre "
            "chaves, como {partidas}, num aviso para mostrar o número. A cada "
            "N vezes, pode parar o macro ou ir a um passo, como a cada 10 "
            "partidas.",
        "runs": "rodadas",
        "Stops the macro by itself after this many runs, like 20 matches of "
        "a farm. A run that starts over does not count.":
            "Para o macro sozinho depois de tantas rodadas, como 20 partidas "
            "de um farm. Uma rodada que recomeça não conta.",
        "Stop after:": "Parar após:", "● Record…": "● Gravar…",
        "Play, and the keys, clicks and wheel become steps":
            "Jogue, e as teclas, os cliques e a roda viram passos",
        "No window list: this needs KDE Plasma.":
            "Sem lista de janelas: isso precisa do KDE Plasma.",
        "Record steps": "Gravar passos",
        "Time to switch to the game window before recording starts.":
            "Tempo para trocar para a janela do jogo antes de a gravação "
            "começar.",
        "Pressing it stops the recording. It is not recorded and the game "
        "does not see it.":
            "Ela encerra a gravação. Não é gravada, e o jogo não a vê.",
        "Keep the pauses between actions": "Manter as pausas entre as ações",
        "Off, the steps run one right after the other.":
            "Desligado, os passos rodam um logo depois do outro.",
        "Start recording": "Começar a gravar",
        "Mouse movement without a click is not recorded. Clicks keep where "
        "they happened.":
            "Movimento do mouse sem clique não é gravado. Os cliques guardam "
            "onde aconteceram.",
        "Add these steps": "Adicionar estes passos",
        "Start after:": "Começar após:", "Stop key:": "Tecla de parar:",
        "The stop key must be a keyboard key.":
            "A tecla de parar precisa ser do teclado.",
        "Cancel": "Cancelar",
        "Recording in {n} s, switch to the game window…":
            "Gravando em {n} s, vá para a janela do jogo…",
        "Recording, {n} actions so far. Press {key} to stop.":
            "Gravando, {n} ações até agora. Aperte {key} para parar.",
        "Positions are not available here (they come from KDE Plasma): "
        "clicks are recorded where the pointer happens to be when the macro "
        "runs.":
            "As posições não estão disponíveis aqui (vêm do KDE Plasma): os "
            "cliques vão acontecer onde o ponteiro estiver quando o macro "
            "rodar.",
        "Stop recording": "Parar de gravar", "Record again": "Gravar de novo",
        "{n} steps.": "{n} passos.", "Nothing was recorded.": "Nada foi gravado.",
        "Capture…": "Capturar…", "Open…": "Abrir…",
        "Remove this button": "Tirar este botão",
        "Remove the image": "Tirar a imagem",
        "Farm assistant": "Assistente de farm",
        "Builds an ordinary macro that farms matches by looking at the "
        "screen, like a person: it brings the game to the front, clicks the "
        "lobby buttons in order, plays your strategy, keeps watching for the "
        "skip, reconnect and end of match buttons, and starts again. Capture "
        "each button from the screen while the game shows it. The macro can "
        "be edited later like any other.":
            "Monta um macro comum que farma partidas olhando a tela, como uma "
            "pessoa: traz o jogo para a frente, clica nos botões do lobby em "
            "ordem, joga a sua estratégia, fica de olho nos botões de pular, "
            "de reconectar e de fim da partida, e começa de novo. Capture "
            "cada botão da tela enquanto o jogo mostra ele. Depois dá para "
            "editar o macro como qualquer outro.",
        "Farm": "Farm",
        "Optional. When the game window closes, the macro opens this link "
        "and waits for the game again. A Roblox game page becomes "
        "roblox://placeId=…, the link Roblox documents for opening a game in "
        "the app.":
            "Opcional. Quando a janela do jogo fecha, o macro abre este link "
            "e espera o jogo de novo. Uma página de jogo do Roblox vira "
            "roblox://placeId=…, o link que o Roblox documenta para abrir um "
            "jogo no app.",
        "+ Add a lobby button": "+ Adicionar um botão do lobby",
        "How long to wait for each lobby button. When the first one does not "
        "show up, the macro takes it that a match already started and goes "
        "on to the strategy.":
            "Quanto esperar por cada botão do lobby. Quando o primeiro não "
            "aparece, o macro entende que a partida já começou e vai para a "
            "estratégia.",
        "None, only farm": "Nenhuma, só farmar",
        "A macro you made or recorded that places and upgrades the towers. "
        "Its steps are copied into the farm, so later changes to it do not "
        "change the farm.":
            "Um macro que você montou ou gravou, que coloca e melhora as "
            "torres. Os passos dele são copiados para o farm, então mudar ele "
            "depois não muda o farm.",
        "Optional. Clicked whenever it shows up, like a skip wave button.":
            "Opcional. Clicado sempre que aparece, como um botão de pular "
            "onda.",
        "Optional. Clicked whenever it shows up, and the farm starts over "
        "from the lobby.":
            "Opcional. Clicado sempre que aparece, e o farm recomeça do "
            "lobby.",
        "A button that only shows when the match is over, like Play again or "
        "Return to lobby. The macro clicks it and starts the next match.":
            "Um botão que só aparece quando a partida acaba, como Jogar de "
            "novo ou Voltar ao lobby. O macro clica nele e começa a próxima "
            "partida.",
        "If the end of the match takes longer than this, the farm starts "
        "over.":
            "Se o fim da partida demorar mais que isso, o farm recomeça.",
        "Match {run} done, farming for {time}.":
            "Partida {run} concluída, farmando há {time}.",
        "{run} becomes the number of the match and {time} how long the farm "
        "has been running.":
            "{run} vira o número da partida e {time} há quanto tempo o farm "
            "está rodando.",
        "Game window:": "Janela do jogo:", "Reopen with:": "Reabrir com:",
        "Lobby buttons:": "Botões do lobby:",
        "Wait for each:": "Esperar cada um:", "Strategy:": "Estratégia:",
        "Skip button:": "Botão de pular:",
        "Reconnect button:": "Botão de reconectar:",
        "End of the match:": "Fim da partida:",
        "Longest match:": "Partida mais longa:", "Report:": "Relatório:",
        "Create the macro": "Criar o macro",
        "Type the name of the game window, like Sober.":
            "Digite o nome da janela do jogo, como Sober.",
        "Capture the button that shows at the end of a match: the farm needs "
        "it to know when a match is over.":
            "Capture o botão que aparece no fim da partida: o farm precisa "
            "dele para saber quando a partida acabou.",
        "The link must start with https://, roblox:// or steam://.":
            "O link precisa começar com https://, roblox:// ou steam://.",
        "The webhook must start with https://.":
            "O webhook precisa começar com https://.",
        "The strategy is too long to fit in a farm: a macro holds {n} steps.":
            "A estratégia é longa demais para caber num farm: um macro tem no "
            "máximo {n} passos.",
        "Import macros": "Importar macros",
        "Farm assistant…": "Assistente de farm…", "Export…": "Exportar…",
        "Import…": "Importar…",
        "Build a macro that farms matches of a tower defense game, step by "
        "step":
            "Montar, passo a passo, um macro que farma partidas de um jogo de "
            "tower defense",
        "Save the selected macro, with its images, in a file to share":
            "Salvar o macro selecionado, com as imagens, num arquivo para "
            "compartilhar",
        "Add the macros of a {ext} file": "Adicionar os macros de um arquivo "
                                          "{ext}",
        "Export macro": "Exportar macro", "WayClick macros": "Macros do WayClick",
        "Could not write {file}: {err}":
            "Não foi possível gravar {file}: {err}",
        "Saved {file}. Import… adds it on another computer.":
            "Salvo em {file}. O botão Importar… adiciona ele em outro "
            "computador.",
        "The Discord webhook stayed out of the file: whoever has it could "
        "post in your channel.":
            "O webhook do Discord ficou fora do arquivo: quem tivesse ele "
            "poderia postar no seu canal.",
        "These images could not be read and were left out: {files}":
            "Estas imagens não puderam ser lidas e ficaram de fora: {files}",
        "{file} is not a WayClick macro file.":
            "{file} não é um arquivo de macros do WayClick.",
        "The app name, like Sober, or the whole title of the game window. "
        "Open windows… lists both.":
            "O nome do app, como Sober, ou o título inteiro da janela do "
            "jogo. Janelas abertas… mostra os dois.",
        # a Doca
        "Dock": "Doca", "Size:": "Tamanho:", "Game:": "Jogo:",
        "Open the Dock": "Abrir a Doca", "Close the Dock": "Fechar a Doca",
        "Start the game": "Abrir o jogo", "Show": "Mostrar",
        "Bring the Dock window to the front":
            "Trazer a janela da Doca para a frente",
        "Open, {w}×{h}.": "Aberta, {w}×{h}.",
        "The game is running.": "O jogo está rodando.",
        "No game is running in it.": "Nenhum jogo rodando nela.",
        "Opening the Dock…": "Abrindo a Doca…",
        "The Dock did not open.": "A Doca não abriu.",
        "Closed.": "Fechada.",
        "KWin did not find the Dock window.":
            "O KWin não achou a janela da Doca.",
        "The game opens in a window of its own that belongs to WayClick. "
        "Macros set to Play in the Dock press keys, click and look at the "
        "screen only in there, so your mouse and keyboard stay free, and the "
        "Dock window can stay behind other windows or minimized. Your usual "
        "Sober opens one copy at a time: close it before starting it in the "
        "Dock, or pick one of your Sober accounts in Account.":
            "O jogo abre numa janela só dele, que é do WayClick. Os macros "
            "marcados com Jogar na Doca apertam teclas, clicam e olham a "
            "tela só lá dentro, então o seu mouse e o seu teclado ficam "
            "livres, e a janela da Doca pode ficar atrás das outras ou "
            "minimizada. O seu Sober de sempre abre uma cópia por vez: "
            "feche ele antes de abrir na Doca, ou escolha uma das suas "
            "contas do Sober em Conta.",
        "Play in the Dock": "Jogar na Doca",
        "Keys, clicks and screen steps go only to the game in the Dock, "
        "never to the window in front, and positions are those of the Dock "
        "screen. Open the Dock on the Dock tab.":
            "Teclas, cliques e passos de tela vão só para o jogo na Doca, "
            "nunca para a janela da frente, e as posições são as da tela da "
            "Doca. Abra a Doca na aba Doca.",
        "in the Dock": "na Doca",
        "The Dock is not open.": "A Doca não está aberta.",
        "The Dock is not open. Open it on the Dock tab first.":
            "A Doca não está aberta. Abra ela antes, na aba Doca.",
        "the Dock is not open, so the macro did not start.":
            "a Doca não está aberta, então o macro não começou.",
        "the Dock closed, so the macro stopped.":
            "a Doca fechou, então o macro parou.",
        "the Dock is not open, so {url} was not opened.":
            "a Doca não está aberta, então {url} não foi aberto.",
        "nothing on this computer opens {url}.":
            "nada neste computador abre {url}.",
        "{url} did not open: {err}": "{url} não abriu: {err}",
        "The game did not open: {err}": "O jogo não abriu: {err}",
        "The Dock did not send its screen.":
            "A Doca não mandou a tela dela.",
        "The Dock needs a Wayland session of KDE Plasma.":
            "A Doca precisa de uma sessão Wayland do KDE Plasma.",
        "The Dock needs KWin (kwin_wayland), the compositor of KDE Plasma.":
            "A Doca precisa do KWin (kwin_wayland), o compositor do KDE "
            "Plasma.",
        "The Dock needs dbus-daemon (the dbus or dbus-daemon package).":
            "A Doca precisa do dbus-daemon (pacote dbus ou dbus-daemon).",
        "The D-Bus of the Dock did not start.":
            "O D-Bus da Doca não iniciou.",
        "The KWin of the Dock did not start.":
            "O KWin da Doca não iniciou.",
        "The Dock window is {w}×{h}, not {W}×{H}: does it fit on the screen? "
        "Macros made at {W}×{H} may miss.":
            "A janela da Doca está com {w}×{h}, não {W}×{H}: ela cabe na "
            "tela? Macros feitos em {W}×{H} podem errar.",
        "Sober is already open outside the Dock, and it opens one copy at a "
        "time. Close it, or pick one of your Sober accounts in Account, then "
        "press Start the game.":
            "O Sober já está aberto fora da Doca, e ele abre uma cópia por "
            "vez. Feche ele, ou escolha uma das suas contas do Sober em "
            "Conta, e aperte Abrir o jogo.",
        "Recording in {n} s, play inside the Dock window…":
            "Gravando em {n} s, jogue dentro da janela da Doca…",
        "Capturing the Dock in {n} s…": "Capturando a Doca em {n} s…",
        "Image and color steps need PyGObject.":
            "Os passos de imagem e cor precisam do PyGObject.",
        "The Dock is already open in another WayClick window.":
            "A Doca já está aberta em outra janela do WayClick.",
        # ---- Tower Defense ----
        "Tower Defense":
            "Tower Defense",
        "tower {n}":
            "torre {n}",
        "Place {tower} at spot {n}":
            "Colocar {tower} no lugar {n}",
        "Sell {tower} at spot {n}":
            "Vender {tower} do lugar {n}",
        "Upgrade {tower} at spot {n}, top path":
            "Melhorar {tower} do lugar {n}, caminho de cima",
        "Upgrade {tower} at spot {n}, bottom path":
            "Melhorar {tower} do lugar {n}, caminho de baixo",
        "Press {key}":
            "Apertar {key}",
        "Click the picture {name}":
            "Clicar na imagem {name}",
        "Wait for the picture {name}":
            "Esperar a imagem {name}",
        "{n} empty pictures of the Screens tab come from the file.":
            "{n} imagens vazias da aba Telas vieram do arquivo.",
        "{n} pictures of the Screens tab were kept as you captured them.":
            "{n} imagens da aba Telas ficaram como você capturou.",
        "It was made with the Dock at {w}×{h}.":
            "Ela foi feita com a Doca em {w}×{h}.",
        "the Dock closed, so the farm stopped.":
            "a Doca fechou, então o farm parou.",
        "Paused":
            "Pausado",
        "Paused.":
            "Pausado.",
        "Resumed.":
            "Continuando.",
        "the farm stopped on an error: {err}":
            "o farm parou num erro: {err}",
        "Farm started with {name}.":
            "Farm começou com {name}.",
        "This strategy has no actions yet: the farm only plays the matches.":
            "Esta estratégia ainda não tem ações: o farm só joga as partidas.",
        "Disconnected: clicked Reconnect, starting over.":
            "Desconectado: cliquei em Reconectar, recomeçando.",
        "The game window closed.":
            "A janela do jogo fechou.",
        "{n} rounds in a row without a match: the pictures on the Screens tab may not match the game. Stopping.":
            "{n} rodadas seguidas sem partida: as imagens da aba Telas podem não bater com o jogo. Parando.",
        "The game is not open in the Dock. Open it there, or put the game link in Settings, then press Start again.":
            "O jogo não está aberto na Doca. Abra ele lá, ou ponha o link do jogo nas Configurações, e aperte Iniciar de novo.",
        "Opening the game…":
            "Abrindo o jogo…",
        "Opening the game in the Dock…":
            "Abrindo o jogo na Doca…",
        "The game is open.":
            "O jogo abriu.",
        "The game did not open in {n} s.":
            "O jogo não abriu em {n} s.",
        "An end of match screen is open: leaving it first.":
            "Uma tela de fim de partida está aberta: saindo dela primeiro.",
        "Lobby: playing the lobby macro…":
            "Lobby: rodando o macro do lobby…",
        "Lobby: playing the lobby macro.":
            "Lobby: rodando o macro do lobby.",
        "Lobby: waiting for button {n} of {total}…":
            "Lobby: esperando o botão {n} de {total}…",
        "The first lobby button did not show up: taking it that the match already started.":
            "O primeiro botão do lobby não apareceu: contando que a partida já começou.",
        "Lobby button {n} did not show up, starting over.":
            "O botão {n} do lobby não apareceu, recomeçando.",
        "Lobby: clicked button {n}.":
            "Lobby: cliquei no botão {n}.",
        "Waiting for match {n} to start…":
            "Esperando a partida {n} começar…",
        "The match did not start in {n} s, starting over.":
            "A partida não começou em {n} s, recomeçando.",
        "Match {n} started.":
            "Partida {n} começou.",
        "Match {n}: next, {what} at {t}.":
            "Partida {n}: a seguir, {what} em {t}.",
        "Match {n}: the strategy is done, waiting for the end of the match…":
            "Partida {n}: a estratégia acabou, esperando o fim da partida…",
        "Match {n} took longer than {m} min.":
            "A partida {n} passou de {m} min.",
        "Stopped skipping waves, as set in Settings.":
            "Parei de pular ondas, como está nas Configurações.",
        "{what}.":
            "{what}.",
        "{what}: the Sell button did not show up.":
            "{what}: o botão de vender não apareceu.",
        "{what}: choose the sell key in Settings or capture the Sell button.":
            "{what}: escolha a tecla de vender nas Configurações ou capture o botão de vender.",
        "{what}: it did not show up.":
            "{what}: ela não apareceu.",
        "{what} (not confirmed: there is no Tower placed picture).":
            "{what} (sem confirmar: falta a imagem Torre colocada).",
        "{what}, after {n} tries.":
            "{what}, depois de {n} tentativas.",
        "{what}: it did not go down in {n} s, going on.":
            "{what}: ela não foi colocada em {n} s, seguindo.",
        "{what}: not yet, maybe not enough cash. Trying again every {n} s.":
            "{what}: ainda não, talvez falte dinheiro. Tentando de novo a cada {n} s.",
        "{tower} at spot {n} did not open its panel when clicked.":
            "{tower} do lugar {n} não abriu o painel ao ser clicada.",
        "Camera lined up.":
            "Câmera alinhada.",
        "defeat":
            "derrota",
        "victory":
            "vitória",
        "over":
            "fim",
        "too long":
            "longa demais",
        "Match {n}: {result}, {time}.":
            "Partida {n}: {result}, {time}.",
        "Done: {n} matches, as set in Settings.":
            "Pronto: {n} partidas, como está nas Configurações.",
        "After the match: waiting for button {n}…":
            "Depois da partida: esperando o botão {n}…",
        "The button {n} after the match did not show up.":
            "O botão {n} de depois da partida não apareceu.",
        "Left the match screen.":
            "Saí da tela da partida.",
        "Place a tower":
            "Colocar uma torre",
        "Upgrade, top path":
            "Melhorar, caminho de cima",
        "Upgrade, bottom path":
            "Melhorar, caminho de baixo",
        "Sell a tower":
            "Vender uma torre",
        "Press a key":
            "Apertar uma tecla",
        "Click a picture":
            "Clicar numa imagem",
        "Wait for a picture":
            "Esperar uma imagem",
        "Action":
            "Ação",
        "Seconds from the start of the match.":
            "Segundos desde o começo da partida.",
        "How long to wait for the picture before going on.":
            "Quanto esperar a imagem antes de seguir.",
        "At:":
            "Em:",
        "Do:":
            "Fazer:",
        "Tower:":
            "Torre:",
        "Spot:":
            "Lugar:",
        "Picture:":
            "Imagem:",
        "spot {n} ({x}, {y})":
            "lugar {n} ({x}, {y})",
        "This tower has no spots yet: pick them on the game with Pick.":
            "Esta torre ainda não tem lugares: marque no jogo com Marcar.",
        "Press a key of the keyboard.":
            "Aperte uma tecla do teclado.",
        "Capture the picture first.":
            "Capture a imagem primeiro.",
        "This action is not complete.":
            "Esta ação não está completa.",
        "Running:":
            "Rodando:",
        "Matches:":
            "Partidas:",
        "Wins:":
            "Vitórias:",
        "Losses:":
            "Derrotas:",
        "Log:":
            "Registro:",
        "Lobby macro:":
            "Macro do lobby:",
        "Match started:":
            "Partida começou:",
        "Optional. Something that only shows once the match has begun, like the wave counter. The clock of the strategy starts when it shows up; without it, right after the lobby.":
            "Opcional. Algo que só aparece com a partida já começada, como o contador de ondas. O relógio da estratégia começa quando isso aparece; sem ele, logo depois do lobby.",
        "Tower placed:":
            "Torre colocada:",
        "A piece of the panel that opens when a tower goes down (turn on Show Tower Options in the game settings). With it, a tower that did not go down, like when there is not enough cash yet, is tried again.":
            "Um pedaço do painel que abre quando uma torre é colocada (ligue Show Tower Options nas configurações do jogo). Com ele, uma torre que não foi colocada, como quando ainda falta dinheiro, é tentada de novo.",
        "Skip wave:":
            "Pular onda:",
        "Optional. Clicked whenever it shows up during the match.":
            "Opcional. Clicado sempre que aparece durante a partida.",
        "Victory:":
            "Vitória:",
        "Something that only shows when the match is won. Counts a win.":
            "Algo que só aparece quando a partida é ganha. Conta uma vitória.",
        "Defeat:":
            "Derrota:",
        "Something that only shows when the match is lost. Counts a loss.":
            "Algo que só aparece quando a partida é perdida. Conta uma derrota.",
        "Reconnect:":
            "Reconectar:",
        "Sell button:":
            "Botão de vender:",
        "Optional. Clicked to sell, after clicking the tower. Without it, the sell key of Settings is pressed.":
            "Opcional. Clicado para vender, depois de clicar na torre. Sem ele, a tecla de vender das Configurações é apertada.",
        "The buttons clicked in order when the match ends, like the one that plays again or the one that goes back to the lobby. With no Victory and Defeat pictures, the first one is also how the end of the match is seen.":
            "Os botões clicados em ordem quando a partida acaba, como o de jogar de novo ou o de voltar ao lobby. Sem as imagens de Vitória e Derrota, o primeiro também é como o fim da partida é visto.",
        "After the match:":
            "Depois da partida:",
        "The app name of the game window in the Dock, like Sober.":
            "O nome do app da janela do jogo na Doca, como Sober.",
        "Game link:":
            "Link do jogo:",
        "A Roblox game page, a private server link (…?privateServerLinkCode=…) or a roblox:// link. It opens in the Dock with Join the game, and again when the game closes.":
            "Uma página de jogo do Roblox, um link de servidor privado (…?privateServerLinkCode=…) ou um link roblox://. Ele abre na Doca com Entrar no jogo, e de novo quando o jogo fecha.",
        "Wait for each lobby button:":
            "Esperar cada botão do lobby:",
        "Wait for the match to start:":
            "Esperar a partida começar:",
        "Fast":
            "Rápida",
        "Balanced":
            "Equilibrada",
        "Slow":
            "Lenta",
        "Action speed:":
            "Velocidade das ações:",
        "How long to wait between pressing, clicking and looking. Slow suits a slow computer or connection.":
            "Quanto esperar entre apertar, clicar e olhar. Lenta serve para computador ou conexão lentos.",
        "Keep trying to place for:":
            "Tentar colocar por até:",
        "Try again every:":
            "Tentar de novo a cada:",
        "whole match":
            "a partida toda",
        "Skip waves until:":
            "Pular ondas até:",
        "Minutes from the start of the match. After that the Skip wave button is left alone.":
            "Minutos desde o começo da partida. Depois disso o botão de pular onda fica quieto.",
        "Zoom out with:":
            "Afastar com:",
        "Then drag down:":
            "Depois arrastar para baixo:",
        "With the right button, from the middle of the screen. A negative number drags up.":
            "Com o botão direito, do meio da tela. Um número negativo arrasta para cima.",
        "Upgrade, top path:":
            "Melhorar, caminho de cima:",
        "Upgrade, bottom path:":
            "Melhorar, caminho de baixo:",
        "Sell:":
            "Vender:",
        "Cancel placing:":
            "Cancelar a colocação:",
        "Start:":
            "Iniciar:",
        "Stop:":
            "Parar:",
        "Pause or resume:":
            "Pausar ou continuar:",
        "{run} is the number of the match, {result} how it ended, {wins} and {losses} the counts, and {time} how long the farm has been running.":
            "{run} é o número da partida, {result} como ela acabou, {wins} e {losses} as contagens, e {time} há quanto tempo o farm está rodando.",
        "Match {run}: {result}. {wins} wins, {losses} losses, farming for {time}.":
            "Partida {run}: {result}. {wins} vitórias, {losses} derrotas, farmando há {time}.",
        "This link would open the game outside the Dock. Use a Roblox game page, a private server link (…?privateServerLinkCode=…) or a roblox:// link.":
            "Este link abriria o jogo fora da Doca. Use uma página de jogo do Roblox, um link de servidor privado (…?privateServerLinkCode=…) ou um link roblox://.",
        "no spots":
            "sem lugares",
        "{n} set ✓":
            "{n} marcados ✓",
        "The list holds {n} strategies.":
            "A lista comporta {n} estratégias.",
        "Strategy {n}":
            "Estratégia {n}",
        "Delete {name}? Its towers, spots and timeline go with it.":
            "Apagar {name}? As torres, os lugares e a linha do tempo vão junto.",
        "Delete strategy":
            "Apagar estratégia",
        "Strategy":
            "Estratégia",
        "This strategy was made with the Dock at {w}×{h}, and the Dock is {W}×{H} now: the spots and pictures will miss. Change the size on the Dock tab.":
            "Esta estratégia foi feita com a Doca em {w}×{h}, e a Doca está em {W}×{H} agora: os lugares e as imagens vão errar. Troque o tamanho na aba Doca.",
        "Open the Dock first: the spots are picked on the game.":
            "Abra a Doca primeiro: os lugares são marcados no jogo.",
        "Stop recording first.":
            "Pare a gravação primeiro.",
        "{n} actions of the timeline use spot {spot} of {tower}, and they go with it. Remove it?":
            "{n} ações da linha do tempo usam o lugar {spot} de {tower}, e elas vão junto. Remover?",
        "Remove spot":
            "Remover lugar",
        "A tower holds {n} spots.":
            "Uma torre comporta {n} lugares.",
        "Remove the {n} spots of {tower}?":
            "Remover os {n} lugares de {tower}?",
        "{n} actions of the timeline use them and go too.":
            "{n} ações da linha do tempo usam eles e vão junto.",
        "Remove spots":
            "Remover lugares",
        "Timeline ({n})":
            "Linha do tempo ({n})",
        "A timeline holds {n} actions.":
            "Uma linha do tempo comporta {n} ações.",
        "Clear the timeline":
            "Limpar a linha do tempo",
        "Remove the {n} actions of {name}? The towers and their spots stay.":
            "Remover as {n} ações de {name}? As torres e os lugares delas ficam.",
        "Make a strategy first: New, at the top.":
            "Crie uma estratégia primeiro: Novo, lá em cima.",
        "Open the Dock first: you record by playing in it.":
            "Abra a Doca primeiro: a gravação é jogando nela.",
        "Stop the farm first.":
            "Pare o farm primeiro.",
        "Record":
            "Gravar",
        "The timeline already has {n} actions. Replace them with the new recording, or add the new ones to them?":
            "A linha do tempo já tem {n} ações. Trocar pela gravação nova, ou juntar as novas a elas?",
        "Replace them":
            "Trocar",
        "Add to them":
            "Juntar",
        "Lining up the camera…":
            "Alinhando a câmera…",
        "None":
            "Nenhum",
        "Export strategy":
            "Exportar estratégia",
        "Saved {file}.":
            "{file} salvo.",
        "{n} pictures could not be read and are not in it.":
            "{n} imagens não puderam ser lidas e ficaram de fora.",
        "Import strategy":
            "Importar estratégia",
        "{file} is not a Tower Defense strategy file of WayClick.":
            "{file} não é um arquivo de estratégia de Tower Defense do WayClick.",
        "{file} holds macros, not a strategy: import it on the Hotkeys tab.":
            "{file} traz macros, não uma estratégia: importe na aba Atalhos.",
        "Imported {name}.":
            "{name} importada.",
        "Make a strategy first: New, at the top of the Strategy tab.":
            "Crie uma estratégia primeiro: Novo, no alto da aba Estratégia.",
        "Capture how a match ends on the Screens tab: Victory, Defeat or a button After the match.":
            "Capture como uma partida acaba na aba Telas: Vitória, Derrota ou um botão de Depois da partida.",
        "Farm stopped.":
            "Farm parado.",
        "Open the Dock first.":
            "Abra a Doca primeiro.",
        "Running…":
            "Rodando…",
        "Pause":
            "Pausar",
        "Resume":
            "Continuar",
        "● Recording {t}":
            "● Gravando {t}",
        "The farm is running. Close the Dock, and the game with it?":
            "O farm está rodando. Fechar a Doca, e o jogo junto?",
        "Put the game link in Settings: a Roblox game page or a private server link.":
            "Ponha o link do jogo nas Configurações: uma página de jogo do Roblox ou um link de servidor privado.",
        "{start} starts · {stop} stops · {pause} pauses or resumes, in any window while the Dock is open":
            "{start} inicia · {stop} para · {pause} pausa ou continua, em qualquer janela com a Doca aberta",
        "Start ({key})":
            "Iniciar ({key})",
        "Stop ({key})":
            "Parar ({key})",
        "Open the Dock on the left: it opens with the game link of Settings, and the game shows up here.":
            "Abra a Doca à esquerda: ela abre com o link do jogo das Configurações, e o jogo aparece aqui.",
        "Picking the spots of {tower}: click where it goes, right click a spot to remove it, Esc ends.":
            "Marcando os lugares de {tower}: clique onde ela vai, botão direito num lugar tira ele, Esc termina.",
        "Recording: 1 to 5 picks a tower and a click places it. Click a tower, then {top} or {bottom} upgrades it{sell}. F10 undoes.":
            "Gravando: 1 a 5 escolhe a torre e um clique coloca ela. Clique numa torre, e {top} ou {bottom} melhora ela{sell}. F10 desfaz.",
        ", {key} sells it":
            ", {key} vende ela",
        "The game of the Dock: mouse, wheel and keys over it go to the game.":
            "O jogo da Doca: mouse, roda e teclas em cima dele vão para o jogo.",
        "The Dock is closed.":
            "A Doca está fechada.",
        "Waiting for the picture of the Dock…":
            "Esperando a imagem da Doca…",
        "Delete":
            "Apagar",
        "● Record":
            "● Gravar",
        "Undo (F10)":
            "Desfazer (F10)",
        "Add…":
            "Adicionar…",
        "Edit…":
            "Editar…",
        "Clear":
            "Limpar",
        "+ Add a button":
            "+ Adicionar um botão",
        "Dock the game":
            "Encaixar o jogo",
        "Undock":
            "Desencaixar",
        "Join the game":
            "Entrar no jogo",
        "Clear the log":
            "Limpar o registro",
        "Line up the camera":
            "Alinhar a câmera",
        "Pick":
            "Marcar",
        "Pick the spots of this tower on the game":
            "Marcar os lugares desta torre no jogo",
        "Remove the spots of this tower":
            "Tirar os lugares desta torre",
        "Minimize the Dock window: the game keeps playing here":
            "Minimizar a janela da Doca: o jogo segue aqui",
        "Open the game link of Settings in the Dock":
            "Abrir na Doca o link do jogo das Configurações",
        "Zoom out and drag the camera down, as at the start of each match":
            "Afastar e arrastar a câmera para baixo, como no começo de cada partida",
        "No sell key":
            "Sem tecla de vender",
        "Show the spots":
            "Mostrar os lugares",
        "Towers":
            "Torres",
        "Lobby":
            "Lobby",
        "Game keys":
            "Teclas do jogo",
        "Report":
            "Relatório",
        "Line up the camera when a match starts":
            "Alinhar a câmera quando a partida começa",
        "The spots are places on the screen, so the camera has to be the same in every match: zoomed all the way out and looking down. Recording lines it up too.":
            "Os lugares são pontos da tela, então a câmera precisa ser a mesma em toda partida: afastada ao máximo e olhando para baixo. A gravação também alinha.",
        "Map, mode, the guide it follows…":
            "Mapa, modo, o guia que ela segue…",
        "The buttons clicked in order to get into a match. The lobby macro, if any, plays before them, like walking into an elevator.":
            "Os botões clicados em ordem para entrar numa partida. O macro do lobby, se houver, roda antes deles, como andar até um elevador.",
        "Pictures of the game, captured from the Dock while it shows them. The farm looks for them on the screen, nothing reads the memory of the game. In the settings of the game, turn off Camera Shake, Low Quality Rings and the Switch Hotbar key (Q also cancels placing), turn on Show Tower Options, and keep the UI Scale as it was when you captured them.":
            "Imagens do jogo, capturadas da Doca enquanto ela mostra elas. O farm procura elas na tela, nada lê a memória do jogo. Nas configurações do jogo, desligue Camera Shake, Low Quality Rings e a tecla Switch Hotbar (o Q também cancela a colocação), ligue Show Tower Options, e mantenha a UI Scale como estava quando você capturou.",
        "They work in any window while the Dock is open. A hotkey macro or the WayClick hotkey on the same key takes it first.":
            "Eles valem em qualquer janela com a Doca aberta. Um macro de atalho ou o atalho do WayClick na mesma tecla fica com ela primeiro.",
        "Screens":
            "Telas",
        "{file} holds a Tower Defense strategy, not macros. Import it on the Tower Defense tab?":
            "{file} traz uma estratégia de Tower Defense, não macros. Importar na aba Tower Defense?",
        # --- 1.11.0: a barra lateral e as contas do Sober
        "Search…": "Buscar…", "Search (Ctrl+F)": "Buscar (Ctrl+F)",
        "Nothing found.": "Nada encontrado.",
        "Automation": "Automação", "Games": "Jogos",
        "Sober accounts": "Contas do Sober",
        "Account:": "Conta:", "Your usual Sober": "O seu Sober de sempre",
        "Which Sober the Dock opens: your usual one, or one of the accounts "
        "of the Sober accounts page. Other games ignore it.":
            "Qual Sober a Doca abre: o de sempre, ou uma das contas da página "
            "Contas do Sober. Os outros jogos não usam isto.",
        "{name} is already open outside the Dock. Close it, then press "
        "Start the game.":
            "{name} já está aberta fora da Doca. Feche ela e aperte Abrir o "
            "jogo.",
        "{name} is already open in the Dock.": "{name} já está aberta na Doca.",
        "Sober is already open in {name}, and it takes a link only when it "
        "opens, so {url} was not opened. Close it first.":
            "O Sober já está aberto em {name}, e ele só recebe um link ao "
            "abrir, então {url} não foi aberto. Feche ele antes.",
        "Sober accounts need Flatpak, with Sober from Flathub.":
            "As contas do Sober precisam do Flatpak, com o Sober do Flathub.",
        "Sober accounts need pasta, from the passt package. Arch: sudo "
        "pacman -S passt. Fedora: sudo dnf install passt. Debian and Ubuntu: "
        "sudo apt install passt.":
            "As contas do Sober precisam do pasta, do pacote passt. Arch: "
            "sudo pacman -S passt. Fedora: sudo dnf install passt. Debian e "
            "Ubuntu: sudo apt install passt.",
        "Sober accounts need {tool}, from util-linux.":
            "As contas do Sober precisam do {tool}, do util-linux.",
        "Sober accounts need util-linux 2.38 or newer (unshare --map-user).":
            "As contas do Sober precisam do util-linux 2.38 ou mais novo "
            "(unshare --map-user).",
        "Sober accounts need time namespaces (Linux 5.6 or newer).":
            "As contas do Sober precisam de namespaces de tempo (Linux 5.6 "
            "ou mais novo).",
        "Sober accounts need user namespaces, and this system turns them "
        "off for users.":
            "As contas do Sober precisam de namespaces de usuário, e este "
            "sistema desliga eles para usuários comuns.",
        "Close it first.": "Feche ela antes.",
        "Each account is a separate Sober, with its own Roblox sign-in, "
        "settings and network, so it opens next to your usual Sober and both "
        "play at the same time. The first time, Sober sets itself up in the "
        "new account (it downloads Roblox again) and you sign in there. Your "
        "usual Sober and its files stay as they are.":
            "Cada conta é um Sober separado, com login do Roblox, "
            "configurações e rede próprios, então ela abre ao lado do seu "
            "Sober de sempre e os dois jogam ao mesmo tempo. Na primeira vez "
            "o Sober se prepara na conta nova (baixa o Roblox de novo) e você "
            "entra com o login lá. O seu Sober de sempre e os arquivos dele "
            "ficam como estão.",
        "Roblox calls running two clients at once an exploit, though it says "
        "it does not ban for it today, and Sober does not support it. Use it "
        "at your own risk.":
            "O Roblox chama rodar dois clientes ao mesmo tempo de exploit, "
            "embora diga que hoje não bane por isso, e o Sober não dá suporte "
            "a isso. Use por sua conta e risco.",
        "{name} (closing…)": "{name} (fechando…)",
        "{name} (open)": "{name} (aberta)",
        "{n} accounts at most.": "No máximo {n} contas.",
        "{name} is already open.": "{name} já está aberta.",
        "Sober did not open in {name}: {why}":
            "O Sober não abriu em {name}: {why}",
        "exit code {n}": "código de saída {n}",
        "{name} is closed.": "{name} foi fechada.",
        "{name} is open.": "{name} está aberta.",
        "Sober stopped in {name}: {why}": "O Sober parou em {name}: {why}",
        "{name} did not close. Close its window.":
            "{name} não fechou. Feche a janela dela.",
        "Account {n}": "Conta {n}",
        "Add account": "Adicionar conta", "Add account…": "Adicionar conta…",
        "Rename account": "Renomear conta", "Rename…": "Renomear…",
        "Remove account": "Remover conta", "Remove…": "Remover…",
        "Open": "Abrir", "Close": "Fechar",
        "Added {name}. Open it, and Sober sets itself up there the first "
        "time.":
            "{name} adicionada. Abra ela, e o Sober se prepara lá na "
            "primeira vez.",
        "Remove {name}? Its Sober data on this computer is deleted: the "
        "Roblox sign-in, the settings and the downloads ({size}). The Roblox "
        "account itself stays as it is.":
            "Remover {name}? Os dados do Sober dela neste computador são "
            "apagados: o login do Roblox, as configurações e os downloads "
            "({size}). A conta do Roblox em si fica como está.",
        "{name} was not removed: {err}": "{name} não foi removida: {err}",
        "Removed {name}.": "{name} removida.",
        "Sober is not installed. Install it from Flathub "
        "(org.vinegarhq.Sober) and open it once.":
            "O Sober não está instalado. Instale pelo Flathub "
            "(org.vinegarhq.Sober) e abra uma vez.",
        "Opening {name}… its Sober window shows up in a few seconds.":
            "Abrindo {name}… a janela do Sober dela aparece em alguns "
            "segundos.",
        "Closing {name}…": "Fechando {name}…",
        "Open the Sober of this account on the desktop":
            "Abrir o Sober desta conta na área de trabalho",
        # 1.12.0: Suspend e buscas na tela vindas de um .ahk
        "Suspend hotkey:": "Atalho de Suspend:",
        "Like Suspend in AutoHotkey: this key turns the hotkey macros on "
        "and off from inside the game, and the game never receives it. "
        "Turning them off also stops the macros that are running.":
            "Como o Suspend do AutoHotkey: esta tecla liga e desliga os "
            "macros de atalho de dentro do jogo, e o jogo nunca a recebe. "
            "Desligar também para os macros que estiverem rodando.",
        "No Suspend hotkey": "Sem atalho de Suspend",
        "{key} is the Suspend hotkey, so the macro with the same hotkey does "
        "not run by it.":
            "{key} é o atalho de Suspend, então o macro com o mesmo atalho "
            "não roda por ele.",
        "{key} is also the arm/disarm hotkey, so one press would arm the "
        "clicker and switch the macros.":
            "{key} também é o atalho de armar/desarmar, então um aperto "
            "armaria o clique e trocaria os macros.",
        "Suspend hotkey: {key} (switches the hotkey macros on and off, and "
        "the game does not receive it)":
            "Atalho de Suspend: {key} (liga e desliga os macros de atalho, e "
            "o jogo não recebe a tecla)",
        "Only the position": "Só a posição",
        "Find {color} in {w}×{h} at {x}, {y}":
            "Achar {color} na área {w}×{h} em {x}, {y}",
        "No {color} in {w}×{h} at {x}, {y}":
            "Sem {color} na área {w}×{h} em {x}, {y}",
        "Watch for {color} in {w}×{h} at {x}, {y}":
            "Vigiar {color} na área {w}×{h} em {x}, {y}",
        "Watch for {color} to go away from {w}×{h} at {x}, {y}":
            "Vigiar {color} sumir da área {w}×{h} em {x}, {y}",
        "Find {color} anywhere on the screen":
            "Achar {color} em qualquer lugar da tela",
        "No {color} anywhere on the screen": "Sem {color} em toda a tela",
        "Watch for {color} anywhere on the screen":
            "Vigiar {color} em toda a tela",
        "Watch for {color} to go away from the screen":
            "Vigiar {color} sumir da tela",
        "Reads the color of one point, like PixelGetColor in AutoHotkey. "
        "With an area picked, it looks for the color anywhere in it, like "
        "PixelSearch.":
            "Lê a cor de um ponto, como o PixelGetColor do AutoHotkey. Com "
            "uma área escolhida, procura a cor em qualquer lugar dela, como o "
            "PixelSearch.",
        "{hk}: a mouse button cannot be the Suspend hotkey here":
            "{hk}: aqui um botão do mouse não pode ser o atalho de Suspend",
        "{hk}: the Suspend hotkey is already {key}, this one was left out":
            "{hk}: o atalho de Suspend já é {key}, este ficou de fora",
        "{hk}: Suspend {mode} became the Suspend hotkey, which switches the "
        "macros on and off":
            "{hk}: Suspend {mode} virou o atalho de Suspend, que liga e "
            "desliga os macros",
        "{hk}: only Suspend was imported, the rest of it was left out":
            "{hk}: só o Suspend foi importado, o resto ficou de fora",
        "ImageSearch *Trans: WayClick compares the whole picture, with no "
        "see-through color":
            "ImageSearch *Trans: o WayClick compara a imagem inteira, sem cor "
            "transparente",
        "ImageSearch *w/*h: the picture is used at its own size":
            "ImageSearch *w/*h: a imagem é usada no tamanho dela",
        "image {file} was not found next to the script; pick it in the step":
            "a imagem {file} não está ao lado do script; escolha no passo",
        "{what}: the position is not a number, skipped":
            "{what}: a posição não é um número, ignorado",
        "{what}: the area is not made of numbers, skipped":
            "{what}: a área não é feita de números, ignorado",
        "{what}: the color is not a number, skipped":
            "{what}: a cor não é um número, ignorado",
        "{what}: the image file is not a plain name, skipped":
            "{what}: o arquivo da imagem não é um nome simples, ignorado",
        "ImageSearch: WayClick compares pictures by similarity, so *{n} "
        "became {pct}% similar; when found, the click goes to the middle of "
        "the picture (AutoHotkey gives its top left corner)":
            "ImageSearch: o WayClick compara imagens por semelhança, então "
            "*{n} virou {pct}% de semelhança; achada, o clique vai no meio "
            "da imagem (o AutoHotkey dá o canto de cima à esquerda)",
        "the offset added to the position found was left out":
            "a soma feita na posição achada ficou de fora",
        "the position found is used only by a click right after the search, "
        "skipped":
            "a posição achada só vale num clique logo depois da busca, "
            "ignorado",
        "{hk}: each press is held {ms} ms, as long as the shortest wait of "
        "the loop, so the game sees it and the loop keeps the script's pace":
            "{hk}: cada aperto fica seguro {ms} ms, o tempo da menor espera "
            "do laço, para o jogo ver a tecla e o laço manter o ritmo do "
            "script",
    },
}
LANGS = {"English": "en_US", "Português (BR)": "pt_BR"}
LANG = "en_US"


def _(text, **fmt):
    out = TRANSLATIONS.get(LANG, {}).get(text, text)
    return out.format(**fmt) if fmt else out


def default_language():
    loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
           or os.environ.get("LANG") or "")
    return "pt_BR" if loc.lower().startswith("pt") else "en_US"


# --------------------------------------------------------------- tema ------
# As cores saem dos esquemas do próprio KDE (/usr/share/color-schemes/*.colors),
# então "Breeze Dark" aqui é exatamente o Breeze Dark do sistema, e não um
# escuro inventado. Quem não tiver os arquivos cai nos dois embutidos.
SCHEME_DIRS = ["/usr/share/color-schemes",
               os.path.join(os.environ.get("XDG_DATA_HOME")
                            or os.path.expanduser("~/.local/share"),
                            "color-schemes")]

# grupo do .colors -> (chave, papel do QPalette)
SCHEME_MAP = [
    ("Colors:Window", "BackgroundNormal", "Window"),
    ("Colors:Window", "ForegroundNormal", "WindowText"),
    ("Colors:View", "BackgroundNormal", "Base"),
    ("Colors:View", "BackgroundAlternate", "AlternateBase"),
    ("Colors:View", "ForegroundNormal", "Text"),
    ("Colors:View", "ForegroundInactive", "PlaceholderText"),
    ("Colors:View", "ForegroundLink", "Link"),
    ("Colors:View", "ForegroundNegative", "BrightText"),
    ("Colors:Button", "BackgroundNormal", "Button"),
    ("Colors:Button", "ForegroundNormal", "ButtonText"),
    ("Colors:Selection", "BackgroundNormal", "Highlight"),
    ("Colors:Selection", "ForegroundNormal", "HighlightedText"),
    ("Colors:Tooltip", "BackgroundNormal", "ToolTipBase"),
    ("Colors:Tooltip", "ForegroundNormal", "ToolTipText"),
]
DISABLED_MAP = [("Colors:Window", "ForegroundInactive", "WindowText"),
                ("Colors:View", "ForegroundInactive", "Text"),
                ("Colors:Button", "ForegroundInactive", "ButtonText")]

BUILTIN_SCHEMES = {
    "Dark": {"Window": "#2a2e32", "WindowText": "#fcfcfc", "Base": "#1f2225",
             "AlternateBase": "#2a2e32", "Text": "#fcfcfc", "Button": "#31363b",
             "ButtonText": "#fcfcfc", "Highlight": "#3daee9",
             "HighlightedText": "#fcfcfc", "Link": "#1d99f3",
             "BrightText": "#da4453", "PlaceholderText": "#a1a9b1",
             "ToolTipBase": "#31363b", "ToolTipText": "#fcfcfc",
             "_disabled": {"WindowText": "#7f8c8d", "Text": "#7f8c8d",
                           "ButtonText": "#7f8c8d"}},
    "Light": {"Window": "#eff0f1", "WindowText": "#232629", "Base": "#fcfcfc",
              "AlternateBase": "#eff0f1", "Text": "#232629",
              "Button": "#eff0f1", "ButtonText": "#232629",
              "Highlight": "#3daee9", "HighlightedText": "#fcfcfc",
              "Link": "#2980b9", "BrightText": "#da4453",
              "PlaceholderText": "#7f8c8d", "ToolTipBase": "#f7f7f7",
              "ToolTipText": "#232629",
              "_disabled": {"WindowText": "#a8a8a8", "Text": "#a8a8a8",
                            "ButtonText": "#a8a8a8"}},
}
_SCHEMES = None
_SYS = {}


def _read_scheme(path):
    """Lê um .colors do KDE (formato INI) para o formato de paleta daqui."""
    groups, cur = {}, None
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    cur = line[1:-1]
                    groups[cur] = {}
                elif cur and "=" in line:
                    k, v = line.split("=", 1)
                    groups[cur][k.strip()] = v.strip()
    except OSError:
        return None
    rgb = lambda v: "#%02x%02x%02x" % tuple(int(x) for x in v.split(",")[:3])
    spec, disabled = {}, {}
    try:
        for group, key, role in SCHEME_MAP:
            val = groups.get(group, {}).get(key)
            if val:
                spec[role] = rgb(val)
        for group, key, role in DISABLED_MAP:
            val = groups.get(group, {}).get(key)
            if val:
                disabled[role] = rgb(val)
    except (ValueError, TypeError):
        return None
    if "Window" not in spec or "WindowText" not in spec:
        return None
    spec["_disabled"] = disabled
    name = groups.get("General", {}).get("Name") or \
        os.path.basename(path).replace(".colors", "")
    return name, spec


def color_schemes():
    """{nome visível: paleta}, dos esquemas instalados mais os embutidos."""
    global _SCHEMES
    if _SCHEMES is not None:
        return _SCHEMES
    found = {}
    for d in SCHEME_DIRS:
        for path in sorted(glob.glob(os.path.join(d, "*.colors"))):
            got = _read_scheme(path)
            if got:
                found[got[0]] = got[1]
    for name, spec in BUILTIN_SCHEMES.items():
        found.setdefault(name, spec)
    _SCHEMES = found
    return found


def _palette(spec):
    from PySide6.QtGui import QPalette
    pal = QPalette()
    for role, color in spec.items():
        if role != "_disabled":
            pal.setColor(getattr(QPalette, role), QColor(color))
    for role, color in spec.get("_disabled", {}).items():
        pal.setColor(QPalette.Disabled, getattr(QPalette, role), QColor(color))
    return pal


def apply_theme(name):
    """Fusion + paleta: o estilo nativo ignora paleta, então forçar cor exige
    trocar de estilo junto. 'System' devolve estilo e paleta originais."""
    app = QApplication.instance()
    if not app:
        return
    _SYS.setdefault("palette", QApplication.palette())
    _SYS.setdefault("style", app.style().objectName())
    spec = color_schemes().get(name)
    if spec:
        app.setStyle("Fusion")
        app.setPalette(_palette(spec))
    else:
        app.setStyle(_SYS["style"])
        app.setPalette(_SYS["palette"])
    for w in app.topLevelWidgets():          # sem isto o já desenhado não muda
        w.setPalette(app.palette())
        for child in w.findChildren(QWidget):
            child.setPalette(app.palette())


# ---------------------------------------------------------- ícone/tray ------
STATE_COLORS = {"run": "#27ae60", "wait": "#f39c12",
                "armed": "#3daee9", "": "#9aa3ab"}
ARROW = [(20, 8), (20, 47), (29, 39), (35, 54), (43, 50), (37, 35), (48, 34)]
INK = "#12161a"


def _draw_arrow(p, color, scale):
    path = QPainterPath()
    pts = [QPointF(x * scale, y * scale) for x, y in ARROW]
    path.moveTo(pts[0])
    for q in pts[1:]:
        path.lineTo(q)
    path.closeSubpath()
    pen = QPen(QColor(INK), 5 * scale)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(path)


def wayclick_icon(state="", size=64):
    """Ícone do app: cursor com ondas de clique, numa placa arredondada."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#3a4650"))
    grad.setColorAt(1, QColor("#222a31"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
    col = QColor(STATE_COLORS.get(state, STATE_COLORS[""]))
    pen = QPen(col, 4 * s)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for i, r in enumerate((13, 19)):
        p.setOpacity(0.8 - i * 0.35)
        p.drawArc(QRectF((24 - r) * s, (20 - r) * s, 2 * r * s, 2 * r * s),
                  -20 * 16, 100 * 16)
    p.setOpacity(1.0)
    _draw_arrow(p, "#f7f9fa" if not state else col, s)
    p.end()
    return QIcon(px)


# Estilos da bandeja. Cada um preenche ~58 de 64 px: os ícones do painel são
# quadrados e cheios, então glifo estreito parece pequeno ao lado dos outros.
def _ink_for(col):
    """Contorno claro em cor escura e vice-versa — sem isso Black some no painel
    escuro e White some no claro."""
    c = QColor(col)
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
    return "#12161a" if lum > 0.45 else "#eef1f4"


def _tray_cursor(p, col, s, ink):
    pts = [(16, 3), (16, 53), (27, 43), (34, 61), (44, 57), (37, 40), (51, 39)]
    path = QPainterPath()
    q = [QPointF(x * s, y * s) for x, y in pts]
    path.moveTo(q[0])
    for pt in q[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    pen = QPen(QColor(ink), 6 * s)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QColor(col))
    p.drawPath(path)


def _tray_mouse(p, col, s, ink):
    p.setBrush(QColor(col))
    p.setPen(QPen(QColor(ink), 6 * s))
    p.drawRoundedRect(QRectF(12 * s, 4 * s, 40 * s, 56 * s), 20 * s, 22 * s)
    p.setPen(QPen(QColor(ink), 4 * s))
    p.drawLine(15 * s, 27 * s, 49 * s, 27 * s)
    p.setBrush(QColor(ink))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(29 * s, 11 * s, 6 * s, 14 * s), 3 * s, 3 * s)


def _tray_dot(p, col, s, ink):
    p.setPen(QPen(QColor(ink), 6 * s))
    p.setBrush(QColor(col))
    p.drawEllipse(QRectF(6 * s, 6 * s, 52 * s, 52 * s))


def _tray_ring(p, col, s, ink):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(ink), 16 * s))
    p.drawEllipse(QRectF(9 * s, 9 * s, 46 * s, 46 * s))
    p.setPen(QPen(QColor(col), 11 * s))
    p.drawEllipse(QRectF(9 * s, 9 * s, 46 * s, 46 * s))


TRAY_STYLES = {"Cursor": _tray_cursor, "Mouse": _tray_mouse,
               "Dot": _tray_dot, "Ring": _tray_ring}
TRAY_COLORS = {"Match state": None, "Green": "#27ae60", "Blue": "#3daee9",
               "Purple": "#9b59b6", "Orange": "#f39c12", "Red": "#e74c3c",
               "Teal": "#1abc9c", "Grey": "#9aa3ab", "White": "#f2f4f6",
               "Black": "#15191d"}
BURST_FRAMES = 6
PULSE_TINTS = (1.0, 0.72, 0.5)      # brilho do glifo em cada fase do pulso


def _tint(col, factor):
    c = QColor(col)
    return QColor.fromHsvF(c.hueF(), c.saturationF() * (0.4 + 0.6 * factor),
                           min(1.0, c.valueF() * (0.55 + 0.45 * factor)))


def tray_icon(state="", phase=0, style="Cursor", color="Match state",
              burst=None, size=64):
    """Ícone da bandeja, sem placa, para ficar do tamanho dos outros do painel.

    A animação é a mesma para todo formato de propósito: pulsar o próprio glifo
    (e não um arco desenhado ao lado) é o que funciona igual num cursor, num
    ponto e num anel — o arco fixo do desenho anterior só fazia sentido para o
    cursor e ficava deslocado nos outros.
    """
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    fixed = TRAY_COLORS.get(color)
    base = QColor(fixed or STATE_COLORS.get(state, STATE_COLORS[""]))
    # o contorno sai da cor BASE, nunca da cor pulsada: calculado do tom
    # escurecido ele cruzava o limiar de luminância e piscava de preto para
    # branco no meio da animação
    ink = _ink_for(base)
    col, scale = base, 1.0
    if burst is not None and burst < BURST_FRAMES:
        t = burst / (BURST_FRAMES - 1.0)
        r = (14 + 18 * t) * s
        p.setPen(QPen(base, max(1.0, 7 * (1 - t) * s)))
        p.setBrush(Qt.NoBrush)
        p.setOpacity(max(0.0, 0.9 * (1 - t)))
        p.drawEllipse(QRectF(32 * s - r, 32 * s - r, 2 * r, 2 * r))
        p.setOpacity(1.0)
        # o glifo encolhe e volta: sem isso ele tapa o anel, que agora quase
        # não sobrava espaço com o glifo preenchendo 58 de 64 px
        scale = 0.62 + 0.38 * t
    elif state in ("run", "armed"):
        col = _tint(base, PULSE_TINTS[phase % len(PULSE_TINTS)])
    if scale != 1.0:
        p.translate(32 * s, 32 * s)
        p.scale(scale, scale)
        p.translate(-32 * s, -32 * s)
    TRAY_STYLES.get(style, _tray_cursor)(p, col.name(), s, ink)
    p.end()
    return QIcon(px)


# ------------------------------------------------------------- autostart ----
XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
AUTOSTART = os.path.join(XDG_CONFIG, "autostart", "wayclick.desktop")
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=WayClick
Comment=Autoclicker for Wayland, using /dev/uinput
Exec={exec}
Icon=input-mouse
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def autostart_enabled():
    return os.path.exists(AUTOSTART)


def set_autostart(on):
    """Entrada XDG em ~/.config/autostart. Começa na bandeja: no login o app
    já herda o grupo 'input', então não precisa do desvio pelo `sg`."""
    if not on:
        try:
            os.remove(AUTOSTART)
        except FileNotFoundError:
            pass
        return True
    try:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        cmd = shlex.join([sys.executable, os.path.abspath(__file__), "--tray"])
        with open(AUTOSTART, "w") as fh:
            fh.write(DESKTOP_ENTRY.format(exec=cmd))
        return True
    except OSError:
        return False


# ------------------------------------------------- editor de macros (UI) ----
STEP_KINDS = (("tap", "Tap a key or click"), ("down", "Hold down"),
              ("up", "Release"), ("wait", "Wait"), ("text", "Type text"),
              ("wheel", "Scroll the wheel"), ("move", "Move the pointer"),
              ("point", "Go to a screen position"),
              ("image", "Find an image on screen"),
              ("pixel", "Check a pixel color"),
              ("watch", "Watch the screen or a window"),
              ("window", "Check or focus a window"),
              ("count", "Count"),
              ("notify", "Send a notification"),
              ("open", "Open a link or file"))
POINT_CLICK_LABELS = (("none", "Only move"), ("left", "Left click"),
                      ("right", "Right click"), ("middle", "Middle click"),
                      ("double", "Double click"))
SEEN_ACT_LABELS = (("none", "Nothing"), ("move", "Point at it"),
                   ("left", "Click it"), ("right", "Right click it"),
                   ("middle", "Middle click it"),
                   ("double", "Double click it"))
FLOW_LABELS = (("next", "Continue"), ("stop", "Stop the macro"),
               ("restart", "Start over from step 1"), ("jump", "Go to step"))
WANT_LABELS = {"image": (("seen", "appears"), ("gone", "disappears")),
               "pixel": (("seen", "has the color"),
                         ("gone", "loses the color")),
               "window": (("seen", "is open"), ("gone", "is closed"))}
WATCH_KIND_LABELS = (("image", "An image"), ("pixel", "A pixel color"),
                     ("window", "A window"))
WINDOW_ACT_LABELS = (("check", "Only check"),
                     ("focus", "Bring it to the front"))
WHEEL_DIRS = (("up", "Up"), ("down", "Down"), ("left", "Left"),
              ("right", "Right"))


class _Rows:
    """Linhas de um QFormLayout que aparecem e somem juntas com o rótulo.
    QFormLayout.setRowVisible só existe do Qt 6.4 em diante."""

    def __init__(self, form):
        self.form, self.rows = form, {}

    def add(self, key, text, widget):
        lbl = QLabel(_(text))
        self.form.addRow(lbl, widget)
        self.rows[key] = (lbl, widget)
        return widget

    def show_only(self, keys):
        for key, (lbl, widget) in self.rows.items():
            lbl.setVisible(key in keys)
            widget.setVisible(key in keys)


def _hrow(*widgets, stretch=True):
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        row.addWidget(x)
    if stretch:
        row.addStretch()
    return w


def _combo(items, current):
    box = QComboBox()
    for key, label in items:
        box.addItem(_(label), key)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


class FlowEdit(QWidget):
    """Para onde o macro vai depois: seguir, parar, recomeçar, ir a um passo."""

    def __init__(self, how, to):
        super().__init__()
        self.how = _combo(FLOW_LABELS, how)
        self.to = QSpinBox()
        self.to.setRange(1, MAX_STEPS)
        self.to.setValue(to)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.how)
        row.addWidget(self.to)
        row.addStretch()
        self.how.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, _i=0):
        self.to.setVisible(self.how.currentData() == "jump")

    def value(self):
        return self.how.currentData(), self.to.value()


class _PickCanvas(QWidget):
    """O quadro capturado, com zoom. Clicar escolhe um pixel, arrastar marca
    um retângulo; tudo em pixels do quadro."""
    hovered = Signal(int, int)
    released = Signal()
    zoom_step = Signal(int)

    def __init__(self, image):
        super().__init__()
        self.image = image
        self.zoom = 1.0
        self.start = self.end = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.set_zoom(1.0)

    def set_zoom(self, z):
        self.zoom = z
        self.setFixedSize(max(1, round(self.image.width() * z)),
                          max(1, round(self.image.height() * z)))
        self.update()

    def _px(self, pos):
        return (min(self.image.width() - 1, max(0, int(pos.x() / self.zoom))),
                min(self.image.height() - 1, max(0, int(pos.y() / self.zoom))))

    def selection(self):
        """(x0, y0, x1, y1), fim exclusivo; um clique dá um pixel só."""
        if self.start is None or self.end is None:
            return None
        (ax, ay), (bx, by) = self.start, self.end
        return min(ax, bx), min(ay, by), max(ax, bx) + 1, max(ay, by) + 1

    def paintEvent(self, _e):
        p = QPainter(self)
        if self.zoom < 1:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(0, 0, self.width(), self.height()), self.image)
        sel = self.selection()
        if sel:
            x0, y0, x1, y1 = (v * self.zoom for v in sel)
            r = QRectF(x0, y0, x1 - x0, y1 - y0)
            p.fillRect(r, QColor(61, 174, 233, 60))
            p.setPen(QPen(QColor("#3daee9"), 2))
            p.drawRect(r)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = self.end = self._px(e.position())
            self.update()

    def mouseMoveEvent(self, e):
        px = self._px(e.position())
        self.hovered.emit(*px)
        if e.buttons() & Qt.LeftButton and self.start is not None:
            self.end = px
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.start is not None:
            self.end = self._px(e.position())
            self.update()
            self.released.emit()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_step.emit(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
        else:
            e.ignore()


class ScreenPicker(QDialog):
    """Escolher na tela capturada: um ponto (e a cor dele) ou uma área, que
    vira o recorte de um passo de imagem ou a região de busca."""
    ZOOMS = (("Fit", 0.0), ("25%", 0.25), ("50%", 0.5), ("100%", 1.0),
             ("200%", 2.0), ("400%", 4.0), ("800%", 8.0))

    def __init__(self, parent, frames, mode):
        super().__init__(parent)
        self.setWindowTitle(_("Pick on screen"))
        self.frames, self.mode = frames, mode
        self.point = self.color = self.rect = self.crop = None
        self.screen = QComboBox()
        for i, f in enumerate(frames, 1):
            self.screen.addItem(_("Screen {n}", n=i) + "  " + _(
                "{w}×{h} at {x}, {y}", w=f.w, h=f.h, x=f.x, y=f.y))
        self.zoom = QComboBox()
        for label, z in self.ZOOMS:
            self.zoom.addItem(_(label), z)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignCenter)
        self.info = QLabel(" ")
        hint = QLabel(_("Click the point.") if mode == "point" else _(
            "Drag a rectangle around it. Ctrl and the wheel zoom."))
        hint.setEnabled(False)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        top = QHBoxLayout()
        if len(frames) > 1:
            top.addWidget(QLabel(_("Screen:")))
            top.addWidget(self.screen)
        top.addWidget(QLabel(_("Zoom:")))
        top.addWidget(self.zoom)
        top.addStretch()
        col = QVBoxLayout(self)
        col.addLayout(top)
        col.addWidget(self.scroll, 1)
        col.addWidget(hint)
        col.addWidget(self.info)
        col.addWidget(self.buttons)
        self.resize(1100, 720)
        self.canvas = None
        self.screen.currentIndexChanged.connect(self._show_screen)
        self.zoom.currentIndexChanged.connect(self._apply_zoom)
        self._show_screen()
        QTimer.singleShot(0, self._apply_zoom)

    def frame(self):
        return self.frames[max(0, self.screen.currentIndex())]

    def _show_screen(self, _i=0):
        self.canvas = _PickCanvas(rgb_qimage(self.frame().rgb))
        self.canvas.hovered.connect(self._hover)
        self.canvas.released.connect(self._picked)
        self.canvas.zoom_step.connect(self._zoom_by)
        self.scroll.setWidget(self.canvas)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._apply_zoom()

    def _apply_zoom(self, _i=0):
        z = self.zoom.currentData() or 0.0
        if not z:
            vp = self.scroll.viewport().size()
            img = self.canvas.image
            z = min(1.0, max(0.05, min((vp.width() - 4) / img.width(),
                                       (vp.height() - 4) / img.height())))
        self.canvas.set_zoom(z)

    def _zoom_by(self, d):
        self.zoom.setCurrentIndex(max(0, min(self.zoom.count() - 1,
                                             self.zoom.currentIndex() + d)))

    def _global(self, px, py):
        gx, gy = self.frame().to_global(px, py)
        return math.floor(gx), math.floor(gy)

    def _color(self, gx, gy, px, py):
        """A cor que o passo vai conferir: a do ponto lógico, lida como o
        macro lê. Com escala, um ponto lógico cobre vários pixels do quadro."""
        c = pixel_at(self.frames, gx, gy)
        return c if c is not None else tuple(int(v)
                                             for v in self.frame().rgb[py, px])

    def _hover(self, px, py):
        gx, gy = self._global(px + 0.5, py + 0.5)
        self.info.setText(f"{gx}, {gy}   "
                          f"{color_hex(self._color(gx, gy, px, py))}")

    def _picked(self):
        f = self.frame()
        x0, y0, x1, y1 = self.canvas.selection()
        if self.mode == "point" or (x1 - x0 < 3 and y1 - y0 < 3):
            if self.mode != "point":
                return                    # área pequena demais: segue arrastando
            self.point = self._global(x0 + 0.5, y0 + 0.5)
            self.color = self._color(*self.point, x0, y0)
            self.accept()
            return
        self.crop = f.rgb[y0:y1, x0:x1].copy()
        gx0, gy0 = f.to_global(x0, y0)
        gx1, gy1 = f.to_global(x1, y1)
        self.rect = [math.floor(gx0), math.floor(gy0),
                     math.ceil(gx1) - math.floor(gx0),
                     math.ceil(gy1) - math.floor(gy0)]
        self.info.setText(_("Selected {w}×{h}", w=x1 - x0, h=y1 - y0))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)


def _ui_wait(seconds, done=lambda: False):
    """Espera sem travar a janela."""
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.02)


def grab_frames(delay, note=None, dock=None):
    """Um quadro de cada tela depois de `delay` segundos, tempo de trocar
    para a janela do jogo. Devolve (quadros, erro); note (um QLabel) mostra
    a contagem. Com dock, o quadro é o da tela da Doca, sem portal."""
    def say(text):
        if note is not None:
            note.setText(text)
    if dock is not None and not dock.alive():
        return [], _("The Dock is not open. Open it on the Dock tab first.")
    cap = dock.screen if dock is not None else SCREEN()
    frames = []
    say("" if dock is not None else _("Waiting for screen sharing…"))
    try:
        cap.start()
        _ui_wait(SCREEN_ASK_S, lambda: cap.state != "starting")
        if cap.state != "on":
            return [], cap.error or _("The screen is not available.")
        cap.acquire()
        try:
            for left in range(int(delay), 0, -1):
                say(_("Capturing the Dock in {n} s…", n=left)
                    if dock is not None else
                    _("Capturing in {n} s, switch to the game window…",
                      n=left))
                _ui_wait(1.0)
            frames = cap.frames()
        finally:
            cap.release()
    finally:
        say("")
    if not frames:
        return [], cap.error or _("No picture came from the screen.")
    return frames, ""


def save_crop(rgb):
    """Um recorte da tela vira PNG novo na pasta de imagens. O caminho, ou
    None se não deu para gravar."""
    folder = images_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    base = time.strftime("image-%Y%m%d-%H%M%S")
    path, n = os.path.join(folder, base + ".png"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}-{n}.png"), n + 1
    return path if rgb_qimage(rgb).save(path, "PNG") else None


def image_thumb(label, path, box=(240, 120), empty=None):
    """A miniatura de uma imagem de passo num QLabel."""
    img = QImage(path) if path else QImage()
    if img.isNull():
        label.setPixmap(QPixmap())
        label.setText(os.path.basename(path) if path
                      else empty or _("No image yet"))
        label.setToolTip(path or "")
        return
    k = min(box[0] / img.width(), box[1] / img.height())
    mode = Qt.FastTransformation if k >= 1 else Qt.SmoothTransformation
    k = min(k, 4.0)
    label.setPixmap(QPixmap.fromImage(img.scaled(
        max(1, round(img.width() * k)), max(1, round(img.height() * k)),
        Qt.KeepAspectRatio, mode)))
    label.setToolTip(path)


class StepDialog(QDialog):
    """Um passo do macro: apertar, segurar, soltar, esperar, digitar..."""

    def __init__(self, parent=None, step=None):
        super().__init__(parent)
        self.setWindowTitle(_("Step"))
        step = dict(step or {"op": "tap", "code": KEYS["E"]})
        self.op = QComboBox()
        for op, label in STEP_KINDS:
            self.op.addItem(_(label), op)
        self.op.setCurrentIndex(max(0, self.op.findData(step["op"])))
        code = step.get("code") or KEYS["E"]
        self.key = KeyCatcher(code, step.get("name") or trigger_label(code),
                              allow_mouse=True)
        self.times = QSpinBox()
        self.times.setRange(1, 1000)
        self.times.setValue(step.get("times", 1))
        self.ms = QDoubleSpinBox()
        self.ms.setRange(0.0, 3600000.0)
        self.ms.setDecimals(1)
        self.ms.setSuffix(" ms")
        self.ms.setValue(step.get("ms", 100.0))
        self.max = QDoubleSpinBox()
        self.max.setRange(0.0, 3600000.0)
        self.max.setDecimals(1)
        self.max.setSuffix(" ms")
        self.max.setSpecialValueText(_("no, exact"))
        self.max.setValue(step.get("max", 0.0))
        self.text = QLineEdit(step.get("text", ""))
        self.text.setMaxLength(500)
        self.wheel = QComboBox()
        for d, label in WHEEL_DIRS:
            self.wheel.addItem(_(label), d)
        self.wheel.setCurrentIndex(max(0, self.wheel.findData(
            step.get("dir", "down"))))
        self.dx, self.dy = QSpinBox(), QSpinBox()
        for box, key in ((self.dx, "dx"), (self.dy, "dy")):
            box.setRange(-5000, 5000)
            box.setValue(step.get(key, 0))
        self._screen_widgets(step)
        self._farm_widgets(step)
        form = QFormLayout()
        self.rows = _Rows(form)
        form.addRow(QLabel(_("Action:")), self.op)
        self.rows.add("key", "Key or button:", self.key)
        self.rows.add("times", "Times:", self.times)
        self.rows.add("ms", "Wait:", self.ms)
        self.rows.add("max", "Random, up to:", self.max)
        self.rows.add("text", "Text:", self.text)
        self.rows.add("dir", "Direction:", self.wheel)
        self.rows.add("dx", "Right (px):", self.dx)
        self.rows.add("dy", "Down (px):", self.dy)
        for key, text, widget in self.farm_rows[:1] + self.screen_rows \
                + self.farm_rows[1:]:
            self.rows.add(key, text, widget)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setEnabled(False)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#d04030;")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        col = QVBoxLayout(self)
        col.addLayout(form)
        col.addWidget(self.note)
        col.addWidget(self.error)
        col.addWidget(buttons)
        self.op.currentIndexChanged.connect(self._sync)
        self.want.currentIndexChanged.connect(self._sync_want)
        self.wwant.currentIndexChanged.connect(self._sync_want)
        self.wkind.currentIndexChanged.connect(self._sync)
        self.cevery.valueChanged.connect(self._sync_count)
        self._sync()

    # ------------------------------------------ vigiar, contar, janela --
    def _farm_widgets(self, step):
        op = step["op"]
        self.wkind = _combo(WATCH_KIND_LABELS, step.get("kind", "image"))
        self.wwant = QComboBox()
        self._wwant = step.get("want", "seen")
        self.every = QDoubleSpinBox()
        self.every.setRange(0.1, 3600.0)
        self.every.setDecimals(1)
        self.every.setSuffix(" s")
        self.every.setValue((step.get("every", 2000.0) if op == "watch"
                             else 2000.0) / 1000.0)
        self.every.setToolTip(_("How often it looks. Every look at the "
                                "screen costs a little CPU; a window is "
                                "checked at most once a second."))
        self.wok = FlowEdit(step.get("ok", "next") if op == "watch"
                            else "next", step.get("ok_to", 1))
        self.counter = QLineEdit(step.get("name", "") if op == "count"
                                 else "")
        self.counter.setMaxLength(24)
        self.counter.setPlaceholderText(_("matches"))
        self.cevery = QSpinBox()
        self.cevery.setRange(0, MAX_RUNS)
        self.cevery.setSpecialValueText(_("never, only count"))
        self.cevery.setValue(step.get("every", 0) if op == "count" else 0)
        self.cok = FlowEdit(step.get("ok", "stop") if op == "count"
                            else "stop", step.get("ok_to", 1))
        self.match = QComboBox()
        self.match.setEditable(True)
        self.match.setInsertPolicy(QComboBox.NoInsert)
        self.match.setMinimumContentsLength(18)
        self.match.setEditText(step.get("match", "Sober"))
        self.match.lineEdit().setMaxLength(100)
        self.b_wins = QPushButton(_("Open windows…"))
        self.b_wins.setToolTip(_("List the windows that are open now"))
        self.b_wins.clicked.connect(self._list_windows)
        self.winact = _combo(WINDOW_ACT_LABELS, step.get("act", "check")
                             if op == "window" else "check")
        self.farm_rows = (
            ("wkind", "Watch for:", self.wkind),
            ("match", "Window:", _hrow(self.match, self.b_wins)),
            ("winact", "When it is open:", self.winact),
            ("wwant", "React when it:", self.wwant),
            ("every", "Check every:", self.every),
            ("wok", "Then:", self.wok),
            ("counter", "Counter:", self.counter),
            ("cevery", "Every:", self.cevery),
            ("cok", "Then:", self.cok))

    def _kind(self):
        """O que o passo olha: o próprio tipo, ou o do vigia."""
        op = self.op.currentData()
        return self.wkind.currentData() if op == "watch" else op

    def _sync_count(self, _v=0):
        self.cok.setEnabled(self.cevery.value() > 0)

    def _dock(self):
        """A Doca, se o macro deste passo joga nela."""
        f = getattr(self.parent(), "target_dock", None)
        return f() if callable(f) else None

    def _list_windows(self):
        window_names(self.match, self.error, self._dock())

    # ------------------------------------------- passos que olham a tela --
    def _screen_widgets(self, step):
        op = step["op"]
        self.px, self.py = QSpinBox(), QSpinBox()
        for box, key in ((self.px, "x"), (self.py, "y")):
            box.setRange(-COORD, COORD)
            box.setValue(step.get(key, 0))
        self.b_pick = QPushButton(_("Pick on screen…"))
        self.b_pick.clicked.connect(lambda: self._capture("point"))
        self.click = _combo(POINT_CLICK_LABELS, step.get("click", "left"))
        self.human = QCheckBox(_("Curved, like a hand"))
        self.human.setChecked(bool(step.get("human")))
        self.human_ms = QDoubleSpinBox()
        self.human_ms.setRange(20.0, 10000.0)
        self.human_ms.setDecimals(0)
        self.human_ms.setSuffix(" ms")
        self.human_ms.setValue(step.get("ms", 300.0))
        self.human.toggled.connect(self.human_ms.setEnabled)
        self.human_ms.setEnabled(self.human.isChecked())
        self.spread = QSpinBox()
        self.spread.setRange(0, 500)
        self.spread.setSuffix(" px")
        self.spread.setSpecialValueText(_("none"))
        self.spread.setValue(step.get("spread", 0))
        self.img_path = step.get("path", "")
        self.img_view = QLabel()
        self.img_view.setAlignment(Qt.AlignCenter)
        self.img_view.setMinimumSize(160, 60)
        self.img_view.setFrameShape(QFrame.StyledPanel)
        self.b_shot = QPushButton(_("Capture from screen…"))
        self.b_shot.clicked.connect(lambda: self._capture("image"))
        self.b_file = QPushButton(_("Open file…"))
        self.b_file.clicked.connect(self._open_image)
        img_w = QWidget()
        img_col = QVBoxLayout(img_w)
        img_col.setContentsMargins(0, 0, 0, 0)
        img_col.addWidget(self.img_view)
        img_col.addWidget(_hrow(self.b_shot, self.b_file))
        self.conf = QSpinBox()
        self.conf.setRange(50, 100)
        self.conf.setSuffix(" %")
        self.conf.setValue(round(step.get("conf", 0.85) * 100))
        self.conf.setToolTip(_("How alike the screen must be to the picture. "
                               "Lower finds more, and also finds wrong "
                               "places."))
        self.region = step.get("region")
        # a ordem do PixelSearch de um .ahk, que o editor não mostra
        self._scan = {k: step[k] for k in ("scan", "rtl", "btt") if k in step}
        self.region_lbl = QLabel()
        self.b_region = QPushButton(_("Pick area…"))
        self.b_region.clicked.connect(lambda: self._capture("region"))
        self.b_whole = QPushButton(_("Whole screen"))
        self.b_whole.clicked.connect(lambda: self._set_region(None))
        self.color = QLineEdit(step.get("color", "#ffffff"))
        self.color.setMaxLength(7)
        self.color.setFixedWidth(90)
        self.swatch = QLabel()
        self.swatch.setFixedSize(22, 22)
        self.color.textChanged.connect(self._sync_swatch)
        self.tol = QSpinBox()
        self.tol.setRange(0, 255)
        self.tol.setValue(step.get("tol", 16))
        self.tol.setToolTip(_("How far red, green and blue may each be from "
                              "the color, 0 to 255."))
        self.want = QComboBox()
        self._want = step.get("want", "seen")
        self.wait_s = QDoubleSpinBox()
        self.wait_s.setRange(0.0, 3600.0)
        self.wait_s.setDecimals(1)
        self.wait_s.setSuffix(" s")
        self.wait_s.setSpecialValueText(_("check once"))
        self.wait_s.setValue(step.get("wait", 5000.0) / 1000.0)
        self.act = _combo(SEEN_ACT_LABELS, step.get(
            "act", "left" if op != "pixel" else "none"))
        self.act.currentIndexChanged.connect(self._sync_want)
        self.ok_flow = FlowEdit(step.get("ok", "next"), step.get("ok_to", 1))
        self.fail_flow = FlowEdit(step.get("fail", "stop"),
                                  step.get("fail_to", 1))
        self.delay = QSpinBox()
        self.delay.setRange(0, 10)
        self.delay.setSuffix(" s")
        self.delay.setValue(3)
        self.delay.setToolTip(_("Time to switch to the game window before the "
                                "screen is captured."))
        self.msg = QLineEdit(step.get("text", "") if op == "notify" else "")
        self.msg.setMaxLength(1000)
        self.hook = QLineEdit(step.get("url", "") if op == "notify" else "")
        self.hook.setPlaceholderText(_("optional") + ", "
                                     "https://discord.com/api/webhooks/…")
        self.shot = QCheckBox(_("Attach a screenshot"))
        self.shot.setChecked(bool(step.get("shot")))
        self.hook.textChanged.connect(self._sync_hook)
        self.link = QLineEdit(step.get("url", "") if op == "open" else "")
        self.link.setPlaceholderText("https://…, roblox://…, steam://…")
        self.cap_note = QLabel("")
        self.cap_note.setWordWrap(True)
        self.screen_rows = (
            ("pos", "Position:", _hrow(QLabel("X"), self.px, QLabel("Y"),
                                       self.py, self.b_pick)),
            ("click", "Then:", self.click),
            ("image", "Image:", img_w),
            ("conf", "Similarity:", self.conf),
            ("region", "Search in:", _hrow(self.region_lbl, self.b_region,
                                           self.b_whole)),
            ("color", "Color:", _hrow(self.swatch, self.color)),
            ("tol", "Tolerance:", self.tol),
            ("want", "Wait until it:", self.want),
            ("wait", "Wait up to:", self.wait_s),
            ("act", "When found:", self.act),
            ("human", "Movement:", _hrow(self.human, self.human_ms)),
            ("spread", "Random offset:", self.spread),
            ("ok", "If it happens:", self.ok_flow),
            ("fail", "If not:", self.fail_flow),
            ("delay", "Capture after:", _hrow(self.delay, self.cap_note)),
            ("msg", "Message:", self.msg),
            ("hook", "Discord webhook:", self.hook),
            ("shot", "", self.shot),
            ("link", "Link or file:", self.link))
        self._show_image()
        self._set_region(self.region)
        self._sync_swatch()
        self._sync_hook()

    def _sync_swatch(self, _t=""):
        c = parse_color(self.color.text())
        self.swatch.setStyleSheet(
            f"background:{color_hex(c)};border:1px solid #888;" if c
            else "border:1px dashed #888;")

    def _sync_hook(self, _t=""):
        self.shot.setEnabled(bool(self.hook.text().strip()))

    def _sync_want(self, _i=0):
        op = self.op.currentData()
        want = self.wwant if op == "watch" else self.want
        seen = want.currentData() != "gone"
        self.act.setEnabled(seen)
        moves = op == "point" or (seen and self.act.currentData() != "none")
        self.human.setEnabled(moves)
        self.human_ms.setEnabled(moves and self.human.isChecked())

    def _show_image(self):
        image_thumb(self.img_view, self.img_path)

    def _set_region(self, rect):
        self.region = rect
        # o vigia nasce depois deste campo; _sync acerta o texto depois
        kind = self._kind() if hasattr(self, "wkind") \
            else self.op.currentData()
        whole = _("Only the position") if kind == "pixel" \
            else _("Whole screen")
        self.region_lbl.setText(
            _("Whole screen") if _whole_screen(rect) else
            _("{w}×{h} at {x}, {y}", w=rect[2], h=rect[3], x=rect[0],
              y=rect[1]) if rect else whole)
        self.b_whole.setEnabled(bool(rect))

    def _open_image(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _("Image"), images_dir() if os.path.isdir(images_dir())
            else os.path.expanduser("~"),
            _("Images") + " (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        if QImage(path).isNull():
            self.error.setText(_("That file is not an image."))
            return
        self.error.setText("")
        self.img_path = path
        self._show_image()

    def _busy(self, on):
        for w in (self.b_pick, self.b_shot, self.b_region, self.b_file,
                  self.op, self.wkind):
            w.setEnabled(not on)

    def _capture(self, mode):
        """Um quadro de cada tela, depois da contagem (dá tempo de trocar
        para a janela do jogo), e o seletor. mode: point, image, region."""
        self.error.setText("")
        self._busy(True)
        try:
            frames, err = grab_frames(self.delay.value(), self.cap_note,
                                      self._dock())
        finally:
            self._busy(False)
            self._sync()
        if not frames:
            self.error.setText(err)
            return
        dlg = ScreenPicker(self, frames, "point" if mode == "point"
                           else "area")
        if dlg.exec() != QDialog.Accepted:
            return
        self._picked(mode, dlg)

    def _picked(self, mode, dlg):
        if mode == "point" and dlg.point:
            self.px.setValue(dlg.point[0])
            self.py.setValue(dlg.point[1])
            if self._kind() == "pixel" and dlg.color:
                self.color.setText(color_hex(dlg.color))
        elif mode == "region" and dlg.rect:
            self._set_region(dlg.rect)
        elif mode == "image" and dlg.crop is not None:
            path = save_crop(dlg.crop)
            if path is None:
                self.error.setText(_("Could not save {path}.",
                                     path=images_dir()))
                return
            self.img_path = path
            self._show_image()

    def _ok(self):
        op = self.op.currentData()
        if op == "count" and counter_name(self.counter.text()) \
                in TEXT_FIELDS:
            self.error.setText(_("{run} and {time} are already taken; pick "
                                 "another name."))
            return
        if self.step() is None:
            self.error.setText(_({
                "image": "Pick an image first.",
                "pixel": "The color must look like #ff8800.",
                "window": "Type the name of the window, like Sober.",
                "count": "Name the counter with letters, digits or _, like "
                         "matches.",
                "notify": "Type a message, or attach a screenshot to a "
                          "webhook.",
                "open": "Type a link or a file.",
                "text": "Type the text.",
                "move": "Right or down must not be zero.",
            }.get(self._kind(), "This step is not complete.")))
            return
        self.accept()

    NOTES = {"text": "Typed with a US keyboard layout: letters, digits and "
                     "symbols land on the same physical keys.",
             "move": "Moves by an amount, from where the pointer is. To go to "
                     "an exact position, use Go to a screen position.",
             "point": "Puts the pointer at an exact position. 0, 0 is the top "
                      "left corner of the leftmost screen, and all screens "
                      "count together, as in Display Configuration.",
             "image": "Looks for the picture on screen, like ImageSearch in "
                      "AutoHotkey. The first time, the system asks which "
                      "screens WayClick may see, and remembers the answer.",
             "pixel": "Reads the color of one point, like PixelGetColor in "
                      "AutoHotkey. With an area picked, it looks for the "
                      "color anywhere in it, like PixelSearch.",
             "notify": "Without a webhook it shows a desktop notification. "
                       "With a Discord webhook (channel settings, "
                       "Integrations) it posts in that channel. {run} in "
                       "the text becomes the number of the run, {time} how "
                       "long the macro has been running, and {matches} the "
                       "value of a counter named matches.",
             "watch": "Keeps looking while the next steps run, like a "
                      "SetTimer with ImageSearch in AutoHotkey. It looks "
                      "while the macro waits (Wait steps, screen searches) "
                      "and between steps, never in the middle of a key "
                      "press. Use it for what can show up at any time, like "
                      "a skip button, a reconnect button or the end of a "
                      "match. When it takes the macro to another step, "
                      "every watch stops until the macro passes over it "
                      "again.",
             "window": "Finds a window by app name, like Sober, or by its "
                       "whole title, and can bring it to the front. Needs "
                       "KDE Plasma. To reopen a game that closed, send the "
                       "else path to an Open a link step.",
             "count": "Adds 1 each time the macro passes here. Write the "
                      "name in braces, like {matches}, in a notification to "
                      "show the number. Every N can stop the macro or go to "
                      "a step, like every 10 matches.",
             "open": "Opens with the default program. Web pages, roblox:// "
                     "and steam:// links, files.",
             "wait": "Random, up to: each run waits a different time between "
                     "the two values.",
             "down": "Stays pressed until a Release step, or until the macro "
                     "stops, which releases everything it held."}

    SHOWN = {"tap": ("key", "times"), "down": ("key",), "up": ("key",),
             "wait": ("ms", "max"), "text": ("text",),
             "wheel": ("dir", "times"), "move": ("dx", "dy"),
             "point": ("pos", "click", "human", "spread", "delay"),
             "image": ("image", "conf", "region", "want", "wait", "act",
                       "human", "ok", "fail", "delay"),
             "pixel": ("pos", "color", "tol", "region", "want", "wait", "act",
                       "human", "ok", "fail", "delay"),
             "window": ("match", "want", "wait", "winact", "ok", "fail"),
             "count": ("counter", "cevery", "cok"),
             "notify": ("msg", "hook", "shot"), "open": ("link",)}
    WATCH_SHOWN = {"image": ("wkind", "image", "conf", "region", "wwant",
                             "every", "act", "human", "wok", "delay"),
                   "pixel": ("wkind", "pos", "color", "tol", "region",
                             "wwant", "every", "act", "human", "wok",
                             "delay"),
                   "window": ("wkind", "match", "wwant", "every", "wok")}

    @staticmethod
    def _want_items(box, items, keep):
        box.blockSignals(True)
        box.clear()
        for key, label in items:
            box.addItem(_(label), key)
        box.setCurrentIndex(max(0, box.findData(keep)))
        box.blockSignals(False)

    def _sync(self, _i=0):
        op = self.op.currentData()
        kind = self._kind()
        self.rows.show_only(self.WATCH_SHOWN[kind] if op == "watch"
                            else self.SHOWN[op])
        if op in WANT_LABELS:
            self._want_items(self.want, WANT_LABELS[op],
                             self.want.currentData() or self._want)
        if op == "watch":
            self._want_items(self.wwant, WANT_LABELS[kind],
                             self.wwant.currentData() or self._wwant)
        self._sync_count()
        self._sync_want()
        self._set_region(self.region)
        self.note.setText(_(self.NOTES[op]) if op in self.NOTES else "")
        self.note.setVisible(op in self.NOTES)
        self.adjustSize()

    def step(self):
        op = self.op.currentData()
        s = {"op": op, "code": self.key.code, "name": self.key.name,
             "times": self.times.value(), "ms": self.ms.value(),
             "max": self.max.value(), "text": self.text.text(),
             "dir": self.wheel.currentData(), "dx": self.dx.value(),
             "dy": self.dy.value()}
        if op in ("tap", "down", "up") and is_button(s["code"]):
            s.pop("name")
        if op in ("point", "image", "pixel"):
            ok, fail = self.ok_flow.value(), self.fail_flow.value()
            s.update(x=self.px.value(), y=self.py.value(),
                     click=self.click.currentData(),
                     human=self.human.isChecked() and self.human.isEnabled(),
                     ms=self.human_ms.value(), spread=self.spread.value(),
                     path=self.img_path, conf=self.conf.value() / 100.0,
                     region=self.region, color=self.color.text(),
                     tol=self.tol.value(), want=self.want.currentData(),
                     wait=self.wait_s.value() * 1000.0,
                     act=self.act.currentData(), ok=ok[0], ok_to=ok[1],
                     fail=fail[0], fail_to=fail[1], **self._scan)
        elif op == "watch":
            ok = self.wok.value()
            s.update(kind=self.wkind.currentData(), x=self.px.value(),
                     y=self.py.value(), path=self.img_path,
                     conf=self.conf.value() / 100.0, region=self.region,
                     color=self.color.text(), tol=self.tol.value(),
                     match=self.match.currentText(),
                     want=self.wwant.currentData(),
                     every=self.every.value() * 1000.0,
                     act=self.act.currentData(),
                     human=self.human.isChecked() and self.human.isEnabled(),
                     ms=self.human_ms.value(), ok=ok[0], ok_to=ok[1],
                     **self._scan)
        elif op == "window":
            ok, fail = self.ok_flow.value(), self.fail_flow.value()
            s.update(match=self.match.currentText(),
                     want=self.want.currentData(),
                     wait=self.wait_s.value() * 1000.0,
                     act=self.winact.currentData(), ok=ok[0], ok_to=ok[1],
                     fail=fail[0], fail_to=fail[1])
        elif op == "count":
            ok = self.cok.value()
            s.update(name=self.counter.text(), every=self.cevery.value(),
                     ok=ok[0], ok_to=ok[1])
        elif op == "notify":
            s.update(text=self.msg.text(), url=self.hook.text(),
                     shot=self.shot.isChecked() and self.shot.isEnabled())
        elif op == "open":
            s["url"] = self.link.text()
        return normalize_step(s)


class MacroDialog(QDialog):
    """Um macro inteiro: atalho, modo, repetição e a lista de passos."""

    MODIFIER_BOXES = (("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt"),
                      ("super", "Super"))

    def __init__(self, parent, macro):
        super().__init__(parent)
        self.setWindowTitle(_("Macro"))
        self.resize(500, 560)
        m = normalize_macro(macro)
        self.mid = m["id"]
        self.enabled = m["enabled"]
        self.name = QLineEdit(m["name"])
        self.name.setMaxLength(60)
        self.name.setPlaceholderText(_("optional"))
        code = m["code"]
        self.hotkey = KeyCatcher(code or KEYS["F1"],
                                 hotkey_label(dict(m, mods=[]))
                                 if code else "F1", allow_mouse=True)
        self.mods = {}
        mod_row = QHBoxLayout()
        mod_row.setContentsMargins(0, 0, 0, 0)
        for key, label in self.MODIFIER_BOXES:
            box = QCheckBox(label)
            box.setChecked(key in m["mods"])
            self.mods[key] = box
            mod_row.addWidget(box)
        mod_row.addStretch()
        mod_w = QWidget()
        mod_w.setLayout(mod_row)
        self.block = QCheckBox(_("Hide the hotkey from other apps"))
        self.block.setChecked(m["block"])
        self.block.setToolTip(_(
            "Like AutoHotkey: the game never sees the hotkey itself. Without "
            "this, the key does its normal job too (AutoHotkey's ~ prefix)."))
        self.mode = QComboBox()
        for key in MACRO_MODES:
            self.mode.addItem(_(MACRO_MODE_LABELS[key]), key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(m["mode"])))
        self.repeat = QCheckBox(_("Repeat the steps"))
        self.repeat.setChecked(m["repeat"])
        self.period = QDoubleSpinBox()
        self.period.setRange(0.0, 3600000.0)
        self.period.setDecimals(1)
        self.period.setSuffix(" ms")
        self.period.setSpecialValueText(_("as soon as they end"))
        self.period.setValue(m["period_ms"])
        self.period.setToolTip(_(
            "Counted from the start of one run to the start of the next, like "
            "AutoHotkey's SetTimer. 1000 ms is once a second."))
        self.press = QDoubleSpinBox()
        self.press.setRange(0.0, 10000.0)
        self.press.setDecimals(1)
        self.press.setSuffix(" ms")
        self.press.setSpecialValueText(_("auto"))
        self.press.setValue(m["press_ms"])
        self.press.setToolTip(_(
            "How long each tap stays down. Auto is 40 ms, or half the repeat "
            "time when that is shorter: games read keys once per frame and "
            "miss a tap that is too short."))
        self.limit = QSpinBox()
        self.limit.setRange(0, MAX_RUNS)
        self.limit.setSpecialValueText(_("never"))
        self.limit.setSuffix(" " + _("runs"))
        self.limit.setValue(m["limit"])
        self.limit.setToolTip(_("Stops the macro by itself after this many "
                                "runs, like 20 matches of a farm. A run that "
                                "starts over does not count."))
        self.dock_box = QCheckBox(_("Play in the Dock"))
        self.dock_box.setChecked(bool(m.get("dock")))
        self.dock_box.setToolTip(_(DOCK_BOX_TIP))

        form = QFormLayout()
        form.addRow(QLabel(_("Name:")), self.name)
        self.hk_lbl = QLabel(_("Hotkey:"))
        form.addRow(self.hk_lbl, self.hotkey)
        self.mods_lbl = QLabel(_("With:"))
        form.addRow(self.mods_lbl, mod_w)
        self.mod_w = mod_w
        form.addRow("", self.block)
        form.addRow(QLabel(_("Mode:")), self.mode)
        rep_row = QHBoxLayout()
        rep_row.setContentsMargins(0, 0, 0, 0)
        rep_row.addWidget(self.repeat)
        self.every = QLabel(_("every"))
        rep_row.addWidget(self.every)
        rep_row.addWidget(self.period, 1)
        rep_w = QWidget()
        rep_w.setLayout(rep_row)
        self.rep_lbl = QLabel(_("Repeat:"))
        form.addRow(self.rep_lbl, rep_w)
        self.rep_w = rep_w
        self.limit_lbl = QLabel(_("Stop after:"))
        form.addRow(self.limit_lbl, self.limit)
        form.addRow(QLabel(_("Key press time:")), self.press)
        form.addRow("", self.dock_box)

        self.steps = list(m["steps"])
        self.step_list = QListWidget()
        self.step_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.step_list.itemDoubleClicked.connect(lambda _i: self._edit_step())
        self.step_list.currentRowChanged.connect(self._sync_buttons)
        btns = QHBoxLayout()
        self.b_add = QPushButton(_("+ Add"))
        self.b_rec = QPushButton(_("● Record…"))
        self.b_rec.setToolTip(_("Play, and the keys, clicks and wheel become "
                                "steps"))
        self.b_rec.clicked.connect(self._record)
        self.b_edit = QPushButton(_("Edit"))
        self.b_del = QPushButton(_("Remove"))
        self.b_up = QPushButton("↑")
        self.b_down = QPushButton("↓")
        for b in (self.b_up, self.b_down):
            b.setFixedWidth(34)
        self.b_up.setToolTip(_("Move up"))
        self.b_down.setToolTip(_("Move down"))
        self.b_add.clicked.connect(self._add_step)
        self.b_edit.clicked.connect(self._edit_step)
        self.b_del.clicked.connect(self._del_step)
        self.b_up.clicked.connect(lambda: self._move_step(-1))
        self.b_down.clicked.connect(lambda: self._move_step(1))
        for b in (self.b_add, self.b_rec, self.b_edit, self.b_del):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(self.b_up)
        btns.addWidget(self.b_down)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setEnabled(False)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#d04030;")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)

        col = QVBoxLayout(self)
        col.addLayout(form)
        steps_lbl = QLabel(_("Steps:"))
        col.addWidget(steps_lbl)
        col.addWidget(self.step_list, 1)
        col.addLayout(btns)
        col.addWidget(self.hint)
        col.addWidget(self.error)
        col.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self._sync)
        self.repeat.toggled.connect(self._sync)
        self._fill_steps()
        self._sync()

    # ------------------------------------------------------------- passos --
    def _fill_steps(self, row=None):
        self.step_list.clear()
        for i, s in enumerate(self.steps, 1):
            self.step_list.addItem(f"{i}. {step_label(s)}")
        if self.steps:
            self.step_list.setCurrentRow(
                min(len(self.steps) - 1, row if row is not None else 0))
        self._sync_buttons()

    def _sync_buttons(self, _row=0):
        row = self.step_list.currentRow()
        has = 0 <= row < len(self.steps)
        self.b_edit.setEnabled(has)
        self.b_del.setEnabled(has)
        self.b_up.setEnabled(has and row > 0)
        self.b_down.setEnabled(has and row < len(self.steps) - 1)
        self.b_add.setEnabled(len(self.steps) < MAX_STEPS)
        self.b_rec.setEnabled(len(self.steps) < MAX_STEPS)

    def insert_steps(self, new):
        """Passos novos depois do selecionado (ou no fim), com os saltos
        acompanhando."""
        new = list(new)[:MAX_STEPS - len(self.steps)]
        if not new:
            return
        row = self.step_list.currentRow()
        at = row + 1 if 0 <= row < len(self.steps) else len(self.steps)
        n = len(new)
        self._retarget(lambda k: k + n if k - 1 >= at else k)
        self.steps[at:at] = new
        self._fill_steps(at)

    def target_dock(self):
        """A Doca, quando o macro joga nela (os passos capturam e listam
        janelas de lá), ou None."""
        return DOCK() if self.dock_box.isChecked() else None

    def _record(self):
        dock = self.target_dock()
        if dock is not None and not dock.alive():
            self.error.setText(_("The Dock is not open. Open it on the Dock "
                                 "tab first."))
            return
        self.error.setText("")
        dlg = RecordDialog(self, cursor=dock.cursor if dock else None,
                           dock=dock is not None)
        if dlg.exec() == QDialog.Accepted:
            self.insert_steps(dlg.steps)

    def _add_step(self):
        row = self.step_list.currentRow()
        base = self.steps[row] if 0 <= row < len(self.steps) else None
        dlg = StepDialog(self, base and dict(base) if base and base["op"]
                         in ("tap", "down", "up") else None)
        if dlg.exec() == QDialog.Accepted and dlg.step():
            at = row + 1 if 0 <= row < len(self.steps) else len(self.steps)
            self._retarget(lambda n: n + 1 if n - 1 >= at else n)
            self.steps.insert(at, dlg.step())
            self._fill_steps(at)

    def _edit_step(self):
        row = self.step_list.currentRow()
        if not 0 <= row < len(self.steps):
            return
        dlg = StepDialog(self, self.steps[row])
        if dlg.exec() == QDialog.Accepted and dlg.step():
            self.steps[row] = dlg.step()
            self._fill_steps(row)

    def _del_step(self):
        row = self.step_list.currentRow()
        if 0 <= row < len(self.steps):
            del self.steps[row]
            self._retarget(lambda n: n - 1 if n - 1 > row else n)
            self._fill_steps(max(0, row - 1))

    def _move_step(self, delta):
        row = self.step_list.currentRow()
        to = row + delta
        if 0 <= row < len(self.steps) and 0 <= to < len(self.steps):
            self.steps[row], self.steps[to] = self.steps[to], self.steps[row]
            self._retarget(lambda n: to + 1 if n == row + 1 else
                           row + 1 if n == to + 1 else n)
            self._fill_steps(to)

    def _retarget(self, fn):
        """"Ir ao passo N" acompanha inserir, remover e mover passos."""
        for st in self.steps:
            for key in ("ok_to", "fail_to"):
                if key in st:
                    st[key] = max(1, fn(st[key]))

    # -------------------------------------------------------------- modo --
    HINTS = {"toggle": "Press the hotkey to start, press it again to stop. "
                       "This is what F1:: with a SetTimer does in AutoHotkey.",
             "hold": "Runs while the hotkey is held and stops the moment you "
                     "let go. Without repeat, keys held by the steps stay "
                     "down until you let go, which makes a remap.",
             "once": "Runs the steps once for every press.",
             "always": "Runs on its own whenever hotkey macros are enabled, "
                       "no hotkey needed. Use a long repeat time, like an "
                       "anti idle key."}

    def _sync(self, _x=None):
        mode = self.mode.currentData()
        keyed = mode != "always"
        for w in (self.hk_lbl, self.hotkey, self.mods_lbl, self.mod_w,
                  self.block):
            w.setEnabled(keyed)
        looping = mode != "once"
        self.rep_lbl.setEnabled(looping)
        self.rep_w.setEnabled(looping)
        self.every.setEnabled(looping and self.repeat.isChecked())
        self.period.setEnabled(looping and self.repeat.isChecked())
        self.limit_lbl.setEnabled(looping and self.repeat.isChecked())
        self.limit.setEnabled(looping and self.repeat.isChecked())
        self.hint.setText(_(self.HINTS[mode]))

    def _ok(self):
        m = self.macro()
        if not m["steps"]:
            self.error.setText(_("Add at least one step."))
            return
        if m["mode"] == "always" and m["repeat"] and m["period_ms"] < 10:
            self.error.setText(_("An Always macro needs a repeat time of at "
                                 "least 10 ms."))
            return
        self.accept()

    def macro(self):
        mode = self.mode.currentData()
        keyed = mode != "always"
        code = self.hotkey.code if keyed else 0
        return normalize_macro({
            "id": self.mid, "name": self.name.text().strip(),
            "enabled": self.enabled, "code": code,
            "key": "" if is_button(code) else self.hotkey.name,
            "mods": [k for k, b in self.mods.items() if b.isChecked()]
            if keyed else [],
            "mode": mode, "repeat": self.repeat.isChecked(),
            "period_ms": self.period.value(),
            "press_ms": self.press.value(),
            "limit": self.limit.value() if self.limit.isEnabled() else 0,
            "block": self.block.isChecked(), "steps": self.steps,
            "dock": self.dock_box.isChecked()})


def arm_clash_text(macros, code, name):
    """O aviso de quando um macro ligado, sem modificador, usa a tecla de
    armar o clique (F8 de fábrica, comum em script de jogo)."""
    if not code or not any(m.get("enabled", True) and m.get("code") == code
                           and not m.get("mods") and m.get("mode") != "always"
                           for m in macros):
        return ""
    return _("{key} is also the arm/disarm hotkey. While hotkey macros are "
             "on, {key} runs the macro and does not arm the clicker; pick "
             "another arm/disarm hotkey to use both.", key=name)


def suspend_clash_text(macros, code, mods, arm_code=0):
    """O aviso de quando a tecla do Suspend é também a de um macro ligado
    (ele deixa de rodar por ela) ou a de armar o clique (as duas coisas
    aconteceriam no mesmo aperto)."""
    if not code:
        return ""
    name = hotkey_label({"code": code, "mods": list(mods),
                         "key": key_label(code)})
    out = []
    if any(m.get("enabled", True) and m.get("code") == code
           and set(m.get("mods") or ()) >= set(mods)
           and m.get("mode") != "always" for m in macros):
        out.append(_("{key} is the Suspend hotkey, so the macro with the same "
                     "hotkey does not run by it.", key=name))
    if arm_code and code == arm_code and not mods:
        out.append(_("{key} is also the arm/disarm hotkey, so one press "
                     "would arm the clicker and switch the macros.",
                     key=name))
    return "\n".join(out)


def import_report(imp, source, arm=None):
    """Texto do que um .ahk vira: um macro por linha e as observações. `arm`
    é (código, nome) da tecla de armar o clique, para avisar se bater."""
    n = len(imp.macros)
    name = os.path.basename(source)
    if n == 1:
        lines = [_("1 macro found in {file}:", file=name)]
    else:
        lines = [_("{n} macros found in {file}:", n=n, file=name)]
    for m in imp.macros:
        head = f"{m['name']}  " if m["name"] else ""
        lines.append(f"  • {head}[{macro_summary(m, 6)}]")
    sus = getattr(imp, "suspend", None)
    if sus:
        lines += ["", _("Suspend hotkey: {key} (switches the hotkey macros "
                        "on and off, and the game does not receive it)",
                        key=sus[2])]
    clash = arm_clash_text(imp.macros, *arm) if arm else ""
    if sus:
        more = suspend_clash_text(imp.macros, sus[0], sus[1],
                                  arm[0] if arm else 0)
        clash = "\n  ".join(t for t in (clash, more) if t)
    if imp.notes or clash:
        lines += ["", _("Notes:")]
        lines += ["  " + note_text(x) for x in sorted(imp.notes,
                                                      key=lambda x: x[0])]
        if clash:
            lines.append("  " + clash)
    return "\n".join(lines)


class ImportDialog(QDialog):
    """Mostra o que o script vira antes de acrescentar."""

    def __init__(self, parent, imp, source, room, arm=None):
        super().__init__(parent)
        self.setWindowTitle(_("Import AutoHotkey"))
        self.resize(560, 380)
        text = QPlainTextEdit(import_report(imp, source, arm))
        text.setReadOnly(True)
        col = QVBoxLayout(self)
        col.addWidget(text, 1)
        if len(imp.macros) > room:
            lbl = QLabel(_("Only {n} fit: the list holds {max} macros.",
                           n=room, max=MAX_MACROS))
            lbl.setStyleSheet("color:#c86000;")
            col.addWidget(lbl)
        buttons = QDialogButtonBox()
        self.add = buttons.addButton(_("Add these macros"),
                                     QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        self.add.setEnabled((bool(imp.macros) and room > 0)
                            or bool(getattr(imp, "suspend", None)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)


def window_names(combo, error, dock=None):
    """Põe na lista do combo as janelas abertas agora (app e título), sem
    perder o que já estava escrito. error: QLabel para quando não dá. Com
    dock, as janelas de dentro da Doca."""
    if dock is not None:
        if not dock.alive() or dock.windows is None:
            error.setText(_("The Dock is not open. Open it on the Dock tab "
                            "first."))
            return False
        kw = dock.windows
    else:
        kw = KWIN()
    wins = kw.windows() if kw.ok else []
    if not wins:
        error.setText(kw.error or _("No window list: this needs KDE Plasma."))
        return False
    error.setText("")
    keep = combo.currentText()
    combo.clear()
    names = []
    for w in wins:
        for name in (w.get("cls"), w.get("title")):
            if name and name not in names:
                names.append(name)
    combo.addItems(names)
    combo.setEditText(keep)
    combo.showPopup()
    return True


class RecordDialog(QDialog):
    """Gravar passos: conta até começar (tempo de ir para o jogo), anota
    teclas, cliques e a roda até a tecla de parar, e mostra no que virou
    antes de acrescentar."""
    TICK_MS = 250

    def __init__(self, parent=None, hub=None, cursor=None, dock=False):
        super().__init__(parent)
        self.setWindowTitle(_("Record steps"))
        self.resize(460, 480)
        self.hub = hub
        self.cursor = cursor            # None: pergunta ao KWin na hora
        self.dock = dock                # grava o que se joga dentro da Doca
        self.events, self.steps = [], []
        self.rec = None
        self._left = 0
        self.delay = QSpinBox()
        self.delay.setRange(0, 10)
        self.delay.setSuffix(" s")
        self.delay.setValue(3)
        self.delay.setToolTip(_("Time to switch to the game window before "
                                "recording starts."))
        self.stop_key = KeyCatcher(KEYS["F10"], "F10")
        self.stop_key.setToolTip(_("Pressing it stops the recording. It is "
                                   "not recorded and the game does not see "
                                   "it."))
        self.pauses = QCheckBox(_("Keep the pauses between actions"))
        self.pauses.setChecked(True)
        self.pauses.setToolTip(_("Off, the steps run one right after the "
                                 "other."))
        self.pauses.toggled.connect(self._show)
        self.b_go = QPushButton(_("Start recording"))
        self.b_go.clicked.connect(self._go)
        self.state = QLabel(_("Mouse movement without a click is not "
                              "recorded. Clicks keep where they happened."))
        self.state.setWordWrap(True)
        self.preview = QListWidget()
        self.error = QLabel("")
        self.error.setStyleSheet("color:#d04030;")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox()
        self.add = buttons.addButton(_("Add these steps"),
                                     QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        self.add.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(QLabel(_("Start after:")), self.delay)
        form.addRow(QLabel(_("Stop key:")), self.stop_key)
        form.addRow("", self.pauses)
        col = QVBoxLayout(self)
        col.addLayout(form)
        col.addWidget(self.b_go)
        col.addWidget(self.state)
        col.addWidget(self.preview, 1)
        col.addWidget(self.error)
        col.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def _busy(self, on):
        for w in (self.delay, self.stop_key, self.add):
            w.setEnabled(not on)

    def _go(self):
        if self.rec is not None:
            self._finish(by_button=True)
            return
        if self._left:
            self._left = 0                       # desistiu na contagem
            self.timer.stop()
            self._busy(False)
            self.add.setEnabled(bool(self.steps))
            self.b_go.setText(_("Start recording"))
            self.state.setText("")
            return
        code = self.stop_key.code
        if not code or is_button(code):
            self.error.setText(_("The stop key must be a keyboard key."))
            return
        self.error.setText("")
        self._busy(True)
        self.b_go.setText(_("Cancel"))
        self._left = self.delay.value()
        if self._left:
            self._say_left()
            self.timer.start(1000)
        else:
            self._begin()

    def _say_left(self):
        if self.dock:
            self.state.setText(_("Recording in {n} s, play inside the Dock "
                                 "window…", n=self._left))
            return
        self.state.setText(_("Recording in {n} s, switch to the game "
                             "window…", n=self._left))

    def _tick(self):
        if self.rec is not None:
            self.state.setText(_("Recording, {n} actions so far. Press {key} "
                                 "to stop.", n=len(self.rec.events),
                                 key=self.stop_key.name))
            return
        self._left -= 1
        if self._left > 0:
            self._say_left()
            return
        self.timer.stop()
        self._begin()

    def _where(self):
        """Quem diz onde o cursor está: o KWin, se ele responder."""
        if self.cursor is not None:
            return self.cursor, ""
        kw = KWIN()
        if kw.ok and kw.cursor() is not None:
            return kw.cursor, ""
        return None, _("Positions are not available here (they come from "
                       "KDE Plasma): clicks are recorded where the pointer "
                       "happens to be when the macro runs.")

    def _begin(self):
        self._left = 0
        cursor, note = self._where()
        self.rec = MacroRecorder(self.hub, self.stop_key.code, cursor)
        self.rec.finished.connect(self._finish)
        self.rec.start()
        self.b_go.setText(_("Stop recording"))
        self.error.setText(note)
        self._tick()
        self.timer.start(self.TICK_MS)

    def _finish(self, by_button=False):
        if self.rec is None:
            return
        self.timer.stop()
        events = self.rec.stop()
        self.rec = None
        if by_button:
            # o clique no botão Parar foi anotado também: sai, com o que veio
            # depois dele
            cut = next((i for i in range(len(events) - 1, -1, -1)
                        if events[i][1] == "key" and events[i][2]
                        == BTN["Left"] and events[i][3] == 1), None)
            if cut is not None:
                events = events[:cut]
        self.events = events
        self._busy(False)
        self.b_go.setText(_("Record again"))
        self._show()
        self.raise_()
        self.activateWindow()

    def _show(self, _on=False):
        self.steps = recorded_steps(self.events, self.pauses.isChecked())
        self.preview.clear()
        for i, st in enumerate(self.steps, 1):
            self.preview.addItem(f"{i}. {step_label(st)}")
        if self.rec is None and not self._left:
            self.state.setText(_("{n} steps.", n=len(self.steps))
                               if self.events else _("Nothing was recorded."))
            self.add.setEnabled(bool(self.steps))

    def done(self, r):
        self.timer.stop()
        if self.rec is not None:                 # fechou gravando
            self.rec.stop()
            self.rec = None
        super().done(r)


class ImageField(QWidget):
    """Uma imagem de botão do jogo: miniatura, capturar da tela, abrir
    arquivo e tirar."""
    changed = Signal()

    def __init__(self, path="", grab=None, error=None, optional=True,
                 remove=None):
        super().__init__()
        self.path = path or ""
        self._grab = grab               # () -> quadros (ou [] com o erro dito)
        self._error = error
        self._remove = remove           # ✕ tira a linha inteira, não a imagem
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(120, 40)
        self.view.setFrameShape(QFrame.StyledPanel)
        self.b_cap = QPushButton(_("Capture…"))
        self.b_cap.setToolTip(_("Capture from screen…"))
        self.b_cap.clicked.connect(self._capture)
        self.b_open = QPushButton(_("Open…"))
        self.b_open.setToolTip(_("Open file…"))
        self.b_open.clicked.connect(self._open)
        self.b_clear = QPushButton("✕")
        self.b_clear.setFixedWidth(30)
        self.b_clear.setToolTip(_("Remove this button") if remove else
                                _("Remove the image"))
        self.b_clear.clicked.connect(remove or (lambda: self.set_path("")))
        self.b_clear.setVisible(optional or remove is not None)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.view, 1)
        for b in (self.b_cap, self.b_open, self.b_clear):
            row.addWidget(b)
        self._show()

    def set_path(self, path):
        self.path = path or ""
        self._show()
        self.changed.emit()

    def retranslate(self):
        self.b_cap.setText(_("Capture…"))
        self.b_cap.setToolTip(_("Capture from screen…"))
        self.b_open.setText(_("Open…"))
        self.b_open.setToolTip(_("Open file…"))
        self.b_clear.setToolTip(_("Remove this button") if self._remove else
                                _("Remove the image"))
        self._show()

    def _show(self):
        image_thumb(self.view, self.path, box=(160, 48), empty=_("none"))
        self.b_clear.setEnabled(bool(self.path) or self._remove is not None)

    def _say(self, text):
        if self._error is not None:
            self._error.setText(text)

    def _open(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _("Image"), images_dir() if os.path.isdir(images_dir())
            else os.path.expanduser("~"),
            _("Images") + " (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        if QImage(path).isNull():
            self._say(_("That file is not an image."))
            return
        self._say("")
        self.set_path(path)

    def _capture(self):
        self._say("")
        frames = self._grab() if self._grab else []
        if not frames:
            return
        dlg = ScreenPicker(self.window(), frames, "area")
        if dlg.exec() != QDialog.Accepted or dlg.crop is None:
            return
        path = save_crop(dlg.crop)
        if path is None:
            self._say(_("Could not save {path}.", path=images_dir()))
            return
        self.set_path(path)


class FarmDialog(QDialog):
    """O assistente de farm: pergunta o jogo, os botões do lobby, a
    estratégia e o fim da partida, e monta um macro comum com farm_macro."""
    MAX_LOBBY = 8

    def __init__(self, parent=None, macros=(), spec=None):
        super().__init__(parent)
        self.setWindowTitle(_("Farm assistant"))
        self.resize(620, 760)
        spec = dict(spec or {})
        self.strategies = [m for m in macros if m.get("steps")]
        self.error = QLabel("")
        self.error.setStyleSheet("color:#d04030;")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cap_note = QLabel("")
        self.delay = QSpinBox()
        self.delay.setRange(0, 10)
        self.delay.setSuffix(" s")
        self.delay.setValue(3)
        self.delay.setToolTip(_("Time to switch to the game window before the "
                                "screen is captured."))
        intro = QLabel(_(
            "Builds an ordinary macro that farms matches by looking at the "
            "screen, like a person: it brings the game to the front, clicks "
            "the lobby buttons in order, plays your strategy, keeps watching "
            "for the skip, reconnect and end of match buttons, and starts "
            "again. Capture each button from the screen while the game "
            "shows it. The macro can be edited later like any other."))
        intro.setWordWrap(True)
        intro.setEnabled(False)

        self.name = QLineEdit(spec.get("name") or _("Farm"))
        self.name.setMaxLength(60)
        self.hotkey = KeyCatcher(spec.get("code") or KEYS["F6"],
                                 spec.get("key") or "F6", allow_mouse=False)
        self.window_name = QComboBox()
        self.window_name.setEditable(True)
        self.window_name.setInsertPolicy(QComboBox.NoInsert)
        self.window_name.setMinimumContentsLength(18)
        self.window_name.setEditText(spec.get("window") or "Sober")
        self.window_name.lineEdit().setMaxLength(100)
        self.window_name.setToolTip(_(
            "The app name, like Sober, or the whole title of the game "
            "window. Open windows… lists both."))
        b_wins = QPushButton(_("Open windows…"))
        b_wins.setToolTip(_("List the windows that are open now"))
        b_wins.clicked.connect(lambda: window_names(
            self.window_name, self.error, self.target_dock()))
        self.dock_box = QCheckBox(_("Play in the Dock"))
        self.dock_box.setChecked(bool(spec.get("dock")))
        self.dock_box.setToolTip(_(DOCK_BOX_TIP))
        self.link = QLineEdit(spec.get("link") or "")
        self.link.setPlaceholderText("https://www.roblox.com/games/…")
        self.link.setToolTip(_(
            "Optional. When the game window closes, the macro opens this "
            "link and waits for the game again. A Roblox game page becomes "
            "roblox://placeId=…, the link Roblox documents for opening a "
            "game in the app."))

        self.lobby_box = QVBoxLayout()
        self.lobby_box.setContentsMargins(0, 0, 0, 0)
        self.lobby = []
        self.b_lobby = QPushButton(_("+ Add a lobby button"))
        self.b_lobby.clicked.connect(lambda: self._add_lobby(""))
        lobby_w = QWidget()
        lobby_col = QVBoxLayout(lobby_w)
        lobby_col.setContentsMargins(0, 0, 0, 0)
        lobby_col.addLayout(self.lobby_box)
        lobby_col.addWidget(self.b_lobby, 0, Qt.AlignLeft)
        for path in spec.get("lobby") or ():
            self._add_lobby(path)
        self.lobby_wait = QSpinBox()
        self.lobby_wait.setRange(1, 3600)
        self.lobby_wait.setSuffix(" s")
        self.lobby_wait.setValue(int(spec.get("lobby_wait") or 60))
        self.lobby_wait.setToolTip(_(
            "How long to wait for each lobby button. When the first one does "
            "not show up, the macro takes it that a match already started "
            "and goes on to the strategy."))

        self.strategy = QComboBox()
        self.strategy.addItem(_("None, only farm"), None)
        for m in self.strategies:
            self.strategy.addItem(f"{m['name'] or hotkey_label(m)}  "
                                  f"({len(m['steps'])})", m["id"])
        self.strategy.setCurrentIndex(max(0, self.strategy.findData(
            spec.get("strategy_id"))))
        self.strategy.setToolTip(_(
            "A macro you made or recorded that places and upgrades the "
            "towers. Its steps are copied into the farm, so later changes "
            "to it do not change the farm."))
        self.skip = self._image(spec.get("skip"))
        self.skip.setToolTip(_("Optional. Clicked whenever it shows up, like "
                               "a skip wave button."))
        self.reconnect = self._image(spec.get("reconnect"))
        self.reconnect.setToolTip(_("Optional. Clicked whenever it shows up, "
                                    "and the farm starts over from the "
                                    "lobby."))
        self.over = self._image(spec.get("over"), optional=False)
        self.over.setToolTip(_("A button that only shows when the match is "
                               "over, like Play again or Return to lobby. "
                               "The macro clicks it and starts the next "
                               "match."))
        self.over_wait = QSpinBox()
        self.over_wait.setRange(1, 600)
        self.over_wait.setSuffix(" min")
        self.over_wait.setValue(int(spec.get("over_wait") or 30))
        self.over_wait.setToolTip(_("If the end of the match takes longer "
                                    "than this, the farm starts over."))
        self.hook = QLineEdit(spec.get("hook") or "")
        self.hook.setPlaceholderText(_("optional") + ", "
                                     "https://discord.com/api/webhooks/…")
        self.report = QLineEdit(spec.get("report")
                                or _("Match {run} done, farming for {time}."))
        self.report.setMaxLength(1000)
        self.report.setToolTip(_("{run} becomes the number of the match and "
                                 "{time} how long the farm has been "
                                 "running."))
        self.shot = QCheckBox(_("Attach a screenshot"))
        self.shot.setChecked(bool(spec.get("shot", True)))
        self.hook.textChanged.connect(self._sync)
        self.limit = QSpinBox()
        self.limit.setRange(0, MAX_RUNS)
        self.limit.setSpecialValueText(_("never"))
        self.limit.setSuffix(" " + _("matches"))
        self.limit.setValue(int(spec.get("limit") or 0))

        form = QFormLayout()
        form.addRow(QLabel(_("Name:")), self.name)
        form.addRow(QLabel(_("Hotkey:")), self.hotkey)
        form.addRow(QLabel(_("Game window:")), _hrow(self.window_name,
                                                     b_wins))
        form.addRow("", self.dock_box)
        form.addRow(QLabel(_("Reopen with:")), self.link)
        form.addRow(QLabel(_("Lobby buttons:")), lobby_w)
        form.addRow(QLabel(_("Wait for each:")), self.lobby_wait)
        form.addRow(QLabel(_("Strategy:")), self.strategy)
        form.addRow(QLabel(_("Skip button:")), self.skip)
        form.addRow(QLabel(_("Reconnect button:")), self.reconnect)
        form.addRow(QLabel(_("End of the match:")), self.over)
        form.addRow(QLabel(_("Longest match:")), self.over_wait)
        form.addRow(QLabel(_("Discord webhook:")), self.hook)
        form.addRow(QLabel(_("Report:")), self.report)
        form.addRow("", self.shot)
        form.addRow(QLabel(_("Stop after:")), self.limit)
        form.addRow(QLabel(_("Capture after:")), _hrow(self.delay,
                                                       self.cap_note))
        inner = QWidget()
        inner_col = QVBoxLayout(inner)
        inner_col.addWidget(intro)
        inner_col.addLayout(form)
        inner_col.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(inner)
        self.buttons = QDialogButtonBox()
        self.make = self.buttons.addButton(_("Create the macro"),
                                           QDialogButtonBox.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        col = QVBoxLayout(self)
        col.addWidget(self.scroll, 1)
        col.addWidget(self.error)
        col.addWidget(self.buttons)
        if not self.lobby:
            self._add_lobby("")
        self._sync()

    def target_dock(self):
        return DOCK() if self.dock_box.isChecked() else None

    def _grab(self):
        self.scroll.setEnabled(False)
        self.buttons.setEnabled(False)
        try:
            frames, err = grab_frames(self.delay.value(), self.cap_note,
                                      self.target_dock())
        finally:
            self.scroll.setEnabled(True)
            self.buttons.setEnabled(True)
        if not frames:
            self.error.setText(err)
        return frames

    def _image(self, path, optional=True):
        return ImageField(path or "", self._grab, self.error, optional)

    def _add_lobby(self, path):
        if len(self.lobby) >= self.MAX_LOBBY:
            return
        box = []
        field = ImageField(path or "", self._grab, self.error,
                           remove=lambda: self._del_lobby(box[0]))
        box.append(field)
        self.lobby.append(field)
        self.lobby_box.addWidget(field)
        self.b_lobby.setEnabled(len(self.lobby) < self.MAX_LOBBY)

    def _del_lobby(self, field):
        if field in self.lobby:
            self.lobby.remove(field)
            self.lobby_box.removeWidget(field)
            field.deleteLater()
        self.b_lobby.setEnabled(len(self.lobby) < self.MAX_LOBBY)

    def _sync(self, _t=""):
        self.shot.setEnabled(bool(self.hook.text().strip()))

    def spec(self):
        sid = self.strategy.currentData()
        strat = next((m for m in self.strategies if m["id"] == sid), None)
        hook = self.hook.text().strip()
        return {"name": self.name.text().strip(), "code": self.hotkey.code,
                "key": self.hotkey.name,
                "window": " ".join(self.window_name.currentText().split()),
                "link": self.link.text().strip(),
                "lobby": [f.path for f in self.lobby if f.path],
                "lobby_wait": self.lobby_wait.value(),
                "strategy_id": sid,
                "strategy": strat["steps"] if strat else [],
                "press_ms": strat["press_ms"] if strat else 0.0,
                "skip": self.skip.path, "reconnect": self.reconnect.path,
                "over": self.over.path, "over_wait": self.over_wait.value(),
                "hook": hook, "report": self.report.text(),
                "shot": self.shot.isChecked() and bool(hook),
                "limit": self.limit.value(),
                "dock": self.dock_box.isChecked()}

    def _ok(self):
        s = self.spec()
        if not s["window"]:
            self.error.setText(_("Type the name of the game window, like "
                                 "Sober."))
            return
        if not s["over"]:
            self.error.setText(_("Capture the button that shows at the end "
                                 "of a match: the farm needs it to know "
                                 "when a match is over."))
            return
        link = farm_link(s["link"])
        if link and not link.lower().startswith(SAFE_LINKS):
            self.error.setText(_("The link must start with https://, "
                                 "roblox:// or steam://."))
            return
        if s["hook"] and not s["hook"].lower().startswith("https://"):
            self.error.setText(_("The webhook must start with https://."))
            return
        try:
            self.macro()
        except ValueError:
            self.error.setText(_("The strategy is too long to fit in a farm: "
                                 "a macro holds {n} steps.", n=MAX_STEPS))
            return
        self.accept()

    def macro(self):
        return farm_macro(self.spec())


class MacroFileDialog(QDialog):
    """O que um arquivo .wayclick traz, antes de acrescentar."""

    def __init__(self, parent, macros, notes, source, room):
        super().__init__(parent)
        self.setWindowTitle(_("Import macros"))
        self.resize(560, 380)
        name = os.path.basename(source)
        n = len(macros)
        lines = [_("1 macro found in {file}:", file=name) if n == 1 else
                 _("{n} macros found in {file}:", n=n, file=name)]
        for m in macros:
            head = f"{m['name']}  " if m["name"] else ""
            lines.append(f"  • {head}[{macro_summary(m, 6)}]")
        if notes:
            lines += ["", _("Notes:")] + ["  " + x for x in notes]
        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        col = QVBoxLayout(self)
        col.addWidget(text, 1)
        if n > room:
            lbl = QLabel(_("Only {n} fit: the list holds {max} macros.",
                           n=room, max=MAX_MACROS))
            lbl.setStyleSheet("color:#c86000;")
            col.addWidget(lbl)
        buttons = QDialogButtonBox()
        self.add = buttons.addButton(_("Add these macros"),
                                     QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        self.add.setEnabled(bool(macros) and room > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)


# a janela com a aba Tower Defense aberta: o jogo, a lista e o registro lado
# a lado (até onde a tela deixa)
TOWER_WINDOW = (1600, 900)


class DockView(QWidget):
    """A tela da Doca dentro do WayClick, como o NoName Macro encaixa o
    Roblox na janela dele: uma captura muitas vezes por segundo, e o que se
    faz em cima dela (mouse, roda e teclas) vai para o jogo pelo fake_input
    da Doca, como se a janela dele estivesse aqui. No modo de escolher, o
    clique vira uma posição (o lugar de uma torre) e não chega ao jogo."""
    picked = Signal(int, int, int)       # x, y na Doca, botão
    # ("key", código, 0, 0), ("click", botão, x, y) e ("hotkey", código, 0,
    # 0) para as teclas que não vão para o jogo
    acted = Signal(str, int, int, int)
    escaped = Signal()
    _framed = Signal()
    FPS = 20

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.image = QImage()
        self.markers = []            # (x, y, texto, cor)
        self.pick = False
        self.empty_text = ""
        self.skip_keys = set()       # não vão para o jogo (atalhos do farm)
        self.fps = self.FPS
        self._keys, self._buttons = set(), set()
        self._rel = False            # direito ou meio segurado: relativo
        self._last = None            # último ponto (Doca, float)
        self._target = QRectF()
        self._lock = threading.Lock()
        self._fresh = None
        self._halt = threading.Event()
        self._thread = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._framed.connect(self._take, Qt.QueuedConnection)

    # ---------------------------------------------------------- captura --
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running():
            return
        self._halt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="wayclick-dock-view")
        self._thread.start()

    def stop(self):
        self._halt.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=1.0)
        self.release_all()

    def _loop(self):
        while not self._halt.is_set():
            t = time.monotonic()
            img = self.dock.screen.qimage() if self.dock.alive() else QImage()
            with self._lock:
                self._fresh = img
            self._framed.emit()
            self._halt.wait(max(0.005, 1.0 / max(1, self.fps)
                                - (time.monotonic() - t)))

    def _take(self):
        with self._lock:
            img, self._fresh = self._fresh, None
        if img is not None:
            self.image = img
            self.update()

    # --------------------------------------------------------- desenho --
    def dock_size(self):
        s = self.dock.out_size or self.dock.size
        if s:
            return int(s[0]), int(s[1])
        return self.image.width(), self.image.height()

    def _fit(self):
        lw, lh = self.dock_size()
        if lw <= 0 or lh <= 0:
            return QRectF()
        W, H = self.width(), self.height()
        k = min(W / lw, H / lh)
        return QRectF((W - lw * k) / 2.0, (H - lh * k) / 2.0, lw * k, lh * k)

    def to_dock(self, pos):
        """Ponto da visão -> ponto da Doca (float), ou None fora do jogo."""
        t = self._fit()
        if t.isEmpty():
            return None
        lw, lh = self.dock_size()
        x = (pos.x() - t.x()) * lw / t.width()
        y = (pos.y() - t.y()) * lh / t.height()
        if x < 0 or y < 0 or x >= lw or y >= lh:
            return None
        return (x, y)

    def to_view(self, x, y):
        t = self._fit()
        lw, lh = self.dock_size()
        return QPointF(t.x() + (x + 0.5) * t.width() / max(1, lw),
                       t.y() + (y + 0.5) * t.height() / max(1, lh))

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 20))
        t = self._fit()
        if self.image.isNull() or t.isEmpty() or not self.dock.alive():
            text = self.empty_text
            err = self.dock.screen.error if self.dock.alive() else ""
            if err and self.dock.screen.state == "failed":
                text = err
            p.setPen(QColor(170, 170, 170))
            p.drawText(self.rect().adjusted(20, 20, -20, -20),
                       Qt.AlignCenter | Qt.TextWordWrap, text)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(t, self.image)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = p.font()
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        for x, y, text, color in self.markers:
            c, col = self.to_view(x, y), QColor(color)
            p.setPen(QPen(QColor(0, 0, 0, 200), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 9, 9)
            p.setPen(QPen(col, 2))
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 80))
            p.drawEllipse(c, 9, 9)
            box = QRectF(c.x() - 18, c.y() - 27, 36, 15)
            p.setPen(QColor(0, 0, 0, 220))
            p.drawText(box.translated(1, 1), Qt.AlignCenter, text)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignCenter, text)
        if self.pick or self.hasFocus():
            p.setPen(QPen(QColor("#3daee9" if self.pick else "#27ae60"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(t.adjusted(1, 1, -1, -1))

    # ------------------------------------------------- para dentro do jogo --
    def _send(self, how, *args):
        inp = self.dock.input
        if inp is None or not self.dock.alive():
            return False
        try:
            getattr(inp, how)(*args)
        except OSError:
            return False
        return True

    def event(self, e):
        # com a visão em foco, toda tecla é do jogo: nem o atalho de uma
        # janela do WayClick (Ctrl+Q, o de armar) rouba ela
        if e.type() == QEvent.ShortcutOverride and not self.pick:
            e.accept()
            return True
        return super().event(e)

    def focusNextPrevChild(self, nxt):
        return False                 # Tab vai para o jogo

    def mouseMoveEvent(self, e):
        at = self.to_dock(e.position())
        if self.pick or at is None:
            return
        if self._rel and self._last is not None:
            dx, dy = at[0] - self._last[0], at[1] - self._last[1]
            if dx or dy:
                # o jogo pode ter travado o ponteiro (câmera do Roblox com o
                # direito): movimento relativo, que ele vê mesmo travado
                self._send("move", dx, dy)
        else:
            self._send("absolute", int(at[0]) + 0.5, int(at[1]) + 0.5)
        self._last = at

    def mousePressEvent(self, e):
        at = self.to_dock(e.position())
        code = QT_BUTTONS.get(e.button())
        self.setFocus(Qt.MouseFocusReason)
        if at is None or code is None:
            return
        if self.pick:
            self.picked.emit(int(at[0]), int(at[1]), code)
            return
        self._send("absolute", int(at[0]) + 0.5, int(at[1]) + 0.5)
        self._last = at
        if self._send("button", code, True):
            self._buttons.add(code)
            if code in (BTN["Right"], BTN["Middle"]):
                self._rel = True
            self.acted.emit("click", code, int(at[0]), int(at[1]))

    def mouseReleaseEvent(self, e):
        code = QT_BUTTONS.get(e.button())
        if code is None or code not in self._buttons:
            return
        self._buttons.discard(code)
        self._send("button", code, False)
        if self._rel and not self._buttons & {BTN["Right"], BTN["Middle"]}:
            self._rel = False
            at = self.to_dock(e.position())
            if at is not None:
                self._send("absolute", int(at[0]) + 0.5, int(at[1]) + 0.5)
                self._last = at

    def wheelEvent(self, e):
        e.accept()
        if self.pick:
            return
        d = e.angleDelta()
        # um dente (120) é DockRunner.WHEEL_STEP; para cima é negativo
        k = DockRunner.WHEEL_STEP / 120.0
        if d.y():
            self._send("axis", 0, -d.y() * k)
        if d.x():
            self._send("axis", 1, -d.x() * k)

    @staticmethod
    def _code(e):
        n = e.nativeScanCode()
        return n - 8 if n > 8 else None

    def keyPressEvent(self, e):
        code = self._code(e)
        if code is None:
            return super().keyPressEvent(e)
        if self.pick:
            if code == KEYS["Esc"]:
                self.escaped.emit()
            return
        if e.isAutoRepeat():
            return
        if code in self.skip_keys:
            self.acted.emit("hotkey", code, 0, 0)
            return
        if self._send("key", code, True):
            self._keys.add(code)
            self.acted.emit("key", code, 0, 0)

    def keyReleaseEvent(self, e):
        code = self._code(e)
        if code is None or e.isAutoRepeat() or code not in self._keys:
            return
        self._keys.discard(code)
        self._send("key", code, False)

    def focusOutEvent(self, e):
        self.release_all()
        self.update()
        super().focusOutEvent(e)

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def release_all(self):
        """Nada fica apertado no jogo quando a visão perde o foco."""
        for code in list(self._keys):
            self._send("key", code, False)
        for code in list(self._buttons):
            self._send("button", code, False)
        self._keys.clear()
        self._buttons.clear()
        self._rel = False


class TowerActionDialog(QDialog):
    """Uma ação da linha do tempo, à mão: a hora e o que fazer, em qual
    torre e lugar, ou a tecla, ou a imagem."""
    KINDS = (("place", "Place a tower"), ("top", "Upgrade, top path"),
             ("bottom", "Upgrade, bottom path"), ("sell", "Sell a tower"),
             ("key", "Press a key"), ("click", "Click a picture"),
             ("wait", "Wait for a picture"))

    def __init__(self, parent, strategy, action=None, grab=None):
        super().__init__(parent)
        self.setWindowTitle(_("Action"))
        self.s = strategy
        a = dict(action or {"t": 0.0, "do": "place", "tower": 0, "point": 0})
        self.error = QLabel("")
        self.error.setStyleSheet("color:#d04030;")
        self.error.setWordWrap(True)
        self.t = QDoubleSpinBox()
        self.t.setRange(0.0, 86400.0)
        self.t.setDecimals(2)          # como a gravação anota
        self.t.setSingleStep(1.0)
        self.t.setSuffix(" s")
        self.t.setValue(float(a.get("t", 0.0)))
        self.t.setToolTip(_("Seconds from the start of the match."))
        self.kind = QComboBox()
        for key, label in self.KINDS:
            self.kind.addItem(_(label), key)
        kind = a["do"]
        if kind == "upgrade":
            kind = a.get("path", "top")
        elif kind == "image":
            kind = "click" if a.get("click", True) else "wait"
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.tower = QComboBox()
        for k in range(TOWER_SLOTS):
            self.tower.addItem(tower_name(strategy, k), k)
        self.tower.setCurrentIndex(max(0, min(TOWER_SLOTS - 1,
                                              int(a.get("tower", 0)))))
        self.spot = QComboBox()
        self._spots(a.get("point", 0))
        self.tower.currentIndexChanged.connect(lambda _i: self._spots(0))
        self.key = KeyCatcher(a.get("code") or KEYS["E"],
                              a.get("name") or "E")
        # "path" é o caminho da melhoria (top/bottom) numa ação de melhorar,
        # e só é o arquivo da imagem numa ação de imagem
        self.image = ImageField(a.get("path") or "" if a["do"] == "image"
                                else "", grab, self.error, optional=False)
        self.wait = QDoubleSpinBox()
        self.wait.setRange(0.0, 3600.0)
        self.wait.setDecimals(1)
        self.wait.setSuffix(" s")
        self.wait.setValue(float(a.get("wait", 10.0)))
        self.wait.setToolTip(_("How long to wait for the picture before going "
                               "on."))
        self.form = QFormLayout()
        self.rows = {}
        for key, text, w in (("t", "At:", self.t), ("kind", "Do:", self.kind),
                             ("tower", "Tower:", self.tower),
                             ("spot", "Spot:", self.spot),
                             ("key", "Key:", self.key),
                             ("image", "Picture:", self.image),
                             ("wait", "Wait up to:", self.wait)):
            lbl = QLabel(_(text))
            self.form.addRow(lbl, w)
            self.rows[key] = (lbl, w)
        self.kind.currentIndexChanged.connect(self._sync)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        col = QVBoxLayout(self)
        col.addLayout(self.form)
        col.addWidget(self.error)
        col.addWidget(buttons)
        self._sync()

    def _spots(self, keep=0):
        k = self.tower.currentData() or 0
        self.spot.clear()
        for j, (x, y) in enumerate(self.s["towers"][k]["points"]):
            self.spot.addItem(_("spot {n} ({x}, {y})", n=j + 1, x=x, y=y), j)
        self.spot.setCurrentIndex(max(0, min(self.spot.count() - 1,
                                             int(keep or 0))))

    def _sync(self, _i=0):
        kind = self.kind.currentData()
        show = {"tower": kind in ("place", "top", "bottom", "sell"),
                "spot": kind in ("place", "top", "bottom", "sell"),
                "key": kind == "key", "image": kind in ("click", "wait"),
                "wait": kind in ("click", "wait")}
        for key, on in show.items():
            for w in self.rows[key]:
                w.setVisible(on)

    def action(self):
        kind = self.kind.currentData()
        a = {"t": round(self.t.value(), 2)}
        if kind in ("place", "top", "bottom", "sell"):
            a.update(do="upgrade" if kind in ("top", "bottom") else kind,
                     tower=self.tower.currentData(),
                     point=self.spot.currentData())
            if kind in ("top", "bottom"):
                a["path"] = kind
        elif kind == "key":
            a.update(do="key", code=self.key.code, name=self.key.name)
        else:
            a.update(do="image", path=self.image.path, click=kind == "click",
                     wait=round(self.wait.value(), 1))
        return _tower_action(a, self.s["towers"])

    def _ok(self):
        kind = self.kind.currentData()
        if kind in ("place", "top", "bottom", "sell") \
                and self.spot.currentData() is None:
            self.error.setText(_("This tower has no spots yet: pick them on "
                                 "the game with Pick."))
            return
        if kind == "key" and (not self.key.code or is_button(self.key.code)):
            self.error.setText(_("Press a key of the keyboard."))
            return
        if kind in ("click", "wait") and not self.image.path:
            self.error.setText(_("Capture the picture first."))
            return
        if self.action() is None:
            self.error.setText(_("This action is not complete."))
            return
        self.accept()


class TowerTab(QWidget):
    """A aba Tower Defense: o jogo da Doca dentro da janela, as estratégias
    (as 5 torres com os lugares, a linha do tempo, gravar jogando), as telas
    do jogo e os ajustes, com F4 começando e F5 parando o farm."""
    changed = Signal()                 # algo para gravar na configuração
    running_changed = Signal()
    _done = Signal(object)
    _cam_done = Signal(object)

    def __init__(self, cfg, dock, macros=lambda: [], claims=None):
        super().__init__()
        self.dock, self.macros, self.claims = dock, macros, claims
        self.c = normalize_tower(cfg)
        self.runner = self.cam = self.rec = self.env = None
        self._cam_then = None
        self.began = None
        self.picking = None
        self.watchers = {}
        self.lobby_fields, self.after_fields = [], []
        self._loading = False
        self._labels, self._tips, self._specials = [], [], []
        self._done.connect(self._finished, Qt.QueuedConnection)
        self._cam_done.connect(self._cam_finished, Qt.QueuedConnection)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#d04030;")
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # ------------------------------------------------------ esquerda --
        self.b_start = QPushButton()
        self.b_start.setMinimumHeight(40)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton()
        self.b_stop.clicked.connect(self.stop)
        self.b_pause = QPushButton()
        self.b_pause.clicked.connect(self.pause)
        self.phase = QLabel("")
        self.phase.setWordWrap(True)
        self.stats = {k: QLabel("0") for k in ("time", "matches", "wins",
                                               "losses")}
        self.stats["time"].setText("0:00")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        for row, (key, text) in enumerate((("time", "Running:"),
                                           ("matches", "Matches:"),
                                           ("wins", "Wins:"),
                                           ("losses", "Losses:"))):
            grid.addWidget(self._label(text), row, 0)
            grid.addWidget(self.stats[key], row, 1)
        grid.setColumnStretch(1, 1)
        self.b_dock = QPushButton()
        self.b_dock.clicked.connect(self._dock_toggle)
        self.b_in = QPushButton()
        self.b_in.clicked.connect(self._dock_in)
        self.b_out = QPushButton()
        self.b_out.clicked.connect(self._dock_out)
        self.b_join = QPushButton()
        self.b_join.clicked.connect(self._join)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.b_log = QPushButton()
        self.b_log.clicked.connect(self.log.clear)
        left_w = QWidget()
        left_w.setFixedWidth(250)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.b_start)
        left.addWidget(_hrow(self.b_stop, self.b_pause, stretch=False))
        left.addWidget(self.phase)
        left.addLayout(grid)
        left.addWidget(self.b_dock)
        left.addWidget(_hrow(self.b_in, self.b_out, stretch=False))
        left.addWidget(self.b_join)
        self.log_lbl = self._label("Log:")
        left.addWidget(self.log_lbl)
        left.addWidget(self.log, 1)
        left.addWidget(self.b_log, 0, Qt.AlignRight)

        # -------------------------------------------------------- centro --
        self.view = DockView(dock)
        self.view.picked.connect(self._picked)
        self.view.acted.connect(self._acted)
        self.view.escaped.connect(lambda: self._pick(None))
        self.b_cam = QPushButton()
        self.b_cam.clicked.connect(self._camera_now)
        self.show_spots = QCheckBox()
        self.show_spots.setChecked(True)
        self.show_spots.toggled.connect(lambda _on: self._markers())
        self.view_note = QLabel("")
        self.view_note.setWordWrap(True)
        self.view_note.setEnabled(False)
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.addWidget(self.view, 1)
        center.addWidget(_hrow(self.b_cam, self.show_spots))
        center.addWidget(self.view_note)

        # ------------------------------------------------------- direita --
        self.side = QTabWidget()
        self.side.setMinimumWidth(340)
        self.side.setMaximumWidth(460)
        for page in (self._strategy_page(), self._screens_page(),
                     self._settings_page()):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(page)
            self.side.addTab(scroll, "")

        self.footer = QLabel("")
        self.footer.setEnabled(False)
        top = QHBoxLayout()
        top.addWidget(left_w)
        top.addLayout(center, 1)
        top.addWidget(self.side)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addLayout(top, 1)
        col.addWidget(self.error)
        col.addWidget(self.footer)
        self.setMinimumSize(980, 600)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        dock.changed.connect(self._dock_changed)
        if not self.c["strategies"]:
            # a primeira já existe: dá para marcar os lugares de cara
            s = normalize_tower_strategy({"name": _("Strategy {n}", n=1)})
            self.c["strategies"].append(s)
            self.c["current"] = s["id"]
        self._load_settings()
        self.shot.setEnabled(bool(self.c["hook"]))
        self._link_note()
        self._fill_strategies()
        self.retranslate()
        self._dock_changed()

    # ----------------------------------------------------------- montagem --
    def _label(self, text):
        lbl = QLabel(_(text))
        self._labels.append((lbl, text))
        return lbl

    def _form_row(self, form, text, widget, tip=None):
        lbl = self._label(text)
        form.addRow(lbl, widget)
        if tip:
            self._tips.append((widget, tip))
        return lbl

    def _strategy_page(self):
        page = QWidget()
        col = QVBoxLayout(page)
        self.strat = QComboBox()
        self.strat.currentIndexChanged.connect(self._strategy_changed)
        self.b_new = QPushButton()
        self.b_new.clicked.connect(self._new)
        self.b_del = QPushButton()
        self.b_del.clicked.connect(self._delete)
        self.b_export = QPushButton()
        self.b_export.clicked.connect(self._export)
        self.b_import = QPushButton()
        self.b_import.clicked.connect(self._import)
        col.addWidget(_hrow(self.strat, self.b_new, self.b_del,
                            stretch=False))
        col.addWidget(_hrow(self.b_import, self.b_export))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.name.textEdited.connect(self._renamed)
        self._form_row(form, "Name:", self.name)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(64)
        self.notes.textChanged.connect(self._notes_changed)
        self._form_row(form, "Notes:", self.notes)
        col.addLayout(form)
        self.size_note = QLabel("")
        self.size_note.setWordWrap(True)
        self.size_note.setStyleSheet("color:#c86000;")
        self.size_note.hide()
        col.addWidget(self.size_note)

        self.towers_box = QGroupBox()
        grid = QGridLayout(self.towers_box)
        self.tower_rows = []
        for k in range(TOWER_SLOTS):
            num = QLabel(str(k + 1))
            num.setStyleSheet(f"color:{TOWER_COLORS[k]};font-weight:bold;")
            name = QLineEdit()
            name.setMaxLength(40)
            name.textEdited.connect(lambda t, k=k: self._tower_named(k, t))
            count = QLabel("")
            pick = QPushButton()
            pick.setCheckable(True)
            pick.clicked.connect(lambda on, k=k: self._pick(k if on else None))
            clear = QPushButton("✕")
            clear.setFixedWidth(30)
            clear.clicked.connect(lambda _c=False, k=k: self._clear_tower(k))
            for c_, w in enumerate((num, name, count, pick, clear)):
                grid.addWidget(w, k, c_)
            self.tower_rows.append((num, name, count, pick, clear))
        grid.setColumnStretch(1, 1)
        col.addWidget(self.towers_box)

        self.timeline_box = QGroupBox()
        tcol = QVBoxLayout(self.timeline_box)
        self.timeline = QListWidget()
        self.timeline.setMinimumHeight(150)
        self.timeline.itemDoubleClicked.connect(lambda _i: self._edit())
        self.timeline.currentRowChanged.connect(lambda _r: self._sync())
        self.b_rec = QPushButton()
        self.b_rec.setCheckable(True)
        self.b_rec.clicked.connect(self._record)
        self.b_undo = QPushButton()
        self.b_undo.clicked.connect(self._undo)
        self.rec_note = QLabel("")
        self.rec_note.setStyleSheet("color:#da4453;font-weight:bold;")
        self.b_add = QPushButton()
        self.b_add.clicked.connect(self._add)
        self.b_edit = QPushButton()
        self.b_edit.clicked.connect(self._edit)
        self.b_remove = QPushButton()
        self.b_remove.clicked.connect(self._remove)
        self.b_clear = QPushButton()
        self.b_clear.clicked.connect(self._clear_actions)
        tcol.addWidget(_hrow(self.b_rec, self.b_undo, self.rec_note))
        tcol.addWidget(self.timeline, 1)
        tcol.addWidget(_hrow(self.b_add, self.b_edit, self.b_remove,
                             self.b_clear))
        col.addWidget(self.timeline_box, 1)

        self.lobby_group = QGroupBox()
        lcol = QVBoxLayout(self.lobby_group)
        self.lobby_hint = QLabel("")
        self.lobby_hint.setWordWrap(True)
        self.lobby_hint.setEnabled(False)
        self.lobby_box = QVBoxLayout()
        self.lobby_box.setContentsMargins(0, 0, 0, 0)
        self.b_lobby = QPushButton()
        self.b_lobby.clicked.connect(lambda: self._add_lobby(""))
        self.lobby_macro = QComboBox()
        self.lobby_macro.currentIndexChanged.connect(self._lobby_macro_set)
        lform = QFormLayout()
        self._form_row(lform, "Lobby macro:", self.lobby_macro)
        lcol.addWidget(self.lobby_hint)
        lcol.addLayout(self.lobby_box)
        lcol.addWidget(self.b_lobby, 0, Qt.AlignLeft)
        lcol.addLayout(lform)
        col.addWidget(self.lobby_group)
        return page

    SCREEN_ROWS = (
        ("start", "Match started:",
         "Optional. Something that only shows once the match has begun, "
         "like the wave counter. The clock of the strategy starts when it "
         "shows up; without it, right after the lobby."),
        ("placed", "Tower placed:",
         "A piece of the panel that opens when a tower goes down (turn on "
         "Show Tower Options in the game settings). With it, a tower that "
         "did not go down, like when there is not enough cash yet, is tried "
         "again."),
        ("skip", "Skip wave:",
         "Optional. Clicked whenever it shows up during the match."),
        ("victory", "Victory:",
         "Something that only shows when the match is won. Counts a win."),
        ("defeat", "Defeat:",
         "Something that only shows when the match is lost. Counts a "
         "loss."),
        ("reconnect", "Reconnect:",
         "Optional. Clicked whenever it shows up, and the farm starts over "
         "from the lobby."),
        ("sell", "Sell button:",
         "Optional. Clicked to sell, after clicking the tower. Without it, "
         "the sell key of Settings is pressed."))
    AFTER_TIP = ("The buttons clicked in order when the match ends, like the "
                 "one that plays again or the one that goes back to the "
                 "lobby. With no Victory and Defeat pictures, the first one "
                 "is also how the end of the match is seen.")

    def _screens_page(self):
        page = QWidget()
        col = QVBoxLayout(page)
        self.screens_hint = QLabel("")
        self.screens_hint.setWordWrap(True)
        self.screens_hint.setEnabled(False)
        col.addWidget(self.screens_hint)
        form = QFormLayout()
        self.screen_fields = {}
        for key, text, tip in self.SCREEN_ROWS:
            f = ImageField(self.c["screens"][key], self._grab, self.error)
            f.changed.connect(lambda k=key: self._screen_set(k))
            self.screen_fields[key] = f
            self._form_row(form, text, f, tip)
            if key == "defeat":
                self.after_box = QVBoxLayout()
                self.after_box.setContentsMargins(0, 0, 0, 0)
                self.b_after = QPushButton()
                self.b_after.clicked.connect(lambda: self._add_after(""))
                aw = QWidget()
                acol = QVBoxLayout(aw)
                acol.setContentsMargins(0, 0, 0, 0)
                acol.addLayout(self.after_box)
                acol.addWidget(self.b_after, 0, Qt.AlignLeft)
                self._form_row(form, "After the match:", aw, self.AFTER_TIP)
        col.addLayout(form)
        col.addStretch()
        for path in self.c["screens"]["after"] or [""]:
            self._add_after(path, save=False)
        return page

    def _spin(self, lo, hi, suffix, special=None, double=False):
        w = QDoubleSpinBox() if double else QSpinBox()
        if double:
            w.setDecimals(1)
            w.setSingleStep(0.5)
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        if special:
            self._specials.append((w, special))
        w.valueChanged.connect(lambda _v: self._collect())
        return w

    def _settings_page(self):
        page = QWidget()
        col = QVBoxLayout(page)
        form = QFormLayout()
        self.win = QLineEdit()
        self.win.setMaxLength(100)
        self.win.textEdited.connect(lambda _t: self._collect())
        self._form_row(form, "Game window:", self.win,
                       "The app name of the game window in the Dock, like "
                       "Sober.")
        self.link = QLineEdit()
        self.link.setMaxLength(2000)
        self.link.setPlaceholderText("https://www.roblox.com/games/"
                                     "3260590327/…")
        self.link.textEdited.connect(lambda _t: self._collect())
        self._form_row(form, "Game link:", self.link,
                       "A Roblox game page, a private server link "
                       "(…?privateServerLinkCode=…) or a roblox:// link. It "
                       "opens in the Dock with Join the game, and again "
                       "when the game closes.")
        self.link_note = QLabel("")
        self.link_note.setWordWrap(True)
        self.link_note.setStyleSheet("color:#c86000;font-size:11px;")
        self.link_note.hide()
        form.addRow(self.link_note)
        self.lobby_wait = self._spin(1, 3600, " s")
        self._form_row(form, "Wait for each lobby button:", self.lobby_wait)
        self.start_wait = self._spin(1, 3600, " s")
        self._form_row(form, "Wait for the match to start:",
                       self.start_wait)
        self.match_max = self._spin(1, 600, " min")
        self._form_row(form, "Longest match:", self.match_max,
                       "If the end of the match takes longer than this, "
                       "the farm starts over.")
        self.limit = self._spin(0, MAX_RUNS, "", special="never")
        self._form_row(form, "Stop after:", self.limit)
        self.speed = QComboBox()
        for key, label in (("fast", "Fast"), ("balanced", "Balanced"),
                           ("slow", "Slow")):
            self.speed.addItem(_(label), key)
        self.speed.currentIndexChanged.connect(lambda _i: self._collect())
        self._form_row(form, "Action speed:", self.speed,
                       "How long to wait between pressing, clicking and "
                       "looking. Slow suits a slow computer or connection.")
        self.place_for = self._spin(5, 3600, " s")
        self._form_row(form, "Keep trying to place for:", self.place_for)
        self.retry = self._spin(0.2, 60.0, " s", double=True)
        self._form_row(form, "Try again every:", self.retry)
        self.skip_until = self._spin(0, 600, " min", special="whole match")
        self._form_row(form, "Skip waves until:", self.skip_until,
                       "Minutes from the start of the match. After that the "
                       "Skip wave button is left alone.")
        col.addLayout(form)

        self.camera = QCheckBox()
        self.camera.toggled.connect(lambda _on: self._collect())
        col.addWidget(self.camera)
        cform = QFormLayout()
        self.k_zoom = KeyCatcher(KEYS["O"], "O")
        self.k_zoom.changed.connect(self._collect)
        self.zoom_ms = self._spin(0, 10000, " ms")
        self._form_row(cform, "Zoom out with:", _hrow(self.k_zoom,
                                                      self.zoom_ms))
        self.drag = self._spin(-3000, 3000, " px")
        self._form_row(cform, "Then drag down:", self.drag,
                       "With the right button, from the middle of the "
                       "screen. A negative number drags up.")
        col.addLayout(cform)

        self.keys_box = QGroupBox()
        kform = QFormLayout(self.keys_box)
        self.k_top = KeyCatcher(KEYS["E"], "E")
        self.k_bottom = KeyCatcher(KEYS["Z"], "Z")
        self.k_sell = KeyCatcher(0, "")
        self.b_sell_x = QPushButton("✕")
        self.b_sell_x.setFixedWidth(30)
        self.b_sell_x.clicked.connect(lambda: self.k_sell.set_key(0,
                                                                  _("none")))
        self.k_cancel = KeyCatcher(KEYS["Q"], "Q")
        for w in (self.k_top, self.k_bottom, self.k_sell, self.k_cancel):
            w.changed.connect(self._collect)
        self._form_row(kform, "Upgrade, top path:", self.k_top)
        self._form_row(kform, "Upgrade, bottom path:", self.k_bottom)
        self._form_row(kform, "Sell:", _hrow(self.k_sell, self.b_sell_x,
                                             stretch=False))
        self._form_row(kform, "Cancel placing:", self.k_cancel)
        col.addWidget(self.keys_box)

        self.hot_box = QGroupBox()
        hform = QFormLayout(self.hot_box)
        self.h_start = KeyCatcher(KEYS["F4"], "F4")
        self.h_stop = KeyCatcher(KEYS["F5"], "F5")
        self.h_pause = KeyCatcher(KEYS["F6"], "F6")
        for w in (self.h_start, self.h_stop, self.h_pause):
            w.changed.connect(self._collect)
        self._form_row(hform, "Start:", self.h_start)
        self._form_row(hform, "Stop:", self.h_stop)
        self._form_row(hform, "Pause or resume:", self.h_pause)
        self.hot_note = QLabel("")
        self.hot_note.setWordWrap(True)
        self.hot_note.setEnabled(False)
        hform.addRow(self.hot_note)
        col.addWidget(self.hot_box)

        self.hook_box = QGroupBox()
        wform = QFormLayout(self.hook_box)
        self.hook = QLineEdit()
        self.hook.setMaxLength(500)
        self.hook.textEdited.connect(lambda _t: self._collect())
        self._form_row(wform, "Discord webhook:", self.hook)
        self.report = QLineEdit()
        self.report.setMaxLength(1000)
        self.report.textEdited.connect(lambda _t: self._collect())
        self._form_row(wform, "Report:", self.report,
                       "{run} is the number of the match, {result} how it "
                       "ended, {wins} and {losses} the counts, and {time} "
                       "how long the farm has been running.")
        self.shot = QCheckBox()
        self.shot.toggled.connect(lambda _on: self._collect())
        wform.addRow("", self.shot)
        col.addWidget(self.hook_box)
        col.addStretch()
        return page

    def _load_settings(self):
        c = self.c
        self._loading = True
        self.win.setText(c["window"])
        self.link.setText(c["link"])
        for w, key in ((self.lobby_wait, "lobby_wait"),
                       (self.start_wait, "start_wait"),
                       (self.match_max, "match_max"), (self.limit, "limit"),
                       (self.place_for, "place_for"), (self.retry, "retry"),
                       (self.skip_until, "skip_until"),
                       (self.zoom_ms, "zoom_ms"), (self.drag, "drag")):
            w.setValue(c[key])
        self.speed.setCurrentIndex(max(0, self.speed.findData(c["speed"])))
        self.camera.setChecked(c["camera"])
        for w, key in ((self.k_zoom, "zoom"), (self.k_top, "top"),
                       (self.k_bottom, "bottom"), (self.k_sell, "sell"),
                       (self.k_cancel, "cancel")):
            code = c["keys"][key]
            w.code, w.name = code, key_label(code) if code else _("none")
            w._refresh()
        for w, key in ((self.h_start, "start"), (self.h_stop, "stop"),
                       (self.h_pause, "pause")):
            code = c["hotkeys"][key]
            w.code, w.name = code, key_label(code)
            w._refresh()
        self.hook.setText(c["hook"])
        self.report.setText(_(TOWER_REPORT) if c["report"] == TOWER_REPORT
                            else c["report"])
        self.shot.setChecked(c["shot"])
        self._loading = False

    def _collect(self, *_a):
        """Os campos dos ajustes para a configuração."""
        if self._loading:
            return
        c = self.c
        c["window"] = " ".join(self.win.text().split()) or "Sober"
        c["link"] = self.link.text().strip()
        for w, key in ((self.lobby_wait, "lobby_wait"),
                       (self.start_wait, "start_wait"),
                       (self.match_max, "match_max"), (self.limit, "limit"),
                       (self.place_for, "place_for"),
                       (self.skip_until, "skip_until"),
                       (self.zoom_ms, "zoom_ms"), (self.drag, "drag")):
            c[key] = int(w.value())
        c["retry"] = round(self.retry.value(), 1)
        c["speed"] = self.speed.currentData() or "balanced"
        c["camera"] = self.camera.isChecked()
        for w, key in ((self.k_zoom, "zoom"), (self.k_top, "top"),
                       (self.k_bottom, "bottom"), (self.k_sell, "sell"),
                       (self.k_cancel, "cancel")):
            code = w.code if w.code and not is_button(w.code) else 0
            c["keys"][key] = code or (0 if key == "sell"
                                      else KEYS[TOWER_KEYS[key]])
        for w, key in ((self.h_start, "start"), (self.h_stop, "stop"),
                       (self.h_pause, "pause")):
            code = w.code if w.code and not is_button(w.code) else 0
            c["hotkeys"][key] = code or KEYS[TOWER_HOTKEYS[key]]
        c["hook"] = self.hook.text().strip()
        c["report"] = self.report.text() or _(TOWER_REPORT)
        c["shot"] = self.shot.isChecked()
        self.shot.setEnabled(bool(c["hook"]))
        self._link_note()
        self._hotkeys()
        self._footer()
        self._view_note()
        self._sync()
        self.changed.emit()

    def _link_note(self):
        link = self.c["link"]
        self.link_note.setText("" if not link or tower_link(link) else _(
            "This link would open the game outside the Dock. Use a Roblox "
            "game page, a private server link "
            "(…?privateServerLinkCode=…) or a roblox:// link."))
        self.link_note.setVisible(bool(self.link_note.text()))

    # -------------------------------------------------------- estratégia --
    def _strategy(self):
        sid = self.c["current"]
        return next((s for s in self.c["strategies"] if s["id"] == sid),
                    None)

    def _fill_strategies(self):
        self.strat.blockSignals(True)
        self.strat.clear()
        for s in self.c["strategies"]:
            self.strat.addItem(s["name"], s["id"])
        self.strat.setCurrentIndex(max(0, self.strat.findData(
            self.c["current"])))
        self.strat.blockSignals(False)
        if self.c["strategies"]:
            self.c["current"] = self.strat.currentData()
        self._show_strategy()

    def _strategy_changed(self, _i=0):
        if self.rec is not None:
            self._stop_rec()
        self._pick(None)
        self.c["current"] = self.strat.currentData() or ""
        self._show_strategy()
        self.changed.emit()

    def _show_strategy(self):
        s = self._strategy()
        self._loading = True
        on = s is not None
        for w in (self.name, self.notes, self.towers_box, self.timeline_box,
                  self.lobby_group, self.b_del, self.b_export):
            w.setEnabled(on)
        self.name.setText(s["name"] if on else "")
        self.notes.setPlainText(s["notes"] if on else "")
        self._loading = False
        self._counts()
        self._fill_timeline()
        self._fill_lobby()
        self._fill_lobby_macro()
        self._size_note()
        self._markers()
        self._sync()

    def _counts(self):
        s = self._strategy()
        for k, (_num_, name, count, pick, _x) in enumerate(self.tower_rows):
            t = s["towers"][k] if s else {"name": "", "points": []}
            if name.text() != t["name"]:
                name.setText(t["name"])
            name.setPlaceholderText(_("tower {n}", n=k + 1))
            n = len(t["points"])
            count.setText(_("{n} set ✓", n=n) if n else _("no spots"))
            pick.setChecked(self.picking == k)

    def _new(self):
        if len(self.c["strategies"]) >= TOWER_MAX_STRATS:
            self.error.setText(_("The list holds {n} strategies.",
                                 n=TOWER_MAX_STRATS))
            return
        n = len(self.c["strategies"]) + 1
        s = normalize_tower_strategy({"name": _("Strategy {n}", n=n)})
        self.c["strategies"].append(s)
        self.c["current"] = s["id"]
        self._fill_strategies()
        self.name.setFocus()
        self.name.selectAll()
        self.changed.emit()

    def _delete(self):
        s = self._strategy()
        if s is None:
            return
        if QMessageBox.question(self, _("Delete strategy"), _(
                "Delete {name}? Its towers, spots and timeline go with it.",
                name=s["name"])) != QMessageBox.Yes:
            return
        self._pick(None)
        if self.rec is not None:
            self._stop_rec()
        self.c["strategies"] = [x for x in self.c["strategies"]
                                if x["id"] != s["id"]]
        self.c["current"] = self.c["strategies"][0]["id"] \
            if self.c["strategies"] else ""
        self._fill_strategies()
        self.changed.emit()

    def _renamed(self, text):
        s = self._strategy()
        if s is None or self._loading:
            return
        s["name"] = " ".join(text.split())[:60] or _("Strategy")
        i = self.strat.findData(s["id"])
        if i >= 0:
            self.strat.setItemText(i, s["name"])
        self.changed.emit()

    def _notes_changed(self):
        s = self._strategy()
        if s is None or self._loading:
            return
        s["notes"] = self.notes.toPlainText()[:2000]
        self.changed.emit()

    def _tower_named(self, k, text):
        s = self._strategy()
        if s is None:
            return
        s["towers"][k]["name"] = " ".join(text.split())[:40]
        self._fill_timeline(keep=True)
        self.changed.emit()

    def _dock_size(self):
        return tuple(self.dock.out_size or self.dock.size)

    def _size_note(self):
        s = self._strategy()
        w, h = self._dock_size()
        if s and s["size"] and tuple(s["size"]) != (w, h):
            self.size_note.setText(_(
                "This strategy was made with the Dock at {w}×{h}, and the "
                "Dock is {W}×{H} now: the spots and pictures will miss. "
                "Change the size on the Dock tab.", w=s["size"][0],
                h=s["size"][1], W=w, H=h))
            self.size_note.show()
        else:
            self.size_note.hide()

    def _mark_size(self, s):
        if s["size"] is None:
            s["size"] = list(self._dock_size())

    def _markers(self):
        s = self._strategy()
        marks = []
        if s and self.show_spots.isChecked():
            for k, t in enumerate(s["towers"]):
                for j, (x, y) in enumerate(t["points"]):
                    marks.append((x, y, f"{k + 1}.{j + 1}", TOWER_COLORS[k]))
        self.view.markers = marks
        self.view.update()

    # ------------------------------------------------------ os lugares --
    def _pick(self, k):
        if k is not None:
            why = ""
            if self._strategy() is None:
                why = _("Make a strategy first: New, at the top.")
            elif not self.dock.alive():
                why = _("Open the Dock first: the spots are picked on the "
                        "game.")
            elif self.rec is not None:
                why = _("Stop recording first.")
            if why:
                self.error.setText(why)
                k = None
        self.picking = k
        self.view.pick = k is not None
        for i, row in enumerate(self.tower_rows):
            row[3].setChecked(i == k)
        if k is not None:
            self.error.setText("")
            self.view.setFocus()
        self._view_note()
        self.view.update()

    def _picked(self, x, y, button):
        s, k = self._strategy(), self.picking
        if s is None or k is None:
            return
        if button == BTN["Right"]:
            hit = tower_near(s, x, y, k)
            if hit is None:
                return
            j = hit[1]
            uses = tower_uses(s, k, j)
            if uses and QMessageBox.question(self, _("Remove spot"), _(
                    "{n} actions of the timeline use spot {spot} of {tower}, "
                    "and they go with it. Remove it?", n=len(uses),
                    spot=j + 1, tower=tower_name(s, k))) != QMessageBox.Yes:
                return
            tower_drop_point(s, k, j)
        elif button == BTN["Left"]:
            pts = s["towers"][k]["points"]
            if len(pts) >= TOWER_MAX_POINTS:
                self.error.setText(_("A tower holds {n} spots.",
                                     n=TOWER_MAX_POINTS))
                return
            pts.append([int(x), int(y)])
            self._mark_size(s)
        else:
            return
        self._counts()
        self._fill_timeline(keep=True)
        self._markers()
        self._size_note()
        self.changed.emit()

    def _clear_tower(self, k):
        s = self._strategy()
        if s is None or not s["towers"][k]["points"]:
            return
        uses = tower_uses(s, k)
        text = _("Remove the {n} spots of {tower}?",
                 n=len(s["towers"][k]["points"]), tower=tower_name(s, k))
        if uses:
            text += " " + _("{n} actions of the timeline use them and go "
                            "too.", n=len(uses))
        if QMessageBox.question(self, _("Remove spots"), text) \
                != QMessageBox.Yes:
            return
        s["towers"][k]["points"] = []
        s["actions"] = [a for a in s["actions"] if a.get("tower") != k]
        self._counts()
        self._fill_timeline()
        self._markers()
        self.changed.emit()

    # ------------------------------------------------- a linha do tempo --
    def _fill_timeline(self, select=None, keep=False):
        s = self._strategy()
        row = self.timeline.currentRow() if keep else select
        self.timeline.clear()
        for a in (s["actions"] if s else []):
            self.timeline.addItem(f"{tower_time(a['t'])}   "
                                  f"{tower_action_text(a, s)}")
        if row is not None and self.timeline.count():
            self.timeline.setCurrentRow(max(0, min(row,
                                                   self.timeline.count() - 1)))
        n = len(s["actions"]) if s else 0
        self.timeline_box.setTitle(_("Timeline ({n})", n=n))
        self._sync()

    def _put(self, a, old=None):
        """Põe a ação na linha do tempo, na ordem da hora."""
        s = self._strategy()
        if old is not None:
            s["actions"].remove(old)
        s["actions"].append(a)
        s["actions"].sort(key=lambda x: x["t"])
        self._fill_timeline(select=next(i for i, x in enumerate(s["actions"])
                                        if x is a))
        self.changed.emit()

    def _add(self):
        s = self._strategy()
        if s is None:
            return
        if len(s["actions"]) >= TOWER_MAX_ACTIONS:
            self.error.setText(_("A timeline holds {n} actions.",
                                 n=TOWER_MAX_ACTIONS))
            return
        row = self.timeline.currentRow()
        t = s["actions"][row]["t"] if 0 <= row < len(s["actions"]) else 0.0
        dlg = TowerActionDialog(self, s, {"t": t, "do": "place", "tower": 0,
                                          "point": 0}, self._grab)
        if dlg.exec() == QDialog.Accepted and dlg.action():
            self._put(dlg.action())

    def _edit(self):
        s = self._strategy()
        row = self.timeline.currentRow()
        if s is None or not 0 <= row < len(s["actions"]):
            return
        old = s["actions"][row]
        dlg = TowerActionDialog(self, s, old, self._grab)
        if dlg.exec() == QDialog.Accepted and dlg.action():
            self._put(dlg.action(), old)

    def _remove(self):
        s = self._strategy()
        row = self.timeline.currentRow()
        if s is None or not 0 <= row < len(s["actions"]):
            return
        del s["actions"][row]
        self._fill_timeline(select=row)
        self.changed.emit()

    def _clear_actions(self):
        s = self._strategy()
        if s is None or not s["actions"]:
            return
        if QMessageBox.question(self, _("Clear the timeline"), _(
                "Remove the {n} actions of {name}? The towers and their "
                "spots stay.", n=len(s["actions"]), name=s["name"])) \
                != QMessageBox.Yes:
            return
        s["actions"] = []
        self._fill_timeline()
        self.changed.emit()

    # ---------------------------------------------------------- gravar --
    def _record(self, on):
        if not on:
            self._stop_rec()
            return
        s = self._strategy()
        why = ""
        if s is None:
            why = _("Make a strategy first: New, at the top.")
        elif not self.dock.alive():
            why = _("Open the Dock first: you record by playing in it.")
        elif self.running():
            why = _("Stop the farm first.")
        if why:
            self.error.setText(why)
            self.b_rec.setChecked(False)
            return
        self._pick(None)
        if s["actions"]:
            ans = self._ask_replace(len(s["actions"]))
            if ans is None:
                self.b_rec.setChecked(False)
                return
            if ans == "replace":
                s["actions"] = []
                self._fill_timeline()
        self.error.setText("")
        if self.c["camera"]:
            self.rec_note.setText(_("Lining up the camera…"))
            self._run_camera(self._begin_rec)
        else:
            self._begin_rec()

    def _ask_replace(self, n):
        """A linha do tempo já tem ações: "replace", "add" ou None."""
        box = QMessageBox(QMessageBox.Question, _("Record"), _(
            "The timeline already has {n} actions. Replace them with the new "
            "recording, or add the new ones to them?", n=n),
            QMessageBox.NoButton, self)
        b_new = box.addButton(_("Replace them"), QMessageBox.DestructiveRole)
        b_add = box.addButton(_("Add to them"), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(b_add)
        box.exec()
        hit = box.clickedButton()
        return "replace" if hit is b_new else "add" if hit is b_add else None

    def _begin_rec(self):
        s = self._strategy()
        if s is None or not self.b_rec.isChecked() or not self.dock.alive():
            self.b_rec.setChecked(False)
            self.rec_note.setText("")
            return
        self._collect()
        self._mark_size(s)
        self.rec = TowerRecorder(s, self.c["keys"])
        self._hotkeys()
        self.view.setFocus()
        self._view_note()
        self._sync()

    def _stop_rec(self):
        rec, self.rec = self.rec, None
        self.b_rec.setChecked(False)
        self.rec_note.setText("")
        if rec is not None:
            rec.s["actions"].sort(key=lambda a: a["t"])
            self._fill_timeline()
            self._counts()
            self._markers()
            self.changed.emit()
        self._hotkeys()
        self._view_note()
        self._sync()

    def _undo(self):
        if self.rec is None or self.rec.undo() is None:
            return
        self._fill_timeline(select=self.timeline.count() - 2)
        self._counts()
        self._markers()
        self.changed.emit()

    def _acted(self, kind, code, x, y):
        if self.rec is None:
            return
        if kind == "hotkey":
            if code == KEYS["F10"]:
                self._undo()
            return
        act = self.rec.key(code) if kind == "key" \
            else self.rec.click(x, y, code)
        if act is not None:
            self._fill_timeline(select=len(self.rec.s["actions"]) - 1)
            self._counts()
            self._markers()
            self.changed.emit()
        self._sync()

    # ---------------------------------------------------------- telas --
    def _grab(self):
        frames, err = grab_frames(0, None, self.dock)
        if not frames:
            self.error.setText(err)
        return frames

    def _screen_set(self, key):
        self.c["screens"][key] = self.screen_fields[key].path
        self.changed.emit()

    def _add_after(self, path, save=True):
        if len(self.after_fields) >= TOWER_MAX_AFTER:
            return
        box = []
        f = ImageField(path or "", self._grab, self.error,
                       remove=lambda: self._del_after(box[0]))
        box.append(f)
        f.changed.connect(self._after_set)
        self.after_fields.append(f)
        self.after_box.addWidget(f)
        self.b_after.setEnabled(len(self.after_fields) < TOWER_MAX_AFTER)
        if save:
            self._after_set()

    def _del_after(self, f):
        if f in self.after_fields:
            self.after_fields.remove(f)
            self.after_box.removeWidget(f)
            f.deleteLater()
        self.b_after.setEnabled(True)
        self._after_set()

    def _after_set(self):
        self.c["screens"]["after"] = [f.path for f in self.after_fields
                                      if f.path][:TOWER_MAX_AFTER]
        self.changed.emit()

    def _fill_lobby(self):
        for f in self.lobby_fields:
            self.lobby_box.removeWidget(f)
            f.deleteLater()
        self.lobby_fields = []
        s = self._strategy()
        for path in (s["lobby"] if s else []):
            self._add_lobby(path, save=False)
        self.b_lobby.setEnabled(len(self.lobby_fields) < TOWER_MAX_LOBBY)

    def _add_lobby(self, path, save=True):
        if len(self.lobby_fields) >= TOWER_MAX_LOBBY:
            return
        box = []
        f = ImageField(path or "", self._grab, self.error,
                       remove=lambda: self._del_lobby(box[0]))
        box.append(f)
        f.changed.connect(self._lobby_set)
        self.lobby_fields.append(f)
        self.lobby_box.addWidget(f)
        self.b_lobby.setEnabled(len(self.lobby_fields) < TOWER_MAX_LOBBY)
        if save:
            self._lobby_set()

    def _del_lobby(self, f):
        if f in self.lobby_fields:
            self.lobby_fields.remove(f)
            self.lobby_box.removeWidget(f)
            f.deleteLater()
        self.b_lobby.setEnabled(True)
        self._lobby_set()

    def _lobby_set(self):
        s = self._strategy()
        if s is None:
            return
        s["lobby"] = [f.path for f in self.lobby_fields
                      if f.path][:TOWER_MAX_LOBBY]
        self.changed.emit()

    def _fill_lobby_macro(self):
        s = self._strategy()
        want = s["lobby_macro"] if s else ""
        self.lobby_macro.blockSignals(True)
        self.lobby_macro.clear()
        self.lobby_macro.addItem(_("None"), "")
        for m in self.macros():
            if m.get("steps"):
                self.lobby_macro.addItem(f"{m['name'] or hotkey_label(m)}  "
                                         f"({len(m['steps'])})", m["id"])
        self.lobby_macro.setCurrentIndex(max(0, self.lobby_macro.findData(
            want)))
        self.lobby_macro.blockSignals(False)

    def _lobby_macro_set(self, _i=0):
        s = self._strategy()
        if s is None:
            return
        s["lobby_macro"] = self.lobby_macro.currentData() or ""
        self.changed.emit()

    def showEvent(self, e):
        self._fill_lobby_macro()
        super().showEvent(e)

    # ------------------------------------------------------ arquivos --
    def _export(self):
        s = self._strategy()
        if s is None:
            return
        name = re.sub(r"[^\w.-]+", "-", s["name"]).strip(".-") or "strategy"
        path, _f = QFileDialog.getSaveFileName(
            self, _("Export strategy"),
            os.path.join(os.path.expanduser("~"), name + MACRO_FILE_EXT),
            _("WayClick macros") + f" (*{MACRO_FILE_EXT})")
        if not path:
            return
        if not path.endswith(MACRO_FILE_EXT):
            path += MACRO_FILE_EXT
        data, missing = export_tower(s, self.c["screens"])
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError as e:
            self.error.setText(_("Could not save {path}.", path=path) + " "
                               + str(e.strerror or e))
            return
        text = _("Saved {file}.", file=os.path.basename(path))
        if missing:
            text += " " + _("{n} pictures could not be read and are not in "
                            "it.", n=len(missing))
        self._log_line(text)
        self.error.setText("")

    def _import(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _("Import strategy"), os.path.expanduser("~"),
            _("WayClick macros") + f" (*{MACRO_FILE_EXT} *.json);;"
            + _("All files") + " (*)")
        if path:
            self.import_path(path)

    def import_path(self, path):
        if len(self.c["strategies"]) >= TOWER_MAX_STRATS:
            self.error.setText(_("The list holds {n} strategies.",
                                 n=TOWER_MAX_STRATS))
            return None
        data = None
        try:
            data = read_macro_file(path)
            s, fill, notes = import_tower(data, self.c["screens"])
        except OSError as e:
            self.error.setText(_("Could not read {file}: {err}",
                                 file=os.path.basename(path),
                                 err=e.strerror or e))
            return None
        except ValueError:
            if isinstance(data, dict) and isinstance(data.get("macros"),
                                                     list):
                self.error.setText(_("{file} holds macros, not a strategy: "
                                     "import it on the Hotkeys tab.",
                                     file=os.path.basename(path)))
            else:
                self.error.setText(_("{file} is not a Tower Defense "
                                     "strategy file of WayClick.",
                                     file=os.path.basename(path)))
            return None
        self.c["strategies"].append(s)
        self.c["current"] = s["id"]
        for key, val in fill.items():
            self.c["screens"][key] = val
        self._fill_screens()
        self._fill_strategies()
        self.error.setText("")
        self._log_line(" ".join([_("Imported {name}.", name=s["name"])]
                                + notes))
        self.changed.emit()
        return s

    def _fill_screens(self):
        sc = self.c["screens"]
        for key, f in self.screen_fields.items():
            if f.path != sc[key]:
                f.blockSignals(True)
                f.set_path(sc[key])
                f.blockSignals(False)
        for f in self.after_fields:
            self.after_box.removeWidget(f)
            f.deleteLater()
        self.after_fields = []
        for path in sc["after"] or [""]:
            self._add_after(path, save=False)

    # ------------------------------------------------------------ farm --
    def running(self):
        r = self.runner
        return r is not None and r.is_alive()

    def _plan(self):
        self._collect()
        plan = json.loads(json.dumps(self.c))
        s = self._strategy()
        plan["strategy"] = json.loads(json.dumps(s)) if s else None
        mid = s["lobby_macro"] if s else ""
        m = next((m for m in self.macros() if m["id"] == mid), None) \
            if mid else None
        plan["lobby_steps"] = [dict(st) for st in m["steps"]] if m else []
        return plan

    def start(self):
        if self.running() or self.cam is not None:
            return
        if self.rec is not None:
            self._stop_rec()
        self._pick(None)
        s, sc = self._strategy(), self.c["screens"]
        why = ""
        if s is None:
            why = _("Make a strategy first: New, at the top of the Strategy "
                    "tab.")
        elif not (sc["victory"] or sc["defeat"] or sc["after"]):
            why = _("Capture how a match ends on the Screens tab: Victory, "
                    "Defeat or a button After the match.")
        elif self.c["link"] and not tower_link(self.c["link"]):
            why = self.link_note.text()
        if why:
            self.error.setText(why)
            return
        self.error.setText("")
        if not self.dock.alive() and not self.dock.start(launch=False):
            self.error.setText(self.dock.error or _("The Dock did not "
                                                    "open."))
            return
        self._swap_env()
        self.runner = TowerRunner(self._plan(), self.dock, self.env,
                                  on_done=self._done.emit)
        self.began = time.monotonic()
        for k in ("matches", "wins", "losses"):
            self.stats[k].setText("0")
        self.runner.start()
        self._sync()
        self.running_changed.emit()

    def stop(self):
        for r in (self.runner, self.cam):
            if r is not None:
                r.stop()
        if self.rec is not None:
            self._stop_rec()

    def pause(self):
        r = self.runner
        if r is None or not r.is_alive():
            return
        r.paused = not r.paused
        self._sync()

    def _swap_env(self):
        self._drain()
        self.env = TowerEnv(self.dock)

    def _finished(self, r):
        self._drain()
        if r is self.runner:
            self._show_stats(r)
            self.runner = None
            self.phase.setText(_("Stopped"))
            self._log_line(_("Farm stopped."))
            self._sync()
            self.running_changed.emit()

    def _run_camera(self, then=None):
        if self.cam is not None or not self.dock.alive():
            return False
        self._swap_env()
        self._cam_then = then
        self.cam = TowerRunner(self._plan(), self.dock, self.env,
                               on_done=self._cam_done.emit, camera_only=True)
        self.cam.start()
        self._sync()
        return True

    def _camera_now(self):
        if self.running():
            return
        if not self.dock.alive():
            self.error.setText(_("Open the Dock first."))
            return
        self._run_camera()

    def _cam_finished(self, r):
        self._drain()
        if r is not self.cam:
            return
        self.cam = None
        then, self._cam_then = self._cam_then, None
        if then:
            then()
        self._sync()

    def _drain(self):
        env = self.env
        if env is None:
            return
        for t, warn, text in env.take():
            self._log_line(text, warn, t)

    def _log_line(self, text, warn=False, t=None):
        stamp = time.strftime("%H:%M:%S", time.localtime(t or time.time()))
        self.log.appendPlainText(f"{stamp}  " + ("⚠ " if warn else "")
                                 + text)

    def _show_stats(self, r):
        for k in ("matches", "wins", "losses"):
            self.stats[k].setText(str(r.counters[k]))
        if self.began is not None:
            self.stats["time"].setText(clock_text(time.monotonic()
                                                  - self.began))

    def _tick(self):
        self._drain()
        r = self.runner
        if r is not None and r.is_alive():
            self.phase.setText(r.phase or _("Running…"))
            self._show_stats(r)
            want = _("Resume") if r.paused else _("Pause")
            if self.b_pause.text() != want:
                self.b_pause.setText(want)
        if self.rec is not None:
            self.rec_note.setText(_("● Recording {t}",
                                    t=tower_time(self.rec.now())))
        self._sync_view()

    def _sync_view(self):
        win = self.window()
        want = self.isVisible() and self.dock.alive() \
            and not (win.windowState() & Qt.WindowMinimized)
        if want and not self.view.running():
            self.view.start()
        elif not want and self.view.running():
            self.view.stop()
        # janela do WayClick atrás de outra: menos capturas
        self.view.fps = DockView.FPS if win.isActiveWindow() else 5

    # ------------------------------------------------------------ Doca --
    def _dock_changed(self):
        if not self.dock.alive():
            self.stop()
            if self.picking is not None:
                self._pick(None)
        self._hotkeys()
        self._size_note()
        self._sync_view()
        self._view_note()
        self._sync()
        self.view.update()

    def _dock_toggle(self):
        if self.dock.state == "starting":
            return
        if self.dock.alive():
            if self.running() and QMessageBox.question(
                    self, _("Close the Dock"),
                    _("The farm is running. Close the Dock, and the game "
                      "with it?")) != QMessageBox.Yes:
                return
            self.stop()
            self.dock.stop()
            return
        if not self.dock.start(launch=False):
            self.error.setText(self.dock.error or _("The Dock did not "
                                                    "open."))
            return
        if tower_link(self.c["link"]):
            self._join()
        else:
            self.dock.launch()

    def _join(self):
        link = tower_link(self.c["link"])
        if not link:
            self.error.setText(self.link_note.text() or _(
                "Put the game link in Settings: a Roblox game page or a "
                "private server link."))
            return
        if not self.dock.alive() and not self.dock.start(launch=False):
            self.error.setText(self.dock.error or _("The Dock did not "
                                                    "open."))
            return
        self.error.setText("")
        self._log_line(_("Opening the game in the Dock…"))
        self.dock.open(link, report=lambda t: self._log_line(
            t[:1].upper() + t[1:], True))

    def _dock_in(self):
        if self.dock.hide() is None and self.dock.alive():
            self._log_line(_("KWin did not find the Dock window."), True)
        self.view.setFocus()

    def _dock_out(self):
        if self.dock.show() is None and self.dock.alive():
            self._log_line(_("KWin did not find the Dock window."), True)

    def set_claims(self, claims):
        """(código) -> bool: a tecla já é de outro (um macro ligado, o
        atalho de armar). Aí o aperto é dele, não do farm."""
        self.claims = claims
        for w in self.watchers.values():
            w.shadow = claims

    def _hotkeys(self):
        """Os atalhos globais do farm, enquanto a Doca está aberta."""
        codes = self.c["hotkeys"]
        if not self.dock.alive():
            for w in self.watchers.values():
                w.stop()
            self.watchers = {}
        else:
            for name, slot in (("start", self.start), ("stop", self.stop),
                               ("pause", self.pause)):
                w = self.watchers.get(name)
                if w is None:
                    w = HotkeyWatcher(codes[name])
                    w.shadow = self.claims
                    w.pressed.connect(slot)
                    w.start()
                    self.watchers[name] = w
                elif w.keycode != codes[name]:
                    w.set_key(codes[name])
        skip = set(codes.values())
        if self.rec is not None:
            skip.add(KEYS["F10"])
        self.view.skip_keys = skip

    # ------------------------------------------------------- aparência --
    def _sync(self):
        if not hasattr(self, "b_clear"):
            return
        running, on = self.running(), self.dock.alive()
        s = self._strategy()
        busy = running or self.cam is not None
        rec = self.rec is not None
        self.b_start.setEnabled(not busy and not rec and s is not None)
        self.b_stop.setEnabled(busy)
        self.b_pause.setEnabled(running)
        if not running:
            self.b_pause.setText(_("Pause"))
        self.b_dock.setText(_("Close the Dock") if on else _("Open the Dock"))
        self.b_dock.setEnabled(self.dock.state != "starting")
        self.b_in.setEnabled(on)
        self.b_out.setEnabled(on)
        self.b_join.setEnabled(bool(tower_link(self.c["link"])))
        self.b_cam.setEnabled(on and not busy and not rec)
        self.b_rec.setEnabled(s is not None and not running
                              and (on or rec))
        self.b_undo.setEnabled(rec and bool(self.rec.undos))
        row = self.timeline.currentRow()
        has = s is not None and 0 <= row < len(s["actions"])
        self.b_edit.setEnabled(has and not rec)
        self.b_remove.setEnabled(has and not rec)
        self.b_add.setEnabled(s is not None and not rec)
        self.b_clear.setEnabled(bool(s and s["actions"]) and not rec)
        for _n, _name, _c, pick, clear in self.tower_rows:
            pick.setEnabled(s is not None and on and not running and not rec)
            clear.setEnabled(s is not None and not running and not rec)
        self.strat.setEnabled(not running and not rec)
        self.b_new.setEnabled(not running and not rec)
        self.b_import.setEnabled(not running and not rec)

    def _footer(self):
        h = self.c["hotkeys"]
        self.footer.setText(_(
            "{start} starts · {stop} stops · {pause} pauses or resumes, in "
            "any window while the Dock is open",
            start=key_label(h["start"]), stop=key_label(h["stop"]),
            pause=key_label(h["pause"])))
        self.b_start.setText(_("Start ({key})", key=key_label(h["start"])))
        self.b_stop.setText(_("Stop ({key})", key=key_label(h["stop"])))

    def _view_note(self):
        if not hasattr(self, "view_note"):
            return
        k = self.c["keys"]
        if not self.dock.alive():
            text = _("Open the Dock on the left: it opens with the game link "
                     "of Settings, and the game shows up here.")
        elif self.picking is not None:
            text = _("Picking the spots of {tower}: click where it goes, "
                     "right click a spot to remove it, Esc ends.",
                     tower=tower_name(self._strategy(), self.picking))
        elif self.rec is not None:
            text = _("Recording: 1 to 5 picks a tower and a click places it. "
                     "Click a tower, then {top} or {bottom} upgrades it{sell}. "
                     "F10 undoes.", top=key_label(k["top"]),
                     bottom=key_label(k["bottom"]),
                     sell=(_(", {key} sells it", key=key_label(k["sell"]))
                           if k["sell"] else ""))
        else:
            text = _("The game of the Dock: mouse, wheel and keys over it go "
                     "to the game.")
        self.view_note.setText(text)
        self.view.empty_text = _("The Dock is closed.") \
            if not self.dock.alive() else _("Waiting for the picture of the "
                                            "Dock…")

    def retranslate(self):
        for lbl, src in self._labels:
            lbl.setText(_(src))
        for w, tip in self._tips:
            w.setToolTip(_(tip))
        for w, special in self._specials:
            w.setSpecialValueText(_(special))
        self.limit.setSuffix(" " + _("matches"))
        for b, src in ((self.b_new, "New"), (self.b_del, "Delete"),
                       (self.b_export, "Export…"), (self.b_import, "Import…"),
                       (self.b_rec, "● Record"), (self.b_undo, "Undo (F10)"),
                       (self.b_add, "Add…"), (self.b_edit, "Edit…"),
                       (self.b_remove, "Remove"), (self.b_clear, "Clear"),
                       (self.b_lobby, "+ Add a lobby button"),
                       (self.b_after, "+ Add a button"),
                       (self.b_in, "Dock the game"), (self.b_out, "Undock"),
                       (self.b_join, "Join the game"),
                       (self.b_log, "Clear the log"),
                       (self.b_cam, "Line up the camera")):
            b.setText(_(src))
        for _n, _name, _c, pick, clear in self.tower_rows:
            pick.setText(_("Pick"))
            pick.setToolTip(_("Pick the spots of this tower on the game"))
            clear.setToolTip(_("Remove the spots of this tower"))
        self.b_in.setToolTip(_("Minimize the Dock window: the game keeps "
                               "playing here"))
        self.b_out.setToolTip(_("Bring the Dock window to the front"))
        self.b_join.setToolTip(_("Open the game link of Settings in the "
                                 "Dock"))
        self.b_cam.setToolTip(_("Zoom out and drag the camera down, as at "
                                "the start of each match"))
        self.b_sell_x.setToolTip(_("No sell key"))
        self.show_spots.setText(_("Show the spots"))
        self.towers_box.setTitle(_("Towers"))
        self.lobby_group.setTitle(_("Lobby"))
        self.keys_box.setTitle(_("Game keys"))
        self.hot_box.setTitle(_("Hotkeys"))
        self.hook_box.setTitle(_("Report"))
        self.camera.setText(_("Line up the camera when a match starts"))
        self.camera.setToolTip(_(
            "The spots are places on the screen, so the camera has to be "
            "the same in every match: zoomed all the way out and looking "
            "down. Recording lines it up too."))
        self.shot.setText(_("Attach a screenshot"))
        self.hook.setPlaceholderText(_("optional") + ", "
                                     "https://discord.com/api/webhooks/…")
        self.notes.setPlaceholderText(_("Map, mode, the guide it follows…"))
        self.lobby_hint.setText(_(
            "The buttons clicked in order to get into a match. The lobby "
            "macro, if any, plays before them, like walking into an "
            "elevator."))
        self.screens_hint.setText(_(
            "Pictures of the game, captured from the Dock while it shows "
            "them. The farm looks for them on the screen, nothing reads the "
            "memory of the game. In the settings of the game, turn off "
            "Camera Shake, Low Quality Rings and the Switch Hotbar key (Q "
            "also cancels placing), turn on Show Tower Options, and keep the "
            "UI Scale as it was when you captured them."))
        self.hot_note.setText(_(
            "They work in any window while the Dock is open. A hotkey macro "
            "or the WayClick hotkey on the same key takes it first."))
        self.side.setTabText(0, _("Strategy"))
        self.side.setTabText(1, _("Screens"))
        self.side.setTabText(2, _("Settings"))
        speed = self.speed.currentIndex()
        for i, src in enumerate(("Fast", "Balanced", "Slow")):
            self.speed.setItemText(i, _(src))
        self.speed.setCurrentIndex(speed)
        if self.k_sell.code == 0:
            self.k_sell.name = _("none")
            self.k_sell._refresh()
        for f in (list(self.screen_fields.values()) + self.after_fields
                  + self.lobby_fields):
            f.retranslate()
        if not self.running():
            self.phase.setText(_("Stopped"))
        self._counts()
        self._fill_timeline(keep=True)
        self._fill_lobby_macro()
        self._footer()
        self._view_note()
        self._sync()

    def state(self):
        # cada campo já grava em self.c quando muda
        return json.loads(json.dumps(self.c))

    def shutdown(self):
        self._timer.stop()
        for r in (self.runner, self.cam):
            if r is not None:
                r.stop()
        for r in (self.runner, self.cam):
            if r is not None:
                r.join(timeout=1.0)
        self.view.stop()
        for w in self.watchers.values():
            w.stop()
        self.watchers = {}


# ------------------------------------------------------------------ UI ------
# "Runs" e não "Clicks": o modo gateia tudo o que estiver ligado — clique,
# macro de teclado e tecla direcionada —, não só o clique.
# Dois modos, não três. "enquanto o atalho" e "enquanto o botão" eram o mesmo
# modo com gatilhos diferentes, e obrigavam a escolher o modo certo antes de
# poder escolher o gatilho — que é onde todo mundo tropeçava. Agora o campo
# Gatilho aceita os dois e o modo só diz se ele segura ou não.
DOCK_BOX_TIP = ("Keys, clicks and screen steps go only to the game in the "
                "Dock, never to the window in front, and positions are those "
                "of the Dock screen. Open the Dock on the Dock tab.")
MACRO_TAB_HINT = ("Each macro has its own hotkey and works in any window, even "
                  "with WayClick stopped, like an AutoHotkey script. Import "
                  "AHK converts the hotkeys of a .ahk file.")
DOCK_TAB_HINT = ("The game opens in a window of its own that belongs to "
                 "WayClick. Macros set to Play in the Dock press keys, click "
                 "and look at the screen only in there, so your mouse and "
                 "keyboard stay free, and the Dock window can stay behind "
                 "other windows or minimized. Your usual Sober opens one "
                 "copy at a time: close it before starting it in the Dock, "
                 "or pick one of your Sober accounts in Account.")


# ------------------------------------------------------ a barra lateral ---
def fold_text(text):
    """Texto para comparar na busca: sem acento e sem caixa, então
    "automacao" acha "Automação"."""
    t = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(c for c in t if not unicodedata.combining(c))


def nav_icon(names, letter):
    """O primeiro destes ícones que o tema tiver. Sem nenhum (outro desktop,
    tema incompleto), a inicial num quadrado: no modo estreito a lista só
    tem os ícones, e um vazio não diria nada."""
    for name in names:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3daee9"))
    p.drawRoundedRect(QRectF(3, 3, 42, 42), 10, 10)
    f = p.font()
    f.setBold(True)
    f.setPixelSize(26)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, 48, 48), Qt.AlignCenter, letter[:1].upper())
    p.end()
    return QIcon(pm)


class SideNav(QWidget):
    """As páginas numa lista à esquerda, em grupos, como nas Configurações
    do Sistema do KDE, no lugar das abas: com oito páginas as abas já não
    cabiam numa janela pequena, e cada função nova piorava.

    Em cima da lista, uma busca que olha o nome da página e os textos dela
    (sem acento e sem caixa). Quando a janela fica estreita demais para os
    nomes, a lista vira só ícones (o nome no tooltip, o ponto de "ligado"
    no ícone) e a busca vira um botão, que abre a lista até escolher.
    Fala a língua do QTabWidget (addTab, tabText, setTabText,
    setCurrentWidget, currentChanged...), que é o que o resto do app usa.
    """
    currentChanged = Signal(int)
    WIDE = 184            # px da lista com os nomes
    NARROW = 50           # px da lista só com os ícones
    ICON = 22
    ROW = 30              # altura de uma página na lista
    DOT = "● "            # o começo do nome de uma página ligada

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages = []            # widget, text, icon, group, item
        self._heads = {}            # grupo -> item do título dele
        self._compact = False       # a janela não comporta os nomes
        self._peek = False          # estreita, mas aberta para buscar
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._first_match)
        self.search.installEventFilter(self)
        self.find_btn = QToolButton()
        self.find_btn.setAutoRaise(True)
        self.find_btn.setIcon(nav_icon(["edit-find", "system-search"], "?"))
        self.find_btn.clicked.connect(self.start_search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(self.ICON, self.ICON))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemClicked.connect(self._picked)
        self.list.installEventFilter(self)
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        self.empty.setEnabled(False)            # cinza pela paleta
        self.empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.empty.hide()
        self.side = QWidget()
        col = QVBoxLayout(self.side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(self.search)
        col.addWidget(self.find_btn, 0, Qt.AlignHCenter)
        col.addWidget(self.list, 1)
        col.addWidget(self.empty)
        self.stack = QStackedWidget()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.side)
        row.addWidget(self.stack, 1)
        # os atalhos do QTabWidget, mais o de buscar
        for keys, step in ((("Ctrl+PgDown", "Ctrl+Tab"), 1),
                           (("Ctrl+PgUp", "Ctrl+Shift+Tab"), -1)):
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(lambda s=step: self.step(s))
        find = QShortcut(QKeySequence(QKeySequence.Find), self)
        find.setContext(Qt.WindowShortcut)
        find.activated.connect(self.start_search)
        self.retranslate()
        self._apply()

    # ------------------------------------------ o que o QTabWidget tem --
    def addTab(self, widget, text, icon=None, group=None):
        if group and group not in self._heads:
            head = QListWidgetItem(_(group))
            head.setFlags(Qt.NoItemFlags)      # não seleciona, as setas pulam
            head.setData(Qt.UserRole, -1)
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            # o segundo grupo em diante com um respiro em cima
            head.setSizeHint(QSize(0, self.ROW + (8 if self._heads else 0)))
            self.list.addItem(head)
            self._heads[group] = head
        i = len(self._pages)
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, i)
        item.setSizeHint(QSize(0, self.ROW))
        self.list.addItem(item)
        self.stack.addWidget(widget)
        self._pages.append({"widget": widget, "text": text,
                            "icon": icon if icon is not None else QIcon(),
                            "group": group, "item": item})
        self._paint(i)
        if self.list.currentRow() < 0:
            self.list.setCurrentItem(item)
        return i

    def count(self):
        return len(self._pages)

    def widget(self, i):
        return self._pages[i]["widget"] if 0 <= i < len(self._pages) else None

    def indexOf(self, widget):
        for i, p in enumerate(self._pages):
            if p["widget"] is widget:
                return i
        return -1

    def currentIndex(self):
        return self.stack.currentIndex() if self._pages else -1

    def currentWidget(self):
        return self.stack.currentWidget()

    def setCurrentIndex(self, i):
        if not 0 <= i < len(self._pages):
            return
        item = self._pages[i]["item"]
        if item.isHidden():                 # a busca escondia: desfaz
            self.search.clear()
        self.list.setCurrentItem(item)

    def setCurrentWidget(self, widget):
        self.setCurrentIndex(self.indexOf(widget))

    def tabText(self, i):
        return self._pages[i]["text"] if 0 <= i < len(self._pages) else ""

    def setTabText(self, i, text):
        if 0 <= i < len(self._pages) and self._pages[i]["text"] != text:
            self._pages[i]["text"] = text
            self._paint(i)
            if self.search.text().strip():
                self._filter(self.search.text())

    def tabIcon(self, i):
        return self._pages[i]["icon"] if 0 <= i < len(self._pages) else QIcon()

    def narrow(self):
        """Só os ícones agora (a janela está estreita e a busca, fechada)."""
        return self._compact and not self._peek

    def step(self, d):
        """A página seguinte (d=1) ou a anterior (d=-1) das que a busca
        mostra, dando a volta."""
        n = len(self._pages)
        i = self.currentIndex()
        for _k in range(n):
            i = (i + d) % n
            if not self._pages[i]["item"].isHidden():
                self.setCurrentIndex(i)
                return

    def retranslate(self):
        self.search.setPlaceholderText(_("Search…"))
        self.find_btn.setToolTip(_("Search (Ctrl+F)"))
        self.empty.setText(_("Nothing found."))
        for group, head in self._heads.items():
            head.setText(_(group))
        if self.search.text().strip():
            self._filter(self.search.text())

    # -------------------------------------------------------- a lista --
    def _row_changed(self, row):
        item = self.list.item(row)
        i = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(i, int) or i < 0:
            return
        if self.stack.currentIndex() != i:
            self.stack.setCurrentIndex(i)
            self.currentChanged.emit(i)

    def _picked(self, _item):
        self._end_peek()

    def _paint(self, i):
        p = self._pages[i]
        item, text = p["item"], p["text"]
        if self.narrow():
            item.setText("")
            item.setIcon(self._dotted(p["icon"]) if text.startswith(self.DOT)
                         else p["icon"])
            item.setToolTip(text)
        else:
            item.setText(text)
            item.setIcon(p["icon"])
            item.setToolTip("")

    def _dotted(self, icon):
        """O ícone com o ponto verde de "ligado" no canto, para o modo
        estreito, em que o nome (e o ● dele) não aparece."""
        s = self.ICON
        dpr = max(1.0, self.devicePixelRatioF())
        pm = QPixmap(int(round(s * dpr)), int(round(s * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        icon.paint(p, 0, 0, s, s)
        r = s * 0.46
        p.setPen(QPen(self.palette().color(QPalette.Base), 1.5))
        p.setBrush(QColor("#27ae60"))
        p.drawEllipse(QRectF(s - r - 0.75, s - r - 0.75, r, r))
        p.end()
        return QIcon(pm)

    # -------------------------------------------------------- a busca --
    def _haystack(self, i):
        """Tudo o que a página escreve, para a busca achar "intervalo" na
        página Clique ou o nome de um macro na página Atalhos."""
        p = self._pages[i]
        bits = [p["text"], _(p["group"]) if p["group"] else ""]
        for w in [p["widget"]] + p["widget"].findChildren(QWidget):
            if isinstance(w, (QLabel, QAbstractButton)):
                bits.append(w.text())
            elif isinstance(w, QGroupBox):
                bits.append(w.title())
            elif isinstance(w, QLineEdit):
                bits.append(w.placeholderText())
            elif isinstance(w, QComboBox):
                bits += [w.itemText(k) for k in range(w.count())]
            elif isinstance(w, QListWidget):
                bits += [w.item(k).text() for k in range(w.count())]
            elif isinstance(w, QTabWidget):
                bits += [w.tabText(k) for k in range(w.count())]
            if w.toolTip():
                bits.append(w.toolTip())
        return fold_text(" ".join(bits))

    def _filter(self, text):
        words = fold_text(text).split()
        groups, shown = set(), 0
        for i, p in enumerate(self._pages):
            ok = not words or all(w in self._haystack(i) for w in words)
            p["item"].setHidden(not ok)
            if ok:
                shown += 1
                groups.add(p["group"])
        for group, head in self._heads.items():
            head.setHidden(self.narrow() or group not in groups)
        self.empty.setVisible(bool(words) and not shown and not self.narrow())

    def _first_match(self):
        for p in self._pages:
            if not p["item"].isHidden():
                self.list.setCurrentItem(p["item"])
                break
        self._end_peek()

    def start_search(self):
        """Ctrl+F ou o botão da lupa: estreita, a lista abre com os nomes até
        escolher uma página (ou Esc)."""
        if self._compact and not self._peek:
            self._peek = True
            self._apply()
        self.search.setFocus(Qt.ShortcutFocusReason)
        self.search.selectAll()

    def _end_peek(self):
        if self._peek:
            self._peek = False
            self._apply()

    def _maybe_end_peek(self):
        if not self._peek:
            return
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self.side or self.side.isAncestorOf(fw)):
            return
        self._end_peek()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.search and t in (QEvent.ShortcutOverride,
                                        QEvent.KeyPress) \
                and ev.key() == Qt.Key_Escape \
                and (self.search.text() or self._peek):
            # o Esc da busca é dela: limpa e fecha, sem chegar ao Esc da
            # janela (que para o autoclicker)
            ev.accept()
            if t == QEvent.KeyPress:
                self.search.clear()
                was = self._peek
                self._end_peek()
                if was:
                    self.list.setFocus(Qt.OtherFocusReason)
            return t == QEvent.KeyPress
        if t == QEvent.FocusOut and obj in (self.search, self.list):
            QTimer.singleShot(0, self._maybe_end_peek)
        return super().eventFilter(obj, ev)

    # ---------------------------------------------- largo ou estreito --
    def _need(self):
        """A largura com que os nomes cabem sem apertar as páginas."""
        return (self.WIDE + self.layout().spacing()
                + self.stack.minimumSizeHint().width())

    def minimumSizeHint(self):
        # o mínimo é o do modo estreito: é ele que deixa a janela encolher
        st = self.stack.minimumSizeHint()
        return QSize(self.NARROW + self.layout().spacing() + st.width(),
                     max(st.height(), self.ROW * 4))

    def sizeHint(self):
        st = self.stack.sizeHint()
        return QSize(self.WIDE + self.layout().spacing() + st.width(),
                     max(st.height(), self.ROW * 4))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def _fit(self):
        compact = self.width() < self._need()
        if compact != self._compact:
            self._compact = compact
            if not compact:
                self._peek = False
            self._apply()

    def _apply(self):
        narrow = self.narrow()
        self.side.setFixedWidth(self.NARROW if narrow else self.WIDE)
        self.search.setVisible(not narrow)
        self.find_btn.setVisible(narrow)
        # estreita, a barra de rolagem comeria os ícones; a roda ainda rola
        self.list.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if narrow else Qt.ScrollBarAsNeeded)
        self._filter(self.search.text())
        for i in range(len(self._pages)):
            self._paint(i)


# ---------------------------------------------- a página das contas -----
SOBER_PAGE_HINT = ("Each account is a separate Sober, with its own Roblox "
                   "sign-in, settings and network, so it opens next to your "
                   "usual Sober and both play at the same time. The first "
                   "time, Sober sets itself up in the new account (it "
                   "downloads Roblox again) and you sign in there. Your usual "
                   "Sober and its files stay as they are.")
SOBER_RISK = ("Roblox calls running two clients at once an exploit, though "
              "it says it does not ban for it today, and Sober does not "
              "support it. Use it at your own risk.")


class SoberPage(QWidget):
    """A página Contas do Sober: a lista, abrir e fechar cada uma.

    Os estados vêm da trava de cada conta, lida de tempos em tempos (é só
    um flock, sem processo nenhum): uma conta aberta antes de o WayClick
    abrir aparece aberta, e uma que o Sober fechou sozinho volta a fechada.
    """
    changed = Signal()           # a lista mudou: salvar, e o combo da Doca
    state_changed = Signal()     # uma conta abriu ou fechou
    POLL_MS = 1500
    FAST_FAIL_S = 30             # saiu com erro antes disso: mostra o porquê
    CLOSE_S = 25                 # mandou fechar e não fechou: avisa

    def __init__(self, accounts, parent=None):
        super().__init__(parent)
        self.accounts = accounts         # a lista do App, a mesma
        self.env = None                  # o ambiente do Sober (os testes)
        self.prefix = None               # idem: o escopo em que ele roda
        self.home = None
        self.open_ids = set()            # as abertas na última olhada
        self._closing = {}               # id -> quando mandou fechar
        self._opening = None             # a conta do "Abrindo…" na tela
        self._fatal = {}                 # id -> o FATAL já mostrado
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setEnabled(False)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _r: self._sync())
        self.list.itemDoubleClicked.connect(lambda _i: self.open_selected())
        self.b_add = QPushButton()
        self.b_add.clicked.connect(self.add)
        self.b_rename = QPushButton()
        self.b_rename.clicked.connect(self.rename)
        self.b_remove = QPushButton()
        self.b_remove.clicked.connect(self.remove)
        self.b_open = QPushButton()
        self.b_open.clicked.connect(self.open_selected)
        self.b_close = QPushButton()
        self.b_close.clicked.connect(self.close_selected)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.hide()
        self.risk = QLabel()
        self.risk.setWordWrap(True)
        self.risk.setEnabled(False)
        self.risk.setStyleSheet("font-size:11px;")
        edit = QHBoxLayout()
        for b in (self.b_add, self.b_rename, self.b_remove):
            edit.addWidget(b)
        edit.addStretch()
        run = QHBoxLayout()
        for b in (self.b_open, self.b_close):
            run.addWidget(b)
        run.addStretch()
        col = QVBoxLayout(self)
        col.addWidget(self.hint)
        col.addWidget(self.list, 1)
        col.addLayout(edit)
        col.addLayout(run)
        col.addWidget(self.status)
        col.addWidget(self.risk)
        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.retranslate()
        self.refresh()

    # -------------------------------------------------------- a lista --
    def selected(self):
        row = self.list.currentRow()
        return self.accounts[row] if 0 <= row < len(self.accounts) else None

    def find(self, acc_id):
        for acc in self.accounts:
            if acc.id == acc_id:
                return acc
        return None

    def _text(self, acc):
        if acc.id in self._closing:
            return _("{name} (closing…)", name=acc.name)
        if acc.id in self.open_ids:
            return SideNav.DOT + _("{name} (open)", name=acc.name)
        return acc.name

    def fill(self, select=None):
        keep = select or (self.selected().id if self.selected() else None)
        self.list.blockSignals(True)
        self.list.clear()
        row = 0
        for i, acc in enumerate(self.accounts):
            self.list.addItem(self._text(acc))
            if acc.id == keep:
                row = i
        self.list.blockSignals(False)
        if self.accounts:
            self.list.setCurrentRow(row)
        self._sync()

    def _sync(self):
        acc = self.selected()
        is_open = acc is not None and acc.id in self.open_ids
        self.b_add.setEnabled(len(self.accounts) < SOBER_ACCOUNTS_MAX)
        self.b_add.setToolTip("" if self.b_add.isEnabled() else _(
            "{n} accounts at most.", n=SOBER_ACCOUNTS_MAX))
        self.b_rename.setEnabled(acc is not None)
        self.b_remove.setEnabled(acc is not None and not is_open)
        self.b_open.setEnabled(acc is not None and not is_open)
        self.b_close.setEnabled(acc is not None and is_open
                                and acc.id not in self._closing)

    @staticmethod
    def _fatal_of(acc):
        """A primeira linha FATAL do Sober desta abertura (o launch.log é
        recriado a cada uma), ou "". Ele fica aberto com a janela de erro
        depois dela."""
        for line in acc.log_tail(1 << 14).splitlines():
            if line.startswith("FATAL:") and "Previous error" not in line \
                    and "Aborting" not in line:
                return line[len("FATAL:"):].strip()[:200]
        return ""

    def refresh(self):
        """Olha a trava de cada conta e acompanha o que mudou."""
        now = time.monotonic()
        opened = set()
        for acc in self.accounts:
            if acc.running():
                opened.add(acc.id)
            was_open = acc.id in self.open_ids
            if acc.id in opened or was_open:
                fatal = self._fatal_of(acc)
                if fatal and self._fatal.get(acc.id) != (acc.started, fatal):
                    self._fatal[acc.id] = (acc.started, fatal)
                    self._opening = None
                    self._say(_("Sober stopped in {name}: {why}",
                                name=acc.name, why=fatal), True)
                elif self._opening == acc.id and any(
                        k in acc.log_tail(1 << 16) for k in (
                            "did_handle_app_startup",
                            "starting onboarding flow")):
                    self._opening = None
                    self._say(_("{name} is open.", name=acc.name))
                if was_open and acc.id not in opened and not fatal \
                        and acc.id not in self._closing:
                    # fechou sozinha (a janela do Sober, ou fora daqui)
                    self._opening = None
                    self._say(_("{name} is closed.", name=acc.name))
            p = acc.proc
            if p is not None and p.poll() is not None and acc.id not in opened:
                acc.proc = None
                code = p.returncode
                if code == SOBER_BUSY:
                    self._say(_("{name} is already open.", name=acc.name))
                elif code and now - acc.started < self.FAST_FAIL_S:
                    tail = acc.log_tail().splitlines()[-3:]
                    self._say(_("Sober did not open in {name}: {why}",
                                name=acc.name,
                                why=" ".join(tail) or _("exit code {n}",
                                                         n=code)), True)
            if acc.id in self._closing:
                if acc.id not in opened:
                    del self._closing[acc.id]
                    self._say(_("{name} is closed.", name=acc.name))
                elif now - self._closing[acc.id] > self.CLOSE_S:
                    del self._closing[acc.id]
                    self._say(_("{name} did not close. Close its window.",
                                name=acc.name), True)
        moved = opened != self.open_ids
        self.open_ids = opened
        for i, acc in enumerate(self.accounts):
            item = self.list.item(i)
            if item is not None and item.text() != self._text(acc):
                item.setText(self._text(acc))
        if self.list.count() != len(self.accounts):
            self.fill()
        self._sync()
        if moved:
            self.state_changed.emit()

    def _say(self, text, bad=False):
        self.status.setText(text)
        self.status.setStyleSheet("color:#d04030;" if bad else "")
        # vazia, a linha só roubaria altura do texto de cima
        self.status.setVisible(bool(text))

    # ------------------------------------------------------------ ações --
    def _ask_name(self, title, text):
        name, ok = QInputDialog.getText(self, title, _("Name:"),
                                        QLineEdit.Normal, text)
        name = " ".join((name or "").split())[:40]
        return name if ok and name else None

    def add(self):
        if len(self.accounts) >= SOBER_ACCOUNTS_MAX:
            return None
        names = {a.name for a in self.accounts}
        n = 2                       # a conta 1 é a do Sober de sempre
        while _("Account {n}", n=n) in names:
            n += 1
        name = self._ask_name(_("Add account"), _("Account {n}", n=n))
        if name is None:
            return None
        ids = {a.id for a in self.accounts}
        acc_id = os.urandom(4).hex()
        while acc_id in ids:
            acc_id = os.urandom(4).hex()
        acc = SoberAccount(acc_id, name)
        self.accounts.append(acc)
        self.fill(select=acc.id)
        self._say(_("Added {name}. Open it, and Sober sets itself up there "
                    "the first time.", name=name))
        self.changed.emit()
        return acc

    def rename(self):
        acc = self.selected()
        if acc is None:
            return
        name = self._ask_name(_("Rename account"), acc.name)
        if name is None or name == acc.name:
            return
        acc.name = name
        self.fill(select=acc.id)
        self.changed.emit()

    def remove(self):
        acc = self.selected()
        if acc is None or acc.running():
            return
        size = human_bytes(acc.disk_bytes())
        ans = QMessageBox.question(
            self, _("Remove account"),
            _("Remove {name}? Its Sober data on this computer is deleted: "
              "the Roblox sign-in, the settings and the downloads ({size}). "
              "The Roblox account itself stays as it is.", name=acc.name,
              size=size), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        try:
            acc.remove_data()
        except OSError as e:
            self._say(_("{name} was not removed: {err}", name=acc.name,
                        err=e), True)
            return
        self.accounts.remove(acc)
        self.fill()
        self._say(_("Removed {name}.", name=acc.name))
        self.changed.emit()

    def sober_env(self):
        if self.env is not None:
            return self.env
        env = dict(os.environ)
        for k in ("XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID",
                  "WAYLAND_SOCKET"):
            env.pop(k, None)
        return env

    def open_selected(self):
        acc = self.selected()
        if acc is None or acc.running():
            return False
        why = sober_tools_missing()
        if not why and not sober_installed():
            why = _("Sober is not installed. Install it from Flathub "
                    "(org.vinegarhq.Sober) and open it once.")
        if why:
            self._say(why, True)
            return False
        try:
            acc.launch(["flatpak", "run", SOBER_APP], env=self.sober_env(),
                       prefix=self.prefix, home=self.home)
        except OSError as e:
            self._say(_("Sober did not open in {name}: {why}", name=acc.name,
                        why=e), True)
            return False
        self._say(_("Opening {name}… its Sober window shows up in a few "
                    "seconds.", name=acc.name))
        self._opening = acc.id
        self._fatal.pop(acc.id, None)
        QTimer.singleShot(300, self.refresh)
        return True

    def close_selected(self):
        acc = self.selected()
        if acc is None or not acc.running():
            return False
        if not acc.close(env=self.env):
            self._say(_("{name} did not close. Close its window.",
                        name=acc.name), True)
            return False
        self._closing[acc.id] = time.monotonic()
        self._say(_("Closing {name}…", name=acc.name))
        self.refresh()
        return True

    def retranslate(self):
        self.hint.setText(_(SOBER_PAGE_HINT))
        self.risk.setText(_(SOBER_RISK))
        self.b_add.setText(_("Add account…"))
        self.b_rename.setText(_("Rename…"))
        self.b_remove.setText(_("Remove…"))
        self.b_open.setText(_("Open"))
        self.b_close.setText(_("Close"))
        self.b_open.setToolTip(_("Open the Sober of this account on the "
                                 "desktop"))
        self.fill()

    def shutdown(self):
        self.timer.stop()


MODES = {"No trigger (hotkey toggles)": "toggle",
         "Runs while the trigger is held": "hold"}
CFG = os.path.join(XDG_CONFIG, "wayclick.json")


class App(QWidget):
    def __init__(self):
        super().__init__()
        self._state = ""
        self._pulse = 0
        self._burst = None
        self.mouse = None
        self.keyboard = None
        self.clicker = None
        self.keymacros = []
        self.antiafk = None
        self.running = False
        self._macro_editing = False

        cfg = {}
        # aproveita a config do nome antigo do projeto, se existir
        old = os.path.join(XDG_CONFIG, "autoclick-wayland.json")
        src = CFG if os.path.exists(CFG) else old
        if os.path.exists(src):
            try:
                with open(src) as fh:
                    cfg = json.load(fh)
            except Exception:
                pass

        global LANG
        LANG = cfg.get("language") or default_language()
        self.update_auto = bool(cfg.get("update_check", True))
        self._update_tag = cfg.get("update_tag", "")
        self._update_url = cfg.get("update_url") or HOMEPAGE
        self._update_last = float(cfg.get("update_last", 0) or 0)
        self._update_hidden = False      # o × vale só nesta sessão
        self._update_told = False
        self._tray_start = False         # só --tray avisa pela bandeja
        self._manual_check = False
        self.theme = cfg.get("theme", "System")
        self.tray_style = cfg.get("tray_style", "Cursor")
        self.tray_color = cfg.get("tray_color", "Match state")
        apply_theme(self.theme)
        self.setWindowTitle(_("WayClick"))

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 1000.0)
        self.interval.setDecimals(1)
        self.interval.setSingleStep(1.0)
        self.interval.setSuffix(" ms")
        self.interval.setValue(cfg.get("interval_ms", 100.0))
        self.rate = QLabel()
        self.interval.valueChanged.connect(self._show_rate)
        self.btn_sel = QComboBox()
        for name, code in BTN.items():
            self.btn_sel.addItem(_(name), name)
        self.btn_sel.setCurrentIndex(
            max(0, self.btn_sel.findData(cfg.get("button", "Left"))))
        self.delay = QSpinBox(); self.delay.setRange(0, 10)
        self.delay.setValue(cfg.get("delay", 3)); self.delay.setSuffix(" s")
        self.duration = QSpinBox(); self.duration.setRange(0, 3600)
        self.duration.setValue(cfg.get("duration", 0))
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText(_("never"))
        self.dev = QComboBox()
        self.dev.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.dev.setMinimumContentsLength(22)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.setToolTip(_("Rescan mice"))
        self.refresh_btn.clicked.connect(lambda: self.refresh_mice(keep=True))
        dev_row = QHBoxLayout()
        dev_row.addWidget(self.dev, 1)
        dev_row.addWidget(self.refresh_btn)
        self._wanted_mouse = cfg.get("mouse_name")
        self.refresh_mice()
        self.dev.currentIndexChanged.connect(self._mouse_changed)

        # qualquer tecla, capturada apertando, igual aos outros campos de
        # tecla. A lista de 16 que existia aqui não deixava usar Q, E ou Shift
        # como gatilho, que é exatamente o caso de "segurar X e repetir Y".
        self.hk = KeyCatcher(cfg.get("hotkey_code")
                             or KEYNAMES.get(cfg.get("hotkey", "F8"), 66),
                             cfg.get("hotkey", "F8"))
        # o AutoHotkey esconde o atalho do programa (F1:: some com o F1 do
        # jogo); aqui é opcional, porque o atalho de armar sempre deixou a
        # tecla passar e quem já usa não deve ser surpreendido
        self.hk_block = QCheckBox(_("Hide it from other apps"))
        self.hk_block.setChecked(bool(cfg.get("hotkey_block", False)))
        self.hk_block.setToolTip(_(
            "The hotkey still arms and disarms, but the game or app in front "
            "never receives it."))
        self.mode = QComboBox()
        for label, key in MODES.items():
            self.mode.addItem(_(label), key)
        old = cfg.get("mode", "toggle")
        self.mode.setCurrentIndex(max(0, self.mode.findData(
            "hold" if old in ("hold", "hotkey_hold", "mouse_hold") else "toggle")))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.sound = QCheckBox(_("Sound feedback on hotkey"))
        self.sound.setChecked(cfg.get("sound", True))
        self.autostart = QCheckBox(_("Start with system"))
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)

        self._labels = []            # (widget, texto-fonte) para retraduzir
        self._hints = []

        # --- macro de teclado: uma tecla por linha ---
        self.key_rows = []
        self.key_list = QVBoxLayout()
        self.key_list.setContentsMargins(0, 0, 0, 0)
        self.key_add = QPushButton(_("+ Add key"))
        self.key_add.clicked.connect(lambda: self._add_key_row())
        for spec in self._key_specs(cfg):
            self._add_key_row(spec)
        click_form = QFormLayout()
        self._row(click_form, "Mouse:", dev_row)
        self._row(click_form, "Interval:", self.interval)
        click_form.addRow("", self.rate)
        self._row(click_form, "Button:", self.btn_sel)
        self.click_box = QCheckBox(_("Enable clicking"))
        self.click_box.setChecked(cfg.get("click_enabled", True))

        key_form = QVBoxLayout()
        head = QHBoxLayout()
        for text, width in (("Key:", 0), ("Interval:", 96), ("Action:", 96)):
            lbl = QLabel(_(text))
            self._labels.append((lbl, text))
            if width:
                lbl.setFixedWidth(width)
                head.addWidget(lbl)
            else:
                head.addWidget(lbl, 1)
        head.addSpacing(34)          # alinha com a coluna do botão de remover
        key_form.addLayout(head)
        key_form.addLayout(self.key_list)
        add_row = QHBoxLayout()
        add_row.addWidget(self.key_add)
        add_row.addStretch()
        key_form.addLayout(add_row)
        self.key_box = QCheckBox(_("Enable the keyboard macro"))
        self.key_box.setChecked(cfg.get("key_enabled", False))
        self.key_box.toggled.connect(self._key_box_toggled)
        self.click_box.toggled.connect(self._click_box_toggled)

        # O gatilho: uma tecla OU um botão do mouse, capturado apertando. Era
        # a pergunta que a UI não respondia — "quero o clique esquerdo" não
        # tinha onde ser dito sem antes acertar o modo, e o campo do atalho,
        # que é o nome óbvio para procurar, só aceitava tecla.
        #
        # Abaixo dele, quem o gatilho segura: sem isso o modo é tudo ou nada, e
        # "segurar o botão para clicar com a macro rodando o tempo todo" — ou o
        # contrário — não existia.
        self.trigger = KeyCatcher(*self._trigger_spec(cfg), allow_mouse=True)
        self.trigger.changed.connect(self._trigger_changed)

        self.trig_boxes = {}
        scope_row = QHBoxLayout()
        scope = cfg.get("trigger_scope") or {}
        for name, label in (("click", "Click"), ("key", "Keyboard"),
                            ("window", "Window")):
            box = QCheckBox(_(label))
            box.setChecked(bool(scope.get(name, True)))
            box.toggled.connect(self._scope_changed)
            self.trig_boxes[name] = box
            self._labels.append((box, label))
            scope_row.addWidget(box)
        scope_row.addStretch()

        form = QFormLayout()
        self._row(form, "Mode:", self.mode)
        self.trig_lbl = self._row(form, "Trigger:", self.trigger)
        self.scope_lbl = self._row(form, "Trigger holds:", scope_row)
        self._row(form, "Start delay:", self.delay)
        self._row(form, "Auto-stop after:", self.duration)
        # a caixa vai na linha de baixo: ao lado do atalho, o texto em
        # português alargava a página toda e a barra lateral perdia os nomes
        self._row(form, "Arm/disarm hotkey:", self.hk)
        form.addRow("", self.hk_block)
        trigger_form = form

        # --- macros com atalho, como scripts do AutoHotkey ---
        self.macros = [normalize_macro(m) for m in
                       (cfg.get("macros") or [])
                       if isinstance(m, dict)][:MAX_MACROS]
        self.macro_box = QCheckBox(_("Enable hotkey macros"))
        self.macro_box.setChecked(bool(cfg.get("macros_enabled", True)))
        # o Suspend do AutoHotkey (F7::Suspend): uma tecla que liga e desliga
        # os macros, de dentro do jogo
        self.suspend_mods = sorted({x for x in (cfg.get("suspend_mods") or [])
                                    if x in MODIFIERS}) \
            if isinstance(cfg.get("suspend_mods"), list) else []
        scode = _num(cfg.get("suspend_code", 0), 0, 0, 0x2ff, int)
        if scode and is_button(scode):
            scode = 0
        self.suspend_key = KeyCatcher(scode, self._suspend_name(
            scode, self.suspend_mods, cfg.get("suspend_key")))
        self.suspend_key.changed.connect(self._suspend_picked)
        self.suspend_clear = QPushButton("×")
        self.suspend_clear.setFixedWidth(30)
        self.suspend_clear.clicked.connect(self._suspend_cleared)
        self.suspend_clear.setEnabled(bool(scode))
        self.macro_list = QListWidget()
        self.macro_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.macro_list.itemChanged.connect(self._macro_item_changed)
        self.macro_list.itemDoubleClicked.connect(
            lambda _i: self._edit_macro())
        self.macro_list.currentRowChanged.connect(self._sync_macro_buttons)
        self.m_new = QPushButton(_("New"))
        self.m_edit = QPushButton(_("Edit"))
        self.m_del = QPushButton(_("Remove"))
        self.m_import = QPushButton(_("Import AHK…"))
        self.m_import.setToolTip(_("Turn the hotkeys of an AutoHotkey script "
                                   "(.ahk) into macros"))
        self.m_farm = QPushButton(_("Farm assistant…"))
        self.m_export = QPushButton(_("Export…"))
        self.m_load = QPushButton(_("Import…"))
        self.m_new.clicked.connect(self._new_macro)
        self.m_edit.clicked.connect(self._edit_macro)
        self.m_del.clicked.connect(self._remove_macro)
        self.m_import.clicked.connect(self._import_ahk)
        self.m_farm.clicked.connect(self._farm)
        self.m_export.clicked.connect(self._export_macro)
        self.m_load.clicked.connect(self._import_macros)
        self._macro_tips()
        mrow = QHBoxLayout()
        for b in (self.m_new, self.m_edit, self.m_del):
            mrow.addWidget(b)
        mrow.addStretch()
        mrow.addWidget(self.m_import)
        mrow2 = QHBoxLayout()
        mrow2.addWidget(self.m_farm)
        mrow2.addStretch()
        mrow2.addWidget(self.m_export)
        mrow2.addWidget(self.m_load)
        self.macro_state = QLabel("")
        self.macro_state.setWordWrap(True)
        self.macro_clash = QLabel("")
        self.macro_clash.setWordWrap(True)
        self.macro_clash.setStyleSheet("color:#c86000;font-size:11px;")
        self.macro_clash.hide()
        macro_form = QVBoxLayout()
        macro_form.setContentsMargins(0, 0, 0, 0)
        macro_form.addWidget(self.macro_list, 1)
        macro_form.addLayout(mrow)
        macro_form.addLayout(mrow2)
        srow = QHBoxLayout()
        slbl = QLabel(_("Suspend hotkey:"))
        self._labels.append((slbl, "Suspend hotkey:"))
        srow.addWidget(slbl)
        srow.addWidget(self.suspend_key, 1)
        srow.addWidget(self.suspend_clear)
        self._suspend_tips()
        macro_form.addLayout(srow)
        macro_form.addWidget(self.macro_clash)
        macro_form.addWidget(self.macro_state)

        # --- injeção direcionada a uma janela (beta) ---
        self.win_sel = QComboBox()
        self.win_sel.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.win_sel.setMinimumContentsLength(22)
        self._scrollable(self.win_sel)
        self.win_refresh = QPushButton("↻")
        self.win_refresh.setFixedWidth(30)
        self.win_refresh.clicked.connect(self.refresh_windows)
        win_row = QHBoxLayout()
        win_row.addWidget(self.win_sel, 1)
        win_row.addWidget(self.win_refresh)
        self.win_key = KeyCatcher(cfg.get("target_key_code", KEYS["Space"]),
                                  cfg.get("target_key", "Space"))
        self.win_sel.currentIndexChanged.connect(self._update_win_warn)
        self.win_secs = QSpinBox(); self.win_secs.setRange(1, 3600)
        self.win_secs.setValue(cfg.get("target_seconds", 60))
        self.win_secs.setSuffix(" s")
        self.win_secs.valueChanged.connect(self._target_restart)

        self.win_warn = QLabel("")
        self.win_warn.setWordWrap(True)
        self.win_warn.setStyleSheet("color:#d04030;font-size:11px;")
        self.win_warn.setTextInteractionFlags(Qt.TextSelectableByMouse)

        target_form = QFormLayout()
        self._row(target_form, "Window:", win_row)
        self._row(target_form, "Key:", self.win_key)
        self._row(target_form, "Every:", self.win_secs)
        # o aviso fica fora do form: QLabel com quebra de linha dentro de
        # QFormLayout não calcula a altura e sai cortado
        self.target_box = QCheckBox(_("Enable sending the key"))
        self.target_box.setChecked(False)
        self.target_box.toggled.connect(self._target_toggled)

        # --- anti-AFK (independente do Start) ---
        self.afk = QCheckBox(_("Anti-AFK: nudge the cursor every"))
        self.afk.setChecked(False)
        self.afk_secs = QSpinBox(); self.afk_secs.setRange(1, 600)
        self.afk_secs.setValue(cfg.get("afk_seconds", 1)); self.afk_secs.setSuffix(" s")
        self.afk.toggled.connect(self._afk_toggled)
        self.afk_secs.valueChanged.connect(self._afk_restart)
        afk_row = QHBoxLayout()
        afk_row.addWidget(self.afk)
        afk_row.addWidget(self.afk_secs)
        afk_row.addStretch()

        self.status = QLabel(_("Stopped"))
        self.status.setAlignment(Qt.AlignCenter)
        self.btn = QPushButton(self._start_label())
        self.btn.setMinimumHeight(46)
        self.btn.clicked.connect(lambda: self.set_running(not self.running))

        self.warn = QLabel(""); self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#c86000;font-size:11px;")
        self.warn.setTextInteractionFlags(Qt.TextSelectableByMouse)


        checks = QHBoxLayout()
        checks.addWidget(self.sound)
        checks.addWidget(self.autostart)
        checks.addStretch()

        # --- as contas do Sober (a página delas e o combo Conta da Doca) ---
        self.sober_accounts = [
            SoberAccount(d["id"], d["name"])
            for d in normalize_sober_accounts(cfg.get("sober_accounts"))]

        # --- a Doca: o jogo numa tela só do WayClick ---
        self.dock = DOCK()
        self.dock_size = QComboBox()
        for dw, dh in DOCK_SIZES:
            self.dock_size.addItem(f"{dw} × {dh}", f"{dw}x{dh}")
        self.dock_size.setCurrentIndex(max(0, self.dock_size.findData(
            str(cfg.get("dock_size") or ""))))
        self.dock.size = self._dock_size_value()
        self.dock_size.currentIndexChanged.connect(self._dock_size_changed)
        cmd = cfg.get("dock_command", DOCK_GAME)
        self.dock_cmd = QLineEdit(cmd if isinstance(cmd, str) else DOCK_GAME)
        self.dock_cmd.setMaxLength(1000)
        self.dock.command = self.dock_cmd.text().strip()
        self.dock_cmd.textChanged.connect(self._dock_cmd_changed)
        self.dock_open = QPushButton("")
        self.dock_open.clicked.connect(self._dock_toggle)
        self.dock_game = QPushButton("")
        self.dock_game.clicked.connect(self._dock_launch)
        self.dock_show = QPushButton("")
        self.dock_show.clicked.connect(self._dock_show)
        dock_row = QHBoxLayout()
        for b in (self.dock_open, self.dock_game, self.dock_show):
            dock_row.addWidget(b)
        dock_row.addStretch()
        self.dock_acct = QComboBox()
        self._fill_dock_accounts(str(cfg.get("dock_account") or ""))
        self.dock_acct.currentIndexChanged.connect(self._dock_acct_changed)
        dock_form = QFormLayout()
        self._row(dock_form, "Size:", self.dock_size)
        self._row(dock_form, "Game:", self.dock_cmd)
        self._row(dock_form, "Account:", self.dock_acct)
        dock_form.addRow(dock_row)
        self.dock_state = QLabel("")
        self.dock_state.setWordWrap(True)
        self.dock_state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dock_note = QLabel("")
        self.dock_note.setWordWrap(True)
        self.dock_note.setStyleSheet("color:#c86000;font-size:11px;")
        self.dock_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dock_note.hide()
        self.dock_hint = QLabel(_(DOCK_TAB_HINT))
        self.dock_hint.setWordWrap(True)
        self.dock_hint.setEnabled(False)
        self._hints.append((self.dock_hint, DOCK_TAB_HINT))

        # --- Tower Defense: o jogo da Doca dentro da janela, e o farm ---
        # numa QScrollArea: o QTabWidget pede o mínimo da maior aba, e sem
        # ela as outras abas não deixariam a janela voltar a 460 px
        self.tower = TowerTab(cfg.get("tower"), self.dock,
                              macros=lambda: self.macros)
        self.tower_scroll = QScrollArea()
        self.tower_scroll.setWidgetResizable(True)
        self.tower_scroll.setFrameShape(QFrame.NoFrame)
        self.tower_scroll.setWidget(self.tower)
        self._small_size = None          # o tamanho de antes da aba crescer
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(2000)
        self._save_timer.timeout.connect(self._save_cfg)
        self.tower.changed.connect(self._save_timer.start)

        # --- as contas do Sober ---
        self.sober_page = SoberPage(self.sober_accounts)
        self.sober_page.changed.connect(self._sober_changed)
        self.sober_page.state_changed.connect(self._sync_tabs)

        # Páginas numa lista à esquerda, em grupos, em vez de tudo empilhado
        # (passava de 900 px de altura) e em vez das abas (com oito páginas
        # elas não cabiam mais numa janela pequena).
        self.tabs = SideNav()
        self.tab_pages = []
        auto, games = "Automation", "Games"
        self.tabs.addTab(self._page(self.click_box, click_form,
                                    _("1000 ms is 1 click/s, 0.1 ms is 10,000. "
                                      "Lower is faster.")), "",
                         nav_icon(["input-mouse"], "C"), auto)
        self.tabs.addTab(self._page(self.key_box, key_form,
                                    _("Click the key field and press the key "
                                      "you want.")), "",
                         nav_icon(["input-keyboard"], "K"), auto)
        self.tabs.addTab(self._page(self.macro_box, macro_form,
                                    _(MACRO_TAB_HINT), grow=True), "",
                         nav_icon(["preferences-desktop-keyboard-shortcut",
                                   "configure-shortcuts"], "H"), auto)
        self.tabs.addTab(self._page(self.target_box, target_form, None,
                                    extra=self.win_warn), "",
                         nav_icon(["preferences-system-windows", "window"],
                                  "W"), auto)
        self.tabs.addTab(self._page(None, trigger_form, None,
                                    extra=[afk_row, checks]), "",
                         nav_icon(["media-playback-start", "player-time"],
                                  "T"), auto)
        self.tabs.addTab(self._page(None, dock_form, None,
                                    extra=[self.dock_state, self.dock_note,
                                           self.dock_hint]), "",
                         nav_icon(["preferences-desktop-display",
                                   "video-display"], "D"), games)
        self.tabs.addTab(self.tower_scroll, "",
                         nav_icon(["applications-games",
                                   "games-config-tiles"], "T"), games)
        self.tabs.addTab(self.sober_page, "",
                         nav_icon(["system-users", "user-group-properties",
                                   "im-user"], "S"), games)
        self._tab_names = ["Click", "Keyboard", "Hotkeys", "Window",
                           "Trigger", "Dock", "Tower Defense",
                           "Sober accounts"]
        self._sync_tabs()
        self.tower.running_changed.connect(self._sync_tabs)
        self.tabs.currentChanged.connect(self._tab_changed)
        for w_ in (self.click_box, self.key_box, self.target_box,
                   self.macro_box):
            w_.toggled.connect(self._sync_tabs)

        # barra de atualização: escondida até existir versão nova
        self.update_bar = QFrame()
        self.update_bar.setFrameShape(QFrame.StyledPanel)
        self.update_lbl = QLabel("")
        self.update_lbl.setWordWrap(True)
        self.update_lbl.setStyleSheet("color:#3daee9;font-weight:bold;")
        self.update_btn = QPushButton(_("Download"))
        self.update_btn.clicked.connect(self._open_update)
        self.update_x = QPushButton("×")
        self.update_x.setFixedWidth(26)
        self.update_x.setToolTip(_("Dismiss until next launch"))
        self.update_x.clicked.connect(self._dismiss_update)
        up_row = QHBoxLayout(self.update_bar)
        up_row.setContentsMargins(8, 4, 8, 4)
        up_row.addWidget(self.update_lbl, 1)
        up_row.addWidget(self.update_btn)
        up_row.addWidget(self.update_x)
        self.update_bar.hide()

        outer = QVBoxLayout(self)
        outer.setMenuBar(self._build_menu())
        outer.addWidget(self.update_bar)
        outer.addWidget(self.tabs, 1)
        outer.addWidget(self.status)
        outer.addWidget(self.btn)
        outer.addWidget(self.warn)
        # larga o bastante para a barra lateral abrir com os nomes: a conta
        # vem das páginas (fonte, estilo e idioma mudam tudo), com folga
        # e alta o bastante para os textos das páginas e a lista inteira,
        # sem passar da tela
        m = outer.contentsMargins()
        width = max(660, self.tabs._need() + m.left() + m.right() + 40)
        height = max(500, outer.totalHeightForWidth(width))
        scr = self.screen()
        if scr is not None:
            height = min(height, max(500, scr.availableGeometry().height()
                                     - 80))
        self.resize(width, height)
        self.setWindowIcon(wayclick_icon())
        self._show_rate()
        self._paint_status()

        # atalho global
        self.watcher = HotkeyWatcher(self.hk.code,
                                     block=self.hk_block.isChecked())
        self.watcher.pressed.connect(self.on_press)
        self.watcher.released.connect(self.on_release)
        self.watcher.start()
        self.hk.changed.connect(self.on_hotkey_change)
        self.hk_block.toggled.connect(self.watcher.set_block)

        # macros com atalho: vivem independentes do Start
        self.engine = MacroEngine(self._macro_outputs, hub=self.watcher.hub)
        self.watcher.shadow = self.engine.claims
        self.suspend_w = SuspendWatcher(self.suspend_key.code,
                                        self.suspend_mods,
                                        hub=self.watcher.hub)
        self.suspend_w.pressed.connect(self._suspend_pressed)
        self.engine.exempt = self.suspend_w.matches
        if self.suspend_key.code:
            self.suspend_w.start()
        self.tower.set_claims(lambda code: self.engine.claims(code)
                              or code == self.hk.code)
        self.engine.changed.connect(self._macros_ran)
        self.engine.toggled.connect(self._macro_toggled)
        self.engine.problem.connect(self._macro_problem)
        self.engine.message.connect(self._macro_message)
        self.engine.screen = SCREEN()
        self.engine.dock = self.dock
        self.dock.changed.connect(self._dock_changed)
        self._show_dock()
        self.pointer = None
        self._track_screens()
        self.macro_box.toggled.connect(lambda _on: self._apply_macros())

        # fallback só com a janela em foco; desligado quando o global funciona,
        # senão a mesma tecla dispararia duas vezes e se anularia
        self.local_sc = QShortcut(self._hk_sequence(), self)
        self.local_sc.setContext(Qt.ApplicationShortcut)
        self.local_sc.activated.connect(lambda: self.set_running(not self.running))
        self.local_sc.setEnabled(not self.watcher.ok)
        QShortcut(QKeySequence("Esc"), self).activated.connect(
            lambda: self.set_running(False))

        self.countdown = QTimer(self); self.countdown.setInterval(1000)
        self.countdown.timeout.connect(self._tick)
        self._left = 0
        self.autostop = QTimer(self); self.autostop.setInterval(1000)
        self.autostop.timeout.connect(self._tick_autostop)
        self._remain = 0

        self.beeper = Beeper()
        self.holder = None      # MouseHold, quando o gatilho é botão
        self.trig_watcher = None  # HotkeyWatcher, quando o gatilho é tecla
        self.bridge = None      # KWinBridge, criado sob demanda
        self.xinject = None     # XInject, para janelas X11
        self.target_hits = 0
        self.target_ms = 0.0
        self.target_timer = QTimer(self)
        self.target_timer.timeout.connect(self._target_tick)
        self._build_tray()

        # cria o mouse virtual já no início: o UI_DEV_CREATE precisa de ~0,4 s
        # de settle e travaria a UI se fosse feito no clique de Start
        self.ensure_mouse()
        self._fill_macros()
        self._apply_macros()

        if not self.watcher.ok:
            member = input_access()[1]
            if member:
                self.warn.setText(_(
                    "Global hotkey OFF: you are in the 'input' group but this "
                    "session started before that, so it has no access yet. "
                    "Log out and back in, or run:  sg input -c '{cmd}'",
                    cmd=f"python3 {os.path.abspath(__file__)}"))
            else:
                self.warn.setText(_(
                    "Global hotkey OFF (no access to /dev/input). Run this, "
                    "then log out and back in:\nsudo usermod -aG input $USER\n"
                    "Until then the hotkey only works with this window "
                    "focused."))
        self._hint()
        self._sync_scope()

        # o aviso aparece já com o que estava em cache: quem instalou por
        # pacote não tem outro lugar onde essa informação chegue. A consulta
        # em si é no máximo uma por dia, e nunca atrasa a abertura.
        self.updater = UpdateCheck()
        self.updater.done.connect(self._update_done)
        self._show_update()
        if (AUTO_UPDATE_CHECK and self.update_auto
                and time.time() - self._update_last > UPDATE_EVERY):
            self.updater.start()

    # ---------------------------------------------------------- helpers --
    def _rate_str(self):
        cps = 1000.0 / self.interval.value()
        n = f"{cps:,.0f}" if cps >= 10 else f"{cps:.1f}"
        return f"{n} " + _("clicks/s")

    def _show_rate(self):
        self.rate.setText("= " + self._rate_str())
        self.rate.setStyleSheet("color:#7a7a7a;font-size:11px;")

    def _hint(self):
        if self.warn.text() and not getattr(self, "_hinted", False):
            return
        self._hinted = True
        hk, trig = self.hk.name, self.trigger.name
        if self.mode_key() != "hold":
            self.warn.setText(_(
                "Move the cursor off this window before starting — otherwise "
                "it clicks itself. That is what the {d}s start delay is for. "
                "{hk} toggles; Esc stops.", d=self.delay.value(), hk=hk))
        elif self._should_swallow():
            self.warn.setText(_(
                "Arm, then hold {trig}: it is captured and becomes the click "
                "stream. {hk} arms/disarms; Esc disarms.", trig=trig, hk=hk))
        else:
            self.warn.setText(_(
                "Arm, then hold {trig} and it runs; let go and it stops. "
                "{hk} arms/disarms; Esc disarms.", trig=trig, hk=hk))
        # o atalho liga e desliga em QUALQUER janela, lido do /dev/input: numa
        # tecla que também escreve, cada vez que ela for digitada em qualquer
        # lugar a macro liga ou desliga
        if not is_button(self.hk.code) and self.hk.code not in SAFE_HOTKEYS:
            self.warn.setText(self.warn.text() + "\n" + _(
                "{hk} also types: it arms and disarms every time you press it "
                "in any window. Pick a key that types nothing (F6-F12, Insert, "
                "Pause, ScrollLock).", hk=hk))

    def _page(self, enable, form, hint=None, extra=None, grow=False):
        """Uma aba: caixa de habilitar (quando a aba é uma engine), o
        formulário, e o que mais vier. Desabilitar a caixa apaga os campos,
        como o QGroupBox marcável fazia antes."""
        page = QWidget()
        col = QVBoxLayout(page)
        if enable is not None:
            col.addWidget(enable)
        fields = QWidget()
        fields.setLayout(form)
        col.addWidget(fields, 1 if grow else 0)
        if enable is not None:
            enable.toggled.connect(fields.setEnabled)
            fields.setEnabled(enable.isChecked())
        if hint:
            lbl = QLabel(hint)
            lbl.setWordWrap(True)
            lbl.setEnabled(False)          # cinza pela paleta, não hardcoded
            col.addWidget(lbl)
            self._hints.append((lbl, hint))
        for item in (extra if isinstance(extra, list) else [extra] if extra else []):
            if isinstance(item, QWidget):
                col.addWidget(item)
            elif item is not None:
                col.addLayout(item)
        if not grow:
            col.addStretch()
        self.tab_pages.append(page)
        return page

    def _tab_changed(self, _i=0):
        """A aba Tower Defense precisa de espaço para o jogo: a janela cresce
        ao entrar nela e volta ao tamanho de antes ao sair."""
        tds = self.tabs.currentWidget() is self.tower_scroll
        big = self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen)
        if tds and self._small_size is None and not big:
            want_w, want_h = TOWER_WINDOW
            scr = self.screen()
            if scr is not None:
                g = scr.availableGeometry()
                want_w = min(want_w, g.width() - 40)
                want_h = min(want_h, g.height() - 60)
            if self.width() < want_w or self.height() < want_h:
                self._small_size = self.size()
                self.resize(max(self.width(), want_w),
                            max(self.height(), want_h))
        elif not tds and self._small_size is not None:
            size, self._small_size = self._small_size, None
            if not big:
                self.resize(size)
        self._bottom()

    def _bottom(self):
        """Na aba Tower Defense, o Start de baixo (o do autoclicker) sai de
        cena enquanto está parado: ali o Start é o do farm."""
        if not hasattr(self, "tower_scroll"):
            return
        hide = (self.tabs.currentWidget() is self.tower_scroll
                and not self.running and not getattr(self, "_state", ""))
        self.status.setVisible(not hide)
        self.btn.setVisible(not hide)
        w = getattr(self, "watcher", None)
        self.warn.setVisible(not hide or (w is not None and not w.ok))

    def _sync_tabs(self):
        """Marca a aba cuja engine está ligada, para saber sem abrir."""
        # a aba do gatilho também marca: um modo que não é "liga e desliga"
        # decide se as outras três chegam a rodar, e isso precisa ser visível
        # sem abrir a aba
        on = [self.click_box.isChecked(), self.key_box.isChecked(),
              self.macro_box.isChecked() and any(
                  m["enabled"] for m in getattr(self, "macros", [])),
              self.target_box.isChecked(), self.mode_key() != "toggle",
              getattr(self, "dock", None) is not None and self.dock.alive(),
              hasattr(self, "tower") and self.tower.running(),
              hasattr(self, "sober_page") and bool(self.sober_page.open_ids)]
        for i, name in enumerate(self._tab_names):
            mark = "● " if on[i] else ""
            self.tabs.setTabText(i, mark + _(name))

    # ------------------------------------------------------------- a Doca --
    def _dock_size_value(self):
        try:
            w, h = (int(x) for x in
                    str(self.dock_size.currentData()).split("x"))
        except ValueError:
            return DOCK_SIZES[0]
        return (w, h)

    def _dock_size_changed(self, _i=0):
        self.dock.set_size(*self._dock_size_value())
        self._show_dock()

    def _account(self, acc_id):
        for acc in self.sober_accounts:
            if acc.id == acc_id:
                return acc
        return None

    def _fill_dock_accounts(self, keep=None):
        """O combo Conta da Doca: o Sober de sempre e as contas do Sober."""
        if keep is None:
            keep = self.dock_acct.currentData() or ""
        self.dock_acct.blockSignals(True)
        self.dock_acct.clear()
        self.dock_acct.addItem(_("Your usual Sober"), "")
        for acc in self.sober_accounts:
            self.dock_acct.addItem(acc.name, acc.id)
        self.dock_acct.setCurrentIndex(max(0, self.dock_acct.findData(keep)))
        self.dock_acct.blockSignals(False)
        self.dock_acct.setToolTip(_(
            "Which Sober the Dock opens: your usual one, or one of the "
            "accounts of the Sober accounts page. Other games ignore it."))
        self.dock.account = self._account(self.dock_acct.currentData() or "")

    def _dock_acct_changed(self, _i=0):
        self.dock.account = self._account(self.dock_acct.currentData() or "")
        self.dock.note = ""
        self._show_dock()
        self._save_timer.start()

    def _sober_changed(self):
        """Contas novas, renomeadas ou removidas: o combo da Doca segue, e a
        configuração é gravada logo."""
        self._fill_dock_accounts()
        self._show_dock()
        self._sync_tabs()
        self._save_timer.start()

    def _dock_cmd_changed(self, text):
        self.dock.command = text.strip()
        self._show_dock()

    def _dock_toggle(self):
        if self.dock.state == "starting":
            return
        if self.dock.alive():
            self.engine.stop_dock()
            self.dock.stop()
            return
        self.dock.size = self._dock_size_value()
        self.dock.command = self.dock_cmd.text().strip()
        self.dock.start(launch=bool(self.dock.command))

    def _dock_launch(self):
        if self.dock.state == "starting":
            return
        self.dock.command = self.dock_cmd.text().strip()
        if self.dock.alive():
            self.dock.launch()
        else:
            self.dock.size = self._dock_size_value()
            self.dock.start(launch=True)

    def _dock_show(self):
        if self.dock.show() is None and self.dock.alive():
            self.dock.note = _("KWin did not find the Dock window.")
            self._show_dock()

    def _dock_changed(self):
        if not self.dock.alive():
            self.engine.stop_dock()
        self._show_dock()
        # os macros "sempre" da Doca começam quando ela abre
        self._apply_macros()

    def _show_dock(self):
        d = self.dock
        st = d.state
        if st == "on":
            w, h = d.out_size or d.size
            text = _("Open, {w}×{h}.", w=w, h=h) + " " + (
                _("The game is running.") if d.game_running()
                else _("No game is running in it."))
        elif st == "starting":
            text = _("Opening the Dock…")
        elif st == "failed":
            text = d.error or _("The Dock did not open.")
        else:
            text = _("Closed.")
        self.dock_state.setText(text)
        self.dock_state.setStyleSheet("color:#d04030;" if st == "failed"
                                      else "")
        self.dock_note.setText(d.note)
        self.dock_note.setVisible(bool(d.note))
        on = st == "on"
        self.dock_open.setText(_("Close the Dock") if on
                               else _("Open the Dock"))
        self.dock_open.setEnabled(st != "starting")
        self.dock_game.setText(_("Start the game"))
        self.dock_game.setEnabled(st != "starting"
                                  and bool(self.dock_cmd.text().strip()))
        self.dock_show.setText(_("Show"))
        self.dock_show.setToolTip(_("Bring the Dock window to the front"))
        self.dock_show.setEnabled(on)
        if hasattr(self, "tabs"):
            self._sync_tabs()

    @staticmethod
    def _scrollable(combo, visible=5):
        """Popup com no máximo `visible` itens e rolagem. Trocar a view por uma
        QListView faz o Qt usar o popup de item view, que respeita
        maxVisibleItems — via stylesheet (combobox-popup:0) também funciona, mas
        aí a combo perde o desenho nativo e fica branca em tema escuro."""
        combo.setView(QListView())
        combo.setMaxVisibleItems(visible)

    # -------------------------------------------------------- menu/i18n --
    def _row(self, form, text, widget):
        lbl = QLabel(_(text))
        self._labels.append((lbl, text))
        form.addRow(lbl, widget)
        return lbl

    def _build_menu(self):
        bar = QMenuBar()
        self.m_file = bar.addMenu(_("File"))
        self.act_run = self.m_file.addAction(self._start_label())
        self.act_run.triggered.connect(lambda: self.set_running(not self.running))
        self.act_hide = self.m_file.addAction(_("Hide to tray"))
        self.act_hide.triggered.connect(self.hide)
        self.m_file.addSeparator()
        self.act_quit = self.m_file.addAction(_("Quit"))
        self.act_quit.triggered.connect(QApplication.instance().quit)

        self.m_set = bar.addMenu(_("Settings"))
        self.m_theme = self.m_set.addMenu(_("Theme"))
        self.theme_acts = {}
        for name in ["System"] + sorted(color_schemes()):
            act = self.m_theme.addAction(_(name))
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, n=name: self._set_theme(n))
            self.theme_acts[name] = act
        self.m_tray = self.m_set.addMenu(_("Tray icon"))
        self.m_style = self.m_tray.addMenu(_("Shape"))
        self.style_acts = {}
        for name in TRAY_STYLES:
            act = self.m_style.addAction(_(name))
            act.setCheckable(True)
            act.setChecked(name == self.tray_style)
            act.triggered.connect(lambda _c=False, n=name: self._set_tray_style(n))
            self.style_acts[name] = act
        self.m_tcolor = self.m_tray.addMenu(_("Color"))
        self.color_acts = {}
        for name in TRAY_COLORS:
            act = self.m_tcolor.addAction(_(name))
            act.setCheckable(True)
            act.setChecked(name == self.tray_color)
            act.triggered.connect(lambda _c=False, n=name: self._set_tray_color(n))
            self.color_acts[name] = act

        self.m_lang = self.m_set.addMenu(_("Language"))
        self.lang_acts = {}
        for label, code in LANGS.items():
            act = self.m_lang.addAction(label)     # nome do idioma não traduz
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, x=code: self._set_language(x))
            self.lang_acts[code] = act
        self.m_set.addSeparator()
        self.act_sound = self.m_set.addAction(_("Sound feedback on hotkey"))
        self.act_sound.setCheckable(True)
        self.act_sound.toggled.connect(self.sound.setChecked)
        self.sound.toggled.connect(self.act_sound.setChecked)
        self.act_auto = self.m_set.addAction(_("Start with system"))
        self.act_auto.setCheckable(True)
        self.act_auto.toggled.connect(self.autostart.setChecked)
        self.autostart.toggled.connect(self.act_auto.setChecked)
        self.act_updates = self.m_set.addAction(_("Check for updates on start"))
        self.act_updates.setCheckable(True)
        self.act_updates.setChecked(self.update_auto)
        self.act_updates.toggled.connect(self._set_update_auto)

        for n, act in self.theme_acts.items():
            act.setChecked(n == self.theme)
        for c, act in self.lang_acts.items():
            act.setChecked(c == LANG)
        self.act_sound.setChecked(self.sound.isChecked())
        self.act_auto.setChecked(self.autostart.isChecked())

        self.m_help = bar.addMenu(_("Help"))
        self.act_update = self.m_help.addAction(_("Check for updates"))
        self.act_update.triggered.connect(self._check_updates_now)
        self.m_help.addSeparator()
        self.act_site = self.m_help.addAction(_("Project on GitHub"))
        self.act_site.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(HOMEPAGE)))
        self.m_help.addSeparator()
        self.act_about = self.m_help.addAction(_("About"))
        self.act_about.triggered.connect(self._about)
        return bar

    def _set_theme(self, name):
        self.theme = name
        for n, act in self.theme_acts.items():
            act.setChecked(n == name)
        apply_theme(name)

    def _set_language(self, code):
        global LANG
        LANG = code
        for c, act in self.lang_acts.items():
            act.setChecked(c == code)
        self.retranslate()

    def _about(self):
        QMessageBox.about(
            self, _("About"),
            f"<b>WayClick {VERSION}</b><br>"
            f"{_('Autoclicker for Wayland using /dev/uinput.')}<br><br>"
            f'<a href="{HOMEPAGE}">{HOMEPAGE}</a>')

    @staticmethod
    def _retext(combo, labels):
        """Retraduz os itens sem mexer na seleção (o valor vive no userData)."""
        keep = combo.currentIndex()
        combo.blockSignals(True)
        for i, src in enumerate(labels):
            combo.setItemText(i, _(src))
        combo.setCurrentIndex(keep)
        combo.blockSignals(False)

    def retranslate(self):
        self.setWindowTitle(_("WayClick"))
        for lbl, src in self._labels:
            lbl.setText(_(src))
        self.click_box.setText(_("Enable clicking"))
        self.key_box.setText(_("Enable the keyboard macro"))
        self.macro_box.setText(_("Enable hotkey macros"))
        self.hk_block.setText(_("Hide it from other apps"))
        self.hk_block.setToolTip(_(
            "The hotkey still arms and disarms, but the game or app in front "
            "never receives it."))
        for b, src in ((self.m_new, "New"), (self.m_edit, "Edit"),
                       (self.m_del, "Remove"), (self.m_import, "Import AHK…"),
                       (self.m_farm, "Farm assistant…"),
                       (self.m_export, "Export…"), (self.m_load, "Import…")):
            b.setText(_(src))
        self.m_import.setToolTip(_("Turn the hotkeys of an AutoHotkey script "
                                   "(.ahk) into macros"))
        self._macro_tips()
        self._suspend_tips()
        if not self.suspend_key.code:
            self.suspend_key.name = _("None")
            self.suspend_key._refresh()
        self._fill_macros()
        self.target_box.setText(_("Enable sending the key"))
        self.afk.setText(_("Anti-AFK: nudge the cursor every"))
        for lbl, src in self._hints:
            lbl.setText(_(src))
        self.tabs.retranslate()
        self.sober_page.retranslate()
        self._fill_dock_accounts()
        self._sync_tabs()
        self._show_dock()
        self.tower.retranslate()
        self._update_win_warn()
        self.sound.setText(_("Sound feedback on hotkey"))
        self.autostart.setText(_("Start with system"))
        self.refresh_btn.setToolTip(_("Rescan mice"))
        self.duration.setSpecialValueText(_("never"))
        self._retext(self.mode, list(MODES.keys()))
        self._retext(self.btn_sel, list(BTN.keys()))
        for row in self.key_rows:
            row.retranslate()
        self.key_add.setText(_("+ Add key"))
        self.m_file.setTitle(_("File"))
        self.m_set.setTitle(_("Settings"))
        self.m_theme.setTitle(_("Theme"))
        self.m_tray.setTitle(_("Tray icon"))
        self.m_style.setTitle(_("Shape"))
        self.m_tcolor.setTitle(_("Color"))
        for name, act in self.style_acts.items():
            act.setText(_(name))
        for name, act in self.color_acts.items():
            act.setText(_(name))
        self.m_lang.setTitle(_("Language"))
        self.m_help.setTitle(_("Help"))
        self.act_hide.setText(_("Hide to tray"))
        self.act_quit.setText(_("Quit"))
        self.act_about.setText(_("About"))
        self.act_site.setText(_("Project on GitHub"))
        self.act_sound.setText(_("Sound feedback on hotkey"))
        self.act_auto.setText(_("Start with system"))
        self.act_updates.setText(_("Check for updates on start"))
        self.act_update.setText(_("Check for updates"))
        self.update_btn.setText(_("Download"))
        self.update_x.setToolTip(_("Dismiss until next launch"))
        self._show_update()
        for name, act in self.theme_acts.items():
            act.setText(_(name))
        self._sync_start_label()
        self.warn.setText("")
        self._hinted = False
        self._hint()
        self.refresh_mice(keep=True)
        self._show_rate()
        self._show_status()
        self._sync_tray()

    # ------------------------------------------------------------- tray --
    def _build_tray(self):
        self.tray = None
        if not SYSTEM_TRAY or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._tray_pixmap(), self)
        menu = QMenu()
        self.act_toggle = QAction(_("Start"), self)
        self.act_toggle.triggered.connect(
            lambda: self.set_running(not self.running))
        self.act_window = QAction(_("Hide window"), self)
        self.act_window.triggered.connect(self._toggle_window)
        quit_act = QAction(_("Quit"), self)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(self.act_toggle)
        menu.addSeparator()
        menu.addAction(self.act_window)
        menu.addAction(quit_act)
        # a janela fechada pelo X só esconde e não avisa ninguém; atualizar os
        # rótulos na abertura do menu evita ter que sobrescrever closeEvent
        menu.aboutToShow.connect(self._sync_tray)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(550)
        self.pulse_timer.timeout.connect(self._pulse_tick)
        self.burst_timer = QTimer(self)
        self.burst_timer.setInterval(70)
        self.burst_timer.timeout.connect(self._burst_tick)
        self.tray.show()
        self._sync_tray()
        # com a bandeja ativa, fechar a janela só esconde; sair é pelo menu
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_window()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self._sync_tray()

    def _tray_state(self):
        state = getattr(self, "_state", "")
        if not state and getattr(self, "engine", None) is not None \
                and self.engine.running():
            return "run"            # um macro com atalho rodando sozinho
        return state

    def _tray_pixmap(self):
        return tray_icon(self._tray_state(), self._pulse,
                         self.tray_style, self.tray_color, self._burst)

    def _pulse_tick(self):
        self._pulse = (self._pulse + 1) % 3
        self.tray.setIcon(self._tray_pixmap())

    def _burst_tick(self):
        """Anel que cresce e some ao ligar ou desligar: feedback de que o
        atalho pegou, mesmo com a janela escondida."""
        self._burst += 1
        if self._burst >= BURST_FRAMES:
            self._burst = None
            self.burst_timer.stop()
        if self.tray:
            self.tray.setIcon(self._tray_pixmap())

    def _burst_start(self):
        if not self.tray:
            return
        self._burst = 0
        self.burst_timer.start()
        self.tray.setIcon(self._tray_pixmap())

    def _set_tray_style(self, name):
        self.tray_style = name
        for n, act in self.style_acts.items():
            act.setChecked(n == name)
        self._sync_tray()

    def _set_tray_color(self, name):
        self.tray_color = name
        for n, act in self.color_acts.items():
            act.setChecked(n == name)
        self._sync_tray()

    def _sync_tray(self):
        if not getattr(self, "tray", None):   # _paint_status roda antes da tray
            return
        state = self._tray_state()
        if state in ("run", "armed"):         # anima só enquanto trabalha
            if not self.pulse_timer.isActive():
                self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
            self._pulse = 0
        label = {"run": _("RUNNING").capitalize(), "wait": _("Starting in {n}s…", n=""),
                 "armed": _("ARMED — hold {btn} mouse button",
                            btn=self.btn_sel.currentText().lower())
                 }.get(state, _("Stopped"))
        self.tray.setIcon(self._tray_pixmap())
        self.tray.setToolTip(f"WayClick — {label}")
        self.act_toggle.setText(_("Stop") if self.running else _("Start"))
        self.act_window.setText(_("Hide window") if self.isVisible()
                                else _("Show window"))

    # ------------------------------------------------------- updates --
    def _set_update_auto(self, on):
        self.update_auto = on
        if on:
            self.updater.start()

    def _check_updates_now(self):
        """Checagem pedida no menu: responde mesmo quando não há novidade —
        silêncio, aqui, é indistinguível de falha."""
        self._manual_check = True
        self._update_hidden = False
        self.updater.start()

    @Slot(str, str)
    def _update_done(self, tag, link):
        if tag:
            self._update_tag, self._update_url = tag, link
            self._update_last = time.time()
        self._show_update()
        if self._manual_check:
            self._manual_check = False
            if not newer_version(self._update_tag):
                QMessageBox.information(
                    self, _("Check for updates"),
                    _("You are up to date ({v}).", v=VERSION) if tag
                    else _("Could not check for updates."))

    def _show_update(self):
        newer = newer_version(self._update_tag)
        if not newer or self._update_hidden:
            self.update_bar.hide()
            return
        text = _("WayClick {v} is available. You have {cur}.",
                 v=newer.lstrip("vV"), cur=VERSION)
        self.update_lbl.setText(text)
        self.update_bar.show()
        # sem janela (--tray) a barra não serve para nada, e o aviso vai pela
        # bandeja. isVisible() não serve para decidir isso: no fim do __init__
        # a janela ainda não foi mostrada, então toda abertura normal cairia
        # aqui e o usuário levaria o popup E a barra.
        if self.tray and self._tray_start and not self._update_told:
            self._update_told = True
            self.tray.showMessage(_("WayClick"), text, wayclick_icon())

    def start_hidden(self):
        """Início só na bandeja: é aqui que o aviso de versão vira notificação."""
        self._tray_start = True
        self.hide()
        self._sync_tray()
        self._show_update()

    def _dismiss_update(self):
        self._update_hidden = True
        self.update_bar.hide()

    def _open_update(self):
        QDesktopServices.openUrl(QUrl(self._update_url or HOMEPAGE))

    def _toggle_autostart(self, on):
        if not set_autostart(on):
            self.warn.setText("Could not write " + AUTOSTART)

    def mode_key(self):
        return self.mode.currentData()

    def _start_label(self):
        """Nos modos com gatilho, apertar o botão não começa nada — só arma, e
        quem dispara é o botão do mouse ou o atalho. Chamar isso de "Start" é o
        que fez a macro de teclado parecer quebrada: o usuário aperta, nada
        acontece, e a explicação está numa aba que ele não tinha por que abrir.
        """
        # com gatilho, apertar não começa nada: deixa o gatilho valer. Sem
        # gatilho, começa mesmo. O botão diz qual dos dois vai acontecer.
        return _("Arm") if self.mode_key() == "hold" else _("Start")

    def _sync_start_label(self):
        if not hasattr(self, "btn"):
            return
        label = _("Stop") if self.running else self._start_label()
        self.btn.setText(label)
        self.act_run.setText(label)

    def _mode_changed(self, _text):
        self.set_running(False)
        self.warn.setText("")
        self._hinted = False
        self._hint()
        self._sync_scope()
        self._sync_tabs()
        self._sync_start_label()

    def _beep(self, which):
        if self.sound.isChecked():
            self.beeper.play(which)

    def _paint_status(self):
        color = STATE_COLORS.get(getattr(self, "_state", ""), STATE_COLORS[""])
        self.status.setStyleSheet(
            f"font-size:17px;font-weight:bold;color:{color};padding:10px;")
        self._sync_tray()
        self._bottom()

    # ------------------------------------------------------------ mouses --
    def refresh_mice(self, keep=False):
        """Lista todos os mouses legíveis. Qualquer marca/quantidade serve."""
        want = self.selected_mouse()[1] if keep else self._wanted_mouse
        self.dev.blockSignals(True)
        self.dev.clear()
        mice = list_mice()
        for path, name in mice:
            self.dev.addItem(f"{name}  ({os.path.basename(path)})", (path, name))
        if not mice:
            self.dev.addItem(_("no mouse detected"), None)
        idx = next((i for i, (_, n) in enumerate(mice) if n == want), 0)
        self.dev.setCurrentIndex(idx)
        self.dev.blockSignals(False)
        self.dev.setEnabled(bool(mice))
        return mice

    def selected_mouse(self):
        data = self.dev.currentData()
        return data if data else (None, None)

    def _mouse_changed(self, _idx):
        """Trocar de mouse recria o device virtual: a identidade clonada tem que
        ser a do mouse que vai ser capturado, senão a sensibilidade não bate."""
        self.set_running(False)
        self._wanted_mouse = self.selected_mouse()[1]
        if self.antiafk:            # a thread guarda o fd antigo
            self.antiafk.stop()
            self.antiafk = None
        self.engine.stop_all()      # os macros também
        if self.mouse:
            self.mouse.close()
            self.mouse = None
        self.ensure_mouse()
        self._afk_restart()
        self._apply_macros()

    def ensure_mouse(self):
        if self.mouse is not None:
            return True
        ok = self._create_mouse()
        if ok and hasattr(self, "engine"):
            path = self.selected_mouse()[0]
            if path:
                self.engine.hub.set_output(path, self.mouse)
        return ok

    def _create_mouse(self):
        # clona o mouse escolhido para herdar velocidade/perfil de aceleração
        # que o usuário configurou; sem mouse acessível, cai no genérico
        path = self.selected_mouse()[0]
        clone, _ = open_devices(is_mouse_any if path else is_mouse,
                                [path] if path else None)
        try:
            self.mouse = VirtualMouse(clone[0].fileno() if clone else None)
        except (PermissionError, OSError) as e:
            self.warn.setText(f"/dev/uinput failed: {e}\n"
                              "sudo usermod -aG input $USER  (then re-login)")
            return False
        finally:
            for f in clone:
                f.close()
        return True

    # ------------------------------------------------------------ estado --
    def set_running(self, on):
        """running = ligado/armado. No modo "segurar", clicar de fato só
        acontece com o botão do mouse pressionado."""
        if on == self.running:
            return
        if on:
            if not self.ensure_mouse():
                return
            if not (self.click_box.isChecked() or self.key_box.isChecked()
                    or self.target_box.isChecked()):
                self.warn.setText(_("Nothing to run: enable Click, Keyboard "
                                    "macro or Send key to a window."))
                return
            self.running = True
            self._sync_start_label()
            self._burst_start()
            self._beep("on")
            self._left = self.delay.value()
            if self._left:
                self._state = "wait"
                self.status.setText(_("Starting in {n}s…", n=self._left))
                self.countdown.start()
            else:
                self._engage()
        else:
            self.running = False
            self.countdown.stop()
            self.autostop.stop()
            self._stop_clicker()
            if self.holder:
                self.holder.stop()
                self.holder = None
            if self.trig_watcher:
                self.trig_watcher.stop()
                self.trig_watcher = None
            self._state = ""
            self._show_status()
            self._sync_start_label()
            self._burst_start()
            self._beep("off")
        self._paint_status()

    def _tick(self):
        self._left -= 1
        if self._left > 0:
            self.status.setText(_("Starting in {n}s…", n=self._left))
        else:
            self.countdown.stop()
            self._engage()

    # ---------------------------------------------------------- engines --
    ENGINES = ("click", "key", "window")

    def _enabled_engines(self):
        return {name for name, box in (("click", self.click_box),
                                       ("key", self.key_box),
                                       ("window", self.target_box))
                if box.isChecked()}

    def _gated_engines(self):
        """O que o gatilho segura. Sem modo que segura não há gatilho, então
        não há nada segurado — e tudo parte assim que se aperta Start."""
        enabled = self._enabled_engines()
        if self.mode_key() != "hold":
            return set()
        return {n for n, box in self.trig_boxes.items()
                if box.isChecked()} & enabled

    def _engage(self):
        """Fim do atraso: o que o gatilho não segura já sai rodando; o resto
        espera o botão."""
        self._remain = self.duration.value()
        if self._remain:
            self.autostop.start()
        gated = self._gated_engines()
        free = self._enabled_engines() - gated
        if free:
            self._start_engines(free)
        if not gated:
            self._state = "run"
            self._show_status()
            self._paint_status()
        elif is_button(self.trigger.code):
            self._arm_hold()             # botão do mouse: precisa da captura
        else:
            self._arm_key_trigger()      # tecla: basta escutar o /dev/input

    def _should_swallow(self):
        """Engolir o gatilho só quando o Clicker reemite ESSE botão: preso ao
        gatilho e sendo o mesmo botão que se clica. Gatilho num botão diferente
        (segurar o lateral para clicar com o esquerdo) não tem quem o reemita,
        e engoli-lo mataria o botão do usuário enquanto armado."""
        return ("click" in self._gated_engines()
                and self.trigger.code == BTN[self.btn_sel.currentData()])

    def _arm_hold(self):
        path = self.selected_mouse()[0]
        self.holder = MouseHold(self.mouse, self.trigger.code,
                                only=[path] if path else None,
                                swallow=self._should_swallow())
        self.holder.pressed.connect(self._trigger_pressed)
        self.holder.released.connect(self._hold_released)
        self.holder.failed.connect(self._hold_failed)
        if not self.holder.start():
            self.holder = None
            return
        self._state = "armed"
        self._show_status()
        self._paint_status()

    def _arm_key_trigger(self):
        """Gatilho numa tecla: um segundo watcher no /dev/input, vivo só
        enquanto armado. Nada de capturar o mouse, que aqui não faz falta."""
        if self.trig_watcher:
            self.trig_watcher.stop()
        self.trig_watcher = HotkeyWatcher(self.trigger.code)
        if not self.trig_watcher.ok:
            self.trig_watcher = None
            self._hold_failed("no access to /dev/input")
            return
        self.trig_watcher.pressed.connect(self._trigger_pressed)
        self.trig_watcher.released.connect(self._hold_released)
        self.trig_watcher.start()
        self._state = "armed"
        self._show_status()
        self._paint_status()

    def _hold_failed(self, msg):
        self.warn.setText(_("Hold mode unavailable: {msg}", msg=_(msg)))
        self.set_running(False)

    def _trigger_pressed(self):
        self._start_engines(self._gated_engines())

    def _hold_released(self):
        """Solta o gatilho: para só o que ele segura. O que roda livre segue."""
        self._stop_engines(self._gated_engines())
        if self.running:
            self._state = "armed" if self._gated_engines() else "run"
            self._show_status()
            self._paint_status()

    def _start_engines(self, names):
        """Liga as engines pedidas, sem mexer nas que já estão rodando."""
        if "click" in names and not self.clicker:
            self.clicker = Clicker(self.mouse, self.interval.value(),
                                   BTN[self.btn_sel.currentData()])
            self.clicker.start()
        if "key" in names and not self.keymacros:
            self._start_key_macros()
        if "window" in names and not self.target_timer.isActive():
            self._target_start()
        self._state = "run"
        self._show_status()
        self._paint_status()

    def _stop_engines(self, names):
        if "window" in names:
            self.target_timer.stop()
        if "click" in names and self.clicker:
            self.clicker.stop()
            self.clicker = None
        if "key" in names:
            self._stop_key_macros()

    def _stop_clicker(self):
        self._stop_engines(self.ENGINES)

    # -------------------------------------------------- teclas da macro --
    MAX_KEYS = 8

    @staticmethod
    def _trigger_spec(cfg):
        """(código, nome) do gatilho. A config antiga guardava o gatilho em dois
        lugares diferentes conforme o modo: o botão em trigger_button/button, a
        tecla em hotkey. Ler os dois evita perder o que já estava configurado."""
        code = cfg.get("trigger_code")
        if not code:
            old = cfg.get("mode", "toggle")
            if old == "hotkey_hold":
                code = cfg.get("hotkey_code") or KEYNAMES.get(
                    cfg.get("hotkey", "F8"), KEYNAMES["F8"])
            else:
                code = BTN.get(cfg.get("trigger_button")
                               or cfg.get("button", "Left"), BTN["Left"])
        return code, cfg.get("trigger_name") or trigger_label(code)

    @staticmethod
    def _key_specs(cfg):
        """Config nova é uma lista de teclas; a antiga tinha uma só, em campos
        soltos. Ler as duas mantém quem atualiza com o que já tinha."""
        rows = cfg.get("keys")
        if isinstance(rows, list) and rows:
            return rows[:App.MAX_KEYS]
        return [{"key": cfg.get("key", "Space"),
                 "key_code": cfg.get("key_code", KEYS["Space"]),
                 "interval_ms": cfg.get("key_interval_ms", 200.0),
                 "mode": cfg.get("key_mode", "Repeat")}]

    def _add_key_row(self, spec=None):
        if len(self.key_rows) >= self.MAX_KEYS:
            return None
        spec = spec or {}
        row = KeyRow(spec.get("key_code", KEYS["Space"]),
                     spec.get("key", "Space"),
                     spec.get("interval_ms", 200.0),
                     spec.get("mode", "Repeat"))
        row.removed.connect(self._remove_key_row)
        row.changed.connect(self._keys_changed)
        self.key_rows.append(row)
        self.key_list.addWidget(row)
        self._sync_key_rows()
        self._keys_changed()
        return row

    def _remove_key_row(self, row):
        if len(self.key_rows) <= 1:
            return                      # a macro sempre tem ao menos uma tecla
        self.key_rows.remove(row)
        self.key_list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._sync_key_rows()
        self._keys_changed()

    def _sync_key_rows(self):
        alone = len(self.key_rows) <= 1
        for r in self.key_rows:
            r.minus.setEnabled(not alone)
        self.key_add.setEnabled(len(self.key_rows) < self.MAX_KEYS)

    def _keys_changed(self):
        """Mexer nas teclas com a macro rodando vale na hora: recriar as
        threads é barato, e exigir parar e começar de novo não se justifica."""
        if not hasattr(self, "warn"):
            return                      # ainda montando a janela
        if self.keymacros:
            self._stop_key_macros()
            self._start_key_macros()
        self._show_status()

    def _start_key_macros(self):
        """Uma thread por tecla. Duas linhas na mesma tecla brigariam pelo
        estado dela — uma segura, a outra solta —, então só a primeira vale."""
        if not self.ensure_keyboard():
            return
        if self.keymacros:
            return
        seen, dup = set(), False
        for row in self.key_rows:
            code = row.catcher.code
            if code in seen:
                dup = True
                continue
            seen.add(code)
            macro = KeyMacro(self.keyboard, row.interval.value(), code,
                             row.mode.currentData() == "Hold")
            macro.start()
            self.keymacros.append(macro)
        if dup:
            self.warn.setText(_("Two rows use the same key; only the first "
                                "one runs."))

    def _stop_key_macros(self):
        for macro in self.keymacros:
            macro.stop()
        self.keymacros = []

    # ------------------------------------------- ponteiro absoluto e telas --
    def ensure_pointer(self):
        """O device que põe o cursor numa posição. Criado na thread da UI:
        nasce em 0,4 s, tempo que a do hub não pode parar."""
        if self.pointer is None:
            try:
                self.pointer = AbsolutePointer()
            except OSError as e:
                self.warn.setText(f"absolute pointer failed: {e}")
                return False
            self.engine.pointer = self.pointer
        return True

    def _track_screens(self):
        """Cada tela é ligada uma vez só, ao aparecer: UniqueConnection com
        método Python não deduplica em todo PySide6, e religar a cada
        mudança dobraria as chamadas."""
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screens_changed)
        for sc in QApplication.screens():
            sc.geometryChanged.connect(self._screens_changed)
        self._screens_changed()

    def _screen_added(self, sc):
        sc.geometryChanged.connect(self._screens_changed)
        self._screens_changed()

    def _screens_changed(self, _screen=None):
        """A área de trabalho inteira (todas as telas, em coordenadas
        lógicas): é sobre ela que o compositor estica o ponteiro absoluto."""
        rect = None
        for sc in QApplication.screens():
            g = sc.geometry()
            rect = g if rect is None else rect.united(g)
        if rect is not None and rect.width() > 0 and rect.height() > 0:
            self.engine.workspace = (rect.x(), rect.y(), rect.width(),
                                     rect.height())

    def _macro_problem(self, text):
        self.warn.setText(text)

    def _macro_message(self, title, text):
        if getattr(self, "tray", None):
            self.tray.showMessage(title, text, wayclick_icon())
        else:
            notify_desktop(title, text)

    # ---------------------------------------------------------- teclado --
    def ensure_keyboard(self):
        if self.keyboard is None:
            try:
                self.keyboard = VirtualKeyboard()
            except OSError as e:
                self.warn.setText(f"virtual keyboard failed: {e}")
                return False
        return True

    def _click_box_toggled(self, on):
        """Ligar/desligar o clique vale na hora, sem exigir parar e começar de
        novo — e desligar só derruba o clique, não a macro nem a tecla alvo."""
        self._sync_hold_swallow(on)
        if hasattr(self, "warn"):
            self._hinted = False
            self._hint()
        if not (self.running and self._state == "run"):
            return
        if on and not self.clicker:
            self.clicker = Clicker(self.mouse, self.interval.value(),
                                   BTN[self.btn_sel.currentData()])
            self.clicker.start()
        elif not on and self.clicker:
            self.clicker.stop()
            self.clicker = None
        self._show_status()

    def _sync_hold_swallow(self, _on=None):
        """Sem clique preso ao gatilho não há quem reemita o botão: o relay tem
        que deixá-lo passar, senão o botão do usuário morre enquanto armado."""
        if self.holder:
            self.holder.swallow = self._should_swallow()

    def _trigger_changed(self, _i=0):
        if not hasattr(self, "warn"):
            return                      # ainda montando a janela
        self.set_running(False)
        self._hinted = False
        self.warn.setText("")
        self._hint()

    def _scope_changed(self, _on=False):
        """Mudar o escopo com algo armado refaz o arranjo: mais simples, e mais
        honesto, do que remanejar engines no meio da rodada."""
        if not hasattr(self, "warn"):
            return                      # ainda montando a janela
        self.set_running(False)
        self._sync_tabs()

    def _sync_scope(self):
        """Gatilho e escopo só existem quando há um modo que segura."""
        on = self.mode_key() == "hold"
        self.scope_lbl.setEnabled(on)
        self.trig_lbl.setEnabled(on)
        self.trigger.setEnabled(on)
        for box in self.trig_boxes.values():
            box.setEnabled(on)

    def _key_box_toggled(self, on):
        """Cria/destroi o device só quando a macro é ligada — evita deixar um
        teclado virtual pendurado na sessão de quem não usa a função.

        Desligar a macro derruba só a macro: _stop_clicker() levaria junto o
        clique e a tecla direcionada, que não têm nada a ver com esta caixa.
        """
        if on:
            self.ensure_keyboard()
            if self.running and self._state == "run":
                self._start_key_macros()
        else:
            self._stop_key_macros()
            # a janela alvo pura Wayland injeta pelo mesmo teclado virtual:
            # só fechar o device quando ninguém mais depende dele
            if self.keyboard and not self.target_box.isChecked() \
                    and not self._macros_need_keyboard():
                self.keyboard.close()
                self.keyboard = None
        self._show_status()

    # ------------------------------------------ janela alvo (beta) --------
    def refresh_windows(self):
        """Lista as janelas abertas, com ícone, mantendo a seleção se possível."""
        if self.bridge is None:
            self.bridge = KWIN()
        keep = (self.win_sel.currentData() or {}).get("id")
        self.win_sel.blockSignals(True)
        self.win_sel.clear()
        wins = self.bridge.windows() if self.bridge.ok else []
        if self.xinject is None:
            self.xinject = XInject()
        xs = self.xinject.windows() if self.xinject.ok else []
        by_pid = {pid: xid for xid, pid, _cls in xs if pid}
        by_cls = {cls.lower(): xid for xid, _pid, cls in xs if cls}
        for w in wins:
            xid = by_pid.get(w["pid"]) or by_cls.get(w["cls"].lower())
            w["xid"] = xid
            mark = "⌨ " if xid else "◐ "     # injeção real x precisa de foco
            label = f"{mark}{w['title'][:36]}  —  {w['cls']}"
            self.win_sel.addItem(app_icon(w["cls"]), label,
                                 {"id": w["id"], "xid": xid})
        if not wins:
            self.win_sel.addItem(_("no window found"), None)
        idx = next((i for i in range(self.win_sel.count())
                    if (self.win_sel.itemData(i) or {}).get("id") == keep), -1)
        self.win_sel.setCurrentIndex(idx if idx >= 0 else 0)
        self.win_sel.blockSignals(False)
        self.win_sel.setEnabled(bool(wins))
        self._update_win_warn()
        if not self.bridge.ok:
            self.warn.setText(_("Targeted key unavailable: {msg}",
                                msg=self.bridge.error or "?"))
        return wins

    def _ensure_xinject(self):
        if self.xinject is None:
            self.xinject = XInject()
        return self.xinject

    def _update_win_warn(self):
        """Wayland puro não tem como receber tecla endereçada: avisa e ensina
        a reabrir o programa como cliente X11, que aí entra na injeção real."""
        if not hasattr(self, "win_warn"):
            return
        sel = self.win_sel.currentData() or {}
        if not self.win_sel.isEnabled() or not sel or sel.get("xid"):
            self._set_win_warn("")
        else:
            self._set_win_warn(_(
                "⚠ Pure Wayland: reachable only by stealing focus for an "
                "instant. Reopen this program in X11 mode and it takes the key "
                "directly, even minimized — SDL_VIDEODRIVER=x11, "
                "GDK_BACKEND=x11 or QT_QPA_PLATFORM=xcb."))

    def _set_win_warn(self, text):
        """Reserva a altura de verdade do texto: QLabel com quebra de linha
        informa uma linha só como mínimo, e o layout espreme as linhas de cima."""
        self.win_warn.setText(text)
        if not text:
            self.win_warn.setMinimumHeight(0)
            return
        width = max(self.win_warn.width(), 360)
        rect = self.win_warn.fontMetrics().boundingRect(
            0, 0, width, 0, Qt.TextWordWrap, text)
        self.win_warn.setMinimumHeight(rect.height() + 6)

    def _target_ready(self):
        """Janela escolhida pronta. Só janela Wayland precisa do teclado
        virtual: a X11 recebe a tecla endereçada, sem passar pelo seat."""
        if not self.win_sel.count() or self.win_sel.currentData() is None:
            self.refresh_windows()
        sel = self.win_sel.currentData()
        if not sel:
            self.warn.setText(_("Pick a window first (↻ to rescan)."))
            return False
        if sel.get("xid"):
            if not ((self._ensure_xinject().keysym_name(self.win_key.code))
                    or x_keysym(self.win_key.name)):
                self.warn.setText(_("That key has no X11 equivalent."))
                return False
            return True
        return self.ensure_keyboard()

    def _target_start(self):
        """Manda a tecla na hora e depois a cada N segundos — esperar o
        intervalo inteiro para o primeiro envio parecia que não funcionava."""
        if not self._target_ready():
            return False
        self._target_tick()
        self.target_timer.start(self.win_secs.value() * 1000)
        return True

    def _target_toggled(self, on):
        if not on:
            self.target_timer.stop()
        elif self.running and self._state == "run":
            self._target_start()
        else:
            self.refresh_windows()
            self.ensure_keyboard()   # 0,4 s de settle: paga agora, não no Start
        self._show_status()

    def _target_restart(self, _v=None):
        if self.target_timer.isActive():
            self.target_timer.start(self.win_secs.value() * 1000)

    def _target_tick(self):
        """Janela X11: injeta direto, sem tocar no foco nem precisar dela
        visível. Janela Wayland: não há como endereçar, então cai no truque de
        dar foco por um instante e devolver."""
        sel = self.win_sel.currentData() or {}
        wid, xid = sel.get("id"), sel.get("xid")
        if not wid:
            return
        if xid:
            keysym = (self._ensure_xinject().keysym_name(self.win_key.code)
                      or x_keysym(self.win_key.name))
            if keysym and self.xinject and self.xinject.ok:
                t0 = time.perf_counter()
                if self.xinject.send_key(xid, keysym):
                    self.target_hits += 1
                    self.target_ms = (time.perf_counter() - t0) * 1000
                    self._show_status()
                    return
        if not self.keyboard:
            return
        t0 = time.perf_counter()
        prev = self.bridge.activate(wid)
        if prev is None:
            self.warn.setText(_("Lost the target window."))
            self.target_box.setChecked(False)
            return
        # o hold do tap já é a folga para a tecla chegar antes de devolver foco
        self.keyboard.tap(self.win_key.code)
        self.target_hits += 1
        if prev and prev != wid:
            self.bridge.activate(prev)
        # quanto tempo o foco do usuário ficou roubado neste ciclo
        self.target_ms = (time.perf_counter() - t0) * 1000
        self._show_status()

    # ---------------------------------------------------------- anti-afk --
    def _afk_toggled(self, on):
        if self.antiafk:
            self.antiafk.stop()
            self.antiafk = None
        if on and self.ensure_mouse():
            self.antiafk = AntiAfk(self.mouse, self.afk_secs.value())
            self.antiafk.start()
        self._show_status()

    def _afk_restart(self, _v=None):
        if self.afk.isChecked():
            self._afk_toggled(True)

    def _running_parts(self):
        """O que está rodando agora, curto, para a linha de status."""
        parts = [self._rate_str()] if self.clicker else []
        for macro in self.keymacros:
            parts.append(key_label(macro.code) + " "
                         + (_("held") if macro.hold
                            else f"{macro.interval * 1000:.0f} ms"))
        return parts

    def _show_status(self):
        state = getattr(self, "_state", "")
        if state == "armed":
            txt = "◆ " + _("ARMED — hold {trig}", trig=self.trigger.name)
            free = self._running_parts()
            if free:                    # o que o gatilho não segura já roda
                txt += "  (" + " + ".join(free) + ")"
        elif state == "run":
            txt = ("● " + _("RUNNING") + "  ("
                   + " + ".join(self._running_parts() or [_("nothing enabled")])
                   + ")")
        else:
            txt = _("Stopped")
        if self._remain:
            txt += "  —  " + _("stops in {n}s", n=self._remain)
        if self.antiafk:
            txt += "   ⟲ " + _("anti-AFK")
        if self.target_timer.isActive():
            txt += ("   ⌨ " + self.win_key.name
                    + f" ×{self.target_hits}"
                    + (f" ({self.target_ms:.0f} ms)" if self.target_ms else ""))
        running = self.engine.running() if hasattr(self, "engine") else []
        if running:
            txt += "   ⚡ " + ", ".join(m.name for m in running[:3])
            if len(running) > 3:
                txt += f" +{len(running) - 3}"
        self.status.setText(txt)

    def _tick_autostop(self):
        self._remain -= 1
        if self._remain <= 0:
            self.set_running(False)
            self.status.setText(_("Stopped (time is up)"))
        else:
            self._show_status()

    # ------------------------------------------------------------ atalho --
    def on_press(self):
        """O atalho liga e desliga, e só. Quem gateia é o Gatilho: separar os
        dois é o que permite 'armar com F8, rodar segurando o botão esquerdo'
        sem que um papel atrapalhe o outro."""
        self.set_running(not self.running)

    def on_release(self):
        pass

    def _hk_sequence(self):
        """QKeySequence do atalho, para o fallback que só vale com a janela em
        foco. Nem todo nome de tecla tem equivalente no Qt ('Left Shift' não
        tem); quando não tem, o fallback simplesmente não existe, o que é bem
        melhor do que um atalho que dispara na tecla errada."""
        seq = QKeySequence(self.hk.name)
        return seq if not seq.isEmpty() else QKeySequence()

    def on_hotkey_change(self, _text=None):
        self.watcher.set_key(self.hk.code)
        seq = self._hk_sequence()
        self.local_sc.setKey(seq)
        self.local_sc.setEnabled(not self.watcher.ok and not seq.isEmpty())
        self._hinted = False
        self.warn.setText("")
        self._hint()
        self._show_clash()

    def _show_clash(self):
        """Avisa quando um macro usa a tecla de armar o clique: com os macros
        ligados ela é do macro, e o clique não arma por ela. E quando a
        tecla do Suspend é a de um macro ou a de armar."""
        if not hasattr(self, "macro_clash"):
            return
        text = arm_clash_text(self.macros, self.hk.code, self.hk.name)
        if hasattr(self, "suspend_key"):
            more = suspend_clash_text(self.macros, self.suspend_key.code,
                                      self.suspend_mods, self.hk.code)
            text = "\n".join(t for t in (text, more) if t)
        self.macro_clash.setText(text)
        self.macro_clash.setVisible(bool(text))

    # ---------------------------------------------------------- Suspend --
    @staticmethod
    def _suspend_name(code, mods, key=None):
        if not code:
            return _("None")
        return hotkey_label({"code": code, "mods": list(mods),
                             "key": key or key_label(code)})

    def _suspend_tips(self):
        self.suspend_key.setToolTip(_(
            "Like Suspend in AutoHotkey: this key turns the hotkey macros on "
            "and off from inside the game, and the game never receives it. "
            "Turning them off also stops the macros that are running."))
        self.suspend_clear.setToolTip(_("No Suspend hotkey"))

    def set_suspend(self, code, mods=()):
        """A tecla do Suspend (0: nenhuma), também do importador."""
        code = 0 if not code or is_button(code) else int(code)
        self.suspend_mods = sorted({x for x in mods if x in MODIFIERS}) \
            if code else []
        self.suspend_key.blockSignals(True)
        self.suspend_key.set_key(code, self._suspend_name(
            code, self.suspend_mods))
        self.suspend_key.blockSignals(False)
        self._suspend_apply()

    def _suspend_picked(self):
        code = self.suspend_key.code
        if is_button(code):          # o campo só aceita teclas; por garantia
            code = 0
        self.set_suspend(code)

    def _suspend_cleared(self):
        self.set_suspend(0)

    def _suspend_apply(self):
        code = self.suspend_key.code
        w = self.suspend_w if hasattr(self, "suspend_w") else None
        if w is not None:
            w.set_key(code, self.suspend_mods)
            if code and not w._on:
                w.start()
            elif not code and w._on:
                w.stop()
        self.suspend_clear.setEnabled(bool(code))
        self._show_clash()
        if hasattr(self, "_save_timer"):
            self._save_timer.start()

    def _suspend_pressed(self):
        """Apertou a tecla do Suspend: os macros de atalho trocam de estado
        (desligar para também o que estiver rodando)."""
        on = not self.macro_box.isChecked()
        self.macro_box.setChecked(on)
        self._beep("on" if on else "off")

    # ---------------------------------------------------- macros (atalhos) --
    def _macro_outputs(self):
        """Por onde os macros escrevem. Chamado da thread do hub, então só
        devolve o que existe: criar device aqui travaria o relay 0,4 s."""
        return self.keyboard, self.mouse

    def _macros_need_keyboard(self):
        if not self.macro_box.isChecked():
            return False
        return any(CompiledMacro(m).uses_keys for m in self.macros
                   if m["enabled"] and m["steps"] and not m.get("dock"))

    def _apply_macros(self):
        """Entrega a lista ao motor. Roda a cada mudança: é barato, e só o
        macro que mudou é parado."""
        if not hasattr(self, "engine"):
            return
        active = self.macro_box.isChecked() and not self._macro_editing
        if active and self._macros_need_keyboard():
            self.ensure_keyboard()
        if active and any(CompiledMacro(m).uses_pointer for m in self.macros
                          if m["enabled"] and m["steps"]
                          and not m.get("dock")):
            self.ensure_pointer()
        if active and self.engine.windows is None and any(
                CompiledMacro(m).uses_windows for m in self.macros
                if m["enabled"] and m["steps"] and not m.get("dock")):
            kw = KWIN()                 # na thread da UI, onde ela vive
            self.engine.windows = kw if kw.ok else None
        path = self.selected_mouse()[0]
        if path and self.mouse is not None:
            # botão do mouse como atalho escondido: o relay sai pelo mesmo
            # device por onde o Clicker clica
            self.engine.hub.set_output(path, self.mouse)
        self.engine.configure(self.macros, active)
        self._sync_tabs()
        self._macros_ran()

    def _fill_macros(self):
        if not hasattr(self, "macro_list"):
            return
        row = self.macro_list.currentRow()
        self.macro_list.blockSignals(True)
        self.macro_list.clear()
        for m in self.macros:
            title = m["name"] or hotkey_label(m)
            item = QListWidgetItem(f"{title}\n{macro_summary(m)}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if m["enabled"] else Qt.Unchecked)
            item.setData(Qt.UserRole, m["id"])
            self.macro_list.addItem(item)
        self.macro_list.blockSignals(False)
        self._show_clash()
        if self.macros:
            self.macro_list.setCurrentRow(max(0, min(row,
                                                     len(self.macros) - 1)))
        self._sync_macro_buttons()

    def _sync_macro_buttons(self, _row=0):
        row = self.macro_list.currentRow()
        has = 0 <= row < len(self.macros)
        self.m_edit.setEnabled(has)
        self.m_del.setEnabled(has)
        self.m_new.setEnabled(len(self.macros) < MAX_MACROS)
        self.m_import.setEnabled(len(self.macros) < MAX_MACROS)
        self.m_farm.setEnabled(len(self.macros) < MAX_MACROS)
        self.m_load.setEnabled(len(self.macros) < MAX_MACROS)
        self.m_export.setEnabled(has)

    def _macro_tips(self):
        self.m_farm.setToolTip(_("Build a macro that farms matches of a "
                                 "tower defense game, step by step"))
        self.m_export.setToolTip(_("Save the selected macro, with its images, "
                                   "in a file to share"))
        self.m_load.setToolTip(_("Add the macros of a {ext} file",
                                 ext=MACRO_FILE_EXT))

    def _macro_item_changed(self, item):
        row = self.macro_list.row(item)
        if 0 <= row < len(self.macros):
            self.macros[row]["enabled"] = item.checkState() == Qt.Checked
            self._apply_macros()

    def _run_macro_dialog(self, macro):
        """O editor pausa os macros: escolher F1 como atalho de um macro que
        já usa F1 seria impossível com ele rodando."""
        self._macro_editing = True
        self._apply_macros()
        try:
            dlg = MacroDialog(self, macro)
            ok = dlg.exec() == QDialog.Accepted
            return dlg.macro() if ok else None
        finally:
            self._macro_editing = False
            self._apply_macros()

    def _new_macro(self):
        if len(self.macros) >= MAX_MACROS:
            return
        m = self._run_macro_dialog({
            "code": KEYS["F1"], "key": "F1", "mode": "toggle",
            "repeat": True, "period_ms": 100.0,
            "steps": [{"op": "tap", "code": KEYS["E"]}]})
        if m:
            self.macros.append(m)
            self._fill_macros()
            self.macro_list.setCurrentRow(len(self.macros) - 1)
            self._apply_macros()

    def _edit_macro(self):
        row = self.macro_list.currentRow()
        if not 0 <= row < len(self.macros):
            return
        m = self._run_macro_dialog(self.macros[row])
        if m:
            self.macros[row] = m
            self._fill_macros()
            self._apply_macros()

    def _remove_macro(self):
        row = self.macro_list.currentRow()
        if not 0 <= row < len(self.macros):
            return
        m = self.macros[row]
        ask = QMessageBox.question(
            self, _("Remove macro"),
            _("Remove {name}?", name=m["name"] or hotkey_label(m)))
        if ask != QMessageBox.Yes:
            return
        del self.macros[row]
        self._fill_macros()
        self._apply_macros()

    def _import_ahk(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _("Import AutoHotkey"), os.path.expanduser("~"),
            _("AutoHotkey scripts") + " (*.ahk *.ah2 *.txt);;"
            + _("All files") + " (*)")
        if path:
            self.import_ahk_path(path)

    def import_ahk_path(self, path, ask=True):
        """Lê o script, mostra no que ele vira e acrescenta os macros."""
        try:
            imp = import_ahk_file(path)
        except OSError as e:
            QMessageBox.warning(self, _("Import AutoHotkey"),
                                _("Could not read {file}: {err}",
                                  file=os.path.basename(path),
                                  err=e.strerror or e))
            return 0
        room = MAX_MACROS - len(self.macros)
        if ask:
            dlg = ImportDialog(self, imp, path, room,
                               arm=(self.hk.code, self.hk.name))
            if dlg.exec() != QDialog.Accepted:
                return 0
        new = imp.macros[:max(0, room)]
        self.macros += new
        self._fill_macros()
        if new:
            self.macro_list.setCurrentRow(len(self.macros) - len(new))
            self.macro_box.setChecked(True)
        if imp.suspend:
            self.set_suspend(imp.suspend[0], imp.suspend[1])
        self._apply_macros()
        return len(new)

    def _farm(self):
        """O assistente de farm. Pausa os macros como o editor: capturar um
        botão do jogo com um macro rodando por cima não dá."""
        if len(self.macros) >= MAX_MACROS:
            return
        self._macro_editing = True
        self._apply_macros()
        m = None
        try:
            dlg = FarmDialog(self, self.macros)
            if dlg.exec() == QDialog.Accepted:
                m = dlg.macro()
        finally:
            self._macro_editing = False
        if m:
            self.macros.append(m)
            self._fill_macros()
            self.macro_list.setCurrentRow(len(self.macros) - 1)
            self.macro_box.setChecked(True)
        self._apply_macros()

    def _export_macro(self):
        row = self.macro_list.currentRow()
        if not 0 <= row < len(self.macros):
            return
        m = self.macros[row]
        base = re.sub(r"[^\w.-]+", "-", m["name"] or hotkey_label(m)) \
            .strip(".-") or "macro"
        path, _f = QFileDialog.getSaveFileName(
            self, _("Export macro"),
            os.path.join(os.path.expanduser("~"), base + MACRO_FILE_EXT),
            _("WayClick macros") + f" (*{MACRO_FILE_EXT})")
        if path:
            if not path.endswith(MACRO_FILE_EXT):
                path += MACRO_FILE_EXT
            self.export_macro_path([m], path)

    def export_macro_path(self, macros, path, ask=True):
        """Grava os macros num arquivo .wayclick, com as imagens dentro."""
        data, missing, hooks = export_macros(macros)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            QMessageBox.warning(self, _("Export macro"),
                                _("Could not write {file}: {err}",
                                  file=os.path.basename(path),
                                  err=e.strerror or e))
            return False
        lines = [_("Saved {file}. Import… adds it on another computer.",
                   file=os.path.basename(path))]
        if hooks:
            lines.append(_("The Discord webhook stayed out of the file: "
                           "whoever has it could post in your channel."))
        if missing:
            lines.append(_("These images could not be read and were left "
                           "out: {files}", files=", ".join(
                               os.path.basename(x) for x in missing)))
        if ask:
            QMessageBox.information(self, _("Export macro"),
                                    "\n\n".join(lines))
        return True

    def _import_macros(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _("Import macros"), os.path.expanduser("~"),
            _("WayClick macros") + f" (*{MACRO_FILE_EXT} *.json);;"
            + _("All files") + " (*)")
        if path:
            self.import_macro_path(path)

    def import_macro_path(self, path, ask=True):
        """Mostra o que o arquivo traz e acrescenta os macros."""
        data = None
        try:
            data = read_macro_file(path)
            macros, notes = import_macros(data, dry=True)
        except OSError as e:
            QMessageBox.warning(self, _("Import macros"),
                                _("Could not read {file}: {err}",
                                  file=os.path.basename(path),
                                  err=e.strerror or e))
            return 0
        except ValueError:
            if isinstance(data, dict) and data.get("kind") == TOWER_FILE_KIND:
                # uma estratégia de Tower Defense: o lugar dela é a outra aba
                if not ask or QMessageBox.question(
                        self, _("Import macros"),
                        _("{file} holds a Tower Defense strategy, not "
                          "macros. Import it on the Tower Defense tab?",
                          file=os.path.basename(path))) == QMessageBox.Yes:
                    self.tabs.setCurrentWidget(self.tower_scroll)
                    self.tower.import_path(path)
                return 0
            QMessageBox.warning(self, _("Import macros"),
                                _("{file} is not a WayClick macro file.",
                                  file=os.path.basename(path)))
            return 0
        room = MAX_MACROS - len(self.macros)
        if ask:
            dlg = MacroFileDialog(self, macros, notes, path, room)
            if dlg.exec() != QDialog.Accepted:
                return 0
        try:
            macros, _notes = import_macros(data)
        except OSError as e:
            QMessageBox.warning(self, _("Import macros"),
                                _("Could not save {path}.",
                                  path=e.filename or images_dir()))
            return 0
        new = macros[:max(0, room)]
        self.macros += new
        self._fill_macros()
        if new:
            self.macro_list.setCurrentRow(len(self.macros) - len(new))
        self._apply_macros()
        return len(new)

    def _macros_ran(self):
        """Algum macro começou ou terminou (sinal vindo de outra thread)."""
        if not hasattr(self, "macro_state"):
            return
        running = self.engine.running()
        if running:
            self.macro_state.setText("⚡ " + _("Running: {names}", names=", ".join(
                m.name for m in running)))
            self.macro_state.setStyleSheet("color:#27ae60;font-weight:bold;")
        else:
            self.macro_state.setText("")
        # vazio e com quebra de linha, o QLabel comeria a altura da lista
        self.macro_state.setVisible(bool(running))
        if hasattr(self, "status"):
            self._show_status()
            self._sync_tray()

    def _macro_toggled(self, on):
        self._burst_start()
        self._beep("on" if on else "off")

    # Limpeza via aboutToQuit e não closeEvent: sobrescrever closeEvent faz o
    # shiboken embrulhar o QCloseEvent, o que segfaulta no PySide6 + Python
    # 3.15rc do Fedora 45 quando a janela é fechada pelo botão do compositor.
    def _cfg_state(self):
        return {"interval_ms": self.interval.value(),
                "button": self.btn_sel.currentData(),
                "delay": self.delay.value(),
                "duration": self.duration.value(),
                "trigger_code": self.trigger.code,
                "trigger_name": self.trigger.name,
                "hotkey": self.hk.name,
                "hotkey_code": self.hk.code,
                "mode": self.mode_key(),
                "sound": self.sound.isChecked(),
                "mouse_name": self.selected_mouse()[1],
                "click_enabled": self.click_box.isChecked(),
                "key_enabled": self.key_box.isChecked(),
                "keys": [r.spec() for r in self.key_rows],
                # a primeira tecla também nos campos antigos: uma
                # versão anterior ainda lê a config sem se perder
                "key": self.key_rows[0].catcher.name,
                "key_code": self.key_rows[0].catcher.code,
                "key_interval_ms": self.key_rows[0].interval.value(),
                "key_mode": self.key_rows[0].mode.currentData(),
                "trigger_scope": {n: b.isChecked() for n, b
                                  in self.trig_boxes.items()},
                "afk_seconds": self.afk_secs.value(),
                "target_key": self.win_key.name,
                "target_key_code": self.win_key.code,
                "target_seconds": self.win_secs.value(),
                "hotkey_block": self.hk_block.isChecked(),
                "macros": self.macros,
                "macros_enabled": self.macro_box.isChecked(),
                "suspend_code": self.suspend_key.code,
                "suspend_key": key_label(self.suspend_key.code)
                if self.suspend_key.code else "",
                "suspend_mods": list(self.suspend_mods),
                "dock_size": "x".join(
                    map(str, self._dock_size_value())),
                "dock_command": self.dock_cmd.text().strip(),
                "dock_account": self.dock_acct.currentData() or "",
                "sober_accounts": [{"id": a.id, "name": a.name}
                                   for a in self.sober_accounts],
                "update_check": self.update_auto,
                "update_tag": self._update_tag,
                "update_url": self._update_url,
                "update_last": self._update_last,
                "theme": self.theme,
                "tray_style": self.tray_style,
                "tray_color": self.tray_color,
                "language": LANG,
                "tower": self.tower.state()}

    def _save_cfg(self):
        """A configuração no disco, de uma vez: um arquivo novo que troca de
        lugar com o velho, então faltar luz no meio não deixa um pela
        metade. Também logo depois de mexer na aba Tower Defense, porque uma
        estratégia gravada jogando não se refaz fácil."""
        try:
            data = self._cfg_state()
            tmp = CFG + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, CFG)
        except Exception:                          # noqa: BLE001
            pass

    def cleanup(self):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        self.set_running(False)
        if self.antiafk:
            self.antiafk.stop()
            self.antiafk = None
        self.target_timer.stop()
        if self.bridge or _KWIN:
            (self.bridge or _KWIN).cleanup()
        self._save_timer.stop()
        self.sober_page.shutdown()
        self.tower.shutdown()
        self.engine.stop_dock()
        self.dock.stop()
        self.engine.close()
        self.watcher.stop()
        if self.trig_watcher:
            self.trig_watcher.stop()
            self.trig_watcher = None
        self.watcher.hub.stop()      # solta toda captura antes de fechar
        if self.keyboard:
            self.keyboard.close()
            self.keyboard = None
        if self.mouse:
            self.mouse.close()
            self.mouse = None
        if self.pointer:
            self.engine.pointer = None
            self.pointer.close()
            self.pointer = None
        if self.engine.screen is not None:
            self.engine.screen.stop()
        self._save_cfg()
        # por último: o resto da limpeza ainda repinta o estado, e repintar
        # uma bandeja já removida estouraria
        if self.tray:
            self.tray.hide()
            self.tray.setParent(None)
            self.tray = None


USAGE = f"""WayClick {VERSION} — autoclicker for Wayland, via /dev/uinput

usage: wayclick [--tray] [--import FILE.ahk [--dry-run]] [--version] [--help]

  --tray            start hidden in the system tray
  --import FILE     turn the hotkeys of an AutoHotkey script into macros and
                    add them to the config (WayClick must not be running)
  --dry-run         with --import: only show what the script would become
  --version         print the version and exit
  --help            show this help and exit

{HOMEPAGE}"""


def _other_instance():
    """Outro WayClick rodando? Ele grava a própria lista ao sair, então
    importar por fora com ele aberto seria perdido."""
    me = os.getpid()
    for d in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(d))
            if pid == me:
                continue
            with open(f"{d}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
        except (OSError, ValueError):
            continue
        if any(os.path.basename(x).startswith(b"wayclick") for x in argv[:2]) \
                and b"--import" not in argv:
            return pid
    return None


def cli_import(argv):
    """wayclick --import arquivo.ahk [--dry-run]"""
    global LANG
    i = argv.index("--import")
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        print("usage: wayclick --import FILE.ahk [--dry-run]", file=sys.stderr)
        return 2
    path, dry = argv[i + 1], "--dry-run" in argv
    cfg = {}
    if os.path.exists(CFG):
        try:
            with open(CFG) as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
    LANG = cfg.get("language") or default_language()
    try:
        imp = import_ahk_file(path)
    except OSError as e:
        print(_("Could not read {file}: {err}", file=path,
                err=e.strerror or e), file=sys.stderr)
        return 1
    arm = cfg.get("hotkey_code") or KEYNAMES.get(cfg.get("hotkey", "F8"),
                                                 KEYNAMES["F8"])
    print(import_report(imp, path, (arm, cfg.get("hotkey") or "F8")))
    if dry or not (imp.macros or imp.suspend):
        return 0 if imp.macros or imp.suspend else 1
    pid = _other_instance()
    if pid:
        print("\n" + _("WayClick is running (pid {pid}). Import from its "
                        "Hotkeys tab, or quit it and run this again.",
                        pid=pid), file=sys.stderr)
        return 1
    old = [normalize_macro(m) for m in (cfg.get("macros") or [])
           if isinstance(m, dict)]
    room = MAX_MACROS - len(old)
    cfg["macros"] = old + imp.macros[:max(0, room)]
    cfg["macros_enabled"] = True
    if imp.suspend:
        cfg["suspend_code"] = imp.suspend[0]
        cfg["suspend_key"] = key_label(imp.suspend[0])
        cfg["suspend_mods"] = list(imp.suspend[1])
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    tmp = CFG + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh)
    os.replace(tmp, CFG)
    added = min(len(imp.macros), max(0, room))
    print("\n" + (_("Added 1 macro to {cfg}.", cfg=CFG) if added == 1 else
                   _("Added {n} macros to {cfg}.", n=added, cfg=CFG)))
    return 0


if __name__ == "__main__":
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(USAGE)
        sys.exit(0)
    if "--version" in sys.argv[1:] or "-V" in sys.argv[1:]:
        print(f"WayClick {VERSION}")
        sys.exit(0)
    if "--import" in sys.argv[1:]:
        sys.exit(cli_import(sys.argv[1:]))
    reexec_with_input_group()   # antes do Qt: pode substituir o processo
    app = QApplication(sys.argv)
    w = App()
    app.aboutToQuit.connect(w.cleanup)

    # Ctrl+C no terminal: sem isso o sinal só é visto quando o interpretador
    # volta a rodar bytecode, e o KeyboardInterrupt estoura no meio da limpeza
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QTimer()
    wake.setInterval(200)
    wake.timeout.connect(lambda: None)   # devolve o controle ao Python
    wake.start()

    if "--tray" in sys.argv[1:] and w.tray:
        w.start_hidden()                  # início silencioso, só a bandeja
    else:
        w.show()
    sys.exit(app.exec())
