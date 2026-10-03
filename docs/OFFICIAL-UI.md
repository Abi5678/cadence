# Official Cadence UI — backend integration notes

This is the approved October 3 UI, imported from source commit `3721b5e5166580906f75997ffd2f93ab8f778006`. Preserve its woven wordmark, Iris & Champagne colors, typography, ribbon geometry and Weaver behavior while connecting the backend.

## Entry points

- `web/index.html`: coordinator workspace — home, tasks, task detail, schedules, doctor inbox, connections, activity and settings.
- `web/encounter.html`: clinician review/documents/journey and patient check-in/journey. The role selector returns to the same main coordinator workspace.
- `web/clinic.html`: Abi's existing live service console, retained separately. This import does not change its API wiring or the service root redirect.

Run the static preview with `python3 serve.py 8091`, then open `/web/`. The existing service also serves repository static files on its configured port (default 8090).

## Integration boundaries

| File | Responsibility / next integration step |
| --- | --- |
| `operations-model.js` | Local task, swarm, schedule, inbox and settings adapter. Replace simulated transitions with authenticated service state and events. |
| `coordinator.js` | Workspace rendering, forms and demo polling. Connect task actions and file handling to durable backend operations. |
| `model.js` | Clinical fixture adapter and review/version/evidence guards. Enforce those guarantees server-side. |
| `app.js` | Clinical and patient rendering and workflow actions. |
| `weaver.js` | Coordinator chat, mascot and task completion cues. Replace deterministic replies with real agent responses. |
| `role-chat.js` | Clinician/patient chat and matching mascot lifecycle. Replace local responses with role-scoped agent sessions. |
| `weaver-work.js` | Shared queued greeting/working/celebration lifecycle and knot/confetti markup. |
| `weave.js` | Ribbon state, entry weaving and information-arrival motion. |
| `weaver.css` / `assets/weaver-poses.png` | Sprite poses, pointer response, knots, waves, celebration and reduced-motion behavior. |

`web/clinic.js` demonstrates the existing service's `/api/state`, `/api/events` SSE, task and approval APIs. Map those service responses to the approved UI rather than assuming the demo adapters already call them. Existing backend source and run configuration are unchanged.

The frontend scheduler runs only while the page is open. Browser state and role selection are not authentication or authorization. Document hashing currently processes files locally and stores metadata only; connect a real upload endpoint before describing this as server delivery. Keep task completion tied to verified service outcomes and delivery receipts.

## Animation lifecycle

Both coordinator and role chats use the shared work lifecycle. Busy work takes precedence over greetings and celebrations. Overlapping operations keep knot weaving active until the last operation finishes; queued gestures then receive their full duration. Preserve the begin/finish pairing (including error paths) when replacing local operations with asynchronous requests.

View entry and chat opening trigger greetings. Successful final replies and completed tasks trigger celebrations. Document processing and active tasks trigger knot weaving. Do not celebrate partial streamed tokens or failed operations. Keep the existing reduced-motion rules and hidden-tab behavior.

## Validation

Run the six Node test files listed in `START-HERE.md`. They cover clinical guards, workspace state, role routes, ribbon continuity, overlapping work and queued animation phases. These are source/lifecycle checks, not an end-to-end test of a connected backend. Perform visual and interaction checks on the demo machine after integration.
