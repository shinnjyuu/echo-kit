"""Optional real debugger clients for synthetic bundled fixtures, not public APIs."""
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from importlib.resources import files

import pytest

from echo_kit.core import Workspace, init_workspace, KitError
from echo_kit.operations import Journal, operation, service_resources, ResourceBusy


@pytest.mark.parametrize('failure', ['conflict', 'changed', 'connect', 'interrupt', 'cleanup'])
def test_debug_reservation_failure_boundaries(tmp_path, failure):
    """Controlled rehearsal of the documented existing-operation lifecycle."""
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    ws.cfg['services'] = {'api': {'resource_id': 'fixture-api', 'depends': ['db']},
                          'db': {'resource_id': 'fixture-db'}}
    resources = service_resources(ws, ['api'], builds=False, access='exclusive')
    assert 'service:fixture-db' in resources
    journal = Journal()
    other = journal.acquire(ws, 'other', {'unrelated': 'exclusive'})
    other_bytes = journal.file(other).read_bytes()
    blocker = journal.acquire(ws, 'using-db', {'service:fixture-db': 'shared'}) if failure == 'conflict' else None
    attempted = []
    oid = None
    with pytest.raises((ResourceBusy, KitError, ConnectionError, KeyboardInterrupt, RuntimeError)):
        with operation(ws, 'debug.fixture', resources) as oid:
            expected, actual = 'generation-1', 'generation-2' if failure == 'changed' else 'generation-1'
            if actual != expected:
                raise KitError('Instance changed before attachment')
            attempted.append('connect')
            if failure == 'connect':
                raise ConnectionError('Synthetic connection refused before attachment')
            try:
                if failure == 'interrupt':
                    raise KeyboardInterrupt()
            finally:
                if failure in ('interrupt', 'cleanup'):
                    journal.update(oid, requires_resolution=True)
                if failure == 'cleanup':
                    raise RuntimeError('Synthetic resume not confirmed')
    assert attempted == ([] if failure in ('conflict', 'changed') else ['connect'])
    assert journal.file(other).read_bytes() == other_bytes
    if oid:
        assert bool(journal.show(oid)['held']) == (failure in ('interrupt', 'cleanup'))
    if blocker:
        assert journal.show(blocker)['held']
        journal.release(blocker)
    journal.release(other)


