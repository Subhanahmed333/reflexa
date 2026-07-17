from __future__ import annotations

import html
import json
import queue
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import RunEvent, RunStatus
from .orchestrator import Orchestrator, OrchestratorConfig


@dataclass(slots=True)
class RunRecord:
    run_id: str
    status: str = RunStatus.PENDING.value
    events: list[dict[str, object]] = field(default_factory=list)
    summary: str = ""
    publication: dict[str, object] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    last_event_at: str | None = None


@dataclass(slots=True)
class RunStore:
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _records: dict[str, RunRecord] = field(default_factory=dict)
    _subscribers: list[queue.Queue[dict[str, object]]] = field(default_factory=list)

    def create(self, run_id: str) -> None:
        with self._lock:
            self._records[run_id] = RunRecord(run_id=run_id)

    def update(self, event: RunEvent) -> None:
        with self._lock:
            run_id = event.data.get("run_id", "live")
            record = self._records.setdefault(run_id, RunRecord(run_id=run_id))
            payload = event.to_dict()
            record.events.append(payload)
            record.last_event_at = event.timestamp
            if event.kind == "run_started":
                record.status = RunStatus.RUNNING.value
                record.started_at = event.timestamp
            elif event.kind == "run_finished":
                record.status = event.data.get("status", record.status)
                record.summary = event.data.get("summary", record.summary)
                record.finished_at = event.timestamp
            elif event.kind == "artifact_written":
                record.publication = dict(event.data)
            for subscriber in list(self._subscribers):
                try:
                    subscriber.put_nowait(payload)
                except queue.Full:
                    continue

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                run_id: {
                    "run_id": record.run_id,
                    "status": record.status,
                    "summary": record.summary,
                    "publication": dict(record.publication) if record.publication else None,
                    "events": [dict(event) for event in record.events],
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "last_event_at": record.last_event_at,
                }
                for run_id, record in self._records.items()
            }

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        subscriber: queue.Queue[dict[str, object]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, object]]) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)


class DashboardApp:
    def __init__(self, orchestrator: Orchestrator, repo_path: Path, test_command: list[str], max_attempts: int = 3):
        self.orchestrator = orchestrator
        self.repo_path = repo_path
        self.test_command = test_command
        self.max_attempts = max_attempts
        self.store = RunStore()
        self._active_thread: threading.Thread | None = None

    def start_run(self) -> str:
        if self._active_thread and self._active_thread.is_alive():
            raise RuntimeError("A run is already in progress.")
        config = OrchestratorConfig(repo_path=self.repo_path, test_command=self.test_command, max_attempts=self.max_attempts)
        self.store.create(config.run_id)
        self._active_thread = threading.Thread(target=self._execute_run, args=(config,), daemon=True)
        self._active_thread.start()
        return config.run_id

    def current_state(self, selected_run_id: str | None = None) -> dict[str, object]:
        return build_dashboard_state(self.store.snapshot(), selected_run_id=selected_run_id)

    def _execute_run(self, config: OrchestratorConfig) -> None:
        sink = _StoreSink(self.store)
        result = self.orchestrator.run(config, sink=sink)
        record = self.store._records.setdefault(result.run_id, RunRecord(run_id=result.run_id))
        record.status = result.status.value
        record.summary = result.summary
        record.finished_at = record.finished_at or record.last_event_at
        if result.publication:
            record.publication = {
                "mode": result.publication.mode,
                "title": result.publication.title,
                "summary_path": str(result.publication.summary_path) if result.publication.summary_path else None,
                "patch_path": str(result.publication.patch_path) if result.publication.patch_path else None,
                "pull_request_url": result.publication.pull_request_url,
            }


class _StoreSink:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    def emit(self, event: RunEvent) -> None:
        self.store.update(event)


class DashboardHandler(BaseHTTPRequestHandler):
    app: DashboardApp

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._send_html(self._render_index())
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            selected_run_id = parse_qs(parsed.query).get("selected_run_id", [None])[0]
            self._send_json(self.app.current_state(selected_run_id=selected_run_id))
            return
        if parsed.path == "/events":
            self._handle_events()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/start":
            try:
                run_id = self.app.start_run()
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
                return
            self._send_json({"run_id": run_id})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _handle_events(self) -> None:
        subscriber = self.app.store.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.app.store.unsubscribe(subscriber)

    def _send_html(self, content: str) -> None:
        encoded = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _render_index(self) -> str:
        repo = html.escape(str(self.app.repo_path))
        command = html.escape(" ".join(self.app.test_command))
        initial_state = json.dumps(self.app.current_state()).replace("</", "<\\/")
        page = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>Reflexa Dashboard</title>
