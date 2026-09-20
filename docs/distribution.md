# Build and artifact distribution

## Version boundaries

Source commits pin llama.cpp through a submodule gitlink. Use `git submodule update --init --recursive`; do not advance to a moving upstream tip with `--remote` during a build. The submodule HEAD must match `llamaCommit` in `config/toolchain.json`.

The source commit, llama.cpp commit, and artifact commit are distinct identifiers. The manifest records the first two. The artifact commit is reported after publication through the Actions output and summary; embedding its own hash in the committed manifest would create a circular reference.

## Runtime package

The artifact root gets a separate `package.json`, not a copy of the development manifest. It has no build scripts, workspaces, dependencies, submodules, or build intermediates. `private: true` prevents accidental npm registry publication without changing the Git-based distribution model.

Each profile's runtime directory is copied as a unit. Do not assume every build produces exactly two files. Types, schemas, host helpers, a manifest, and license notices accompany the runtime. The manifest covers every other file's path, size, and SHA-256; unexpected files are rejected. `npm pack` output is checked against the actual package tree.

Mixing source commits, upstream commits, or binding schemas across profiles is rejected. Dirty source builds cannot be published. A single file of 100 MiB or more is rejected before pushing. Exceeding that guard requires an explicit distribution change, not a silent conversion to Git LFS pointers.

## Publication

The first artifact commit has no parent. Later commits use the previous artifact tip as their parent. Each commit replaces the complete package tree so obsolete auxiliary files do not survive. Publication uses normal pushes only, never force-pushes.

When concurrent builds race, the losing publisher fetches the new tip and recreates its candidate commit on that parent. Existing commits and hashes remain intact. After 20 failed attempts, the job fails and can be rerun. A concurrency configuration that cancels earlier pending builds is deliberately not used.

The test, profile-build, and assembly jobs have read permission; only the publication job has write permission. Credentials are supplied through a process-local HTTP header rather than stored in the remote URL. Anyone who can push a source branch can change the resulting artifact; the workflow does not grant publication credentials to untrusted external pull requests.

## Parallel profile builds

Host/publication tests and the four profile builds run independently. Each profile
has its own runner, checkout, toolchain, and `build/<profile>/` directory. The matrix
allows up to four concurrent builds and does not cancel the other profiles when
one fails, so their diagnostics remain available. Runner availability and repository
concurrency limits still determine when jobs actually start.

`scripts/stage_ci_build.py` transfers only the runtime directory, four generated API
files, and provenance for one profile. CMake caches, object files, static libraries,
and SDK executables are not uploaded. The WebGPU JSPI job additionally supplies the
standalone Emscripten and Dawn notices using the same selection rules as local
packaging. Each shard has distinct paths; the assembly job merges them under
`build/`. That job checks out llama.cpp for its notices and browser-test templates,
but does not install Emscripten or compile the core again.

The existing `build` job remains the aggregate success check. Assembly requires
the host tests and every matrix build to succeed. It then uses
the existing packager, CPU Chromium tests (including the chat/tool surface),
validation recorder, and final package checks. No partial profile set is published.
Only the resulting `runtime-package` is passed to the single publication job.
The local, sequential build commands remain supported.

Parallel runners reduce elapsed time rather than the total amount of compilation.
They also repeat setup work and add artifact transfers; total runner usage can
increase. Profile build times and queueing determine the actual speedup.

## Release checks

Publication requires all four profile builds, Chromium tests for the two CPU profiles, and package verification. The workflow compiles WebGPU but does not record GPU inference as verified. CPU smoke tests use a small untrained synthetic GGUF, not a quality benchmark or a multi-GiB model acceptance test.

Reproducible tests and CI verification logic belong in the source repository. Per-run logs and test-result JSON belong in ignored build output or CI artifacts, not committed documentation. Artifact manifests retain build provenance and the scope of checks actually performed.

## Retention

Consumers may depend on older artifact hashes, so do not delete or replace artifact history. Binary history still occupies space in the shared repository even when source branches contain no binaries. Limit fetch scope and depth where appropriate, and only compact storage in ways that preserve referenced commits.
