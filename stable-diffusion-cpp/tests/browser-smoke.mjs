// Real Wasm/Worker/filesystem/callback boundary tests. No model or GPU inference.
import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.argv[2] ?? 'dist/package');
const { chromium } = await import(pathToFileURL(path.resolve('../.tools/browser/node_modules/playwright/index.mjs')).href);
const server = createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname);
    if (pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Image core boundary test</title>'); return; }
    const file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', file.endsWith('.mjs') ? 'text/javascript' : file.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const profiles = JSON.parse(await readFile('config/profiles.json', 'utf8'));
  for (const profile of Object.keys(profiles)) {
    for (const variant of ['browser', 'test']) {
      const result = await page.evaluate(async ({ profile, variant }) => {
        const run = async ({ base, variant }) => {
          const create = (await import(base + 'core.mjs')).default;
          const response = await fetch(base + 'core.wasm');
          if (!response.ok) throw Error('Missing Wasm');
          const wasmBinary = new Uint8Array(await response.arrayBuffer());
          const logs = [], progress = [];
          const core = await create({ wasmBinary,
            locateFile(name) { if (name !== 'core.wasm') throw Error('Unexpected side file'); return base + name; },
            onLog: (level, text) => logs.push([level, text]),
            onProgress: (...args) => progress.push(args),
          });
          if (core._sdb_abi_version() !== 1 || !core.FS || !core.WORKERFS) throw Error('Missing bridge/filesystem boundary');
          // WORKERFS must actually read a browser Blob, not just exist as a name.
          core.FS.mkdir('/models');
          core.FS.mount(core.WORKERFS, { blobs: [{ name: 'probe.bin', data: new Blob([new Uint8Array([2, 3, 5, 7])]) }] }, '/models');
          if (Array.from(core.FS.readFile('/models/probe.bin')).join(',') !== '2,3,5,7') throw Error('WORKERFS read mismatch');
          core.FS.unmount('/models');
          for (let attempt = 0; attempt < 2; attempt++) {
            if (await core.ccall('sdb_load', 'number', ['string'], ['[]'], { async: true }) !== 0) throw Error('Invalid input was accepted');
            if (!core.UTF8ToString(core._sdb_error())) throw Error('No diagnostic');
          }
          if (logs.length < 2 || !logs.every(([, text]) => text.includes('object'))) throw Error('Factory log callbacks did not arrive');
          if (variant === 'test') {
            core._sdb_test_callbacks();
            if (JSON.stringify(progress) !== '[[1,4,0.125]]' || logs.at(-1)[1] !== 'browser callback probe') throw Error('Wasm callback path failed');
            core.setCallbacks({ onProgress() { throw Error('Listener failure'); }, onLog() { throw Error('Listener failure'); } });
            core._sdb_test_callbacks(); // Listener errors must not cross into Wasm.
          } else if (core._sdb_test_callbacks !== undefined) {
            throw Error('Test-only hook leaked into production');
          }
          core.setCallbacks({});
          const count = logs.length;
          await core.ccall('sdb_load', 'number', ['string'], ['[]'], { async: true });
          if (logs.length !== count) throw Error('Callbacks were not cleared');
          core._sdb_unload();
          return { passed: true, scope: 'real-Wasm Worker, WORKERFS, invalid input and callbacks; no model/GPU inference' };
        };
        const source = `const run = ${run.toString()}; onmessage = async ({ data }) => { try { postMessage({ result: await run(data) }); } catch (error) { postMessage({ error: String(error.stack || error) }); } };`;
        const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
        const worker = new Worker(url, { type: 'module' });
        let timer;
        try {
          return await new Promise((resolve, reject) => {
            timer = setTimeout(() => reject(Error(`Worker smoke timed out: ${profile}/${variant}`)), 120000);
            worker.onerror = event => reject(Error(event.message));
            worker.onmessage = ({ data }) => data.error ? reject(Error(data.error)) : resolve({ profile, variant, ...data.result });
            worker.postMessage({ base: `${location.origin}/profiles/${profile}/${variant}/`, variant });
          });
        } finally {
          clearTimeout(timer); worker.terminate(); URL.revokeObjectURL(url);
        }
      }, { profile, variant });
      console.log(result);
      const file = path.resolve('build', profile, variant, 'provenance.json');
      const provenance = JSON.parse(await readFile(file, 'utf8'));
      provenance.validation.browserSmoke = true;
      provenance.validation.browserSmokeScope = result.scope;
      await writeFile(file, JSON.stringify(provenance, null, 2) + '\n');
    }
  }
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