<style>
:root {{ color-scheme: dark; --bg:#080c16; --panel:rgba(16,22,38,.9); --line:rgba(148,190,255,.15); --text:#eef4ff; --muted:#97a7c7; --accent:#8fd0ff; --good:#72d69a; --bad:#ff8c93; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font-family: Segoe UI, system-ui, sans-serif; color:var(--text); background: radial-gradient(circle at top left, rgba(143,208,255,.14), transparent 25%), linear-gradient(180deg,#0d1322,#070b12 65%); }}
main {{ width:min(1400px, calc(100vw - 24px)); margin:0 auto; padding:24px 0 36px; }}
.hero,.panel,.card,.run-card {{ background:var(--panel); border:1px solid var(--line); box-shadow:0 24px 60px rgba(0,0,0,.3); }}
.hero {{ border-radius:24px; padding:24px; margin-bottom:16px; }}
.hero h1 {{ margin:10px 0 8px; font-size:clamp(2rem,5vw,4rem); line-height:1; letter-spacing:-.05em; }}
.badge {{ display:inline-flex; align-items:center; gap:6px; padding:6px 10px; border-radius:999px; color:var(--accent); background:rgba(143,208,255,.08); text-transform:uppercase; letter-spacing:.08em; font-size:12px; font-weight:700; }}
.meta {{ display:flex; flex-wrap:wrap; gap:10px; color:var(--muted); }}
.actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }} button, select {{ font:inherit; }} button {{ border:0; border-radius:12px; padding:12px 16px; font-weight:800; cursor:pointer; }} .primary {{ background:linear-gradient(135deg,var(--accent),#c9edff); color:#08111d; }} .secondary {{ background:rgba(255,255,255,.05); color:var(--text); border:1px solid rgba(255,255,255,.08); }}
.layout {{ display:grid; grid-template-columns:320px minmax(0,1fr); gap:16px; }}
.sidebar {{ border-radius:24px; padding:16px; align-self:start; }} .runs {{ display:grid; gap:10px; max-height:70vh; overflow:auto; margin-top:12px; }}
.run-card {{ padding:14px; border-radius:18px; text-align:left; color:inherit; background:rgba(255,255,255,.03); cursor:pointer; }} .run-card.active {{ border-color:rgba(143,208,255,.5); background:rgba(143,208,255,.09); }} .run-card h3, .run-card p {{ margin:0; }} .run-card p {{ color:var(--muted); margin-top:6px; }}
.content {{ display:grid; gap:16px; }} .stats {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; }} .stat {{ border-radius:18px; padding:16px; }} .stat .label {{ margin:0 0 8px; color:var(--muted); text-transform:uppercase; font-size:11px; letter-spacing:.12em; }} .stat .value {{ margin:0; font-size:1.7rem; font-weight:800; }}
.grid2 {{ display:grid; grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr); gap:16px; }} .card {{ border-radius:24px; padding:18px; min-width:0; }} h2 {{ margin:0 0 6px; font-size:1.1rem; }} .lead {{ margin:0; color:var(--muted); line-height:1.65; }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }} .chip {{ display:inline-flex; gap:6px; padding:8px 10px; border-radius:999px; background:rgba(255,255,255,.04); color:var(--muted); }}
.diff-status {{ color:var(--accent); background:rgba(143,208,255,.09); }}
.status {{ display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; text-transform:uppercase; font-size:12px; font-weight:800; letter-spacing:.08em; }} .status-running {{ color:#ffd77b; background:rgba(255,215,123,.1); }} .status-succeeded, .status-passed {{ color:var(--good); background:rgba(114,214,154,.1); }} .status-failed {{ color:var(--bad); background:rgba(255,140,147,.1); }} .status-patched {{ color:var(--accent); background:rgba(143,208,255,.1); }} .status-pending {{ color:var(--muted); background:rgba(255,255,255,.05); }}
.attempts, .events {{ display:grid; gap:10px; }} .attempt, .event {{ border-radius:18px; padding:14px; background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.06); }} .attempt-top, .event-top {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; }} .attempt-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }} .mini {{ border-radius:14px; padding:12px; background:rgba(5,9,16,.35); border:1px solid rgba(255,255,255,.05); }} .mini h4 {{ margin:0 0 8px; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.12em; }} pre {{ margin:0; white-space:pre-wrap; word-break:break-word; font:12.5px/1.55 Consolas, monospace; }}
.select {{ min-width:220px; padding:10px 12px; border-radius:12px; background:rgba(255,255,255,.04); color:var(--text); border:1px solid rgba(255,255,255,.08); }} .toolbar {{ display:flex; justify-content:space-between; gap:10px; align-items:center; }} .empty {{ color:var(--muted); padding:14px; border:1px dashed rgba(255,255,255,.12); border-radius:16px; }}
@media (max-width: 1100px) {{ .layout, .grid2, .stats {{ grid-template-columns:1fr; }} .attempt-grid {{ grid-template-columns:1fr; }} }}
@media (prefers-reduced-motion: reduce) {{ *,*::before,*::after {{ animation-duration:1ms !important; transition-duration:1ms !important; }} }}
</style>
</head>
<body>
<main>
<section class=\"hero\">
  <div class=\"badge\">Day 4 Dashboard</div>
  <h1>Live repair runs, attempt by attempt.</h1>
  <p class=\"lead\">Reflexa is streaming the manual repair loop in real time: failing test output, GPT/Groq diagnosis, patch application, sandbox validation, and the exported artifact.</p>
  <div class=\"meta\"><span class=\"chip\">Repo: [[REPO]]</span><span class=\"chip\">Test command: [[COMMAND]]</span></div>
  <div class=\"actions\"><button class=\"primary\" id=\"start\">Start repair run</button><button class=\"secondary\" id=\"refresh\">Refresh now</button><span class=\"chip\" id=\"connection\">Streaming live updates</span></div>
