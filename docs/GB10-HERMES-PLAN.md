# Cadence on GB10 with Hermes + OpenShell

**Event:** BuilderBase × Dell × NVIDIA, Hult Cambridge. **Sat Oct 3, 2026.** Doors 09:00, build 10:30–18:00, code freeze 18:00. One Dell Pro Max GB10 per team.

**Pinned stack:** checked Oct 2 against NemoClaw source.

| Component | Version |
|---|---|
| NemoClaw | lkg `v0.0.124` (commit `6f3cced`) |
| OpenShell | `0.0.116` (the exact version that NemoClaw lkg requires) |
| Hermes sandbox | `hermes-sandbox:v0.0.124` |
| Inference | vLLM from NVIDIA's NGC container, serving `nvidia/Qwen3.6-35B-A3B-NVFP4` |

Large items (models, container images, installers) ship on an external drive, so venue WiFi is only needed for Slack and small onboarding fetches.

---

## 0. Decide tonight

1. **Rules conflict.** The event brief says:
   > "NemoClaw + OpenClaw + OpenShell on the Dell Pro Max with GB10. All inference local."

   The agent must connect through an OpenClaw connector (Slack, Discord or Telegram). Hermes (`NEMOCLAW_AGENT=hermes`, CLI `nemohermes`) *replaces* OpenClaw, and NemoClaw marks Hermes support as experimental.
   - Ask the organizers whether Hermes qualifies.
   - The drive carries both sandbox images. If they say OpenClaw only, switching means re-onboarding (~20 min), with no new downloads. Everything else in this plan is unchanged: the vLLM server, the Cadence tools service, its MCP endpoint and the Slack app.
2. **Scope.** Build the BUILD-HANDOFF "Saturday acceptance path", one task type end to end:
   - It does *not* need audio transcription, the original three cases or the v1.3 encounter journey.
   - If you want the v1.3 journey instead, add the `asr` extra when staging.
3. **Drive.** Use a USB-C NVMe SSD of at least 128 GB (256 GB if you take all extras), formatted **ExFAT**:
   - macOS can write it, and Ubuntu reads it without extra drivers.
   - It holds files over 4 GB.
   - It has no symlinks, which the staging script handles.

## 1. Architecture

```
Dell Pro Max GB10 (DGX OS / Ubuntu 24.04, aarch64, 128 GB unified memory)
│
├─ vLLM  (NGC image, host network)  :8000  →  nvidia/Qwen3.6-35B-A3B-NVFP4
│
├─ OpenShell 0.0.116 gateway
│    └─ sandbox "cadence"  =  hermes-sandbox v0.0.124
│          ├─ model calls  → https://inference.local → host vLLM :8000
│          ├─ Slack (Socket Mode, outbound only) ↔ doctor DMs / threads
│          └─ MCP client   → https://<docker0 IP, normally 172.17.0.1>:8443/mcp
│
└─ Cadence service (FastAPI + SQLite, uv venv, on the host)
     ├─ https://<docker0 IP>:8443/mcp   tools for Hermes (bearer token)
     ├─ http://127.0.0.1:8080           web/ UI + REST + server-sent events (SSE)
     ├─ Slack Web API (bot token, files only): fetch doctor uploads by file id
     └─ local-system bridge: writes approved outputs to ~/cadence-outbox + receipt
```

**Acceptance path.** These steps map to BUILD-HANDOFF:
1. The coordinator assigns a task in the UI (`POST /api/tasks`), and SQLite issues a real task id.
2. The service starts Hermes with a message to its OpenAI-compatible API. NemoClaw forwards that API to host loopback on 8642 (or the next free port up to 8652); the bearer token comes from `nemohermes cadence gateway-token --quiet`. Hermes calls `get_task`, then reports progress with `post_event` (planning, evidence, missing info).
3. Hermes calls `request_doctor_input`, then asks the doctor in a Slack thread.
4. The doctor replies or uploads in Slack. Hermes calls `attach_slack_file(task_id, file_id)`.
   - The **service** downloads the file with the bot token, checks its type and size, and computes its SHA-256.
   - The file contents never enter the model context, so the agent cannot read anything in an upload as instructions.
5. Hermes calls `propose_output`, and the coordinator reviews and approves it in the UI.
6. The service writes the output to the outbox and records a receipt. It then tells Hermes, which replies in the Slack thread, and the UI history updates over SSE.

