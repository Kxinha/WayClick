"""As contas do Sober sem Sober: a lista de contas, a pasta e a trava de
cada uma, o comando que abre (flock, pasta, as montagens e a volta ao
usuário), achar e fechar só a cópia da conta, a página Contas do Sober, a
Doca com uma conta (e um link para uma conta já aberta, que não abre) e a
configuração.

flatpak, systemctl e systemd-run aqui são falsos (um PATH só do teste, que
anota cada chamada). O "Sober" é um script que faz como ele: segura a porta
54587 do 127.0.0.1 da rede em que está, e um segundo na mesma rede para em
"An instance of Sober is already running", como o de verdade faz (medido em
t_sober_accounts_kwin.py). O flock, o pasta e o unshare são os de verdade,
então cada conta abre mesmo numa rede e num namespace de montagem
separados, só que sem flatpak e sem sair para a internet. O HOME é
temporário: a pasta do Sober de sempre nunca é tocada, e o script falso se
recusa a rodar na rede do computador. O Sober de verdade está em
t_sober_accounts_kwin.py.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_sober_accounts.py
"""
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-soberacc-")
HOME = os.path.join(TMP, "home")
BIN = os.path.join(TMP, "bin")
OUT = os.path.join(TMP, "out")
RT = os.path.join(TMP, "rt")
for _d in (HOME, BIN, OUT):
    os.makedirs(_d)
os.makedirs(RT, mode=0o700)
CALLS = os.path.join(TMP, "calls.log")
PS = os.path.join(TMP, "ps.txt")
TARGET = os.path.join(HOME, ".var", "app", "org.vinegarhq.Sober")
HOSTNET = os.readlink("/proc/self/ns/net")
HOSTTIME = os.readlink("/proc/self/ns/time")
SOBER = ["flatpak", "run", "org.vinegarhq.Sober"]
os.environ.update({
    "HOME": HOME,
    "XDG_CONFIG_HOME": os.path.join(TMP, "config"),
    "XDG_DATA_HOME": os.path.join(TMP, "data"),
    "XDG_RUNTIME_DIR": RT,
    "PATH": BIN + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
    "WCTEST_CALLS": CALLS, "WCTEST_PS": PS, "WCTEST_OUT": OUT,
    "WCTEST_TARGET": TARGET, "WCTEST_HOSTNET": HOSTNET,
})

FAKE_FLATPAK = r'''#!/usr/bin/python3 -I
import json, os, signal, socket, sys
args = sys.argv[1:]
with open(os.environ["WCTEST_CALLS"], "a") as fh:
    fh.write(json.dumps(["flatpak"] + args) + "\n")
if args[:1] == ["ps"]:
    try:
        with open(os.environ["WCTEST_PS"]) as fh:
            sys.stdout.write(fh.read())
    except OSError:
        pass
    sys.exit(0)
if args[:1] == ["info"]:
    sys.exit(0 if args[1:] == ["org.vinegarhq.Sober"] else 1)
if args[:2] != ["run", "org.vinegarhq.Sober"]:
    sys.exit(0)                     # kill e o resto: só anotados
net = os.readlink("/proc/self/ns/net")
if net == os.environ["WCTEST_HOSTNET"]:
    # nunca na rede do computador: ali a 54587 seria do Sober de verdade
    print("wctest: refusing to run in the host network", flush=True)
    sys.exit(97)
target = os.environ["WCTEST_TARGET"]


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


who = (read(os.path.join(target, "who")) or "").strip() or "nobody"
fail = read(os.path.join(target, "fail"))
if fail:
    print(fail.strip(), flush=True)
    sys.exit(3)
url = args[2] if len(args) > 2 else ""
out = os.environ["WCTEST_OUT"]
s = socket.socket()
try:
    s.bind(("127.0.0.1", 54587))
except OSError:
    # já há um Sober nesta rede: o de verdade para aqui
    with open(os.path.join(out, who + ".second"), "a") as fh:
        fh.write(url + "\n")
    print("FATAL: Crash: An instance of Sober is already running.",
          flush=True)
    sys.exit(1)
s.listen(4)
reach = None
port = int(os.environ.get("WCTEST_HOSTPORT") or 0)
if port:
    t = socket.socket()
    t.settimeout(2)
    try:
        t.connect(("127.0.0.1", port))
        reach = True
    except OSError:
        reach = False
    t.close()
signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
report = {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid(),
          "net": net, "time": os.readlink("/proc/self/ns/time"),
          "resolv": read("/etc/resolv.conf"),
          "resolv_real": os.path.realpath("/etc/resolv.conf"),
          "wayland": os.environ.get("WAYLAND_DISPLAY"), "url": url,
          "token": os.environ.get("XDG_ACTIVATION_TOKEN"),
          "host_port_reachable": reach, "data": sorted(os.listdir(target))}
with open(os.path.join(target, "written-by-sober"), "w") as fh:
    fh.write(who)
tmp = os.path.join(out, who + ".json.tmp")
with open(tmp, "w") as fh:
    json.dump(report, fh)
os.replace(tmp, os.path.join(out, who + ".json"))
while True:
    signal.pause()
'''
FAKE_SYSTEMCTL = r'''#!/usr/bin/python3 -I
import json, os, sys
with open(os.environ["WCTEST_CALLS"], "a") as fh:
    fh.write(json.dumps(["systemctl"] + sys.argv[1:]) + "\n")
sys.exit(int(os.environ.get("WCTEST_SYSTEMCTL_RC") or 0))
'''
FAKE_SYSTEMD_RUN = r'''#!/usr/bin/python3 -I
import json, os, sys
args = sys.argv[1:]
with open(os.environ["WCTEST_CALLS"], "a") as fh:
    fh.write(json.dumps(["systemd-run"] + args) + "\n")
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 1
os.execvp(args[i], args[i:])
'''
for _name, _text in (("flatpak", FAKE_FLATPAK), ("systemctl", FAKE_SYSTEMCTL),
                     ("systemd-run", FAKE_SYSTEMD_RUN)):
    with open(os.path.join(BIN, _name), "w") as _fh:
        _fh.write(_text)
    os.chmod(os.path.join(BIN, _name), 0o755)
    # o resto do teste só roda com as falsas na frente das de verdade
    assert shutil.which(_name) == os.path.join(BIN, _name), _name

