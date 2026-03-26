"""Orchestrator loop — the autonomous PM → Dev → Test → Review cycle.

Launches Claude Code instances as subprocesses, each with a role-specific
prompt. Coordinates through the shared SQLite database. The orchestrator
never writes code itself — it only dispatches agents and monitors progress.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from opero.agents.prompts import pm_spec_prompt, pm_review_prompt, dev_prompt, tester_prompt
from opero.core.engine import OperoEngine
from opero.core.models import TaskStatus, _new_id, _now
from opero.db.schema import get_connection


@dataclass
class AgentProcess:
    """Tracks a running Claude Code subprocess."""
    process: subprocess.Popen
    agent_name: str
    task_id: str
    started_at: float = field(default_factory=time.time)
    retries: int = 0


class OrchestratorLoop:
    MAX_RETRIES = 3
    POLL_INTERVAL = 5  # seconds
    HANG_TIMEOUT = 120  # seconds with no DB activity before killing

    def __init__(
        self,
        project_path: str,
        project_id: str,
        spec_text: str | None = None,
        max_iterations: int = 3,
        parallel_agents: int = 1,
        skip_testing: bool = False,
    ):
        self.project_path = project_path
        self.project_id = project_id
        self.spec_text = spec_text
        self.max_iterations = max_iterations
        self.parallel_agents = parallel_agents
        self.skip_testing = skip_testing
        self.run_id = _new_id()
        self.engine = OperoEngine(project_path)
        self._active: list[AgentProcess] = []
        self._stopped = False

        # Handle graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print("\n✦ Orchestrator shutting down gracefully...")
        self._stopped = True
        self._kill_all()

    def _kill_all(self):
        for ap in self._active:
            try:
                ap.process.kill()
            except OSError:
                pass
        self._active.clear()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _update_run(self, **kwargs):
        conn = get_connection(self.project_path)
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(
            f"UPDATE orchestrator_runs SET {sets} WHERE id = ?",
            list(kwargs.values()) + [self.run_id],
        )
        conn.commit()
        conn.close()

    def _create_run(self):
        conn = get_connection(self.project_path)
        conn.execute(
            "INSERT INTO orchestrator_runs (id, project_id, status, phase, iteration, spec_text, config) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, self.project_id, "running", "planning", 1, self.spec_text or "",
             json.dumps({"max_iterations": self.max_iterations, "parallel_agents": self.parallel_agents, "skip_testing": self.skip_testing})),
        )
        conn.commit()
        conn.close()

    def _get_ready_tasks(self) -> list:
        """Get TODO tasks with all dependencies satisfied, sorted by priority."""
        tasks = self.engine.tasks.list_tasks(project_id=self.project_id, status=TaskStatus.TODO)
        ready = []
        done_ids = {t.id for t in self.engine.tasks.list_tasks(project_id=self.project_id, status=TaskStatus.DONE)}
        active_task_ids = {ap.task_id for ap in self._active}

        for t in tasks:
            if t.id in active_task_ids:
                continue
            deps_met = all(d in done_ids for d in t.dependencies)
            if deps_met:
                ready.append(t)

        ready.sort(key=lambda t: t.priority)
        return ready

    def _get_unverified_tasks(self) -> list:
        """Get DONE tasks that haven't been verified yet."""
        conn = get_connection(self.project_path)
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND status = 'done' AND (verification_status IS NULL OR verification_status = '')",
            (self.project_id,),
        ).fetchall()
        conn.close()
        from opero.core.models import Task
        return [Task.from_row(dict(r)) for r in rows]

    def _count_tasks_by_status(self) -> dict:
        tasks = self.engine.tasks.list_tasks(project_id=self.project_id)
        counts = {"total": len(tasks), "todo": 0, "in_progress": 0, "done": 0, "blocked": 0}
        for t in tasks:
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        return counts

    def _build_completed_summary(self) -> str:
        """Build a summary of what completed agents have built, for context passing."""
        done_tasks = self.engine.tasks.list_tasks(project_id=self.project_id, status=TaskStatus.DONE)
        if not done_tasks:
            return ""
        lines = []
        for t in done_tasks:
            out = t.outputs[:200] if t.outputs else "no output recorded"
            lines.append(f"- **{t.title}** [{t.assigned_agent or '?'}]: {out}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Claude Code launcher
    # ------------------------------------------------------------------

    def _ensure_mcp_config(self):
        """Ensure .mcp.json exists so Claude Code can access opero MCP tools."""
        mcp_path = Path(self.project_path).resolve() / ".mcp.json"
        abs_project_path = str(Path(self.project_path).resolve())

        # Find the opero package root (parent of the opero/ package dir)
        import opero
        opero_root = str(Path(opero.__file__).resolve().parent.parent)

        # Prefer the venv Python if it exists
        venv_python = Path(self.project_path).resolve() / ".opero" / "venv" / "bin" / "python"
        py = str(venv_python) if venv_python.exists() else sys.executable
        env = {"OPERO_PROJECT_PATH": abs_project_path}
        # If not using venv, add source dir to PYTHONPATH
        if not venv_python.exists():
            try:
                from importlib.metadata import distribution
                distribution("opero")
            except Exception:
                env["PYTHONPATH"] = opero_root

        config = {
            "mcpServers": {
                "opero": {
                    "command": py,
                    "args": ["-m", "opero.mcp.stdio_server"],
                    "env": env,
                },
                "supabase": {
                    "url": "http://127.0.0.1:54321/mcp",
                },
            }
        }
        mcp_path.write_text(json.dumps(config, indent=2))
        return str(mcp_path)

    def _launch_claude(self, prompt: str, agent_name: str, task_id: str = "") -> AgentProcess:
        """Launch a Claude Code instance with the given prompt.

        Uses `claude --print <prompt>` which runs non-interactively with full
        tool access (Edit, Bash, Write, Read, MCP tools, etc.). The
        --dangerously-skip-permissions flag lets the agent work without asking
        for human approval on each tool call. We pass --mcp-config explicitly
        to ensure the opero MCP server is always available.
        """
        print(f"  ▸ Launching {agent_name}" + (f" on task {task_id}" if task_id else ""))

        mcp_config = self._ensure_mcp_config()

        proc = subprocess.Popen(
            [
                "claude",
                "--print",                          # non-interactive, stdout output
                "--dangerously-skip-permissions",    # autonomous — no human approval
                "--mcp-config", mcp_config,          # ensure opero MCP tools available
                "--system-prompt", f"You are the {agent_name} agent. Follow your instructions precisely. Use the opero MCP tools for all task tracking.",
                "--",                                # separator: everything after is the prompt
                prompt,                              # the actual prompt (positional arg)
            ],
            cwd=self.project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "OPERO_PROJECT_PATH": self.project_path},
        )

        ap = AgentProcess(process=proc, agent_name=agent_name, task_id=task_id)
        self._active.append(ap)

        # Mark task as in_progress if assigned
        if task_id:
            self.engine.tasks.update(task_id, status="in_progress", assigned_agent=agent_name)
            # Create execution record
            execution = self.engine.agents.create_execution(task_id, agent_name)
            self.engine.agents.update_execution(execution.id, status="running", started_at=_now())

        return ap

    def _wait_for_completion(self, timeout_per_agent: int = 600) -> list[AgentProcess]:
        """Poll active processes until all complete or timeout."""
        completed = []
        start = time.time()

        while self._active and not self._stopped:
            still_active = []
            for ap in self._active:
                ret = ap.process.poll()
                if ret is not None:
                    # Process finished
                    stdout = ap.process.stdout.read().decode() if ap.process.stdout else ""
                    stderr = ap.process.stderr.read().decode() if ap.process.stderr else ""
                    elapsed = time.time() - ap.started_at

                    if ret == 0:
                        print(f"  ✓ {ap.agent_name} completed" + (f" task {ap.task_id}" if ap.task_id else "") + f" ({elapsed:.0f}s)")
                    else:
                        print(f"  ✗ {ap.agent_name} failed (exit={ret})" + (f" task {ap.task_id}" if ap.task_id else ""))
                        if stderr:
                            print(f"    stderr: {stderr[:200]}")
                        # Handle failure
                        if ap.task_id:
                            self._handle_agent_failure(ap)

                    completed.append(ap)
                elif time.time() - ap.started_at > timeout_per_agent:
                    print(f"  ⧖ {ap.agent_name} timed out after {timeout_per_agent}s — killing")
                    ap.process.kill()
                    if ap.task_id:
                        self._handle_agent_failure(ap)
                    completed.append(ap)
                else:
                    still_active.append(ap)

            self._active = still_active

            if self._active:
                time.sleep(self.POLL_INTERVAL)

        return completed

    def _handle_agent_failure(self, ap: AgentProcess):
        """Handle a failed agent: retry or mark blocked."""
        if ap.retries < self.MAX_RETRIES:
            print(f"    Retrying task {ap.task_id} (attempt {ap.retries + 1}/{self.MAX_RETRIES})")
            self.engine.tasks.update(ap.task_id, status="todo")
        else:
            print(f"    Max retries reached for task {ap.task_id} — marking blocked")
            self.engine.tasks.update(ap.task_id, status="blocked")

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _phase_planning(self):
        """Phase 1: PM analyzes spec and creates features/tasks."""
        if not self.spec_text:
            print("  (No spec provided, skipping planning phase)")
            return

        self._update_run(phase="planning")
        print("\n✦ Phase 1: PLANNING — PM analyzing spec...")

        prompt = pm_spec_prompt(self.spec_text, self.project_id)
        self._launch_claude(prompt, "pm_analyst")
        self._wait_for_completion(timeout_per_agent=300)

        # Verify tasks were created
        counts = self._count_tasks_by_status()
        print(f"  Created: {counts['total']} tasks across features")

        if counts["total"] == 0:
            print("  ⚠ PM created no tasks — check spec or retry")
            return

    def _phase_development(self):
        """Phase 2: Dev agents work on TODO tasks in parallel."""
        self._update_run(phase="development")
        print("\n✦ Phase 2: DEVELOPMENT — Agents building...")

        while not self._stopped:
            ready = self._get_ready_tasks()
            if not ready and not self._active:
                break

            # Build context of what's been completed so far
            completed_summary = self._build_completed_summary()

            # Launch agents for ready tasks up to parallel limit
            slots = self.parallel_agents - len(self._active)
            for task in ready[:slots]:
                agent = self.engine.agents.find_agent_for_task(task)
                agent_name = agent.name if agent else "fullstack_dev"
                prompt = dev_prompt(
                    task.id, task.title, task.description,
                    task.success_criteria, agent_name,
                    completed_tasks_summary=completed_summary,
                )
                self._launch_claude(prompt, agent_name, task.id)

            if self._active:
                # Wait for at least one to finish
                self._wait_for_completion(timeout_per_agent=600)

        counts = self._count_tasks_by_status()
        print(f"  Development complete: {counts['done']} done, {counts['blocked']} blocked, {counts['todo']} remaining")

    def _phase_testing(self):
        """Phase 3: Tester verifies completed tasks."""
        if self.skip_testing:
            print("\n  (Testing phase skipped)")
            return 0

        self._update_run(phase="testing")
        print("\n✦ Phase 3: TESTING — Verifying completed work...")

        unverified = self._get_unverified_tasks()
        if not unverified:
            print("  No tasks to verify")
            return 0

        failures = 0
        for task in unverified:
            if self._stopped:
                break
            prompt = tester_prompt(task.id, task.title, task.success_criteria,
                                   task_outputs=task.outputs or "")
            self._launch_claude(prompt, "tester", task.id)

            # Run tests one at a time (tester may need the server)
            self._wait_for_completion(timeout_per_agent=300)

            # Check result
            conn = get_connection(self.project_path)
            row = conn.execute("SELECT verification_status FROM tasks WHERE id = ?", (task.id,)).fetchone()
            conn.close()
            if row and row["verification_status"] == "failed":
                failures += 1

        print(f"  Tested {len(unverified)} tasks: {len(unverified) - failures} passed, {failures} failed")
        return failures

    def _phase_review(self) -> int:
        """Phase 4: PM reviews all work and creates follow-ups. Returns count of new tasks."""
        self._update_run(phase="review")
        print("\n✦ Phase 4: REVIEW — PM reviewing completed work...")

        # Count tasks before review
        before_count = self._count_tasks_by_status()["total"]

        prompt = pm_review_prompt(self.project_id, spec_text=self.spec_text or "")
        self._launch_claude(prompt, "pm_analyst")
        self._wait_for_completion(timeout_per_agent=300)

        # Count tasks after review
        after_count = self._count_tasks_by_status()["total"]
        new_tasks = after_count - before_count

        if new_tasks > 0:
            print(f"  PM created {new_tasks} follow-up tasks")
        else:
            print("  PM review complete — no follow-up tasks needed")

        return new_tasks

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        """Run the full orchestrator loop."""
        print(f"✦ Opero Orchestrator starting")
        print(f"  Run ID: {self.run_id}")
        print(f"  Max iterations: {self.max_iterations}")
        print(f"  Parallel agents: {self.parallel_agents}")
        print(f"  Testing: {'disabled' if self.skip_testing else 'enabled'}")
        if self.spec_text:
            print(f"  Spec: {self.spec_text[:100]}{'...' if len(self.spec_text) > 100 else ''}")

        # Check claude CLI is available
        try:
            subprocess.run(["claude", "--version"], capture_output=True, check=True, timeout=10)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print("\n  ✗ Error: 'claude' CLI not found or not working.")
            print("  Install Claude Code: https://docs.anthropic.com/en/docs/claude-code")
            sys.exit(1)

        self._create_run()

        try:
            for iteration in range(1, self.max_iterations + 1):
                if self._stopped:
                    break

                self._update_run(iteration=iteration)
                print(f"\n{'='*50}")
                print(f"✦ Iteration {iteration}/{self.max_iterations}")
                print(f"{'='*50}")

                # Phase 1: Planning (only on first iteration with spec)
                if iteration == 1 and self.spec_text:
                    self._phase_planning()

                # Phase 2: Development
                self._phase_development()

                if self._stopped:
                    break

                # Phase 3: Testing
                failures = self._phase_testing()

                if self._stopped:
                    break

                # Phase 4: Review
                new_tasks = self._phase_review()

                # Decide whether to continue
                counts = self._count_tasks_by_status()
                if counts["todo"] == 0 and new_tasks == 0 and failures == 0:
                    print(f"\n✦ All work complete! No pending tasks or follow-ups.")
                    break

                if new_tasks > 0 or failures > 0:
                    print(f"\n  → Continuing to iteration {iteration + 1} ({counts['todo']} tasks todo, {failures} test failures)")

            # Final status
            self._update_run(status="completed", completed_at=_now())
            counts = self._count_tasks_by_status()
            print(f"\n{'='*50}")
            print(f"✦ Orchestrator complete")
            print(f"  Tasks: {counts['done']} done, {counts['blocked']} blocked, {counts['todo']} remaining")
            print(f"{'='*50}")

        except Exception as e:
            self._update_run(status="failed", completed_at=_now())
            self._kill_all()
            print(f"\n  ✗ Orchestrator error: {e}")
            raise

    # ------------------------------------------------------------------
    # Control methods (for CLI status/pause/stop)
    # ------------------------------------------------------------------

    @staticmethod
    def get_status(project_path: str, project_id: str) -> dict | None:
        conn = get_connection(project_path)
        row = conn.execute(
            "SELECT * FROM orchestrator_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def pause(project_path: str, run_id: str):
        conn = get_connection(project_path)
        conn.execute("UPDATE orchestrator_runs SET status = 'paused' WHERE id = ?", (run_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def stop(project_path: str, run_id: str):
        conn = get_connection(project_path)
        conn.execute("UPDATE orchestrator_runs SET status = 'completed', completed_at = ? WHERE id = ?", (_now(), run_id))
        conn.commit()
        conn.close()