**Ownership.** The service owns state and enforcement: task versions, approvals, the four delivery states (prepared, approved, attempted, confirmed) and receipts. Hermes only proposes changes and sends messages. Keep the BUILD-HANDOFF clinical safeguards and use synthetic data only.

**MCP tools (minimum set):**

| Tool | Purpose |
|---|---|
| `get_task` | Read the task brief |
| `post_event` | Record a progress event |
| `request_doctor_input` | Open a request to the doctor |
| `attach_slack_file` | Fetch and verify a doctor's upload |
| `propose_output` | Submit an output for coordinator review |
| `get_delivery_status` | Read the delivery receipt |

**Out of scope Saturday:**
- durable schedules (the UI's local scheduler stays labeled as a demo)
- authentication beyond a single demo session
- PDF parsing (accept TXT and PDF by type, size and hash only)

**UI change.** Add a service-backed adapter beside `web/operations-model.js` that keeps the same subscribe/state interface (BUILD-HANDOFF item 2), and point `index.html` at it.

**Hermes MCP endpoint rules** (from NemoClaw `add-mcp-server.mdx`):
- HTTPS on a stable private address in the 10.x, 172.16–31.x or 192.168.x ranges. Loopback and `host.openshell.internal` are rejected.
- The certificate must carry the IP address as an "IP SAN" when the URL uses a bare IP.
- The private CA must be passed in `NEMOCLAW_CORPORATE_CA_BUNDLE` **before onboarding**.
- Registration: `nemohermes cadence mcp add cadence --url https://$BR:8443/mcp --env CADENCE_MCP_TOKEN --trusted-private-host $BR`
- The docker0 bridge address (`$BR`, normally 172.17.0.1) is stable and private. It is used because the venue LAN address changes with DHCP or a switch to the hotspot.
- **Unverified until the venue:** that the OpenShell gateway can route to `$BR`. The fallback is in §8.

## 2. Models

| Model | Disk | Role | Why |
|---|---|---|---|
| `nvidia/Qwen3.6-35B-A3B-NVFP4` @`491c2f1` | 23.5 GB | **Primary** | Top NemoClaw GB10 preset (priority 550). Mixture-of-experts with 3B active parameters, so agent loops stay fast. The `qwen3_coder` tool-call parser is qualified for it. |
| `nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8` @`main` | 5.3 GB | Fallback | Same vLLM image. Loads in seconds if the 35B misbehaves. |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` @`0dcd680` | 21.6 GB + 12.7 GB image | Extra `lightning` | NVIDIA-branded option. Needs its own vLLM image. |
| `Qwen/Qwen3.6-27B-FP8` @`e89b16e` | 30.9 GB | Extra `qwen27b` | Dense, so roughly 10 tok/s or less on GB10. Only for quality comparison. |
| `unsloth/Nemotron-3-Nano-30B-A3B-GGUF` Q4_K_XL @`9ad8b36` | 22.8 GB + 1.8 GB image | Extra `llamacpp` | Fallback that does not depend on vLLM. |
| `openai/whisper-large-v3-turbo` | 1.6 GB | Extra `asr` | Only for the v1.3 journey. Whether the NGC image serves audio is unverified. |

**Not chosen:** Hermes-4 70B/36B (dense, ~70 GB on disk) and Nemotron Super 120B (~80 GB). Both are too slow or too large for GB10's ~273 GB/s memory bandwidth. Hermes Agent works with any OpenAI-compatible model.

## 3. Container images (linux/arm64, saved as `docker load` tarballs)

| Image | Size | Use |
|---|---|---|
| `nvcr.io/nvidia/vllm@sha256:92045…59e2` | 9.6 GB download, 27.7 GB unpacked | vLLM, as in the NemoClaw GB10 recipe |
| `ghcr.io/nvidia/nemoclaw/hermes-sandbox:v0.0.124` | 0.65 GB | Hermes sandbox |
| `ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.124` | 0.71 GB | Fallback if Hermes is disallowed |
| `ghcr.io/nvidia/openshell/supervisor:0.0.116` | 0.02 GB | OpenShell |
| `nvidia/cuda:12.8.0-base-ubuntu24.04` | small | GPU smoke test |

**Gotcha: `docker load` may drop registry digests.**
- vLLM is unaffected, because we run it ourselves from a local tag.
- The sandbox is affected. Onboarding resolves it by digest and may re-pull ~0.65 GB.
- Supplying the CA also makes onboarding build the CA into the sandbox image, and that build can fetch from npm.
- **Budget about 1 GB of phone-hotspot data** in case venue WiFi is unusable.

## 4. Software on the drive

| Item | Notes |
|---|---|
| Node `v22.23.3` linux-arm64 | NemoClaw needs ≥22.19 and npm ≥10. Staging it avoids the nvm download at the venue. |
| OpenShell 0.0.116 `.deb` | Plus the three musl/gnu tarballs and their checksum files. The installer skips OpenShell when this version is already present. |
| NemoClaw `v0.0.124` checkout | Saved as `.tgz`, because the repo has symlinks. |
| npm cache | Warmed for linux/arm64. Use it with `npm_config_prefer_offline=true`. |
| `uv` aarch64 static binary | DGX OS may lack `python3-venv`. |
| Python wheels | fastapi, uvicorn[standard], pydantic, httpx, python-multipart, slack_sdk, mcp. Built for CPython 3.12 manylinux aarch64. |
| This repo | Copied to `cadence/` on the drive. |

## 5. Staging the drive (tonight, on the Mac)

The Mac's internal disk has only ~2.6 GB free, so everything streams straight to the drive.

```bash
brew install crane
scripts/stage-drive.sh /Volumes/<DRIVE>                # core, ~45 GB
scripts/stage-drive.sh /Volumes/<DRIVE> llamacpp       # optional extras, rerun-safe
```

- The script refuses to run unless the target is ExFAT.
- It skips finished items, so rerun it after any network drop.
- It ends by writing `SHA256SUMS`.
- If a model download returns 401 or 403, run `hf auth login`. The token stays in `~/.cache/huggingface`, not on the drive.

Layout:

```
<DRIVE>/models/<name>/            hf --local-dir checkouts (+ .complete marker)
<DRIVE>/images/*.tar  IMAGES.txt  crane tarballs and their source refs
<DRIVE>/software/                 node, uv, openshell, nemoclaw tgz, npm-cache/
<DRIVE>/wheels/                   python wheels
<DRIVE>/cadence/                  this repo
<DRIVE>/SHA256SUMS
```

## 6. Tonight checklist

- [ ] Message the organizers. Ask:
  - Is Hermes in place of OpenClaw OK?
  - Can we set up before 10:30?
  - Is NemoClaw already installed on the GB10s?
  - Is there wired Ethernet?
  - Is prepared code allowed?
- [ ] Format the drive as ExFAT, run the staging script, and check the summary sizes.
- [ ] Create the Slack app in a **test workspace**. Use api.slack.com/apps → *From manifest*:
  ```yaml
  display_information: {name: Cadence}
  features:
    bot_user: {display_name: Cadence, always_online: true}
    app_home: {messages_tab_enabled: true, messages_tab_read_only_enabled: false}
  oauth_config:
    scopes:
      bot: [app_mentions:read, channels:history, channels:read, chat:write, files:read,
            groups:history, im:history, im:read, im:write, users:read]
  settings:
    event_subscriptions:
      bot_events: [app_mention, message.channels, message.groups, message.im]
    socket_mode_enabled: true
  ```
  - Install the app to get the bot token (`xoxb-`).
  - Create an app-level token with `connections:write` (`xapp-`).
  - Note the member IDs of the "doctor" and coordinator accounts for `SLACK_ALLOWED_USERS`.
  - Keep both tokens in a password manager only: not on the drive, not in git, not in screenshots.
  - Check the scope list against Hermes's Slack setup page once onboarding shows it.
- [ ] Charge your phone for the hotspot. Pack a USB-C to USB-A adapter and an Ethernet dongle.
- [ ] Write synthetic doctor and task fixtures only.

## 7. Day-of runbook

Set up 09:15–10:30 if allowed; otherwise use the first hour of the build.

**Order matters.** Start vLLM first. Its first load (weights plus kernel compilation) can take 5–20 minutes, and the recipe allows up to 30. Install everything else while it loads.

```bash
# 7.1 Health
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi   # after 7.3
df -h ~                                         # need ~100 GB free

# 7.2 Copy from the drive and verify (sha256sum can run in the background)
rsync -a --info=progress2 --exclude .hf-cache --exclude tmp /media/$USER/<DRIVE>/ ~/stage/
(cd ~/stage && sha256sum -c SHA256SUMS --quiet && echo SUMS-OK) &

# 7.3 Load images; tag vLLM locally
for t in ~/stage/images/*.tar; do
  id=$(docker load -i "$t" | awk '/Loaded image/{print $NF}'); echo "$t -> $id"
  case "$t" in */vllm-ngc.tar) docker tag "$id" cadence/vllm:ngc ;; esac