def fixture(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(files('echo_kit').joinpath('skills/references', name).read_bytes())
    return path


def line(path, marker):
    return next(i for i, text in enumerate(path.read_text().splitlines(), 1) if marker in text)


def test_stream_events_without_pause(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    p = subprocess.run([node, str(fixture(tmp_path, 'runtime-node.cjs'))], capture_output=True, text=True, timeout=10)
    assert p.returncode == 0
    assert 'DO_NOT_REPORT' not in p.stdout + p.stderr
    events = [json.loads(x) for x in p.stdout.splitlines()]
    assert [e['event'] for e in events] == ['input', 'input', 'parsed', 'end']
    assert [e['sequence'] for e in events] == [1, 2, 3, 4]
    assert {e['id'] for e in events} == {'synthetic-1'}
    elapsed = [int(e['elapsed_ns']) for e in events]
    assert elapsed == sorted(elapsed)
    assert events[-1]['result'] == 'rejected'


class DAP:
    def __init__(self, sock):
        self.sock, self.seq, self.messages = sock, 0, []
        self.stream = sock.makefile('rb')

    def send(self, command, arguments):
        self.seq += 1
        body = json.dumps(dict(seq=self.seq, type='request', command=command, arguments=arguments)).encode()
        self.sock.sendall(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
        return self.seq

    def wait(self, predicate):
        while True:
            for i, message in enumerate(self.messages):
                if predicate(message):
                    return self.messages.pop(i)
            headers = {}
            while True:
                row = self.stream.readline()
                if not row:
                    raise EOFError('Debugger disconnected')
                if row == b'\r\n':
                    break
                key, value = row.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            self.messages.append(json.loads(self.stream.read(int(headers['content-length']))))

    def reply(self, seq):
        msg = self.wait(lambda m: m.get('request_seq') == seq)
        assert msg['success'], msg.get('message')
        return msg.get('body', {})

    def call(self, command, **args):
        return self.reply(self.send(command, args))

    def event(self, name):
        return self.wait(lambda m: m.get('event') == name).get('body', {})


@pytest.mark.skipif(os.environ.get('ECHO_KIT_DEBUG_TESTS') != '1', reason='Explicit real debugger evidence')
def test_python_real_breakpoint_step_and_disconnect(tmp_path):
    pytest.importorskip('debugpy')
    path = fixture(tmp_path, 'runtime-python.py')
    # Adapter is owned by this test; launch requests create only the synthetic child.
    from test_core import free_port
    port = free_port()
    proc = subprocess.Popen([sys.executable, '-m', 'debugpy.adapter', '--host', '127.0.0.1', '--port', str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sock = None
    dap = None
    disconnected = False
    try:
        deadline = time.monotonic() + 15
        while sock is None:
            try:
                sock = socket.create_connection(('127.0.0.1', port), timeout=10)
            except ConnectionRefusedError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.05)
        dap = DAP(sock)
        dap.call('initialize', adapterID='python', linesStartAt1=True, columnsStartAt1=True)
        launch = dap.send('launch', {'program': str(path), 'console': 'internalConsole', 'justMyCode': True})
        dap.event('initialized')
        source = {'path': str(path)}
        result = dap.call('setBreakpoints', source=source, breakpoints=[{'line': line(path, 'accepted =')}])
        assert result['breakpoints'][0]['verified']
        dap.call('configurationDone')
        dap.reply(launch)
        stopped = dap.event('stopped')
        thread = stopped['threadId']
        frames = dap.call('stackTrace', threadId=thread)['stackFrames']
        assert frames[0]['name'] == 'classify'
        for name in ('amount', 'threshold'):
            assert dap.call('evaluate', expression=name, frameId=frames[0]['id'], context='watch')['result'] == '10'
        dap.call('next', threadId=thread)
        dap.event('stopped')
        frame = dap.call('stackTrace', threadId=thread)['stackFrames'][0]
        assert dap.call('evaluate', expression='accepted', frameId=frame['id'], context='watch')['result'] == 'False'
        dap.call('setBreakpoints', source=source, breakpoints=[])
        dap.call('continue', threadId=thread)
        dap.event('terminated')
        outputs = ''.join(m.get('body', {}).get('output', '') for m in dap.messages)
        assert 'rejected' in outputs and 'DO_NOT_REPORT' not in outputs
        dap.call('disconnect', terminateDebuggee=False)
        disconnected = True
    finally:
        if dap:
            if not disconnected:
                try:
                    # Only the synthetic child launched by this test is terminated.
                    dap.call('disconnect', terminateDebuggee=True)
                except (OSError, EOFError, AssertionError):
                    pass
            dap.stream.close()
        if sock:
            sock.close()
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(os.environ.get('ECHO_KIT_DEBUG_TESTS') != '1', reason='Explicit real debugger evidence')
def test_node_real_breakpoint_step_and_disconnect(tmp_path):
    websocket = pytest.importorskip('websocket')
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    path = fixture(tmp_path, 'runtime-node.cjs')
    proc = subprocess.Popen([node, '--inspect-brk=127.0.0.1:0', str(path)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    def reader():
        for row in proc.stderr:
            lines.put(row)
    threading.Thread(target=reader, daemon=True).start()
    ws = None
    try:
        endpoint = lines.get(timeout=10).strip().split('Debugger listening on ')[1]
        ws = websocket.create_connection(endpoint, timeout=10, suppress_origin=True)
        seq, pending = 0, []
        def wait(predicate):
            while True:
                for i, msg in enumerate(pending):
                    if predicate(msg):
                        return pending.pop(i)
                pending.append(json.loads(ws.recv()))
        def call(method, **params):
            nonlocal seq
            seq += 1
            ws.send(json.dumps({'id': seq, 'method': method, 'params': params}))
            result = wait(lambda m: m.get('id') == seq)
            assert 'error' not in result
            return result.get('result', {})
        def paused():
            return wait(lambda m: m.get('method') == 'Debugger.paused')['params']
        call('Debugger.enable')
        call('Runtime.runIfWaitingForDebugger')
        first = paused()
        script = first['callFrames'][0]['location']['scriptId']
        bp = call('Debugger.setBreakpoint', location={'scriptId': script, 'lineNumber': line(path, 'const accepted') - 1})
        call('Debugger.resume')
        hit = paused()
        frame = hit['callFrames'][0]
        for name in ('amount', 'threshold'):
            assert call('Debugger.evaluateOnCallFrame', callFrameId=frame['callFrameId'], expression=name)['result']['value'] == 10
        call('Debugger.stepOver')
        frame = paused()['callFrames'][0]
        assert call('Debugger.evaluateOnCallFrame', callFrameId=frame['callFrameId'], expression='accepted')['result']['value'] is False
        call('Debugger.removeBreakpoint', breakpointId=bp['breakpointId'])
        call('Debugger.resume')
        call('Debugger.disable')
        ws.close()
        ws = None
        proc.wait(timeout=10)
        output = proc.stdout.read()
        assert 'rejected' in output and 'DO_NOT_REPORT' not in output
    finally:
        if ws:
            ws.close()
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
