#!/usr/bin/env bash
# Dev-install loop (ticket 17): link payload/anki_theme into the live Anki's
# addons21 as `anki_theme` — the folder identity the consented installer (tickets
# 19-20) installs to. The plugin's sync routine replaces this for real
# installs; for development the symlink keeps the working tree live (restart
# Anki to re-import changed modules; runtime-generated web/anki_theme.css lands
# in the repo tree, gitignored).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LINK="${ANKI_THEME_ADDONS:-$HOME/.local/share/Anki2/addons21}/anki_theme"

case "${1:-install}" in
  install)
    if [ -e "$LINK" ] || [ -L "$LINK" ]; then
      echo "already present: $LINK (remove first: $0 remove)" >&2
      exit 1
    fi
    ln -s "$REPO/payload/anki_theme" "$LINK"
    echo "linked $LINK -> $REPO/payload/anki_theme"
    ;;
  remove)
    if [ ! -L "$LINK" ]; then
      echo "not a dev link: $LINK (refusing — not installed by this script)" >&2
      exit 1
    fi
    rm "$LINK"
    echo "removed $LINK"
    ;;
  *)
    echo "usage: $0 [install|remove]" >&2
    exit 2
    ;;
esac