done
docker image inspect ghcr.io/nvidia/nemoclaw/hermes-sandbox:v0.0.124 --format '{{.RepoDigests}}'  # empty = may re-pull

# 7.4 Keep local services off the venue LAN (does not persist across reboot)
for i in $(ls /sys/class/net | grep -E '^(en|eth|wl)'); do
  for p in 8000 8080 8443 8642; do sudo iptables -I INPUT -i "$i" -p tcp --dport "$p" -j DROP; done
done

# 7.5 vLLM: the NemoClaw GB10 recipe flags (vllm.qwen3-6-35b-a3b-nvfp4.spark-single.v1)
mkdir -p ~/.cache/cadence-vllm
docker run -d --name cadence-vllm --restart unless-stopped --gpus all \
  --network host --ipc host --shm-size 64g --ulimit memlock=-1 --ulimit stack=67108864 \
  -e HF_HUB_OFFLINE=1 -v ~/.cache/cadence-vllm:/root/.cache \
  -v ~/stage/models/qwen3.6-35b-a3b-nvfp4:/models/qwen:ro \
  cadence/vllm:ngc vllm serve /models/qwen \
  --served-model-name nvidia/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000 \
  --max-model-len 262144 --gpu-memory-utilization 0.4 --dtype auto --quantization modelopt \
  --kv-cache-dtype fp8 --attention-backend flashinfer --moe-backend marlin \
  --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --async-scheduling \
  --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 --load-format fastsafetensors
