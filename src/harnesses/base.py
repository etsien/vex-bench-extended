"""Base harness interface.

Mirrors the original VEX-Bench Agent ABC but decoupled from any single
CLI tool. Each harness implementation wraps a specific execution backend
(Claude Code, Codex, OpenCode, Cursor SDK, Vertex AI).
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


@dataclass(frozen=True)
class RunContext:
    cwd: Path
    language: str
    timeout: int = 600
    artifacts_dir: Path | None = None


class AgentResult(BaseModel):
    raw_output: str


class RunStats(BaseModel):
    steps: int | None = None
    completed: bool = False
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ParsedResult:
    path: Path
    raw: str
    text: str
    category: str
    reasoning: str | None
    stats: RunStats


class BaseHarness(ABC):
    """Execution backend for a single model."""

    @abstractmethod
    def run(self, prompt: str, ctx: RunContext) -> AgentResult: ...

    @abstractmethod
    def extract_text(self, raw_output: str) -> str: ...

    @abstractmethod
    def result_filename(self) -> str: ...

    @abstractmethod
    def extract_stats(self, raw_output: str) -> RunStats: ...

    @abstractmethod
    def load_result(self, run_dir: Path) -> ParsedResult | None: ...

    @abstractmethod
    def has_result(self, run_dir: Path) -> bool: ...

    def save_report(self, result_dir: Path) -> None:
        """Optionally write a human-readable report. No-op by default."""


# ---------------------------------------------------------------------------
# Docker helpers shared by container-based harnesses
# ---------------------------------------------------------------------------

def run_in_container(
    image: str,
    args: list[str],
    *,
    entrypoint: str,
    src: Path,
    timeout: int,
    env_file: Path | None = None,
    mounts: Sequence[tuple[Path, str]] = (),
    workdir: str = "/work",
    artifacts_dir: Path | None = None,
    artifact_paths: Sequence[tuple[str, str]] = (),
    network: str | None = None,
) -> str:
    """Run a one-shot agent command in a disposable container; return stdout."""
    create_cmd = ["docker", "create", "--workdir", workdir]
    if network is not None:
        create_cmd += ["--network", network]
    if env_file is not None:
        create_cmd += ["--env-file", str(env_file)]
    for host_path, container_path in mounts:
        create_cmd += ["-v", f"{host_path}:{container_path}:ro"]
    create_cmd += ["--entrypoint", entrypoint, image, *args]

    try:
        created = subprocess.run(create_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"docker create failed: {exc.stderr.strip() or '(no stderr)'}"
        ) from exc
    cid = created.stdout.strip()

    try:
        try:
            subprocess.run(
                ["docker", "cp", f"{src.resolve()}/.", f"{cid}:{workdir}"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"docker cp failed: {exc.stderr.strip() or '(no stderr)'}"
            ) from exc

        try:
            started = subprocess.run(
                ["docker", "start", "-a", cid],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"container timed out after {timeout}s"
                f"{_captured_io(exc.stdout, exc.stderr)}"
            ) from exc

        if started.returncode != 0:
            raise RuntimeError(
                f"container exited {started.returncode}: "
                f"{started.stderr.strip() or '(no stderr)'}"
                f"{_captured_io(started.stdout, started.stderr)}"
            )
        return started.stdout
    finally:
        if artifacts_dir is not None:
            _copy_container_artifacts(cid, artifacts_dir, artifact_paths)
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)


def _copy_container_artifacts(
    cid: str, artifacts_dir: Path, artifact_paths: Sequence[tuple[str, str]]
) -> None:
    if not artifact_paths:
        return
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for container_path, name in artifact_paths:
        dest = artifacts_dir / name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        subprocess.run(
            ["docker", "cp", f"{cid}:{container_path}", str(dest)],
            capture_output=True,
            check=False,
        )


def _captured_io(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    parts = []
    for name, value in (("stdout", stdout), ("stderr", stderr)):
        text = _decode(value).strip()
        if text:
            parts.append(f"\n{name} tail:\n{text[-4000:]}")
    return "".join(parts)


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
