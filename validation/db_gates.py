"""Shared hard gate for SQLite-backed repair suites."""
from __future__ import annotations


def gate_failures(cases, integrity_check, foreign_key_rows):
    failures = []
    failed_cases = [
        str(case.get("id", "unknown"))
        for case in cases
        if case.get("status") != "PASS"
    ]
    if failed_cases:
        failures.append("case failures: " + ", ".join(failed_cases))
    if integrity_check != "ok":
        failures.append(f"integrity_check: {integrity_check}")
    if foreign_key_rows:
        failures.append(f"foreign_key_check: {len(foreign_key_rows)} row(s)")
    return failures


def passed(cases, integrity_check, foreign_key_rows):
    return not gate_failures(cases, integrity_check, foreign_key_rows)