docker logs -f cadence-vllm        # wait for "Application startup complete"
curl -s localhost:8000/v1/models

# 7.6 Private CA + MCP certificate (openssl commands tested on the Mac)
BR=$(ip -4 -o addr show docker0 | awk '{print $4}' | cut -d/ -f1); echo "$BR"   # normally 172.17.0.1
mkdir -p ~/cadence-tls && cd ~/cadence-tls
openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=Cadence Hackathon CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout ca-key.pem -out ca.pem
openssl req -newkey rsa:2048 -nodes -subj "/CN=$BR" -keyout server-key.pem -out server.csr
printf 'subjectAltName=IP:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n' "$BR" > ext.cnf
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 7 -extfile ext.cnf -out server.pem
openssl verify -x509_strict -CAfile ca.pem server.pem && chmod 644 ca.pem && chmod 600 *-key.pem

# 7.7 Node + OpenShell + NemoClaw (Hermes), onboarded against the running vLLM
sudo tar -xJf ~/stage/software/node-v22.23.3-linux-arm64.tar.xz -C /opt
export PATH=/opt/node-v22.23.3-linux-arm64/bin:$PATH
sudo apt-get install -y ~/stage/software/openshell_0.0.116-1_arm64.deb && openshell --version
mkdir -p ~/src && tar -xzf ~/stage/software/nemoclaw-v0.0.124-6f3cced.tgz -C ~/src
export npm_config_cache=~/stage/software/npm-cache npm_config_prefer_offline=true
export NEMOCLAW_AGENT=hermes NEMOCLAW_PROVIDER=vllm NEMOCLAW_SANDBOX_NAME=cadence \
  NEMOCLAW_CORPORATE_CA_BUNDLE=~/cadence-tls/ca.pem
printf 'Slack bot token: ' >&2; IFS= read -r -s SLACK_BOT_TOKEN; printf '\n' >&2
printf 'Slack app token: ' >&2; IFS= read -r -s SLACK_APP_TOKEN; printf '\n' >&2
export SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_ALLOWED_USERS=<member ids>
cd ~/src/NemoClaw && bash install.sh --non-interactive --yes-i-accept-third-party-software
#   if onboarding fails after install: nemohermes onboard --resume
nemohermes cadence status           # gateway, api and inference checks should pass
unset SLACK_APP_TOKEN               # SLACK_BOT_TOKEN stays only in the Cadence service env