</section>
<section class=\"layout\">
  <aside class=\"sidebar panel\">
    <div class=\"toolbar\"><div><strong>Runs</strong><div class=\"lead\">Pick a run to inspect its full timeline.</div></div><select id=\"run-select\" class=\"select\"></select></div>
    <div class=\"runs\" id=\"run-list\"></div>
  </aside>
  <div class=\"content\">
    <section class=\"stats\" id=\"stats\"></section>
    <section class=\"grid2\">
      <article class=\"card\">
        <div style=\"display:flex;justify-content:space-between;gap:12px;align-items:flex-start;\"><div><h2 id=\"selected-title\">No run selected</h2><p class=\"lead\" id=\"selected-summary\">Start a repair run to see the live loop and final output.</p></div><div id=\"selected-status\"></div></div>
        <div class=\"chips\" id=\"selected-meta\"></div>
        <div class=\"mini\" style=\"margin-top:12px;\"><h4>Patch artifact</h4><pre id=\"publication-pre\">No patch yet.</pre></div>
      </article>
      <article class=\"card\"><h2>Sandbox and provider timeline</h2><p class=\"lead\">Each attempt groups the failure, diagnosis, patch, and sandbox output into one block.</p><div class=\"attempts\" id=\"attempts\"></div></article>
    </section>
    <section class=\"grid2\">
      <article class=\"card\"><h2>Live event stream</h2><div class=\"events\" id=\"events\"></div></article>
      <article class=\"card\"><h2>Run JSON</h2><div class=\"mini\"><pre id=\"raw-json\"></pre></div></article>
    </section>
  </div>
