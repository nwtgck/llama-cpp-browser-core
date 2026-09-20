# llama-cpp-browser-core

This branch distributes a prebuilt runtime. Source code and build instructions are on the source branches of the same repository. Installing this package does not install Emscripten or compile C/C++.

```sh
npm install github:nwtgck/llama-cpp-browser-core#ARTIFACT_COMMIT_SHA
```

Replace `ARTIFACT_COMMIT_SHA` with the complete commit hash containing the chosen runtime artifacts, not a source commit. No npm registry publication is required.

```js
import { createCore } from 'llama-cpp-browser-core/examples/runtime';

const core = await createCore({ profile: 'cpu-wasm32' });
await core.api.llama_backend_init();
const version = await core.api.llama_version();
console.log(core.readUtf8(version));
await core.api.llama_backend_free();
```

The dynamically imported modules and their neighboring Wasm assets must be served together. A bundler may not discover them automatically. After copying `profiles/` into a public directory, set `baseURL`, for example `new URL('/runtime/profiles/', location.origin)`. This is an example path, not a required deployment layout. Do not mix generated JavaScript and Wasm from different artifact commits.

Generated-module types are in each profile's `core.d.ts`; normalized C function types are in `api/functions.d.ts`, with layout identifiers in `api/schema.json`. The native core reports actual memory layouts. `manifest.json` records source commits, build settings, file hashes, and performed checks. The `lcb_` C bindings normalize pointers and sizes to JavaScript `bigint` and replace structure return values with explicit return-storage pointers.

This is not a high-level chat library. Applications own files, workers, generation loops, conversations, cancellation, and data formats. `mountReadOnlyFile` is an optional adapter for synchronous range reads; it does not impose a storage backend.

`examples/runtime/` is always shipped as tested reference code for application
TypeScript implementations; importing it is optional. Applications may import a
profile's generated `core.mjs` directly for Embind and C exports.
See [native chat and multimodal bindings](chat-and-multimodal.md). The public
bindings may change with the pinned upstream version.

All profiles are single-threaded. `webgpu-wasm64-jspi` is experimental and requires device-specific testing. A successful build, a synthetic-model smoke test, and support for a large production model are separate claims; consult the manifest for the checks actually performed.

The upstream GPU loader can allocate a tensor-sized staging buffer. This runtime has not replaced that path with bounded staging. Limiting individual file reads does not bound every allocation in the loader.

Project code is MIT-licensed. Upstream and toolchain notices are under `licenses/`.
Original headers retained as `.txt` under `licenses/embedded/` preserve embedded
notices verbatim; they are not build inputs. Model weights are not included.
