%global appname wayclick

# systemd-rpm-macros define _udevrulesdir, e ele não está num fedora:latest
# pelado — que é onde o CI monta. Sem este fallback o %files quebra com
# 'File must begin with "/"', que é o que derrubou a v1.3.1 e a v1.3.2.
%{!?_udevrulesdir: %global _udevrulesdir %{_prefix}/lib/udev/rules.d}

Name:           wayclick
Version:        1.6.0
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
