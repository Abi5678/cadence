#!/usr/bin/env bash
# One command before every rehearsal / take: fresh synthetic data, services healthy, month-end packets built.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
bash "$HERE/reset-demo.sh" >/dev/null
for i in $(seq 1 30); do curl -fs localhost:8090/api/health >/dev/null && break; sleep 1; done
H=$(curl -fs localhost:8090/api/health)
echo "health: $H"
echo "speech: $(curl -fs --max-time 60 localhost:8001/health || echo DOWN)"
curl -fs -X POST "localhost:8090/api/ccm/close?direct=true" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("month-end %s: %d packets ready, $%.2f; not billable: %d" % (d["month"], d["ready_for_review"], d["billable_total"], len(d["not_billable"])))'
cat <<'TXT'

Ready. Open on the demo laptop:
  Cadence app (Weaver):  http://localhost:8090/web/
  Mission control:       http://localhost:8090/web/mission.html   (press ? for director keys)
Doctor (Darsana): Slack -> Apps -> Cadence -> Messages.
TXT
