from types import SimpleNamespace

from app.evals.evaluators import FieldsMatch


def check(expected, output):
    return FieldsMatch().evaluate(SimpleNamespace(expected_output=expected, output=output))  # type: ignore[arg-type]


def test_partial_expected_output_ignores_unlisted_fields():
    assert check({"currency": "eur"}, {"currency": "EUR", "rationale": "whatever"}).value


def test_numbers_use_relative_tolerance():
    assert check({"total": 100.0}, {"total": 100.5}).value
    assert not check({"total": 100.0}, {"total": 103}).value


def test_lists_match_by_position_and_report_the_path():
    result = check({"items": [{"qty": 1}, {"qty": 2}]}, {"items": [{"qty": 1}, {"qty": 3}]})
    assert not result.value and "items[1].qty" in result.reason
