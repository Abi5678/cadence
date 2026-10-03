#!/usr/bin/env bash
# Stage everything Cadence needs on the GB10 onto an external ExFAT drive, so
# nothing large has to come over venue WiFi. See docs/GB10-HERMES-PLAN.md.
#
# Usage: scripts/stage-drive.sh <dir on an ExFAT drive> [lightning] [qwen27b] [llamacpp] [asr]
#   runs on macOS or on the GB10 itself (Linux)
#   core (~45 GB): Qwen3.6-35B-A3B-NVFP4, Nemotron-3-Nano-4B-FP8, NGC vLLM image,
#                  Hermes + OpenClaw sandboxes, OpenShell 0.0.116, NemoClaw v0.0.124,
#                  Node 22, uv, Python wheels, npm cache, this repo
#   extras:        lightning (+35 GB)  Nemotron-3.5-Lightning-30B-A3B NVFP4 + its vLLM image
#                  qwen27b   (+31 GB)  Qwen3.6-27B-FP8 (dense, slower)
#                  llamacpp  (+25 GB)  Nemotron-3-Nano-30B-A3B GGUF + llama.cpp server image
#                  asr       (+2 GB)   whisper-large-v3-turbo
# Resumable: finished items are skipped, so rerun it after a dropped connection.
# Needs: hf (huggingface_hub), crane or docker, curl, git, npm, python3 + pip, shasum.
set -euo pipefail

D="${1:?usage: $0 /Volumes/DRIVE [lightning] [qwen27b] [llamacpp] [asr]}"; shift
EXTRAS=" $* "
REPO="$(cd "$(dirname "$0")/.." && pwd)"
has() { case "$EXTRAS" in *" $1 "*) return 0 ;; esac; return 1; }

# --- preflight -------------------------------------------------------------
[ -d "$D" ] && [ -w "$D" ] || { echo "not a writable directory: $D" >&2; exit 1; }
if command -v diskutil >/dev/null; then
  fs="$(diskutil info "$D" 2>/dev/null | awk -F': *' '/File System Personality/{print $2}')" || true
else
  fs="$(findmnt -no FSTYPE --target "$D" 2>/dev/null)" || true
fi
case "$fs" in [Ee][Xx][Ff][Aa][Tt]) ;;
  *) echo "expected an ExFAT drive (Ubuntu reads it natively), got '${fs:-unknown}'" >&2; exit 1 ;; esac
for c in hf curl git npm python3 shasum; do
  command -v "$c" >/dev/null || { echo "missing tool: $c" >&2; exit 1; }
done
command -v crane >/dev/null || command -v docker >/dev/null || { echo "missing tool: crane (or docker)" >&2; exit 1; }
need=50
has lightning && need=$((need + 35))
has qwen27b && need=$((need + 32))
has llamacpp && need=$((need + 25))
has asr && need=$((need + 2))
free=$(( $(df -k "$D" | awk 'NR==2{print $4}') / 1024 / 1024 ))
[ "$free" -ge "$need" ] || echo "WARNING: ${free} GB free on $D, plan needs ~${need} GB total (fine if partly staged already)" >&2

mkdir -p "$D"/models "$D"/images "$D"/software "$D"/wheels "$D"/tmp
LOCAL_HF="${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}"  # this machine's own HF cache, if any
LOCAL_TMP="${TMPDIR:-/tmp}"  # local disk: a git clone on ExFAT turns symlinks into plain files
# The Mac's internal disk is nearly full: keep caches and temp files on the drive.
# HF_HOME stays default so an `hf auth login` token never lands on the drive.
export HF_HUB_CACHE="$D/.hf-cache/hub" HF_XET_CACHE="$D/.hf-cache/xet" TMPDIR="$D/tmp" \
  HF_HUB_DISABLE_TELEMETRY=1 COPYFILE_DISABLE=1

# --- helpers ---------------------------------------------------------------
model() {  # repo revision dir [single-file]
  local out="$D/models/$3"
  if [ -f "$out/.complete" ]; then echo "skip model $3"; return; fi
  # Seed from this machine's HF cache when it holds this exact revision; hf download
  # below then hash-checks the copied files instead of downloading them again.
  local snap="$LOCAL_HF/models--${1//\//--}/snapshots/$2"
  if [ -z "${4:-}" ] && [ -d "$snap" ] && [ ! -d "$out" ]; then
    echo "seeding $3 from $snap"; mkdir -p "$out"; cp -RL "$snap/." "$out/"
  fi
  # --local-dir writes real files (no symlinks), which ExFAT requires.
  hf download "$1" ${4:-} --revision "$2" --local-dir "$out"
  echo "$1@$2" > "$out/.complete"
}
image() {  # ref name
  local out="$D/images/$2.tar"
  if [ -f "$out" ]; then echo "skip image $2"; return; fi
  if command -v crane >/dev/null; then
    crane pull --platform linux/arm64 "$1" "$out.part"
  else  # e.g. on the GB10 itself, where docker is native arm64
    docker pull --platform linux/arm64 "$1" && docker save -o "$out.part" "$1"
  fi
  mv "$out.part" "$out"
  printf '%s\t%s\n' "$2.tar" "$1" >> "$D/images/IMAGES.txt"
}
fetch() {  # url filename
  local out="$D/software/$2"
  if [ -f "$out" ]; then echo "skip $2"; return; fi
  curl -fL --retry 3 -o "$out.part" "$1"
  mv "$out.part" "$out"
}

