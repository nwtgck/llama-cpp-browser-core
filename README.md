# Browser inference core monorepo

One source repository, independent browser runtimes, one append-only `artifacts`
branch. Existing llama.cpp source has moved to `llama-cpp/`. Image generation is
an experimental sibling, `stable-diffusion-cpp/`, not an extension of llama's
model pipeline. Both have their own profile definitions and upstream pins.

```text
llama-cpp/                  # existing bridge, config, scripts, tests, upstream
stable-diffusion-cpp/        # new bridge, config, scripts, tests, upstreams
  upstream-patches/         # explicit user-authorized browser experiment
scripts/                    # multi-runtime assembly, publication, reporting
.github/workflows/          # source-bound builds and privileged report boundary
```

## Migration

Before applying a patch that moves the llama submodule, start from a clean source
checkout. `git submodule deinit -- vendor/llama.cpp` safely refuses a dirty
submodule (do not add `--force`). Apply with `git apply --index`, then run
`git submodule sync --recursive` and `git submodule update --init --recursive`.
The old submodule name is retained in `.gitmodules` so its cached Git objects can
be reused at the new path. Commit all staged changes before building publishable
artifacts. ZIP source snapshots contain no submodule data.

Run legacy commands from `llama-cpp/`; root `npm test` forwards the Node tests.
Root `npm run test:python` runs all three Python test suites. Toolchain bootstrap
currently remains in `llama-cpp/scripts/setup_toolchain.py`; its pinned compiler
and Dawn are reused by the image build, not independently upgraded.

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
