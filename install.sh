#!/bin/sh
# multi-grok installer (macOS / Linux).
#
# Prerequisites:
#   - python3 >= 3.8 on PATH
#   - run this file from a clone: ./install.sh
#   - no root privileges; files go under --prefix (default ~/.local)
#
# The installer does not edit the shell startup file, and it does not remove
# account directories or grok-<name> launchers on uninstall.
set -eu

PREFIX="${HOME}/.local"
CONFIG_FILE=""
UNINSTALL=0
MARKER="# managed-by: multi-grok-installer"

usage() {
  cat <<EOF
Usage: install.sh [--prefix DIR] [--config FILE] [--uninstall] [-h|--help]

Install multi-grok from a clone of the repository.

Prerequisites:
  python3 >= 3.8. Run ./install.sh from the clone. Remote curl installs are
  not provided until a release archive exists.

Options:
  --prefix DIR     install under DIR (default: ~/.local)
                   files: DIR/share/multi-grok and DIR/bin/multi-grok
  --config FILE    after installing, run 'multi-grok apply -f FILE'
  --uninstall      remove the tool only; configuration, account directories
                   and grok-<name> launchers are kept
  -h, --help       show this help

Examples:
  ./install.sh
  ./install.sh --prefix "\$HOME/.local"
  ./install.sh --config examples/config.example.json
  ./install.sh --uninstall
EOF
}

log() { printf '[multi-grok-install] %s\n' "$*"; }
die() { printf '[multi-grok-install] error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) [ $# -ge 2 ] || die "--prefix needs a value (see -h)"; PREFIX="$2"; shift 2 ;;
    --config) [ $# -ge 2 ] || die "--config needs a value (see -h)"; CONFIG_FILE="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf '[multi-grok-install] error: unknown option: %s (see -h)\n' "$1" >&2; exit 2 ;;
  esac
done

case "$PREFIX" in
  *"'"*) die "--prefix must not contain a single quote" ;;
esac

SHARE_DIR="${PREFIX}/share/multi-grok"
BIN_DIR="${PREFIX}/bin"
WRAPPER="${BIN_DIR}/multi-grok"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
SOURCE_DIR="${SCRIPT_DIR}/src/multi_grok"

if [ "$UNINSTALL" -eq 1 ]; then
  removed=0
  if [ -d "$SHARE_DIR" ]; then rm -rf "$SHARE_DIR"; removed=1; fi
  if [ -f "$WRAPPER" ] && sed -n 2p "$WRAPPER" | grep -qF "$MARKER"; then
    rm -f "$WRAPPER"
    removed=1
  fi
  if [ "$removed" -eq 1 ]; then
    log "removed multi-grok from ${PREFIX}; configuration, accounts and launchers are kept"
  else
    log "unchanged: multi-grok is not installed under ${PREFIX}"
  fi
  exit 0
fi

[ -f "${SOURCE_DIR}/__init__.py" ] || die "run install.sh from a clone; missing ${SOURCE_DIR}"

PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || die "python3 not found; install Python 3.8 or newer"
case "$PYTHON" in
  *"'"*) die "the path of python3 contains a single quote" ;;
esac
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "python3 at ${PYTHON} is older than 3.8"
case "$PYTHON" in
  */shims/*)
    REAL_PYTHON=$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || true)
    case "$REAL_PYTHON" in
      *"'"*) REAL_PYTHON="" ;;
    esac
    if [ -n "$REAL_PYTHON" ] && [ -x "$REAL_PYTHON" ]; then
      log "using ${REAL_PYTHON} instead of the shim ${PYTHON}"
      PYTHON="$REAL_PYTHON"
    fi
    ;;
esac

mkdir -p "$BIN_DIR"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/multi-grok-install.XXXXXX")
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT
mkdir -p "${STAGE}/src/multi_grok"
# 不把 __pycache__ 装进去。cp 之后删，避免依赖 rsync。
cp -R "${SOURCE_DIR}/." "${STAGE}/src/multi_grok/"
find "${STAGE}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -name '*.pyc' -type f -exec rm -f {} + 2>/dev/null || true

cat > "${STAGE}/wrapper" <<EOF
#!/bin/sh
${MARKER}
export PYTHONPATH='${SHARE_DIR}/src'\${PYTHONPATH:+:\$PYTHONPATH}
export PYTHONDONTWRITEBYTECODE=1
exec '${PYTHON}' -m multi_grok "\$@"
EOF

same=0
if [ -d "${SHARE_DIR}/src/multi_grok" ] && [ -f "$WRAPPER" ] \
   && diff -rq -x __pycache__ -x '*.pyc' "${STAGE}/src/multi_grok" "${SHARE_DIR}/src/multi_grok" >/dev/null 2>&1 \
   && cmp -s "${STAGE}/wrapper" "$WRAPPER"; then
  same=1
fi
if [ "$same" -eq 1 ]; then
  log "unchanged: ${WRAPPER}"
else
  rm -rf "$SHARE_DIR"
  mkdir -p "$SHARE_DIR"
  mv "${STAGE}/src" "${SHARE_DIR}/src"
  mv "${STAGE}/wrapper" "$WRAPPER"
  chmod 755 "$WRAPPER"
  log "installed ${WRAPPER}"
fi

if [ -n "$CONFIG_FILE" ]; then
  [ -f "$CONFIG_FILE" ] || die "config file not found: ${CONFIG_FILE}"
  "$WRAPPER" apply -f "$CONFIG_FILE"
fi
