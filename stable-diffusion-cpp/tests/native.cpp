#include "ggml.h"
#include "ggml-cpu.h"
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <vector>
extern "C" int sdb_abi_version();
extern "C" int sdb_load(const char*);
extern "C" int sdb_generate(const char*);
extern "C" const char* sdb_error();
extern "C" void sdb_unload();
// This is the actual patched source entry point, not a copy of its math.
ggml_tensor* ggml_ext_group_norm_32(ggml_context*, ggml_tensor*);
int main() {
    if (sdb_abi_version() != 1 || sdb_load("[]") != 0 || !*sdb_error() ||
        sdb_load("{\"model\":\"/outside.gguf\",\"gpuBudgetMiB\":2048}") != 0 ||
        sdb_generate("{}") != 0) return 1;
    for (int batch : {1, 2}) {
        ggml_init_params params{64 * 1024 * 1024, nullptr, false};
        auto ctx = ggml_init(params);
        auto input = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, 17, 11, 64, batch);
        auto values = static_cast<float*>(input->data);
        for (int64_t i = 0; i < ggml_nelements(input); ++i) values[i] = std::sin(float(i) * 0.17f);
        auto reference = ggml_group_norm(ctx, input, 32, 1e-6f);
        auto actual = ggml_ext_group_norm_32(ctx, input);
        auto graph = ggml_new_graph(ctx);
        ggml_build_forward_expand(graph, reference);
        ggml_build_forward_expand(graph, actual);
        if (ggml_graph_compute_with_ctx(ctx, graph, 1) != GGML_STATUS_SUCCESS) return 2;
        float maximum = 0;
        for (int64_t i = 0; i < ggml_nelements(input); ++i)
            maximum = std::max(maximum, std::abs(static_cast<float*>(reference->data)[i] - static_cast<float*>(actual->data)[i]));
        std::printf("group-norm batch=%d max-absolute-error=%.9g\n", batch, maximum);
        if (!std::isfinite(maximum) || maximum > 1e-4f) return 3;
        ggml_free(ctx);
    }
    sdb_unload();
    std::puts("Native bridge boundaries and group-normalization parity passed; no model inference performed.");
}
