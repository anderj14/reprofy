# Reprofy

**Turn a vague bug report into a reviewed, test-backed fix.**

Reprofy is an experimental Python CLI for small development teams without dedicated QA staff. It takes a plain-language report, asks an AI model to understand and reproduce the problem, and proposes a source change with a regression test. Pytest checks the proposal in a disposable copy of the project. A developer reviews the test and diff and explicitly approves any change to the project.

> **Prototype status:** Reprofy currently supports Python projects that use pytest. AI-generated tests and patches can be wrong; inspect them before approval. Reprofy does not make source changes without human approval in manual mode. Automatic mode prepares a verified branch for human review and does not push or merge it on its own.

The demo shop is synthetic and contains no customer or production data. The standalone demo project is available at [reprofy-demo-shop](https://github.com/anderj14/reprofy-demo-shop). Reprofy is an independent hackathon project.

## How it works

1. A customer or developer submits a report and expected behavior.
2. A configured model classifies the issue and suggests relevant project files.
3. Reprofy generates a regression test and runs it against the current code. A model review checks whether the failure matches the report. If the configured primary model returns invalid structured output or its request times out or loses its connection during expected-behavior inference, reproduction generation, or semantic review, Reprofy can retry that step with the configured Muse Wonder fallback.
4. A model proposes a source patch. Reprofy runs the reproduction test and configured test suite against a disposable project copy.
5. In **manual mode**, a developer reviews and approves the exact patch before it is applied. Reprofy then reruns the configured suite; if it fails, the model analyzes the failure but does not edit code. In **automatic mode**, Reprofy prepares the verified result on an isolated Git branch; a developer reviews and merges it.

`fix_verified` means the generated test and configured suite passed in the disposable copy. It is evidence for review, not proof that the fix is correct.

## Try the full local demo

The Docker demo runs a synthetic storefront, a local report intake API, and a worker. Reports submitted in the storefront are saved to the example project and picked up by the worker. The model key stays in the developer's local `.env`; it is not sent to the browser.

### Requirements

- macOS, Linux, or Windows with Docker Desktop / Docker Engine and Compose
- Git
- A Meta Model API key or Baseten API key
- `pipx` to install and launch the `reprofy` CLI

Clone the project, install the CLI, and configure a provider:

```bash
git clone https://github.com/anderj14/reprofy.git
cd reprofy
brew install pipx              # macOS; see pipx docs for other platforms
pipx ensurepath
pipx install --editable .
cp .env.example .env
```

Add the credentials for the provider you want to use to `.env`. For Meta, set `MODEL_API_KEY`; for Baseten, set `BASETEN_API_KEY`. Model names and base URLs have example values in `.env.example`. Keep `.env` private; Git ignores it.

Create a separate local token for the report form and start the services:

```bash
cp .env.docker.example .env.docker
openssl rand -hex 32
# Paste the generated value into .env.docker as REPROFY_INGEST_TOKEN
docker compose --env-file .env.docker up --build
```

In another terminal, open the interactive CLI:

```bash
cd reprofy
reprofy
```

The menu starts at the repository root. Choose **[p] Choose another project folder** and enter `examples/shop`. Then choose **[8] Switch to automatic mode** and confirm. The Docker worker is already running. Automatic processing requires a clean Git working tree; commit or stash local changes before submitting the report.

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), browse the shop, and submit a report from **Report an issue**. The menu refreshes report counts while the worker processes the report. Choose **[10] View live activity · all reports** to watch detailed progress in a separate terminal. Choose **[11] Review prepared branches** to inspect the completed change.

