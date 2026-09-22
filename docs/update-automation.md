# Upstream updates and consumer reports

## Scope and boundaries

The automation has three separate responsibilities:

1. `update-llama-cpp.yml` resolves an upstream revision, proposes the submodule
   gitlink and `config/toolchain.json` change, and dispatches the existing build.
2. `build.yml` retains its ten profile/variant builds, tests, package validation,
   and append-only artifact publication. After publication, it generates the
   consumer report with npm-resolved lock data.
3. `runtime-comments.yml` reports the latest source-bound state on open,
   same-repository PRs. It can run again without compiling or publishing again.

The runtime package, exported interfaces, manifest format, and profile policy do
not acquire consumer-specific inputs. No consumer repository is cloned or edited.
Consumer knowledge exists only in the reporting generator, with its intentional
policy exception documented immediately above that implementation. The generated
YAML describes artifact facts, correspondences and historical search hints; it
is not an instruction prompt for an assistant.

## One-time repository setup

The workflow files **and their Python helpers must be on the default branch**.
GitHub only discovers `workflow_dispatch` and `workflow_run` triggers there. The
source branch selected in the updater's **Run workflow** menu must also contain
this implementation. This is a one-time installation requirement; subsequent
upstream-update PRs do not need to be merged before their artifacts are usable.

In **Settings > Actions > General > Workflow permissions**, enable **Allow GitHub
Actions to create and approve pull requests**. Repository/organization rules must
permit the declared job permissions and creation of the `automation/llama-cpp/*`
branches. Artifact branch protection must continue permitting the existing
publisher. No personal access token, GitHub App, auto-approval or auto-merge is
introduced. The updater requests `contents: write`, `pull-requests: write` and
`actions: write`; the reporter requests only content/action reads and PR writes.

Public GitHub artifact/raw URLs are provided in the consumer metadata. Private
forks need their own authorized retrieval path; a repository-scoped token cannot
be assumed to grant cross-repository package access.

## Browser-only upstream update

Open **Actions > Update llama.cpp > Run workflow** and choose the source base
branch. Inputs have these exact semantics:

| Input | Meaning |
| --- | --- |
| `target: latest` | GitHub's latest published full release; additionally required to be a non-draft, non-prerelease `vX.Y.Z` tag. |
| `target: latest-unstable` | First published `bNNNN` pre-release in GitHub's newest-first release-list order, following pagination. It is not `master` HEAD. |
| `target: custom`, `custom_ref: ...` | An upstream tag, branch, or full lowercase 40-character commit. `refs/tags/...` and `refs/heads/...` disambiguate colliding names. `master` is available only as an explicit custom ref. |
| `allow_non_fast_forward` | Explicit consent for a target older than or divergent from the existing upstream pin, including a switch back from nightly to stable. Defaults to false. |

Release status and tag shape are both checked. There is no fallback from stable
to nightly, from missing releases to branches, or from a failed lookup to cached
pins. Named refs (including annotated tags) resolve to a full commit **once**;
only that commit is fetched and recorded. A ref name is a locator, not a claim
that the upstream tag is immutable.

The source checkout must be clean and its two existing pins must agree. A no-op
update creates no commit, PR or build. Otherwise only these paths are staged:

- `vendor/llama.cpp` (the gitlink, not patched vendor source)
- `config/toolchain.json` (only `llamaCommit` changes)

The current overlay is applied to a temporary copy as a cheap preflight. Passing
this check means patch application passed, **not** that compilation, browser
execution, or model inference passed. A failed preflight still creates a **draft
PR**, including diagnostics, but does not launch ten predictably failing builds.
The patch is not fuzzed, dropped, or rewritten automatically. A human repair push
to that PR branch takes the ordinary build path.

Candidate branch names contain the base branch identity, base source commit and
upstream target commit. A rerun reuses an open candidate and preserves any manual
repair commits on that branch. It never rebases or force-pushes. Different pins
on an existing candidate are rejected; closed PRs are not reopened. A changed
base commit produces a new candidate rather than overwriting an earlier one.
Concurrent conflicting branch creation fails safely and can be rerun. No build
concurrency group cancels existing publication work.

