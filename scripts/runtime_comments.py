#!/usr/bin/env python3
"""Reconcile runtime comments using trusted default-branch code and bounded report data."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import zipfile

from github_api import GitHub, full_sha, repository_name

MARKER = '<!-- lcb-runtime-artifact-comment:v1 -->'
START = '<!-- lcb-published-runtime:start -->'
END = '<!-- lcb-published-runtime:end -->'
BOT = 'github-actions[bot]'
REPORT_FILES = {'report.json', 'consumer-update.md', 'consumer-update.yaml'}
MAX_ARCHIVE = 256 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def download_report(api: GitHub, repository: str, artifact_id: int) -> bytes:
    # GitHub redirects artifact downloads to a signed storage URL. The token is
    # deliberately NOT forwarded to that host, and no archive code is executed.
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'lcb-runtime-reporter'}
    if api.token:
        headers['Authorization'] = 'Bearer ' + api.token
    url = f'https://api.github.com/repos/{repository_name(repository)}/actions/artifacts/{int(artifact_id)}/zip'
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=60) as response:
            content = response.read(MAX_ARCHIVE + 1)
    except HTTPError as error:
        if error.code != 302:
            raise RuntimeError(f'Artifact download failed: HTTP {error.code}') from None
        location = error.headers['Location']
        parsed = urlparse(location)
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise ValueError('Unexpected artifact download redirect')
        with urlopen(Request(location), timeout=60) as response:
            content = response.read(MAX_ARCHIVE + 1)
    if len(content) > MAX_ARCHIVE:
        raise ValueError('Report archive exceeds the size limit')
    return content


def decode_report(content: bytes, repository: str, run: dict) -> str:
    if len(content) > MAX_ARCHIVE:
        raise ValueError('Report archive exceeds the size limit')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) != len(REPORT_FILES) or {item.filename for item in entries} != REPORT_FILES:
            raise ValueError('Unexpected or duplicate files in report archive')
        if any(item.file_size > 60000 or stat.S_ISLNK(item.external_attr >> 16) for item in entries):
            raise ValueError('Unsafe report archive entry')
        envelope = json.loads(archive.read('report.json'))
        raw = archive.read('consumer-update.md')
        if (envelope.get('schemaVersion') != 1 or envelope.get('repository') != repository
                or envelope.get('runId') != run['id'] or envelope.get('runAttempt') != run['run_attempt']
                or envelope.get('sourceCommit') != run['head_sha']):
            raise ValueError('Report identity does not match the source workflow run')
        full_sha(envelope['sourceCommit'])
        commit = full_sha(envelope['artifactCommit'])
        if hashlib.sha256(raw).hexdigest() != envelope.get('markdownSha256'):
            raise ValueError('Report Markdown digest mismatch')
        text = raw.decode('utf-8')
        if len(raw) > 55000 or START in text or END in text or MARKER in text:
            raise ValueError('Unexpected report delimiters or length')
        if f'github:{repository}#{commit}' not in text or run['head_sha'] not in text:
            raise ValueError('Report body does not identify its source and artifact')
        return text


def same_repo_pr(pr: dict, repository: str) -> bool:
    return (pr['state'] == 'open' and (pr['head'].get('repo') or {}).get('full_name') == repository
            and pr['head']['ref'] != 'artifacts' and not pr['head']['ref'].startswith('artifacts/'))


def latest_run(api: GitHub, repository: str, pr: dict) -> dict | None:
    runs = []
    for page in range(1, 11):
        params = urlencode({'branch': pr['head']['ref'], 'head_sha': full_sha(pr['head']['sha']),
                            'per_page': 100, 'page': page})
        values = api.request('GET', f'/repos/{repository}/actions/workflows/build.yml/runs?{params}')
        # GitHub limits filtered run searches to 1,000 results. Refuse a partial
        # history instead of overlooking a recently rerun older run ID.
        if values.get('total_count', 0) > 1000:
            raise ValueError('Too many matching builds to establish the latest run safely')
        batch = values['workflow_runs']
        runs.extend(run for run in batch
                    if run['head_sha'] == pr['head']['sha'] and run['head_branch'] == pr['head']['ref']
                    and (run.get('head_repository') or {}).get('full_name') == repository
                    and run['event'] in ('push', 'workflow_dispatch'))
        if len(batch) < 100 or page * 100 >= values.get('total_count', 1001):
            break
    else:
        raise ValueError('Build history pagination limit exceeded')
    # A rerun keeps its run ID; start time distinguishes a newly requested rerun
    # from a newer-ID run that actually started earlier. Completion time is not
    # used, because an old slow build must not win by finishing last.
    return max(runs, key=lambda run: (run.get('run_started_at') or run.get('created_at') or '',
                                      run['id'], run['run_attempt'])) if runs else None


def run_stamp(run: dict | None):
    return None if run is None else (run['id'], run['run_attempt'], run['status'], run.get('conclusion'))


def owned_comment(comments: list[dict]) -> dict | None:
    return next((comment for comment in comments if comment['user']['login'] == BOT
                 and comment['user'].get('type') == 'Bot' and comment.get('body', '').startswith(MARKER)), None)


def previous_payload(body: str) -> str | None:
    if body.count(START) != 1 or body.count(END) != 1:
        return None
    start, end = body.index(START) + len(START), body.index(END)
    return body[start:end].strip() if end > start else None


def comment_body(pr: dict, run: dict | None, payload: str | None, previous: str = '', error: str | None = None) -> str:
    head = full_sha(pr['head']['sha'])
    header = f'{MARKER}\n## Runtime publication status\n\n**Current PR head:** `{head}`\n\n'
    if run is None:
        status = 'No matching runtime build is recorded for this head. An upstream update may still need overlay repair or explicit build dispatch.'
    elif run['status'] != 'completed':
        status = f'The latest runtime build/report run is **{run["status"]}**. No new complete consumer report is available yet.'
    elif run['conclusion'] == 'success' and payload is not None:
        status = 'The runtime build and consumer report succeeded for this PR head.'
    elif run['conclusion'] == 'success':
        status = 'The runtime build succeeded, but its consumer report is unavailable or expired.'
    else:
        status = (f'The latest runtime build/report run concluded **{run["conclusion"]}**. '
                  'Publication may have completed before a reporting failure; this is not a new complete consumer report.')
    header += status + '\n'
    if run:
        header += f'\n[Build/report run]({run["html_url"]}) (attempt {run["run_attempt"]})\n'
    if error:
        header += '\nReport retrieval error: ' + error.replace('`', "'").replace('\n', ' ')[:500] + '\n'
    if payload is not None:
        header += '\n' + START + '\n' + payload.strip() + '\n' + END + '\n'
    else:
        old = previous_payload(previous)
        if old:
            header += ('\n<details>\n<summary>Previous successful publication — not confirmation of the current head/run</summary>\n\n'
                       + START + '\n' + old + '\n' + END + '\n\n</details>\n')
    if len(header.encode('utf-8')) > 62000:
        raise ValueError('PR comment size limit exceeded')
    return header


def reconcile(api: GitHub, repository: str, downloader=download_report) -> int:
    repository_name(repository)
    changed = 0
    # The workflow serializes reconciliation repository-wide. Every invocation
    # scans open same-repository PRs, so coalesced pending events cannot strand a
    # different PR. PRs opened after a successful push build are covered as well.
    for pr in api.pages(f'/repos/{repository}/pulls', state='open'):
        if not same_repo_pr(pr, repository):
            continue
        run = latest_run(api, repository, pr)
        comments = list(api.pages(f'/repos/{repository}/issues/{pr["number"]}/comments'))
        previous = owned_comment(comments)
        payload = None
        error = None
        if run and run['status'] == 'completed' and run['conclusion'] == 'success':
            try:
                name = f'consumer-update-{run["run_attempt"]}'
                artifacts = api.request('GET', f'/repos/{repository}/actions/runs/{run["id"]}/artifacts?'
                                        + urlencode({'name': name, 'per_page': 100}))['artifacts']
                matches = [item for item in artifacts if item['name'] == name and not item['expired']]
                if len(matches) != 1:
                    raise ValueError('No unique unexpired report artifact for this attempt')
                payload = decode_report(downloader(api, repository, matches[0]['id']), repository, run)
            except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as failure:
                error = str(failure)
        # Event payloads and run completion order are not authoritative. Refresh
        # both PR head and newest run before writing, including rerun attempts.
        current = api.request('GET', f'/repos/{repository}/pulls/{pr["number"]}')
        if not same_repo_pr(current, repository) or current['head']['sha'] != pr['head']['sha']:
            continue
        if run_stamp(latest_run(api, repository, current)) != run_stamp(run):
            continue
        body = comment_body(current, run, payload, previous['body'] if previous else '', error)
        if previous and previous['body'] == body:
            continue
        if previous:
            api.request('PATCH', f'/repos/{repository}/issues/comments/{previous["id"]}', {'body': body})
        else:
            api.request('POST', f'/repos/{repository}/issues/{pr["number"]}/comments', {'body': body})
        changed += 1
    return changed


def main() -> None:
    changed = reconcile(GitHub(), repository_name(os.environ['GITHUB_REPOSITORY']))
    print(f'Reconciled runtime publication comments: {changed} changed.')


if __name__ == '__main__':
    main()
