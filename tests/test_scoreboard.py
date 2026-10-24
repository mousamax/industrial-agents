"""Scoreboard logic: rows turn into rates, unmeasured rows score 0, points split between owners."""

from __future__ import annotations

import pytest
import yaml

from app import scoreboard as sb


def test_pytest_rows_count_only_passes():
    tests = {
        "test_geometry_x[a]": "passed",
        "test_geometry_x[b]": "failed",
        "test_geometry_y[c]": "xfail",
        "test_other[d]": "passed",
    }
    m = sb.measure_pytest(["test_geometry_"], tests)
    assert m.rate == 1 / 3
    assert "test_geometry_x (failed)" in m.failing and "test_geometry_y (xfail)" in m.failing


def test_rows_without_tests_or_runs_score_zero():
    assert sb.measure_pytest(["test_nothing_"], {}).rate == 0.0
    assert sb.measure_bench("warmup", None, {"suites": {}}).rate == 0.0
    assert sb.measure_scenarios({"suites": {}}).rate == 0.0
    assert sb.measure({"missing": "write it"}, {}, {}).rate == 0.0


def test_bench_rows_filter_kinds():
    state = {"suites": {"benchmark": {"by_kind": {"errand": [9, 10], "largest_room": [5, 5]}}}}
    assert sb.measure_bench("benchmark", ["errand"], state).rate == 0.9
    assert sb.measure_bench("benchmark", None, state).rate == 14 / 15


def test_config_rows_are_well_formed():
    config = yaml.safe_load(sb.CONFIG.read_text(encoding="utf-8"))
    sources = {"pytest", "bench", "scenarios", "manual", "missing"}
    assert sum(r["stake"] for r in config["rows"]) == 100
    for r in config["rows"]:
        used = sources & r.keys()
        assert len(used) == 1 or used == {"pytest", "scenarios"}, r["id"]  # code tests + agent scenarios
        assert set(r["owners"]) <= set(config["people"]), r["id"]


def test_a_row_measured_by_tests_and_scenarios_needs_both():
    state = {
        "suites": {
            "scenarios": {
                "correct": 2,
                "total": 3,
                "failing": ["live-b"],
                "results": {"live-a": True, "live-b": False, "other": True},
            }
        }
    }
    row = {"pytest": ["test_live_"], "scenarios": ["live-"]}
    m = sb.measure(row, {"test_live_x": "passed", "test_live_y": "passed"}, state)
    assert m.rate == pytest.approx((1.0 + 0.5) / 2)
    assert "live-b" in m.failing
