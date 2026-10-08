// One browser server, isolated client contexts, and a read-only local desktop.
const { spawn } = require('node:child_process');
const { existsSync } = require('node:fs');
const net = require('node:net');
const { chromium } = require('playwright-core');
const children = [];
let server;
let stopping = false;

async function shutdown(code) {
  if (stopping) return;
  stopping = true;
  const deadline = setTimeout(() => process.exit(code), 5000);
  deadline.unref();
  if (server) await server.close().catch(() => {});
  for (const child of children) child.kill('SIGTERM');
  process.exit(code);
}

function start(command, args) {
  const child = spawn(command, args, { stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error); shutdown(1); });
  child.on('exit', (code, signal) => {
    if (!stopping) {
      console.error(`${command} exited: ${code ?? signal}`);
      shutdown(1);
    }
  });
}

async function until(check, label) {
  const deadline = Date.now() + 15000;
  while (!stopping && Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`${label} did not start`);
}

function portReady(port) {
  return new Promise(resolve => {
    const socket = net.connect(port, '127.0.0.1');
    socket.setTimeout(500);
    const finish = value => { socket.destroy(); resolve(value); };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    socket.once('timeout', () => finish(false));
  });
}

process.on('SIGTERM', () => shutdown(0));
process.on('SIGINT', () => shutdown(0));
(async () => {
  const headless = process.env.ECHO_BROWSER_HEADLESS === 'true';
  if (!headless) {
    start('Xvfb', [':99', '-screen', '0', '1440x1000x24', '-ac', '-nolisten', 'tcp']);
    await until(() => existsSync('/tmp/.X11-unix/X99'), 'Display');
    // Read-only is enforced here, not by a bypassable viewer query parameter.
    start('x11vnc', ['-display', ':99', '-forever', '-shared', '-viewonly', '-nosel',
      '-nopw', '-listen', '127.0.0.1', '-rfbport', '5900']);
    await until(() => portReady(5900), 'VNC');
    start('websockify', ['--web', '/usr/share/novnc', '6080', '127.0.0.1:5900']);
    await until(() => portReady(6080), 'Viewer');
  }
  server = await chromium.launchServer({headless, host: '0.0.0.0', port: 3000, wsPath: '/',
    args: ['--window-position=0,0', '--window-size=1440,1000']});
  server.on('close', () => { if (!stopping) shutdown(1); });
  console.log(JSON.stringify({ready: true, headless, endpoint: server.wsEndpoint()}));
})().catch(error => { console.error(error); shutdown(1); });
