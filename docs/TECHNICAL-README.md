# Cadence — coordinator workspace

The primary UI now serves the staff coordinator. It preserves the approved Weave visuals, Iris & Champagne palette, typography and wordmark.

## Run

Serve this folder with `python3 -m http.server 8080 --bind 127.0.0.1`, then open http://localhost:8080/web/.

## New primary workflow

Assign a task → inspect the three-agent demo swarm → add documents/context → review a proposed handoff → approve the demo output. Pause, resume, cancel, task search and status filters are functional. The Doctor inbox includes an explicitly simulated Slack request/file entry point. Schedules support create/edit/delete, pause, run now, weekday/day filtering, and due-time runs while the browser remains open. Settings persist clinic identity, timezone, handoff review preference, channel and destination labels.

Synthetic task, schedule and inbox state is saved on this browser using localStorage. Files are hashed locally; only file metadata and hashes are saved, not document bytes or interpreted clinical contents. Do not use real patient data. The local demo scheduler does not run when the page is closed, cannot catch up missed runs and is not an unattended production scheduler.

`web/operations-model.js` is the new state adapter, `web/coordinator.js` the views/controllers, and `web/coordinator.css` the layout extension. The original guarded encounter workflows remain at `web/encounter.html` and are linked in the sidebar. They retain their independent in-memory fixture adapter.

## Required backend integration

Connect an authenticated task service with persistent tasks and schedules, an agent event stream, durable workers, Slack verified event intake and thread replies, GB10 orchestration, and an authenticated local-system bridge. Replace simulated transitions with service-confirmed state. Preserve evidence gaps and approvals; do not mark actual delivery complete without a receipt. The proposed runtime uses the roles planner, evidence and handoff, but the prototype does not execute an LLM or tools.

No Slack message, clinical judgment, external upload or local filesystem write is performed by the demo. Output downloads are labeled JSON previews. Connection cards describe the prerequisites and stay disconnected.

## Tests

Run `node tests/operations.test.cjs`, `node tests/coordinator-view.test.cjs`, `node tests/state.test.cjs`, and `node tests/weave.test.cjs`. These cover task lifecycle and evidence requirements, schedule CRUD, inbox intake, restoration, all views, escaping, and preserved encounter gates. Browser visual QA is unavailable for this static project in the current preview environment.

---

## Original encounter implementation notes

## Demo path

1. Open the Patient demo session, confirm the exact administrative fields and audio acknowledgment, then check in. Return to Clinician and load the clearly labeled transcript fixture. A local WAV can be selected, but live ASR is disabled until connected to GB10.
2. Review the small cited summary. Accept, reject, or edit it; saving edits invalidates prior review. Accept the exact current version.
3. Complete the encounter. This creates a missing-document request assigned to Dr. Chen with a Slack preview, not an actual message.
4. Open Your documents, download the synthetic TXT, select it, and attest. Case, encounter, assigned clinician, signed status, size and actual SHA-256 are checked in the browser.
5. Document arrival automatically starts simulated assembly and version verification. No manual agent prompt or verification button is needed.
6. Use the explicitly simulated role switch to open Coordinator. Inspect provenance, download the mock manifest, and acknowledge the handoff separately from readiness.
7. Operator can remove a source to invalidate readiness or reset. Patient kiosk is a separate, interactive check-in view; check-in and consent gate audio and transcript work. Journey distinguishes seeded history from timestamped browser events.

## Files

- `web/index.html`: shell, approved woven wordmark, role and evidence dialogs.
- `web/weave.css`: pearl, iris and champagne theme, responsive layouts and reduced-motion support.
- `web/weave.js`: state-driven ribbon visualization, entry weaving and information-arrival motion.
- `web/assets/`: approved wordmark and self-hosted fonts.
- `web/model.js`: isolated in-memory fixture adapter and version/role guards.
- `web/app.js`: clinician, document, journey, patient, coordinator and operator views.
- `fixtures/signed-encounter.txt`: synthetic signed-document attestation, including an inert hostile instruction.
- `tests/state.test.cjs`: workflow guard and invalidation checks.
- `dist/`: matching static deployment output.

## Backend integration boundary

Replace `CadenceModel` methods with authenticated service responses. The browser is not an authorization or verification boundary.

| UI action | Guide service route |
|---|---|
| Audio upload | POST /visits/{id}/audio |
| Local transcription | POST /asr/jobs; GET /asr/jobs/{id} |
| Draft summary | POST /profile-drafts |
| Exact-version review | POST /profile-drafts/{id}/review |
| Complete encounter | POST /mock/ehr/encounters/{id}/complete |
| Request missing document | POST /document-requests |
| Assigned upload | POST /document-requests/{id}/upload |
| Agent assembly / verification | POST /packets; POST /packets/{id}/verify |
| Read packet | GET /packets/{id} |
| Confirm administrative fields | PATCH /kiosk/profile |
| Patient check-in | POST /kiosk/checkins |
| Runtime evidence | GET /demo/evidence |

The backend owns independent role sessions, case/encounter binding, CSRF protection, server-generated upload paths, file/type/size validation, signature-status checks, idempotency keys, expected versions, source hashes and fresh independent verification. Persist pending work in SQLite with outbox/reconciliation as specified by the guide. Keep Slack in OpenClaw with minimal operational notices; do not send clinical transcript or attachments. Poll authenticated state or use service events to update the UI. Populate telemetry only from actual runtime measurements.

## Deliberate scaffold limits

All state is in browser memory and resets on refresh. Demo role switching is not authentication. No live LLM, ASR, GB10, Slack, EHR, payer, OpenShell, durable worker, real upload or independent source verification is implemented. The TXT fallback is supported; PDF handling remains a backend task. Real audio is not transcribed; fixture text is explicitly independent of any selected recording. The summary fixture has no clinical diagnosis or inferred billing code. The kiosk allows preferred-name and contact-preference confirmation only; it cannot update clinical fields. Pre-visit approvals remain seeded context.

The sample hostile document instruction is rendered only as escaped text. This demonstrates neither agent refusal nor sandbox enforcement; both evidence fields stay “not observed.” LLM routing stays unknown, and GPU/performance values stay N/A. No claim submission, real clinical signature, compliance or runtime validation is claimed.

## Validation

Run `node tests/state.test.cjs`. Checks cover stale and rejected reviews, mismatched/unsigned/unattested documents, demo role guards, automatic readiness, duplicate document events, separate acknowledgment, source drift during verification and reset. JavaScript syntax checks passed. Browser visual/interactivity QA could not run in the available preview environment; responsive styles are included but visually unverified.

## PRD alignment follow-up

Check-in now starts pending and requires explicit patient confirmation. Blocked packets list missing requirements and their owners. The coordinator sees the accepted summary, reviewer, source version and fixture verification time. A file read that finishes after the originating run, role or source revision changed is rejected. These are frontend simulation guards; backend enforcement remains required.

## The Weave

Every view uses the approved pearl, iris and champagne identity and woven wordmark. Ribbons weave into place when entering a view. Check-in inputs, document selection, accepted evidence and verification transitions animate the weave again. Missing evidence retains a visible gap; packet completion is tied to the guarded fixture state. The ribbons open their related review, document or requirement views.

Clinician, summary review, Documents, patient check-in, coordinator packet, operator evidence and Activity use this design. Activity provides read-only historical snapshots. Reduced-motion preferences disable ribbon animation. Fonts and the approved wordmark are served locally.

Run `node tests/state.test.cjs` and `node tests/weave.test.cjs` to verify workflow guards, every route, entry motion, document gaps and readiness transitions.
