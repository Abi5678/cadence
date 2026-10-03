#!/usr/bin/env bash
# Install NemoClaw + Hermes against the running vLLM, then wire Cadence MCP + skill (§7.7, §7.9).
# Prereqs: vLLM answering on :8000, ops/make-tls.sh done, OpenShell 0.0.116 installed, docker access.
set -euo pipefail
STAGE=${STAGE:-$HOME/stage}
HERE=$(cd "$(dirname "$0")/.." && pwd)
export PATH=$HOME/.local/node-v22.23.3-linux-arm64/bin:$HOME/.local/bin:$PATH
curl -fsS localhost:8000/v1/models >/dev/null || { echo "vLLM not ready on :8000"; exit 1; }
openshell --version
mkdir -p ~/src && [ -d ~/src/NemoClaw ] || tar -xzf "$STAGE/software/nemoclaw-v0.0.124-6f3cced.tgz" -C ~/src
export npm_config_cache=$STAGE/software/npm-cache npm_config_prefer_offline=true
export NEMOCLAW_AGENT=hermes NEMOCLAW_PROVIDER=vllm NEMOCLAW_SANDBOX_NAME=cadence \
  NEMOCLAW_CORPORATE_CA_BUNDLE=$HOME/cadence-tls/ca.pem
printf 'Slack bot token (xoxb-, hidden): ' >&2; IFS= read -r -s SLACK_BOT_TOKEN; printf '\n' >&2
printf 'Slack app token (xapp-, hidden): ' >&2; IFS= read -r -s SLACK_APP_TOKEN; printf '\n' >&2
printf 'Slack member IDs allowed (comma-separated): ' >&2; IFS= read -r SLACK_ALLOWED_USERS
export SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_ALLOWED_USERS
(cd ~/src/NemoClaw && bash install.sh --non-interactive --yes-i-accept-third-party-software) || nemohermes onboard --resume
unset SLACK_APP_TOKEN
nemohermes cadence status
# Cadence service env file (outside git): Slack bot token for doctor escalations + Hermes API token
BR=$(ip -4 -o addr show docker0 | awk '{print $4}' | cut -d/ -f1)
CADENCE_MCP_TOKEN=$(cat ~/cadence-tls/mcp-token); export CADENCE_MCP_TOKEN
nemohermes cadence mcp add cadence --url "https://$BR:8443/mcp" --env CADENCE_MCP_TOKEN --trusted-private-host "$BR"
nemohermes cadence skill install "$HERE/skills/cadence-business-agent/"
mkdir -p ~/.config/cadence && umask 077 && cat > ~/.config/cadence/env <<ENV
CADENCE_MCP_TOKEN=$CADENCE_MCP_TOKEN
HERMES_API_TOKEN=$(nemohermes cadence gateway-token --quiet)
SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN
CADENCE_AGENT_BACKEND=hermes
ENV
openshell forward list
echo "Done. Restart the service: systemctl --user restart cadence"