</section>
</main>
<script id=\"initial-state\" type=\"application/json\">[[INITIAL_STATE]]</script>
<script>
const app = { data: JSON.parse(document.getElementById('initial-state').textContent), selectedRunId: null };
const runSelect = document.getElementById('run-select'), runList = document.getElementById('run-list'), stats = document.getElementById('stats'), selectedTitle = document.getElementById('selected-title'), selectedSummary = document.getElementById('selected-summary'), selectedStatus = document.getElementById('selected-status'), selectedMeta = document.getElementById('selected-meta'), publicationPre = document.getElementById('publication-pre'), attempts = document.getElementById('attempts'), events = document.getElementById('events'), rawJson = document.getElementById('raw-json'), connection = document.getElementById('connection');
const startButton = document.getElementById('start'), refreshButton = document.getElementById('refresh'), publicationStatus = document.getElementById('publication-status');
const esc = (v) => String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const time = (v) => v ? new Date(v).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' }) : '—';
const cls = (v) => `status-${String(v || 'pending').toLowerCase()}`;
function renderStats(state) {{ const t = state.totals || {}; stats.innerHTML = [['Runs',t.runs||0],['Succeeded',t.succeeded||0],['Failed',t.failed||0],['Running',t.running||0],['Attempts',t.attempts||0],['Model steps',t.provider_steps||0]].map(([l,v]) => `<article class=\"stat panel\"><p class=\"label\">${esc(l)}</p><p class=\"value\">${esc(v)}</p></article>`).join(''); }}
function renderRuns(state) {{ const runs = state.runs || []; runSelect.innerHTML = runs.map((run) => `<option value=\"${esc(run.run_id)}\">${esc(run.run_id)} • ${esc(run.status)}</option>`).join(''); if (app.selectedRunId && runs.some((run) => run.run_id === app.selectedRunId)) {{ runSelect.value = app.selectedRunId; }} else if (runs.length) {{ app.selectedRunId = state.selected_run_id || runs[0].run_id; runSelect.value = app.selectedRunId; }} runList.innerHTML = runs.map((run) => `<button type=\"button\" class=\"run-card ${run.run_id === app.selectedRunId ? 'active' : ''}\" data-id=\"${esc(run.run_id)}\"><strong>${esc(run.run_id)}</strong><div class=\"chip status ${cls(run.status)}\" style=\"margin-top:8px;\">${esc(run.status)}</div><p>${esc(run.summary || 'No summary yet.')}</p><p style=\"margin-top:8px;\">${esc(run.attempt_count || 0)} attempts • ${esc(run.event_count || 0)} events</p></button>`).join('') || '<div class=\"empty\">No runs yet.</div>'; runList.querySelectorAll('[data-id]').forEach((button) => button.addEventListener('click', () => {{ app.selectedRunId = button.getAttribute('data-id'); render(app.data); refresh(app.selectedRunId).catch(() => {{}}); }})); }}
function renderSelected(state) { const selected = state.selected_run; if (!selected) { selectedTitle.textContent = 'No run selected'; selectedSummary.textContent = 'Kick off a repair run to see the live loop and its final artifact.'; selectedStatus.innerHTML = ''; selectedMeta.innerHTML = ''; publicationStatus.textContent = 'Waiting for the first export'; publicationPre.textContent = 'No patch yet.'; attempts.innerHTML = '<div class="empty">No attempt data available yet.</div>'; events.innerHTML = '<div class="empty">No live events yet.</div>'; rawJson.textContent = JSON.stringify(state, null, 2); return; } selectedTitle.textContent = selected.run_id; selectedSummary.textContent = selected.summary || 'Repair loop still running.'; selectedStatus.innerHTML = `<span class="status ${cls(selected.status)}">${esc(selected.status)}</span>`; selectedMeta.innerHTML = [['Started', time(selected.started_at)], ['Finished', time(selected.finished_at)], ['Last event', time(selected.last_event_at)], ['Attempts', selected.attempt_count || 0], ['Events', selected.event_count || 0]].map(([l,v]) => `<span class="chip"><strong>${esc(l)}:</strong> ${esc(v)}</span>`).join(''); const publicationText = selected.publication && selected.publication.patch_text ? selected.publication.patch_text : 'No patch artifact captured yet.'; publicationPre.textContent = publicationText; publicationStatus.textContent = selected.publication ? `Exported as ${selected.publication.mode || 'artifact'}` : 'Waiting for the first export'; const attemptCards = (selected.attempts || []).map((attempt) => { const diagnosis = attempt.diagnosis || attempt.diagnosis_summary || 'No diagnosis recorded yet.'; const failure = attempt.failure_output || 'No failure output recorded yet.'; const stdout = attempt.stdout || 'No sandbox stdout captured.'; const stderr = attempt.stderr || 'No sandbox stderr captured.'; const patchPaths = (attempt.patch_paths || []).join(', ') || 'No files patched yet.'; const verification = (attempt.verification_notes || []).join('\n') || 'No verification notes captured.'; const sandboxOutput = `${esc(stdout)}${stderr ? `\n\n${esc(stderr)}` : ''}`; const patchOutput = `${esc(patchPaths)}${verification ? `\n\nVerification:\n${esc(verification)}` : ''}`; return `<section class=\"attempt\"><div class=\"attempt-top\"><strong>Attempt ${esc(attempt.attempt || '?')}</strong><span class=\"status ${cls(attempt.status)}\">${esc(attempt.status)}</span></div><div class=\"attempt-grid\"><div class=\"mini\"><h4>Hypothesis</h4><pre>${esc(diagnosis)}</pre></div><div class=\"mini\"><h4>Sandbox output</h4><pre>${sandboxOutput}</pre></div><div class=\"mini\"><h4>Failure trace</h4><pre>${esc(failure)}</pre></div><div class=\"mini\"><h4>Patch targets</h4><pre>${patchOutput}</pre></div></div></section>`; }).join('') || '<div class=\"empty\">Attempt details will appear here once the repair loop starts.</div>'; attempts.innerHTML = attemptCards; events.innerHTML = (selected.events || []).slice().reverse().map((event) => `<article class=\"event\"><div class=\"event-top\"><strong>${esc(event.kind)}</strong><span>${esc(time(event.timestamp))}</span></div><div>${esc(event.message || '')}</div><pre>${esc(JSON.stringify(event.data || {}, null, 2))}</pre></article>`).join('') || '<div class=\"empty\">Events will stream here as soon as the run begins.</div>'; rawJson.textContent = JSON.stringify(selected, null, 2); }
function render(state) {{ app.data = state; renderStats(state); renderRuns(state); renderSelected(state); }}
async function refresh(selectedRunId = app.selectedRunId) {{ const url = selectedRunId ? `/api/state?selected_run_id=${encodeURIComponent(selectedRunId)}` : '/api/state'; const response = await fetch(url, {{ cache: 'no-store' }}); if (!response.ok) throw new Error(`Refresh failed: ${response.status}`); const state = await response.json(); app.selectedRunId = state.selected_run_id || selectedRunId || null; render(state); }}
async function startRun() {{ startButton.disabled = true; refreshButton.disabled = true; try {{ const response = await fetch('/start', {{ method: 'POST' }}); if (!response.ok) {{ const payload = await response.json().catch(() => ({{}})); throw new Error(payload.error || `Start failed: ${response.status}`); }} const payload = await response.json(); app.selectedRunId = payload.run_id; await refresh(app.selectedRunId); }} finally {{ startButton.disabled = false; refreshButton.disabled = false; }} }}
runSelect.addEventListener('change', () => {{ app.selectedRunId = runSelect.value || null; render(app.data); refresh(app.selectedRunId).catch(() => {{}}); }}); startButton.addEventListener('click', () => startRun().catch((error) => connection.textContent = error.message)); refreshButton.addEventListener('click', () => refresh().catch((error) => connection.textContent = error.message));
const source = new EventSource('/events'); source.onopen = () => connection.textContent = 'Streaming live updates'; source.onerror = () => connection.textContent = 'Waiting for updates'; source.onmessage = async (event) => {{ const payload = JSON.parse(event.data); const row = document.createElement('article'); row.className = 'event'; row.innerHTML = `<div class=\"event-top\"><strong>${esc(payload.kind)}</strong><span>${esc(time(payload.timestamp))}</span></div><div>${esc(payload.message || '')}</div><pre>${esc(JSON.stringify(payload.data || {}, null, 2))}</pre>`; events.prepend(row); try {{ await refresh(app.selectedRunId); }} catch (error) {{ connection.textContent = error.message; }} }};
render(app.data);
</script>
</body>
</html>"""
        page = page.replace("join('\n')", "join('\\n')")
        page = page.replace("${stderr ? `\n\n${esc(stderr)}` : ''}", "${stderr ? `\n\n${esc(stderr)}` : ''}")
        page = page.replace("${verification ? `\n\nVerification:\n${esc(verification)}` : ''}", "${verification ? `\n\nVerification:\n${esc(verification)}` : ''}")
        page = page.replace("{{", "{").replace("}}", "}")
        return page.replace("[[REPO]]", repo).replace("[[COMMAND]]", command).replace("[[INITIAL_STATE]]", initial_state)



def build_dashboard_state(snapshot: dict[str, object], selected_run_id: str | None = None) -> dict[str, object]:
    runs = [_build_run_view(record) for record in snapshot.values()]
    runs.sort(key=_run_sort_key, reverse=True)
    if not selected_run_id:
        selected_run_id = _select_default_run_id(runs)
    selected_run = next((run for run in runs if run["run_id"] == selected_run_id), None)
    totals = {
        "runs": len(runs),
        "pending": sum(1 for run in runs if run["status"] == RunStatus.PENDING.value),
        "running": sum(1 for run in runs if run["status"] == RunStatus.RUNNING.value),
        "succeeded": sum(1 for run in runs if run["status"] == RunStatus.SUCCEEDED.value),
        "failed": sum(1 for run in runs if run["status"] == RunStatus.FAILED.value),
        "attempts": sum(run["attempt_count"] for run in runs),
        "provider_steps": sum(run["provider_steps"] for run in runs),
        "sandbox_steps": sum(run["sandbox_steps"] for run in runs),
    }
    return {"selected_run_id": selected_run_id, "selected_run": selected_run, "runs": runs, "totals": totals}


def _build_run_view(record: dict[str, object]) -> dict[str, object]:
    events = list(record.get("events", []))
    attempts = _build_attempts(events)
    provider_steps = sum(1 for event in events if event.get("kind") in {"diagnosis", "provider_error", "provider_fallback"})
    sandbox_steps = sum(1 for event in events if event.get("kind") == "test_result")
    return {
        "run_id": record.get("run_id"),
        "status": record.get("status", RunStatus.PENDING.value),
        "summary": record.get("summary", ""),
        "publication": record.get("publication"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "last_event_at": record.get("last_event_at"),
        "events": events,
        "event_count": len(events),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "provider_steps": provider_steps,
        "sandbox_steps": sandbox_steps,
    }


def _build_attempts(events: list[dict[str, object]]) -> list[dict[str, object]]:
    attempts: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for event in events:
        kind = event.get("kind")
        data = event.get("data") or {}
        if kind == "attempt_started":
            current = {"attempt": data.get("attempt"), "status": "running", "started_at": event.get("timestamp"), "last_event_at": event.get("timestamp"), "events": [], "diagnosis": "", "diagnosis_summary": "", "confidence": None, "assumptions": [], "verification_notes": [], "stdout": "", "stderr": "", "failure_output": "", "patch_paths": []}
            attempts.append(current)
            continue
        if current is None:
            continue
        current["events"].append(event)
        current["last_event_at"] = event.get("timestamp")
        if kind == "test_result":
            current["stdout"] = data.get("stdout", current["stdout"])
            current["stderr"] = data.get("stderr", current["stderr"])
            current["returncode"] = data.get("returncode")
            current["passed"] = data.get("passed")
            current["duration_seconds"] = data.get("duration_seconds")
            current["status"] = "passed" if data.get("passed") else "failed"
        elif kind == "failure_detected":
            current["failure_output"] = data.get("failure_output", current["failure_output"])
        elif kind == "diagnosis":
            current["diagnosis"] = event.get("message", current["diagnosis"])
            current["diagnosis_summary"] = data.get("summary", current["diagnosis_summary"])
            current["confidence"] = data.get("confidence")
            current["assumptions"] = data.get("assumptions", current["assumptions"])
            current["verification_notes"] = data.get("verification_notes", current["verification_notes"])
        elif kind == "patch_applied":
            current["patch_paths"] = data.get("edits", current["patch_paths"])
            if current.get("status") != "passed":
                current["status"] = "patched"
        elif kind == "provider_error":
            current["status"] = "provider error"
        elif kind == "provider_fallback":
            current["status"] = "heuristic fallback"
        elif kind == "run_finished":
            current["status"] = data.get("status", current["status"])
    return attempts


def _run_sort_key(run: dict[str, object]) -> tuple[str, str]:
    return (str(run.get("last_event_at") or run.get("started_at") or ""), str(run.get("run_id") or ""))


def _select_default_run_id(runs: list[dict[str, object]]) -> str | None:
    for run in runs:
        if run["status"] == RunStatus.RUNNING.value:
            return str(run["run_id"])
    return str(runs[0]["run_id"]) if runs else None


def serve_dashboard(host: str, port: int, app: DashboardApp) -> ThreadingHTTPServer:
    handler = type("ReflexaDashboardHandler", (DashboardHandler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)
