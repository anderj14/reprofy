# Reprofy

**Turn a vague bug report into a reviewed, test-backed fix.**

Reprofy is an experimental Python CLI for small teams without dedicated QA. It uses a model to classify a report, write a regression test, explain the observed failure, and propose a source diff. Pytest checks the proposal in a disposable project copy. A developer reviews and explicitly approves the source change and new test before Reprofy applies them.

The demo store is synthetic. It contains no customer, order, or production data. Reprofy is an independent hackathon project; an earlier BugBoard prototype inspired the initial triage idea, but this repository is not affiliated with or integrated into BugBoard.

## What works today

- `init` and `config` create and validate project settings.
- `report`, `list`, and `show` persist and inspect bug reports in local YAML.
- `delete BUG-ID` removes only the record of an applied bug, after exact-ID confirmation; source changes and regression tests remain.
- `fix BUG-ID` classifies a report, generates a reproduction, checks execution evidence, asks the configured repro model to review its cause, then proposes a source patch.
- Invalid or semantically rejected reproductions can receive feedback and be regenerated up to three times.
- `apply BUG-ID --check` reviews and re-verifies without writing. Interactive `apply` writes only after the developer types `APPLY`.
- Meta Model API and Baseten OpenAI-compatible endpoints are supported. Models stay configurable through environment references.

`fix_verified` means the proposed patch made the generated test and configured suite pass in a temporary copy. It does not apply the patch or replace human review.

## Quick start

Requires Python 3.10 or newer and a Meta Model API or Baseten API key.

```bash
git clone https://github.com/anderj14/reprofy.git
cd reprofy
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Add the credentials for the provider you want to use to `.env`. Keep that file private; it is ignored by Git. The example file contains the variable names and default model identifiers:

| Provider | Credential | Model variable | Example model |
| --- | --- | --- | --- |
| Meta | `MODEL_API_KEY` | `META_MODEL` | `muse-spark-1.3` |
| Baseten | `BASETEN_API_KEY` | `BASETEN_MODEL` | `meta-models/muse-spark-open-1026` |

The base URL variables `META_BASE_URL` and `BASETEN_BASE_URL` have defaults in `.env.example`. Do not paste credentials into reports, YAML, or bug artifacts.

### Install `reprofy` as a global terminal command

The package registers a `reprofy` command. To use it from any project directory without activating this repository's virtual environment, install it with `pipx`:

```bash
# macOS
brew install pipx
pipx ensurepath

# From the cloned Reprofy repository
pipx install --editable .
reprofy --help
```

The editable install points to this checkout, so keep the repository at its current path. The `.venv` install above is still useful for running the test suite and helper scripts.

## Try the complete flow

### Full local demo: app → Reprofy API → CLI

The demo runs the storefront and report intake API as separate Docker services on the developer's computer. Reports are stored in the mounted example project, so the host CLI can inspect and process them. The Meta/Baseten model key remains in the developer's host `.env`; it is never sent to the browser or containers.

```bash
cp .env.docker.example .env.docker
openssl rand -hex 32
# Put the generated value in .env.docker as REPROFY_INGEST_TOKEN
docker compose --env-file .env.docker up --build
```

Open the Reprofy CLI menu and switch the project to **automatic** mode, then submit a bug from **Report an issue** at `http://127.0.0.1:8000`. The worker processes it automatically. In **manual** mode, use **Generate a fix proposal manually** for each report. In another terminal, check progress and usage:

```bash
reprofy list --project examples/shop
reprofy show BUG-ID --project examples/shop
```

Automatic mode is off by default and is saved locally in `.reprofy/settings.yaml`. The repository must have a clean Git working tree before the worker can start. If an infrastructure issue puts a report in `needs_human_review`, fix the issue and choose **Retry a report awaiting human review** from the CLI menu. When a report reaches `branch_ready`, inspect its local branch and merge it manually. Reprofy never pushes or merges automatically. Stop the services with `docker compose --env-file .env.docker down`. More demo scenarios are documented in [examples/shop/README.md](examples/shop/README.md).

### CLI-only walkthrough

This creates a new disposable shop project with the seeded pagination bug and one report. It makes no model calls and refuses to overwrite an existing path.

```bash
python scripts/prepare_demo.py --project /tmp/reprofy-demo
python -m reprofy config --project /tmp/reprofy-demo
python -m reprofy list --project /tmp/reprofy-demo
```

Generate a proposal with Meta (or replace `meta` with `baseten` when the matching variables are configured):

```bash
python -m reprofy fix BUG-001 --project /tmp/reprofy-demo --env-file .env
python -m reprofy show BUG-001 --project /tmp/reprofy-demo
```

Read the expected behavior, generated reproduction, cause review, pytest output, and diff in the printed `ARTIFACTS` directory. Then preview the current proposal:

