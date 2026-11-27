# WayClick

**An autoclicker that actually works on Wayland.**

[Português (BR)](README.pt-BR.md) · [Install](#install) · [Usage](#using-it) · [How it works](#how-it-works)

Instead of fighting the compositor, WayClick creates a **virtual mouse in the
kernel** through `/dev/uinput`, so its clicks arrive the same way your real
mouse's clicks do — no X11, no `xdotool`, no compositor extensions, nothing
running as root.

<p align="center">
  <img src="docs/wayclick-dark.png" width="46%" alt="WayClick, dark theme">
</p>

- **1 click/s down to a 0.1 ms interval** — 10,000 clicks/s, measured at 100%
  delivery on the receiving window
- **Trigger** on any key or any mouse button, set by pressing it: hold left
  click and it presses E, or hold a thumb button, or a key
- **Arm/disarm hotkey** that works even when the window is not focused
- **Hold mode** — clicks only while you physically hold the mouse button
- **Keyboard macro** — up to eight keys at once, each captured the way
  shortcuts are set, each with its own interval; repeat or hold
- **Hotkey macros, AutoHotkey style**: each macro with its own hotkey (key or
  mouse button, with modifiers), toggle, while held, once or always, a list of
  steps, and the hotkey hidden from the game like AutoHotkey does. A
  **Suspend hotkey** switches them all off and on, like `F7::Suspend`.
  **Import AHK** turns a `.ahk` script into macros, and what has no step of
  its own runs on a built in **AutoHotkey interpreter** (variables, math,
  functions, loops). **Hotstrings** (`::btw::by the way`), **window
  conditions** (`#IfWinActive`, `#HotIf WinActive(...)`) and a **sound** when
  a macro turns on or off, which the Settings menu can silence
- **Screen steps** in those macros, like AutoHotkey's `ImageSearch`,
  `PixelSearch` and `PixelGetColor`. Go to an exact spot and click, find an image, check a pixel
  color, wait until it shows up or goes away and choose what comes next, post
  to a Discord webhook with a screenshot, open a link. **Pick on screen** takes
  the spot, the color or the picture from your own screen
- **Farm steps**: *Watch* keeps looking at the screen or a window while the
  other steps run, *Window* finds the game and brings it to the front, *Count*
  and *Stop after N runs* end a farm by themselves. **Record** turns what you
  play into steps, the **Farm assistant** builds a lobby, match, end and again
  loop for a tower defense game, and **Export / Import** shares a macro with
  its pictures in one `.wayclick` file
- **The Dock** opens the game in a KWin of its own, in a window of WayClick,
  and macros set to *Play in the Dock* press keys, click and look at the
  screen only in there, so your mouse and keyboard stay free and the Dock can
  sit behind other windows or minimized
- **The Tower Defense tab** shows the game of the Dock inside WayClick and
  farms a tower defense game from there, with five towers and their spots, a
  timeline you record by playing, the lobby, pictures of the end of a match,
  Start F4, Stop F5 and Pause F6, a log and the counts. It only looks at the
  screen and presses keys, never reading the memory of the game
- **Sober accounts** open more copies of Sober next to your usual one, each
  with its own Roblox sign-in, settings and network, so a second account
  plays at the same time as the first, on the desktop or in the Dock
- **Send key to a window** — pick an open window and feed it a key; for X11
  targets it arrives with no focus change, even minimized
- **Anti-AFK** — nudges the pointer and puts it back, with zero drift
- **Update check** — tells you a new release is out, from GitHub or GitLab,
  without you going to look
- **Tray icon** in four shapes and ten colors, pulsing while it works and
  flashing when you start it; autostart, your system color schemes, English and
  Portuguese

---

## Requirements

| | |
|---|---|
| Desktop | KDE Plasma on Wayland |
| Python | 3.9 or newer |
| Qt bindings | PySide6 (`QtMultimedia` optional — only for the beeps) |
| Permissions | your user in the `input` group, and `/dev/uinput` writable by it |
| Screen steps (optional) | numpy, PyGObject and GStreamer with its PipeWire plugin, plus the desktop portal (`xdg-desktop-portal-kde` on Plasma) |
| Sober accounts (optional) | Sober from Flathub, `pasta` from the `passt` package, util-linux 2.38 or newer, Linux 5.6 or newer, and user namespaces turned on for users |

## Install

### Packages

Grab one from [the latest release](https://github.com/gabrielmf1998/WayClick/releases/latest):

| | |
|---|---|
| **Fedora, RHEL** | `sudo dnf install ./wayclick-*.noarch.rpm` |
| **Debian, Ubuntu** | `sudo apt install ./wayclick_*_all.deb` |
| **Arch, Manjaro** | `sudo pacman -U ./wayclick-*-any.pkg.tar.zst` |
| **Anything else** | `chmod +x WayClick-*.AppImage && ./WayClick-*.AppImage` |

The packages ship the udev rule, so all that is left is joining the `input`
group once: `sudo usermod -aG input $USER`, then log out and back in. The
AppImage carries its own Python and Qt and needs no dependencies at all — only
the hotkey beeps are missing there, since PySide6-Essentials leaves out
QtMultimedia; it falls back to `paplay` from your system.

The steps that look at the screen (find an image, check a pixel color, a
screenshot in a webhook) use numpy, PyGObject and GStreamer from your system,
which the AppImage's own Python cannot load. There they report what is missing
and take their *If not* path, and you type positions by hand instead of
picking them. The rpm and deb recommend those packages, and the Arch package
lists them as optional.

### One command

```bash
curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/WayClick/main/install.sh | bash
```

It detects your distro, installs PySide6, sets up the `uinput` module and its
udev rule, adds you to the `input` group, and drops the app into `~/.local`
with a menu entry. Everything that needs `sudo` is announced before it runs.
Then just run `wayclick`, or find **WayClick** in your application menu.

> Piping a script into `bash` means trusting it. Read it first if you like:
> [`install.sh`](install.sh).

### By hand

Same four steps on every distro; only the package name changes.

**1. Install PySide6**

```bash
sudo pacman -S pyside6                                   # Arch, Manjaro
sudo dnf install python3-pyside6                         # Fedora
sudo apt install python3-pyside6.qtwidgets \
                 python3-pyside6.qtmultimedia            # Debian, Ubuntu
pip install --user PySide6                               # anything else
```

**2. Make sure the `uinput` module is loaded, now and at boot**

```bash
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
```

**3. Let the `input` group use `/dev/uinput`**

On several distros the device is created as `root:root`, so group membership
alone is not enough:

```bash
sudo tee /etc/udev/rules.d/99-wayclick-uinput.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput
```

**4. Join the `input` group**

```bash
sudo usermod -aG input $USER
```

Group changes only apply to **new** sessions. Log out and back in — or start it
anyway: WayClick notices the mismatch and relaunches itself through `sg input`,
which works immediately and asks for no password.

**Run it**

```bash
git clone https://github.com/gabrielmf1998/WayClick.git
cd WayClick
python3 wayclick.py
```

## Using it

```bash
wayclick            # normal
wayclick --tray     # start hidden in the system tray
wayclick --import script.ahk --dry-run   # what an AutoHotkey script becomes
wayclick --version
```

The pages sit in a sidebar on the left, in two groups, as in System
Settings. **Automation** has **Click**, **Keyboard**, **Hotkeys**, **Window**
and **Trigger**, and **Games** has **Dock**, **Tower Defense** and **Sober
accounts**. **Hotkeys** holds the AutoHotkey style macros, which run on their
own hotkeys and do not need Start. **Click**, **Keyboard** and **Window** are
the three things Start can run. Each has an *enable* box, and its name in the
sidebar shows a dot when it is on, so you can see what is armed without
opening it. Enable any combination. **Trigger** is how it all starts and
stops, plus Anti-AFK, which runs on its own outside Start/Stop. **Dock** opens
the game in [the Dock](#the-dock), and **Sober accounts** opens
[a second Sober](#sober-accounts).

The search box above the pages (Ctrl+F) finds a page by its name or by any
text in it, and Ctrl+Tab or Ctrl+PgDown goes to the next page (Ctrl+Shift+Tab
or Ctrl+PgUp back). In a window too narrow for the names the sidebar shows
only the icons, with each name in its tooltip and the dot on the icon, and
the search becomes a button. Status and the Start button stay visible below
the pages, and the window opens at 660×548 (709 wide in Portuguese), small
enough for a 1366×768 screen.

The menu bar has the rest: **File** starts, stops, hides to tray and quits;
**Settings** holds **Theme** and **Language** (English, Português BR) plus the
sound and autostart toggles; **Help** links here and shows the version. Both
preferences are saved. The language defaults to your locale, so a Brazilian
system opens in Portuguese with no setup.

**Tray icon** has its own submenu: the **shape** (cursor, mouse, dot or ring)
and the **color** — one of nine fixed colors including white and black, or
*Match state*, which keeps the default grey stopped, blue armed, green
clicking. The outline flips between dark and light depending on the fill, so
white and black stay visible on any panel. The glyph pulses while running, and
starting or stopping pops it from small to full size behind a ring that
expands and fades — the feedback you want when triggering by hotkey with the
window hidden.

**Theme** reads the color schemes installed on your system
(`/usr/share/color-schemes/*.colors`), so "Breeze Dark" here is the real Breeze
Dark, not an approximation — any scheme you install shows up in the menu. Two
built-in schemes cover systems that ship none, and *System* hands the styling
back to your desktop.

### Click

**Mouse** — pick which mouse to use. Every mouse the system exposes is listed;
`↻` rescans after plugging one in. This matters more than it looks: the virtual
device is created as a *clone* of the mouse you pick (same name, vendor and
product id), which is how KDE applies **your** pointer speed and acceleration
profile to it. Without that, the pointer falls back to system defaults and feels
wrong the moment hold mode takes over.

**Interval** — time between clicks, from 1000 ms down to 0.1 ms, with the
clicks/s equivalent shown below it. Lower is faster.

**Button** — left, right or middle.

### Keyboard macro

Presses a key through a second virtual device, a real keyboard as far as the
kernel is concerned. To choose the **key** you click the field and press the
key you want, the way you would set a shortcut — no list to hunt through, and
any key on your keyboard works. Then pick the **action**: `Repeat` taps it at
the interval you set, `Hold` presses it once and keeps it down. The key is
always released on stop, on quit, and if the process dies — it never stays
stuck.

**+ Add key** gives you another line, up to eight. Each line is a key, an
interval and an action of its own, and each runs on its own thread — which is
the point: moving with `W` every second while `Space` fires every 50 ms is one
macro, not two settings fighting over one interval. Lines can be added and
removed while it runs, and `−` takes one out; the last line stays. Two lines on
the same key would fight over its state, so only the first of them runs and the
status line says so.

One thing to expect in `Hold`: holding a normal key makes the system's own key
repeat kick in, exactly as if you held it on your keyboard. Modifiers (Shift,
Ctrl, Alt, Super) do not repeat, so they stay cleanly held.

### Hotkeys (AutoHotkey style)

What AutoHotkey does on Windows, for Wayland: each **macro** has its own
hotkey and runs from any window, whatever has focus, even with WayClick
stopped. Several live side by side, like several `.ahk` scripts running at
once, and each one shows a mark in the list while it runs (plus a ⚡ in the
status line and an animated tray icon).

A macro is

- a **hotkey**: any key or mouse button, set by pressing it, optionally with
  Ctrl, Shift, Alt or Super. Extra modifiers do not get in the way: an `F1`
  macro still fires while you hold Shift to sprint. When two macros share a
  key, `Ctrl+F1` beats `F1` while Ctrl is down.
- **Hide the hotkey from other apps**: on by default, as in AutoHotkey, so the
  game never sees the `F1` you pressed. Turn it off and the key also does its
  usual job (AutoHotkey's `~` prefix).
- a **mode**: *Toggle* (press to start, again to stop), *While held* (stops the
  moment you let go), *Once per press*, or *Always* (no hotkey, runs whenever
  macros are enabled; good for an anti idle key every minute).
- **Repeat the steps every N ms**, counted start to start like `SetTimer`, or
  as soon as they end. **Key press time** is how long each tap stays down;
  *auto* is 40 ms or half the repeat time, because games read keys once per
  frame and miss a tap that is too short.
- **steps**, in order: tap a key or click (N times), hold down, release, wait
  (exact, or a random time between two values), type text, scroll the wheel,
  move the pointer by an amount, the [screen steps](#screen-steps), and
  **Run AutoHotkey code** (see [the interpreter](#the-autohotkey-interpreter)).
- **Trigger**: a hotkey, or **Typed text (hotstring)**. A hotstring runs when
  you type its text and then a space, Enter or punctuation, like
  `::btw::by the way`: it erases what you typed and runs the steps, so a
  *Type text* step makes it a text replacement. Capitals are copied (`btw`,
  `Btw`, `BTW` give `by the way`, `By the way`, `BY THE WAY`), it does not
  fire inside a word, and Backspace edits what it watches. Its options are
  the AutoHotkey ones: `*` (no ending key needed), `?` (also inside a word),
  `C` (exact case), `C1` (do not copy capitals), `B0` (keep what was typed)
  and `O` (leave out the ending key). A hotkey on the same key wins, as in
  AutoHotkey, so with a `Space` macro end it with Enter.
- **Only in window**: like `#IfWinActive`, the hotkey works only while that
  window is active (the app name, like `Sober`, or part of the title;
  *Open windows…* lists them), and elsewhere the key does its normal job.
  *not in it* is `#IfWinNotActive`. A macro for one window beats a global
  macro on the same key. It reads the active window from KWin, so it needs
  KDE Plasma; elsewhere the hotkey works in every window and says so once.
- **Sound when it turns on or off**: two notes going up when the macro
  starts and going down when it stops. On by default for *Toggle* macros;
  tick it for *While held* or *Once per press* (a once macro sounds again at
  the end only when it ran for half a second or more, like a `while` loop).
  A `SetTimer` started from code sounds up when it starts and down when the
  last timer stops. **Settings > Sound when a macro turns on or off**
  silences every macro at once; the Suspend hotkey uses the same sounds.

Whatever a macro holds is released when it stops, when you disable it and when
WayClick quits. A modifier you are holding for the hotkey is let go before the
steps run, as AutoHotkey does, so `Alt+A` sending `Ctrl+C` arrives as `Ctrl+C`
and not `Ctrl+Alt+C`. Opening the editor pauses the macros, so you can pick a
hotkey that a running macro already uses.

The **Suspend hotkey** under the list (none by default) does what
`F7::Suspend` does in a script: one press turns the hotkey macros off, so
their keys reach the game as plain keys again, and the next press turns them
back on, with the same sound as the arm hotkey. The game never receives the
Suspend key itself, modifiers work as for a macro (`Ctrl+F7` leaves a plain
`F7` alone), and a warning shows when it is also a macro's key or the
arm/disarm hotkey. One difference from AutoHotkey: Suspend there leaves a
running macro alone (that is `Pause`), while WayClick also stops the macros
that are running and lets go of anything they hold, so a bhop that is
spamming a key stops the moment you press it.

**Import AHK…** reads a `.ahk` file (v1 or v2) and turns its hotkeys into
macros, showing first what each one became and what it could not convert, by
line number. The patterns game scripts use become plain steps you can edit,
and whatever has no step of its own (variables, math, functions, string
work) runs as AutoHotkey code on the
[interpreter](#the-autohotkey-interpreter):

| In the script | Becomes |
|---|---|
| `F1::` + `toggle := !toggle` + `SetTimer, Label, 50` / `Off` | Toggle, every 50 ms |
| `SetTimer(Func, t ? 50 : 0)`, `% toggle ? 1 : "Off"` | Toggle, every 50 / 1 ms |
| `while toggle { ... }`, `Loop { if !toggle break ... }` | Toggle, steps in a loop |
| `while GetKeyState("X", "P") { ... }`, `if state = U break` | While held |
| `Send {RButton down}`, `KeyWait`, `{RButton up}` | While held, button held down |
| `a::b`, `CapsLock::Ctrl` | While held, a remap |
| `F5:: Send ..., Click, Sleep, Loop 3 { }` | Once per press |
| `SetTimer` in the auto-execute section | Always |
| `F7::Suspend`, `Suspend, Toggle`, `Suspend(-1)` | the Suspend hotkey |
| `PixelGetColor, c, X, Y` + `if (c = 0x...)` | Check a pixel color, then / else |
| `PixelSearch, Px, Py, X1, Y1, X2, Y2, color, var` + `if !ErrorLevel` + `Click %Px%, %Py%` | Check a pixel color in that area, click where found |
| `ImageSearch, X, Y, X1, Y1, X2, Y2, *40 file.png` + `if (ErrorLevel = 0)` + `Click` | Find an image in that area, click it, else the other branch |
| v2 `if PixelSearch(&x, &y, ...)`, `if !ImageSearch(...) return`, `found := ImageSearch(...)` | the same steps |
| `Send` with `{Key down}`, `{Key up}`, `{Key N}`, `^!+#`, text, `{Click}`, `{WheelDown N}`; `Click`, `Sleep`, `Random`, `MouseMove ..., R`, `Gosub`, functions, stacked hotkeys, `$`, `*`, `~`, `sc`/`vk` names, `SetKeyDelay` | the matching steps |
| `::btw::by the way`, `:*C:abbr::`, a hotstring with a body, `#Hotstring` | a hotstring macro |
| `#IfWinActive ahk_exe RobloxPlayerBeta.exe`, `#IfWinNotActive`, v2 `#HotIf WinActive("...")` | Only in window (Windows programs map to their Linux window, like Roblox to Sober) |
| `#MaxThreadsPerHotkey 2` | a second press runs alongside the first, so `toggle := !toggle` + `while toggle` stops it |
| `n++`, `x := y * 2`, `if (a > b && c)`, functions with `ByRef` and `static`, `Send %n%`, `SetTimer` labels with math | Run AutoHotkey code |

`Key Up` hotkeys, `A & B` combinations, objects and arrays, `for`,
`switch`, `Loop, Parse` and GUI commands are reported and skipped, by line;
the rest of the script still imports. `ahk_class` matches the app name or
the title, and `#IfWinExist` or windows by id or group work in every window,
each with a note. `Click X, Y` and `MouseMove X, Y` become
[Go to a screen position](#screen-steps) steps, with a note when the script
measured them from the active window, which is AutoHotkey's default.

The screen commands keep AutoHotkey's rules, from its
[PixelGetColor](https://www.autohotkey.com/docs/v1/lib/PixelGetColor.htm),
[PixelSearch](https://www.autohotkey.com/docs/v1/lib/PixelSearch.htm) and
[ImageSearch](https://www.autohotkey.com/docs/v1/lib/ImageSearch.htm) pages.
In v1 a color is blue, green, red (`0x0000FF` is red) unless the command says
`RGB`, and v2 always uses red, green, blue. v1 `PixelSearch` looks column by
column from the left unless `Fast` is given, and `X1 > X2` or `Y1 > Y2`
search from the other side; the variation becomes the step's tolerance. The
`*n` variation of `ImageSearch` (0 to 255) becomes a similarity (`*40` is
87 %), and the picture is looked for next to the script. `A_ScreenWidth`,
`A_ScreenHeight` and the `A_VirtualScreen` names mean the whole screen. The
`if`/`else` after the search becomes the step's *If it happens* and *If not*,
including `else if` chains, `break` and `return`. Two things differ: WayClick
clicks the center of a found picture, where AutoHotkey gives its top left
corner, and a click at a variable that no search filled is skipped with a
note.

The bhop script a friend wrote in Python for Linux (hold Space and F9 is
pressed every 15 ms, F7 pauses) is a three line AutoHotkey script, and imports
as *Space, while held, tap F9, wait 15.6 ms* plus the Suspend hotkey F7:

```ahk
F7::Suspend
*$Space:: while GetKeyState("Space", "P") {
    Send {Blind}{F9}
    Sleep 15
}
```

A loop like that holds each press for its shortest wait (15.6 ms here)
instead of the usual 40 ms, so a round takes 31.2 ms, about 32 F9 a second,
which is the 30 ms of the original and what Roblox reads (about one key per
frame at 60 fps, see below).

The script attached to the first request for this feature imports as *F1,
toggle, every 15.6 ms, tap E*:

```ahk
F1::
toggle := !toggle
if (toggle)
        SetTimer, SpamE, 1
else
        SetTimer, SpamE, Off
return

SpamE:
Send, e
return
```

Why 15.6 and not 1: on Windows, AutoHotkey rounds `SetTimer` periods and
`Sleep` delays up to a multiple of 10 or 15.6 ms (the *Timer precision* notes
in the [SetTimer](https://www.autohotkey.com/docs/v2/lib/SetTimer.htm) and
[Sleep](https://www.autohotkey.com/docs/v2/lib/Sleep.htm) docs), so that
script really pressed E about 64 times a second. The import keeps what the
script did there: a timer or a wait under 15.6 ms becomes 15.6 ms (`Sleep 0`
stays 0), and the report says so. Faster would not help in a game, which reads
keys about once per frame and queues the rest: in Roblox (Sober at 60 fps, see
`tests/t_sober_kwin.py`) half a second of E at 1 ms kept arriving for 18
seconds after the macro was turned off. At 15.6 ms every E still arrives, the
queue is just shorter, and at 33 ms nothing is left over. The macro editor
still takes 1 ms (measured: median 1.000 ms between taps) for a program that
reads that fast.

#### The AutoHotkey interpreter

A **Run AutoHotkey code** step holds AutoHotkey code (v1, or v2 with the box
ticked) and runs it inside WayClick, with the macro's keyboard and mouse. The
importer uses it for hotkeys whose bodies are real programs, and you can
write one by hand. It covers what game scripts use:

- variables that keep their value between presses (so `toggle := !toggle`
  works), `global`, `static`, `local`, and `%var%` in commands
- expressions: math, `//`, `**`, comparisons, `&&`, `||`, `!`, `? :`,
  string concatenation with `.`, `++`, `+=` and the rest
- `if`/`else`, `while`, `Loop`, `Loop N`, `until`, `break`, `continue`,
  `return`, `Gosub`, `Goto`, labels, `try`
- functions with parameters, defaults, `ByRef` (v1) and `&ref` (v2)
- `Send`, `SendInput`, `SendRaw`, `SendText`, `ControlSend` (to the active
  window), `Click`, `MouseClick`, `MouseMove`, `Sleep`, `SetKeyDelay`,
  `GetKeyState`, `KeyWait`, `PixelGetColor`, `PixelSearch`, `ImageSearch`,
  `WinActive`, `WinExist`, `WinActivate`, `WinWaitActive`, `SetTimer`,
  `Suspend`, `SoundBeep`, `MsgBox` and `TrayTip` (as a notification),
  `ExitApp` (stops the macro and its timers)
- `Random`, `Abs`, `Round`, `Floor`, `Ceil`, `Mod`, `Min`, `Max`, `Sqrt`,
  `StrLen`, `SubStr`, `InStr`, `StrReplace`, `Trim`, `StrUpper`,
  `StrLower`, `Format`, `RegExMatch`, `RegExReplace`, `Chr`, `Ord`, and
  `A_Index`, `A_TickCount`, `A_ThisHotkey`, `A_ScreenWidth`,
  `A_ScreenHeight`, `ErrorLevel`, `Clipboard`

The code is checked when you save the step (and when a script is imported),
and an error names the line.
An endless `while` stops when the macro stops, when macros are disabled
(or suspended) and when WayClick quits, like any other macro.

From a terminal, `wayclick --import script.ahk --dry-run` prints the same
report, and without `--dry-run` adds the macros to the config (with WayClick
closed, since a running WayClick saves its own list on exit).

### Screen steps

Five more kinds of step cover what AutoHotkey scripts do with `Click X, Y`,
`ImageSearch`, `PixelGetColor` and `Run`.

| Step | What it does |
|---|---|
| **Go to a screen position** | Puts the pointer on an exact pixel, then clicks (left, right, middle or double) or only moves. *Curved, like a hand* glides there in the time you set instead of jumping, and *Random offset* lands up to that many pixels away each time. |
| **Find an image on screen** | Looks for a picture on every screen, or only inside an area you mark, at a *Similarity* from 50 to 100 % (85 by default). When found it can point at it or click its center. |
| **Check a pixel color** | Reads one point and compares it with a color, red, green and blue each within the *Tolerance*, like `PixelGetColor`. With an area marked (*Search in*) it looks for the color anywhere inside, like `PixelSearch`, and can click where it found it: row by row from the top left, or column by column when it came from a v1 script. |
| **Send a notification** | A desktop notification. With a Discord webhook (channel settings, Integrations) it posts in that channel instead, and can attach a screenshot of each screen. |
| **Open a link or file** | Opens it with the default program, so web pages, `roblox://` and `steam://` links and files all work. |

The image and color steps can wait. *Wait until it* appears or disappears
(for a color, has it or loses it), for up to the time you set, or *check
once*. *If it happens* and *If not* then choose what comes next, which is to
continue, stop the macro, start over from step 1 or go to a step by number.
That is enough for the usual game loop of waiting for a button, clicking it,
waiting for the round to end, collecting and starting over. By default a step
that does not see what it waits for stops the macro, so nothing gets clicked
blind.

**Pick on screen…** fills the step from your own screen. After a countdown
(*Capture after*, 3 s by default) that gives you time to switch to the game,
WayClick takes one picture of every screen and shows it. Click the spot, and
for a color step its color comes along, or drag a box around the picture to
look for. Pictures are saved in `~/.local/share/wayclick/images`, and *Open
file…* takes a PNG, JPEG, BMP or WebP instead.

The first time a step needs the screen, the system asks which screens WayClick
may see, in the same screen sharing dialog video calls use. WayClick asks the
portal to remember the answer and keeps the token it gets back in
`~/.config/wayclick/screen-token`, readable only by you, so where the desktop
supports that, later runs skip the question. The share is open only while a
macro needs it and closes after 30 s without use, so the screen sharing
indicator in the panel only shows while something is looking.

Positions follow the layout in Display Configuration, every screen together,
with 0, 0 at the top left corner of the whole layout. With scaling they are in
scaled units, so a 4K screen at 150 % counts as 2560 wide. AutoHotkey measures
`Click X, Y` from the active window by default, which comes out the same for a
fullscreen game on the leftmost screen.

One limit to know about. While a game keeps the cursor locked, in a first
person camera or while you drag to turn, KWin does not move the pointer at
all, so a position step does nothing there. Use positions where the game shows
a free cursor, such as menus, lobbies, shops and build screens.

### Farming: watch, window, count, record, share

Four more pieces let a macro farm matches by itself, as the tower defense
macros for Windows do, but only by looking at the screen and pressing keys
like a person. WayClick never reads or touches the memory of a game.

| Step or setting | What it does |
|---|---|
| **Watch the screen or a window** | Keeps looking while the next steps run, like a `SetTimer` with `ImageSearch` in AutoHotkey, for an image, a pixel color or a window, every N seconds. When it sees what it waits for it can click it and continue, or take the macro elsewhere (start over, stop, go to a step). It looks while the macro waits (Wait steps, screen searches) and between steps, never in the middle of a key press. When a watch takes the macro to another step, every watch stops until the macro passes over it again. |
| **Check or focus a window** | Finds a window by app name (`Sober` matches `org.vinegarhq.Sober`) or by its whole title, can wait for it to open or close, and can bring it to the front, minimized or not. A title that only contains the text does not count, so with the game closed a terminal or a browser tab about it is not taken for the game. Needs KDE Plasma, since only KWin knows the windows. |
| **Count** | Adds 1 each time the macro passes, and every N times can stop the macro or go to a step. |
| **Stop after N runs** | In the macro itself. A repeating macro stops by itself after that many runs, and a run that starts over does not count. |

Notifications fill in `{run}` (the number of the run), `{time}` (how long the
macro has been running, like `1:02:05`) and `{wins}` for a counter named
wins.

**● Record…** in the macro editor turns what you play into steps. After a
countdown it notes keys, mouse buttons and the wheel until you press the stop
key (F10 by default, which the game does not see), then shows the steps
before adding them after the selected one. A quick press becomes a tap, a
click keeps where it happened (the position comes from KWin), a press that
moved becomes a drag, turns of the wheel in a row become one step, and the
pauses between actions become waits in steps of 10 ms, or are left out.
Mouse movement without a click is not recorded.

**Farm assistant…** on the Hotkeys tab builds a whole farm in one go. You
capture the lobby buttons, pick a strategy (a macro you made or recorded that
places and upgrades the towers) and capture the button that shows at the end
of a match, plus a skip wave button and a reconnect button if the game has
them. It makes an ordinary macro that

1. checks that the game window is open, and if not, opens the link you gave
   and waits for it,
2. brings the game to the front and keeps watching for the window closing
   and for the reconnect button, either of which starts the farm over,
3. clicks the lobby buttons in order (when the first one is missing, a match
   is already on, so it goes straight to the strategy),
4. plays the strategy, clicking the skip button whenever it shows up, with
   the end of the match cutting the strategy short,
5. clicks the end of match button, posts the report to your Discord webhook,
   with a screenshot if you want, and starts again, until the number of
   matches you set.

A Roblox game page link becomes `roblox://placeId=ID`, the format on the
Roblox Creator Hub page about deep linking, and Sober registers itself for
`roblox://` links. Opening a game that way has not been tried with Sober
yet; if it does nothing, paste a link that works for you or leave the field
empty. What the assistant makes can be edited later like any other macro, and
no ready strategies come with WayClick. Check the rules of the game first,
since many online games ban automated farming.

**Export…** saves the selected macro in a `.wayclick` file with its pictures
inside, so a strategy can be shared as one file, and **Import…** adds the
macros of such a file. The Discord webhook never goes into the file, and
webhooks in a file are dropped on import anyway, because a file from someone
else could send pictures of your screen to their channel. Pictures are only
saved when they really are images, and never outside the pictures folder. A
step that would open anything other than a web, `roblox://` or `steam://`
link becomes an empty wait, so the other steps keep their numbers, and the
imported macros arrive turned off.

### The Dock

The tower defense macros for Windows play in a view of their own, so the PC
stays usable while they farm. **The Dock** does the same on KDE Plasma: a
KWin of its own that WayClick opens in a window, with the game inside.
Macros set to **Play in the Dock** work only in there:

- keys, clicks, the wheel and drags go to the game in the Dock, never to the
  window in front, and the pointer and the focus of your desktop stay where
  they are;
- screen, image, color and window steps look at the Dock, with no screen
  sharing dialog;
- the Dock window can sit behind other windows or be minimized, and the game
  keeps running and the macro keeps seeing it (measured with a game that
  waits for vsync in `tests/t_dock_hidden.py`, 60 frames a second in front,
  covered and minimized);
- positions are those of the Dock screen, with 0,0 at its top left corner,
  and the Dock keeps the size you chose (1280×720 by default; resized, it
  goes back), so the spots of a macro stay the same wherever the window is;
- a `roblox://` link in a Dock macro opens inside the Dock, while web links
  open on your desktop as always.

On the **Dock** tab choose the **Size** and the **Game** command
(`flatpak run org.vinegarhq.Sober` by default) and press **Open the Dock**,
and the game starts in it. **Start the game** opens it again after you close
it, opening the Dock first if needed. **Show** brings the Dock window to the
front, and the tab shows ● while the Dock is open. The Dock is an ordinary
window, so you can also click into it and play yourself. In the macro editor
and in the Farm assistant tick **Play in the Dock**; **● Record…** and **Pick
on screen…** then work on the Dock too. Closing the Dock window, or **Close
the Dock**, closes the game and stops its macros, and a Dock macro started
while the Dock is closed says so and does not run.

**Account** picks which Sober the Dock opens, your usual one or one of the
[Sober accounts](#sober-accounts), and other games ignore it. Your usual
Sober opens one copy at a time, so close it before you start it in the Dock,
or pick an account. The Dock page tells you when the Sober it would open is
already open somewhere. Sober takes a `roblox://` link only when it opens, so
a link of a Dock macro is not opened while an account is open in the Dock,
and the Dock says so. The Dock
needs a Plasma Wayland session (`kwin_wayland`) and `dbus-daemon`, which come
with Plasma.

### The Tower Defense tab

The tower defense macros for Windows show the game inside their own window
and farm from there. The **Tower Defense** tab does the same with the Dock:
the game of the Dock shows in the middle of the tab about 20 times a second,
and mouse, wheel and keys over it go to the game, so you can also play from
the tab. The farm only looks at the screen of the Dock and presses keys and
clicks in it. It never reads or touches the memory of the game.

| Part | What is there |
|---|---|
| Left | **Start (F4)**, **Stop (F5)** and **Pause (F6)**, what the farm is doing, how long it has been running, matches, wins and losses, **Open the Dock**, **Dock the game** (minimizes the Dock window while the game keeps playing in the tab) and **Undock**, **Join the game** (opens the game link in the Dock), and the log |
| Middle | The game. **Line up the camera** zooms out and drags down as at the start of each match, and **Show the spots** marks where each tower goes |
| Right | The **Strategy**, **Screens** and **Settings** tabs |

On this tab the WayClick window grows to 1600×900 (less on a smaller screen)
and goes back to its size when you leave the tab. The view stops while the
tab is hidden and drops to 5 frames a second while WayClick is behind
another window.

**Strategy.** A strategy has a name, notes, five towers with the spots where
each goes, a timeline and the lobby.

- **Pick** on a tower, then click on the game where it goes. A right click
  removes a spot and Esc ends.
- **● Record** lines up the camera and then notes what you play on the view.
  1 to 5 takes a tower and a click places it (within 8 px of a spot the tower
  already has, it is that spot), clicking a tower and then E or Z upgrades it
  on the top or bottom path, Q drops the tower in hand, and F10 undoes the
  last action. The times count from when the recording starts, so start it
  when the match starts.
- **Add…**, **Edit…** and **Remove** change the timeline by hand, with
  place, upgrade on either path, sell, press a key, click a picture and wait
  for a picture.
- **Lobby** holds the buttons clicked in order to get into a match, and a
  lobby macro (one of the Hotkeys tab) that plays before them, like walking
  into an elevator.
- **Export…** and **Import…** share a strategy in one `.wayclick` file with
  its pictures. A strategy file given to the import of the Hotkeys tab is
  offered to this tab, and a file of macros given here points to the Hotkeys
  tab.
- A strategy keeps the size of the Dock it was made at, and the tab warns
  when the Dock is another size, since the spots are places on the screen.

**Screens.** Pictures of the game, captured from the Dock with **Capture…**
while it shows them.

| Picture | What the farm does with it |
|---|---|
| Match started | Optional. The clock of the strategy starts when it shows up, and without it right after the lobby |
| Tower placed | A piece of the panel that opens when a tower goes down. With it, a tower that did not go down, like when there is not enough cash yet, is tried again |
| Skip wave | Optional. Clicked whenever it shows up during the match |
| Victory, Defeat | Something that only shows when the match is won, or lost. Counts a win or a loss |
| Reconnect | Optional. Clicked whenever it shows up, and the farm starts over from the lobby |
| Sell button | Optional. Clicked to sell after clicking the tower. Without it the sell key of Settings is pressed |
| After the match | The buttons clicked in order when the match ends, like the one that plays again. With no Victory and Defeat pictures the first one is also how the end of a match is seen |

In the settings of the game turn off Camera Shake and Low Quality Rings,
turn on Show Tower Options (the panel that the Tower placed picture comes
from), keep Cancel Placement on Q and the Switch Hotbar key off Q, and keep
the UI Scale as it was when you captured the pictures.

**Settings.**

| Setting | What it does |
|---|---|
| Game window | The app name of the game window in the Dock, `Sober` by default |
| Game link | Opens the game in the Dock with **Join the game**, and again when the game closes. A Roblox game page becomes `roblox://placeId=ID` and a private server link (`…?privateServerLinkCode=…`) becomes `roblox://placeId=ID&linkCode=CODE`, the two forms on the [Roblox page about deep linking](https://create.roblox.com/docs/production/promotion/deeplinks); a `roblox://` link stays as it is |
| Waits, Longest match, Stop after | How long to wait for each lobby button and for the match to start, how long a match may take before the farm moves on, and how many matches to play (never stops by default) |
| Action speed | Fast, Balanced or Slow, the pauses between pressing, clicking and looking |
| Keep trying to place for, Try again every | How long a tower that does not go down is tried again, and how often |
| Skip waves until | Minutes from the start of the match after which Skip wave is left alone (the whole match by default) |
| Camera | **Line up the camera when a match starts** holds the zoom out key (O) for some time and then drags down with the right button from the middle of the screen |
| Game keys | Upgrade on the top path (E) and on the bottom path (Z), sell (none), cancel placing (Q) |
| Hotkeys | Start F4, Stop F5 and Pause F6, in any window while the Dock is open. A hotkey macro or the WayClick hotkey on the same key takes it first |
| Report | A Discord webhook that gets a line after each match, with a screenshot if you want, filled with `{run}`, `{result}`, `{wins}`, `{losses}` and `{time}` |

Share links (`roblox.com/share?code=…`) are refused. The
[Roblox page about share links](https://create.roblox.com/docs/production/promotion/share-links)
gives them no `roblox://` form, so they would open in the browser, outside
the Dock. Use the game page or a link with `privateServerLinkCode`, or open
the server by hand in the Dock before you press Start.

**How a farm goes.**

1. The game window has to be open in the Dock. When it is not, the farm
   opens the game link in the Dock and waits up to 2 minutes for it.
2. It plays the lobby macro, if there is one, and clicks the lobby buttons
   in order. When the first one does not show up, a match is already on.
3. It waits for Match started, starts the clock of the strategy and lines up
   the camera.
4. Each action runs at its time. A placement presses the key of the tower,
   clicks its spot and waits for the Tower placed picture. When that does not
   show up, Q cancels and the tower is tried again, and Skip wave keeps being
   clicked meanwhile. An upgrade clicks the tower and presses E or Z.
5. Victory, Defeat or the first After the match button end the match. The
   farm counts it, posts the report, clicks the After the match buttons and
   starts over, until the number of matches you set.
6. When the game window closes or the Reconnect button shows up, the round
   starts over, and the game opens again by its link. Five rounds in a row
   without a match stop the farm, since the pictures probably do not match
   the game.

Pause lets go of everything and stops the clock of the match with it, so the
strategy goes on where it was.

This was measured against a fake tower defense game in a nested KWin
(`tests/t_tower_kwin.py`): two matches, with the game crashing in the second
one and opening again by its `roblox://` link, and the view at 20 frames a
second, each capture of a 960×540 Dock taking 2.9 ms, about 7% of a core for
WayClick and 4% for the KWin of the Dock. It has not been tried against
Tower Defense Simulator itself yet, and no ready strategies come with
WayClick. Check the rules of the game first, since many online games ban
automated farming.

### Sober accounts

Sober opens one copy at a time. A second one finds the first through a fixed
port on 127.0.0.1 and does not open, so on Linux two Roblox accounts could
not play at once the way they can on Windows. The **Sober accounts** page
gives each account a Sober of its own, with its own Roblox sign-in, settings
and network, so it opens next to your usual Sober and both play at the same
time.

Press **Add account…**, name it, and press **Open** (or double click it). The
first time, Sober sets itself up in the new account and downloads Roblox
again, and you sign in to Roblox there. After that it opens straight into
Roblox. **Close** closes that account only, **Rename…** renames it, and
**Remove…** deletes its Sober data on this computer (the Roblox sign-in, the
settings and the downloads), while the Roblox account itself stays as it is.
There can be up to four accounts. An account that was open before WayClick
started shows as open, and quitting WayClick leaves the open accounts
running. On the Dock page, **Account** opens one of them in the Dock.

How it works. Each account runs Sober in a network of its own, made by
`pasta`, where the 127.0.0.1 port of your usual Sober does not exist, and no
port is forwarded in or out. In there the account folder
(`~/.local/share/wayclick/sober-accounts/<id>/app`) is mounted over
`~/.var/app/org.vinegarhq.Sober` for that copy only, and `unshare` gives
Sober back your own user, plus a time namespace of its own with the same
clocks, before `flatpak run` starts it as usual. Sober locks parts of
`/proc/self/ns/time` (about a hundred locks show in `/proc/locks` after a
while), and an account sharing that namespace with your usual Sober stopped
1 to 3 minutes after opening at "status != SL_RESULT_SUCCESS", the crash
RAM-Linux lists too, where
[an issue](https://github.com/broreirik/RAM-Linux/issues/1) proposes the
same fix. Your usual
Sober, its folder and its scope are never touched. A lock in the account
folder tells whether it is open, and the network written down at start is how
WayClick finds that copy in `flatpak ps` to close it, never the one in the
network of your computer.

This was measured with the real Sober in a nested KWin
(`tests/t_sober_accounts_kwin.py`). Two accounts reached Roblox a few seconds
apart and stayed open side by side for 4 minutes, each in a network and a
time namespace of its own, with Sober writing only into the account folder
and taking its locks on its own namespace. Closing one left the other open, an
empty account opened at the first screen of Sober, and the Sober of the
computer running the test was not touched.

Limits

- every copy is the same app with the same window title, so a *Window* step
  that looks for Sober matches any of them;
- each open account uses as much memory as one more Roblox;
- Sober takes a `roblox://` link only when it opens, so a link for an account
  that is already open is not opened (measured, a second Sober in the same
  account stops at "An instance of Sober is already running");
- it needs `pasta`, util-linux 2.38 or newer and Linux 5.6 or newer (time
  namespaces), and a later Sober may add checks that this does not get past.

When the Sober of an account stops, the page says why in red, from the
`FATAL` line of its log (`launch.log` in the account folder), and Sober keeps
its error window open until you close it.

Roblox staff have called running more than one client an exploit while
saying it is not banned today, though that may change
([DevForum, July 2025](https://devforum.roblox.com/t/is-fishstrap-bloxstrap-no-longer-allowed-on-the-platform/3829253/13)),
and Sober does not support it ([Sober FAQ](https://vinegarhq.org/Sober/FAQ/index.html)).
Use it at your own risk.

### Trigger

**Mode**

- `No trigger (hotkey toggles)` — Start runs it; the hotkey toggles it too.
- `Runs while the trigger is held` — arm it, then it runs only while you hold
  the trigger. Let go and it stops, still armed.

The mode gates **everything that is enabled**, not just clicking, and the
button below says which of the two it will do: **Start** when there is no
trigger, **Arm** when there is one. The Trigger tab carries a dot whenever a
trigger is set, the same way the other tabs mark what they have enabled.

**Trigger** — what you hold. Click the field and press it: any key on your
keyboard, or any mouse button including the two thumb ones. There is one field
because there is one question; which of the two it is decides how WayClick
listens, not what you have to configure. A mouse button goes through the same
capture hold mode uses; a key is read from `/dev/input` like the hotkey.

"Hold left click and it presses E ten times a second" is that: trigger set to
`Left click`, the keyboard macro on `E` at 100 ms, clicking off.

**Trigger holds** — which engines wait for the trigger. Untick one and it runs
the moment you press Arm, while the ticked ones still wait. Hold the mouse
button to autoclick while the keyboard macro runs the whole time, or the
reverse.

**Start delay** — seconds before it begins. Move the cursor off the window
first, otherwise the autoclicker clicks its own Stop button.

**Auto-stop after** — stops on its own after N seconds (`never` = off).

**Arm/disarm hotkey** — arms and disarms from any window, whatever has focus.
Set by pressing it, same as everything else, so any key works; the field warns
when you pick one that also types, since it would arm and disarm every time you
pressed it anywhere. `Esc` always stops when the window is focused.
**Hide it from other apps** keeps arming and disarming but stops the key from
reaching the game, the way AutoHotkey hides its hotkeys; off by default, so
nothing changes for whoever already uses it. When a macro without modifiers
uses the same key (an imported script with its own `F8::`, say), the macro
gets the key while macros are enabled and the clicker is not armed by it; the
Hotkeys tab and the import report warn about this. A `Ctrl+F8` macro leaves
plain `F8` to the arm hotkey.

It is not the trigger. Arming makes the trigger live, and the trigger is what
runs things — which is what lets "arm with F8, run while holding left click"
be two separate, obvious settings.

**Sound feedback** — a short high beep when it starts, low when it stops, so you
know the hotkey registered without looking.

### Send key to a window

Pick one of your open windows and WayClick feeds it a key on an interval —
Space into a game every 60 s, for instance, while you keep working. The list
shows every open window with its real icon and a marker for how the key will
get there, and the key is set by pressing it, same as in the macro:

| | |
|---|---|
| **⌨** | X11 window (Xwayland). The key is addressed to that window: your focus is never touched, and it works **even minimized**. |
| **◐** | Pure Wayland window. There is no way to address it, so WayClick focuses it for ~40 ms, sends the key and hands focus back. |

Measured against an X11 target running in its own process, with focus parked on
another window the whole time: 3 of 3 delivered while unfocused, 6 more while
minimized, 0 leaked into the focused window.

If the window you picked is pure Wayland, the group shows a red notice. The way
out is to reopen that program as an X11 client, which usually takes one
environment variable:

```bash
SDL_VIDEODRIVER=x11 ./game          # SDL (most games)
GDK_BACKEND=x11 ./app               # GTK
QT_QPA_PLATFORM=xcb ./app           # Qt
flatpak run --env=SDL_VIDEODRIVER=x11 org.example.App
```

Then it shows up as ⌨ and takes the key directly. Two honest caveats: an app
that reads raw input may ignore synthetic X11 events, and this is keyboard
only — clicks follow the cursor rather than focus, so aiming them at a window
would mean warping your pointer.

### Updates

Installed from a package or `install.sh`, there is nowhere a new version would
announce itself — so WayClick asks the repository. When a release is newer than
what you are running, a bar appears at the top with a **Download** button that
opens that release; `×` hides it until the next launch, on purpose: the whole
point is that it tells you *every* time you open it, until you update. Started
with `--tray`, the notice arrives as a tray message instead, since there is no
window to put a bar on.

It asks GitHub first and falls back to GitLab — the project lives in both and
either answers without a login. The reply is cached and the question is asked
at most once a day, never on the UI thread, so a slow network cannot delay the
window opening. **Help > Check for updates** asks right now and answers even
when there is nothing new; **Settings > Check for updates on start** turns the
automatic check off, and then nothing is requested at all.

Nothing is downloaded or installed for you: the button opens the release page
and you update the way you installed it.

### Anti-AFK

Nudges the pointer a few pixels and puts it right back, every N seconds. It is
independent of Start/Stop — tick it and it runs, even while nothing is clicking,
which is the point: games and sites usually decide you are idle from **pointer
movement**, not from clicks, so an autoclicker alone can still get you kicked
for being AFK.

Two details make it behave. The two moves are 50 ms apart, because sent together
the compositor would fold +4 and −4 into the same frame and nothing would appear
to move at all. And the direction flips every cycle: pointer acceleration scales
the outbound and return moves by slightly different factors, so they do not
cancel on their own, and without flipping the cursor would crawl across the
screen — measured at 0.15 px per cycle, hundreds of pixels an hour. Alternating
pins it between two positions instead: **0.0000 px of drift**.

The step is 4 device units rather than 1 because acceleration shrinks it: on a
slowed-down pointer, 1 unit came out as 0.402 px on screen — not even a whole
pixel, which an app reading integer coordinates would never notice. 4 units land
around 2–3 px, enough to register anywhere and still invisible, since it returns
in 50 ms.

## How it works

Wayland deliberately has no "send a click to that window" API, so WayClick works
one layer below the compositor.

**Clicking** — `/dev/uinput` creates a real input device in the kernel. libinput
picks it up, the compositor treats it as hardware, and clicks land wherever your
cursor is. To reach 10,000 clicks/s the press/SYN/release/SYN sequence goes out
in a single `write()`, and the loop uses an absolute deadline: it sleeps for the
bulk of the interval and, only below 1 ms, busy-waits the last few hundred µs,
because OS sleep granularity alone cannot hit sub-millisecond timing. From 1 ms
up it only sleeps (median period measured at 1.000 ms), since a busy-wait holds
Python's GIL and would starve the thread that relays your keyboard.

**Global hotkey** — reads `/dev/input/event*` directly, the only way to see keys
that are not focused on your window. Keyboards created by remappers (keyd,
kmonad, input-remapper) count as valid sources.

**Hiding a hotkey.** The compositor has no API to swallow a key, so WayClick
does it the way hold mode does with the mouse: one input thread reads every
keyboard, and when some hotkey has to be hidden it takes an `EVIOCGRAB` on that
keyboard and relays everything else through a virtual clone with the same name,
ids, keys and LEDs (so KDE keeps your layout and Caps Lock still lights up).
The grab only happens while no key is down, otherwise the compositor would see
a press and never the release, and keys still down when it lets go are released
on the clone. Relay latency measured at 0.04 ms median, also while a 1 ms macro
runs. Every macro, the arm hotkey and hold mode share that one reader, which is
why grabbing the keyboard does not deafen the others.

**Per-window key** — Wayland gives input to whoever has focus and offers no way
to address a surface; KWin's `fake_input`, the only injection protocol it
implements, has no surface argument either. X11 is the opposite: `XSendEvent`
carries a destination window and the client handles it while unfocused and
unmapped, which is why the ⌨ path works and the ◐ one has to borrow focus.
WayClick reads the window list from KWin scripting (the only thing that sees
Wayland windows) and the X11 ids straight from `_NET_CLIENT_LIST` through
libX11.

**Hold mode** — this one needs a trick. The compositor tracks button state per
*seat*, so while your physical button is held it **discards every click you
inject** — measured: 0 out of 20 delivered. So WayClick takes an `EVIOCGRAB` on
the mouse you selected, meaning the compositor no longer sees the real device,
and relays everything through the virtual one — motion, wheel, other buttons —
filtering out only the trigger button, which becomes the click stream. Relay
latency measured at 0.02 ms median under a 10,000 clicks/s load, so the pointer
stays smooth. The grab is released on stop, on quit, and by the kernel if the
process dies.

The trigger is only swallowed when clicking is enabled, since that is when the
autoclicker reemits it. Hold the button to drive just the keyboard macro and it
is relayed as usual — you keep your button and get the macro.

**Going to a position.** A relative mouse can only say how far to move, and
pointer acceleration changes how far. So for screen steps WayClick creates one
more device, an absolute pointer like the mouse a virtual machine gives its
guest, with `ABS_X` and `ABS_Y` from 0 to 65535 and three buttons. udev tags
it as a mouse, libinput hands it to KWin as absolute motion, and KWin spreads
that range over the whole desktop, every screen together. WayClick turns the
position into the value that lands on the center of that pixel, from the
screen layout Qt reports, and follows the layout when screens change. Checked
through libinput itself on three layouts, every corner and edge lands on its
exact pixel. The kernel drops a value equal to the last one, so going to the
same spot again first steps one unit aside. The device is only created when
an enabled macro needs it.

**Seeing the screen.** On Wayland only the compositor has the picture, and the
way in for an ordinary program is the ScreenCast desktop portal, the one video
calls use. WayClick talks to it on its own D-Bus connection, asks for every
screen with the cursor left out, and reads the frames through PipeWire and
GStreamer (`pipewiresrc`, `videoconvert`, `appsink`), ten per second. Finding
an image is normalized cross correlation (OpenCV's `TM_CCOEFF_NORMED`) written
in numpy with FFTs. Candidates come from the gray picture, at half or a
quarter of the size when the template survives that (fine text and noise do
not, and are searched at full size), and each one is then scored in all three
colors and against its average color, so the same button greyed out, or the
same icon in another color, does not count. Measured on synthetic 1920×1080
screens (`tests/bench_find_image.py`), 284 of 288 random cuts from 16×16 to
300×150 were found at their exact spot, with a median between about 30 and
60 ms per screen. The other four landed on an equal copy, two flat areas of
the same color and two 16 px cuts with a 0.999 twin.

**Windows and the cursor.** On Wayland only the compositor knows the
windows, so window steps and the recorder ask KWin. WayClick loads a small
KWin script over D-Bus (`org.kde.kwin.Scripting`), and the script reads
`workspace.windowList()` or `workspace.cursorPos` and calls a D-Bus service of
WayClick back with the answer. Measured against a nested KWin
(`tests/t_farm_kwin.py`), a question takes 5 to 10 ms, and a window step asks
at most once a second.

**The Dock.** The Dock is `kwin_wayland --wayland-display $WAYLAND_DISPLAY
--socket wayland-wayclick-dock` with a fixed width and height, so it runs as
a window of your desktop (KWin's Wayland backend), next to a `dbus-daemon` of
its own. Its settings, state and cache live in
`$XDG_RUNTIME_DIR/wayclick-dock`, so the screen settings it saves never touch
the desktop's, and only your keyboard layout and input settings (`kxkbrc`,
`kcminputrc`) are copied in. Its kwinrc maximizes new windows with no border,
so the game fills it, and turns off the screen edges and the Overview, which
a macro in a corner would open. The game runs with `WAYLAND_DISPLAY`
pointing at the Dock and no `DISPLAY`, with the rest of your environment, the
session D-Bus included, so portals and the keyring work as usual. Macros talk
to that KWin directly. Input goes through `org_kde_kwin_fake_input` on the
Dock socket, the same path a real device takes inside KWin, so the desktop
never sees it, and the screen comes from
`org.kde.KWin.ScreenShot2.CaptureWorkspace` on the Dock's D-Bus (about 5 ms
for 1280×720, also while the window is hidden). Window steps run KWin scripts
on that D-Bus. A small script in the desktop's KWin finds the Dock window by
its process to keep its size, bring it forward and close it. Measured in the
nested KWin of `tests/t_dock_kwin.py`, the Dock opens in about 0.2 s.

**The Tower Defense tab.** The view is a widget that asks the KWin of the
Dock for the same ScreenShot2 capture up to 20 times a second, from a thread
of its own, and draws it scaled into the tab, keeping its shape. A press, a
release, a move, the wheel or a key over it becomes the matching
`org_kde_kwin_fake_input` request on the Dock socket, at the point of the
Dock under the pointer, and while the right or middle button is held the
motion is sent relative, since a game may lock the pointer while it turns
the camera. Keys the farm uses as hotkeys are not sent. The farm is a Dock
macro runner of its own, with the clock of the match, watches for the end of
a match, a closed window, the reconnect button and skip wave, and waits in
slices of a quarter of a second, so Pause and Stop act at once. Nothing in it
reads the memory of the game or of Sober.

## Troubleshooting

**"Global hotkey OFF"** — you are not in the `input` group, or the session
predates the group change. Follow steps 3 and 4 above, then log out and back in.

**`/dev/uinput` permission denied** — the udev rule in step 3 is missing, or the
`uinput` module is not loaded (step 2). Check with `ls -l /dev/uinput`.

**Clicking does nothing / it turns itself off** — the cursor was over WayClick's
own window when it started. That is what the start delay is for.

**Web pages freeze at very high rates** — not the injection: browsers process
input on a single JS thread and cannot keep up with 10,000 events/s. On the web,
1–5 ms (200–1000 clicks/s) is the useful ceiling. Native apps take far more.

**Sensitivity feels different in hold mode** — make sure the mouse selected in
the UI is the one you are actually holding; the clone copies that device's
identity, and that is what KDE keys its per-device settings on.

**Kicked for being AFK even though it was clicking** — idle detection usually
watches pointer movement, not clicks. Turn on Anti-AFK. Some games go further
and check whether your character moved, in which case use the keyboard macro to
press a movement key too.

**A position step does not move the cursor in a game.** The game has the
pointer locked (a first person camera, or while you turn), and KWin does not
move a locked pointer. Use the step where the game shows its cursor.

**An image step never finds the picture.** Capture it again with the game at
the resolution and scaling it runs at, since the search does not resize, and
leave out parts that change, like counters or animated backgrounds. Lowering
*Similarity* a little helps with compression and glow effects, and marking a
search area makes it faster and keeps look alikes elsewhere out.

**Screen steps say what to install.** Install the packages the message lists
and restart WayClick. The AppImage cannot load them, so for screen steps use a
package or `install.sh`.

**The screen sharing question comes back every time.** The desktop did not
keep the permission, or it refused the saved token. WayClick saves whatever
token comes back each time, and deleting `~/.config/wayclick/screen-token`
makes it ask afresh.

## Tests

The files in `tests/` are runnable checks, not unit tests — they create fake
input devices and measure real behaviour.

These are isolated and safe to run while you use the desktop, even with a
game open: WayClick only opens devices named `wctest`, the fake keyboard and
mouse are grabbed before their first event, and every virtual device WayClick
creates is grabbed by the test the instant it appears, which is also how the
test reads what would have reached the compositor (see `tests/harness.py`). No
root needed.

```bash
python3 tests/t_ahk_import.py                       # the importer on the scripts in tests/ahk
python3 tests/t_ahk_screen.py                       # PixelGetColor, PixelSearch, ImageSearch and Suspend from a .ahk, run on a fake screen
sg input -c "python3 tests/t_suspend_ui.py"         # the Suspend hotkey: the bhop script, on, off, Ctrl, warnings, config, --import
python3 tests/t_ahk_vm.py                           # the AutoHotkey interpreter: expressions, loops, functions, timers, errors, v1 and v2
sg input -c "python3 tests/t_hotstrings.py"         # hotstrings, #IfWinActive, macro sounds, #MaxThreadsPerHotkey and SetTimer on the engine
python3 tests/t_ahk_kwin.py                         # a .ahk end to end in a nested KWin, with two screenshots
sg input -c "python3 tests/t_hotkey_macros.py"      # SpamE F1.ahk at 15.6 and 1 ms, every mode, hiding, relay
sg input -c "python3 tests/t_hotkeys_ui.py"         # the Hotkeys tab end to end, config, --import
sg input -c "python3 tests/t_hotkeys_dialogs.py"    # the macro and step editors
sg input -c "python3 tests/t_clicker_timing.py"     # Clicker period and keyboard relay together
sg input -c "python3 tests/t_screen_macros.py"      # screen steps on a fake screen, positions, webhook
sg input -c "python3 tests/t_screen_ui.py"          # step editor, Pick on screen, screen layout
sg input -c "python3 tests/t_abs_pointer_libinput.py"  # the absolute pointer as libinput reads it
sg input -c "python3 tests/t_farm_macros.py"        # watches, windows, counters, the recorder, a whole farm on a fake game
sg input -c "python3 tests/t_farm_ui.py"            # editors, recorder, farm assistant, .wayclick files
python3 tests/t_dock_ui.py                          # the Dock tab, Dock macros, editors and links on a fake Dock
python3 tests/t_tower_ui.py                         # the Tower Defense tab: strategies, recorder, files and a whole farm on a fake game
python3 tests/t_farm_kwin.py                        # window steps and the cursor against a real KWin, nested
python3 tests/t_dock_kwin.py                        # the Dock in a real KWin, nested: keys, clicks, wheel, screen, windows, size, links, closing
python3 tests/t_dock_hidden.py                      # a game keeps drawing in the Dock behind other windows and minimized
python3 tests/t_tower_kwin.py                       # the farm and the view of the Tower Defense tab on a fake game in the real Dock
python3 tests/t_sober_dock.py                       # Sober opening in the Dock, its Close button found and clicked
python3 tests/t_screen_portal.py                    # the real portal, inside bubblewrap
python3 tests/bench_find_image.py                   # image search, found or not and how fast
python3 tests/t_sober_kwin.py                       # all of it against Roblox (Sober), in a nested KWin
python3 tests/t_sidenav.py                          # the sidebar: groups, dots, search, keys, icons only when narrow
python3 tests/t_sober_accounts.py                   # Sober accounts on a fake Sober: lock, pasta, closing only its copy, the page, the Dock
python3 tests/t_sober_accounts_kwin.py              # two accounts with the real Sober at once, in a nested KWin
```

`t_screen_portal.py` runs the real `xdg-desktop-portal` inside `bwrap` with
its own D-Bus, its own PipeWire and a fake ScreenCast backend that serves
synthetic screens, so no dialog appears and nothing of your screen is shared.
It needs `bwrap`, `dbus-run-session`, `pipewire` and `wireplumber`, and skips
when one is missing.

`t_sober_kwin.py` runs WayClick against a real game window. `tests/kwin_nested.py`
starts a nested `kwin_wayland --virtual` with two screens inside `bwrap`, with
its own D-Bus, its own PipeWire with a muted output, the real
`xdg-desktop-portal-kde` (the test answers the share dialog by clicking it) and
an input bridge that hands every WayClick virtual device to the nested KWin
only. A fresh Sober opens in it with its own home in `/var/tmp` (only the
Roblox APKs and the version string are copied from yours, no account data),
its own network through `pasta` (package `passt`) and the lowest CPU and disk
priority, so your own Sober can stay open. The fake keyboard only types while
the WayClick inside the sandbox reports holding its grab and the grab is
confirmed busy. The test imports `tests/ahk` scripts and checks them in the
Roblox login form, reading the text back through the sandbox clipboard; runs
the screen steps against the Roblox start screen (image found and clicked,
pixel color, notifications, webhook screenshots, open link); and goes through
the restore token and the dialog choices. It needs Sober with Roblox
downloaded once, `pasta`, `bwrap`, `kwin_wayland`, `wl-clipboard` and
`kscreen-doctor`, and skips when one is missing (`WCTEST_PASTA=/path/to/pasta`
uses one that is not installed).

`t_ahk_kwin.py` uses the same sandbox with one screen: a target window that
shows the keys and clicks it gets, and the real WayClick importing
`tests/ahk/kwin_e2e.ahk`. Holding Space sends F9 about 32 times a second
(measured median 31.3 ms), F7 suspends and resumes, and the `ImageSearch` and
`PixelSearch` hotkeys click the picture and the red dot. With the KWin
watcher reporting the active window, an `#IfWinActive` hotkey runs a `Loop`
on the interpreter and types its sum, an `#IfWinNotActive` one stays a plain
key, and `::btw::` erases `btw` and types `by the way`. Screens come from the
sandbox KWin's ScreenShot2, and two screenshots land in
`/tmp/wayclick-ahk-kwin` (or `WCTEST_SHOTS`).

`t_dock_kwin.py`, `t_dock_hidden.py`, `t_tower_kwin.py` and `t_sober_dock.py` open the Dock
inside the same box, so its window lands on the nested KWin and nothing
reaches your desktop. `t_sober_dock.py` lets the Dock start Sober with its
own command. The box has no network, so that Sober never meets yours over its
fixed 127.0.0.1 port and sees no account (only the Roblox APKs and the
version string are copied, as above), and it stops at its own "Failed to
fetch remote manifest" screen. A Dock macro then finds the Close button of
that screen by its picture and clicks it, and Sober closes. Roblox itself
needs the network, so that part stays with `t_sober_kwin.py`.

`t_sober_accounts.py` runs the real `flock`, `pasta` and `unshare` around a
fake Sober that holds the 127.0.0.1 port in each network, with fake
`flatpak`, `systemctl` and `systemd-run` first in its PATH. It refuses to
start in the network of your computer, so it never meets your Sober.
`t_sober_accounts_kwin.py` opens three accounts with the real Sober in the
nested KWin, each through `pasta` with internet and no sign-in (the Roblox
APKs and the version string are copied from yours, as above), at the lowest
CPU priority, and checks before closing anything that it belongs to the
test. Without `pasta` the first skips the parts that open a
Sober, and the second skips.
`t_dock_hidden.py` needs `es2gears_wayland` (mesa-demos) and skips without
it.

Run them with `QT_QPA_PLATFORM=offscreen`. The rest need the compositor; run
them from the project root:

```bash
sg input -c "python3 tests/t_hold.py"        # hold mode end to end, with a fake mouse
sg input -c "python3 tests/t_hold_keyonly.py"  # hold mode driving only the keyboard macro
sg input -c "python3 tests/t_trigger_scope.py" # keyboard free while clicking waits for the button
sg input -c "python3 tests/t_macro_trigger.py" # hold a mouse button, and hold a key, to drive a macro
python3 tests/t_multikey.py                  # several keys at once, each on its own interval
python3 tests/t_update.py                    # update notice: compare, cache, and the bar
sg input -c "python3 tests/t_clone.py"       # asks KWin over D-Bus if the clone inherited your settings
sg input -c "python3 tests/t_hotkey.py"      # global hotkey, with a fake keyboard
sg input -c "python3 tests/t_relay_lat.py"   # relay latency under a 10 kHz click load
sg input -c "python3 tests/t_multimouse.py"  # several mice, each cloned with its own identity
python3 tests/t_fast.py 0.1                  # emitted vs. delivered clicks at 10,000/s
python3 tests/t_keymacro.py                  # keys delivered, and never left stuck
python3 tests/t_antiafk.py                   # nudge amplitude on screen and drift over time
sg input -c "python3 tests/t_xinject.py"     # key into an X11 window, unfocused then minimized
sg input -c "python3 tests/t_target.py"      # same for a Wayland target, through the focus path
sg input -c "python3 tests/t_holdtime.py"    # press duration, measured the way a game polls it
```

They open a fullscreen window to count what actually arrives, so expect the
screen to flash for a few seconds. Each test uses its own throwaway config, so
none of them touch your settings.

## Translating

Translations are a plain dict in `wayclick.py` — no `.ts` files, no build step,
because the app is a single file. To add a language, copy the `"pt_BR"` block in
`TRANSLATIONS`, change the key to your locale prefix, translate the right-hand
side, and add it to `LANGS`. Anything you leave out falls back to English, so a
partial translation still works. Strings with `{name}` placeholders keep them.

## Notes

Settings live in `~/.config/wayclick.json`. Nothing runs as root, nothing is
installed system-wide except the udev rule, and the virtual devices disappear
when the process exits. Pictures for image steps are kept in
`~/.local/share/wayclick/images`, and the screen sharing token in
`~/.config/wayclick/screen-token`. The screen is only shared while a macro
needs it, and frames never leave the machine unless a step posts a screenshot
to the webhook you gave it. Pictures from imported `.wayclick` files go to the
same pictures folder. Sober accounts live in
`~/.local/share/wayclick/sober-accounts`, one folder each.

Use it where automation is allowed. Plenty of online games ban input automation.

## Building the packages

```bash
bash packaging/build-rpm.sh        # needs rpm-build
bash packaging/build-deb.sh        # needs only ar and tar, no dpkg
bash packaging/build-pacman.sh     # needs bsdtar, builds off Arch too
bash packaging/build-appimage.sh   # downloads a portable Python and Qt
makepkg -p packaging/PKGBUILD      # the from-source route, on Arch
```

Everything lands in `dist/`. A tag push builds all of them on CI and attaches
them to the release — see [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Uninstall

```bash
bash uninstall.sh            # keeps your settings, the udev rule and your Sober accounts
bash uninstall.sh --purge    # removes those too, except a Sober account that is open
```

For a packaged install use your package manager instead (`dnf remove wayclick`,
`apt remove wayclick`, `pacman -R wayclick`).

## License

MIT — see [LICENSE](LICENSE).
