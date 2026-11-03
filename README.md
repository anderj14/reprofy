# reprofy
AI agent that turns plain-language bug reports into tested fixes

Under development. The CLI supports project configuration, persistent bug reports, model-backed reproduction and patch proposals, and human-approved application. Fixes are tested in temporary copies. Automatic semantic failure-cause acceptance and reproduction repair retries are still being built; review whether the generated test represents the intended bug before approving a patch.

## Initialize configuration

```bash
python -m reprofy init --project examples/shop --source-dir shop --backend meta
python -m reprofy config --project examples/shop
```

Initialization creates `reprofy.yaml`, local state directories and a `.reprofy/` ignore rule without overwriting an existing configuration. Use `--backend baseten` to initialize all three model steps for Baseten. Each step records a `model_env` reference such as `META_MODEL` or `BASETEN_MODEL`; the model value and API credentials stay in the environment or your local `.env`. Initialization and config validation do not load credentials or call providers.

The config includes Python/pytest command arguments, source directories, test directory, attempt/timeout limits, and per-step backend, model-variable reference, reasoning effort, and token budget. `config` validates and displays the YAML. The product `fix BUG-ID` command uses these settings; the older development probes retain their explicit shop inputs.

## Record and inspect bugs

```bash
python -m reprofy report "The last product page repeats an item" --expected "Each product appears once across pages" --project examples/shop
python -m reprofy list --project examples/shop
python -m reprofy show BUG-001 --project examples/shop
python -m reprofy list --project examples/shop --status new --json
```

These commands require an initialized project and make no model calls. Reports are stored as local YAML files under `.reprofy/bugs/`. Optional `--expected`, `--actual`, and `--context` fields preserve details supplied by the reporter. `report`, `list`, and `show` support JSON output. The store validates records, uses atomic writes and revision checks, and reports corrupt records rather than silently skipping them. An interrupted writer can leave a `.store.lock` requiring inspection before retrying.

## Generate a proposal for a recorded bug

```bash
python -m reprofy fix BUG-001 --project examples/shop --env-file .env
python -m reprofy show BUG-001 --project examples/shop
python -m reprofy apply BUG-001 --project examples/shop --check
python -m reprofy apply BUG-001 --project examples/shop
```

`fix` consumes provider credits and requires a new bug with an explicit expected behavior. If the report omitted it, supply `--expected "..."` to fix. Each step uses its configured backend/model environment reference. An explicit `--env-file` selects credentials; otherwise the project `.env`, then the working-directory `.env`, is considered. Existing process environment variables take precedence. Keys are not copied into bug records or proposal artifacts. Contributor model variants are excluded by this project's configuration policy.

Before paid calls, the configured baseline suite must pass. Context comes from the configured source map and a bounded import-based selection of tests. A regression is saved per bug under `tests/repro/test_bug_001.py`; proposals and execution evidence live under `.reprofy/proposals/BUG-001/RUN_ID/`. Source changes stay in copies until approved. `fix --json` returns structured output with progress on stderr. Completed usage records include bug, step and run identifiers.

`fix_verified` means the generated patch passed the reproduction and configured suite. The stored reproduction's `fails_as_expected` flag remains false until a human approves source and test together. The actual failure cause still requires review. Successful apply records `applied`; rejection records `rejected`. If filesystem application succeeds but bug bookkeeping fails, the CLI reports a warning instead of claiming the source was unchanged.

Process and apply one bug at a time in this version. Applying another proposal can invalidate outstanding snapshots. Failed processing is retained as `failed`; inspect its artifacts and create a new report for another attempt. A red baseline leaves the bug `new` so it can be retried after the baseline is repaired. The older path-based apply interface remains available for proposals without a bug record.

## Review and apply

From the repository root, with the virtual environment active:

```bash
python scripts/fix_test.py meta
python -m reprofy apply .reprofy/proposals/RUN_ID/apply.json --project examples/shop --check
python -m reprofy apply .reprofy/proposals/RUN_ID/apply.json --project examples/shop
```

Replace `RUN_ID` with the proposal directory printed by the development script. The generation script uses the backend's model from `.env` and consumes API credits; review/apply use local tests only. Installing with `pip install -e '.[dev]'` also provides the `reprofy apply` entry point.

`--check` previews the source and regression-test diff and reruns verification in a temporary copy. Interactive apply requires typing `APPLY` after reviewing the requirement and diff. Source, tests, or pytest configuration changed since verification invalidate the proposal. The command refuses to overwrite an existing regression test or apply to a different project directory.

Apply stages both files and restores written changes on ordinary write failures. If recovery fails or a concurrent edit prevents restoration, backups are retained under the target project's `.reprofy/`; an abrupt process/machine failure can also require manual recovery. Multiple file replacements are not one atomic filesystem transaction. Temporary copies are not a security sandbox.
