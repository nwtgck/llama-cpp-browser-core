#!/usr/bin/env python3
"""Small GitHub.com API client for repository automation (standard library only)."""
from __future__ import annotations

import base64
from collections.abc import Iterator
import json
import os
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f'GitHub API HTTP {status}: {message}')


def full_sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('Expected a full lowercase Git commit SHA')
    return value


def repository_name(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value)
            or any(part in ('.', '..') for part in value.split('/'))):
        raise ValueError('Expected owner/repository')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class GitHub:
    """Explicit GitHub API operations used by the updater and runtime reporter.

    Keep endpoint paths and mutation payloads in these named operations so they
    can be audited together. Transport, pagination and credentials are private
    implementation details, not a general-purpose API for calling scripts.
    """

    def __init__(self, token: str | None = None):
        self._token = token if token is not None else os.environ.get('GH_TOKEN')

    def get_latest_release(self, repository: str) -> dict:
        return self._request('GET', f'/repos/{repository_name(repository)}/releases/latest')

    def iter_releases(self, repository: str) -> Iterator[dict]:
        return self._pages(f'/repos/{repository_name(repository)}/releases')

    def get_commit(self, repository: str, commit: str) -> dict:
        return self._request('GET', f'/repos/{repository_name(repository)}/commits/{full_sha(commit)}')

    def get_ref(self, repository: str, ref: str) -> dict:
        # Only named tag/branch refs are used here. Encoding prevents ref text
        # from changing the endpoint or introducing query parameters.
        if (not isinstance(ref, str) or not ref.startswith(('tags/', 'heads/'))
                or any(part in ('', '.', '..') for part in ref.split('/'))):
            raise ValueError('Expected a tags/... or heads/... ref')
        return self._request('GET', f'/repos/{repository_name(repository)}/git/ref/{quote(ref, safe="/")}')

    def get_tag(self, repository: str, tag_sha: str) -> dict:
        return self._request('GET', f'/repos/{repository_name(repository)}/git/tags/{full_sha(tag_sha)}')

    def compare_commits(self, repository: str, base: str, head: str) -> dict:
        return self._request('GET', f'/repos/{repository_name(repository)}/compare/{full_sha(base)}...{full_sha(head)}')

    def iter_pull_requests(self, repository: str, *, state: str = 'open',
                           head: str | None = None, base: str | None = None) -> Iterator[dict]:
        if state not in ('open', 'closed', 'all'):
            raise ValueError('Expected an open, closed or all pull-request state')
        params = {'state': state}
        if head is not None:
            params['head'] = head
        if base is not None:
            params['base'] = base
        return self._pages(f'/repos/{repository_name(repository)}/pulls', **params)

    def get_pull_request(self, repository: str, number: int) -> dict:
        return self._request('GET', f'/repos/{repository_name(repository)}/pulls/{self._positive_id(number)}')

    def iter_build_runs(self, repository: str, *, branch: str, head_sha: str) -> Iterator[dict]:
        path = f'/repos/{repository_name(repository)}/actions/workflows/build.yml/runs'
        for page in range(1, 11):
            params = urlencode({'branch': branch, 'head_sha': full_sha(head_sha),
                                'per_page': 100, 'page': page})
            values = self._request('GET', path + '?' + params)
            # GitHub caps filtered run searches at 1,000 results. A partial
            # history could hide a newly rerun older ID, so fail closed.
            if values.get('total_count', 0) > 1000:
                raise ValueError('Too many matching builds to establish the latest run safely')
            batch = values['workflow_runs']
            if not isinstance(batch, list):
                raise ValueError('Expected a workflow-run API array')
            yield from batch
            if len(batch) < 100 or page * 100 >= values.get('total_count', 1001):
                return
        raise ValueError('Build history pagination limit exceeded')

    def iter_run_artifacts(self, repository: str, run_id: int, *, name: str) -> Iterator[dict]:
        return self._pages(f'/repos/{repository_name(repository)}/actions/runs/{self._positive_id(run_id)}/artifacts',
                           collection='artifacts', name=name)

    def download_artifact_archive(self, repository: str, artifact_id: int, *, max_bytes: int) -> bytes:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError('Expected a positive archive size limit')
        url = (f'https://api.github.com/repos/{repository_name(repository)}'
               f'/actions/artifacts/{self._positive_id(artifact_id)}/zip')
        try:
            with build_opener(_NoRedirect()).open(Request(url, headers=self._headers()), timeout=60) as response:
                content = response.read(max_bytes + 1)
        except HTTPError as error:
            if error.code != 302:
                raise RuntimeError(f'Artifact download failed: HTTP {error.code}') from None
            location = error.headers.get('Location', '')
            parsed = urlparse(location)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Unexpected artifact download redirect')
            # GitHub redirects artifact downloads to a signed storage URL. This
            # new request deliberately has no API credentials or shared headers.
            with urlopen(Request(location), timeout=60) as response:
                content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError('Artifact archive exceeds the size limit')
        return content

    def iter_pull_request_comments(self, repository: str, number: int) -> Iterator[dict]:
        # Conversation comments use the issues endpoint, not review comments.
        return self._pages(f'/repos/{repository_name(repository)}/issues/{self._positive_id(number)}/comments')

    def create_pull_request_comment(self, repository: str, number: int, body: str) -> dict:
        return self._request('POST', f'/repos/{repository_name(repository)}/issues/{self._positive_id(number)}/comments',
                             {'body': body})

    def update_pull_request_comment(self, repository: str, comment_id: int, body: str) -> dict:
        return self._request('PATCH', f'/repos/{repository_name(repository)}/issues/comments/{self._positive_id(comment_id)}',
                             {'body': body})

    @staticmethod
    def _positive_id(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('Expected a positive GitHub identifier')
        return value

    def _headers(self) -> dict[str, str]:
        headers = {'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'llama-cpp-browser-core-automation'}
        if self._token:
            headers['Authorization'] = 'Bearer ' + self._token
        return headers

    def _request(self, method: str, path: str, data: dict | None = None):
        # Callers cannot choose a host. API redirects are not followed, so this
        # authenticated request cannot forward the credential to another host.
        if not path.startswith('/repos/') or '\n' in path or '\r' in path:
            raise ValueError('Expected a repository API path')
        headers = self._headers()
        body = None if data is None else json.dumps(data).encode('utf-8')
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request('https://api.github.com' + path, data=body, headers=headers, method=method)
        opener = build_opener(_NoRedirect())
        for attempt in range(3):
            try:
                with opener.open(request, timeout=60) as response:
                    content = response.read()
                return json.loads(content) if content else None
            except HTTPError as error:
                # Mutating requests are not retried: an ambiguous timeout must not
                # create duplicate comments after an ambiguous response.
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

    def _pages(self, path: str, *, collection: str | None = None, **params) -> Iterator[dict]:
        for page in range(1, 101):
            response = self._request('GET', path + '?' + urlencode({**params, 'per_page': 100, 'page': page}))
            values = response if collection is None else response[collection]
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
