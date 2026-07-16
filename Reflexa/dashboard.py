from __future__ import annotations

import html
import json
import queue
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .models import RunEvent, RunResult, RunStatus
from .orchestrator import Orchestrator, OrchestratorConfig


@dataclass(slots=True)
class RunRecord:
    run_id: str
    status: str = RunStatus.PENDING.value
    events: list[dict] = field(default_factory=list)
    summary: str = ""
    publication: dict | None = None


@dataclass(slots=True)
class RunStore:
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _records: dict[str, RunRecord] = field(default_factory=dict)
    _subscribers: list[queue.Queue[dict]] = field(default_factory=list)

    def create(self, run_id: str) -> None:
        with self._lock:
            self._records[run_id] = RunRecord(run_id=run_id)

    def update(self, event: RunEvent) -> None:
        with self._lock:
            record = self._records.setdefault(event.data.get("run_id", "live"), RunRecord(run_id=event.data.get("run_id", "live")))
            record.events.append(event.to_dict())
            if event.kind == "run_finished":
                record.status = event.data.get("status", record.status)
            if event.kind == "artifact_written":
                record.publication = event.data
            if event.kind == "run_started":
                record.status = RunStatus.RUNNING.value
            payload = event.to_dict()
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
                    "events": record.events,
                    "publication": record.publication,
                }
                for run_id, record in self._records.items()
            }

    def subscribe(self) -> queue.Queue[dict]:
        subscriber: queue.Queue[dict] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict]) -> None:
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

    def _execute_run(self, config: OrchestratorConfig) -> None:
        sink = _StoreSink(self.store)
        result = self.orchestrator.run(config, sink=sink)
        record = self.store._records.setdefault(result.run_id, RunRecord(run_id=result.run_id))
        record.status = result.status.value
        record.summary = result.summary
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
        if self.path == "/api/state":
            self._send_json(self.app.store.snapshot())
            return
        if self.path == "/events":
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
                    payload = json.dumps(event)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
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
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Reflexa Dashboard</title>
  <style>
    :root {{ color-scheme: dark; --bg: #0b1020; --panel: #121a31; --accent: #89b4ff; --text: #ecf2ff; --muted: #95a3c7; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: linear-gradient(180deg, #101831, #0b1020 60%); color: var(--text); }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 32px; }}
    .hero {{ display: grid; gap: 8px; padding: 24px; background: rgba(18,26,49,.9); border: 1px solid rgba(137,180,255,.2); border-radius: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }}
    .card {{ background: rgba(18,26,49,.9); border: 1px solid rgba(137,180,255,.14); border-radius: 16px; padding: 16px; }}
    button {{ background: var(--accent); color: #08111f; border: 0; padding: 12px 18px; border-radius: 12px; font-weight: 700; cursor: pointer; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #091020; padding: 12px; border-radius: 12px; overflow: auto; }}
    .event {{ margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,.08); }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="muted">Reflexa MVP</div>
      <h1>Autonomous bug-fixing loop</h1>
      <div class="muted">Repo: {repo}</div>
      <div class="muted">Test command: {command}</div>
      <div><button id="start">Start repair run</button></div>
    </section>
    <section class="grid">
      <div class="card">
        <h2>Run State</h2>
        <pre id="state">Waiting for a run...</pre>
      </div>
      <div class="card">
        <h2>Event Stream</h2>
        <div id="events"></div>
      </div>
    </section>
  </main>
  <script>
    const stateNode = document.getElementById('state');
    const eventsNode = document.getElementById('events');
    async function refresh() {{
      const response = await fetch('/api/state');
      stateNode.textContent = JSON.stringify(await response.json(), null, 2);
    }}
    document.getElementById('start').addEventListener('click', async () => {{
      await fetch('/start', {{method: 'POST'}});
      await refresh();
    }});
    const source = new EventSource('/events');
    source.onmessage = (event) => {{
      const payload = JSON.parse(event.data);
      const row = document.createElement('div');
      row.className = 'event';
      row.innerHTML = `<strong>${{payload.kind}}</strong><div class="muted">${{payload.timestamp}}</div><div>${{payload.message}}</div><pre>${{JSON.stringify(payload.data, null, 2)}}</pre>`;
      eventsNode.prepend(row);
      refresh();
    }};
    refresh();
  </script>
</body>
</html>"""


def serve_dashboard(host: str, port: int, app: DashboardApp) -> ThreadingHTTPServer:
    handler = type("ReflexaDashboardHandler", (DashboardHandler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    return server

