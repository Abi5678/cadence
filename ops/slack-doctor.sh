#!/usr/bin/env bash
# Change which Slack members Hermes accepts (SLACK_ALLOWED_USERS) by re-adding the Slack channel.
# Usage: bash ops/slack-doctor.sh U0C7BM52U48[,U...]   (prompts for the Slack tokens; nothing is stored in git)
set -euo pipefail
if ! docker info >/dev/null 2>&1 && [ -z "${CADENCE_IN_SG:-}" ]; then
  exec sg docker -c "CADENCE_IN_SG=1 bash $(printf %q "$0") $(printf %q "${1:-}")"
fi
[ -n "${1:-}" ] || { echo "usage: $0 <member ids, comma-separated>"; exit 1; }
export PATH=$HOME/.local/node-v22.23.3-linux-arm64/bin:$HOME/.local/bin:$PATH
export SLACK_ALLOWED_USERS="$1" NEMOCLAW_AGENT=hermes
printf 'Slack bot token (xoxb-, hidden): ' >&2; IFS= read -r -s SLACK_BOT_TOKEN; printf '\n' >&2
printf 'Slack app token (xapp-, hidden): ' >&2; IFS= read -r -s SLACK_APP_TOKEN; printf '\n' >&2
export SLACK_BOT_TOKEN SLACK_APP_TOKEN
echo "If asked for 'Slack Channel IDs', press Enter to skip (DMs don't need it). Never paste a token there." >&2
nemohermes cadence channels remove slack --yes || nemohermes cadence channels remove slack
nemohermes cadence channels add slack --yes || nemohermes cadence channels add slack
nemohermes cadence status | tail -3
echo "Hermes now accepts Slack messages from: $SLACK_ALLOWED_USERS"
