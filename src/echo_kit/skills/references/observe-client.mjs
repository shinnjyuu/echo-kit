// Import this file in a persistent Node tool. The CLI owns resource management.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

export async function openObserver({ workspace, url, services = [], auth, adapter, actor, reference, referenceRoot, spec,
  timeoutMs = 10000, startupTimeoutMs = 900000, executable = 'echo-kit' }) {
  const args = ['--workspace', workspace];
  if (actor) args.push('--actor', actor);
  args.push('browser', 'observe', '--url', url, '--timeout-ms', String(timeoutMs));
  for (const name of services) args.push('--service', name);
  if (auth) args.push('--auth', auth);
  if (adapter) args.push('--adapter', adapter);
  if (reference) args.push('--reference', reference);
  if (referenceRoot) args.push('--reference-root', referenceRoot);
  if (spec) args.push('--spec', spec);
  const child = spawn(executable, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let sequence = 0, closed = false, broken = false, chain = Promise.resolve();
  const exited = new Promise((resolve, reject) => {
    child.once('exit', code => resolve(code));
    child.once('error', reject);
  });
  exited.catch(() => {});
  child.stderr.on('data', chunk => process.stderr.write(chunk));
  child.stdin.on('error', () => { broken = true; });
  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity })[Symbol.asyncIterator]();
  async function receive(waitMs = timeoutMs * 4 + 15000) {
    let timer;
    try {
      const next = await Promise.race([lines.next(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Observation response timed out')), waitMs);
      })]);
      if (next.done) throw new Error('Observation process exited');
      return JSON.parse(next.value);
    } catch (error) {
      broken = true;
      child.stdin.end();
      throw error;
    } finally { clearTimeout(timer); }
  }
  const startup = await receive(startupTimeoutMs);
  if (startup.event !== 'ready') {
    child.stdin.end();
    throw new Error('Observer preparation failed: ' + JSON.stringify(startup));
  }
  function send(op, options = {}) {
    const task = chain.then(async () => {
      if (closed || broken) throw new Error('Observation session is closed');
      const id = String(++sequence);
      child.stdin.write(JSON.stringify({ ...options, op, id }) + '\n');
      const result = await receive();
      if (result.id !== id) {
        broken = true;
        child.stdin.end();
        throw new Error('Observation response id mismatch');
      }
      return result;
    });
    chain = task.catch(() => {});
    return task;
  }
  return { startup,
    observe: options => send('observe', options),
    exec: (command, options = {}) => send('exec', { ...options, command }),
    inspect: options => send('inspect', options),
    reference: options => send('reference', options),
    compare: options => send('compare', options),
    navigate: url => send('navigate', { url }),
    reload: () => send('reload'),
    async close() {
      if (closed) return { status: 'closed' };
      if (broken) { await exited; closed = true; return { status: 'unconfirmed' }; }
      const result = await send('close');
      closed = true;
      child.stdin.end();
      if (result.status !== 'closed' || await exited !== 0) throw new Error('Observation cleanup unconfirmed');
      return result;
    },
  };
}