```bash
python -m reprofy apply BUG-001 --project /tmp/reprofy-demo --check
```

If the proposal still matches what you reviewed, run `apply` and type `APPLY` at the prompt:

```bash
python -m reprofy apply BUG-001 --project /tmp/reprofy-demo
```

The initial model-backed `fix` uses provider credits. Setup, report/list/show, `--check`, and the final apply verification run locally. Remove `/tmp/reprofy-demo` when finished.

## Configure your own Python project

From the project directory, initialize Reprofy with the source and test paths that your repository uses:

```bash
reprofy init --source-dir src --tests-dir tests --backend meta
reprofy config
reprofy report "Checkout becomes negative with a coupon" \
  --expected "Reject coupon percentages above 100 with ValueError; 100 is valid"
reprofy list
reprofy show BUG-001
reprofy fix BUG-001 --env-file .env
reprofy apply BUG-001 --check
reprofy apply BUG-001
```

Edit `reprofy.yaml` to select backend, model environment variable, reasoning effort, token budget, pytest command, timeout, and patch-attempt limit for `classify`, `repro`, and `fix`. The semantic review uses the configured `repro` model. The values for model names and credentials are read from the process environment or selected `.env`; they are never copied to the YAML configuration.

## Evaluation snapshot

One run per case and provider on independent clean copies of the same frozen shop fixture. A case counts as resolved only when the complete workflow reaches `fix_verified`.

| Model | Verified proposals | Reproduction attempts | Patch attempts | Model calls | Prompt tokens | Completion tokens | Provider latency | Estimated list cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Meta Muse Spark 1.3 | 3/3 | 5 | 3 | 14 | 18,296 | 17,303 | 194.162 s | $0.0964 |
| Baseten Muse Spark Open | 0/3 | 9 | 0 | 13 | 14,029 | 28,832 | 93.994 s | $0.1401 |

The cases were coupon validation, duplicate pagination, and case-insensitive email uniqueness. Meta verified all three. Baseten classified all three reports, but its generated test code was flattened or syntactically invalid across the three allowed attempts per case; no patch was proposed. We kept the validator strict. This is a small first-run comparison, not a statistically meaningful model ranking.

Cost is a list-price estimate before account credits and cache discounts, using $1.25 per million input tokens and $4.25 per million output tokens for the tested models. See the [Meta pricing page](https://dev.meta.ai/docs/pricing-rate-limits) and [Baseten Muse Spark Open pricing](https://www.baseten.co/library/muse-spark-open/). The actual charge can differ. Evaluation details and raw local artifacts are written under `docs/evaluation/`, which is intentionally excluded from Git.

Re-run the matrix with your own credentials:

```bash
python scripts/evaluate_backends.py meta --env-file .env
python scripts/evaluate_backends.py baseten --env-file .env
```

Each run uses three temporary clean projects. It saves proposals and metrics but does not apply them.

## How it works

1. A report includes the developer's expected behavior.
2. A configured model classifies severity, module, and likely files.
3. Reprofy gathers bounded source/test context and asks the repro model for a regression test.
4. Pytest records collection/setup/call results, exceptions, traceback, and whether configured source files executed. Obvious shortcuts such as constant assertions and deliberate raises are rejected.
5. The repro model reviews the failing test against the report, expected behavior, source, and execution evidence. Rejected tests get feedback for up to three generation attempts.
6. A fix model proposes exact search/replace edits to allowed source files. The test and configured suite rerun in a fresh temporary copy.
7. Reprofy stores a unified diff and apply proposal. Only an explicit human approval applies the source and new regression test.

Bug data, run artifacts, and usage logs are local to each project under `.reprofy/`. Usage logs contain provider/model, token counts, latency, and step metadata; they omit prompts and completion content. Reports and code sent to hosted models are subject to the selected provider's terms and data controls.

## Scope and limitations

- Python and pytest only. The CLI processes one bug at a time.
- No embeddable widget, duplicate detection, watch mode, or local Ollama backend yet.
- Test-file selection is bounded and import-based; it is not a full dependency graph.
- The semantic reproduction review is a model judgment, not proof. Developers must inspect both the regression test and patch.
- Temporary project copies are not a security sandbox. Generated Python can access the machine's available resources; do not run this version on untrusted projects in a sensitive environment.
- Applying multiple files is not a globally atomic filesystem transaction. Reprofy validates stale proposals, attempts rollback after ordinary write errors, and retains recovery backups if rollback cannot finish.

## Development and demo

Run the automated suite:

```bash
python -m pytest -q
```

The demo shop and frozen buggy fixture are synthetic. The actual demo project's approved fixes have regression tests under `examples/shop/tests/repro/`. The benchmark fixture remains unchanged so evaluation runs can repeat the original bugs. See [DEMO.md](DEMO.md) for a timed three-minute recording outline.
