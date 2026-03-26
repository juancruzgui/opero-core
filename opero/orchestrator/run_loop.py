"""Standalone entry point for running the orchestrator loop.

Called by the interactive PM session via:
  python -m opero.orchestrator.run_loop --project-path /path --project-id abc123

This runs the full PM → Dev → Test → Review cycle as a background process.
"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Run the Opero orchestrator loop")
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--spec-file", help="Path to spec file")
    parser.add_argument("--spec", help="Inline spec text")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--skip-testing", action="store_true")
    args = parser.parse_args()

    spec_text = None
    if args.spec_file:
        from pathlib import Path
        spec_text = Path(args.spec_file).read_text()
    elif args.spec:
        spec_text = args.spec

    from opero.orchestrator.loop import OrchestratorLoop
    loop = OrchestratorLoop(
        project_path=args.project_path,
        project_id=args.project_id,
        spec_text=spec_text,
        max_iterations=args.max_iterations,
        parallel_agents=args.parallel,
        skip_testing=args.skip_testing,
    )
    loop.run()


if __name__ == "__main__":
    main()
