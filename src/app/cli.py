"""`ia`: the team's command line. `ia --help` for everything."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from app import observability
from app.core import AgentCard
from app.models import model as resolve_model
from app.registry import get, registry
from app.settings import ROOT

cli = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()
AGENTS_DIR = Path(__file__).parent / "agents"


def _parse_input(raw: str) -> Any:
    """JSON, `@path/to/file` (its text becomes the input), or a plain string."""
    if raw.startswith("@"):
        return Path(raw[1:]).read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@cli.command("list")
def list_() -> None:
    """Every agent and flow in the repo."""
    table = Table("name", "kind", "description")
    for name, entry in registry().items():
        table.add_row(name, entry.kind, entry.card.description)
    console.print(table)


@cli.command()
def run(
    name: str,
    input: Annotated[str, typer.Argument(help="JSON, @file, or text. Text fills the first input field.")],
    model: Annotated[str | None, typer.Option(help="alias (smart/balanced/fast) or model id")] = None,
) -> None:
    """Run an agent/flow once and print the output."""
    observability.setup()
    card = get(name).card
    inputs = _parse_input(input)
    if isinstance(inputs, str) and card.input_type is not str:
        first_field = next(iter(card.input_type.model_fields))  # type: ignore[union-attr]
        inputs = {first_field: inputs}
    output = asyncio.run(card.run(inputs, model=resolve_model(model) if model else None))
    console.print_json(output.model_dump_json() if isinstance(output, BaseModel) else json.dumps(output))


@cli.command()
def chat(name: str) -> None:
    """Interactive terminal chat with an agent."""
    observability.setup()
    card = get(name).card
    if not isinstance(card, AgentCard):
        raise typer.BadParameter(f"{name} is a flow; use `ia run`")
    card.agent.to_cli_sync(deps=card.deps() if card.deps else None)


@cli.command()
def ui(name: str, port: int = 8001) -> None:
    """Browser chat UI for one agent (fastest demo)."""
    import uvicorn

    observability.setup()
    card = get(name).card
    if not isinstance(card, AgentCard):
        raise typer.BadParameter(f"{name} is a flow; use `ia run {name}` or POST /run/{name}")
    uvicorn.run(card.agent.to_web(deps=card.deps() if card.deps else None), port=port)


@cli.command("eval")
def eval_(
    name: str,
    model: Annotated[str | None, typer.Option(help="alias or model id; default: the agent's own")] = None,
    label: Annotated[str | None, typer.Option(help="name this experiment (shows in Logfire)")] = None,
    case: Annotated[str | None, typer.Option(help="only cases whose name contains this")] = None,
    repeat: Annotated[int, typer.Option(help="run each case N times (LLMs are noisy)")] = 1,
    concurrency: int = 4,
    save_baseline: Annotated[bool, typer.Option(help="store as the baseline to compare against")] = False,
    verbose: Annotated[bool, typer.Option("-v", help="show inputs and outputs")] = False,
    min_pass: Annotated[float, typer.Option(help="exit 1 below this assertion pass rate")] = 0.0,
) -> None:
    """Run evals.yaml for an agent/flow."""
    from app.evals.runner import assertion_pass_rate, run_eval

    report = asyncio.run(
        run_eval(
            name,
            model=model,
            label=label,
            case=case,
            repeat=repeat,
            concurrency=concurrency,
            save_baseline=save_baseline,
            verbose=verbose,
        )
    )
    if assertion_pass_rate(report) < min_pass:
        raise typer.Exit(1)


@cli.command()
def analyze(
    name: str,
    report: Annotated[Path | None, typer.Option(help="saved run to analyze; default: latest")] = None,
) -> None:
    """Open coding of an eval run, grouped into failure_modes.yaml (used for closed coding by `ia eval`)."""
    from app.evals.runner import analyze as run_analysis

    asyncio.run(run_analysis(name, report))


@cli.command("eval-gen")
def eval_gen(
    name: str,
    n: int = 5,
    hint: Annotated[str, typer.Option(help="what kinds of cases you want")] = "",
) -> None:
    """Draft N new eval cases with an LLM into evals.generated.yaml. Review before merging into evals.yaml."""
    from pydantic_ai.models import infer_model
    from pydantic_evals import Dataset
    from pydantic_evals.generation import generate_dataset

    from app.evals.evaluators import CUSTOM_EVALUATORS

    entry = get(name)
    card = entry.card
    output_type = card.agent.output_type if isinstance(card, AgentCard) else Any
    instructions = f"Cases for this agent: {card.description}\n{hint}"
    prompt_file = entry.path / "prompt.md"
    if prompt_file.exists():
        instructions += f"\n\nThe agent's prompt, for context:\n{prompt_file.read_text()}"
    path = entry.path / "evals.generated.yaml"
    asyncio.run(
        generate_dataset(
            dataset_type=Dataset[card.input_type, output_type, Any],  # type: ignore[valid-type]
            path=path,
            custom_evaluator_types=CUSTOM_EVALUATORS,
            model=infer_model(resolve_model("smart")),
            n_examples=n,
            extra_instructions=instructions,
        )
    )
    console.print(f"wrote {path.relative_to(ROOT)}: review it, then move the good cases into evals.yaml")


@cli.command("eval-schema")
def eval_schema() -> None:
    """Write evals/schema.json so editors autocomplete evals.yaml files."""
    from pydantic_evals import Dataset

    from app.evals.evaluators import CUSTOM_EVALUATORS

    schema = Dataset[Any, Any, Any].model_json_schema_with_evaluators(CUSTOM_EVALUATORS)
    path = ROOT / "evals" / "schema.json"
    path.write_text(json.dumps(schema, indent=2))
    console.print(f"wrote {path.relative_to(ROOT)}")


@cli.command()
def new(name: str) -> None:
    """Scaffold a new agent from agents/_template."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise typer.BadParameter("use snake_case, e.g. spec_checker")
    target = AGENTS_DIR / name
    if target.exists():
        raise typer.BadParameter(f"{target} already exists")
    shutil.copytree(AGENTS_DIR / "_template", target, ignore=shutil.ignore_patterns("__pycache__"))
    for f in target.iterdir():
        if f.is_file():
            f.write_text(f.read_text().replace("__NAME__", name))
    console.print(
        f"created {target.relative_to(ROOT)}: edit prompt.md, agent.py, evals.yaml; then `ia eval {name}`"
    )


def main() -> None:
    cli()
