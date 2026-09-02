#!/usr/bin/env bash
# Offline Demo Smoke Kit - setup for macOS / Linux.
#
#   bash scripts/setup.sh [--tts] [--torch cuda|rocm|cpu|auto] [--torch-index URL]
#                         [--prefetch auto|turbo|nano|classic|none] [--model NAME]
#                         [--python PATH] [--base-url URL] [--no-doctor]
#
# Creates .venv, installs requirements.txt, optionally torch + requirements-tts.txt
# (voice cloning) followed by `python -m demo_smoke prefetch --tts <--prefetch>` (default
# auto = what `run --tts auto` picks here), optionally `ollama pull NAME`, then runs
# `python -m demo_smoke doctor`. Idempotent (safe to re-run), never uses sudo, never
# touches anything outside the kit except pip's and Hugging Face's caches.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KIT_DIR"

WITH_TTS=0
TORCH=auto
TORCH_INDEX=""
TORCH_VERSION="${DEMO_SMOKE_TORCH_VERSION:-2.6.0}"
PREFETCH=auto
MODEL=""
PY=""
RUN_DOCTOR=1
BASE_URL="${DEMO_SMOKE_BASE_URL:-http://localhost:11434/v1}"

usage() {
  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() { echo "setup.sh: $*" >&2; exit 3; }

while [ $# -gt 0 ]; do
  case "$1" in
    --tts) WITH_TTS=1 ;;
    --torch) [ $# -ge 2 ] || die "--torch needs a value"; TORCH="$2"; shift ;;
    --torch=*) TORCH="${1#--torch=}" ;;
    --torch-index) [ $# -ge 2 ] || die "--torch-index needs a value"; TORCH_INDEX="$2"; shift ;;
    --torch-index=*) TORCH_INDEX="${1#--torch-index=}" ;;
    --prefetch) [ $# -ge 2 ] || die "--prefetch needs a value"; PREFETCH="$2"; shift ;;
    --prefetch=*) PREFETCH="${1#--prefetch=}" ;;
    --model) [ $# -ge 2 ] || die "--model needs a value"; MODEL="$2"; shift ;;
    --model=*) MODEL="${1#--model=}" ;;
    --python) [ $# -ge 2 ] || die "--python needs a value"; PY="$2"; shift ;;
    --python=*) PY="${1#--python=}" ;;
    --base-url) [ $# -ge 2 ] || die "--base-url needs a value"; BASE_URL="$2"; shift ;;
    --base-url=*) BASE_URL="${1#--base-url=}" ;;
    --no-doctor) RUN_DOCTOR=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "setup.sh: unknown option: $1" >&2; usage >&2; exit 4 ;;
  esac
  shift
done

case "$TORCH" in cuda|rocm|cpu|auto) ;; *) die "--torch must be cuda, rocm, cpu or auto (got '$TORCH')" ;; esac
case "$PREFETCH" in auto|turbo|nano|classic|none) ;; *) die "--prefetch must be auto, turbo, nano, classic or none (got '$PREFETCH')" ;; esac

# ---------------------------------------------------------------- python
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

if [ -z "$PY" ]; then
  for c in python3.11 python3.12 python3.10 python3.13 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || die "no Python >= 3.10 found; install Python 3.11 or pass --python /path/to/python"
py_ok "$PY" || die "$PY is older than Python 3.10"
echo "== python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"
if [ "$WITH_TTS" = 1 ]; then
  # torch 2.6.0 (pinned by chatterbox-tts) ships wheels for CPython 3.9-3.13 only,
  # and none for Intel (x86_64) macOS.
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)' \
    || die "--tts needs Python 3.10-3.13 (torch $TORCH_VERSION has no wheels for $("$PY" -c 'import platform; print(platform.python_version())')); pass --python /path/to/python3.11"
  if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = x86_64 ]; then
    die "torch $TORCH_VERSION has no Intel-Mac wheel; voice cloning needs Apple Silicon or Linux/Windows (run without --tts and use --tts tone)"
  fi
fi

# ---------------------------------------------------------------- venv
VPY="$KIT_DIR/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "== creating .venv"
  "$PY" -m venv "$KIT_DIR/.venv" || die "could not create .venv (on Debian/Ubuntu: apt install python3-venv)"
