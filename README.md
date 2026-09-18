# llama-cpp-browser-core

A low-level [llama.cpp](https://github.com/ggml-org/llama.cpp) runtime for browser applications. This repository builds WebAssembly and Emscripten JavaScript, then publishes the runtime package to a separate Git branch.

Applications own model downloads, storage, workers, generation loops, and conversation formats. The core exposes the underlying operations without imposing a chat API or an OPFS directory layout.

## Install a runtime

Install a specific **artifact commit**, not a commit from a source branch:

```sh
npm install github:OWNER/llama-cpp-browser-core#ARTIFACT_COMMIT_SHA
```

Replace `OWNER` and `ARTIFACT_COMMIT_SHA` with the repository owner and the chosen artifact commit. No npm registry publication or install-time C/C++ compilation is required.

```js
import { createCore } from 'llama-cpp-browser-core';

const core = await createCore({ profile: 'cpu-wasm32' });
await core.api.llama_backend_init();
const version = await core.api.llama_version();
console.log(core.readUtf8(version));
await core.api.llama_backend_free();
```

Serve the selected profile's JavaScript, Wasm, and any auxiliary files together. Bundlers may not discover dynamically imported assets automatically. When copying `profiles/` to a public directory, pass its URL as `baseURL`:

```js
const core = await createCore({
  profile: 'cpu-wasm32',
  baseURL: new URL('/runtime/profiles/', location.origin),
});
```

See the [API and ownership contract](docs/core-contract.md) and the [OPFS worker example](examples/opfs-model.mjs).

## Build from source

Prerequisites: Linux, Git, Python 3.11 or later, Clang, CMake 3.24 or later, Ninja, and Node.js 22. Downloading the toolchain requires network access.

```sh
git submodule update --init --recursive
python3 scripts/setup_toolchain.py
source .tools/emsdk/emsdk_env.sh
python3 scripts/build.py --profile cpu-wasm32
python3 scripts/build.py --profile cpu-wasm64
python3 scripts/build.py --profile webgpu-wasm64-jspi
python3 scripts/package_runtime.py
```

Build outputs go to `build/<profile>/runtime/`; the assembled package goes to `dist/package/`. Do not commit generated binaries to source branches. Toolchain and upstream pins are in `config/toolchain.json`; profile settings are in `config/profiles.json`.

| Profile | Pointer width | Maximum linear memory | Backends |
|---|---:|---:|---|
| `cpu-wasm32` | 32-bit | 4 GiB | CPU |
| `cpu-wasm64` | 64-bit | 16 GiB | CPU |
| `webgpu-wasm64-jspi` | 64-bit | 16 GiB | CPU and WebGPU; experimental |

All profiles are single-threaded. Memory limits are build-time ceilings, not guarantees that a browser can allocate that memory or run a model of that size. Browser and device support must be checked for the chosen profile.

## Test

```sh
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
```

Native integration tests can run without Emscripten:

```sh
cmake -S . -B build/native -G Ninja -DLCB_NATIVE_CHECK=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --target core -j 4
python3 scripts/make_test_model.py build/fixture.gguf
python3 tests/native_smoke.py --library build/native/libcore.so \
  --schema build/native/generated/schema.json --model build/fixture.gguf \
  --output build/native-results.json
```

The generated model is a small, deterministic, untrained GGUF fixture. Its purpose is to exercise loading and execution, not language quality or compatibility with large trained models. Native tests, host-side mocks, and browser Wasm tests cover different execution paths.

## Distribution

Source branches contain the pinned llama.cpp submodule and build tooling. The `artifacts` branch contains only the installable runtime, types, schemas, manifest, and license notices.

GitHub Actions builds on pushes outside `artifacts` and `artifacts/**`. It builds all three profiles, runs Chromium smoke tests for the two CPU profiles, and verifies the package before publishing an append-only artifact commit. Only the publication job has repository write permission. Repository rules must permit that job to update the artifact branch.

The artifact branch tip is the most recently published result, not necessarily a build from `main`. Consumers should pin the complete artifact commit and commit their lockfile. See the [distribution contract](docs/distribution.md).

## Scope and limitations

The bindings generator exposes non-deprecated, non-variadic llama.cpp, GGUF, and selected backend functions. It generates types and a schema fingerprint, while the native core reports structure layouts. Normalized pointers and sizes use JavaScript `bigint`. Raw upstream exports are also available to callers that understand the exact native ABI.

`mountReadOnlyFile` connects synchronous range reads to the ordinary model loader without creating an additional file-wide JavaScript buffer. It does not eliminate the model's resident memory requirements or the upstream GPU loader's tensor-sized staging buffers. Bounded GPU staging, pthread profiles, and `mtmd` integration are not implemented.

WebGPU is experimental; the workflow compiles that profile but does not certify GPU inference on real devices. Small synthetic-model tests do not establish multi-GiB model support or broad browser compatibility.

## License

Project code is licensed under the [MIT License](LICENSE). Runtime packages collect upstream and toolchain notices under `licenses/`. Model weights are not included and have separate licensing requirements.
