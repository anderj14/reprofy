# Reprofy design

**Status:** Working draft. Scope and decisions marked PROPOSED remain subject to Anderson's approval.

## Goal

Reprofy helps small teams without dedicated QA turn a bug report written in ordinary language into a reproducible, verified fix. A report is classified, the agent writes a pytest reproduction that must fail for a project-related reason, and then proposes a patch. The reproduction and full test suite must pass against the patch before a diff is presented for human review.

Reprofy is a Python/pytest tool for one repository at a time. Its CLI is intended for developers and can also emit structured JSON for use by other coding agents. It complements coding agents by accepting reports from non-programmers and making a failing reproduction test a required part of its workflow; it does not claim to replace those agents.

The project is being built during the Meta Global AI Developer Hackathon. It must use Meta models. The planned hosted backend is Meta Model API; Muse Spark Open served through Baseten is the planned open-weights mode. BugBoard v1 is earlier inspiration only: no BugBoard code is included.

## User flow

1. `reprofy init` creates project configuration and local state.
2. `reprofy report "..."` records a natural-language report.
3. `reprofy list` shows recorded reports and their states.
4. `reprofy fix BUG-ID` classifies the report, writes and checks a reproduction test, proposes a patch, and verifies the reproduction plus the existing suite. It prints a diff only after successful verification. It does not modify the working project.
5. A developer reviews and approves the change, then runs `reprofy apply BUG-ID` to apply it.

**DECIDED:** Keep `fix` proposal-only and make `apply` the explicit human-approved write step. In non-interactive or `--json` mode, `fix` returns structured state and diff data without applying changes.

## Agent workflow

### 1. Classify

Build a bounded map of the repository and ask the selected model for severity (`low`, `medium`, `high`, or `critical`), affected area, suspected files, and model-reported confidence. The area is constrained to modules inferred from the repository rather than free-form model text. Confidence is informational and is not calibrated.

### 2. Gather context

Read the suspected source files and related tests, subject to configured size limits. Do not include secrets or environment files in model context. The project must not be run against employer code or client data.

### 3. Write a reproduction test

Ask the model to create a pytest test under `tests/repro/`. Run it and inspect the result. A valid reproduction fails because of an assertion or an exception in project code. Import, syntax, and collection failures do not count. Send actionable failure output back to the model and retry up to three times.

### 4. Propose a patch

The model returns search/replace blocks rather than a hand-written unified diff. Each search block must match exactly once. Reprofy constructs the diff with Python's `difflib`, making the proposed edit reviewable and anchored to exact source text.

### 5. Verify

Apply the patch in an isolated working copy, run the reproduction test, then run the configured full test command. Bound each command by a timeout. Send test failures back to the model and retry up to the configured attempt limit. A fix that cannot be verified is marked failed and is not offered as verified.

**DECIDED:** Use a temporary copy of the project for execution and verification. Permit edits only in configured `source_dirs`, and generated tests only in `tests/repro/`. This copy-based approach is a guardrail, not a security sandbox; Docker isolation is out of scope for the hackathon.

### 6. Human review and apply

Show the verified diff and test results. Only the explicit apply action writes the patch to the real project. A human remains responsible for reviewing the diff and deciding whether to apply it.

## CLI and project configuration

Planned commands:

```text
reprofy init
reprofy report "The checkout total becomes negative with a full discount"
reprofy list
reprofy fix BUG-001 [--json]
reprofy apply BUG-001
```

Project configuration lives in `reprofy.yaml` and includes language, test command, source directories, test directory, maximum patch attempts, per-command timeout, and model settings for classification, reproduction, and patch generation.

**DECIDED:** Store backend, model, reasoning effort, and maximum completion tokens separately for each model step. Begin evaluation with Muse Spark for classification and compare Spark with Muse Wonder for reproduction and patch generation. Baseten's `meta-models/muse-spark-open-1026` is the planned partner-hosted open-weights backend. This can qualify for the open-weights bonus; it is not deployment on the user's own hardware. Defaults remain subject to measured reliability, latency, and cost.

## Components and data

