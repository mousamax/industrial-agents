from types import SimpleNamespace

from pydantic_ai.models.test import TestModel

from app.evals import coding


async def test_closed_coding_gives_one_assertion_per_failure_mode(tmp_path):
    path = tmp_path / "failure_modes.yaml"
    coding.save_codebook(
        coding.Codebook(
            task="extract quotes",
            failure_modes=[
                coding.FailureMode(name="wrong_currency", definition="Currency differs from the quote"),
                coding.FailureMode(name="missed_blocker", definition="An unapproved supplier is not flagged"),
            ],
        ),
        path,
    )
    ctx = SimpleNamespace(inputs={"document": "..."}, output={"currency": "EUR"})
    labels = coding.Labels(
        present={"wrong_currency": True}, reasons={"wrong_currency": "says EUR, quote is USD"}
    )
    with coding._closed_coder.override(model=TestModel(custom_output_args=labels.model_dump())):
        result = await coding.ClosedCoding(codebook=str(path)).evaluate(ctx)  # type: ignore[arg-type]
    assert result["no_wrong_currency"].value is False
    assert result["no_wrong_currency"].reason == "says EUR, quote is USD"
    assert result["no_missed_blocker"].value is True