The worker does not push or merge branches automatically. After review, the branch menu can publish a branch, create a Pull Request, or merge an approved Pull Request, with explicit confirmations. These actions require a project with its own appropriate Git remote, Git credentials, and GitHub CLI (`gh auth login`). The included `examples/shop` is inside the Reprofy checkout, so do not publish its branches as a Pull Request unless you intentionally want them in the Reprofy repository. For a separate demo repository, use [reprofy-demo-shop](https://github.com/anderj14/reprofy-demo-shop).

Stop the local services with:

```bash
docker compose --env-file .env.docker down
```

More seeded scenarios and demo notes are in [examples/shop/README.md](examples/shop/README.md). For a concise recording outline, see [DEMO.md](DEMO.md).

## Use Reprofy with a Python project

Install Reprofy and configure your provider credentials as above. From the target project's directory, open the interactive CLI:

```bash
reprofy
```

If the project has not been configured, choose **[1] Initialize this project** and enter its Python source directory, test directory, and provider. Reprofy creates `reprofy.yaml` and local state without overwriting an existing configuration. Then use the menu:

- **[1] Report a bug** to enter a report and expected behavior.
- **[2] List bug reports** or **[3] Inspect a bug** to review reports and see model-token usage by workflow step.
- In manual mode, choose **[4] Generate a fix proposal manually**, then **[5] Review and apply a proposal**. Reprofy shows the exact diff and asks for explicit approval.
- **[7] Delete a resolved bug report** removes only the applied report record; source changes and regression tests remain.
- To process reports automatically, choose **[8] Switch to automatic mode**. Start a worker in a second terminal with `reprofy worker --project .`; it allows up to 10 attempts per workflow stage by default, then prepares a verified result on an isolated branch or stops for human review.
- Use **[9] Retry a report awaiting human review** after resolving its blocker.
- Choose **[10] View live activity · all reports** to follow the worker or **[11] Review prepared branches** to inspect, publish, open a Pull Request, or merge an approved Pull Request.
- From branch review, you can also open a prepared project branch in a separate VS Code window.
- Choose **[6] Show project configuration** to review the current settings.

Edit `reprofy.yaml` to set source and test paths, the pytest command and timeout, model environment-variable names, model IDs, reasoning effort, and token limits. Configure `repro_fallback` to retry expected-behavior inference, reproduction generation, and semantic review with Muse Wonder when Muse Spark returns invalid structured output or its request fails. If Spark returns a well-formed test that does not reproduce the reported bug, Reprofy gives the repro model feedback and retries the configured workflow; that semantic rejection alone does not trigger the Wonder fallback. The fallback is not used for classification or patch generation. Credentials and model values are read from the environment or a `.env` file; they are not copied into the project configuration.

To see all commands:

```bash
reprofy --help
```

For a disposable, pre-seeded project that makes no model calls during setup, use this helper from the Reprofy checkout, then open `/tmp/reprofy-demo` from the CLI menu with **[p] Choose another project folder**:

```bash
python scripts/prepare_demo.py --project /tmp/reprofy-demo
```

## Model and evaluation notes

Reprofy supports Meta's Model API and Baseten's OpenAI-compatible Muse Spark endpoint. Models and credentials are selected through environment references; see `.env.example`. A project can configure Muse Wonder as a fallback for structured reasoning/reproduction steps when the primary model fails to produce valid output or its request times out. The fallback behavior is explicit in the project's `reprofy.yaml`.

In one initial comparison, using independent copies of three seeded bug cases:

| Model endpoint | Verified proposals | Reproduction attempts | Patch attempts | Model calls | Prompt tokens | Completion tokens | Provider latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Meta Muse Spark 1.3 | 3/3 | 5 | 3 | 14 | 18,296 | 17,303 | 194.2 s |
| Baseten Muse Spark Open | 0/3 | 9 | 0 | 13 | 14,029 | 28,832 | 94.0 s |

In these runs, Meta verified all three proposals. Baseten classified the reports, but its generated tests were malformed or flattened in the allowed attempts, so Reprofy rejected them and did not propose patches. This comparison predates the configurable Muse Wonder fallback. It is a small first-run comparison, not a statistically meaningful model ranking. Results, latency, and model behavior can vary by provider and model version. Usage is recorded locally under each project's ignored `.reprofy/` directory; logs include token counts and latency but omit prompts and completion content.

Re-run the comparison with your own credentials:

```bash
python scripts/evaluate_backends.py meta --env-file .env
python scripts/evaluate_backends.py baseten --env-file .env
```

The evaluator uses temporary clean projects and saves proposals and metrics; it does not apply fixes.

## Safety and current limits

- Python and pytest only; Reprofy handles one report at a time.
- The CLI, report API, and project data are local in this prototype. The Docker form is intended for a local demo, not exposed public hosting.
- No duplicate detection, watch mode, embeddable third-party widget, or Ollama/local-model backend yet.
- File selection uses bounded source and test context; it is not a complete dependency graph.
- Model review is fallible. Developers should inspect the reproduction test and diff.
- Disposable project copies are not a security sandbox. Generated Python can access available machine resources. Do not use this prototype on untrusted projects in a sensitive environment.
- Multi-file apply is not a globally atomic filesystem transaction. Reprofy validates stale proposals, attempts rollback after ordinary write errors, and retains recovery backups if rollback cannot finish.
- Reports and code sent to hosted models are subject to the selected provider's terms and data controls.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,webdemo]'
python -m pytest -q
```

The shop and test fixtures are synthetic. Approved example fixes have regression tests under `examples/shop/tests/repro/`; the frozen fixture under `tests/fixtures/buggy_shop/` preserves the original seeded bugs for repeatable evaluations.
