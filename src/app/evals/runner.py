"""Run an agent's or flow's `evals.yaml`, compare to the committed baseline, keep a local history.

ia eval supplier_quote                          # run, print table vs baseline
ia eval supplier_quote --model balanced --repeat 3
ia eval supplier_quote --case usd --label shorter-prompt
ia eval supplier_quote --save-baseline          # you improved it: commit evals/baselines/
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai.models import infer_model
from pydantic_evals import Dataset
from pydantic_evals.evaluators.llm_as_a_judge import set_default_judge_model
from pydantic_evals.reporting import EvaluationReport, EvaluationReportAdapter

from app import observability
from app.core import tool_descriptions
from app.evals import coding
from app.evals.evaluators import CUSTOM_EVALUATORS
from app.models import model as resolve_model
from app.registry import Entry, get
from app.settings import ROOT, get_settings

REPORTS = ROOT / "evals" / "reports"
BASELINES = ROOT / "evals" / "baselines"


def load_dataset(entry: Entry) -> Dataset[Any, Any, Any]:
    if not entry.evals_path.exists():
        raise FileNotFoundError(f"{entry.evals_path} not found; copy one from agents/_template")
    return Dataset[entry.card.input_type, Any, Any].from_file(  # type: ignore[name-defined]
        entry.evals_path, custom_evaluator_types=CUSTOM_EVALUATORS
    )


def codebook_path(entry: Entry) -> Path:
    return entry.path / "failure_modes.yaml"


def latest_report(name: str) -> Path:
    runs = sorted((REPORTS / name).glob("*.json"))
    if not runs:
        raise FileNotFoundError(f"no saved runs for {name}; run `ia eval {name}` first")
    return runs[-1]


def safe_filename(label: str) -> str:
    """Model ids like `openrouter:anthropic/claude-haiku-5.5` must not create subfolders."""
    return re.sub(r"[^\w.-]+", "_", label)


def baseline_path(name: str) -> Path:
    return BASELINES / f"{name}.json"


def load_report(path: Path) -> EvaluationReport[Any, Any, Any] | None:
    return EvaluationReportAdapter.validate_json(path.read_bytes()) if path.exists() else None


def save_report(report: EvaluationReport[Any, Any, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(EvaluationReportAdapter.dump_json(report, indent=2))
    return path


def assertion_pass_rate(report: EvaluationReport[Any, Any, Any]) -> float:
    results = [bool(a.value) for case in report.cases for a in case.assertions.values()]
    failed_cases = len(report.failures)
    total = len(results) + failed_cases
    return sum(results) / total if total else 0.0


async def run_eval(
    name: str,
    *,
    model: str | None = None,
    label: str | None = None,
    case: str | None = None,
    repeat: int = 1,
    concurrency: int = 4,
    save_baseline: bool = False,
    closed_coding: bool = True,
    verbose: bool = False,
) -> EvaluationReport[Any, Any, Any]:
    observability.setup()
    set_default_judge_model(infer_model(resolve_model("judge")))

    entry = get(name)
    dataset = load_dataset(entry)
    if case:
        dataset.cases = [c for c in dataset.cases if case in (c.name or "")]
        if not dataset.cases:
            raise ValueError(f"no case in {entry.evals_path.name} matches {case!r}")

    codebook = codebook_path(entry)
    if closed_coding and codebook.exists():
        dataset.add_evaluator(coding.ClosedCoding(codebook=str(codebook)))

    resolved = resolve_model(model) if model else None

    label = label or (model or "default")

    async def task(inputs: Any) -> Any:
        with observability.entrypoint(name, kind="eval", tags=[f"label:{label}"]):
            return await entry.card.run(inputs, model=resolved)

    report = await dataset.evaluate(
        task,
        name=f"{name}:{label}",
        task_name=name,
        max_concurrency=concurrency,
        repeat=repeat,
        metadata={"model": resolved or "agent default", "label": label, "dev": get_settings().dev_name},
    )

    baseline = None if case else load_report(baseline_path(name))
    report.print(
        baseline=baseline,
        include_reasons=True,
        include_output=verbose,
        include_input=verbose,
        include_total_duration=True,
    )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    saved = save_report(report, REPORTS / name / f"{stamp}-{safe_filename(label)}.json")
    print(f"assertion pass rate: {assertion_pass_rate(report):.0%}   report: {saved.relative_to(ROOT)}")
    if save_baseline:
        print(f"baseline saved: {save_report(report, baseline_path(name)).relative_to(ROOT)} (commit it)")
    return report


async def analyze(name: str, report_path: Path | None = None) -> Path:
    """Open-code one eval run, then group the notes into a failure-mode codebook."""
    observability.setup()
    entry = get(name)
    report_path = report_path or latest_report(name)
    report = load_report(report_path)
    assert report is not None
    task = entry.card.description
    if tools := tool_descriptions(entry.card):
        task += f"\n\nThe agent can call these tools: {json.dumps(tools)}"
    for prompt in sorted(entry.path.glob("*prompt*.md")):
        task += f"\n\n{prompt.name}:\n{prompt.read_text()}"

    codes = await coding.open_code(report, task)
    notes_path = report_path.with_suffix(".open_codes.yaml")
    notes_path.write_text(yaml.safe_dump([c.model_dump() for c in codes], sort_keys=False, width=110))
    bad = [c for c in codes if not c.ok]
    print(f"open coding: {len(bad)}/{len(codes)} outputs have problems -> {notes_path.relative_to(ROOT)}")
    for c in bad:
        for note in c.notes:
            print(f"  [{c.case}] {note}")
    if not bad:
        return notes_path

    target = codebook_path(entry)
    existing = coding.load_codebook(target) if target.exists() else None
    book = await coding.axial_code(codes, task, existing)
    out = target.with_name("failure_modes.proposed.yaml") if existing else target
    coding.save_codebook(book, out)
    print(f"\ncodebook ({len(book.failure_modes)} failure modes) -> {out.relative_to(ROOT)}")
    for m in book.failure_modes:
        print(f"  {m.name}: {m.definition}")
    print("review/edit it; from now on `ia eval` labels every output with these modes (closed coding)")
    return out
