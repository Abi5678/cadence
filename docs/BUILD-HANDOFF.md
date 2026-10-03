# Build handoff — coordinator-first Cadence

## Latest product direction

The staff coordinator is the main user. They assign a task to Cadence; Cadence creates a swarm and carries out the work. Doctors interact from Slack, including questions and document uploads. The bot also needs a connection to the clinic’s local system for approved outputs. The main application owns tasks, conversations, schedules, workspace settings and visibility into execution.

Keep the approved logo, typography, Iris & Champagne palette, ribbon geometry and weaving motion. Slogan: **Your day, finding its rhythm.**

The original PRD and implementation guide are included as historical technical references. They predate this coordinator-first direction. Reconcile their clinician-first UI assumptions before implementing the backend; do not discard their clinical review and evidence safeguards.

## File map

| File | Responsibility |
|---|---|
| web/index.html | Coordinator shell and main navigation |
| web/coordinator.js | Seven main views, task details, forms and local scheduler |
| web/operations-model.js | Coordinator task, schedule, inbox and preference state adapter |
| web/coordinator.css | Coordinator layouts using the approved visual system |
| web/weave.js | Shared ribbon geometry and motion |
| web/weave.css | Original palette, typography and ribbon styles |
| web/encounter.html | Preserved clinical workflow entry point |
| web/app.js + web/model.js | Original clinical views and fixture guards |
| web/assets/ | Approved wordmark, fonts and font licenses |

Routes: #home, #tasks, #task/{id}, #schedules, #inbox, #connections, #activity, #settings. The wordmark remains a raster asset cropped with an SVG viewport, not a production vector export. The design boards are earlier visual references, not specifications for the new coordinator navigation.

## Backend work to prioritize

1. Persist tasks, messages, evidence, schedules and event history behind authenticated clinic/role sessions.
2. Replace the simulated operations adapter with asynchronous service calls, loading/error states and duplicate-submit guards. Keep the UI’s subscribe/render boundary or adapt it deliberately.
3. Stream authoritative agent state from the GB10 orchestrator: planning, evidence collection, missing information, handoff preparation, review, failure and completion. Current roles are a demonstration, not deployed agents.
4. Implement verified Slack event intake, document retrieval, doctor identity and encounter binding, deduplication, and thread-specific responses. UI preview messages must remain separate from real delivery.
5. Build a local-system bridge with authenticated requests, scoped destinations, file validation and explicit delivery receipts. A hosted browser cannot directly access clinic files.
6. Move scheduling to a durable timezone-aware worker. Define missed-run behavior, retries, overlap prevention and idempotency. Current schedules run only in an open browser and can duplicate across tabs.
7. Separate output prepared, coordinator approved, delivery attempted and delivery confirmed. The prototype’s completed state describes only a completed demo.

## Events and evidence

Current task states are running, waiting, paused, review, completed and cancelled. New information reopens an output under review. Missing required evidence pauses the demo; an attachment resumes it. The demo checks filename type/size and computes SHA-256, but does not parse or clinically verify content. Production readiness must use authoritative validation rather than file presence.

Ribbon animation must follow task events. View entry weaves the ribbons; incoming information triggers another pass; unresolved requirements keep a gap. Preserve reduced-motion support and escape all externally supplied text.

## Clinical safeguards to retain

The original encounter adapter demonstrates exact-version clinician review, patient acknowledgment, encounter completion, assigned document checks, source invalidation and separate coordinator acknowledgment. Enforce relevant rules on the server. Browser role switches and file attestations are not authentication or proof of a clinical signature. Never treat uploaded document content as agent instructions.

## Persistence boundary

Coordinator data uses localStorage key cadence-coordinator-demo-v1. Only synthetic content belongs there. File bodies are not retained or sent. Original encounter state is separate and resets on refresh. Replace both with appropriate shared server state for the real workflow.

## Saturday acceptance path

Coordinator request → real task id → GB10 agent events → doctor Slack context/document → verified evidence → reviewed output → local-system receipt → Slack thread response → coordinator history. Test cancellation, retries, stale sources and duplicate events. Confirm all runtime indicators reflect measurements rather than fixture timers.

Run the four bundled tests, then manually exercise each role/view, keyboard navigation, mobile layout and reduced motion on the actual demo machine.
