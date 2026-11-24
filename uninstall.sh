#!/usr/bin/env bash
# Removes a WayClick installed by install.sh (everything lives in ~/.local).
#   bash uninstall.sh            keeps your settings, your Sober accounts and
#                                the udev rule
#   bash uninstall.sh --purge    also removes those (an open account stays)
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wayclick"
BIN="$HOME/.local/bin/wayclick"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/wayclick.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/wayclick.svg"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/wayclick.json"
ACCOUNTS="$APP_DIR/sober-accounts"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/wayclick.desktop"
RULE=/etc/udev/rules.d/99-wayclick-uinput.rules

purge=0
[ "${1:-}" = "--purge" ] && purge=1

pkill -f "$APP_DIR/wayclick.py" 2>/dev/null || true
for f in "$BIN" "$DESKTOP" "$ICON"; do
    [ -e "$f" ] && rm -f "$f" && echo "  removed $f"
done
if [ -d "$APP_DIR" ]; then
    # the Sober accounts (sign-in, settings, downloads) are kept like the
    # settings; --purge removes them below
    find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name sober-accounts \
        -exec rm -rf {} +
    rmdir "$APP_DIR" 2>/dev/null || true
    echo "  removed $APP_DIR"
fi
[ -e "$AUTOSTART" ] && rm -f "$AUTOSTART" && echo "  removed $AUTOSTART"

if [ "$purge" = "1" ]; then
    [ -e "$CONFIG" ] && rm -f "$CONFIG" && echo "  removed $CONFIG"
    if [ -d "$ACCOUNTS" ]; then
        for acc in "$ACCOUNTS"/*/; do
            acc="${acc%/}"
            [ -d "$acc" ] || continue
            # never under an open Sober: its lock is held while it runs
            if [ -e "$acc/lock" ] && ! flock -n "$acc/lock" true 2>/dev/null; then
                echo "  kept $acc: that Sober account is open, close it first"
                continue
            fi
            rm -rf "$acc" && echo "  removed $acc"
        done
        rmdir "$ACCOUNTS" "$APP_DIR" 2>/dev/null || true
    fi
    if [ -e "$RULE" ]; then
        echo "  removing the udev rule needs sudo"
        if sudo rm -f "$RULE"; then
            sudo udevadm control --reload-rules || true
            echo "  removed $RULE"
        else
            echo "  kept $RULE (sudo did not run)"
        fi
    fi
    echo
    echo "Your user is still in the 'input' group. To undo that too:"
    echo "  sudo gpasswd -d $USER input"
else
    echo
    echo "Kept: settings ($CONFIG) and the udev rule."
    [ -d "$ACCOUNTS" ] && echo "Kept: your Sober accounts ($ACCOUNTS)."
    echo "Use --purge to remove those as well."
fi
update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/icon-cache.kcache" 2>/dev/null || true
kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
echo "Done."