A PR-permission or dispatch failure can occur after the branch already exists.
The updater keeps partial identity information in its summary and
`upstream-update-report` artifact. Rerunning the same input can recover the
branch/PR without replacing human changes.

## Why the build is explicitly dispatched

A `GITHUB_TOKEN` push does not trigger another `push` workflow. The updater
therefore creates the PR first, then calls `build.yml` with the branch ref and an
`expected_source` input. GitHub resolves the branch for the new run; the test job
rejects a different event SHA so a race cannot publish the wrong requested
source. Ordinary human pushes keep their existing behavior.

The build matrix and aggregate `build` job are unchanged. A successful publish
produces an immutable artifact commit **before merge**. The `artifacts` tip may
belong to any successfully built source branch and is not a stable-channel alias.
Consumers pin the reported artifact commit, not `artifacts` HEAD or the upstream
commit.

## What the single YAML block contains

After the publisher reports its commit, a temporary dependency-free npm consumer
resolves that exact Git dependency using `--package-lock-only --ignore-scripts
--no-audit --no-fund --lockfile-version=3`. The complete generated package entry is
retained, including `integrity` when npm supplies it. Node/npm versions and lock
format are recorded. Unexpected transitive dependencies, install hooks, a changed
specifier, repository, version or commit cause an error rather than an incomplete
lock fragment. The consumer's other dependencies and root package metadata are
not represented and cannot be replaced by this isolated fixture's complete lock.

The report combines:

- Distinct artifact, source, and upstream commits; manifest format, bytes and
  SHA-256; commit-pinned raw and archive locators.
- Every browser profile's `core.mjs`, `core.wasm` and type declaration file
  identity, plus shared interface files. Values come from the verified manifest,
  not duplicated handwritten hash tables.
- Consumer dependency/lock entry correspondences, reviewed-generated-code pins,
  standalone Wasm pins, conditional profile/schema/binding compatibility, relevant
  tests, and an example of an unrelated fixed capability-probe hash. Historical
  paths and symbols are explicitly search hints, not a live consumer scan or a
  claim that those locations remain current.
- The upstream overlay's pristine source digest, patch digest, reconstructed
  patched-source digest, helper/hook/doc identities, and activation recorded in
  each compiled profile/variant. Every other `patches/**/*.patch` is inventoried
  as unclassified rather than silently omitted. Toolchain patching is separately
  identified as not being a llama.cpp source patch.
- Actual validation scope from the manifest. Synthetic CPU model checks and
  mocked GPU suspension are not relabelled as real GPU/model inference.

The overlay inventory is deliberately scoped. When overlay mechanisms change,
`upstream_provenance.py` needs corresponding maintenance. A new ad-hoc compiler
transformation elsewhere in the repository is not automatically understood by
this report. Its source commit and raw locators remain available for investigation.

The reporting checkout must match the built source and upstream commits, and its
toolchain must match each variant's provenance. The report does not alter the
package or embed its own artifact commit in that package, avoiding a self-hash
cycle. Metadata and its integrity envelope are separate Actions artifacts.

A report digest identifies bytes, not compatibility approval. In particular,
generated-source adapter hashes are review gates; changing their values alone
does not establish that an exact-source replacement still has the intended
meaning. Upstream API/template/model changes can require real integration code
changes that this automation cannot infer from the hash table.

## Retrieval without reinstalling all dependencies

The metadata makes an updated, large `node_modules` archive optional for this
specific package. It supplies the commit and manifest identities needed to obtain
and verify only the runtime package independently of the consumer's other
installed dependencies. A complete package includes both variants, interfaces,
examples and notices; a few downloaded `core.mjs` files are review inputs, not a
complete installation.

GitHub-rendered/raw text viewed through a text tool is not proof of byte-exact
retrieval. Download availability, size limits and the assistant's tools remain
external constraints. File hashes certify bytes **after** retrieval; they cannot
recreate unavailable bytes. Likewise, an old installation of other consumer
dependencies can limit local tests. The YAML alone supports identifying pin
changes, but does not certify an end-to-end consumer build without its inputs.
Archive container bytes are not pinned: the extracted manifest and payload digests
are authoritative. Source archives also omit submodule contents, whose upstream
commit is separately provided.

