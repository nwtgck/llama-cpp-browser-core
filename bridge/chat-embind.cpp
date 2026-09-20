#include "common/chat.h"
#include "common/json-schema-to-grammar.h"
#include "common/reasoning-budget.h"

#include <emscripten/bind.h>
#include <cstdint>
#include <stdexcept>

namespace {
template <typename T> T * pointer(uint64_t address) {
    if (address > UINTPTR_MAX) throw std::range_error("pointer exceeds address space");
    return reinterpret_cast<T *>(static_cast<uintptr_t>(address));
}

template <typename T> uint64_t address(T * value) {
    return static_cast<uint64_t>(reinterpret_cast<uintptr_t>(value));
}

// The upstream template type is incomplete and has a custom deleter.
// This owner lets ordinary Embind delete() invoke that upstream deleter.
struct templates_owner {
    common_chat_templates_ptr value;

    templates_owner(uint64_t model, const std::string & source, const std::string & bos, const std::string & eos)
        : value(common_chat_templates_init(pointer<const llama_model>(model), source, bos, eos)) {}
};
} // namespace

// These are type/member registrations, not a second chat data model.
#define FIELD(type, member) .property(#member, &type::member)
#define VALUE(name) .value(#name, name)

EMSCRIPTEN_BINDINGS(llama_common_chat) {
    using namespace emscripten;

    enum_<common_chat_role>("common_chat_role")
        VALUE(COMMON_CHAT_ROLE_UNKNOWN) VALUE(COMMON_CHAT_ROLE_SYSTEM)
        VALUE(COMMON_CHAT_ROLE_ASSISTANT) VALUE(COMMON_CHAT_ROLE_USER) VALUE(COMMON_CHAT_ROLE_TOOL);
    enum_<common_chat_tool_choice>("common_chat_tool_choice")
        VALUE(COMMON_CHAT_TOOL_CHOICE_AUTO) VALUE(COMMON_CHAT_TOOL_CHOICE_REQUIRED) VALUE(COMMON_CHAT_TOOL_CHOICE_NONE);
    enum_<common_chat_format>("common_chat_format")
        VALUE(COMMON_CHAT_FORMAT_CONTENT_ONLY) VALUE(COMMON_CHAT_FORMAT_PEG_SIMPLE)
        VALUE(COMMON_CHAT_FORMAT_PEG_NATIVE) VALUE(COMMON_CHAT_FORMAT_PEG_GEMMA4)
        VALUE(COMMON_CHAT_FORMAT_PEG_MINIMAX_M3) VALUE(COMMON_CHAT_FORMAT_COUNT);
    enum_<common_chat_continuation>("common_chat_continuation")
        VALUE(COMMON_CHAT_CONTINUATION_NONE) VALUE(COMMON_CHAT_CONTINUATION_AUTO)
        VALUE(COMMON_CHAT_CONTINUATION_REASONING) VALUE(COMMON_CHAT_CONTINUATION_CONTENT);
    enum_<common_reasoning_format>("common_reasoning_format")
        VALUE(COMMON_REASONING_FORMAT_NONE) VALUE(COMMON_REASONING_FORMAT_AUTO)
        VALUE(COMMON_REASONING_FORMAT_DEEPSEEK_LEGACY) VALUE(COMMON_REASONING_FORMAT_DEEPSEEK);
    enum_<common_grammar_trigger_type>("common_grammar_trigger_type")
        VALUE(COMMON_GRAMMAR_TRIGGER_TYPE_TOKEN) VALUE(COMMON_GRAMMAR_TRIGGER_TYPE_WORD)
        VALUE(COMMON_GRAMMAR_TRIGGER_TYPE_PATTERN) VALUE(COMMON_GRAMMAR_TRIGGER_TYPE_PATTERN_FULL);
    enum_<common_reasoning_budget_state>("common_reasoning_budget_state")
        VALUE(REASONING_BUDGET_IDLE) VALUE(REASONING_BUDGET_COUNTING) VALUE(REASONING_BUDGET_FORCING)
        VALUE(REASONING_BUDGET_WAITING_UTF8) VALUE(REASONING_BUDGET_DONE);

    register_vector<std::string>("string_vector");
    register_vector<llama_token>("llama_tokens");
    register_vector<llama_tokens>("llama_token_sequences");
    register_vector<size_t>("size_vector");
    register_vector<common_chat_msg>("common_chat_messages");
    register_vector<common_chat_tool>("common_chat_tools");
    register_vector<common_chat_tool_call>("common_chat_tool_calls");
    register_vector<common_chat_msg_content_part>("common_chat_content_parts");
    register_vector<common_chat_msg_diff>("common_chat_message_diffs");
    register_vector<common_grammar_trigger>("common_grammar_triggers");
    register_vector<common_chat_msg_delimiter>("common_chat_delimiter_vector");
    register_vector<common_chat_msg_span>("common_chat_span_vector");
    register_map<std::string, std::string>("string_map");
    register_map<std::string, bool>("bool_map");
    register_map<size_t, size_t>("size_map");

    class_<common_json>("common_json")
        .constructor<>()
        .class_function("parse", &common_json::parse)
        .class_function("parse_no_throw", &common_json::parse_no_throw)
        .class_function("array", select_overload<common_json()>(&common_json::array))
        .class_function("object", select_overload<common_json()>(&common_json::object))
        .function("dump", &common_json::dump)
        .function("is_discarded", &common_json::is_discarded)
        .function("is_null", &common_json::is_null)
        .function("is_object", &common_json::is_object)
        .function("is_array", &common_json::is_array)
        .function("size", &common_json::size);

    using clock = std::chrono::system_clock;
    class_<clock::duration>("system_clock_duration")
        .constructor<>()
        .constructor<clock::duration::rep>()
        .function("count", &clock::duration::count);
    class_<clock::time_point>("system_clock_time_point")
        .constructor<>()
        .constructor<clock::duration>()
        .function("time_since_epoch", &clock::time_point::time_since_epoch);
    function("system_clock_period_num", +[]() -> int64_t { return clock::period::num; });
    function("system_clock_period_den", +[]() -> int64_t { return clock::period::den; });
    function("system_clock_now", &clock::now);

    class_<common_chat_tool_call>("common_chat_tool_call").constructor<>()
        FIELD(common_chat_tool_call, name) FIELD(common_chat_tool_call, arguments) FIELD(common_chat_tool_call, id);
    class_<common_chat_msg_content_part>("common_chat_msg_content_part").constructor<>()
        FIELD(common_chat_msg_content_part, type) FIELD(common_chat_msg_content_part, text);
    class_<common_chat_msg>("common_chat_msg").constructor<>()
        FIELD(common_chat_msg, role) FIELD(common_chat_msg, content) FIELD(common_chat_msg, content_parts)
        FIELD(common_chat_msg, tool_calls) FIELD(common_chat_msg, reasoning_content)
        FIELD(common_chat_msg, tool_name) FIELD(common_chat_msg, tool_call_id)
        .function("to_json_oaicompat", &common_chat_msg::to_json_oaicompat)
        .function("render_content", &common_chat_msg::render_content)
        .function("empty", &common_chat_msg::empty)
        .function("contains_media", &common_chat_msg::contains_media);
    class_<common_chat_tool>("common_chat_tool").constructor<>()
        FIELD(common_chat_tool, name) FIELD(common_chat_tool, description) FIELD(common_chat_tool, parameters);
    class_<common_chat_msg_diff>("common_chat_msg_diff").constructor<>()
        FIELD(common_chat_msg_diff, reasoning_content_delta) FIELD(common_chat_msg_diff, content_delta)
        FIELD(common_chat_msg_diff, tool_call_index) FIELD(common_chat_msg_diff, tool_call_delta)
        .class_function("compute_diffs", &common_chat_msg_diff::compute_diffs);
    function("common_chat_no_tool_call_index", +[]() -> size_t { return std::string::npos; });
    class_<common_grammar_trigger>("common_grammar_trigger").constructor<>()
        FIELD(common_grammar_trigger, type) FIELD(common_grammar_trigger, value) FIELD(common_grammar_trigger, token);
    class_<common_chat_msg_span>("common_chat_msg_span").constructor<>()
        FIELD(common_chat_msg_span, role) FIELD(common_chat_msg_span, pos) FIELD(common_chat_msg_span, len)
        .function("valid", &common_chat_msg_span::valid);
    class_<common_chat_msg_spans>("common_chat_msg_spans").constructor<>()
        FIELD(common_chat_msg_spans, spans)
        .function("add", &common_chat_msg_spans::add)
        .function("is_user_start", &common_chat_msg_spans::is_user_start)
        .function("last_user_message_pos", &common_chat_msg_spans::last_user_message_pos);
    class_<common_chat_msg_delimiter>("common_chat_msg_delimiter").constructor<>()
        FIELD(common_chat_msg_delimiter, role) FIELD(common_chat_msg_delimiter, delimiter) FIELD(common_chat_msg_delimiter, tokens);
    class_<common_chat_msg_delimiters>("common_chat_msg_delimiters").constructor<>()
        FIELD(common_chat_msg_delimiters, delimiters)
        .function("add", &common_chat_msg_delimiters::add)
        .function("tokenize", +[](common_chat_msg_delimiters & self, uint64_t vocab) { self.tokenize(pointer<const llama_vocab>(vocab)); })
        .function("split", &common_chat_msg_delimiters::split)
        .function("to_json", &common_chat_msg_delimiters::to_json);
    class_<common_chat_templates_inputs>("common_chat_templates_inputs").constructor<>()
        FIELD(common_chat_templates_inputs, messages) FIELD(common_chat_templates_inputs, grammar)
        FIELD(common_chat_templates_inputs, json_schema) FIELD(common_chat_templates_inputs, add_generation_prompt)
        FIELD(common_chat_templates_inputs, continue_final_message) FIELD(common_chat_templates_inputs, use_jinja)
        FIELD(common_chat_templates_inputs, tools) FIELD(common_chat_templates_inputs, tool_choice)
        FIELD(common_chat_templates_inputs, parallel_tool_calls) FIELD(common_chat_templates_inputs, reasoning_format)
        FIELD(common_chat_templates_inputs, enable_thinking) FIELD(common_chat_templates_inputs, now)
        FIELD(common_chat_templates_inputs, chat_template_kwargs) FIELD(common_chat_templates_inputs, add_bos)
        FIELD(common_chat_templates_inputs, add_eos) FIELD(common_chat_templates_inputs, force_pure_content);
    class_<common_chat_params>("common_chat_params").constructor<>()
        FIELD(common_chat_params, format) FIELD(common_chat_params, prompt) FIELD(common_chat_params, grammar)
        FIELD(common_chat_params, grammar_lazy) FIELD(common_chat_params, generation_prompt)
        FIELD(common_chat_params, supports_thinking) FIELD(common_chat_params, thinking_start_tag)
        FIELD(common_chat_params, thinking_end_tags) FIELD(common_chat_params, grammar_triggers)
        FIELD(common_chat_params, preserved_tokens) FIELD(common_chat_params, additional_stops)
        FIELD(common_chat_params, parser) FIELD(common_chat_params, message_delimiters);
    class_<common_chat_parser_params>("common_chat_parser_params").constructor<>()
        .constructor<const common_chat_params &>()
        FIELD(common_chat_parser_params, format) FIELD(common_chat_parser_params, reasoning_format)
        FIELD(common_chat_parser_params, reasoning_in_content) FIELD(common_chat_parser_params, generation_prompt)
        FIELD(common_chat_parser_params, parse_tool_calls) FIELD(common_chat_parser_params, is_continuation)
        FIELD(common_chat_parser_params, echo) FIELD(common_chat_parser_params, debug) FIELD(common_chat_parser_params, parser);
    class_<common_chat_prompt_preset>("common_chat_prompt_preset").constructor<>()
        FIELD(common_chat_prompt_preset, system) FIELD(common_chat_prompt_preset, user);
    class_<common_peg_arena>("common_peg_arena").constructor<>()
        .function("load", &common_peg_arena::load)
        .function("save", &common_peg_arena::save)
        .function("empty", &common_peg_arena::empty)
        .function("size", &common_peg_arena::size)
        .function("to_json", &common_peg_arena::to_json)
        .class_function("from_json", &common_peg_arena::from_json);

    class_<templates_owner>("common_chat_templates")
        .constructor<uint64_t, const std::string &, const std::string &, const std::string &>()
        .function("apply", +[](const templates_owner & self, const common_chat_templates_inputs & inputs) {
            return common_chat_templates_apply(self.value.get(), inputs);
        })
        .function("source", +[](const templates_owner & self, const std::string & variant) {
            return common_chat_templates_source(self.value.get(), variant);
        })
        .function("was_explicit", +[](const templates_owner & self) { return common_chat_templates_was_explicit(self.value.get()); })
        .function("get_caps", +[](const templates_owner & self) { return common_chat_templates_get_caps(self.value.get()); })
        .function("support_enable_thinking", +[](const templates_owner & self) { return common_chat_templates_support_enable_thinking(self.value.get()); })
        .function("format_single", +[](const templates_owner & self, const std::vector<common_chat_msg> & past,
            const common_chat_msg & message, bool add_assistant, bool use_jinja) {
            return common_chat_format_single(self.value.get(), past, message, add_assistant, use_jinja);
        })
        .function("format_example", +[](const templates_owner & self, bool use_jinja, const std::map<std::string, std::string> & kwargs) {
            return common_chat_format_example(self.value.get(), use_jinja, kwargs);
        })
        .function("get_asr_prompt", +[](const templates_owner & self) { return common_chat_get_asr_prompt(self.value.get()); });

    function("common_chat_verify_template", &common_chat_verify_template);
    function("common_chat_parse", &common_chat_parse);
    function("common_chat_peg_parse", &common_chat_peg_parse);
    function("common_chat_msgs_parse_oaicompat", &common_chat_msgs_parse_oaicompat);
    function("common_chat_msgs_to_json_oaicompat", &common_chat_msgs_to_json_oaicompat);
    function("common_chat_tools_parse_oaicompat", &common_chat_tools_parse_oaicompat);
    function("common_chat_tools_to_json_oaicompat", &common_chat_tools_to_json_oaicompat);
    function("common_chat_continuation_parse", &common_chat_continuation_parse);
    function("common_chat_tool_choice_parse_oaicompat", &common_chat_tool_choice_parse_oaicompat);
    function("common_chat_msg_delimiters_parse", &common_chat_msg_delimiters_parse);
    function("common_chat_role_from_string", &common_chat_role_from_string);
    function("common_chat_role_to_string", +[](common_chat_role role) { return std::string(common_chat_role_to_string(role)); });
    function("common_chat_format_name", +[](common_chat_format format) { return std::string(common_chat_format_name(format)); });
    function("common_reasoning_format_name", +[](common_reasoning_format format) { return std::string(common_reasoning_format_name(format)); });
    function("common_reasoning_format_from_name", &common_reasoning_format_from_name);
    function("json_schema_to_grammar", &json_schema_to_grammar);
    function("common_reasoning_budget_init", +[](uint64_t vocab, const std::vector<llama_tokens> & starts,
        const std::vector<llama_tokens> & ends, const llama_tokens & forced, int32_t budget, common_reasoning_budget_state initial) {
        return address(common_reasoning_budget_init(pointer<const llama_vocab>(vocab), starts, ends, forced, budget, initial));
    });
    function("common_reasoning_budget_get_state", +[](uint64_t sampler) { return common_reasoning_budget_get_state(pointer<const llama_sampler>(sampler)); });
    // A const vector pointer cannot use register_vector's mutable class handle.
    // Copy the short marker so callers own a readable value with normal delete().
    function("common_reasoning_budget_get_end_match_copy", +[](uint64_t sampler) -> llama_tokens {
        const auto * match = common_reasoning_budget_get_end_match(pointer<const llama_sampler>(sampler));
        return match ? *match : llama_tokens{};
    });
    function("common_reasoning_budget_force", +[](uint64_t sampler) { return common_reasoning_budget_force(pointer<llama_sampler>(sampler)); });
}

#undef FIELD
#undef VALUE
