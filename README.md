# Browser Inference Core

One source repository, independent browser runtimes, one append-only `artifacts`
branch. Existing llama.cpp source has moved to `llama-cpp/`. Image generation is
an experimental sibling, `stable-diffusion-cpp/`, not an extension of llama's
model pipeline. Both have their own profile definitions and upstream pins.

```text
llama-cpp/                  # existing bridge, config, scripts, tests, upstream
stable-diffusion-cpp/        # new bridge, config, scripts, tests, upstreams
  upstream-patches/         # explicit user-authorized browser experiment
toolchain/config.json       # common pinned browser compiler and Dawn
scripts/                    # common setup, caches, assembly, publication, reporting
.github/workflows/          # source-bound builds and privileged report boundary
```

## Migration

The submodule-move instructions below apply only to the initial single-runtime
layout migration. Skip them when the two runtime directories already exist.

Before applying a patch that moves the llama submodule, start from a clean source
checkout. `git submodule deinit -- vendor/llama.cpp` safely refuses a dirty
submodule (do not add `--force`). Apply with `git apply --index`, then run
`git submodule sync --recursive` and `git submodule update --init --recursive`.
The old submodule name is retained in `.gitmodules` so its cached Git objects can
be reused at the new path. Commit all staged changes before building publishable
artifacts. ZIP source snapshots contain no submodule data.

The current project name is **Browser Inference Core** (`browser-inference-core`).
This is a provisional name, not an instruction to rename a GitHub repository.
The installable package name stays `llama-cpp-browser-core` for existing imports;
publication URLs and install commands use the actual `GITHUB_REPOSITORY` value.

Run runtime-specific commands from their own directories. Root `npm test` runs
both Node test suites; `npm run test:python` runs all three Python suites.
Bootstrap the shared compiler from the repository root:

```sh
python3 scripts/setup_toolchain.py
source .tools/emsdk/emsdk_env.sh
```

Compiler, Dawn and the already-approved Asyncify correction are pinned in
`toolchain/config.json`. The llama pin stays in
`llama-cpp/config/toolchain.json`; image pins stay in
`stable-diffusion-cpp/config/upstreams.json`. Their versions are not coupled.
Old ignored `llama-cpp/.tools/` contents are no longer read; do not copy them into
the new shared installation. No submodule relocation is needed for this change.

## Parallel builds and bounded caches

```text
host tests ──────────────────┐
llama compile (10 pairs) ────┴─ llama package + browser checks ──┐
                                                              ├─ aggregate + publish
image compile (4 pairs) ─────┬─ image package + browser checks ─┘
image native checks ────────┘
```

Only final aggregation waits for both runtimes. Each side transfers its own
compiler/Dawn license notices and never borrows the other's build artifact.
Runner availability can still limit actual parallelism.

CI caches the SHA-256-verified Dawn download, Emscripten's system-library cache,
and strictly partitioned ccache objects. It always verifies the pinned toolchain,
configures a fresh build tree and links the current runtime. It never restores
finished runtime artifacts as a shortcut to a successful build. Cache saving is
restricted to successful default-branch push builds; PR and other branch builds
only restore through these configured actions. See
[the cache and toolchain contract](toolchain/README.md) for trust limits,
invalidation, and the cold-cache path.

## Runtime package layout (manifest format 3)

```text
manifest.json
llama-cpp/manifest.json
llama-cpp/profiles/<profile>/<variant>/core.{mjs,wasm,d.ts}
llama-cpp/api/...
stable-diffusion-cpp/manifest.json
stable-diffusion-cpp/profiles/<profile>/<variant>/core.{mjs,wasm,d.ts}
```

Each inner manifest keeps runtime-specific provenance and validation. The root
manifest hashes the complete tree, including both inner manifests, and refuses
mixed source commits. npm exports preserve legacy llama import aliases; direct
filesystem consumers must use `llama-cpp/` and its inner manifest. No duplicate
Wasm payloads are shipped. Publication waits for both complete runtime packages;
there is no last-writer-wins replacement of one runtime with the other.

Naidan can keep its current llama dependency unchanged and install this artifact
under the separate dependency name `stable-diffusion-cpp-browser-core` using the
exact install command generated after successful publication. Never put a
fictional artifact commit into a consumer lockfile. A local `dist/package` may
also be selected explicitly for development.

The image runtime is experimental. Native tests, browser ABI smoke checks and
compilation are distinct from actual GPU image generation. Consult each profile's
recorded validation scope; real-model inference is not asserted automatically.
