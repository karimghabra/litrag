#!/usr/bin/env bash
# Removes what install.sh put in place: app/, venv/, uv/ (with the Python and the cache uv
# kept there), the log, this script's copy and the applications-menu entry. Never the
# libraries, never Docling's models under them, never Ollama or its models.
#
#   ./uninstall.sh [--yes]
#
# LITRAG_INSTALL_ROOT names the install, as for install.sh.
set -euo pipefail

yes=0
for a in "$@"; do
  case "$a" in
    -y | --yes) yes=1 ;;
    -h | --help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "uninstall.sh: unknown option $a" >&2; exit 2 ;;
  esac
done

root="${LITRAG_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/litrag}"
desktop="${XDG_DATA_HOME:-$HOME/.local/share}/applications/litrag.desktop"
libroot="${LITRAG_ROOT:-${PROTRACKER_LIBRARY:-$HOME/.protracker/library}}"

# each folder only if it is what the installer made there, whatever the root was set to
gone=()
[ -f "$root/app/resources/app.asar" ] && gone+=("$root/app")
[ -d "$root/app.new" ] && gone+=("$root/app.new")
[ -f "$root/venv/pyvenv.cfg" ] && gone+=("$root/venv")
[ -x "$root/uv/uv" ] && gone+=("$root/uv")
[ -f "$root/install.log" ] && gone+=("$root/install.log")
[ -f "$root/uninstall.sh" ] && gone+=("$root/uninstall.sh") # this script, maybe: bash keeps reading it
[ -f "$desktop" ] && gone+=("$desktop")
if [ ${#gone[@]} -eq 0 ]; then
  echo "Nothing of litrag's is installed at $root."
  exit 0
fi

echo "This removes:"
printf '  %s\n' "${gone[@]}"
echo "and keeps the libraries at $libroot, and Ollama."
if [ "$yes" != 1 ]; then
  read -r -p "Remove litrag? [y/N] " answer
  case "$answer" in y | Y | yes | YES) ;; *) echo "Nothing removed."; exit 0 ;; esac
fi
if command -v pgrep >/dev/null && pgrep -f "$root/app/litrag" >/dev/null; then
  echo "litrag is running from $root/app: close it, then run uninstall.sh again." >&2
  exit 1
fi
rm -rf "${gone[@]}"
rmdir "$root" 2>/dev/null || true
echo "litrag is removed. The libraries at $libroot are as they were."