import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtWidgets import (QApplication, QInputDialog,  # noqa: E402
                               QMessageBox)

a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US"}, fh)
app = QApplication(sys.argv)
a.LANG = "en_US"
KIDS = []
HAVE_PASTA = shutil.which("pasta") is not None


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        app.processEvents()
        time.sleep(0.02)
    return bool(cond())


def calls(tool=None):
    try:
        with open(CALLS) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []
    return [r for r in rows if tool is None or r[0] == tool]


def report(who):
    try:
        with open(os.path.join(OUT, who + ".json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def seconds(who):
    """Os Sober que abriram numa conta já aberta (e pararam)."""
    try:
        with open(os.path.join(OUT, who + ".second")) as fh:
            return fh.read().split()
    except OSError:
        return []


def sober_runs():
    return [c for c in calls("flatpak") if c[1:2] == ["run"]]


def set_ps(*rows):
    with open(PS, "w") as fh:
        for row in rows:
            inst, pid = row[0], row[1]
            app_id = row[2] if len(row) > 2 else "org.vinegarhq.Sober"
            fh.write(f"{inst}\t{pid}\t{app_id}\n")


def spawn(argv):
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    KIDS.append(p)
    return p


def stop_group(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass


def fresh(acc_id, name, who):
    acc = a.SoberAccount(acc_id, name)
    acc.prepare()
    with open(os.path.join(acc.data(), "who"), "w") as fh:
        fh.write(who)
    return acc


def gone(path):
    try:
        os.remove(path)
    except OSError:
        pass


def open_for_real(acc, who, **kw):
    """Abre a conta com o Sober falso e espera ele contar como está."""
    gone(os.path.join(OUT, who + ".json"))
    p = acc.launch(SOBER, **kw)
    KIDS.append(p)
    until(lambda: report(who) is not None, 20)
    return report(who)


def port_taken_on_host(port):
    hexport = f":{port:04X}"
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name) as fh:
                for line in fh.readlines()[1:]:
                    f = line.split()
                    if f[1].endswith(hexport) and f[3] == "0A":
                        return True
        except OSError:
            pass
    return False


# ------------------------------------------------------- a lista --
norm = a.normalize_sober_accounts
got = norm([{"id": "1A2B3C4D", "name": "  Friend   one "},
            {"id": "1a2b3c4d", "name": "dupe"}, {"id": "xyz", "name": "bad"},
            "junk", {"id": "00000001", "name": ""},
            {"id": "00000002", "name": "x" * 60},
            {"id": "00000003", "name": "c"}, {"id": "00000004", "name": "d"}])
check("the list keeps 8 hex ids, short names, no repeats, 4 at most",
      got == [{"id": "1a2b3c4d", "name": "Friend one"},
              {"id": "00000001", "name": "00000001"},
              {"id": "00000002", "name": "x" * 40},
              {"id": "00000003", "name": "c"}], str(got))
check("and anything else is an empty list",
      norm(None) == [] and norm({"id": "1a2b3c4d"}) == [] and norm("x") == [])

# ----------------------------------------------- pasta, trava, comando --
acc = fresh("1a2b3c4d", "Friend", "friend")
ROOT = os.path.join(TMP, "data", "wayclick", "sober-accounts")
check("each account has its own folder in the WayClick data",
      acc.folder() == os.path.join(ROOT, "1a2b3c4d")
      and acc.data() == os.path.join(ROOT, "1a2b3c4d", "app"))
check("private, with the DNS file of pasta and the lock",
      os.stat(acc.folder()).st_mode & 0o777 == 0o700
      and open(acc.resolv_path()).read() == "nameserver 169.254.1.1\n"
      and os.path.exists(acc.lock_path()))
check("it covers the usual Sober folder under HOME",
      a.sober_data_dir() == TARGET
      and a.sober_data_dir("/x") == "/x/.var/app/org.vinegarhq.Sober")

saved = dict(a._HELP_TEXT)
a._HELP_TEXT["pasta"] = "--no-pidns  --map-host-loopback ADDR  --no-map-gw"
p_new = a.sober_pasta()
a._HELP_TEXT["pasta"] = "--no-map-gw"
p_old = a.sober_pasta()
a._HELP_TEXT["pasta"] = ""
p_none = a.sober_pasta()
a._HELP_TEXT.clear()
a._HELP_TEXT.update(saved)
BASE = ["pasta", "--config-net", "--quiet", "-t", "none", "-u", "none",
        "-T", "none", "-U", "none", "--dns-forward", "169.254.1.1"]
check("pasta forwards no port either way, and none of the loopback ports "
      "of the computer (-T none: the usual Sober holds 54587 there)",
      p_new == BASE + ["--no-pidns", "--map-host-loopback", "none", "--"],
      str(p_new))
check("an older pasta gets --no-map-gw, and one without it nothing more",
      p_old == BASE + ["--no-map-gw", "--"] and p_none == BASE + ["--"])

cmd = acc.command(SOBER)
pas = a.sober_pasta()
i = 6 + len(pas)
check("the command: flock holds the account lock, 75 if it is taken",
      cmd[:6] == ["flock", "--nonblock", "--close", "--conflict-exit-code",
                  "75", acc.lock_path()])
check("then pasta, then the mounts as root of its namespace",
      cmd[6:i] == pas and cmd[i:i + 3] == ["sh", "-c", a.SOBER_INNER])
check("the account folder over the usual one, back to the real user, "
      "then Sober",
      cmd[i + 3:i + 10] == ["wayclick-sober", acc.data(), TARGET,
                            acc.resolv_path(), acc.run_path(),
                            str(os.getuid()), str(os.getgid())]
      and cmd[i + 10:] == SOBER, str(cmd[i + 3:]))

check("the inner script has no $$ or ${ (systemd-run would expand them)",
      "$$" not in a.SOBER_INNER and "${" not in a.SOBER_INNER)
check("no user systemd, no scope of its own", acc.scope_prefix() == [])
os.makedirs(os.path.join(RT, "systemd"))
open(os.path.join(RT, "systemd", "private"), "w").close()
sp = acc.scope_prefix()
check("with it, a scope named after the account",
      sp[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
      and len(sp) == 6 and sp[5].startswith("--unit=wayclick-sober-1a2b3c4d-"),
      str(sp))

check("closed: nobody holds the lock", not acc.running())
holder = spawn(["flock", acc.lock_path(), "sleep", "600"])
check("an flock on the lock means open", until(acc.running, 5))
r = subprocess.run(["flock", "--nonblock", "--close", "--conflict-exit-code",
                    "75", acc.lock_path(), "true"])
check("and a second flock exits 75 (SOBER_BUSY)", r.returncode == a.SOBER_BUSY)
with open(acc.run_path(), "w") as fh:
    fh.write("123\nnet:[1]\n")
try:
    acc.launch(["true"], prefix=[])
    refused = ""
except OSError as e:
    refused = str(e)
check("launch refuses an open account and keeps its run file",
      refused == "Friend is already open."
      and open(acc.run_path()).read() == "123\nnet:[1]\n", refused)
stop_group(holder)
check("the lock goes with the holder", until(lambda: not acc.running(), 5))
with open(acc.run_path(), "w") as fh:
    fh.write(f"{os.getpid()}\n{HOSTNET}\n")
check("a run file with the network of the computer is never used",
      acc.run_info() == (None, None))
with open(acc.run_path(), "w") as fh:
    fh.write("garbage")
check("nor a broken one", acc.run_info() == (None, None))
check("closed, there is nothing to close", acc.close() is False)

# ------------------------------------------- abrir de verdade (sem Sober) --
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
os.environ["WCTEST_HOSTPORT"] = str(listener.getsockname()[1])
user = spawn(["sleep", "600"])      # o Sober de sempre, na rede do computador
rep = None
if not HAVE_PASTA:
    check("pasta is installed (passt package): the rest needs it", False)
else:
    rep = open_for_real(acc, "friend", prefix=[])
    check("the account Sober opens", rep is not None, acc.log_tail())
if rep is not None:
    taken = port_taken_on_host(54587)
    check("in a network of its own, holding its own 54587",
          rep["net"] != HOSTNET and rep["net"].startswith("net:["),
          f"54587 on the computer: {'taken' if taken else 'free'}")
    check("the loopback ports of the computer are not reachable from it",
          rep["host_port_reachable"] is False)
    # o Sober trava trechos do /proc/self/ns/time: com o namespace de tempo
    # do Sober de sempre, o da conta caía em SL_RESULT_SUCCESS
    check("and a time namespace of its own, so its locks on "
          "/proc/self/ns/time never meet those of the usual Sober",
          rep["time"] != HOSTTIME and rep["time"].startswith("time:["),
          f"{rep['time']} vs {HOSTTIME}")
    check("it runs as the real user", rep["uid"] == os.getuid()
          and rep["gid"] == os.getgid())
    check("it sees the account folder where Sober looks",
          rep["data"] == ["who"], str(rep["data"]))
    check("and the DNS of pasta in resolv.conf",
          rep["resolv"] == "nameserver 169.254.1.1\n"
          and rep["resolv_real"] == os.path.realpath("/etc/resolv.conf"))
    check("the run file has its pid and network (unshare execs in place)",
          acc.run_info() == (rep["pid"], rep["net"]), str(acc.run_info()))
    check("what it writes lands in the account folder, never in the usual "
          "one",
          until(lambda: os.path.exists(os.path.join(acc.data(),
                                                    "written-by-sober")), 5)
          and os.listdir(TARGET) == [], str(os.listdir(TARGET)))
    check("open: the lock says so", acc.running())
    try:
        acc.launch(SOBER, prefix=[])
        twice = False
    except OSError:
        twice = True
    check("a second launch of the same account is refused", twice)

    # ------------------------------------------------ achar e fechar --
    set_ps(("usual", user.pid), ("friend", rep["pid"]),
           ("other", rep["pid"], "org.example.App"))
    check("flatpak ps finds the account copy by its network, never the "
          "usual Sober", acc.instance() == ("friend", rep["pid"]),
          str(acc.instance()))
    units = {user.pid: "app-flatpak-org.vinegarhq.Sober-wctestusual.scope",
             rep["pid"]: "app-flatpak-org.vinegarhq.Sober-wctestfriend.scope"}
    REAL_UNIT = a.cgroup_unit
    a.cgroup_unit = lambda pid: units.get(int(pid), "")
    open(CALLS, "w").close()
    check("close stops the flatpak scope of the account copy, and only it",
          acc.close() and calls("systemctl") == [
              ["systemctl", "--user", "stop", "--no-block",
               "app-flatpak-org.vinegarhq.Sober-wctestfriend.scope"]],
          str(calls("systemctl")))
    units[rep["pid"]] = "wayclick-sober-1a2b3c4d-77.scope"
    open(CALLS, "w").close()
    check("or the WayClick scope of this account",
          acc.close() and calls("systemctl") == [
              ["systemctl", "--user", "stop", "--no-block",
               "wayclick-sober-1a2b3c4d-77.scope"]])
    units[rep["pid"]] = "wayclick-sober-ffffffff-77.scope"
    open(CALLS, "w").close()
    closed = acc.close()
    check("the scope of another account is never stopped: a TERM to its "
          "own process instead",
          closed and calls("systemctl") == []
          and until(lambda: not acc.running(), 10), acc.log_tail())
    check("the copy exits and the lock goes with it",
          acc.proc.poll() is not None and not acc.running())

    rep = open_for_real(acc, "friend", prefix=[])
    units[rep["pid"]] = "app-flatpak-org.vinegarhq.Sober-wctestfriend.scope"
    os.environ["WCTEST_SYSTEMCTL_RC"] = "1"
    open(CALLS, "w").close()
    set_ps(("usual", user.pid), ("friend", rep["pid"]))
    closed = acc.close()
    check("if systemctl fails, a TERM closes it anyway",
          closed and len(calls("systemctl")) == 1
          and until(lambda: not acc.running(), 10))
    os.environ.pop("WCTEST_SYSTEMCTL_RC")
    a.cgroup_unit = REAL_UNIT
    check("flatpak kill is never used, and the usual Sober is untouched",
          not any(c[:2] == ["flatpak", "kill"] for c in calls())
          and user.poll() is None)

    # ----------------------------------------------------- apagar --
    with open(os.path.join(acc.data(), "big"), "wb") as fh:
        fh.write(b"\0" * 300000)
    check("its size on disk is counted", acc.disk_bytes() > 300000
          and a.human_bytes(acc.disk_bytes()).endswith(" KB"))
    holder = spawn(["flock", acc.lock_path(), "sleep", "600"])
    until(acc.running, 5)
    try:
        acc.remove_data()
        why = ""
    except OSError as e:
        why = str(e)
    check("an open account is never deleted", why == "Close it first."
          and os.path.isdir(acc.folder()))
    stop_group(holder)
    until(lambda: not acc.running(), 5)
    elsewhere = os.path.join(TMP, "elsewhere")
    os.makedirs(elsewhere)
    os.symlink(elsewhere, os.path.join(ROOT, "deadbeef"))
    try:
        a.SoberAccount("deadbeef", "x").remove_data()
        why = ""
    except OSError as e:
        why = str(e)
    check("nor anything outside the accounts folder",
          why.startswith("not an account folder") and os.path.isdir(elsewhere))
    os.remove(os.path.join(ROOT, "deadbeef"))
    acc.remove_data()
    check("closed, its folder goes", not os.path.exists(acc.folder()))

# ---------------------------------------------------- a Doca com conta --
class FakeProc:
    pid = 4242424

    def poll(self):
        return None


class FakeLink:
    closed = False


dock = a.DOCK()
dock.state = "on"
dock.kwin, dock.link = FakeProc(), FakeLink()
check("a fake open Dock", dock.alive())
dock.command = "flatpak run org.vinegarhq.Sober"
dockie = fresh("2b3c4d5e", "Dockie", "dockie")
dock.account = dockie
runs = []
REAL_RUN = dock._run
dock._run = lambda argv, account=None: runs.append((list(argv), account)) \
    or FakeProc()
check("Start the game with an account opens Sober in it",
      dock.launch() and runs == [(SOBER, dockie)], str(runs))
runs.clear()
reports = []
a.scheme_handler = lambda url: SOBER + [url]
dock.open("wctest://first", reports.append)
check("so does a link, with the link",
      runs == [(SOBER + ["wctest://first"], dockie)] and not reports)
runs.clear()
dock._run = REAL_RUN
if HAVE_PASTA:
    gone(os.path.join(OUT, "dockie.json"))
    check("for real: the Dock opens the account Sober", dock.launch()
          and until(lambda: report("dockie") is not None, 20),
          dockie.log_tail())
    rep2 = report("dockie") or {}
    check("on the Dock socket, in the account network and time namespace",
          rep2.get("wayland") == a.DOCK_SOCKET
          and rep2.get("net") not in (None, HOSTNET)
          and rep2.get("time") not in (None, HOSTTIME), str(rep2))
    check("and the Dock knows it opened it", dock.account_here(dockie))
    dock.note = ""
    check("Start the game again says it is open in the Dock",
          dock.launch() is False
          and dock.note == "Dockie is already open in the Dock.", dock.note)
    n_runs = len(sober_runs())
    dock.open("wctest://dock-link", reports.append)
    pump(1.0)
    check("a link for the account open in the Dock opens nothing, and says "
          "why (Sober takes a link only when it opens)",
          reports == ["Sober is already open in Dockie, and it takes a link "
                      "only when it opens, so wctest://dock-link was not "
                      "opened. Close it first."]
          and len(sober_runs()) == n_runs and not seconds("dockie"),
          str(reports))
    dock._flatpak_ps = lambda: f"{rep2.get('pid')}\torg.vinegarhq.Sober\n"
    check("a Sober in an account network is not one outside the Dock",
          not dock.sober_outside(SOBER))
    dock._flatpak_ps = lambda: f"{user.pid}\torg.vinegarhq.Sober\n"
    check("one in the network of the computer is",
          dock.sober_outside(SOBER))
    set_ps(("dockie", rep2.get("pid")))
    check("Close closes it", dockie.close()
          and until(lambda: not dockie.running(), 10))
    # aberta pela página, não pela Doca
    rep2 = open_for_real(dockie, "dockie", prefix=[])
    check("opened outside the Dock: the Dock does not count it as its own",
          rep2 is not None and not dock.account_here(dockie))
    dock.note = ""
    check("Start the game says it is open outside the Dock",
          dock.launch() is False and dock.note == (
              "Dockie is already open outside the Dock. Close it, then "
              "press Start the game."), dock.note)
    reports.clear()
    dock.open("wctest://not-here", reports.append)
    check("and a link does not go to it",
          reports == ["Dockie is already open outside the Dock. Close it, "
                      "then press Start the game."]
          and not seconds("dockie"), str(reports))
    set_ps(("dockie", (rep2 or {}).get("pid")))
    dockie.close()
    until(lambda: not dockie.running(), 10)
dock.account = None
reports.clear()
dock._flatpak_ps = lambda: f"{user.pid}\torg.vinegarhq.Sober\n"
dock.open("wctest://usual", reports.append)
check("without an account, a usual Sober open outside still blocks it",
      reports == [a.SOBER_TWICE], str(reports))
dock._flatpak_ps = lambda: ""
dock.games = []
dock.state, dock.kwin, dock.link = "off", None, None

# ------------------------------------------------------------ a página --
asked = []
answers = []


def fake_text(*args, **kw):
    asked.append(args[1:])
    return answers.pop(0) if answers else ("", False)


QInputDialog.getText = staticmethod(fake_text)
yes_no = [QMessageBox.No]
QMessageBox.question = staticmethod(lambda *x, **k: yes_no[0])
accounts = []
page = a.SoberPage(accounts)
changed, states = [], []
page.changed.connect(lambda: changed.append(1))
page.state_changed.connect(lambda: states.append(1))
check("an empty page: only Add works",
      page.list.count() == 0 and page.b_add.isEnabled()
      and not page.b_open.isEnabled() and not page.b_close.isEnabled()
      and not page.b_rename.isEnabled() and not page.b_remove.isEnabled())
answers.append(("   Friend   of mine ", True))
acc = page.add()
check("Add asks a name, suggesting Account 2 (1 is the usual Sober)",
      asked and asked[-1][-1] == "Account 2" and acc is not None
      and acc.name == "Friend of mine", str(asked))
check("and adds it with a fresh id, selected",
      len(accounts) == 1 and len(acc.id) == 8
      and int(acc.id, 16) >= 0 and page.selected() is acc
      and changed == [1] and page.b_open.isEnabled())
check("it says what happens the first time",
      page.status.text().startswith("Added Friend of mine. Open it")
      and not page.status.isHidden())
answers.append(("x", False))
check("Cancel adds nothing", page.add() is None and len(accounts) == 1)
for k in range(3):
    answers.append((f"Extra {k}", True))
    page.add()
check("4 accounts at most",
      len(accounts) == 4 and not page.b_add.isEnabled()
      and page.b_add.toolTip() == "4 accounts at most."
      and page.add() is None)
for extra in accounts[1:]:
    page.list.setCurrentRow(accounts.index(extra))
    yes_no[0] = QMessageBox.Yes
    page.remove()
check("Remove asks, then deletes the folder and the account",
      len(accounts) == 1 and accounts[0] is acc
      and page.status.text().startswith("Removed Extra"))
yes_no[0] = QMessageBox.No
page.list.setCurrentRow(0)
page.remove()
check("No keeps it", len(accounts) == 1)
answers.append(("Best   friend", True))
n = len(changed)
page.rename()
check("Rename renames", acc.name == "Best friend"
      and page.list.item(0).text() == "Best friend" and len(changed) == n + 1)
answers.append(("Best friend", True))
page.rename()
check("the same name changes nothing", len(changed) == n + 1)

if HAVE_PASTA:
    acc.prepare()
    with open(os.path.join(acc.data(), "fail"), "w") as fh:
        fh.write("boom: Failed to fetch remote manifest")
    open(CALLS, "w").close()
    check("Open opens it", page.open_selected())
    until(lambda: calls("systemd-run"), 10)
    scopes = calls("systemd-run")
    check("in a scope of its own on the desktop",
          len(scopes) == 1
          and scopes[0][1:5] == ["--user", "--scope", "--quiet", "--collect"]
          and scopes[0][5].startswith(f"--unit=wayclick-sober-{acc.id}-")
          and scopes[0][6] == "flock", str(scopes))
    check("a Sober that fails at once says why, in red",
          until(lambda: "boom: Failed to fetch remote manifest"
                in page.status.text(), 20)
          and "color:#d04030" in page.status.styleSheet()
          and page.status.text().startswith("Sober did not open in Best "
                                            "friend:"), page.status.text())
    os.remove(os.path.join(acc.data(), "fail"))
    with open(os.path.join(acc.data(), "who"), "w") as fh:
        fh.write("page")
    states.clear()
    os.environ["XDG_ACTIVATION_TOKEN"] = "wctest-token"
    check("Open again", page.open_selected()
          and until(lambda: report("page") is not None, 20), acc.log_tail())
    check("the list shows it open, with the dot, and Close works",
          until(lambda: acc.id in page.open_ids, 5)
          and page.list.item(0).text() == "● Best friend (open)"
          and page.b_close.isEnabled() and not page.b_open.isEnabled()
          and not page.b_remove.isEnabled() and states)
    rep3 = report("page")
    os.environ.pop("XDG_ACTIVATION_TOKEN")
    check("as the page opens it: in the account network, without the "
          "activation token of WayClick",
          rep3["net"] != HOSTNET and rep3["token"] is None, str(rep3))

    def log_more(text):
        with open(acc.log_path(), "a") as fh:
            fh.write(text)

    log_more('info: app_interface$json: {"type":"did_handle_app_startup"}\n')
    page.refresh()
    check("once Roblox is up, Opening… becomes open",
          page.status.text() == "Best friend is open.", page.status.text())
    # o que o Sober da conta fazia antes do namespace de tempo: para, e
    # fica aberto com a janela de erro dele
    log_more("FATAL: application called abort: status != "
             "SL_RESULT_SUCCESS\nFATAL: Crash: Previous error is fatal, "
             "trying to show GUI error dialog...\nFATAL: Crash: Aborting..."
             "\n")
    page.refresh()
    stopped, red = page.status.text(), page.status.styleSheet()
    page._say("")
    page.refresh()
    check("a Sober that stops says so, in red, once",
          stopped == "Sober stopped in Best friend: application called "
          "abort: status != SL_RESULT_SUCCESS"
          and "color:#d04030" in red and page.status.text() == "", stopped)
    set_ps(("page", rep3["pid"]), ("usual", user.pid))
    states.clear()
    shut = page.close_selected()
    check("Close closes it", shut and (
        (page.list.item(0).text() == "Best friend (closing…)"
         and not page.b_close.isEnabled()) or not acc.running()),
        page.list.item(0).text())
    check("and the page sees it closed",
          until(lambda: acc.id not in page.open_ids
                and page.status.text() == "Best friend is closed.", 15)
          and page.list.item(0).text() == "Best friend" and states,
          page.status.text())

    with open(os.path.join(acc.data(), "who"), "w") as fh:
        fh.write("page2")
    check("open once more", page.open_selected()
          and until(lambda: report("page2") is not None, 20)
          and until(lambda: acc.id in page.open_ids, 5), acc.log_tail())
    os.kill(report("page2")["pid"], signal.SIGTERM)
    check("an account that closes by itself (its window) shows closed",
          until(lambda: acc.id not in page.open_ids
                and page.status.text() == "Best friend is closed.", 15),
          page.status.text())

    class Done:
        returncode = a.SOBER_BUSY

        def poll(self):
            return a.SOBER_BUSY

    acc.proc, acc.started = Done(), time.monotonic()
    page.refresh()
    check("an flock that found it taken says it is already open",
          page.status.text() == "Best friend is already open.")

a.LANG = "pt_BR"
page.retranslate()
check("the page in Portuguese",
      page.b_add.text() == "Adicionar conta…" and page.b_open.text() == "Abrir"
      and page.b_close.text() == "Fechar"
      and page.hint.text().startswith("Cada conta é um Sober separado")
      and page.risk.text().startswith("O Roblox chama"))
a.LANG = "en_US"
page.retranslate()
page.shutdown()

# ------------------------------------------- o App: combo e configuração --
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US",
               "dock_account": "3c4d5e6f",
               "sober_accounts": [{"id": "3c4d5e6f", "name": "Alt"},
                                  {"id": "bad", "name": "nope"}]}, fh)
w = a.App()
w.show()
pump(0.3)
items = [(w.dock_acct.itemText(k), w.dock_acct.itemData(k))
         for k in range(w.dock_acct.count())]
check("the app loads the accounts and the Dock account",
      [x.name for x in w.sober_accounts] == ["Alt"]
      and items == [("Your usual Sober", ""), ("Alt", "3c4d5e6f")]
      and w.dock_acct.currentData() == "3c4d5e6f"
      and w.dock.account is w.sober_accounts[0], str(items))
check("the page uses the same list", w.sober_page.accounts is w.sober_accounts)
answers.append(("Second", True))
w.sober_page.add()
check("a new account shows up in the Dock combo",
      w.dock_acct.count() == 3 and w.dock_acct.itemText(2) == "Second"
      and w.dock_acct.currentData() == "3c4d5e6f")
w.sober_page.list.setCurrentRow(0)
answers.append(("Alt two", True))
w.sober_page.rename()
check("a rename too", w.dock_acct.itemText(1) == "Alt two")
yes_no[0] = QMessageBox.Yes
w.sober_page.remove()
check("removing the Dock account goes back to the usual Sober",
      w.dock_acct.count() == 2 and w.dock_acct.currentData() == ""
      and w.dock.account is None)
w.dock_acct.setCurrentIndex(1)
check("picking an account sets it for the Dock",
      w.dock.account is not None and w.dock.account.name == "Second")
idx = w.tabs.indexOf(w.sober_page)
if HAVE_PASTA:
    second = w.sober_accounts[0]
    second.prepare()
    with open(os.path.join(second.data(), "who"), "w") as fh:
        fh.write("second")
    w.sober_page.list.setCurrentRow(0)
    gone(os.path.join(OUT, "second.json"))
    w.sober_page.open_selected()
    check("an open account puts the dot on the Sober accounts page",
          until(lambda: report("second") is not None
                and w.tabs.tabText(idx).startswith("● "), 20),
          w.tabs.tabText(idx))
    set_ps(("second", report("second")["pid"]))
    w.sober_page.close_selected()
    check("and it goes when it closes",
          until(lambda: not w.tabs.tabText(idx).startswith("● "), 15),
          w.tabs.tabText(idx))
a.LANG = "pt_BR"
w.retranslate()
check("translated: the combo and the page name",
      w.dock_acct.itemText(0) == "O seu Sober de sempre"
      and w.tabs.tabText(idx) == "Contas do Sober"
      and w.dock_acct.currentData() == w.sober_accounts[0].id)
a.LANG = "en_US"
w.retranslate()
w.cleanup()
with open(a.CFG) as fh:
    cfg = json.load(fh)
check("accounts and the Dock account are saved",
      cfg.get("sober_accounts") == [{"id": w.sober_accounts[0].id,
                                     "name": "Second"}]
      and cfg.get("dock_account") == w.sober_accounts[0].id,
      str({k: cfg.get(k) for k in ("sober_accounts", "dock_account")}))

# ------------------------------------------------------------ limpeza --
for x in [acc, dockie] + list(accounts) + list(w.sober_accounts):
    if x.proc is not None and hasattr(x.proc, "pid") and x.proc.poll() is None:
        KIDS.append(x.proc)
for p in KIDS:
    if p.poll() is None:
        stop_group(p)
listener.close()
leftover = [p.pid for p in KIDS if p.poll() is None]
check("nothing is left running", not leftover, str(leftover))
code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
