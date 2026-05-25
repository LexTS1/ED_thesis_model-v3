"""Run configured model_v3 validation runners with logs and a combined summary."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any, Iterable, Mapping

import yaml


LOGGER = logging.getLogger(__name__)
DEFAULT_CONFIG_DIR = Path("config/validation")
DEFAULT_OUTPUT_DIR = Path("outputs/validation")
DEFAULT_SUMMARY_JSON = Path("outputs/validation_summary.json")
DEFAULT_SUMMARY_MD = Path("outputs/validation_summary.md")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle) or {})


def _iter_runner_configs(config_dir: Path) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for path in sorted(config_dir.glob("*.yaml")):
        payload = _load_yaml(path)
        runner_cfg = dict(payload.get("runner", {}))
        if not runner_cfg:
            continue
        runner_cfg["config_path"] = str(path)
        configs.append(runner_cfg)
    return configs


def _select_runner_configs(configs: Iterable[Mapping[str, Any]], selected: set[str] | None) -> list[dict[str, Any]]:
    selected_configs: list[dict[str, Any]] = []
    for config in configs:
        name = str(config.get("name", "")).strip()
        if not name:
            continue
        if selected is None or name in selected:
            selected_configs.append(dict(config))
    if selected is not None:
        found = {str(config.get("name")) for config in selected_configs}
        missing = sorted(selected - found)
        if missing:
            raise ValueError(f"Unknown validation runner(s): {', '.join(missing)}")
    return selected_configs


def _normalise_command(command: Iterable[Any], *, repo_root: Path) -> list[str]:
    replacements = {
        "{python}": sys.executable,
        "{repo_root}": str(repo_root),
    }
    normalised: list[str] = []
    for raw_part in command:
        part = str(raw_part)
        for token, value in replacements.items():
            part = part.replace(token, value)
        normalised.append(part)
    return normalised


def _tail(text: str, *, lines: int = 40) -> str:
    split = text.splitlines()
    return "\n".join(split[-lines:])


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_log(log_path: Path, *, command: list[str], stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "$ " + " ".join(command),
                "",
                "## stdout",
                stdout.rstrip(),
                "",
                "## stderr",
                stderr.rstrip(),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _artifact_status(paths: Iterable[Any], *, repo_root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(str(raw_path))
        resolved = path if path.is_absolute() else repo_root / path
        artifacts.append(
            {
                "path": str(path),
                "exists": resolved.exists(),
                "size_bytes": resolved.stat().st_size if resolved.exists() and resolved.is_file() else None,
            }
        )
    return artifacts


def _run_one(
    runner_cfg: Mapping[str, Any],
    *,
    repo_root: Path,
    quick: bool,
    timeout_override: int | None,
    log_dir: Path,
) -> dict[str, Any]:
    name = str(runner_cfg["name"])
    base_command = list(runner_cfg.get("command", []))
    mode_args = list(runner_cfg.get("quick_args" if quick else "full_args", []))
    command = _normalise_command([*base_command, *mode_args], repo_root=repo_root)
    if not command:
        raise ValueError(f"Runner {name} does not define runner.command")

    timeout_seconds = int(timeout_override or runner_cfg.get("timeout_seconds", 900))
    log_path = log_dir / f"{name}.log"
    env = dict(os.environ)
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"

    LOGGER.info("validation.runner.start name=%s timeout_s=%s quick=%s", name, timeout_seconds, quick)
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        elapsed = perf_counter() - started
        status = "passed" if completed.returncode == 0 else "failed"
        stdout = completed.stdout
        stderr = completed.stderr
        returncode: int | None = completed.returncode
    except subprocess.TimeoutExpired as exc:
        elapsed = perf_counter() - started
        status = "timeout"
        stdout = _coerce_text(exc.stdout)
        stderr = _coerce_text(exc.stderr)
        returncode = None

    _write_log(log_path, command=command, stdout=stdout, stderr=stderr)
    artifact_key = "quick_expected_artifacts" if quick else "full_expected_artifacts"
    expected_artifacts = runner_cfg.get(artifact_key, runner_cfg.get("expected_artifacts", []))
    artifacts = _artifact_status(expected_artifacts, repo_root=repo_root)
    LOGGER.info("validation.runner.complete name=%s status=%s elapsed_s=%.1f log=%s", name, status, elapsed, log_path)
    return {
        "name": name,
        "description": runner_cfg.get("description", ""),
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout_seconds,
        "quick": quick,
        "command": command,
        "config_path": runner_cfg.get("config_path", ""),
        "log_path": str(log_path),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "artifacts": artifacts,
    }


def _write_summary(summary: Mapping[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# model_v3 Validation Summary",
        "",
        f"- quick mode: `{summary['quick']}`",
        f"- overall status: `{summary['overall_status']}`",
        f"- elapsed seconds: `{summary['elapsed_seconds']:.1f}`",
        f"- artifact status: `{'debug/non-thesis' if summary['quick'] else 'canonical candidate'}`",
        "",
        "| Runner | Status | Elapsed (s) | Timeout (s) | Log |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for result in summary["results"]:
        lines.append(
            "| {name} | {status} | {elapsed:.1f} | {timeout} | {log} |".format(
                name=result["name"],
                status=result["status"],
                elapsed=float(result["elapsed_seconds"]),
                timeout=result["timeout_seconds"],
                log=result["log_path"],
            )
        )
    lines.extend(["", "## Artifact Check", ""])
    for result in summary["results"]:
        lines.append(f"### {result['name']}")
        artifacts = result.get("artifacts", [])
        if not artifacts:
            lines.append("- no expected artifacts declared")
            lines.append("")
            continue
        for artifact in artifacts:
            status = "present" if artifact["exists"] else "missing"
            size = f", {artifact['size_bytes']} bytes" if artifact.get("size_bytes") is not None else ""
            lines.append(f"- `{artifact['path']}`: {status}{size}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def run_all_validations(
    *,
    config_dir: Path,
    output_dir: Path,
    summary_json: Path,
    summary_md: Path,
    selected_runners: set[str] | None,
    quick: bool,
    timeout_override: int | None,
) -> dict[str, Any]:
    repo_root = _repo_root()
    configs = _select_runner_configs(_iter_runner_configs(repo_root / config_dir), selected_runners)
    if not configs:
        raise ValueError(f"No validation runner configs found in {config_dir}")

    started = perf_counter()
    log_dir = repo_root / output_dir / "logs"
    results = [
        _run_one(
            config,
            repo_root=repo_root,
            quick=quick,
            timeout_override=timeout_override,
            log_dir=log_dir,
        )
        for config in configs
    ]
    elapsed = perf_counter() - started
    failed = [result for result in results if result["status"] != "passed"]
    summary = {
        "quick": quick,
        "overall_status": "passed" if not failed else "failed",
        "elapsed_seconds": elapsed,
        "results": results,
    }
    _write_summary(summary, json_path=repo_root / summary_json, md_path=repo_root / summary_md)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run configured model_v3 validations with timeouts and summary artifacts.")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR), help="Directory containing validation runner YAML configs.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for per-runner logs.")
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON), help="Combined summary JSON path.")
    parser.add_argument("--summary-md", default=str(DEFAULT_SUMMARY_MD), help="Combined summary Markdown path.")
    parser.add_argument("--runner", action="append", default=None, help="Runner name to execute. Repeatable.")
    parser.add_argument("--timeout-seconds", type=int, default=None, help="Override every per-runner timeout.")
    parser.add_argument("--full", action="store_true", help="Run full validation commands instead of quick commands.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S")
    args = build_parser().parse_args(argv)
    summary = run_all_validations(
        config_dir=Path(args.config_dir),
        output_dir=Path(args.output_dir),
        summary_json=Path(args.summary_json),
        summary_md=Path(args.summary_md),
        selected_runners=set(args.runner) if args.runner else None,
        quick=not bool(args.full),
        timeout_override=args.timeout_seconds,
    )
    print(f"Validation summary: {summary['overall_status']} in {summary['elapsed_seconds']:.1f}s")
    print(str(_repo_root() / args.summary_md))
    return 0 if summary["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
