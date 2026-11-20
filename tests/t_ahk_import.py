"""Importador de AutoHotkey: cada script de tests/ahk vira o que deveria.

Puro texto, sem device nenhum. Cobre v1 e v2, SetTimer com e sem ternário,
laços com while/Loop/break, GetKeyState nos dois estilos, KeyWait, remapeamento,
atalhos empilhados, Gosub, Send com chaves, modificadores e texto, Click,
roda, Random, SetKeyDelay, BOM e CRLF, e o que não dá para importar.

    QT_QPA_PLATFORM=offscreen python3 tests/t_ahk_import.py
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import wayclick as a                                        # noqa: E402

a.LANG = "en_US"
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ahk")
CASES = {
    "SpamE F1.ahk": (["F1, toggle, every 15.6 ms: Tap E"],
                     ["rounds timers up to 10 or 15.6 ms"]),
    "v2_toggle.ahk": (["F2, toggle, every 50 ms: Tap Space"], []),
    "v2_func.ahk": (["F1, toggle, every 15.6 ms: Tap E"], ["SetTimer 1 ms"]),
    "ternary.ahk": (["F1, toggle, every 15.6 ms: Tap Q",
                     "Ctrl+Shift+F2, toggle, every 15.6 ms: Tap Q"],
                    ["SetTimer 10 ms"]),
    "braces_else.ahk": (["F1, toggle, every 25 ms: Tap E ×2, "
                         "Move to 100, 200, Click Left"],
                        ["measured them from the active window"]),
    "v1_while_toggle.ahk": (["F3, toggle: Hold W, Wait 100 ms, Release W, "
                             "Wait 50 ms"], []),
    "random_sleep.ahk": (["F10, toggle: Tap 1, Wait 80 to 120 ms"], []),
    "hold_loop.ahk": (["Left click, while held: Click Left, Wait 30 ms"],
                      ["clicks its own button"]),
    "loop_break.ahk": (["Side click, while held: Tap E, Wait 20 ms"], []),
    "v1_gks.ahk": (["F6, while held: Tap R, Wait 15.6 ms"],
                   ["Sleep 5: on Windows AutoHotkey waits at least"]),
    "v2_hold.ahk": (["Extra click, while held: Click Left, Wait 40 to 60 ms",
                     "F1, once: Wait 500 ms, Tap F"], []),
    "keywait.ahk": (["F4, while held, no repeat: Hold Right button",
                     "` (grave), once: Tap A"], ["Pause is not supported"]),
    "remap.ahk": (["Caps Lock, while held, no repeat: Hold Left Ctrl",
                   "Ctrl+J, once: Type “Hello World”, Tap Enter",
                   "Alt+A, once: Hold Left Ctrl, Tap C, Release Left Ctrl, "
                   "Wait 50 ms, …"], []),
    "stacked.ahk": (["F7, once: Tap X", "F8, once: Tap X",
                     "Numpad +, once: Hold Left button, Wait 15.6 ms, "
                     "Release Left button"], ["Sleep 10"]),
    "settimer_auto.ahk": (["no hotkey, always, every 60000 ms: Tap Space"],
                          ["starts with the script",
                           "ExitApp is not supported"]),
    "combo.ahk": (["F5, once: Click Right, Wait 100 ms, Click Right, "
                   "Wait 100 ms, …", "F6, once: Wheel down ×3",
                   "Numpad 1, once: Tap Numpad 5"],
                  ["hotstrings are not supported", "(#If)",
                   "two-key combinations", "release hotkeys (Up)"]),
}

bad = 0
for name, (want, notes) in sorted(CASES.items()):
    imp = a.import_ahk_file(os.path.join(HERE, name))
    got = [a.macro_summary(m) for m in imp.macros]
    text = "\n".join(a.note_text(n) for n in imp.notes)
    miss = [n for n in notes if n not in text]
    ok = got == want and not miss
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print("   got:  ", got)
        print("   want: ", want)
        if miss:
            print("   notes missing:", miss, "\n   notes:", text)

# detalhes que o resumo não mostra
imp = a.import_ahk_file(os.path.join(HERE, "random_sleep.ahk"))
checks = [
    ("SetKeyDelay in Event mode sets the press time",
     imp.macros[0]["press_ms"] == 30.0),
    ("SendInput ignores SetKeyDelay, like AutoHotkey",
     a.import_ahk_file(os.path.join(HERE, "SpamE F1.ahk"))
     .macros[0]["press_ms"] == 0.0),
    ("~ lets the key pass", not a.AhkImport("~F1::Send x\n").macros[0]
     ["block"]),
    ("without ~ the key is hidden", a.AhkImport("F1::Send x\n").macros[0]
     ["block"]),
    ("scancode and vk names", [m["code"] for m in a.AhkImport(
        "sc03B::Send a\nvk70::Send b\n").macros] == [59, 59]),
    ("an empty file imports nothing", a.AhkImport("").macros == []),
    ("garbage does not crash", isinstance(a.AhkImport(
        "}}}{{{ ::: \x00 if ( while ) Loop, Parse\n::\n").macros, list)),
    ("macros are capped", len(a.AhkImport("".join(
        f"F{1 + i % 12}::Send {i % 10}\n" for i in range(80))).macros)
     == a.MAX_MACROS),
    ("a big Loop count is cut to the step limit", len(a.AhkImport(
        "F1::\nLoop 100000\n{\nSend a\n}\nreturn\n").macros[0]["steps"])
     <= a.MAX_STEPS),
    # tempo como no Windows: abaixo de um tique, um tique; acima, o pedido
    ("SetTimer 16 stays 16 ms", a.AhkImport(
        "F1::SetTimer, S, 16\nS:\nSend e\nreturn\n").macros[0]
     ["period_ms"] == 16.0),
    ("Sleep 0 waits nothing, Sleep 20 waits 20", [
        st.get("ms") for st in a.AhkImport(
            "F1::\nSend a\nSleep 0\nSend b\nSleep 20\nreturn\n")
        .macros[0]["steps"] if st["op"] == "wait"] == [0.0, 20.0]),
    ("Random 1 to 10 waits one tick", [
        (st["ms"], st.get("max")) for st in a.AhkImport(
            "F1::\nRandom, r, 1, 10\nSleep, %r%\nSend a\nreturn\n")
        .macros[0]["steps"] if st["op"] == "wait"]
     == [(a.AHK_TICK_MS, None)]),
    ("the short Sleep note shows once per script", sum(
        "waits at least" in a.note_text(n) for n in a.AhkImport(
            "F1::\nSleep 1\nSend a\nSleep 2\nreturn\n").notes) == 1),
    # um macro na tecla de armar o clique (F8 de fábrica) ganha aviso
    ("an F8 macro warns about the F8 arm hotkey", "F8 is also the arm"
     in a.import_report(a.import_ahk_file(os.path.join(HERE, "stacked.ahk")),
                        "stacked.ahk", (a.KEYS["F8"], "F8"))),
    ("Ctrl+F8, a disabled F8 or another key do not warn", not any(
        a.arm_clash_text(ms, a.KEYS["F8"], "F8") for ms in (
            [{"code": a.KEYS["F8"], "mods": ["ctrl"], "mode": "once"}],
            [{"code": a.KEYS["F8"], "enabled": False, "mode": "once"}],
            [{"code": a.KEYS["F7"], "mode": "once"}]))),
]
for label, ok in checks:
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}")

# o que vem da config ou do importador passa pela validação
m = a.normalize_macro({"code": "x", "mode": "nope", "period_ms": -5,
                       "steps": [{"op": "tap", "code": 0}, {"op": "rm -rf"},
                                 {"op": "wait", "ms": "1e9"},
                                 {"op": "text", "text": "a" * 900}]})
ok = (m["mode"] == "toggle" and m["code"] == 0 and m["period_ms"] == 0.0
      and [s["op"] for s in m["steps"]] == ["wait", "text"]
      and m["steps"][0]["ms"] == 3600000.0
      and len(m["steps"][1]["text"]) == 500)
bad += not ok
print(f"{'PASS' if ok else 'FAIL'}  bad config values are clamped or dropped")

total = len(CASES) + len(checks) + 1
print(f"\n{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
