# FixFlow

A small maintenance request manager for hostels and PGs: resident complaint → reviewed structured ticket → SQLite → manager assignment and status history.

Python, Streamlit, Pydantic, Gemma through Ollama, SQLite and optional Sentry. Extraction asks for reported symptoms, forbids invented costs and diagnoses, and requires human review before saving.

## Local setup

Use Python 3.11 or newer. The local verification environment uses 3.11; the Render Blueprint pins 3.11.17. See [VERIFICATION.md](VERIFICATION.md) for the actual commands and evidence from this workspace.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On Linux/macOS use `source .venv/bin/activate` and `cp .env.example .env`.
Set a long, unique `MANAGER_PASSWORD` in `.env`. Do not commit `.env`. Missing password disables all dashboard access. `PUBLIC_DEMO=true` adds a synthetic-data notice; the manager password is still required.

```powershell
python -m fixflow.seed
streamlit run app.py
```

The default mode is **Demo**, prominently labeled as deterministic sample extraction. It does not call a model. Open the local URL printed by Streamlit (normally `http://localhost:8501`). Data is stored in `data/fixflow.db`. Tables initialize automatically; seeding creates six fictional requests with status New and is safe to repeat without resetting manager updates. The authenticated dashboard also has an explicit synthetic seed action.

### Source layout

| File | Responsibility |
| --- | --- |
| `app.py` | Complaint review/save, manager authentication, dashboard and ticket details |
| `fixflow/models.py`, `fixflow/extraction.py` | Validated schema, Ollama extraction, missing information and labeled demo mode |
| `fixflow/database.py` | SQLite records/history, status transitions, duplicate flags and metrics |
| `fixflow/monitoring.py` | Optional allowlisted Sentry events and traces |
| `fixflow/seed.py`, `fixflow/benchmark.py` | Synthetic data and extraction timing commands |
| `tests/` | Core behavior and Streamlit workflow regression checks |

### Gemma / Ollama setup

