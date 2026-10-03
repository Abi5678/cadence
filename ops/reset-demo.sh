#!/usr/bin/env bash
# Wipe the synthetic clinic database and restart the service with fresh seed data (for rehearsals).
set -euo pipefail
systemctl --user stop cadence
rm -f "${CADENCE_DB:-$HOME/.local/share/cadence/cadence.db}"{,-wal,-shm}
systemctl --user start cadence
echo "Fresh demo data loaded: http://localhost:8090/web/clinic.html"
