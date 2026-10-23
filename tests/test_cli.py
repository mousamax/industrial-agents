from app.cli import _parse_input
from app.evals.runner import safe_filename


def test_plain_text_that_looks_like_json_stays_text():
    assert _parse_input("2024") == "2024"
    assert _parse_input("true") == "true"
    assert _parse_input('{"document": "x"}') == {"document": "x"}
    assert _parse_input("{not json") == "{not json"


def test_model_ids_make_flat_report_filenames():
    assert "/" not in safe_filename("openrouter:anthropic/claude-haiku-5.5")
