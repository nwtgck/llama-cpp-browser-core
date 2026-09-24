// ABI-only real-Wasm browser smoke: does not load a model or claim GPU inference.
import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.argv[2] ?? 'dist/package');
const { chromium } = await import(pathToFileURL(path.resolve('../llama-cpp/.tools/browser/node_modules/playwright/index.mjs')).href);
const server = createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname);
    if (pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Image core ABI test</title>'); return; }
    const file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', file.endsWith('.mjs') ? 'text/javascript' : 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(120000);
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const profiles = JSON.parse(await readFile('config/profiles.json', 'utf8'));
  for (const profile of Object.keys(profiles)) {
    for (const variant of ['browser', 'test']) {
      const result = await page.evaluate(async ({ profile, variant }) => {
        const base = `/profiles/${profile}/${variant}/`;
        const create = (await import(base + 'core.mjs')).default;
        const wasmBinary = new Uint8Array(await (await fetch(base + 'core.wasm')).arrayBuffer());
        const core = await create({ wasmBinary, locateFile(name) { if (name !== 'core.wasm') throw Error('Unexpected side file'); return base + name; } });
        if (core._sdb_abi_version() !== 1 || !core.FS || !core.WORKERFS) throw Error('Missing bridge/filesystem boundary');
        if (await core.ccall('sdb_load', 'number', ['string'], ['[]'], { async: true }) !== 0) throw Error('Invalid input was accepted');
        if (!core.UTF8ToString(core._sdb_error())) throw Error('No diagnostic');
        core._sdb_unload();
        return { profile, variant, passed: true, scope: 'real-Wasm ABI and invalid input; no GPU or model' };
      }, { profile, variant });
      console.log(result);
      const file = path.resolve('build', profile, variant, 'provenance.json');
      const provenance = JSON.parse(await readFile(file, 'utf8'));
      provenance.validation.browserSmoke = true;
      provenance.validation.browserSmokeScope = result.scope;
      await writeFile(file, JSON.stringify(provenance, null, 2) + '\n');
      // Drop the Wasm instance/realm before allocating the next one.
      await page.reload();
    }
  }
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
