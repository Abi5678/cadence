# Cadence — official UI handoff

Approved UI, October 3, 2026. Source: `3721b5e5166580906f75997ffd2f93ab8f778006`.

## Run the UI

From the repository root:

```sh
python3 serve.py 8091
```

Open **http://localhost:8091/web/**. No npm installation or API keys are needed. Use localhost rather than opening the HTML file directly so file hashing works. Stop with Ctrl+C.

The coordinator workspace is the main entry point. Use its role selector to open Clinician or Patient. Each view has its own named Weaver chat. The operator/demo view has been removed.

The approved woven wordmark, Iris & Champagne palette, interactive ribbons, and animated Weaver are included. Weaver greets on view/chat entry, weaves knots during work and document processing, and celebrates completed tasks and final chat answers. Reduced-motion preferences are supported.

## Backend handoff

Read [docs/OFFICIAL-UI.md](docs/OFFICIAL-UI.md) for the current frontend integration boundaries and animation lifecycle. It supersedes older UI descriptions in the original handoff documents.

Abi's existing `service/`, `ops/`, `scripts/`, `skills/`, and live `web/clinic.*` console are preserved. See [docs/GB10-HERMES-PLAN.md](docs/GB10-HERMES-PLAN.md) for the backend setup. The service defaults to port 8090; port 8080 is reserved for the OpenShell gateway.

The approved UI still uses synthetic local adapters. Its agent execution, chat answers and Slack intake are demos; connecting it to the existing service is the next integration step. Local file processing retains metadata/hashes, not uploaded document bytes. Use synthetic data.

## Validate

```sh
node tests/operations.test.cjs
node tests/coordinator-view.test.cjs
node tests/state.test.cjs
node tests/weave.test.cjs
node tests/ribbon-motion.test.cjs
node tests/weaver-lifecycle.test.cjs
```
