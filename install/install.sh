#!/usr/bin/env bash
# litrag's installer for Linux, run from the folder the release archive extracted to:
#
#   ./install.sh [--torch auto|cpu|cu130|none] [--prefetch-models] [--skip-ollama]
#
# It puts the app, uv and the parser's Python environment in one folder of this user's
# (no root needed), and fetches Ollama's embedder if Ollama is here. Running it again is the
# update: app/ is replaced by the archive's, venv/ is brought in line with the new lock.
# Libraries ($LITRAG_ROOT, else ~/.protracker/library) are never created, moved or deleted;
# the one thing written beside them is <libraries>/models/docling, and only when asked.
#
#   $LITRAG_INSTALL_ROOT/          else ${XDG_DATA_HOME:-~/.local/share}/litrag
#   ├── app/                       the unpacked app; app/resources/parser is the parser's source
#   ├── venv/                      the parser's environment (kept across updates)
#   ├── uv/                        uv itself, the Python it manages, and its cache
#   └── install.log                (the cache goes where UV_CACHE_DIR says, if it says)
#
# --torch auto (the default) takes the CUDA 13 build when nvidia-smi runs, else the CPU one;
# none installs no torch extra at all. --prefetch-models puts Docling's layout and table
# models in <libraries>/models/docling now instead of on the first paper; once that folder is
# there the worker reads only it, so every later run refreshes it for the Docling installed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
torch=auto
prefetch=0
skip_ollama=0
python=3.12 # every locked package has a wheel for it, on Linux and on Windows

usage() { sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() {
  echo "" >&2
  echo "install.sh: $*" >&2
  echo "Nothing after this step was done.${log:+ The log: $log}" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --torch) torch="${2:-}"; shift 2 || die "--torch needs a value" ;;
    --torch=*) torch="${1#*=}"; shift ;;
    --prefetch-models) prefetch=1; shift ;;
    --skip-ollama) skip_ollama=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$torch" in auto | cpu | cu130 | none) ;; *) die "--torch is auto, cpu, cu130 or none, not '$torch'" ;; esac

case "$(uname -s)" in
  Linux) ;;
  Darwin) die "there is no macOS build of litrag yet. It runs from source there: README, 'From source (developers)'." ;;
  *) die "this is the Linux installer; on Windows run install.cmd" ;;
esac
[ "$(uname -m)" = x86_64 ] || die "this build is for x86_64 Linux, and this machine is $(uname -m)"
[ -x "$here/app/litrag" ] && [ -f "$here/app/resources/parser/uv.lock" ] ||
  die "no app/ beside this script: extract the whole archive, then run install.sh from the folder it made"

root="${LITRAG_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/litrag}"
mkdir -p "$root"
root="$(cd "$root" && pwd)"
log="$root/install.log"
exec > >(tee -a "$log") 2>&1
step() { printf '\n== %s\n' "$*"; }
echo ""
echo "litrag install, $(date '+%Y-%m-%d %H:%M:%S'), from $here into $root"

# ---- 1. the app ---------------------------------------------------------------------------
step "The app"
if [ "$here/app" -ef "$root/app" ]; then
  echo "running from the install itself; app/ stays as it is"
else
  if command -v pgrep >/dev/null && pgrep -f "$root/app/litrag" >/dev/null; then
    die "litrag is running from $root/app: close it, then run install.sh again"
  fi
  rm -rf "$root/app.new"
  cp -a "$here/app" "$root/app.new"
  rm -rf "$root/app"
  mv "$root/app.new" "$root/app"
  cp "$here/uninstall.sh" "$root/uninstall.sh" # the uninstaller stays with the install, so the download can go
fi
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/app/resources/parser/pyproject.toml" | head -1)"
echo "litrag $version in $root/app"

# ---- 2. uv --------------------------------------------------------------------------------
# astral's installer, into uv/ and nowhere else: no PATH edits, and no update receipt, which
# would take the place of the receipt of a uv this person installed for themselves. It runs on
# every install, so an update brings a uv that can read the new lock; if it cannot be reached,
# the uv already here is used.
step "uv"
uv="$root/uv/uv"
fetch() {
  if command -v curl >/dev/null; then curl -LsSf "$1"; elif command -v wget >/dev/null; then wget -qO- "$1"; else return 127; fi
}
if fetch https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$root/uv" UV_NO_MODIFY_PATH=1 UV_DISABLE_UPDATE=1 sh; then
  :
elif [ -x "$uv" ]; then
  echo "(could not reach astral.sh; keeping the uv already in $root/uv)"
else
  die "could not install uv from https://astral.sh/uv/install.sh (needs curl or wget, and the network)"
fi
[ -x "$uv" ] || die "uv is not at $uv after its installer ran"
"$uv" --version

# ---- 3. torch -----------------------------------------------------------------------------
step "torch"
gpu=""
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  gpu="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
fi
if [ "$torch" = auto ]; then
  if [ -n "$gpu" ]; then torch=cu130; else torch=cpu; fi
  echo "auto: ${gpu:+nvidia-smi sees $gpu, so }$torch"
