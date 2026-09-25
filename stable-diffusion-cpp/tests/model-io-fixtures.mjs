// Self-contained for serialization into the real-Wasm browser Worker.
// Virtual sparse files exercise offsets, not trained model inference.
export function makeModelIoFixtures(gib) {
  function sparse(path, header, size, payloadOffset) {
    let readBytes = 0;
    return { path, offset: payloadOffset, size, source: {
      size,
      read(destination, offset) {
        if (!Number.isSafeInteger(offset) || offset < 0 || offset > size || destination.length > 65536) throw Error('Invalid/uncapped model fixture read');
        const length = Math.min(destination.length, size - offset);
        readBytes += length;
        if (readBytes > 4 * 1024 * 1024) throw Error('Native code scanned sparse weight payload');
        destination.fill(0, 0, length);
        if (offset < header.length) destination.set(header.subarray(offset, Math.min(header.length, offset + length)));
        const value = [0, 0, 128, 63];
        for (let i = 0; i < 4; i++) if (payloadOffset + i >= offset && payloadOffset + i < offset + length) destination[payloadOffset + i - offset] = value[i];
        return length;
      },
    }, summary() { return { size, offset: payloadOffset, readBytes }; } };
  }
  const gap = gib * 2 ** 30;
  const text = new TextEncoder().encode(JSON.stringify({ padding: { dtype: 'U8', shape: [gap], data_offsets: [0, gap] }, weight: { dtype: 'F32', shape: [1], data_offsets: [gap, gap + 4] } }));
  const header = new Uint8Array(8 + text.length);
  new DataView(header.buffer).setBigUint64(0, BigInt(text.length), true); header.set(text, 8);
  const safetensors = sparse('/models/sparse.safetensors', header, header.length + gap + 4, header.length + gap);
  const shards = [0, 1].map(index => {
    const bytes = [];
    const u32 = value => { for (let i = 0; i < 4; i++) bytes.push(value >>> (8 * i) & 255); };
    const u64 = value => { const n = BigInt(value); for (let i = 0; i < 8; i++) bytes.push(Number(n >> BigInt(8 * i) & 255n)); };
    const str = value => { const data = new TextEncoder().encode(value); u64(data.length); bytes.push(...data); };
    u32(0x46554747); u32(3); u64(1); u64(3);
    for (const [key, value] of [['split.no', index], ['split.count', 2], ['split.tensors.count', 2]]) { str(key); u32(4); u32(value); }
    str('weight' + index); u32(1); u64(1); u32(0); u64(0);
    const header = new Uint8Array(Math.ceil(bytes.length / 32) * 32); header.set(bytes);
    return sparse(`/models/parts-0000${index + 1}-of-00002.gguf`, header, header.length + 4, header.length);
  });
  return { safetensors, shards };
}
