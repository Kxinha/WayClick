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
  steps, and the hotkey hidden from the game like AutoHotkey does. **Import
  AHK** turns a `.ahk` script into macros
- **Screen steps** in those macros, like AutoHotkey's `ImageSearch` and
  `PixelGetColor`. Go to an exact spot and click, find an image, check a pixel
  color, wait until it shows up or goes away and choose what comes next, post
  to a Discord webhook with a screenshot, open a link. **Pick on screen** takes
  the spot, the color or the picture from your own screen
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

Five tabs. **Hotkeys** holds the AutoHotkey style macros, which run on their
own hotkeys and do not need Start. **Click**, **Keyboard** and **Window** are
the three things Start can run. Each has an *enable* box, and the tab shows a
dot when it is on, so you can see what is armed without opening it. Enable any combination. **Trigger** is
how it all starts and stops, plus Anti-AFK, which runs on its own outside
Start/Stop. Status and the Start button stay visible below the tabs, and the
window is small enough for a 1366×768 screen.

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
  move the pointer by an amount, and the [screen steps](#screen-steps).

Whatever a macro holds is released when it stops, when you disable it and when
WayClick quits. A modifier you are holding for the hotkey is let go before the
steps run, as AutoHotkey does, so `Alt+A` sending `Ctrl+C` arrives as `Ctrl+C`
and not `Ctrl+Alt+C`. Opening the editor pauses the macros, so you can pick a
hotkey that a running macro already uses.

**Import AHK…** reads a `.ahk` file (v1 or v2) and turns its hotkeys into
macros, showing first what each one became and what it could not convert, by
line number. It is not an AutoHotkey interpreter; it recognises the patterns
game scripts use:

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
| `Send` with `{Key down}`, `{Key up}`, `{Key N}`, `^!+#`, text, `{Click}`, `{WheelDown N}`; `Click`, `Sleep`, `Random`, `MouseMove ..., R`, `Gosub`, functions, stacked hotkeys, `$`, `*`, `~`, `sc`/`vk` names, `SetKeyDelay` | the matching steps |

Hotstrings, `Key Up` hotkeys, `A & B` combinations, `#If` window conditions
(the hotkey works in every window instead) and general scripting are reported
and skipped. `Click X, Y` and `MouseMove X, Y` become
[Go to a screen position](#screen-steps) steps, with a note when the script
measured them from the active window, which is AutoHotkey's default.

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
| **Check a pixel color** | Reads one point and compares it with a color, red, green and blue each within the *Tolerance*. |
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
sg input -c "python3 tests/t_hotkey_macros.py"      # SpamE F1.ahk at 15.6 and 1 ms, every mode, hiding, relay
sg input -c "python3 tests/t_hotkeys_ui.py"         # the Hotkeys tab end to end, config, --import
sg input -c "python3 tests/t_hotkeys_dialogs.py"    # the macro and step editors
sg input -c "python3 tests/t_clicker_timing.py"     # Clicker period and keyboard relay together
sg input -c "python3 tests/t_screen_macros.py"      # screen steps on a fake screen, positions, webhook
sg input -c "python3 tests/t_screen_ui.py"          # step editor, Pick on screen, screen layout
sg input -c "python3 tests/t_abs_pointer_libinput.py"  # the absolute pointer as libinput reads it
python3 tests/t_screen_portal.py                    # the real portal, inside bubblewrap
python3 tests/bench_find_image.py                   # image search, found or not and how fast
python3 tests/t_sober_kwin.py                       # all of it against Roblox (Sober), in a nested KWin
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
to the webhook you gave it.

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
bash uninstall.sh            # keeps your settings and the udev rule
bash uninstall.sh --purge    # removes those too
```

For a packaged install use your package manager instead (`dnf remove wayclick`,
`apt remove wayclick`, `pacman -R wayclick`).

## License

MIT — see [LICENSE](LICENSE).