else
  echo "== .venv exists, reusing"
fi

echo "== installing requirements.txt"
"$VPY" -m pip install --disable-pip-version-check --quiet --upgrade pip
"$VPY" -m pip install --disable-pip-version-check -r requirements.txt

# ---------------------------------------------------------------- tts (optional)
if [ "$WITH_TTS" = 1 ]; then
  OS="$(uname -s)"
  if [ "$TORCH" = auto ]; then
    if [ "$OS" = Darwin ]; then TORCH=cpu            # PyPI wheels include MPS on Apple Silicon
    elif command -v nvidia-smi >/dev/null 2>&1; then TORCH=cuda
    elif command -v rocminfo >/dev/null 2>&1 || [ -d /opt/rocm ]; then TORCH=rocm
    else TORCH=cpu
    fi
    echo "== torch accelerator (auto): $TORCH"
  fi
  if [ -z "$TORCH_INDEX" ]; then
    case "$TORCH" in
      cuda) TORCH_INDEX="https://download.pytorch.org/whl/cu126" ;;
      rocm) [ "$OS" = Linux ] || die "ROCm torch wheels exist for Linux only; use --torch cpu"
            TORCH_INDEX="https://download.pytorch.org/whl/rocm6.2.4" ;;
      cpu)  [ "$OS" = Darwin ] || TORCH_INDEX="https://download.pytorch.org/whl/cpu" ;;
    esac
  fi
  echo "== installing torch==$TORCH_VERSION torchaudio==$TORCH_VERSION ${TORCH_INDEX:+from $TORCH_INDEX}"
  if [ -n "$TORCH_INDEX" ]; then
    "$VPY" -m pip install --disable-pip-version-check "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" --index-url "$TORCH_INDEX"
  else
    "$VPY" -m pip install --disable-pip-version-check "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
  fi
  echo "== installing requirements-tts.txt (chatterbox-tts)"
  "$VPY" -m pip install --disable-pip-version-check -r requirements-tts.txt
  if [ "$PREFETCH" != none ]; then
    echo "== prefetch --tts $PREFETCH (Chatterbox weights into the Hugging Face cache)"
    "$VPY" -m demo_smoke prefetch --tts "$PREFETCH" || die "prefetch failed; re-run: python -m demo_smoke prefetch --tts $PREFETCH (while online)"
  else
    echo "== remember: python -m demo_smoke prefetch --tts auto   while still online (caches what --tts auto uses here)"
  fi
fi

# ---------------------------------------------------------------- ollama model (optional)
if [ -n "$MODEL" ]; then
  if command -v ollama >/dev/null 2>&1; then
    echo "== ollama pull $MODEL"
    ollama pull "$MODEL" || echo "setup.sh: warning: ollama pull failed (is 'ollama serve' running?)" >&2
  else
    echo "setup.sh: warning: ollama not on PATH; install it from https://ollama.com and run: ollama pull $MODEL" >&2
  fi
fi

# ---------------------------------------------------------------- doctor
rc=0
if [ "$RUN_DOCTOR" = 1 ]; then
  echo "== doctor"
  if [ -n "$MODEL" ]; then
    "$VPY" -m demo_smoke doctor --base-url "$BASE_URL" --model "$MODEL" || rc=$?
  else
    "$VPY" -m demo_smoke doctor || rc=$?
  fi
fi

cat <<MSG
== done. Next (the example scenario needs your app at http://localhost:3000 with /login and /manuals,
   and its login reads DEMO_USER / DEMO_PASS; copy your reference clip into the kit, e.g. voice/ref.wav):
   source .venv/bin/activate
   python -m demo_smoke voice-check --ref voice/ref.wav
   DEMO_USER=... DEMO_PASS=... python -m demo_smoke run scenarios/example-chat-with-manuals.json --out demo-output/chat-with-manuals --narration template --ref voice/ref.wav
   DEMO_USER=... DEMO_PASS=... opencode run --agent demo-smoke --auto --command smoke "scenarios/example-chat-with-manuals.json demo-output/chat-with-manuals"
   No app yet? Try the bundled mock app first: see README "Try it on the bundled mock app".
MSG
exit "$rc"
