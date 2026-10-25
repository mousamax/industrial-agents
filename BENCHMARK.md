# BENCHMARK.md

Moved to [`SUBMISSION/BENCHMARK.md`](SUBMISSION/BENCHMARK.md) (the submission folder Synera grades:
`SUBMISSION/BENCHMARK.md` and `SUBMISSION/answers.json`). Short version, from the repo root:

```bash
uv sync && echo 'ANTHROPIC_API_KEY=...' > .env
uv run run_benchmark <building_dir> <questions.json> SUBMISSION/answers.json --check
```
