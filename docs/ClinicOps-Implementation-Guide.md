# ClinicOps — One-Day Implementation Guide

**Companion:** [ClinicOps PRD](ClinicOps-PRD.md)  
**Build target:** Dell Pro Max with GB10, local inference, NemoClaw + OpenClaw + OpenShell, live Slack connector  
**Runtime data:** synthetic only; no implementation or GB10 validation claimed  
**Revision:** 1.3, September 28, 2026 — two builders, one patient journey, Slack + local kiosk  
**Working rule:** every completed case needs a fresh read from the mock source that owns the final state.

## 1. Build target and architecture

Build one `CASE-104` journey: confirmation → insurance/deductible review → provider-driven reschedule → kiosk check-in → local speech transcription/review → missing-document recovery → verified draft claim packet. Team: two builders. Staff: real Slack via OpenClaw. Patient: local simulated kiosk. Monitoring, Telegram, additional agents and full clinical profile updates: excluded from the event build. Pre-visit work appears as completed history; the live sequence is brief check-in → short local transcription → reviewed summary → missing-document recovery → security evidence → verified packet. Earlier authorization, waitlist and unsigned-note scenarios are alternate fixtures, outside the stage script.

The [event](https://luma.com/rvinoam5) requires local execution with NemoClaw, OpenClaw, OpenShell and an actual messaging channel. Build 10:30–18:00; internal feature freeze 16:15; five rehearsals; record 17:00; submit 17:30. Three minutes is a planning assumption. Application-prework permission is unresolved.

```text
EXTERNAL                                  DELL GB10 HOST
Slack <---- minimum notices ----> [OpenShell sandbox]
                                  OpenClaw agent (NemoClaw managed)
                                  Intake / Documentation / Claim modules
                                      |                   |
                          inference.local              typed tools
                                      |                   |
                             local LLM server      authenticated service
                                                         |-- SQLite / audit / outbox
Local kiosk + clinician browser <-- scoped sessions ---->|-- mock EHR/payer/scheduler
                                                         |-- local ASR worker
                                                         |-- profile review / uploads
                                                         |-- packet builder + verifier
                                                         |-- event loop / reconciliation
Local evidence panel <---------- redacted state + host telemetry
```

The supplied diagram becomes three modules under one agent. Replace Clinical Triage with Visit Documentation. Label the host and sandbox separately; the host service does not inherit OpenShell process/network restrictions. All modules share the local LLM. ASR uses a separate local endpoint. SQLite is synthetic storage, not automatically encrypted PHI. Slack is external, so local inference is not air-gapping.

NemoClaw manages the OpenClaw sandbox and inference routing; the service owns business state, policy, writes and verification. [Overview](https://docs.nvidia.com/nemoclaw/latest/about/overview.html); [routing](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/inference/about-inference-routing).

## 2. Responsibility map

| Component | Owns | Does not own |
|---|---|---|
| Source simulator | Seed data; synthetic event timing; fake EHR/payer/scheduler responses | Agent decisions |
| Detector | Cross-object rules and deduplicated case creation | Free-form model planning |
| Case engine | Lifecycle, locks, retries, outbox, reconciliation | Clinical judgment |
| OpenClaw agent | Investigates scoped case, selects allowed plan, explains result, uses tools | Approval/role checks, direct SQL writes |
| Action service | Preconditions, approval, idempotency, mock writes, verification | Inventing patient facts |
| OpenShell | Sandbox filesystem and network policy; MCP tool name filtering | Business-rule validation of arguments |
| Slack | Staff-facing notices, approvals, questions | Source of truth for patient or payer state |

## 3. Repo structure

```text
clinicops/
  README.md                    # one-page quickstart and demo commands
  pyproject.toml               # FastAPI, SQLite adapter, MCP server, tests
  .env.example                 # variable names only; no real tokens
  app/
    main.py                    # FastAPI health, mock APIs, event endpoints
    db.py                      # SQLite connections, migrations, transactions
    models.py                  # strict request/response schemas
    events.py                  # append, dedupe, outbox, replay
    graph.py                   # node/edge upserts and graph reads
    detectors.py               # pure rules over source snapshots
    cases.py                   # state transitions and reconciliation
    policy.py                  # action matrix, actor roles, approval checks
    actions.py                 # mock side effects + verify-read
    kiosk.py                   # appointment-scoped patient sessions
    asr.py                     # local-only speech worker adapter
    profiles.py                # one reviewed summary draft; no full clinical profile merge
    uploads.py                 # actor/case-scoped document intake
    packets.py                 # manifest, hashes, requirement checks, verification
    sessions.py                # local role sessions, CSRF, expiry
    slack.py                   # case message builder; no full patient record
    hooks.py                   # OpenClaw webhook dispatcher and retry
    mcp_server.py              # Streamable HTTP tool surface
    telemetry.py               # host measurements + redacted policy log reader
    evidence_api.py            # read-only panel snapshots / event stream
  web/
    index.html                 # persistent four-area evidence strip + timeline
    kiosk.html                 # patient confirmation, acceptance, check-in
    clinician.html             # exact draft review and document upload
    packet.html                # coordinator evidence manifest
  plugin/                      # alternative in-sandbox tool implementation
  demo/
    injection.txt              # synthetic hostile instruction fixture
    boundary_probe.py          # fixed harmless marker; sandbox execution only
    mock_ehr.py
    mock_payer.py
    mock_scheduler.py
    mock_billing.py
  agent/
    AGENTS.md                  # agent instructions: tool use and boundaries
    examples.md                # case prompts and expected tool sequence
  fixtures/
    seed.json                  # synthetic patients, rules, records
    demo_events.jsonl          # replayable journey with explicit demo-clock jumps
    visit.wav                  # short synthetic audio, pre-recorded input
    signed-encounter.txt        # synthetic document with hostile instruction
    packet_requirements.json   # explicit fixture checklist and supplied billing fields
  scripts/
    seed.py
    replay.py
    reset.py
    smoke.py                   # end-to-end preflight
  tests/
    test_journey.py
    test_actions.py
    test_replay.py
    test_approvals.py
    test_documents_and_packet.py
  ops/
    openshell-policy-notes.md  # actual permitted hosts/ports after inspection
    runbook.md
```

The `profile-drafts` naming is retained for the small review component; MVP output is only a reviewed visit summary for packet inclusion. It does not update the full clinical chart.

This is a proposed tree, not a requirement to implement every file separately. Merge modules when time is tight; preserve their interfaces and behavioral tests.

## 4. Local model and machine setup

### Pre-event checklist

This is a setup plan, not a claim that the GB10 or software has already passed these checks.

1. Confirm the event's current rules, accepted team status, and whether prepared code/assets are permitted.
2. Stage the repository, Python/Node dependencies, Docker images, and **at least two compatible model weights** on an external drive. Record checksums and free disk requirements. The GB10 is Arm-based and the Dell product lists 128 GB memory; use Arm-compatible images and verify the actual event unit. [Dell specifications](https://www.dell.com/en-us/shop/desktop-computers/dell-pro-max-with-gb10/spd/dell-pro-max-fcm1253-micro/xcto_fcm1253_usx).
3. Create a dedicated Slack app and test workspace. Enable Socket Mode, install the bot, record the `xoxb-` bot token and `xapp-` app-level token, and grant `connections:write` to the latter. Use only the scopes needed for mentions, DMs, history and posting; the [OpenClaw minimal manifest](https://docs.openclaw.ai/channels/slack/setup) is a starting point.
4. Record Slack user IDs for scheduler, clinician, and operator, and the operations channel ID. Grant the bot `im:history` for the scheduler DM approval check. Do not authorize by display name.
5. Generate a long random hook token and separate MCP bearer token. Store them outside the repo.
6. Prepare synthetic records only. Check for names or IDs copied from real records.
7. Run a dry rehearsal on any comparable Linux/Arm setup, but expect to validate model performance on the actual GB10.

### Day-of order

1. Verify Docker, free disk, GPU visibility, clock/time zone, and Slack egress.
2. Start the **local** model server. Favor the NemoClaw-managed vLLM profile on a supported DGX Spark-like host if it is available and already staged. A documented supported choice is `Qwen/Qwen3.6-27B-FP8`. Easier fallback: local Ollama using a tool-capable installed model such as `qwen3.6:35b`; lower-memory fallback: `qwen3.5:9b`. Model names and profile availability can change, so select by the installed catalog and run the tool-call smoke test before relying on one. [NemoClaw local server comparison](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/choose-local-inference-server); [vLLM profiles](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm).
3. Install NemoClaw from the maintained installer, then run `nemoclaw onboard` and explicitly select **OpenClaw** and the local inference option. Select Slack during onboarding or add it later. Do not choose a hosted provider as a runtime fallback. The installer and onboarding sequence are documented in the [official quickstart](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/get-started/quickstart).

   ```bash
   curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
   nemoclaw onboard
   ```

   Review the installer before running it if required by the team's machine policy. Name the sandbox `clinicops` in the wizard so the remaining examples match.
4. Configure Slack allowlists (`SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`) before the sandbox is built or rebuilt. Ensure one active Socket Mode session uses the app token. [NemoClaw Slack setup](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/messaging-channels/set-up-slack).
5. Verify `nemoclaw clinicops status`, `nemoclaw clinicops doctor`, and a structured tool call from inside the sandbox. Replace `clinicops` with the chosen sandbox name. Record actual dashboard and gateway ports from the status output; do not hardcode them.

**Model choice rule:** choose the strongest **measured reliable tool caller** that loads in time. Ollama is a fast setup path, but some model/template combinations emit tool-call JSON as text. If this happens under a realistic case, move to a parser-configured local vLLM endpoint rather than parsing text as an action. [NemoClaw local inference](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/choose-local-inference-server).

### Local speech setup and acceptance gate

Use the Nemotron Speech guidance for selection, but treat current vendor documentation as the compatibility authority. Candidate: **Parakeet 1.1B CTC English**, single short English clip, no diarization. NVIDIA’s current matrix lists Spark support for that model and Parakeet 1.1B RNNT Multilingual. This is a candidate for the Dell GB10, not evidence that an arbitrary NIM image works on it. Verify the specific ARM64 image, model profile, driver and access entitlement on the actual box. [ASR support matrix](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html).

Step 1 — Record exact local ASR image digest, model ID/profile, architecture and runtime requirements. Cache permitted artifacts before the event; keep download credentials out of logs. Do not select a cloud endpoint.

Step 2 — Start the supported self-hosted container using the model-specific NVIDIA instructions. Bind service access to host loopback or a protected local interface. `ASR_BASE_URL` is a project setting, not a vendor CLI flag. The ClinicOps host adapter invokes ASR; the agent receives transcript IDs through typed tools and cannot set the destination URL.

Step 3 — Submit a 10–15 second synthetic WAV. Verify a fresh transcript, correct encounter association, inference process/GPU evidence, timestamps and no remote ASR fallback. A health endpoint alone is insufficient. The kiosk can upload a fixture WAV; browser-native speech recognition is not used because its processing location is not guaranteed.

Step 4 — Keep both LLM and ASR loaded if measured resources permit. Serialize audio transcription and LLM drafting when needed. Test actual coexistence; do not promise a latency based on another GPU’s benchmark. Show ASR processing seconds and audio seconds; use `processing_seconds / audio_seconds` for real-time factor.

**12:00 speech cutoff:** if the preferred ASR deployment fails, switch only to an already validated local speech runtime cached for this ARM64/GB10 machine. If none works, use a clearly labeled prerecorded transcript fixture and record the live-ASR requirement as unmet. Never call a cloud transcription service to hide the gap. A recorded input clip with fresh local ASR is still a live transcription demo; replaying a transcript is not.

## 5. State and storage

Use SQLite with WAL mode and one writer transaction per event or action. Never update a source object and publish a wake without a durable outbox row. Minimum tables:

```sql
source_objects(id TEXT PRIMARY KEY, kind TEXT, source TEXT,
  source_version INTEGER, status TEXT, patient_ref TEXT,
  body_json TEXT, observed_at TEXT);
graph_edges(id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT,
  relation TEXT, evidence_id TEXT);
source_events(event_id TEXT PRIMARY KEY, source TEXT, object_id TEXT,
  source_version INTEGER, event_type TEXT, body_json TEXT, received_at TEXT);
cases(case_id TEXT PRIMARY KEY, rule_id TEXT, subject_id TEXT,
  state TEXT, priority INTEGER, owner_role TEXT, version INTEGER,
  opened_at TEXT, updated_at TEXT, resolved_at TEXT);
case_events(id INTEGER PRIMARY KEY, case_id TEXT, from_state TEXT,
  to_state TEXT, reason_code TEXT, evidence_json TEXT, actor TEXT, at TEXT);
action_attempts(id TEXT PRIMARY KEY, case_id TEXT, action_type TEXT,
  idempotency_key TEXT UNIQUE, expected_versions_json TEXT,
  status TEXT, request_json TEXT, response_json TEXT, verified_at TEXT);
approvals(id TEXT PRIMARY KEY, case_id TEXT, action_type TEXT,
  nonce_hash TEXT, case_version INTEGER, actor_slack_id TEXT,
  decision TEXT, expires_at TEXT, used_at TEXT);
outbox(id TEXT PRIMARY KEY, kind TEXT, payload_json TEXT,
  attempts INTEGER, next_attempt_at TEXT, delivered_at TEXT);
```

Indexes: `source_events(object_id, source_version)`, `cases(state, priority)`, `approvals(case_id, action_type)`, `outbox(delivered_at, next_attempt_at)`. Unique case key: `(rule_id, subject_id)` for the event fixture so scans cannot open duplicates. Automatic reopening is deferred; contradictory later state removes the verified badge and flags manual review. Reset creates a new demo run ID.

### Journey-specific objects

Keep the generic tables above and add these small tables (columns below are required design fields, not complete migration SQL):

| Table | Required fields / invariant |
|---|---|
| `journeys` | `journey_id`, `patient_ref`, `encounter_id`; shared parent for module tasks |
| `reschedule_proposals` | original appointment/version, proposed slot/version, service date, proposal hash, approval ID, expiry |
| `patient_responses` | proposal ID/hash, authenticated kiosk session, decision, event ID/time; never supplied by model assertion |
| `checkins` | appointment ID/version, patient session, confirmed fields, consent acknowledgment, time |
| `audio_assets` | encounter ID, local path, hash, duration, actor; bounded synthetic files only |
| `transcripts` | audio ID/hash, model/runtime ID, text, elapsed time, status |
| `profile_drafts` | transcript ID, short summary draft, source references, version, reviewer, reviewed version/hash |
| `document_requests` | encounter, required type, assigned clinician, state, expiry |
| `documents` | request ID, encounter, hash, MIME, size, storage path, uploader, synthetic signed-status attestation |
| `packets` | encounter, requirements version, manifest hash, source-version snapshot, state, verifier/time |
| `notification_receipts` | case, milestone/version, dedupe key, Slack channel/ts; avoid repeated DMs |

Use UTC wall-clock audit times; store fixture `business_time` separately for date jumps. The source event records both. A synthetic visit advancing days must not create a negative or fabricated performance duration.

Graph: appointment requires eligibility for its service date; reschedule requires staff grant and patient acceptance of the same proposal; transcript derives from audio; draft derives from transcript; clinician review approves exact draft version; packet requires signed document and reviewed data. One `journey_id=CASE-104` links task IDs such as `TASK-104-SCHEDULE` and `TASK-104-PACKET`.

States include `WAITING_APPROVAL`, `WAITING_PATIENT`, `WAITING_REVIEW`, `WAITING_DOCUMENT`, and `ESCALATED`. A resolved schedule task does not resolve the packet task. Packet readiness is `READY_FOR_COORDINATOR_REVIEW`; later evidence changes immediately remove the badge and require fresh verification, without a general automatic-reopening engine.

## 6. Event loop and case transitions

Implement a **single long-running service** with these loops:

1. **Ingest:** source adapter accepts a synthetic event, validates `event_id` and monotonic `source_version`, stores it, updates the materialized source object and edges, and commits.
2. **Detect:** pure rules compare related objects and upsert cases. New or materially changed cases add a wake to the outbox.
3. **Dispatch:** outbox worker calls authenticated `POST /hooks/agent` with a short trusted message such as `Investigate CASE-104. Use ClinicOps tools; do not rely on this notification for facts.` Set `Idempotency-Key` to the outbox ID. Use an isolated agent session. A `200` is only admission; the service waits for action/case state, not a chat reply, to determine success.
4. **Reconcile:** every 30–60 seconds, re-read all open cases and pending actions. Wake only on a changed source version, expired wait, failed attempt, or overdue deadline. Back off repeated failures and cap attempts.
5. **Publish:** state-change outbox entries produce Slack notices. The agent can post through its OpenClaw Slack connector, but the service records a dedupe key and the agent must include that key in the publish tool request. Avoid a separate raw Slack bot client in MVP.

On restart: replay unprocessed source events, recompute materialized graph, resume undelivered outbox rows, and reconcile nonterminal cases. Do not re-run a write merely because a previous agent session disappeared; look up `action_attempts` by idempotency key and verify the external mock state first.

**Hook setup:** OpenClaw inbound hooks are disabled by default. Enable them with a dedicated token, allow only the chosen agent ID, validate configuration, and test a harmless local request to the actual forwarded gateway port. The [documented hook example](https://docs.openclaw.ai/automation/cron-jobs/webhooks) uses `POST /hooks/agent`, Bearer auth, `Idempotency-Key`, and `{message, name, agentId, deliver:false}`. Merge this shape into the sandbox's OpenClaw configuration, with the real long random token loaded from an approved secret source:

```json5
{
  hooks: {
    enabled: true,
    token: "<long-random-hook-token>",
    path: "/hooks",
    allowedAgentIds: ["main"],
    allowRequestSessionKey: false
  }
}
```

Run `openclaw config validate` and restart the managed gateway using the current NemoClaw/OpenClaw procedure; then send one harmless `POST /hooks/agent` with `Authorization: Bearer`, `Content-Type: application/json`, and a unique `Idempotency-Key`. Use the actual forwarded gateway port from `nemoclaw clinicops status`. Keep the hook bound to the local machine and expose no public URL. If the NemoClaw-managed gateway does not expose a usable host loopback hook path on event day, use one OpenClaw automation running every minute to call `clinicops_next_case`; preserve the same event log and case engine. [OpenClaw automation](https://docs.openclaw.ai/automation).

## 7. Detectors and event rules

Implement these bounded rules for the single journey:

| Rule | Condition | Actionable result |
|---|---|---|
| `CONFIRMATION_DUE` | Appointment reaches fixture reminder time and lacks a current response | Local kiosk confirmation task |
| `ELIGIBILITY_REQUIRED` | Missing/stale payer response for appointment service date | Read mock eligibility; persist evidence |
| `DEDUCTIBLE_REVIEW` | Known remaining amount exceeds configured fixture threshold | Factual review flag; no denial of care or payment claim |
| `PROVIDER_CONFLICT` | Provider availability invalidates booked slot | Candidate replacement under hard schedule rules |
| `VISIT_READY` | Kiosk check-in/audio submitted | Local transcription then draft review |
| `PACKET_BLOCKED` | Completed encounter lacks required reviewed data/document | Assigned clinician request; keep packet blocked |
| `PACKET_ASSEMBLABLE` | Requirements present and current | Agent builds, then verifies packet |

Deduplicate on journey + rule + subject/source version. A deductible threshold is a demo business configuration, not a clinical guideline. Unknown eligibility/deductible fields cause `UNKNOWN`/review, never a false pass. No-show, referral, denial, authorization and vitals detectors are deferred.

The event loop initiates work after each source event and catches missed changes on its periodic scan. An uploaded document’s contents cannot emit trusted source events or grant approval.

## 8. Project APIs and mocks

All paths below belong to this project, not vendor APIs. Use one FastAPI service, with authenticated roles and session scope. External-system responses are synthetic. Demo mutation routes bind to local operator access, not agent tools.

| Endpoint | Contract and guard |
|---|---|
| `POST /demo/reset` | Operator, explicit demo flag; new run ID, reset fixture only |
| `POST /demo/start` | Start automatic scripted source events; pause progression at real human inputs |
| `POST /demo/advance-clock` | Operator; change fixture business time and label it on panel |
| `GET /mock/scheduler/appointments/{id}` | Versioned current appointment |
| `GET /mock/scheduler/alternatives?appointment_id=...` | Matching provider, type, duration; one available and one conflicting fixture slot |
| `POST /mock/scheduler/reschedules` | Proposal, staff grant, patient response and versions required; atomic move |
| `GET /mock/payer/eligibility?appointment_id=...` | Coverage, service date, remaining/total deductible, currency, source timestamp; synthetic caveat |
| `POST /kiosk/confirmations` | Session-bound appointment confirmation |
| `POST /kiosk/reschedule-responses` | Session-bound proposal ID/hash, accept/decline, CSRF protection |
| `PATCH /kiosk/profile` | Session-bound administrative allowlist only; patient confirms exact changes |
| `POST /kiosk/checkins` | Correct appointment and fixture time; explicit administrative fields/consent |
| `GET /mock/ehr/encounters/{id}` | Current encounter, reviewed summary and signed-document metadata, each versioned |
| `POST /mock/ehr/encounters/{id}/complete` | Authenticated clinician fixture action; emits encounter-completed event |
| `GET /mock/ehr/documents/{id}` | Authorized source read; ownership, hash, status and version |
| `POST /visits/{id}/audio` | Authorized visit session; WAV, bounded size/duration; local path generated server-side |
| `POST /asr/jobs` | Audio ID only; server owns endpoint/model selection; returns job ID |
| `GET /asr/jobs/{id}` | Pending/failed/completed plus transcript ID and runtime metadata |
| `POST /profile-drafts` | Encounter, transcript ID, one cited summary draft; no direct clinical profile commit |
| `POST /profile-drafts/{id}/review` | Assigned clinician session; exact version/hash; accept/edit/reject |
| `POST /document-requests` | Missing type/encounter, assigned doctor; deduplicated |
| `POST /document-requests/{id}/upload` | Assigned clinician session; expiry/CSRF/size/type/ownership checks |
| `POST /packets` | Encounter and requirement version; missing evidence returns `MISSING_EVIDENCE` |
| `POST /packets/{id}/verify` | Fresh reads and manifest/hash checks; service-owned verification |
| `GET /packets/{id}` | Authorized coordinator; state, manifest, local summary |
| `GET /demo/evidence` | Redacted read-only timeline, metrics and sampled runtime evidence |

Every mutation uses an idempotency key; source-dependent changes use expected versions. Same key + same payload returns prior result; same key + changed payload is `409`. Stale source version is `409`, role failure `403`, expired session/link `401/410` as appropriate. For concurrent reschedules, perform conditional update and unique active-slot allocation inside one transaction; never cancel the original before acquiring the new slot.

Mock eligibility fixture example: active coverage for the current service date; $3,000 annual deductible, $1,800 remaining; configurable high-remaining threshold $1,000. Do not infer exact patient responsibility from these values. Billing fixture supplies all required codes/amounts; missing values block the packet.

Event envelope:

```json
{
  "event_id": "evt-provider-104-v2",
  "source": "mock-scheduler",
  "event_type": "provider.availability_changed",
  "object_id": "PROVIDER-104",
  "source_version": 2,
  "occurred_at": "<actual UTC time>",
  "business_time": "<fixture date/time>",
  "payload": {"unavailable_slot_id": "SLOT-104-A"}
}
```

### Kiosk, review and upload boundaries

Use separate patient, clinician, coordinator and operator sessions with short expiry. For the demo, operator provisions known fixture users; describe this as simulated identity. Session cookies are HttpOnly/SameSite, mutation routes require CSRF defense, and role switching requires an explicit authenticated session change. The LLM cannot create actor sessions or approve its own drafts.

The Slack link opens a local login/entry page; a URL query parameter alone cannot authorize upload. Bind each document request to encounter, assigned actor, allowed type and expiry. Use server-generated paths, reject traversal and oversized/unrecognized files, store outside the web root, and serve only authorized downloads. A practical fixture limit is 5 MB and TXT or text-only PDF; no OCR or arbitrary macros. Parse content as data in a restricted worker; do not execute attachments. Escape extracted text in HTML; do not render uploaded HTML/scripts or treat document instructions as application commands. Hash original bytes and keep provenance. Use TXT as the fast fallback.

A loopback link works only on the machine running the browser. Both presenters use browsers on the GB10 or preconfigured authenticated SSH forwards. If using private LAN instead, configure TLS and authentication; do not publish charts or upload routes through a public tunnel.

### Draft claim packet

Create `manifest.json` and `summary.html` in a local packet directory. PDF export is polish. Manifest records packet/encounter IDs, requirements version, source IDs/versions/hashes, reviewed draft hash, synthetic signed-document attestation, approvals, action IDs and verified time. Display unresolved items explicitly. Verification compares current records with the snapshot; no missing item, unsigned document, stale review or unknown required billing field may yield ready. Label the packet “Draft — ready for coordinator review; no claim submitted.”

## 9. Agent tool contracts

**Decision deadline: 11:30.** Rehearse this route before the event if preparation rules allow. If one read and one permitted write have not succeeded from the sandbox by 11:30, switch to the plugin fallback in section 13.

Expose a **small Streamable HTTP MCP server** from the ClinicOps service, with strict JSON schemas and bounded responses. Register it through NemoClaw's managed MCP path so OpenShell stores the bearer credential and scopes the endpoint. A private endpoint needs stable routable private HTTPS, a valid certificate/CA, a firewall rule, and explicit trusted-private registration; sandbox `127.0.0.1` is not the host. Test this topology early. NemoClaw's MCP `--deny-tool` filters tool names, **not argument values**, so `policy.py` must validate every action argument. [Managed MCP setup](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/manage-sandboxes/mcp-servers/add-an-mcp-server); [OpenShell policy](https://docs.nvidia.com/openshell/reference/policy-schema).

After the private HTTPS service and CA are ready, load the bearer value into `CLINICOPS_MCP_TOKEN` through a masked prompt or secret store and register the endpoint. Replace the example hostname with one that resolves to the GB10's stable private interface and matches its TLS certificate:

```bash
nemoclaw clinicops mcp add clinicops-tools \
  --url https://mcp.clinicops.example/mcp \
  --env CLINICOPS_MCP_TOKEN \
  --trusted-private-host mcp.clinicops.example
nemoclaw clinicops mcp status clinicops-tools --json
```

The second command must report a ready provider, policy, and adapter and a matching trusted private target before the agent uses the tools. Remove the bearer value from the host shell environment after registration.

| Tool | Inputs | Output | Server enforcement |
|---|---|---|---|
| `clinicops_next_case` | `limit <= 1` | One open case ID, rule, compact evidence IDs | Role/scope; no bulk dump |
| `clinicops_get_case` | `case_id` | Current version, rule, related IDs, allowed next steps | Case ID allowlist; redacted fields |
| `clinicops_get_evidence` | `case_id`, `evidence_id` | Typed summary, source version, timestamp | Evidence must belong to case; bounded text |
| `clinicops_get_rule` | `rule_id` | Mock payer/business rule | Versioned local rule only |
| `clinicops_propose_action` | `case_id`, `case_version`, `action_type`, `evidence_ids`, `reason` | Plan ID and policy verdict | Whitelisted plan; evidence provenance |
| `clinicops_execute_action` | `plan_id`, `expected_case_version`, `idempotency_key` | Attempt ID, status, result | Approval and source preconditions in transaction |
| `clinicops_verify_action` | `attempt_id` | Current source status, `verified` boolean | Fresh read from owning mock API |
| `clinicops_request_approval` | `plan_id`, `role`, `expires_in` | Approval ID/nonce and safe Slack text | Allowed role, single active nonce |
| `clinicops_record_approval` | `approval_id`, `dm_channel_id`, `reply_ts` | Grant or denial | Independently fetch DM message from Slack; check sender, text, nonce, case version |
| `clinicops_publish_update` | `case_id`, `milestone`, `dedupe_key` | Queued/published | Only whitelisted milestones and redacted template |
| `clinicops_status` | `scope` | Counts and top cases | No raw PHI |

Tool results always include `{ok, error_code?, case_id?, case_version?, evidence_ids?, retryable?}`. Return stable machine-readable codes: `STALE_CASE`, `MISSING_EVIDENCE`, `APPROVAL_REQUIRED`, `NOT_AUTHORIZED`, `SOURCE_CONFLICT`, `VERIFICATION_FAILED`, and `RATE_LIMITED`. The agent may retry only `retryable: true`, with a bounded two-attempt cap for transient operations; missing evidence waits for a new event.

The agent instruction file should say: fetch the case first; cite tool evidence IDs; use only one of the allowed plans; ask the service for approval; never treat a planned or submitted action as completed; call verification; report unresolved exceptions honestly; never put full note text or patient data into Slack. Model output is untrusted input to the policy service.

### Journey action schemas

Keep the generic tool surface above. `action_type` is a closed enum; forbid arbitrary URL, shell, SQL, file path or destination arguments.

| Action | Required identifiers | Additional guard |
|---|---|---|
| `CHECK_ELIGIBILITY` | appointment ID/version | Fixed mock payer adapter; result scoped to service date |
| `REQUEST_CONFIRMATION` | appointment ID/version | Kiosk outbox only; dedupe |
| `OFFER_RESCHEDULE` | proposal ID/hash | Scheduler grant for exact proposal |
| `COMMIT_RESCHEDULE` | proposal, patient response, grant | Matching acceptance and fresh free-slot check |
| `TRANSCRIBE_AUDIO` | encounter/audio ID | Audio belongs to encounter; local ASR only |
| `DRAFT_PROFILE` | transcript ID, cited proposed fields | Field allowlist; clinical result stays draft |
| `ACCEPT_REVIEWED_SUMMARY` | draft ID/version/hash, review ID | Independent clinician review; makes summary eligible for packet only |
| `REQUEST_DOCUMENT` | encounter, required type | Assigned clinician; dedupe request |
| `ASSEMBLE_PACKET` | encounter, requirement version | Complete source-backed requirements |

`clinicops_verify_action` verifies the owning source, not the model’s textual report. The model may propose a new plan when a tool says evidence is missing, select evidence IDs, draft a cited update, and explain an exception. Detectors/checklists do not pre-generate all reasoning text; the demo must still exercise real local tool selection.

## 10. Action sequence and approvals

```text
confirmation_due → agent checks eligibility → kiosk confirmation request
patient confirms → provider availability changes → agent proposes replacement
scheduler Slack approval verified → offer appears in kiosk
patient accepts → service checks versions → atomic reschedule → fresh read
operator explicitly advances fixture clock → patient checks in and submits audio
local ASR → transcript → one local LLM summary draft → clinician review/accept
encounter completed → missing-document detector → Slack clinician request
clinician uploads fixture → hostile content treated as untrusted evidence
real refusal/denial evidence displayed → valid required content retained
agent assembles packet → service freshly verifies sources/hashes/reviews
READY_FOR_COORDINATOR_REVIEW → minimal Slack handoff with local link
```

The staff approval service independently calls Slack `conversations.history` for the exact DM timestamp. Check bot-visible DM, sender ID and role, expected message text/code, proposal hash, case version, expiry and one-time use. The agent supplies a message reference, never a trusted user identity. OpenClaw owns the Slack session; the service’s bot-token use is only for verification, not another Socket Mode listener. Cache verified message references and honor Retry-After. [Slack API](https://docs.slack.dev/reference/methods/conversations.history/).

Patient acceptance is a separate authenticated kiosk event; the Slack approval cannot substitute for it. Clinical review accepts the summary for packet inclusion; it does not merge a full clinical profile. It is another separate local actor event; the scheduler cannot approve clinical text. New transcript/draft content invalidates an old review. A changed proposed slot invalidates the previous approval and acceptance.

At 15:00, if Slack identity verification is not working, use an authenticated local scheduler approval control with the same version-bound grant and label `approval_source=local_operator`. Slack still shows the request and outcome. State this fallback on stage; never imply that an unverified DM authorized the action.

## 11. OpenShell and credential boundary

Start from NemoClaw's generated policy. Inspect its effective form using `openshell policy get <sandbox> --full` before changes. Permit only the managed `inference.local` path, Slack channel egress needed by the connector, and the exact private MCP endpoint. Keep general web browsing, shell execution to host, and arbitrary network destinations unavailable to the agent. OpenShell can enforce destination, binary, and REST method/path rules, while managed MCP can filter tool names. Apply any needed network addition as a narrow rule and verify a denied request; do not replace the full policy casually because static filesystem and process settings are set at sandbox creation. [OpenShell policy schema](https://docs.nvidia.com/openshell/reference/policy-schema); [policy management](https://docs.nvidia.com/openshell/dev/how-it-works/policies/manage-policies).

Separate secrets: Slack bot/app tokens in NemoClaw/OpenShell channel setup; MCP bearer in OpenShell provider; hook token in host service and OpenClaw config. No tokens in Git, screenshots, trace payloads, or fixture JSON. Because Slack is a network service, “all inference local” must be checked by confirming no hosted LLM provider or cloud fallback is configured, not by claiming the box has no internet access.

## 12. Slack implementation and local pages

Use one dedicated synthetic workspace, `#clinicops-demo`, allowlisted users and one Socket Mode connection. Staff roles can be two demo users with explicitly configured roles; local patient/doctor sessions remain distinct. [OpenClaw Slack transport](https://docs.openclaw.ai/channels/slack/transports).

One journey maps to one channel thread; scheduler and assigned clinician receive DMs. Message templates: schedule conflict/proposed replacement; deductible review needed (details local); missing signed document with local entry link; packet ready for coordinator review; unresolved exception with owner. Avoid polling messages, chart text, audio, full patient identifiers or clinical drafts.

Keep the right-hand local browser in a single layout with tabs for timeline, patient kiosk, clinician review/upload and packet. The evidence strip stays visible in all tabs. Builder 1 narrates and operates Slack; Builder 2 operates explicitly labeled patient/clinician demo roles. Warm sessions and preload allowed fixtures; actions remain real and audited.

## 13. Two-person implementation phases and cutoffs

| Time | Builder 1: agent/platform/workflow | Builder 2: local experience/evidence | Exit gate |
|---|---|---|---|
| Before event, as permitted | Cache supported local model/runtime; rehearse platform setup | Cache ASR/image/audio artifacts; prepare design and fixtures only as rules permit | Do not assume application prework allowed |
| 09:00–10:30 | Event briefing and allowed machine setup | Inspect model assets and local browser access | Confirm event rules; application build starts 10:30 |
| 10:30–11:30 | NemoClaw, local LLM tool call, Slack, private HTTPS MCP | Local ASR smoke test; kiosk skeleton; API schema agreement | **11:30 MCP cutoff** |
| 11:30–12:30 | SQLite, source events, hooks, missing-document task and tool contracts | Brief check-in, fixture audio, local ASR adapter, single summary review | **12:00 ASR fallback decision**; prioritize live hero |
| 12:30–13:30 | Event loop, real agent document request/resume sequence, source verification | Authenticated upload and packet manifest; panel baseline | **13:30 hooks cutoff**; document arrival resumes packet work |
| 13:30–14:15 | OpenShell enforced probe and correlated logs | GPU/local endpoint collector; packet verification and clinician UI | Real security evidence and no false-ready packet |
| 14:15–15:00 | Minimal pre-visit mocks/approval verification; run and retain earlier history | Finish local ASR→review→upload chain; readable history summary | **15:00 approval cutoff**; no live rescheduling scene |
| 15:00–16:15 | Joint end-to-end checks and fix blocking defects | Joint stage layout, timing and evidence checks | **16:15 feature freeze**, no monitoring |
| 16:15–17:00 | Five complete rehearsals with timings | Operate role changes; record results/fallbacks | Fits three-minute stage script |
| 17:00–17:15 | Record genuine local backup run | Check playback readability and sound | Usable labeled recording |
| 17:15–17:30 | Package, submit, save receipt | Final device/session check | **Submit 17:30** |
| 17:30–18:00 | Submission/recovery buffer | No features | Official freeze 18:00 |

Agree on identifiers and request/response schemas first; neither person implements a competing state store. Builder 1 owns SQLite and action-policy interfaces; Builder 2 owns UI, ASR and packet adapters against those interfaces. Integrate at each exit gate, not at 16:00.

**11:30 tool fallback:** if private HTTPS MCP has not completed one real read and write from the sandbox, use a small in-sandbox OpenClaw plugin with the same typed tools and sandbox-local service/store. Kiosk/upload/ASR access still needs an explicitly supported, authenticated boundary. Prove that access before adopting this fallback; a plugin alone does not solve host ASR or browser connectivity. Use reviewed narrow runtime forwarding/service access, not unrestricted mounts or arbitrary shell access. If cross-boundary audio remains unavailable, label transcript fallback and record live ASR unmet. Keep redacted telemetry available to the host panel. [Tool plugins](https://docs.openclaw.ai/plugins/tool-plugins).

**13:30 trigger fallback:** use a documented OpenClaw scheduled scan every minute if hooks fail. An @mention is emergency recovery only and is disclosed; it does not replace the always-on requirement.

Exclude monitoring, additional detectors, multi-agent work, Telegram and full clinical profile updates. Microphone streaming and PDF export are expendable polish. Pre-visit work is always completed before the pitch and shown with real audit timestamps. Build the live document recovery/packet verification path first; if pre-visit automation is unfinished, show explicitly labeled synthetic context, not invented completed-agent history. Do not remove approval, provenance, idempotency or source rechecks to add visual polish.

## 14. Essential checks and rehearsals

Use six behavior-focused checks; no broad test suite is required for the one-day build:

1. **Journey:** source-driven start, deductible flag, approved/accepted reschedule, check-in, fresh local transcript, clinician-reviewed summary, document request, valid upload, independently verified packet. No prompt is needed for each next agent action.
2. **Permissions:** wrong/stale/reused Slack grant fails; kiosk response must match patient/proposal; missing clinical review and wrong-encounter upload cannot complete the packet. Slot conflict preserves a recoverable original appointment state.
3. **Replay/restart:** repeat a source event, upload or lost action reply; no duplicate booking/summary acceptance/packet/DM. Resume from durable pending state.
4. **Packet truth:** remove required document, change source version, corrupt hash or leave a draft unreviewed; readiness and synthetic value counter stay unset. Unsupported or stale evidence never becomes a green badge.
5. **Injection/enforcement:** uploaded instruction does not become authority; correlate actual sandbox denial with the fixed harmless probe and receiver observation. Continue legitimate packet assembly; separate refusal from OpenShell enforcement.
6. **Locality:** LLM and ASR endpoints/processes verified local; no hosted fallback. Validate transcript against the synthetic script for names/numbers/negation before its review. Stale telemetry shows unknown, unsupported GPU metric N/A. Record ASR/model latency with both loaded.

After freeze, five rehearsals with reset, real wall-clock durations, failure notes and chosen fallback. One failure rehearsal exercises a stalled packet without falsely reporting success. A clinician review click must remain meaningful even when the transcript is correct.

## 15. Local evidence panel and business measures

Build a single local page with plain HTML/CSS/JavaScript, served by the existing service. Use a two-second JSON poll for simplicity; SSE is optional. Slack occupies the left half of the projected screen, the panel the right. A fixed header shows current case and business outcome. Four areas make the runtime visible:

| Area | Source and update | Truth rule |
|---|---|---|
| Case timeline | `case_events` and fresh mock source reads | Render availability changed → rescheduled → checked in → transcribed → document missing → uploaded → packet verified; security event is separate |
| GPU and token speed | Host GPU telemetry supported by installed driver; server token counts and generation timing | Show GPU %, sample age, model ID and decode tokens/s when available; unsupported is N/A, not zero |
| Inference route | Read-only active OpenShell provider/route snapshot, local LLM upstream, local ASR endpoint/process and OpenClaw fallback configuration | `inference.local` alone does not prove local inference; inspect upstream and any hosted fallback |
| Policy denials | Actual OpenShell NET/HTTP deny events, sandbox ID, policy revision, timestamp and unique log offset/event ID | Count once per event, only since current demo start; keep refusals and app-policy rejects separate |

The intended route label is **“inference.local → local GB10 model · hosted inference providers: 0”**. Render it only when verified. “Hosted providers” here means inference providers, not Slack or other non-model services. If the check fails or the source is stale, show **unknown / check required**. Show the sampled version/time; this is observable evidence, not a formal proof of all machine traffic.

Token speed: prefer the server's generation counter divided by measured generation duration. If only whole-request duration is available, label it “output tokens / request second” because that includes prefill and tool latency. Never animate fake GPU usage or token rates. Keep collector credentials and privileged access on the host; the panel gets only a redacted snapshot. An unavailable driver metric is an explicit N/A; model throughput and process identity remain useful evidence.

### Minimal panel API (project endpoints)

`GET /demo/evidence` returns the current `run_id`, `sampled_at`, timeline, business measures, inference check, GPU sample and denial list. `GET /demo/evidence/events` is optional SSE. These endpoints are read-only and loopback-bound; when presenting from a laptop, use an authenticated SSH forward rather than exposing the panel publicly.

```json
{
  "run_id": "rehearsal-05",
  "sampled_at": "<actual UTC time>",
  "inference": {"route": "inference.local", "upstream_local": null,
    "hosted_inference_provider_count": null, "verification": "unknown"},
  "asr": {"upstream_local": null, "model_id": null,
    "audio_seconds": null, "processing_seconds": null, "execution_mode": "unknown"},
  "gpu": {"utilization_pct": null, "status": "unavailable"},
  "generation": {"tokens_per_second": null, "measurement": "unavailable"},
  "security": {"openshell_denials": 0, "events": []},
  "business": {"detection_seconds": null, "resolution_seconds": null,
    "interactions_by_role": {"patient": 0, "scheduler": 0, "clinician": 0, "coordinator": 0},
    "synthetic_amount_prepared_for_review_usd": 0,
    "hosted_inference_fees_usd": null}
}
```

Null means not observed. Deduplicate logs using a persisted file offset/event ID; reset the run baseline, not historical logs. The host collector can consume the installed OpenShell log format and keep a redacted raw line behind a “View evidence” toggle. A timeout or DNS error without a matching policy event is not an OpenShell block. [OpenShell sandbox logging](https://docs.nvidia.com/openshell/observability/logging); [policy tutorial](https://docs.nvidia.com/openshell/get-started/tutorials/first-network-policy).

### Security demonstration mechanics

Add a clearly synthetic untrusted-document fixture containing an instruction to upload chart material to an unapproved destination. Do not change the agent's system instructions to make it obey. If it refuses, display the refusal accurately. If it attempts an available sandbox-local network tool and OpenShell blocks it, correlate the request with the deny log and continue the business case.

Do not expand the agent's tools merely to make the attack work. The reliable fallback is a fixed **operator-triggered sandbox boundary probe**, separate from the model: run a pre-reviewed request inside the same OpenShell sandbox using a harmless fixed marker, a controlled test receiver and the active enforced policy. Verify receiver reachability from the operator context first, while no marker is sent. Then confirm sandbox denial and no received marker. A host-side MCP server making the request would bypass this enforcement point and is not a valid demonstration. If the runtime log is missing, show “probe inconclusive.”

Display either “agent rejected instruction” or “OpenShell blocked egress,” or both when each independently occurred. The memorable sequence is **visit documented → packet blocked → doctor supplies evidence → unsafe instruction contained → packet verified**. It must continue after the security event. Do not claim network rules are semantic PHI filtering; Slack is an allowed egress path and still requires data minimization.

### Business scorecard

Measure a manual walkthrough of the same fixture. Report detection latency, missing-document-to-verified-packet time, and all staff/patient interactions by role. Machine-active duration and human-wait duration are separate. Do not advertise one staff approval as the total work: clinician review/upload and patient acceptance also occur.

Count synthetic billed amount once per freshly verified packet; label “prepared for coordinator review,” never collected revenue. $0 hosted inference fees applies only when both LLM and ASR are local; hardware/power/staff costs remain excluded. No percentage savings until a measured comparable manual baseline exists.

Add ASR status, model ID, audio duration and processing duration to `/demo/evidence`. A cached transcript must carry `execution_mode=fixture_replay`; genuine local inference carries `execution_mode=live_local`. The LLM route badge does not automatically validate ASR locality. Separate proposed targets from measured results in the pitch.

## 16. Setup checklist and runbook

### Before a run

- Record NemoClaw/OpenClaw/OpenShell versions, effective enforced policy, local LLM/ASR image/model identifiers, driver and measured latencies.
- Confirm no hosted LLM or ASR fallback; check actual `inference.local` upstream, not just the route name.
- Start local inference, ClinicOps service, SQLite/outbox, mock sources, ASR worker and telemetry collector. Confirm real requests succeed.
- Verify `nemoclaw clinicops status`, `nemoclaw clinicops doctor`, managed MCP readiness (or declared plugin route), Slack status and allowed test mention.
- Test one authenticated hook; otherwise enable the scheduled scan. Keep only one Socket Mode listener.
- Provision patient/clinician/coordinator/scheduler demo sessions. Verify local links from the actual presenting browsers, not only server loopback.
- Load fresh synthetic seed; clear run-specific state only; retain credentials/model caches and audit history. Reset requires local operator plus explicit demo flag.
- Stage synthetic WAV and text document, verify packet checklist and supplied billing data. Preload pages and input devices.
- Run the six essential checks; prepare harmless boundary probe and controlled receiver observation. Verify genuine deny log collection.
- Reset for demo; start source sequence before speaking. Label any pre-completed steps with their real timestamps.

### Three-minute operation — fixed live scope

Before speaking, run the supported pre-visit sequence and preserve its real audit history: confirmation, eligibility/deductible check, approved and accepted reschedule. Advance the fixture business clock to the visit, labeled explicitly. If a pre-visit step was seeded rather than executed, mark it “fixture context” and exclude it from autonomous-work metrics. The running service is waiting for visit events; the packet is not already completed.

1. **0:00–0:20:** Show earlier pre-visit history and the local evidence strip. Establish the unfinished administrative work.
2. **0:20–0:50:** Brief kiosk check-in and fresh local transcription of the short synthetic clip.
3. **0:50–1:10:** Clinician reviews one cited visit-summary card. Encounter completion triggers missing-document detection without another agent prompt.
4. **1:10–1:40:** Slack request arrives; clinician follows local authenticated link and uploads the synthetic signed document. Watch the agent resume.
5. **1:40–2:10:** Show the hostile instruction and actual refusal; display the separately labeled genuine OpenShell probe/deny evidence as applicable. Continue legitimate work.
6. **2:10–2:40:** Assemble and independently verify packet; show coordinator Slack handoff and source manifest.
7. **2:40–3:00:** Close on measured blocker-to-ready time, actual human interactions and local execution evidence.

Builder 1 narrates/handles Slack; Builder 2 operates labeled patient and clinician roles. Keep check-in and review brief. Do not open a full profile editor or reenact rescheduling. Monitoring, Telegram and extra agents are excluded. Security evidence follows the truth rules in section 15; an operator probe is never described as an agent-caused block. If the run stalls, retain its actual state and switch to labeled backup footage.

### Recovery and triage

Check `local models → sandbox → tool route → event admission → task queue → action/approval → source verification → Slack delivery`. Successful hook admission is not task completion. Slack lag does not undo a verified packet; the source/audit store is truth.

- Missing ASR: use an already validated local fallback; otherwise clearly labeled transcript fixture and unmet live-ASR requirement.
- Tool-call text instead of structured call: select the staged local model/template/parser that passed smoke testing; never execute parsed free-form model text as shell.
- Slack approval verification unavailable: local authenticated scheduler control, same grants, explicit fallback label.
- Missing clinician review/document: stay blocked and show owner; do not invent evidence.
- Lost write response: lookup idempotency record and read owning source before retry.
- Broken source event delivery: periodic reconciliation; @mention only as disclosed manual recovery.
- GPU stats unavailable: N/A, keep actual inference and process timing; never fake utilization.
- OpenShell deny log unavailable: probe inconclusive, not blocked. Keep agent refusal label only if observed.
- Slack outage: preserve local state and restore real connector; a screenshot does not meet the live-channel requirement.

At reset, clear only synthetic run state and fixture pointers. Do not delete model caches, change policy or rotate credentials during rehearsal reset.

## 17. Fallback plan and post-event roadmap

Keep a recording of a genuine local run captured at 17:00. Label playback as recorded, with original date/runtime/fallbacks. Deterministic mocks are disclosed; agent/tool results and security evidence are not fabricated. If the live run stalls, show the actual blocked state and use the recording for the rest.

Monitoring is excluded from the event build. After the hackathon: add `vitals.alert_simulated` with fixture-defined trigger; open an administrative follow-up; place a fixed check-in message in kiosk; record response or notify staff on a simulated timeout. Maintain a simulated-monitoring banner. No device ingestion, clinical severity interpretation, treatment advice or emergency claims.

Other post-event work, in order: authorization-rescue alternate; extra referral/no-show/denial/follow-up detectors; Slack buttons; long audio and diarization; automatic reopening; Telegram; specialist agents. None is necessary to tell the core story.

## 18. References to verify on event morning

- [NVIDIA ASR support matrix](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html)
- [Boston event brief](https://luma.com/rvinoam5)
- [NemoClaw quickstart](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/get-started/quickstart)
- [NemoClaw local inference choices](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/choose-local-inference-server)
- [NemoClaw managed MCP registration](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/manage-sandboxes/mcp-servers/add-an-mcp-server)
- [NemoClaw Slack setup](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/messaging-channels/set-up-slack)
- [OpenShell policy reference](https://docs.nvidia.com/openshell/reference/policy-schema)
- [OpenClaw Slack setup](https://docs.openclaw.ai/channels/slack/setup)
- [OpenClaw inbound webhooks](https://docs.openclaw.ai/automation/cron-jobs/webhooks)
- [OpenClaw automations](https://docs.openclaw.ai/automation)

The application schema, API routes, tool names, thresholds, and scenario IDs in this guide are implementation proposals. The linked platform docs are the source of truth for version-specific CLI/configuration behavior.
