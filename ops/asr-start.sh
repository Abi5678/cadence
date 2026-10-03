#!/usr/bin/env bash
# Build (first time) and run the NVIDIA speech service on 127.0.0.1:8001.
set -euo pipefail
cd "$(dirname "$0")/asr"
docker image inspect cadence/asr >/dev/null 2>&1 || docker build -t cadence/asr .
docker rm -f cadence-asr >/dev/null 2>&1 || true
mkdir -p "$HOME/.cache/cadence-asr"
docker run -d --name cadence-asr --restart unless-stopped --gpus all --network host --ipc host \
  -v "$HOME/.cache/cadence-asr:/models" cadence/asr
echo "warming up (downloads models on first start)..."
until curl -fs localhost:8001/health; do sleep 5; done; echo
