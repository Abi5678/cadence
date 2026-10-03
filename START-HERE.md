# Cadence — coordinator UI handoff

For Abishek · Saturday, October 3, 2026

This package replaces the earlier clinician-first UI handoff. It contains the latest published coordinator-first source, commit `3418a2c0830354a92d406dd8ee10641b419dbeed` (September 30, 2026).

## Run

1. Unzip this archive.
2. Open a terminal in the `Cadence-Coordinator-UI` folder.
3. Run `python3 serve.py` (Windows: `py serve.py`).
4. Open **http://localhost:8080/web/**.

No npm setup, API keys or internet connection is required. Python 3 serves the static UI. If port 8080 is occupied, use `python3 serve.py 8081` and open that port. Stop with Ctrl+C. Use localhost instead of opening index.html directly so document hashing works.

## Start with the coordinator

1. Choose “Prepare an encounter handoff” on the home screen. The supporting-document requirement is selected automatically.
2. Assign the task. Watch the three-agent demo swarm plan the work and wait for evidence.
3. Attach `fixtures/signed-encounter.txt` through the task conversation and send it.
4. Watch the ribbons continue weaving. Review the proposed handoff, download its JSON preview, and approve the demo output.
5. Create a schedule. Try Run now, edit it, and pause it. The local scheduler runs only while the page is open.
6. Open Doctor inbox and simulate a Dr. Chen request with an attachment. It becomes another tracked task; nothing is posted to Slack.
7. Use Clinic settings to change the timezone, channel, destination label and review preference.
8. Open Encounter review in the sidebar to access the previous clinical demo and its patient/coordinator/operator roles.

## What is included

- Complete editable frontend: task conversation, swarm progress, follow-up information, attachments, schedules, doctor inbox, connections, activity and settings.
- Approved logo, self-hosted fonts, original ribbon geometry, Iris & Champagne styling, and available design boards.
- Original encounter workflows and synthetic document fixture.
- Four Node.js test files, the original PRD and implementation guide, and an updated build handoff.

Read **docs/BUILD-HANDOFF.md** before connecting the backend. The original PRD describes the earlier clinician-first scope; the latest coordinator-first direction in the handoff takes precedence for the main UI.

## What is real and what is simulated

Forms, navigation, task controls, file hashing, local state, schedule editing and preview downloads work. Task execution, swarm agents and Slack intake are simulations. No inference, live Slack delivery, real clinical analysis, external upload or local-system write occurs. File bytes are not retained; only metadata and hashes are stored.

Synthetic tasks and preferences persist on this browser using localStorage. The earlier encounter demo has separate in-memory state. A backend is required for shared records, authentication, durable schedules and real tool execution. Do not enter real patient data.

## Validate

With Node.js installed, run:

```
node tests/operations.test.cjs
node tests/coordinator-view.test.cjs
node tests/state.test.cjs
node tests/weave.test.cjs
```

These passed on the published source. Browser visual verification was unavailable; check the actual demo machine and mobile layout before presenting.

This archive excludes hosting credentials, deployment configuration and Git internals. It runs independently of the private hosted Site.
