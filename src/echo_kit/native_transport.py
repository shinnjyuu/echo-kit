"""Narrow adapter to the pinned agent-browser daemon ABI; no CLI auto-restart."""
from __future__ import annotations

import json
import math
import os
import re
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .core import KitError

MAX_MESSAGE = 16 * 1024 * 1024


class AgentConnection:
    def __init__(self, directory: Path, session: str, timeout: float = 10):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session):
            raise KitError('Invalid native daemon session identifier')
        if not math.isfinite(timeout) or timeout <= 0:
            raise KitError('Native daemon timeout must be positive')
        self.directory, self.session, self.timeout = Path(directory), session, timeout

    def _socket(self, timeout=None):
        if os.name == 'nt':
            port_file = self.directory / (self.session + '.port')
            if port_file.is_symlink() or port_file.stat().st_size > 16:
                raise OSError('Invalid native daemon port file')
            value = port_file.read_text(encoding='ascii').strip()
            if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
                raise OSError('Invalid native daemon port file')
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            address = ('127.0.0.1', int(value))
        else:
            path = self.directory / (self.session + '.sock')
            if path.is_symlink():
                raise OSError('Invalid native daemon socket')
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            address = str(path)
        connection.settimeout(self.timeout if timeout is None else timeout)
        try:
            connection.connect(address)
        except BaseException:
            connection.close()
            raise
        return connection

    def request(self, action: str, *, budget=None, **params):
        if not isinstance(action, str) or not action or 'id' in params or 'action' in params:
            raise KitError('Invalid native daemon action')
        request_id = uuid.uuid4().hex
        try:
            body = json.dumps({'id': request_id, 'action': action, **params},
                              ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
        except (ValueError, TypeError):
            raise KitError('Native action must contain finite JSON values') from None
        if len(body) > MAX_MESSAGE:
            raise KitError('Native action exceeds the message size limit')
        sent = 0
        connection = None
        started = time.monotonic()
        timeout = self.timeout if budget is None else min(budget, self.timeout)
        try:
            if timeout <= 0:
                raise TimeoutError()
            connection = self._socket() if budget is None else self._socket(timeout)
            # A request is sent once. Once any bytes leave this client, losing the
            # reply must never lead to re-running an action with business effects.
            while sent < len(body):
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                count = connection.send(body[sent:])
                if count == 0:
                    raise OSError('Disconnected')
                sent += count
            received = bytearray()
            while b'\n' not in received:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                chunk = connection.recv(min(65536, MAX_MESSAGE + 1 - len(received)))
                if not chunk:
                    raise OSError('Disconnected')
                received.extend(chunk)
                if len(received) > MAX_MESSAGE:
                    raise ValueError('Response too large')
            line, trailing = received.split(b'\n', 1)
            response = json.loads(line)
            if trailing.strip() or not isinstance(response, dict) or response.get('id') != request_id or type(response.get('success')) is not bool:
                raise ValueError('Invalid native daemon response')
            return response
        except (OSError, ValueError, UnicodeError):
            code = 'outcome_unknown' if sent else 'stopped'
            return {'id': request_id, 'success': False, 'code': code,
                    'error': 'Native action outcome is unknown; inspect state before continuing' if sent else
                             'Native daemon is unavailable; explicitly start a new Kit session'}
        finally:
            if connection is not None:
                connection.close()


def _number(value, *, positive=True, integer=True):
    try:
        result = int(value) if integer else float(value)
        if not math.isfinite(result) or result < (1 if positive and integer else 0) or (positive and result == 0):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise KitError('Invalid numeric native browser option') from None


def _options(values, choices):
    parsed, positionals = {}, []
    index = 0
    while index < len(values):
        token = values[index]
        if token in choices:
            key, converter = choices[token]
            if key in parsed:
                raise KitError('Duplicate native browser option: ' + token)
            if converter is None:
                parsed[key] = True
            else:
                index += 1
                if index >= len(values):
                    raise KitError('Missing native browser option value: ' + token)
                parsed[key] = converter(values[index])
        elif token.startswith('-'):
            raise KitError('Unsupported native browser option: ' + token)
        else:
            positionals.append(token)
        index += 1
    return parsed, positionals


def translate(command: list[str]) -> dict:
    """Translate the small supported command surface, without upstream launch/config flags."""
    if not isinstance(command, list) or not command or any(not isinstance(value, str) or '\x00' in value for value in command):
        raise KitError('Expected a native browser command argument list')
    name, *args = command
    if name == 'open' and len(args) == 1:
        try:
            url = urlsplit(args[0])
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError()
        except ValueError:
            raise KitError('Native open requires an HTTP(S) URL without embedded credentials') from None
        return {'action': 'navigate', 'url': args[0]}
    if name == 'snapshot':
        options, rest = _options(args, {
            '-i': ('interactive', None), '--interactive': ('interactive', None),
            '-c': ('compact', None), '--compact': ('compact', None),
            '-s': ('selector', str), '--selector': ('selector', str),
            '-d': ('maxDepth', lambda value: _number(value, positive=False)),
            '--depth': ('maxDepth', lambda value: _number(value, positive=False)),
        })
        if not rest:
            return {'action': 'snapshot', **options}
    if name == 'click' and len(args) == 1:
        return {'action': 'click', 'selector': args[0]}
    if name == 'fill' and len(args) == 2:
        return {'action': 'fill', 'selector': args[0], 'value': args[1]}
    if name == 'get' and args:
        simple = {'url': 'url', 'title': 'title'}
        selectors = {'text': 'gettext', 'html': 'innerhtml', 'value': 'inputvalue',
                     'box': 'boundingbox', 'styles': 'styles', 'count': 'count'}
        if args[0] in simple and len(args) == 1:
            return {'action': simple[args[0]]}
        if args[0] in selectors and len(args) == 2:
            return {'action': selectors[args[0]], 'selector': args[1]}
        if args[0] == 'attr' and len(args) == 3:
            return {'action': 'getattribute', 'selector': args[1], 'attribute': args[2]}
    if name == 'wait':
        options, rest = _options(args, {'--text': ('text', str), '--url': ('url', str),
                                      '--fn': ('expression', str), '--load': ('state', str),
                                      '--timeout': ('timeout', _number)})
        modes = [key for key in ('text', 'url', 'expression', 'state') if key in options]
        if len(modes) <= 1 and len(rest) <= 1 and not (rest and modes):
            action = {'url': 'waitforurl', 'expression': 'waitforfunction', 'state': 'waitforloadstate'}
            if options.get('state') not in (None, 'load', 'domcontentloaded', 'networkidle'):
                raise KitError('Unsupported page load state')
            if rest:
                if rest[0].isdigit():
                    if 'timeout' in options:
                        raise KitError('A numeric wait cannot also specify --timeout')
                    options['timeout'] = _number(rest[0], positive=False)
                else:
                    options['selector'] = rest[0]
            if options:
                return {'action': action.get(modes[0], 'wait') if modes else 'wait', **options}
    if name == 'screenshot':
        options, rest = _options(args, {'--full': ('fullPage', None), '-f': ('fullPage', None),
                                      '--selector': ('selector', str), '--annotate': ('annotate', None),
                                      '--ready-selector': ('ready_selector', str)})
        if len(rest) == 1:
            return {'action': 'screenshot', 'format': 'png', 'path': rest[0], **options}
    if name == 'diff' and args and args[0] == 'screenshot':
        options, rest = _options(args[1:], {'--baseline': ('baseline', str), '--output': ('output', str),
                                           '--selector': ('selector', str), '--full': ('fullPage', None),
                                           '--threshold': ('threshold', lambda value: _number(value, positive=False, integer=False))})
        if not rest and 'baseline' in options and 'output' in options:
            if options.get('threshold', .1) > 1:
                raise KitError('Screenshot threshold must be between 0 and 1')
            return {'action': 'diff_screenshot', **options}
    if name == 'trace':
        if args == ['start']:
            return {'action': 'trace_start'}
        if len(args) == 2 and args[0] == 'stop':
            return {'action': 'trace_stop', 'path': args[1]}
    if name == 'tab':
        if not args or args == ['list']:
            return {'action': 'tab_list'}
        if len(args) == 1 and args[0] not in ('new', 'close') and not args[0].startswith('-'):
            return {'action': 'tab_switch', 'tabId': args[0]}
    raise KitError('Unsupported native browser command; session lifecycle and configuration are managed by Echo Kit')
