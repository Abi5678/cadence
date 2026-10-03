#!/usr/bin/env bash
# Load staged container images and tag vLLM locally (GB10-HERMES-PLAN §7.3).
set -euo pipefail
STAGE=${STAGE:-$HOME/stage}
for t in "$STAGE"/images/{vllm-ngc,hermes-sandbox-v0.0.124,openshell-supervisor-0.0.116,cuda-12.8.0-base-ubuntu24.04}.tar; do
  id=$(docker load -i "$t" | awk '/Loaded image/{print $NF}'); echo "$t -> $id"
  case "$t" in */vllm-ngc.tar) docker tag "$id" cadence/vllm:ngc ;; esac
done
docker image inspect ghcr.io/nvidia/nemoclaw/hermes-sandbox:v0.0.124 --format 'hermes digests: {{.RepoDigests}}' || true
