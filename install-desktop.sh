#!/bin/sh
# Write desktop and application-menu launchers for this user.
# The generated files contain this machine's paths, so they are not committed.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DESKTOP="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/user-dirs.dirs" ]; then
  # shellcheck disable=SC1090
  . "${XDG_CONFIG_HOME:-$HOME/.config}/user-dirs.dirs"
  DESKTOP="${XDG_DESKTOP_DIR:-$DESKTOP}"
fi
APPS="${XDG_DATA_HOME:-$HOME/.local/share/applications}"
mkdir -p "$DESKTOP" "$APPS"
ICON="$ROOT/share/prompt-face.svg"
if command -v ghostty >/dev/null 2>&1; then
  TERM_EXEC="ghostty -e"
elif command -v x-terminal-emulator >/dev/null 2>&1; then
  TERM_EXEC="x-terminal-emulator -e"
else
  echo "Install Ghostty or another terminal that accepts -e." >&2
  exit 1
fi

write_entry() {
  name=$1
  comment=$2
  exec_line=$3
  dest=$4
  cat > "$dest" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$name
Comment=$comment
Exec=$exec_line
Icon=$ICON
Terminal=false
StartupNotify=true
Categories=AudioVideo;
Keywords=obs;prompt;face;
EOF
  chmod +x "$dest"
  if command -v gio >/dev/null 2>&1; then
    gio set "$dest" metadata::trusted true || true
  fi
}

write_entry "Prompt" "Start the Prompt face tracker" "$TERM_EXEC $ROOT/prompt-face" "$DESKTOP/Prompt.desktop"
write_entry "Prompt OBS" "Paint the Prompt face into OBS" "$TERM_EXEC $ROOT/prompt-obs" "$DESKTOP/Prompt-OBS.desktop"
write_entry "Prompt page" "Open the Prompt expression page" "xdg-open http://127.0.0.1:8771" "$DESKTOP/Prompt-Page.desktop"
cp "$DESKTOP/Prompt.desktop" "$APPS/prompt-face.desktop"
cp "$DESKTOP/Prompt-OBS.desktop" "$APPS/prompt-obs.desktop"
cp "$DESKTOP/Prompt-Page.desktop" "$APPS/prompt-page.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
echo "Launchers written to $DESKTOP and $APPS"
