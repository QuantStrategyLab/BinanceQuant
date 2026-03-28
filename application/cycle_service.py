"""Application-level cycle execution helpers for BinanceQuant."""

from __future__ import annotations

import json
import os


def write_execution_report(report, *, reports_dir="reports", filename="execution_report.json"):
    os.makedirs(reports_dir, exist_ok=True)
    output_path = os.path.join(reports_dir, filename)
    with open(output_path, "w") as handle:
        json.dump(report, handle, indent=2, default=str)
    return output_path


def run_live_cycle(
    *,
    runtime_builder,
    execute_cycle,
    output_printer=print,
    report_writer=write_execution_report,
    exit_fn=None,
):
    runtime = runtime_builder()
    report = execute_cycle(runtime)
    output_printer("\n".join(report.get("log_lines", [])))
    report_path = report_writer(report)

    if report.get("status") != "ok" and exit_fn is not None:
        exit_fn(1)

    return report, report_path

