"""As contas do Sober com o Sober de verdade, numa caixa (kwin_nested.py):
duas contas abertas juntas, cada uma na rede, no namespace de tempo e na
pasta dela, e as duas juntas por WCTEST_SOAK segundos (240; sem o namespace
de tempo a segunda caía em 1 a 3 minutos), fechar uma sem
a outra, um segundo Sober dentro da conta aberta (que para em "An instance
of Sober is already running": o Sober só recebe um link ao abrir, e por isso
a Doca não manda link para uma conta aberta) e uma conta vazia, que mostra a
primeira tela do Sober.

O WayClick não abre aqui: é a SoberAccount dele que abre e fecha cada Sober,
com o escopo próprio dela (wayclick-sober-<conta>-*; o flatpak run passa o
Sober para um escopo app-flatpak-* dele, que o close() acha pela rede) e
um prefixo que põe o Sober no Wayland e no D-Bus da caixa (env -i), com
pouca CPU, disco na menor prioridade e memória limitada. A rede é a do pasta
de cada conta: o Sober precisa de internet (sober.vinegarhq.org e os
servidores do Roblox), sem login nenhum. Do Sober de quem roda só os APKs e
a versão são copiados, como em t_sober_kwin.py; ele e o escopo dele nunca
são tocados, e o teste confere que nenhuma cópia daqui está na rede do
computador.

    nice -n 19 python3 -B tests/t_sober_accounts_kwin.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BASE = tempfile.mkdtemp(prefix="wctest-soberacc-", dir="/var/tmp")
os.environ["XDG_DATA_HOME"] = os.path.join(BASE, "data")
import kwin_nested as kn          # noqa: E402
import t_sober_kwin as S          # noqa: E402
sys.path.insert(0, os.path.dirname(HERE))
import wayclick as a              # noqa: E402

APP = a.SOBER_APP
HOSTNET = os.readlink("/proc/self/ns/net")
HOSTTIME = os.readlink("/proc/self/ns/time")
HOSTTIME_LOCK = "00:07:" + HOSTTIME[len("time:["):-1]
# quanto tempo as duas contas ficam abertas juntas: sem um namespace de
# tempo próprio, a segunda caía entre 1 e 3 minutos (SL_RESULT_SUCCESS)
SOAK_S = int(os.environ.get("WCTEST_SOAK", "240"))
LINK = "roblox://placeId=1818"
R = []


def check(label, ok, detail=""):
    R.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + label
          + (f"  ({detail})" if detail else ""), flush=True)


def read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def scope_procs(pid):
    """Os processos do escopo (cgroup) de `pid`."""
    try:
        with open(f"/proc/{pid}/cgroup") as fh:
            cg = fh.read().strip().split(":", 2)[-1]
        with open(f"/sys/fs/cgroup{cg}/cgroup.procs") as fh:
            return [int(x) for x in fh.read().split()]
    except (OSError, ValueError):
        return []


def time_ns(pid):
    try:
        return os.readlink(f"/proc/{pid}/ns/time")
    except OSError:
        return ""


def locks_of(pids):
    """As travas do /proc/locks desses processos: [dispositivo:inode]."""
    pids = {str(p) for p in pids}
    out = []
    with open("/proc/locks") as fh:
        for line in fh:
            parts = line.split()
            if "->" in parts:
                continue
            if len(parts) >= 6 and parts[4] in pids:
                out.append(parts[5])
    return out


def files_in(path):
    n = 0
    for _root, _dirs, files in os.walk(path):
        n += len(files)
    return n


def main():
    if not shutil.which("pasta"):
        print("SKIP: pasta (passt) is not installed")
        return 0
    if a.sober_tools_missing():
        print("SKIP:", a.sober_tools_missing())
        return 0
    version = S.user_app_version()
    if not version or not S.apk_files():
        print("SKIP: the Sober of this computer has no Roblox downloaded")
        return 0
    home = os.path.join(BASE, "home")
    target = os.path.join(home, ".var", "app", APP)
    os.makedirs(target)
    open(os.path.join(target, "NOT-MOUNTED"), "w").close()
    rt = tempfile.mkdtemp(prefix="wctest-soberacc-rt-")
    os.chmod(rt, 0o700)
    accs = {}
    for acc_id, name, ready in (("a0000001", "A", True),
                                ("b0000002", "B", True),
                                ("c0000003", "Empty", False)):
        acc = a.SoberAccount(acc_id, name)
        acc.prepare()
        if ready:
            # o Sober pronto (APKs e estado), feito numa pasta e posto no
            # lugar da pasta da conta
            tmp = os.path.join(BASE, "prep-" + acc_id)
            S.Sober._prepare(types.SimpleNamespace(home=tmp, version=version))
            os.rmdir(acc.data())
            os.rename(os.path.join(tmp, ".var", "app", APP), acc.data())
            shutil.rmtree(tmp)
        accs[name] = acc
    before_user = S.user_instances()
    scopes = []
    try:
        with kn.Bench(outputs=1) as b:
            os.makedirs(os.path.join(rt, "pulse"), exist_ok=True)
            os.symlink(os.path.join(b.rt, "pulse", "native"),
                       os.path.join(rt, "pulse", "native"))
            vk = {"0x1002": "*radeon*", "0x8086": "*intel*",
                  "0x10de": "*nvidia*"}.get(b.vendor)
            flat = ["flatpak", "run", "--nosocket=x11",
                    "--nosocket=fallback-x11"]
            if vk:
                flat.append(f"--env=VK_LOADER_DRIVERS_SELECT={vk}")
                if vk != "*nvidia*":
                    flat.append("--env=__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/"
                                "x86_64-linux-gnu/GL/default/share/glvnd/"
                                "egl_vendor.d/50_mesa.json")
            flat.append(APP)
            env = {"HOME": home, "USER": os.environ.get("USER", "wctest"),
                   "LOGNAME": os.environ.get("USER", "wctest"),
                   "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                   "XDG_RUNTIME_DIR": rt,
                   "WAYLAND_DISPLAY": os.path.join(b.rt, kn.SOCKET),
                   "DBUS_SESSION_BUS_ADDRESS": f"unix:path={b.rt}/bus",
                   "XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
            box = ["env", "-i"] + [f"{k}={v}" for k, v in env.items()] \
                + ["nice", "-n", "19"]
            limits = ["-p", "CPUWeight=1", "-p", "IOWeight=1", "-p",
                      "MemoryMax=4G", "-p", "TimeoutStopSec=5"]

            def open_acc(acc):
                sp = acc.scope_prefix()
                if not sp:
                    raise RuntimeError("no user systemd for the scopes")
                unit = [x for x in sp if x.startswith("--unit=")][0]
                scopes.append(unit.split("=", 1)[1] + ".scope")
                return acc.launch(flat, prefix=sp + limits + box, home=home)

            def wait_log(acc, secs, keys=("did_handle_app_startup",
                                          "starting onboarding flow",
                                          "FATAL", "remote manifest")):
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    s = acc.log_tail(1 << 22)
                    for k in keys:
                        if k in s:
                            return k
                    if acc.proc.poll() is not None:
                        return f"exited {acc.proc.returncode}"
                    time.sleep(0.3)
                return "timeout"

            def sober_windows():
                return [w for w in b.kwin.windows()
                        if "vinegarhq" in w["cls"].lower()]

            def ours(pid):
                """O escopo de `pid`, se é de uma cópia deste teste: um
                wayclick-sober-*, ou um app-flatpak-* de uma instância da
                caixa (nunca uma do Sober de quem roda) com todos os
                processos fora da rede do computador. Senão None."""
                unit = a.cgroup_unit(pid) if pid else ""
                if unit.startswith("wayclick-sober-"):
                    return unit
                inst = unit[len(f"app-flatpak-{APP}-"):-len(".scope")]
                try:
                    box_insts = set(os.listdir(os.path.join(rt, ".flatpak")))
                    with open(f"/proc/{pid}/cgroup") as fh:
                        cg = fh.read().strip().split(":", 2)[-1]
                    with open(f"/sys/fs/cgroup{cg}/cgroup.procs") as fh:
                        procs = [int(x) for x in fh.read().split()]
                except (OSError, ValueError):
                    return None
                if not unit.startswith(f"app-flatpak-{APP}-") \
                        or inst not in box_insts \
                        or inst in S.user_instances() or not procs:
                    return None
                if any(a.netns_of(q) in (HOSTNET, "") for q in procs):
                    return None
                return unit

            def close(acc):
                """close() da conta, conferindo antes que o escopo que ele
                vai parar é um deste teste."""
                found = acc.instance(env)
                pid = found[1] if found else acc._member()
                unit = ours(pid)
                if not unit:
                    return ("not closed: unexpected unit "
                            f"{a.cgroup_unit(pid) if pid else ''!r}")
                return "ok" if acc.close(env) else "close() said no"

            A, B, E = accs["A"], accs["B"], accs["Empty"]
            n_a = files_in(A.data())
            t0 = time.monotonic()
            open_acc(A)
            ra = wait_log(A, 240)
            check("account A opens Roblox", ra == "did_handle_app_startup",
                  f"{ra} after {time.monotonic() - t0:.0f} s; "
                  + A.log_tail(300))
            t1 = time.monotonic()
            open_acc(B)
            rb = wait_log(B, 150)
            check("account B opens Roblox too, with A open",
                  rb == "did_handle_app_startup",
                  f"{rb} after {time.monotonic() - t1:.0f} s; "
                  + B.log_tail(300))
            end = time.monotonic() + 30
            wins = []
            while time.monotonic() < end:
                wins = sober_windows()
                if len(wins) >= 2:
                    break
                time.sleep(0.5)
            check("two Sober windows in the same KWin", len(wins) >= 2,
                  json.dumps([w["caption"] for w in wins]))
            try:
                _m, rgb = b.kwin.screenshot()
                kn.save_png(rgb, os.path.join(BASE, "two.png"))
                shutil.copyfile(os.path.join(BASE, "two.png"),
                                "/tmp/wctest-sober-accounts-two.png")
            except Exception as e:          # a foto é só para ver
                print("screenshot:", e)
            ia, ib = A.instance(env), B.instance(env)
            check("each account finds its own copy in flatpak ps",
                  ia and ib and ia != ib, f"{ia} {ib}")
            nets = {k: a.netns_of(v[1]) for k, v in (("A", ia), ("B", ib))
                    if v}
            check("each in a network of its own, none the computer's",
                  len(set(nets.values())) == 2
                  and HOSTNET not in nets.values(), json.dumps(nets))
            pa, _na = A.run_info()
            check("the pid WayClick keeps is the one in flatpak ps",
                  ia is not None and pa == ia[1], f"run {pa}, ps {ia}")
            check("Sober writes into the account folder, never the usual one",
                  files_in(A.data()) > n_a
                  and os.listdir(target) == ["NOT-MOUNTED"]
                  and "NOT-MOUNTED" not in A.log_tail(1 << 22),
                  f"{n_a} -> {files_in(A.data())} files")
            check("the Sober of this computer is untouched",
                  S.user_instances() == before_user
                  and not ({x[0] for x in (ia, ib) if x}
                           & S.user_instances()))

            # ------------------- o namespace de tempo, e as duas juntas --
            procs = {k: scope_procs(v[1]) for k, v in (("A", ia), ("B", ib))
                     if v}
            tns = {k: sorted({time_ns(q) for q in v} - {""})
                   for k, v in procs.items()}
            check("each account has a time namespace of its own, neither "
                  "the computer's",
                  len(tns) == 2 and all(len(v) == 1 for v in tns.values())
                  and tns["A"] != tns["B"]
                  and HOSTTIME not in tns["A"] + tns["B"], json.dumps(tns))
            t_soak = time.monotonic()
            end = t_soak + SOAK_S
            dead = ""
            while time.monotonic() < end and not dead:
                for acc in (A, B):
                    if acc.proc.poll() is not None or not acc.running():
                        dead = f"{acc.name} closed"
                    elif "SL_RESULT_SUCCESS" in acc.log_tail(1 << 16):
                        dead = f"{acc.name}: SL_RESULT_SUCCESS"
                time.sleep(2)
            lk = {k: locks_of(v) for k, v in procs.items()}
            own = {k: [x for x in v if x.startswith("00:07:")]
                   for k, v in lk.items()}
            # em 2026-11-24 o Sober travava ~100 vezes o /proc/self/ns/time
            # (uma por objeto de áudio, parece); em 2026-11-27, com o mesmo
            # Sober 1.8.0 e o Roblox mais novo, nenhuma em 240 s. A contagem
            # fica no detalhe; o que importa é nenhuma no namespace de sempre
            check("their locks on /proc/self/ns/time are on their own "
                  "namespaces, never the one of the usual Sober",
                  not any(HOSTTIME_LOCK in v for v in lk.values()),
                  json.dumps({k: sorted(set(v)) + [len(v)]
                              for k, v in own.items()}))
            check(f"both stay open together for {SOAK_S} s",
                  not dead and A.running() and B.running(),
                  (dead or "ok") + f" after "
                  f"{time.monotonic() - t_soak:.0f} s")

            # ------------------- um segundo Sober dentro da B aberta --
            # o que a Doca não faz: entrar na rede e na pasta da B e abrir
            # outro Sober com um link. O Sober só olha o link ao abrir, e o
            # segundo para (e a B segue)
            pb = B._member()
            second = ["nsenter", f"--target={pb}", "--user", "--net",
                      "--mount", "--preserve-credentials", "--"] \
                + flat + [LINK]
            unit = f"wctest-soberacc-second-{os.getpid()}"
            scopes.append(unit + ".scope")
            slog = os.path.join(BASE, "second.log")
            insts = {i for i, _p in a.flatpak_instances(env=env)}
            t2 = time.monotonic()
            with open(slog, "wb") as out:
                sp2 = subprocess.Popen(
                    ["systemd-run", "--user", "--scope", "--quiet",
                     "--collect", f"--unit={unit}"] + limits + box + second,
                    stdout=out, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            end = time.monotonic() + 90
            while time.monotonic() < end and sp2.poll() is None \
                    and "already running" not in read(slog):
                time.sleep(0.5)
            took = time.monotonic() - t2
            stext = read(slog)
            check("a second Sober in the open B stops at \"already running\"",
                  "An instance of Sober is already running" in stext,
                  f"after {took:.0f} s; " + stext[-300:])
            # ele fica aberto com uma janela de erro: fecha só o escopo dele
            for inst, pid in a.flatpak_instances(env=env):
                if inst not in insts and a.netns_of(pid) == B.run_info()[1]:
                    u = ours(pid)
                    if u:
                        S.stop_unit(u)
            S.stop_unit(unit + ".scope")
            check("B is still open", B.running() and B.proc.poll() is None)

            # ---------------------------------- fechar só a A --------
            r = close(A)
            end = time.monotonic() + 30
            while time.monotonic() < end and A.running():
                time.sleep(0.3)
            check("closing A closes A", r == "ok" and not A.running(), r)
            check("and B stays open", B.running() and B.proc.poll() is None
                  and B.instance(env) is not None)
            r = close(B)
            end = time.monotonic() + 30
            while time.monotonic() < end and B.running():
                time.sleep(0.3)
            check("then B closes too", r == "ok" and not B.running(), r)

            # ---------------------------------- uma conta vazia ------
            t3 = time.monotonic()
            open_acc(E)
            re_ = wait_log(E, 120)
            check("an empty account starts with the first screen of Sober",
                  re_ == "starting onboarding flow",
                  f"{re_} after {time.monotonic() - t3:.0f} s; "
                  + E.log_tail(300))
            r = close(E)
            end = time.monotonic() + 30
            while time.monotonic() < end and E.running():
                time.sleep(0.3)
            check("and closes", r == "ok" and not E.running(), r)
            check("still nothing of the Sober of this computer touched",
                  S.user_instances() == before_user)
    finally:
        for unit in scopes:
            S.stop_unit(unit)
        for acc in accs.values():
            if acc.proc is not None and acc.proc.poll() is None:
                try:
                    acc.proc.kill()
                except OSError:
                    pass
        shutil.rmtree(rt, ignore_errors=True)
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"\n{R.count(True)}/{len(R)} passed", flush=True)
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
