#!/usr/bin/env bash
# Dev-install loop (ticket 17): link payload/ankiya into the live Anki's
# addons21 as `ankiya` — the folder identity the consented installer (tickets
# 19-20) installs to. The plugin's sync routine replaces this for real
# installs; for development the symlink keeps the working tree live (restart
# Anki to re-import changed modules; runtime-generated web/ankiya.css lands
# in the repo tree, gitignored).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LINK="${ANKIYA_ADDONS:-$HOME/.local/share/Anki2/addons21}/ankiya"

case "${1:-install}" in
  install)
    if [ -e "$LINK" ] || [ -L "$LINK" ]; then
      echo "already present: $LINK (remove first: $0 remove)" >&2
      exit 1
    fi
    ln -s "$REPO/payload/ankiya" "$LINK"
    echo "linked $LINK -> $REPO/payload/ankiya"
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
