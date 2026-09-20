# Example runtime

These files are always shipped with the artifact as tested reference code for
application TypeScript implementations. Importing the example is optional;
changing application host code does not require rebuilding Wasm.

```js
import { createCore } from 'llama-cpp-browser-core/examples/runtime';
const core = await createCore({ profile: 'cpu-wasm32' });
await core.api.llama_backend_init();
console.log(core.readUtf8(await core.api.llama_version()));
await core.api.llama_backend_free();
```

`index.mjs` loads a profile; `bindings.mjs` demonstrates generated C schema checks,
record access, memory helpers, and serialized Promise calls; `read-only-file.mjs`
adapts synchronous range reads to Emscripten's filesystem. None owns a model,
conversation, tool loop, or worker. CPU Chromium tests use these shipped files for
synthetic model loading/decode/state operations; host tests cover the helpers.

Alternatively import `llama-cpp-browser-core/profiles/cpu-wasm32/core.mjs` directly.
Its Embind chat types and C exports work without these helpers. See the artifact's
[native binding guide](../../chat-and-multimodal.md) for ownership and limitations.
