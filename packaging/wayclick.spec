%global appname wayclick

# systemd-rpm-macros define _udevrulesdir, e ele não está num fedora:latest
# pelado — que é onde o CI monta. Sem este fallback o %files quebra com
# 'File must begin with "/"', que é o que derrubou a v1.3.1 e a v1.3.2.
%{!?_udevrulesdir: %global _udevrulesdir %{_prefix}/lib/udev/rules.d}

Name:           wayclick
Version:        1.8.0
Release:        1%{?dist}
Summary:        Autoclicker for Wayland, using /dev/uinput

License:        MIT
URL:            https://github.com/gabrielmf1998/WayClick
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
Requires:       python3 >= 3.9
Requires:       python3-pyside6
Recommends:     (python3-pyside6-multimedia or python3-pyside6)
# os passos que olham a tela: achar imagem, cor de um ponto
Recommends:     python3-numpy
Recommends:     python3-gobject
Recommends:     pipewire-gstreamer
Recommends:     gstreamer1-plugins-base

%description
An autoclicker that works on Wayland. It creates a virtual mouse in the kernel
through /dev/uinput, so its clicks arrive as real input with no X11, no xdotool
and nothing running as root. Goes from 1 click/s down to a 0.1 ms interval,
with a global hotkey, a hold mode, a multi-key keyboard macro, per-window key injection
and an anti-AFK nudge.

Members of the 'input' group can use it right after install; the package ships
the udev rule that grants that group access to /dev/uinput.

%prep
%autosetup

%build
# nada a compilar: um arquivo Python

%install
install -Dpm 0755 wayclick.py %{buildroot}%{_datadir}/%{name}/wayclick.py
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<'LAUNCH'
#!/usr/bin/env bash
exec python3 /usr/share/wayclick/wayclick.py "$@"
LAUNCH
chmod 0755 %{buildroot}%{_bindir}/%{name}
install -Dpm 0644 packaging/wayclick.desktop %{buildroot}%{_datadir}/applications/%{name}.desktop
install -Dpm 0644 packaging/wayclick.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
install -Dpm 0644 packaging/99-wayclick-uinput.rules %{buildroot}%{_udevrulesdir}/99-wayclick-uinput.rules
install -Dpm 0644 packaging/uinput.conf %{buildroot}%{_prefix}/lib/modules-load.d/wayclick-uinput.conf
install -Dpm 0644 LICENSE %{buildroot}%{_datadir}/licenses/%{name}/LICENSE

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop
python3 -c "import ast,sys; ast.parse(open('wayclick.py').read())"

%post
/usr/sbin/udevadm control --reload-rules >/dev/null 2>&1 || :
/usr/sbin/modprobe uinput >/dev/null 2>&1 || :
/usr/bin/gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
/usr/bin/update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :

%files
%license LICENSE
%doc README.md README.pt-BR.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%{_udevrulesdir}/99-wayclick-uinput.rules
%{_prefix}/lib/modules-load.d/wayclick-uinput.conf

%changelog
* Fri Nov 20 2026 gabrielmf1998 <noreply@github.com> - 1.8.0-1
- Watch steps keep looking for an image, a pixel color or a window while
  the next steps run, and can click it or take the macro to another step
- Window steps find a window by app name or by its whole title through
  KWin, wait for it to open or close and bring it to the front
- Count steps, Stop after N runs, and {run}, {time} and counters in
  notifications
- Record in the macro editor turns keys, clicks, drags and the wheel into
  steps
- Farm assistant builds a whole match farm, from opening the game and the
  lobby buttons to a strategy, skip and reconnect buttons, the end of the
  match and a Discord report
- Export and Import share macros with their pictures as .wayclick files,
  without webhooks or unsafe links

* Fri Nov 20 2026 gabrielmf1998 <noreply@github.com> - 1.7.1-1
- Imported AutoHotkey timers and waits shorter than 15.6 ms run every
  15.6 ms, like AutoHotkey on Windows, so a game that reads fewer keys per
  second no longer keeps getting keys after the macro stops; the import
  report says so
- A hotkey macro on the arm/disarm key (F8 by default, common in game
  scripts) takes that key while macros are on, and the clicker no longer
  arms with it; the Hotkeys tab and the import report warn about it
- New end to end test against Roblox (Sober) in a nested KWin with the real
  KDE share dialog: imported scripts, image and pixel steps, webhook
  screenshots, the restore token and every dialog choice

* Fri Nov 20 2026 gabrielmf1998 <noreply@github.com> - 1.7.0-1
- Screen steps in hotkey macros: go to an exact screen position and click,
  find an image on screen, check a pixel color, send a notification or a
  Discord webhook with screenshots, open a link or file
- Image and color steps can wait until something appears or disappears,
  and choose what comes next: continue, stop, start over or go to a step
- Pick on screen takes a position, a color or a picture from the screen,
  through the ScreenCast portal, remembered with a restore token
- An absolute pointer device puts the cursor on an exact pixel, with an
  optional curved, hand like movement and a random offset
- Import AHK turns Click X, Y and MouseMove X, Y into screen positions
- Image search checks every candidate in color, and keeps fine textures at
  full size, so look alikes in another color are not taken for the picture

* Wed Nov 18 2026 gabrielmf1998 <noreply@github.com> - 1.6.0-1
- Hotkey macros in the AutoHotkey style: each with its own hotkey, toggle,
  while held, once or always, and a list of steps
- The hotkey can be hidden from the game, like AutoHotkey does
- Import AHK turns the hotkeys of a .ahk script (v1 or v2) into macros;
  also wayclick --import FILE [--dry-run]
- The arm hotkey can be hidden from other apps too
- One input reader shared by every hotkey, relaying grabbed keyboards
- Timers sleep instead of spinning from 1 ms up, so nothing starves the relay

* Tue Sep 30 2026 gabrielmf1998 <noreply@github.com> - 1.5.0-1
- One Trigger field: any key or any mouse button, set by pressing it
- Two trigger modes instead of three; the trigger decides how it listens
- The hotkey is named for its job: arm/disarm, not trigger
- Thumb mouse buttons usable as trigger and as click

* Sun Sep 13 2026 gabrielmf1998 <noreply@github.com> - 1.4.0-1
- Keyboard macro takes several keys, each with its own interval
- Trigger picks which engines it holds, and says so before you press it
- Checks GitHub and GitLab for a newer release
- Hold mode no longer eats the mouse button when clicking is off

* Sat Aug 29 2026 gabrielmf1998 <noreply@github.com> - 1.3.0-1
- Tabs, system color schemes, new icons
