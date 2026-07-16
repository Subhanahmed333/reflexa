from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .dashboard import DashboardApp, serve_dashboard
from .orchestrator import Orchestrator, OrchestratorConfig
from .publisher import LocalArtifactPublisher
from .providers import HeuristicRepairProvider, OpenAIRepairProvider
from .sandbox import DockerSandboxRunner, LocalSandboxRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reflexa")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", required=True, help="Path to the repository to repair.")
    common.add_argument("--tests", required=True, help="Test command to run inside the sandbox.")
    common.add_argument("--max-attempts", type=int, default=3)
    common.add_argument(
        "--provider",
        choices=("openai", "heuristic"),
        default="openai",
        help="Repair provider. OpenAI is the demo default; heuristic is fallback-only.",
    )
    common.add_argument(
        "--sandbox",
        choices=("docker", "local"),
        default="docker",
        help="Validation sandbox. Docker is the demo default.",
    )
    common.add_argument(
        "--allow-heuristic-fallback",
        action="store_true",
        help="Allow heuristic fallback if the OpenAI provider cannot produce a valid repair plan.",
    )
    common.add_argument(
        "--openai",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    common.add_argument(
        "--use-docker",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    run_parser = subparsers.add_parser("run", parents=[common], help="Run one repair cycle.")
    run_parser.add_argument("--output-dir", default=".reflexa_artifacts")

    serve_parser = subparsers.add_parser("serve", parents=[common], help="Serve the dashboard.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    return parser


def create_orchestrator(args: argparse.Namespace) -> Orchestrator:
    provider_name = "openai" if getattr(args, "openai", False) else args.provider
    sandbox_name = "docker" if getattr(args, "use_docker", False) else args.sandbox
    provider = OpenAIRepairProvider() if provider_name == "openai" else HeuristicRepairProvider()
    sandbox = DockerSandboxRunner() if sandbox_name == "docker" else LocalSandboxRunner()
    publisher = LocalArtifactPublisher(Path(getattr(args, "output_dir", ".reflexa_artifacts")))
    return Orchestrator(
        provider=provider,
        sandbox=sandbox,
        publisher=publisher,
        allow_heuristic_fallback=args.allow_heuristic_fallback,
    )


def parse_test_command(value: str) -> list[str]:
    return shlex.split(value, posix=False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    test_command = parse_test_command(args.tests)
    orchestrator = create_orchestrator(args)

    if args.command == "run":
        config = OrchestratorConfig(repo_path=repo, test_command=test_command, max_attempts=args.max_attempts)
        result = orchestrator.run(config)
        print(f"Status: {result.status.value}")
        print(f"Attempts: {result.attempts}")
        print(result.summary)
        if result.publication and result.publication.patch_path:
            print(f"Patch: {result.publication.patch_path}")
        if result.publication and result.publication.pull_request_url:
            print(f"PR: {result.publication.pull_request_url}")
        return 0 if result.status.value == "succeeded" else 1

    if args.command == "serve":
        app = DashboardApp(orchestrator=orchestrator, repo_path=repo, test_command=test_command, max_attempts=args.max_attempts)
        server = serve_dashboard(args.host, args.port, app)
        print(f"Reflexa dashboard listening on http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
