# Publication validation and packing

The runtime payload, manifests, package exports, source checks, provenance,
license checks, size guards and append-only history are unchanged. This is a
publication-pipeline optimization, not a compiler or runtime optimization.

`npm pack --dry-run --json` still compresses the package to obtain its file list.
The pipeline therefore assigns npm packing to one boundary instead of repeating
it at every payload check. No alternative packlist implementation or dependency
is introduced, and no compression settings are changed.

## Validation ownership

| Boundary in the publish job | Full payload/provenance checks | npm packing |
| --- | --- | --- |
| Inputs, assembled temporary tree and assembly destination | Every time | Deferred explicitly by `--defer-npm-pack` |
| Publisher input | Yes, before copying can dereference links | Deferred to the private snapshot |
| Publisher's private snapshot | Yes, before Git initialization or remote access | Each runtime separately, then the root package (3 calls) |
| Staged immutable Git tree | Every raw blob and root manifest, before remote access | Not repeated |
| Report input, before lock resolution | Yes | Reused only with this publication's matching manifest digest |
| Report input, after lock resolution and provenance collection | Yes again, against the same digest | Not repeated |
| End of metadata construction, before report writes | Bound metadata snapshots and another full payload check | Not repeated |

The default assembly/verification commands and each runtime's validator still
perform npm packing. The publisher has **no skip-packing option**. Standalone
report generation without a publisher digest runs all three pack checks before
resolving the lock; it revalidates the same payload afterward without compressing
it again.

The publish job previously invoked npm packing 8 times during assembly, 3 times
during publication, and 6 times during reporting. It now invokes it 0 + 3 + 0
times. This count does **not** include npm's separate work to generate the consumer
lock. Timings depend on actual payloads, runner capacity, Git transfer and npm's
network resolution, so the call-count reduction is not a wall-clock guarantee.

## Exact-tree handoff to reporting

`publish_artifacts.py` records `commit` and `manifest-sha256` in `GITHUB_OUTPUT`
only after a successful push. The digest is checked against both the private
snapshot that passed all three npm checks and the immutable Git tree actually
committed, not simply read from potentially changed files after publication. The workflow passes both outputs from that same step to the
reporter. No evidence file is added to the distributed package.

`--published-manifest-sha256` is a trusted same-job handoff, **not** a signature or
an independently verifiable proof of remote publication. Do not compute it from
an unverified package, copy it from another job or restore it from a cache. Omit
the option for independent/standalone verification.

The reporter rejects an empty, malformed or mismatching digest. A matching
manifest alone is insufficient: it still hashes and checks every payload, both
inner manifests, required files, runtime configuration, source identity and
cleanliness. The root manifest covers all files, including inner manifests.
Updating a payload and regenerating the manifests therefore also invalidates
the handed-off digest. Revalidation after lock/provenance work prevents a later
change from being silently reported as the earlier package.

The existing immutable source-commit check remains before publication. npm lock
resolution still uses npm itself with an isolated empty cache, the actual
published Git commit, and all existing lock-entry checks. Integrity values are
not reconstructed locally. A reporting failure still leaves the already
published commit and its job output intact; it never rewrites history.

## Checkout and diagnostic scope

Only the publish job changes its submodule checkout. It initializes
`llama-cpp/vendor/llama.cpp` at the source commit's gitlink, shallowly and without
recursion. Consumer provenance reads the two llama source overlays from this
checkout; image provenance comes from the downloaded, fully checked manifests.
Compilation and test jobs retain their existing recursive checkouts. No upstream
pin or patch changes.

Wasm header checks read exactly eight bytes instead of loading the entire Wasm
file to slice its first eight bytes. Full streaming SHA-256 checks are retained.

Publication stderr separates `stage-and-validate`, `git-stage-and-verify` and
`git-publish` time; reporting separates `report-input-validation` from `npm-lock-resolution`. These
logs do not alter stdout commit output, distributed files or report schemas.

## Local regression coverage

`python3 -m unittest discover -s tests -p 'test_publish_optimization.py'` exercises
real npm packing, the workflow's selective Git checkout, local bare-Git pushes,
append-only history, old-commit installation and an empty-cache `npm ci`.
Fixtures contain synthetic Wasm headers: these tests do not establish compilation
or real-model inference. GitHub lock resolution and upstream overlay collection
are mocked only in report tests; the production implementations are unchanged.

## Adversarial review of the publication boundaries

The staged filesystem is not necessarily the Git tree. Global Git ignore rules
can drop files and attributes/clean filters or line-ending normalization can
change bytes during `git add`. A file can also change after its npm check. After
`write-tree`, the publisher therefore checks the immutable tree before any remote
access: exact path set, regular-file entry types, and every blob's size/SHA-256,
including the root manifest. It uses NUL-framed `ls-tree` output and one raw
`cat-file --batch` process, with at most one MiB per payload read. On a mismatch
it fails rather than silently changing the user's Git settings. That same tree
is reused for every append-only retry, so later worktree changes do not change
what is committed. These checks add no npm compression.

Reporting captures small metadata files as byte snapshots, checks those bytes
against the validated root manifest, then parses them. The llama metadata builder
accepts the captured manifest and hashes exactly what it parses, rather than
reading a potentially different manifest a second time. Both unchanged and
changed-then-restored files are covered: a late replacement cannot poison the
in-memory metadata. The reporter also repeats payload validation at the end of
metadata construction, before writing report files. npm packing stays deferred
on both post-publication checks.

All three validators now reject directory symlinks, dangling symlinks and special
files before opening JSON metadata. A file-only listing previously ignored
these entries. Peer dependency and bundled dependency declarations (including
both bundle field spellings) are rejected alongside the existing dependency and
lifecycle-hook checks. Ordinary packages produced by this repository have none
of these fields, so their bytes and metadata formats are unchanged.

`tests/test_publish_adversarial.py` injects faults into Git staging, the final npm
check and report construction. It also checks real local Git push conflicts,
permission/packing failures, immutable-tree reuse, unusual filenames and bounded
blob reads. These checks defend the content handoffs of this trusted job, not a
runner where an attacker controls the Python/Git/npm executables, environment,
credentials or arbitrary concurrent code. The receipt remains a trusted same-job
handoff, not a signature. Network lock generation is unchanged; fault tests use
fixed lock data and make no GitHub publication or inference claim.
