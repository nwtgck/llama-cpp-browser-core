/** Real generated-module checks; independent of the example runtime. */
export async function checkChatSurface(native, template) {
  const check = (value, label) => { if (!value) throw new Error(label); };
  const objects = [];
  const own = value => { objects.push(value); return value; };
  const json = value => own(native.common_json.parse(JSON.stringify(value)));
  const toJS = value => JSON.parse(value.dump(-1));
  try {
    const templates = own(new native.common_chat_templates(0n, template, '', ''));
    check(templates.was_explicit(), 'Template override lost');
    const caps = own(templates.get_caps());
    check(Number(caps.size()) > 0, 'Missing template capabilities');
    const tools = [{ type: 'function', function: {
      name: 'lookup', description: 'Find weather',
      parameters: { type: 'object', properties: { city: { type: 'string' } }, required: ['city'] },
    } }];
    const messages = [{ role: 'user', content: 'Weather in Tokyo?' }];
    const inputs = own(new native.common_chat_templates_inputs());
    check(Number(own(inputs.messages).size()) === 0, 'Native empty message default changed');
    check(inputs.tool_choice === native.common_chat_tool_choice.COMMON_CHAT_TOOL_CHOICE_AUTO, 'Native tool choice default changed');
    inputs.messages = own(native.common_chat_msgs_parse_oaicompat(json(messages)));
    inputs.tools = own(native.common_chat_tools_parse_oaicompat(json(tools)));
    inputs.enable_thinking = false;
    const params = own(templates.apply(inputs));
    check(params.prompt.includes('lookup') && params.prompt.includes('Weather in Tokyo?'), 'Jinja tools/messages missing');
    check(typeof params.format.value === 'number', 'Chat format must retain its native enum');
    check(typeof params.grammar === 'string' && typeof params.grammar_lazy === 'boolean', 'Grammar metadata missing');
    check(typeof params.generation_prompt === 'string' && typeof params.supports_thinking === 'boolean', 'Generation metadata missing');
    check(typeof params.thinking_start_tag === 'string', 'Reasoning metadata missing');
    for (const field of ['thinking_end_tags', 'grammar_triggers', 'preserved_tokens', 'additional_stops']) {
      check(Number(own(params[field]).size()) >= 0, `Missing native vector: ${field}`);
    }
    const delimiters = own(params.message_delimiters);
    check(Number(own(delimiters.delimiters).size()) >= 0, 'Message delimiters missing');
    check(params.parser.length > 0, 'Missing serialized tool parser');
    const parserParams = own(new native.common_chat_parser_params(params));
    parserParams.reasoning_format = native.common_reasoning_format.COMMON_REASONING_FORMAT_AUTO;
    const arena = own(new native.common_peg_arena());
    arena.load(params.parser);
    const generated = '<tool_call>\n{"name":"lookup","arguments":{"city":"Tokyo"}}\n</tool_call>';
    const parsed = own(native.common_chat_peg_parse(arena, generated, false, parserParams));
    const parsedJSON = toJS(own(parsed.to_json_oaicompat(false)));
    check(parsedJSON.tool_calls?.[0]?.function.name === 'lookup', 'Tool name not parsed');
    check(JSON.parse(parsedJSON.tool_calls[0].function.arguments).city === 'Tokyo', 'Tool arguments not parsed');
    const restored = own(new native.common_peg_arena());
    restored.load(arena.save());
    const again = own(native.common_chat_peg_parse(restored, generated, false, parserParams));
    check(own(again.to_json_oaicompat(false)).dump(-1) === own(parsed.to_json_oaicompat(false)).dump(-1), 'Saved parser changed result');
    own(native.common_chat_peg_parse(arena, generated.slice(0, -5), true, parserParams));
    // Assignment copies an arena once; repeated parse calls then reuse the native member.
    parserParams.parser = arena;
    const fromParams = own(native.common_chat_parse(generated, false, parserParams));
    check(own(fromParams.to_json_oaicompat(false)).dump(-1) === own(parsed.to_json_oaicompat(false)).dump(-1), 'Native parser member changed result');
    inputs.messages = own(native.common_chat_msgs_parse_oaicompat(json([...messages,
      { ...parsedJSON, tool_calls: parsedJSON.tool_calls.map(call => ({ ...call, id: 'caller-assigned' })) },
      { role: 'tool', content: 'Sunny', name: 'lookup', tool_call_id: 'caller-assigned' }])));
    check(own(templates.apply(inputs)).prompt.includes('<tool_response>\nSunny'), 'Tool result history missing');

    const plainParams = own(new native.common_chat_parser_params());
    const previous = own(native.common_chat_parse('hel', true, plainParams));
    const current = own(native.common_chat_parse('hello', false, plainParams));
    const diffs = own(native.common_chat_msg_diff.compute_diffs(previous, current));
    const diff = own(diffs.get(0));
    check(diff.content_delta === 'lo', 'Streaming text diff failed');
    check(diff.tool_call_index === native.common_chat_no_tool_call_index(), 'Native missing-index sentinel changed');
    // Keep the public JSON + force_gbnf signature even when upstream adds
    // schema-document overloads. Both calls use the same owned common_json.
    const schema = json({ type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'] });
    const schemaBefore = schema.dump(-1);
    for (const forceGbnf of [false, true]) {
      const grammar = native.json_schema_to_grammar(schema, forceGbnf);
      // This runtime disables LLGuidance, so both modes produce GBNF.
      check(typeof grammar === 'string' && grammar.includes('root ::='), `JSON schema grammar missing (force_gbnf=${forceGbnf})`);
      check(schema.dump(-1) === schemaBefore, 'JSON schema conversion changed the input');
    }
    const invalid = own(native.common_json.parse_no_throw('{'));
    check(invalid.is_discarded(), 'Upstream nonthrowing JSON parse changed');

    const trigger = own(new native.common_grammar_trigger());
    trigger.type = native.common_grammar_trigger_type.COMMON_GRAMMAR_TRIGGER_TYPE_PATTERN_FULL;
    check(trigger.type === native.common_grammar_trigger_type.COMMON_GRAMMAR_TRIGGER_TYPE_PATTERN_FULL, 'Grammar trigger enum changed');
    const delimiter = own(new native.common_chat_msg_delimiter());
    delimiter.role = native.common_chat_role.COMMON_CHAT_ROLE_TOOL;
    check(delimiter.role === native.common_chat_role.COMMON_CHAT_ROLE_TOOL, 'Delimiter role enum changed');
    const zero = own(new native.system_clock_duration(0n));
    inputs.now = own(new native.system_clock_time_point(zero));
    check(own(own(inputs.now).time_since_epoch()).count() === 0n, 'Native clock value changed');
    check(native.system_clock_period_num() > 0n && native.system_clock_period_den() > 0n, 'Native clock period missing');

    const starts = own(new native.llama_token_sequences());
    const ends = own(new native.llama_token_sequences());
    const start = own(new native.llama_tokens()); start.push_back(1);
    const end = own(new native.llama_tokens()); end.push_back(2);
    starts.push_back(start); ends.push_back(end);
    const budget = native.common_reasoning_budget_init(0n, starts, ends, end, 4,
      native.common_reasoning_budget_state.REASONING_BUDGET_IDLE);
    let endCopy;
    try {
      check(Number(own(native.common_reasoning_budget_get_end_match_copy(budget)).size()) === 0, 'Unexpected reasoning end match');
      await native._lcb_llama_sampler_accept(budget, 1);
      check(native.common_reasoning_budget_get_state(budget) === native.common_reasoning_budget_state.REASONING_BUDGET_COUNTING, 'Reasoning counting transition failed');
      await native._lcb_llama_sampler_accept(budget, 2);
      check(native.common_reasoning_budget_get_state(budget) === native.common_reasoning_budget_state.REASONING_BUDGET_DONE, 'Reasoning completion transition failed');
      endCopy = own(native.common_reasoning_budget_get_end_match_copy(budget));
      check(Number(endCopy.size()) === 1 && endCopy.get(0) === 2, 'Reasoning end copy is unreadable');
      check(Array.from(endCopy).join(',') === '2', 'Reasoning end copy is not iterable');
      const disposable = native.common_reasoning_budget_get_end_match_copy(budget);
      disposable.delete();
      check(native.common_reasoning_budget_get_state(budget) === native.common_reasoning_budget_state.REASONING_BUDGET_DONE, 'Deleting the owned copy changed sampler state');
      endCopy.set(0, 3);
      check(own(native.common_reasoning_budget_get_end_match_copy(budget)).get(0) === 2, 'Reasoning end copy aliases sampler state');
      await native._lcb_llama_sampler_reset(budget);
      check(Number(own(native.common_reasoning_budget_get_end_match_copy(budget)).size()) === 0, 'Reasoning reset retained the end match');
      check(endCopy.get(0) === 3, 'Reasoning reset invalidated the owned copy');
      await native._lcb_llama_sampler_accept(budget, 1);
      check(native.common_reasoning_budget_force(budget), 'Reasoning force failed');
      check(native.common_reasoning_budget_get_state(budget) === native.common_reasoning_budget_state.REASONING_BUDGET_FORCING, 'Reasoning force transition failed');
    } finally { await native._lcb_llama_sampler_free(budget); }
    check(endCopy.get(0) === 3, 'Sampler release invalidated the owned copy');

    // Bulk data stays in linear memory through the generated C ABI.
    const rgb = native._lcb_malloc(3n);
    check(rgb !== 0n, 'RGB allocation failed');
    let bitmap = 0n;
    try {
      native.HEAPU8.set([13, 29, 47], Number(rgb));
      bitmap = await native._lcb_mtmd_bitmap_init(1, 1, rgb);
      check(bitmap !== 0n && (await native._lcb_mtmd_bitmap_get_n_bytes(bitmap)) === 3n, 'RGB bitmap failed');
      check(native.HEAPU8[Number(await native._lcb_mtmd_bitmap_get_data(bitmap)) + 2] === 47, 'RGB pixels changed');
    } finally {
      if (bitmap) await native._lcb_mtmd_bitmap_free(bitmap);
      native._lcb_free(rgb);
    }
    const pcm = native._lcb_malloc(8n);
    check(pcm !== 0n, 'PCM allocation failed');
    let audio = 0n;
    try {
      const view = new DataView(native.HEAPU8.buffer, Number(pcm), 8);
      view.setFloat32(0, 0.25, true); view.setFloat32(4, -0.5, true);
      audio = await native._lcb_mtmd_bitmap_init_from_audio(2n, pcm);
      check(audio !== 0n && (await native._lcb_mtmd_bitmap_is_audio(audio)) === 1, 'PCM bitmap failed');
    } finally {
      if (audio) await native._lcb_mtmd_bitmap_free(audio);
      native._lcb_free(pcm);
    }
    check((await native._lcb_mtmd_helper_support_video(0n)) === 0, 'Unexpected subprocess video support');
    return { directGeneratedModule: true, nativeChatToolsAndHistory: true, reusedParser: true,
      nativeEnumsAndDefaults: true, reasoningSampler: true, multimodalBitmaps: true,
      trainedModelTools: false, multimodalModelInference: false };
  } finally {
    for (const object of objects.reverse()) object.delete();
  }
}
