"""Resolve and pin the local transport used by an owned Docker container."""

import os
import subprocess

from .core import KitError


def is_local(host):
    if not isinstance(host, str):
        return False
    if os.name == 'nt':
        return host.lower().startswith(('npipe:////./pipe/', 'npipe://./pipe/'))
    return host.startswith('unix:///')


def command(args, host=None):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    prefix = ['docker']
    if host is not None:
        if not is_local(host):
            raise KitError('Docker endpoint is outside the local emergency scope')
        prefix += ['--host', host]
        options['env'] = {key: value for key, value in os.environ.items()
                          if key not in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY')}
    return prefix + list(args), options


def selected_host(timeout=2):
    context = os.environ.get('DOCKER_CONTEXT')
    if not context and os.environ.get('DOCKER_HOST'):
        return os.environ['DOCKER_HOST']
    args = ['context', 'inspect', '--format', '{{(index .Endpoints "docker").Host}}']
    if context:
        args.append(context)
    argv, options = command(args)
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=timeout, **options)
    if result.returncode or not result.stdout.strip():
        raise KitError('Docker context is unavailable')
    return result.stdout.strip()
