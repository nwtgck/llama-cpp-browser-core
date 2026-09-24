// Test builds only: exercise the actual patched GGUF reader and C++ stream.
// These probes neither allocate a model nor perform inference.
#include "stable-diffusion.h"
#include "model_io/gguf_io.h"
#include "core/util.h"
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <vector>
extern "C" {
uint64_t sdc_test_gguf_offset(const char* path) {
    std::vector<TensorStorage> tensors;
    std::string error;
    if (!read_gguf_file(path, tensors, &error) || tensors.empty()) return UINT64_MAX;
    return tensors.back().offset;
}
uint32_t sdc_test_gguf_value(const char* path) {
    const uint64_t offset = sdc_test_gguf_offset(path);
    if (offset == UINT64_MAX) return 0;
    std::ifstream file(path, std::ios::binary);
    file.seekg(static_cast<std::streamoff>(offset));
    uint8_t data[4] = {};
    file.read(reinterpret_cast<char*>(data), sizeof(data));
    if (!file) return 0;
    return uint32_t(data[0]) | uint32_t(data[1]) << 8 | uint32_t(data[2]) << 16 | uint32_t(data[3]) << 24;
}
void sdc_test_callbacks() {
    log_printf(SD_LOG_INFO, __FILE__, __LINE__, "native callback probe");
    pretty_progress(1, 4, 0.125f);
}
}
