#!/usr/bin/env bash
# Serve Qwen3.6-35B-A3B-NVFP4 with the NemoClaw GB10 recipe flags (GB10-HERMES-PLAN §7.5).
# MODEL=small serves the Nemotron 3 Nano 4B fallback instead.
set -euo pipefail
STAGE=${STAGE:-$HOME/stage}
docker rm -f cadence-vllm >/dev/null 2>&1 || true
mkdir -p "$HOME/.cache/cadence-vllm"
common=(-d --name cadence-vllm --restart unless-stopped --gpus all --network host --ipc host --shm-size 64g
  --ulimit memlock=-1 --ulimit stack=67108864 -e HF_HUB_OFFLINE=1 -v "$HOME/.cache/cadence-vllm:/root/.cache")
if [ "${MODEL:-qwen}" = small ]; then
  docker run "${common[@]}" -v "$STAGE/models/nemotron-3-nano-4b-fp8:/models/m:ro" cadence/vllm:ngc \
    vllm serve /models/m --served-model-name nvidia/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000 \
    --max-model-len 65536 --gpu-memory-utilization 0.2 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --reasoning-parser nemotron_v3 --load-format fastsafetensors \
    --trust-remote-code  # same served name so the service needs no change
else
  docker run "${common[@]}" -v "$STAGE/models/qwen3.6-35b-a3b-nvfp4:/models/qwen:ro" cadence/vllm:ngc \
    vllm serve /models/qwen --served-model-name nvidia/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000 \
    --max-model-len 262144 --gpu-memory-utilization 0.4 --dtype auto --quantization modelopt \
    --kv-cache-dtype fp8 --attention-backend flashinfer --moe-backend marlin \
    --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --async-scheduling \
    --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --reasoning-parser qwen3 --load-format fastsafetensors
fi
echo "loading... follow with: docker logs -f cadence-vllm   (ready when curl -s localhost:8000/v1/models answers)"