# 7.8 Cadence service
sudo tar -xzf ~/stage/software/uv-aarch64-unknown-linux-gnu.tar.gz -C /usr/local/bin --strip-components 1
cd ~/stage/cadence && uv venv --python 3.12 && \
  uv pip install --no-index --find-links ~/stage/wheels fastapi 'uvicorn[standard]' pydantic httpx python-multipart slack_sdk mcp
#   run: UI/REST on 127.0.0.1:8080; MCP with --ssl-certfile/--ssl-keyfile on $BR:8443

# 7.9 Wire Hermes to Cadence
IFS= read -r -s CADENCE_MCP_TOKEN; export CADENCE_MCP_TOKEN
nemohermes cadence mcp add cadence --url "https://$BR:8443/mcp" --env CADENCE_MCP_TOKEN --trusted-private-host "$BR"
unset CADENCE_MCP_TOKEN
# Hermes API: NemoClaw owns this forward (8642-8652); do not add an `openshell forward` for it
openshell forward list                                # note cadence's API port, normally 8642
HERMES_API_TOKEN=$(nemohermes cadence gateway-token --quiet)   # the Cadence service's env only
curl -fsS -H "Authorization: Bearer $HERMES_API_TOKEN" http://127.0.0.1:8642/v1/models
#   connection refused: nemohermes cadence recover
```

**Build blocks:**

| Time | Work | Done when |
|---|---|---|
| 10:30–13:00 | Service, SQLite and MCP tools; Hermes calls `get_task`/`post_event` | One event from Hermes shows up in SQLite |
| 13:00–15:30 | Slack doctor flow (`request_doctor_input`, `attach_slack_file`); UI adapter + SSE | A doctor upload in Slack turns into verified evidence in the UI |
| 15:30–17:00 | Approval → outbox receipt → Slack thread reply; test cancel, retry, duplicate file and stale source | The full acceptance path runs twice in a row |
| 17:00–18:00 | Freeze features, rehearse a 3-minute demo, record a backup video | Video saved locally and on the drive |

**Memory budget.** vLLM at `0.4` utilization uses about 51 GB of the 128 GB unified memory. Do not start a second large model.
- If NemoClaw warns about headroom, lower `--max-model-len` to 65536 before raising anything else.
- If the 4B model must run alongside the 35B, give it `--gpu-memory-utilization 0.1`.

## 8. Fallbacks

| Failure | Switch to | Cost |
|---|---|---|
| Organizers require OpenClaw | Re-onboard without `NEMOCLAW_AGENT`. Same vLLM, MCP, Slack app and CA (the MCP flow is identical for OpenClaw). | ~20 min |
| 35B will not load or runs out of memory | Same image, `/models/nemotron-3-nano-4b-fp8`. Its parser flags come from the NemoClaw `nemotron-3-nano-4b-fp8` recipe. | ~10 min |
| vLLM image broken | llama.cpp extra (if staged). Onboard with `NEMOCLAW_PROVIDER=llama-cpp-local` or `custom`. | ~20 min |
| MCP not working by 13:00 | Keep Hermes as the Slack conversation agent. The service drives Hermes through :8642 and picks up doctor uploads by polling `conversations.replies` on the request thread with the bot token. | ~1 h |
| Venue WiFi down | Phone hotspot. Only Slack Socket Mode and small onboarding fetches need it. | — |
| Demo-time failure | Play the 17:00 backup video. | — |

## 9. Open risks (verify first at the venue)

1. Whether Hermes is accepted, given the brief requires OpenClaw (§0).
2. Whether `docker load` keeps the sandbox digest. If not, onboarding re-pulls 0.65 GB.
3. Whether onboarding reaches the network for the image catalog, npm and the CA image bake. The npm cache is a best-effort mitigation.
4. Whether the OpenShell gateway can route to `$BR:8443`. This is the MCP path.
5. Whether the venue allows setup before 10:30, and whether NemoClaw comes preinstalled. If it is preinstalled, check `nemoclaw --version` and `openshell --version` against the pins above before reusing it.
