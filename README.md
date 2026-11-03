# reprofy
AI agent that turns plain-language bug reports into tested fixes

Under development. The development workflow classifies a report, generates a reproduction, proposes a source patch, and verifies the reproduction and suite in temporary project copies. The initial CLI supports reviewing and applying structured proposals with interactive approval. Automatic failure-cause acceptance, bug persistence, and the remaining product commands are still being built.

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