## Comment lifecycle and security

Actions logs and the step summary receive the same generated Markdown. The PR
comment wraps that body in a source-aware status header. The installation command
is immediately visible; the single English YAML block is inside `<details>`.

The reporter operates on default-branch code, never PR-head code. It does not
install PR dependencies, evaluate YAML, execute uploaded scripts, or extract an
archive into its checkout. Only a bounded archive containing three named report
files is read in memory. The envelope must match the repository, run ID, rerun
attempt and source SHA. Download redirects receive no GitHub authorization header.
Fork PRs and non-source build events are excluded.

`workflow_run` requested/in-progress/completed events cover normal builds and
reruns. `pull_request_target` opened/reopened/synchronize events handle PRs opened
**after** a build, and refresh the status for changed heads without executing their
code. Only comments authored by `github-actions[bot]` with the private report
marker are edited. A matching user comment is not overwritten.

Every invocation reconciles open same-repository PRs under one reporter-only
concurrency group. Pending-event coalescing therefore does not discard another
PR's update. Event payloads are not used as the latest-state authority: the current
PR head and matching newest build attempt are queried again before a write. Run
start time accounts for reruns retaining an old run ID; completion order does not
make a slow older build newer. Matching runs are paginated up to GitHub's filtered
search limit; an incomplete history is rejected rather than silently selecting
from a partial page. GitHub has no compare-and-swap comment API, so a
head change after the final read can still briefly precede reconciliation by its
next event. The displayed source identity makes that boundary explicit.

A changed head, pending run, failed run or unavailable report never inherits an
unqualified success claim. Previously posted successful metadata is retained
inside a section labelled **not confirmation of the current head/run**. An older
completed event cannot blindly replace the current head's status.

The small report artifact is named per rerun attempt and requests 90-day retention
(subject to repository limits). Existing PR comments persist independently of
artifact expiry. A much later PR may not be able to recover an expired report;
that state is explicit. A future oversized report fails rather than truncating
its YAML.

## Failure recovery and non-goals

| Failure | Result / recovery |
| --- | --- |
| No valid stable/nightly release | Failed updater with no implicit channel change. |
| Overlay conflict | Draft candidate PR, diagnostic report, no automatic heavy build. |
| Wrapper/API/compiler incompatibility | Existing build fails; no runtime package is published. Manual source repair is needed. |
| PR creation or dispatch denied | Already-created branch/PR remains; summary retains its identity. Correct permissions and rerun the updater. |
| npm resolution/report generation fails after publication | Published artifact remains valid and its commit/install command remains in the publisher summary. Publication is not rolled back; the new consumer report is incomplete. |
| Comment API failure | Published artifact and report remain; rerun **Report runtime artifacts on PRs** without rebuilding. |
| Metadata retention expired | Reporter states it cannot recover that report. A publication/report job rerun is possible without rerunning successful compile jobs, but may append a new artifact commit. |

This is half-automation, not an auto-merge bot or a general source migration
engine. It does not modify the consumer, automatically approve new generated-code
adapters, promise downloaded bytes are available to every assistant, or claim
real GPU inference succeeded when only compile/mock checks ran.

## Local verification

The new Python tests use fake API responses, bounded ZIP fixtures, npm subprocess
fixtures and real **local** Git repositories. They cover both-pin updates,
channel/ref validation, conflict drafts, no-op/retry behavior, preserved manual
commits, npm lock constraints, actual overlay hashing, comment freshness and
archive safety. A YAML-parser round trip is additionally performed when PyYAML
is available; no third-party Python package is required by the automation.

Representative focused commands:

```sh
python3 -m unittest discover -s tests -p test_update_automation.py
python3 -m unittest discover -s tests -p test_consumer_metadata.py
python3 -m unittest discover -s tests -p test_runtime_comments.py
python3 -m unittest discover -s tests -p test_automation_workflows.py
```

These tests do not replace the first live GitHub dispatch, token/ruleset checks,
public npm Git resolution, or the existing WebAssembly build/browser validation.