Install [Ollama](https://ollama.com/download), start its service, and run:

```text
ollama pull gemma3:1b
ollama serve
```

If the desktop app already runs the service, do not start a second server. Set `FIXFLOW_MODE=ollama` in `.env` or select **Gemma via Ollama** in the form. Configuration:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_MODEL` | `gemma3:1b` | Replaceable model name |
| `OLLAMA_URL` | `http://localhost:11434` | Trusted Ollama service base URL |
| `OLLAMA_TIMEOUT` | `60` | Positive, finite read timeout in seconds; connect timeout is 5 seconds |
| `FIXFLOW_DB` | `data/fixflow.db` | Durable SQLite file path |
| `FIXFLOW_MODE` | `demo` | Default extraction selection |

[`gemma3:1b`](https://ollama.com/library/gemma3:1b) is a text model chosen for its small footprint. Its currently listed quantized download is approximately 806 MB and requires Ollama 0.6 or later. Allow additional memory for runtime and context; an 8 GB RAM machine is a practical starting point, **not a measured minimum**. CPU inference is possible; no GPU is required by this app. Actual memory use and latency depend on hardware and context. Review the [Gemma Terms of Use](https://ai.google.dev/gemma/terms), including use restrictions and redistribution obligations; open weights do not mean an unrestricted software license.

The app sends a Pydantic JSON schema through Ollama's [`format` parameter](https://docs.ollama.com/capabilities/structured-outputs), then validates the response locally. Use a local or self-hosted Ollama server: Ollama's documentation currently says its cloud service does not support structured outputs. Gemma generates category, symptom summary, suggested priority, missing information and follow-up questions. Form location fields remain authoritative: the app replaces model-supplied locations with exactly what the resident entered and adds deterministic questions for blank location fields. This intentionally asks residents to fill the location form rather than infer room details from prose.

No failed inference is automatically converted to demo output. A clear error explains that no ticket was saved. Explicitly select Demo to test without Ollama. AI suggestions can hallucinate or misclassify even when structurally valid. Review every result. Urgent keywords and model-suggested urgency flag prompt human review; these checks cannot certify safety and can produce false positives/negatives.

### Measured latency

Every successful extraction displays and stores `latency_ms`, model name and source. Ollama is not installed in the build environment, so **no real Gemma latency or hardware benchmark is claimed**. Run the provided measurement command on your inference host:

```text
python -m fixflow.benchmark --mode ollama --runs 5
python -m fixflow.benchmark --mode demo --runs 20
```

It uses a fixed synthetic complaint and prints first-run latency separately from subsequent runs. The first request may include model loading; it is not necessarily a cold start if the model was already loaded. These are application wall-clock measurements, not model quality evaluation. Demo timing does not predict AI latency. Record CPU/GPU, RAM, OS, `ollama --version`, `ollama show gemma3:1b`, and the command output alongside a real measurement. No model weights are bundled with FixFlow.

## Demo workflow

1. Open **New Request**, select Demo, enter “The bathroom tap keeps dripping all night.” Add Cedar, floor 1, Room 103.
2. Click **Review request**. Inspect sample category, priority and symptom summary. Check the review box and save.
3. Leave the room blank on another review to see missing-information warnings and follow-up questions. Answer by editing the form or description and click Review again. You can also save a ticket with explicitly flagged missing information.
4. Submit the same complaint at the same full location to see a possible-duplicate warning. Requests are flagged, never automatically merged or discarded.
5. Open **Manager Dashboard**, sign in, filter tickets, and select a ticket to see details. Assign Demo Staff, move New → Assigned → In progress → Resolved → Closed. Reopening clears the previous resolution timestamp.
6. Add synthetic tickets to exercise category filters and urgent-review warnings. Counts include sample tickets. Resolution averages appear after at least one ticket is resolved; the sample size is shown.

Only the authenticated dashboard exposes full records, history, and duplicate IDs. Residents see their own reviewed draft and a save receipt. The receipt ID does not grant ticket access. Saving sends the displayed reviewed version; form edits require another Review click. A unique submission key prevents duplicate saves from retries/double clicks.

## Testing

```text
python -m pytest -q
```

Tests use temporary SQLite files, deterministic extraction and mocked Ollama responses. Streamlit AppTest exercises submission, authentication and management. They do **not** verify real Ollama inference, Sentry delivery, browser appearance or Render deployment.

`requirements.txt` sets Streamlit 1.65 as the minimum for the tested UI APIs and Sentry SDK 2.72 for its explicit `data_collection` privacy controls. Other dependencies also have bounded versions; [VERIFICATION.md](VERIFICATION.md) records the installed versions used for testing.

## Sentry and privacy

Set `SENTRY_DSN`, `SENTRY_ENVIRONMENT` and `SENTRY_TRACES_SAMPLE_RATE` (a fraction from 0 to 1) to enable explicit traces for extraction and database operations. Monitoring is optional; leave the DSN empty to disable it. Invalid monitoring settings show a warning while ticket management remains available. Monitoring failures do not interrupt ticket operations.

The [Python SDK configuration](https://getsentry.github.io/sentry-python/api.html) explicitly disables default/auto integrations, automatic data collection, request bodies, local variables, source context, breadcrumbs, sessions, logs and profiling. `before_send` and `before_send_transaction` allow only generic failure names and transaction timing/trace identifiers. Model prompts, response content, SQL statements, resident names, locations, staff names, credentials, exception details and stack frames are excluded. Tests inspect actual SDK envelopes with an in-memory transport; actual Sentry delivery requires your account and has not been verified here. Existing infrastructure/access logs and your inference service must be reviewed separately for their privacy settings.

## Render deployment

`render.yaml` prepares a single paid Python web service with a persistent disk mounted at `/var/data`; `FIXFLOW_DB` points inside it. [Render free services have ephemeral filesystems and cannot attach persistent disks](https://render.com/docs/free), so they are unsuitable for these SQLite production records. Read [persistent disk constraints](https://render.com/docs/disks): only files under the mount survive, the disk is available at runtime, and the service must use one instance. Keep SQLite and its WAL files together and expect deployment downtime. Do not seed in the build command or horizontally scale this architecture. The [Blueprint fields](https://render.com/docs/blueprint-spec) and [fully qualified Python version setting](https://render.com/docs/python-version) follow Render's documentation; the configuration has not been deployed.

1. Push the source to your repository and create a Render Blueprint from `render.yaml`.
2. Confirm the paid service plan and disk cost in Render before provisioning. Set a unique `MANAGER_PASSWORD`, keep `PUBLIC_DEMO=false`, and optionally configure Sentry. When the Blueprint asks for `OLLAMA_URL`, use `http://localhost:11434` for Demo only, or supply your protected inference host. Leave `SENTRY_DSN` empty if monitoring is not configured.
3. The Blueprint starts in Demo mode without seed records. For a public synthetic demo, use fictional data only, optionally set `PUBLIC_DEMO=true`, then sign in and click **Add synthetic demo tickets** after deployment. For real resident use, first provide a secure reachable self-hosted Ollama server and switch `FIXFLOW_MODE=ollama`.
4. Render's app container does not include Ollama, model weights or guaranteed inference hardware. `localhost:11434` on Render is not your laptop. Do not expose an unauthenticated Ollama port to the internet. Use a private, network-restricted connection or a trusted HTTPS proxy that enforces network access controls; this MVP has no provider-specific authentication adapter. A local setup is the supported complete AI path until that infrastructure exists.
5. Check `/_stcore/health`, authenticate to the dashboard, create a synthetic ticket, restart the service and verify persistence before storing real data. No deployment has been performed or verified here.
6. Make backups using SQLite's online backup API (for example a secured maintenance script using `sqlite3.Connection.backup`), protect backups like the database, and test restoration. Do not copy only the main file while writes are active.

## Limits and operating scope

- This MVP uses a shared manager password and in-memory Streamlit session access, with no individual accounts, resident ticket lookup, tenant isolation or audit identities. Manager authentication is required in demo mode too. Restrict it to one property and trusted users. Public real-data deployments need proper authentication, rate limiting and operational review; the current login has no brute-force protection.
- The complaint form is open. It has length validation but no abuse throttling. Do not run an unprotected public inference endpoint with unrestricted expensive submissions.
- Database fields are parameterized and validated, but SQLite files and backups are not encrypted by this app. Use protected storage and access permissions. There is no retention/deletion workflow yet.
- Duplicate detection requires all location fields, case-insensitive matching and simple lexical similarity. It misses paraphrases and incomplete locations; flags are suggestions.
- Category/priority and free-text AI fields require human review. The application cannot verify that all generated text is factually supported. No automatic repair decision follows extraction.
- Concurrent writes are serialized by SQLite. The storage layer rejects updates carrying a stale status or version timestamp; the manager UI supplies both values. Concurrent browser-session conflict handling was not separately exercised. Small-property workloads are the intended scope.
- Status history records status/staff changes, not proof that maintenance was completed. Resolution is explicitly set by a manager.
- Dependencies have bounded version ranges, not a reproducible lock file. Tests establish compatibility with the installed versions documented in `VERIFICATION.md`.
- No partner qualification or challenge submission is claimed.