- **Model interface:** a shared completion interface returning content, optional reasoning, finish reason, token counts, backend/model identity, and latency.
- **OpenAI-compatible adapter:** used for Meta Model API and Baseten with their configured base URLs and credentials.
- **Fake backend:** deterministic responses for local tests without API calls or token cost.
- **Structured output:** request JSON Schema where supported, then validate with Pydantic. Retry once on invalid output. Treat a `length` finish as suspect and retry with a larger output budget.
- **Usage log:** append backend-reported token usage and request metadata to `.reprofy/usage.jsonl`; never log API keys.
- **Bug store:** one YAML file per bug under `.reprofy/bugs/`, accessed through a small `BugStore` interface (`get`, `save`, `list`).
- **Patch representation:** exact search/replace operations; Reprofy renders the unified diff.

Each bug record contains an ID, creation time, state, report (`text`, optional expected/actual behavior and context), classification, reproduction-test metadata, and fix-attempt/diff/verification metadata. The state progression is:

```text
new -> classified -> repro_written -> fix_proposed -> fix_verified -> applied
  \         \              \                \                \
   +---------+--------------+----------------+-----------------> failed
                                      +------------------------> rejected
```

`rejected` records a human declining a proposed fix. `failed` records a workflow that could not produce a valid reproduction or verified patch within its retry limits.

## Demo fixture

The repository will include a small shop application created during the hackathon, with synthetic data, passing baseline tests, and five seeded reports:

1. A full-discount coupon produces a negative total.
2. The last pagination page repeats an item.
3. Floating-point tax rounding changes the total by one cent.
4. Registration treats uppercase and lowercase email addresses as distinct.
5. A deliberately difficult issue spanning files or depending on hard-to-reproduce state, expected to demonstrate an honest failure after retries.

At least one report will be in Spanish. The demo and README will identify the fixture as synthetic and event-built.

## Scope and non-goals

**DECIDED:** Python and pytest only; library plus CLI first; JSON output for agent integration; YAML persistence behind a storage interface; search/replace patch format; the shop fixture and five reports.

**DECIDED:** Do not include other languages, MCP/editor plugins, a Docker sandbox, duplicate detection, screenshots, a watch command, or SQLite unless time remains. A one-page FastAPI interface is conditional on the full CLI loop working by the Sunday checkpoint. If the checkpoint is missed, focus on the core loop and polish.

The project is not a general coding agent, a secure execution sandbox, a calibrated severity/confidence service, or a guarantee that generated tests capture the user's intent. It is not a basic retrieval application: it inspects and exercises a local codebase to establish a regression test and verify a patch.

## Risks and mitigations

- **Generated reproduction tests may fail for the wrong reason.** Parse pytest outcomes, reject collection/import/syntax failures, provide bounded retries, and demonstrate an honest failure case.
- **Generated code is executed locally.** Use a temporary copy and timeouts if approved; clearly disclose that this is not a real sandbox.
- **Model output may be malformed or misapplied.** Validate structured data, require exact unique search matches, generate diffs locally, and run tests before presenting a fix.
- **Model confidence is not calibrated.** Label it model-reported confidence and do not present it as a probability of correctness.
- **Reasoning tokens, latency, and API credit are limited.** Set explicit token budgets, record usage, and compare backends on the same demo reports.
- **Schedule is tight.** The full CLI loop is the checkpoint; web UI and evaluation extras are conditional on completing it.

## Hackathon and privacy constraints

All submitted code and functionality must be built during the event. The demo must show event-built work, and BugBoard v1 must not be copied into this project. Use only synthetic demo data. Do not commit secrets, client data, or employer code. Hosted model calls may transmit included source context to the provider; users must understand that before using the hosted backend. Recheck the official hackathon rules if an uncertain requirement arises.

## Demo success criteria

The three-minute demo should show a vague report becoming a valid failing reproduction, a proposed fix, a passing reproduction and suite, and a human-approved apply. It should also show the hard case exhausting retries and explain the recovery/failed state honestly. Name the Meta model backends used. An evaluation script may report solved cases, attempts, token usage, and cost for Meta Model API Muse Spark and Baseten Muse Spark Open if the core loop is ready.