# --- models ----------------------------------------------------------------
model nvidia/Qwen3.6-35B-A3B-NVFP4 491c2f1ea524c639598bf8fa787a93fed5a6fbce qwen3.6-35b-a3b-nvfp4
model nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 main nemotron-3-nano-4b-fp8
has lightning && model nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 0dcd680e5585c791728c83342b311d0a0026dbeb nemotron-3.5-lightning-30b-a3b-nvfp4
has qwen27b && model Qwen/Qwen3.6-27B-FP8 e89b16ebf1988b3d6befa7de50abc2d76f26eb09 qwen3.6-27b-fp8
has llamacpp && model unsloth/Nemotron-3-Nano-30B-A3B-GGUF 9ad8b366c308f931b2a96b9306f0b41aef9cd405 nemotron-3-nano-30b-a3b-gguf Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf
has asr && model openai/whisper-large-v3-turbo main whisper-large-v3-turbo

# --- container images (linux/arm64, docker-load-able tarballs) -------------
image nvcr.io/nvidia/vllm@sha256:9204569b17ee4c0eff75194b8e6e458479c8aee18953b5ab9cf359fcdac659e2 vllm-ngc
image ghcr.io/nvidia/nemoclaw/hermes-sandbox:v0.0.124 hermes-sandbox-v0.0.124
image ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.124 openclaw-sandbox-v0.0.124
image ghcr.io/nvidia/openshell/supervisor:0.0.116 openshell-supervisor-0.0.116
image nvidia/cuda:12.8.0-base-ubuntu24.04 cuda-12.8.0-base-ubuntu24.04
has lightning && image vllm/vllm-openai@sha256:3af90144a0926e5c5fe46ee16e5201e763dd854538b9d7ce433755f11dadaf78 vllm-openai-lightning
has llamacpp && image ghcr.io/nvidia/nemoclaw/llama-cpp-server@sha256:9d0cddd7bcaf98d3b75a7fc8c7ce3af3a9973b5f23a8092e7e93a9afc473a675 llama-cpp-server

# --- software --------------------------------------------------------------
fetch https://nodejs.org/dist/v22.23.3/node-v22.23.3-linux-arm64.tar.xz node-v22.23.3-linux-arm64.tar.xz
fetch https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz uv-aarch64-unknown-linux-gnu.tar.gz
OS_REL=https://github.com/NVIDIA/OpenShell/releases/download/v0.0.116
for f in openshell_0.0.116-1_arm64.deb \
         openshell-aarch64-unknown-linux-musl.tar.gz openshell-checksums-sha256.txt \
         openshell-gateway-aarch64-unknown-linux-gnu.tar.gz openshell-gateway-checksums-sha256.txt \
         openshell-sandbox-aarch64-unknown-linux-musl.tar.gz openshell-sandbox-checksums-sha256.txt; do
  fetch "$OS_REL/$f" "$f"
done

# NemoClaw lkg as a tarball (ExFAT cannot hold the repo's symlinks), plus an npm
# cache warmed for linux/arm64 so the installer's npm steps can run offline.
NC_TGZ="$D/software/nemoclaw-v0.0.124-6f3cced.tgz"
if [ ! -f "$NC_TGZ" ]; then
  W="${LOCAL_TMP%/}/cadence-nemoclaw-src"
  [ -d "$W/NemoClaw" ] || git clone --depth 1 --branch v0.0.124 https://github.com/NVIDIA/NemoClaw.git "$W/NemoClaw"
  [ "$(git -C "$W/NemoClaw" rev-parse HEAD)" = 6f3cced4230ae9660c049cc11804daf37797c595 ] \
    || { echo "NemoClaw v0.0.124 is not commit 6f3cced; stop and recheck the lkg" >&2; exit 1; }
  tar -czf "$NC_TGZ.part" -C "$W" NemoClaw && mv "$NC_TGZ.part" "$NC_TGZ"
  npm_flags="--ignore-scripts --no-bin-links --no-audit --no-fund --os=linux --cpu=arm64"
  # npm ci (not install) so the lockfile stays untouched.
  (export npm_config_cache="$D/software/npm-cache"
   cd "$W/NemoClaw" && npm ci $npm_flags && cd nemoclaw && npm ci $npm_flags) \
    || echo "WARNING: npm cache warm-up failed; the installer will fetch npm packages at the venue" >&2
  rm -rf "$W"  # temp clone this script created; the tarball is the deliverable
fi

# Python wheels for the Cadence service (DGX OS = Ubuntu 24.04, CPython 3.12, aarch64).
python3 -m pip download --quiet --dest "$D/wheels" --only-binary=:all: \
  --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
  --platform manylinux_2_28_aarch64 --platform linux_aarch64 \
  --python-version 3.12 --implementation cp \
  fastapi 'uvicorn[standard]' pydantic httpx python-multipart slack_sdk mcp

# This repo (plan, UI, fixtures). No .git, no secrets.
rsync -rt --exclude .git --exclude .DS_Store "$REPO/" "$D/cadence/"

# --- checksums -------------------------------------------------------------
(cd "$D" && find models images software wheels cadence -type f \
   ! -name '*.part' ! -name '._*' ! -path '*/.cache/*' ! -path 'software/npm-cache/*' -print0 \
   | xargs -0 shasum -a 256 > SHA256SUMS)
echo "staged on $D:"
du -sh "$D"/models/* "$D"/images "$D"/software "$D"/wheels 2>/dev/null
echo "verify on the GB10: cd <copy> && sha256sum -c SHA256SUMS --quiet"