else
  echo "$torch, as asked${gpu:+ (nvidia-smi sees $gpu)}"
fi
driver="${gpu##*, }"
if [ "$torch" = cu130 ] && [ -n "$gpu" ] && [ "${driver%%.*}" -lt 580 ] 2>/dev/null; then
  echo "warning: CUDA 13 needs an NVIDIA driver of R580 or newer and this one is $driver;"
  echo "         torch will not see the GPU until the driver is updated (or use --torch cpu)"
fi

# ---- 4. the parser's environment ----------------------------------------------------------
step "The parser's Python environment (the first time: a few GB, several minutes)"
extra=()
[ "$torch" = none ] || extra=(--extra "$torch")
UV_PROJECT_ENVIRONMENT="$root/venv" \
  UV_PYTHON_PREFERENCE=only-managed \
  UV_PYTHON_INSTALL_DIR="$root/uv/python" \
  UV_CACHE_DIR="${UV_CACHE_DIR:-$root/uv/cache}" \
  "$uv" sync --frozen --no-dev --python "$python" --project "$root/app/resources/parser" ${extra[@]+"${extra[@]}"} ||
  die "uv sync failed (above). Fix what it names and run install.sh again; the app is in place already"
py="$root/venv/bin/python"
[ -x "$root/venv/bin/litrag-parser" ] || die "the environment has no litrag-parser"
torch_says="$("$py" -c 'import torch, docling, litrag_parser.worker; print(torch.__version__, "cuda" if torch.cuda.is_available() else "no cuda")')" ||
  die "the environment does not import (above)"
echo "torch $torch_says"

# ---- 5. Docling's models ------------------------------------------------------------------
libroot="${LITRAG_ROOT:-${PROTRACKER_LIBRARY:-$HOME/.protracker/library}}"
models="$libroot/models/docling"
step "Docling's models"
if [ "$prefetch" = 1 ] || [ -n "$(ls -A "$models" 2>/dev/null)" ]; then
  # the layout model and TableFormer are all the worker's pipeline uses (no OCR, no enrichment);
  # docling-tools lays them out as the worker's artifacts_path reads them (~0.7 GB)
  echo "fetching the layout and table models into $models"
  "$root/venv/bin/docling-tools" models download layout tableformer --quiet -o "$models" ||
    die "could not fetch Docling's models into $models"
  prefetched="$models"
else
  echo "not prefetched: the first paper fetches them (~0.5 GB, once). --prefetch-models does it now."
  prefetched=""
fi

# ---- 6. Ollama ----------------------------------------------------------------------------
step "Ollama"
ollama_says="skipped"
if [ "$skip_ollama" = 1 ]; then
  echo "skipped (--skip-ollama)"
elif command -v ollama >/dev/null; then
  if ollama pull nomic-embed-text; then
    ollama_says="ready, with nomic-embed-text"
  else
    ollama_says="installed, but nomic-embed-text was not pulled"
    echo "could not pull nomic-embed-text: is Ollama running (ollama serve)? Then: ollama pull nomic-embed-text"
  fi
else
  ollama_says="not installed"
  echo "Ollama is not installed. Papers are read without it; Query, and naming headings by"
  echo "meaning, need it. Its Linux installer needs root, so it is not run from here:"
  echo "  https://ollama.com/download/linux   then   ollama pull nomic-embed-text"
fi

# ---- 7. the launcher ----------------------------------------------------------------------
step "The launcher"
flags=""
# Chromium's sandbox needs unprivileged user namespaces (or a setuid helper, which needs root).
# Where the kernel or AppArmor withholds them, as Ubuntu 24.04 does, the app cannot start with it.
if [ "$(id -u)" = 0 ] || ! unshare --user --map-root-user true 2>/dev/null; then
  flags=" --no-sandbox"
  echo "as root, or without user namespaces, the app runs without Chromium's sandbox"
fi
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat >"$apps/litrag.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=litrag
Comment=One library of papers per project, read into trees you can see
Exec=env "LITRAG_VENV=$root/venv" "$root/app/litrag"$flags
Terminal=false
Categories=Science;Education;
EOF
echo "$apps/litrag.desktop"

# ---- 8. what was done ---------------------------------------------------------------------
step "Done"
run="$root/app/litrag$flags"
# the app finds venv/ beside app/ at the default root; anywhere else it has to be told
[ "$root" = "$(cd "${XDG_DATA_HOME:-$HOME/.local/share}" && pwd)/litrag" ] || run="LITRAG_VENV=$root/venv $run"
cat <<EOF
  litrag $version   $root/app
  parser         $root/venv ($torch; torch $torch_says, Python $("$py" -c 'import platform; print(platform.python_version())'))
  Ollama         $ollama_says
  models         ${prefetched:-fetched on the first paper}
  libraries      $libroot (untouched)
  log            $log

Start it from the applications menu (litrag), or: $run
Run this again to update; $root/uninstall.sh removes it (never the libraries).
EOF
