# The copy is prepared before ABI generation so both Clang's schema and every
# translation unit see exactly the same public header (including capability queries).
get_filename_component(LCB_TTS_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(LCB_TTS_OVERLAY "${CMAKE_CURRENT_BINARY_DIR}/mtmd-tts-overlay")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${LCB_TTS_ROOT}/scripts/prepare_tts.py"
    --source "${LCB_LLAMA_SOURCE}" --output "${LCB_TTS_OVERLAY}"
    COMMAND_ERROR_IS_FATAL ANY)
set(LCB_TTS_INCLUDE "${LCB_TTS_OVERLAY}/tools/mtmd")
file(GLOB_RECURSE LCB_TTS_SOURCE_INPUTS CONFIGURE_DEPENDS
    "${LCB_LLAMA_SOURCE}/tools/mtmd/*.cpp" "${LCB_LLAMA_SOURCE}/tools/mtmd/*.h" "${LCB_LLAMA_SOURCE}/tools/mtmd/*.hpp")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${LCB_TTS_SOURCE_INPUTS}
    "${LCB_TTS_ROOT}/scripts/prepare_tts.py"
    "${LCB_TTS_ROOT}/patches/mtmd-tts-generation.patch")
foreach(source clip.cpp models/models.h models/qwen3tts-gen.cpp mtmd-helper-gen.cpp mtmd-helper.h)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${LCB_LLAMA_SOURCE}/tools/mtmd/${source}")
endforeach()

function(lcb_attach_tts_overlay)
    get_target_property(sources mtmd SOURCES)
    set(patched_sources)
    foreach(source IN LISTS sources)
        if(IS_ABSOLUTE "${source}" OR NOT EXISTS "${LCB_TTS_INCLUDE}/${source}" OR NOT EXISTS "${LCB_LLAMA_SOURCE}/tools/mtmd/${source}")
            message(FATAL_ERROR "Upstream mtmd source layout changed; review the TTS overlay: ${source}")
        endif()
        list(APPEND patched_sources "${LCB_TTS_INCLUDE}/${source}")
    endforeach()
    set_property(TARGET mtmd PROPERTY SOURCES "${patched_sources}")
    # Upstream precompiles models.h by absolute path. Replace that too, otherwise
    # the old class layout would leak into patched and unpatched translation units.
    set_property(TARGET mtmd PROPERTY PRECOMPILE_HEADERS "${LCB_TTS_INCLUDE}/models/models.h")
    set_source_files_properties("${LCB_TTS_INCLUDE}/mtmd-helper-gen.cpp" "${LCB_TTS_INCLUDE}/mtmd-helper.cpp"
        TARGET_DIRECTORY mtmd PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
    target_include_directories(mtmd BEFORE PRIVATE "${LCB_TTS_INCLUDE}" "${LCB_TTS_INCLUDE}/models")
    target_include_directories(mtmd PRIVATE "${LCB_LLAMA_SOURCE}/tools/mtmd" "${LCB_LLAMA_SOURCE}/tools/mtmd/models")
endfunction()
