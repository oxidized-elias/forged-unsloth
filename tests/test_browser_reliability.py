"""Exercise the shipped TypeScript helpers with Node's built-in type stripping."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js required for frontend regression checks')
def test_browser_lifecycle_and_read_retry():
    source = '''
import assert from 'node:assert/strict';
const { retryableOnce, waitForStartup, retryBrowserRead } = await import(MODULE);
let ready;
const order = [];
const once = retryableOnce(() => new Promise(resolve => { ready = () => { order.push('listener ready'); resolve(); }; }));
const open = async () => { await once(); order.push('open page'); };
const a = open(), b = open();
await Promise.resolve();
assert.deepEqual(order, []);
ready(); await Promise.all([a, b]);
assert.deepEqual(order, ['listener ready', 'open page', 'open page']);
let attempts = 0;
const failed = retryableOnce(async () => { if (++attempts === 1) throw Error('IPC failed'); });
await assert.rejects(failed()); await failed(); assert.equal(attempts, 2);
await waitForStartup([Promise.reject(Error('optional catalog failed'))], 100);
await waitForStartup([new Promise(() => {})], 5);
const controller = new AbortController();
let reads = 0;
assert.equal(await retryBrowserRead(async () => {
  if (++reads === 1) throw Object.assign(Error('temporary unavailable'), {status: 503});
  return 'loaded';
}, controller.signal, true), 'loaded');
assert.equal(reads, 2);
let writes = 0;
await assert.rejects(retryBrowserRead(async () => { writes++; throw new TypeError('network'); }, controller.signal, false));
assert.equal(writes, 1);
let denied = 0;
await assert.rejects(retryBrowserRead(async () => { denied++; throw Object.assign(Error('denied'), {status: 403}); }, controller.signal, true));
assert.equal(denied, 1);
let cancelled = 0;
const pending = retryBrowserRead(async () => { cancelled++; throw new TypeError('network'); }, controller.signal, true);
setTimeout(() => controller.abort(), 5);
await assert.rejects(pending);
assert.equal(cancelled, 1);
console.log('Browser lifecycle and retries passed');
'''.replace('MODULE', json.dumps((ROOT / 'frontend/browser-reliability.ts').as_uri()))
    result = subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-'],
                            input=source, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Browser lifecycle and retries passed' in result.stdout
