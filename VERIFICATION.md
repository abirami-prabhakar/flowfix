# Verification - 15 October 2026

The workspace already contained a substantial Streamlit MVP: complaint extraction, SQLite persistence, manager workflows, synthetic seeding, monitoring, tests and deployment documentation. These components were inspected and reused. No Git repository was present. This audit strengthens validation, failure handling, privacy, review consent and concurrent manager edits; it does not replace the application architecture.

## Environment and commands

Verification used the existing workspace virtual environment, `.venv311`, with Python 3.11.9 on Windows. No real credentials were needed. Direct dependency versions in that environment:

| Dependency | Installed version |
| --- | --- |
| Streamlit | 1.65.0 |
| Pydantic | 2.14.1 |
| Requests | 2.34.2 |
| python-dotenv | 1.2.4 |
| sentry-sdk | 2.72.0 |
| pytest | 9.1.1 |

- `.venv311/Scripts/python.exe -m pytest -q`: baseline **26 passed in 2.49 seconds**; final **101 passed in 8.13 seconds**, with no warnings. Tests use isolated temporary SQLite files.
- `.venv311/Scripts/python.exe -m compileall -q app.py fixflow tests`: passed.
- `uv pip check --python .venv311/Scripts/python.exe`: 48 packages checked; installed dependencies compatible.
- `.venv311/Scripts/python.exe -m fixflow.seed`: six synthetic tickets ready. Tests verify repeated seeding preserves manager changes and creates no duplicate seed records.
- SQLite inspection: `PRAGMA integrity_check` returned `ok`; the default database contained six seeded synthetic tickets. A synthetic extraction with missing floor and room/common area produced two follow-up questions.
- `.venv311/Scripts/python.exe -m fixflow.benchmark --mode demo --runs 20`: first request **0.309 ms**, mean **0.060 ms**, median **0.043 ms**, subsequent mean **0.047 ms**. This measures deterministic sample extraction, not Gemma inference.
- `.venv311/Scripts/python.exe -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false`: server started. HTTP checks returned **200** for `/` and **200**, body `ok`, for `/_stcore/health`.
- UTF-8 and environment/configuration checks passed: `.env.example` has empty credentials; Render's database path is inside `/var/data`; the Blueprint explicitly uses one service instance and pins Python 3.11.17. This is a static consistency check, not a Render API/schema validation or deployment test.

## Scope of evidence

Core tests cover output and input validation, authoritative form locations, missing information and follow-up questions, urgency cues, duplicate matching, valid/invalid status transitions, resolution timing, initial/history persistence, submission retry behavior, corrupt records, storage errors, stale staff/status edits, seed preservation, inference configuration, timeouts, HTTP errors, malformed or incomplete model responses and monitoring failures.

Streamlit AppTest verifies resident review/save, fresh consent for a new review, follow-up answers, failed-inference handling, password protection including public demo mode, assignment and status updates. Resident/model text is displayed literally, preventing Markdown image requests from complaint content.

The actual Sentry SDK was exercised with an in-memory transport. Captured event and transaction envelopes excluded injected resident content, user fields, extra data, tags and child spans. Tests also verify telemetry initialization, reporting and transaction failures cannot prevent ticket operations. No Sentry network delivery or account configuration was verified.

An isolated headless Chrome session rendered the request form and reviewed sample draft at **1440 x 1100** and **390 x 1100**. Screenshots were visually inspected; neither viewport had horizontal overflow, and mobile columns stacked. Local review artifacts are in `.cache/browser-preview/` (`desktop.png`, `draft-desktop.png`, `mobile.png`); they are not application assets. Dashboard browser appearance was not visually checked; its workflow is covered by AppTest.

## Unverified integrations and limitations

Ollama was not installed or available on the host: no executable was found and `http://127.0.0.1:11434` was unreachable. Therefore **real Gemma extraction, quality, hardware requirements and latency were not measured**. Model tests use mocked Ollama responses; Demo mode is explicitly labeled and does not call a model. The README includes installation and a real inference benchmark command.

No Render service, persistent disk or partner submission was provisioned. The Blueprint follows [Render's documented fields](https://render.com/docs/blueprint-spec) and [persistent-disk constraints](https://render.com/docs/disks); the targeted Python 3.11.17 runtime was not executed locally. Render deployment requires paid persistent storage and a separately protected, reachable Ollama server for real inference. A shared manager password and lack of abuse throttling limit this MVP to a controlled, single-property deployment. No partner qualification is claimed.

## Next required action

Copy `.env.example` to `.env`, set a long unique `MANAGER_PASSWORD`, install Ollama and run `ollama pull gemma3:1b`. Start the local app with `.venv311/Scripts/python.exe -m streamlit run app.py`, select **Gemma via Ollama**, and review a synthetic extraction on your own hardware. Measure it with `.venv311/Scripts/python.exe -m fixflow.benchmark --mode ollama --runs 5`. Establish protected inference hosting, persistent storage and tested backups before using real production records.
