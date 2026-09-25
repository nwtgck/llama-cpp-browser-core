# Browser inference runtimes

Runtime-specific source, bindings, profiles and tests belong in their runtime directory.
Do not merge Wasm instances or upstream version pins between runtimes.

- `llama-cpp/`: the existing explicit-approval/last-resort policy still applies.
- `stable-diffusion-cpp/upstream-patches/`: the user expressly authorized the
  implementing agent to choose browser/WebGPU enablement patches for this
  experiment on 2026-09-24. This does not authorize changes to llama.cpp's runtime.

Never edit a checked-out upstream in place or misrepresent compilation/mock tests
as real-model browser inference. Record patch identities and upstream commits.
Publication must be append-only, source-bound, and contain runtime assets only.
