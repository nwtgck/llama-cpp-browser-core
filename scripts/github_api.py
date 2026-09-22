#!/usr/bin/env python3
"""Small GitHub.com API client for repository automation (standard library only)."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f'GitHub API HTTP {status}: {message}')


def full_sha(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('Expected a full lowercase Git commit SHA')
    return value


def repository_name(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('Expected owner/repository')
    return value


class GitHub:
    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.environ.get('GH_TOKEN')

    def request(self, method: str, path: str, data=None):
        # No user-controlled host: the credential is sent only to api.github.com.
        if not path.startswith('/repos/') or '\n' in path or '\r' in path:
            raise ValueError('Expected a repository API path')
        headers = {'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'llama-cpp-browser-core-automation'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        body = None if data is None else json.dumps(data).encode('utf-8')
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request('https://api.github.com' + path, data=body, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urlopen(request, timeout=60) as response:
                    content = response.read()
                return json.loads(content) if content else None
            except HTTPError as error:
                # Mutating requests are not retried: an ambiguous timeout must not
                # create duplicate PRs, comments, commits, or workflow dispatches.
                if method == 'GET' and error.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    message = json.loads(error.read()).get('message', 'Request failed')
                except (ValueError, AttributeError):
                    message = 'Request failed'
                raise ApiError(error.code, str(message)) from None
            except URLError:
                raise RuntimeError('GitHub API connection failed') from None
        raise RuntimeError('GitHub API retry limit exceeded')

    def pages(self, path: str, **params):
        for page in range(1, 101):
            values = self.request('GET', path + '?' + urlencode({**params, 'per_page': 100, 'page': page}))
            if not isinstance(values, list):
                raise ValueError('Expected a paginated API array')
            yield from values
            if len(values) < 100:
                return
        raise RuntimeError('API pagination limit exceeded; refusing a partial result')


def git(*args: str, cwd, check: bool = True, env=None):
    return subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True,
                          check=check, env=env)


def git_auth_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    index = int(env.get('GIT_CONFIG_COUNT', '0'))
    env.update({
        'GIT_CONFIG_COUNT': str(index + 1),
        f'GIT_CONFIG_KEY_{index}': 'http.https://github.com/.extraheader',
        f'GIT_CONFIG_VALUE_{index}': 'AUTHORIZATION: basic ' + base64.b64encode(
            ('x-access-token:' + token).encode()).decode(),
        'GIT_TERMINAL_PROMPT': '0',
    })
    return env
