#!/usr/bin/env python3
"""Resolve an upstream revision, propose its two pins, and dispatch the existing build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

from github_api import ApiError, GitHub, full_sha, git, git_auth_env, repository_name
from prepare_mtmd import prepare

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'ggml-org/llama.cpp'
PREFIX = '/repos/' + UPSTREAM
STABLE = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
NIGHTLY = re.compile(r'b[0-9]+\Z')
PINS = {'vendor/llama.cpp', 'config/toolchain.json'}


def checked_ref(value: str) -> str:
    # Deliberately exclude revision expressions, options, URLs and abbreviated
    # hashes. Named refs and full commits have unambiguous, auditable identities.
    if not value or value.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
        raise ValueError('Custom ref must be a tag, branch, or full commit SHA')
    subprocess.run(['git', 'check-ref-format', 'refs/tags/' + value], check=True, capture_output=True)
    return value


def peel(api: GitHub, obj: dict) -> str:
    for _ in range(10):
        if obj['type'] == 'commit':
            return full_sha(obj['sha'])
        if obj['type'] != 'tag':
            break
        obj = api.request('GET', PREFIX + '/git/tags/' + full_sha(obj['sha']))['object']
    raise ValueError('Upstream ref does not resolve to a commit')


def resolve_named_ref(api: GitHub, ref: str) -> str:
    checked_ref(ref)
    if re.fullmatch(r'[0-9a-f]{40}', ref):
        result = api.request('GET', PREFIX + '/commits/' + ref)
        if result['sha'] != ref:
            raise ValueError('Commit resolution mismatch')
        return ref
    candidates = [ref[5:]] if ref.startswith(('refs/tags/', 'refs/heads/')) else ['tags/' + ref, 'heads/' + ref]
    objects = []
    for candidate in candidates:
        try:
            objects.append(api.request('GET', PREFIX + '/git/ref/' + quote(candidate, safe='/'))['object'])
        except ApiError as error:
            if error.status != 404:
                raise
    if len(objects) != 1:
        raise ValueError('Ref is missing or ambiguous; use refs/tags/... or refs/heads/...')
    return peel(api, objects[0])


def resolve_target(api: GitHub, target: str, custom_ref: str = '') -> dict:
    release = None
    if target == 'custom':
        ref = checked_ref(custom_ref)
    else:
        if custom_ref:
            raise ValueError('custom_ref is only valid with target=custom')
        if target == 'latest':
            release = api.request('GET', PREFIX + '/releases/latest')
            # A tag pattern alone cannot establish release status. Conversely,
            # older bNNNN releases must never silently become the stable channel.
            if release['draft'] or release['prerelease'] or not STABLE.fullmatch(release['tag_name']):
                raise ValueError('Latest release is not a published stable vX.Y.Z release; no fallback')
        elif target == 'latest-unstable':
            # First published bNNNN pre-release in GitHub's release-list order.
            # This is the released nightly channel, never an unbuilt master tip.
            release = next((item for item in api.pages(PREFIX + '/releases')
                            if not item['draft'] and item['prerelease']
                            and item.get('published_at') and NIGHTLY.fullmatch(item['tag_name'])), None)
            if release is None:
                raise ValueError('No published bNNNN pre-release was found; no fallback')
        else:
            raise ValueError('Unknown update target')
        ref = 'refs/tags/' + release['tag_name']
    result = {'requested': target, 'ref': ref, 'commit': resolve_named_ref(api, ref)}
    if release:
        result['release'] = {key: release[key] for key in ('tag_name', 'published_at', 'html_url', 'prerelease')}
    return result


def read_pins(root: Path) -> tuple[str, dict]:
    toolchain = json.loads((root / 'config/toolchain.json').read_text())
    commit = full_sha(toolchain['llamaCommit'])
    entry = git('ls-tree', 'HEAD', '--', 'vendor/llama.cpp', cwd=root).stdout.split()
    if len(entry) != 4 or entry[:3] != ['160000', 'commit', commit]:
        raise ValueError('Submodule gitlink and toolchain.json disagree')
    return commit, toolchain


def change_pins(root: Path, commit: str) -> None:
    _, toolchain = read_pins(root)
    src = root / 'vendor/llama.cpp'
    git('fetch', '--no-tags', '--depth=1', 'https://github.com/' + UPSTREAM + '.git', full_sha(commit), cwd=src)
    git('checkout', '--detach', commit, cwd=src)
    git('submodule', 'update', '--init', '--recursive', cwd=src)
    toolchain['llamaCommit'] = commit
    (root / 'config/toolchain.json').write_text(json.dumps(toolchain, indent=2) + '\n')
    git('add', '--', *sorted(PINS), cwd=root)
    changed = set(git('diff', '--cached', '--name-only', cwd=root).stdout.splitlines())
    if changed != PINS:
        raise ValueError('Updater may change only the submodule gitlink and llamaCommit')


def overlay_preflight(root: Path) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix='lcb-update-overlay-') as tmp:
            prepare(root / 'vendor/llama.cpp', Path(tmp) / 'overlay', root / 'patches/mtmd-webgpu-bf16.patch', capture_output=True)
        return {'status': 'passed', 'scope': 'patch application only; not compilation or inference'}
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # A failed overlay still produces a draft PR, preserving the exact
        # update candidate for human repair. No automatic patch fuzz or deletion.
        details = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        return {'status': 'failed', 'scope': 'patch application only', 'error': details.strip()[:4000]}


def candidate_branch(base: str, source: str, target: str) -> str:
    # Base identity avoids collisions across source branches; including the base
    # commit avoids ever rebasing or force-pushing over manual repair commits.
    identity = hashlib.sha256(base.encode()).hexdigest()[:12]
    return f'automation/llama-cpp/{identity}-{full_sha(source)[:12]}-{full_sha(target)[:12]}'


def find_pr(api: GitHub, repository: str, branch: str, base: str) -> dict | None:
    owner = repository.split('/')[0]
    matches = [pr for pr in api.pages('/repos/' + repository + '/pulls', state='all',
                                      head=owner + ':' + branch, base=base)
               if (pr['head'].get('repo') or {}).get('full_name') == repository and pr['head']['ref'] == branch]
    if any(pr['state'] != 'open' for pr in matches):
        raise ValueError('This exact update already has a closed PR; no automatic reopening or overwrite')
    if len(matches) > 1:
        raise ValueError('Multiple update PRs found')
    return matches[0] if matches else None


def propose(root: Path, api: GitHub, repository: str, base: str, target: dict,
            allow_non_fast_forward: bool = False, *, progress: dict | None = None) -> dict:
    repository_name(repository)
    checked_ref(base)
    if base == 'artifacts' or base.startswith(('artifacts/', 'automation/llama-cpp/')):
        raise ValueError('Choose a source base branch, not artifacts or an updater branch')
    if git('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none', cwd=root).stdout:
        raise ValueError('Updater requires a clean source checkout')
    source = full_sha(git('rev-parse', 'HEAD', cwd=root).stdout.strip())
    previous, _ = read_pins(root)
    result = progress if progress is not None else {}
    result.update({'base': base, 'baseCommit': source, 'previousCommit': previous, 'target': target})
    if previous == target['commit']:
        return {**result, 'status': 'unchanged'}
    comparison = api.request('GET', PREFIX + '/compare/' + previous + '...' + target['commit'])
    if comparison['status'] not in ('ahead', 'identical') and not allow_non_fast_forward:
        raise ValueError('Target is older or divergent; allow_non_fast_forward is required for this change')
    result['upstreamRelation'] = comparison['status']
    branch = candidate_branch(base, source, target['commit'])
    result.update({'branch': branch, 'branchUrl': f'https://github.com/{repository}/tree/{branch}'})
    pr = find_pr(api, repository, branch, base)
    remote = 'https://github.com/' + repository + '.git'
    existing = git('ls-remote', '--exit-code', '--heads', remote, 'refs/heads/' + branch, cwd=root, check=False, env=git_auth_env(os.environ['GH_TOKEN']))
    if existing.returncode not in (0, 2):
        raise RuntimeError('Cannot inspect the remote update branch')
    if existing.stdout:
        head = full_sha(existing.stdout.split()[0])
        git('fetch', '--no-tags', '--depth=1', remote, 'refs/heads/' + branch, cwd=root,
            env=git_auth_env(os.environ['GH_TOKEN']))
        # Only validate its pins. Any human fixes on a previous attempt survive.
        pins = json.loads(git('show', head + ':config/toolchain.json', cwd=root).stdout)
        link = git('ls-tree', head, '--', 'vendor/llama.cpp', cwd=root).stdout.split()
        if pins.get('llamaCommit') != target['commit'] or len(link) != 4 or link[:3] != ['160000', 'commit', target['commit']]:
            raise ValueError('Existing updater branch has different pins; refusing to overwrite it')
        preflight = {'status': 'not-repeated', 'scope': 'existing branch preserved; full build is authoritative'}
    else:
        change_pins(root, target['commit'])
        preflight = overlay_preflight(root)
        result['preflight'] = preflight
        git('-c', 'user.name=github-actions[bot]', '-c',
            'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit', '-m', 'chore: update llama.cpp to ' + target['ref'].removeprefix('refs/tags/'), cwd=root)
        head = full_sha(git('rev-parse', 'HEAD', cwd=root).stdout.strip())
        result['sourceCommit'] = head
        # No force-push, including races between simultaneous dispatches.
        git('push', remote, head + ':refs/heads/' + branch, cwd=root,
            env=git_auth_env(os.environ['GH_TOKEN']))
    result.update({'branch': branch, 'sourceCommit': head, 'preflight': preflight})
    if pr is None:
        body = ('## Upstream update\n\n'
                f'- Requested channel: `{target["requested"]}`\n'
                f'- Resolved ref: `{target["ref"]}`\n'
                f'- Previous llama.cpp: `{previous}`\n'
                f'- Proposed llama.cpp: `{target["commit"]}`\n'
                f'- Upstream relation: `{comparison["status"]}`\n'
                f'- Overlay preflight: **{preflight["status"]}** ({preflight["scope"]})\n\n'
                f'[Upstream comparison](https://github.com/{UPSTREAM}/compare/{previous}...{target["commit"]})\n\n'
                'Only the submodule gitlink and `config/toolchain.json` are changed. '
                'A failed preflight leaves a draft PR for manual overlay repair. '
                'A successful build publishes an immutable runtime artifact before merge. '
                'The runtime comment contains installation and consumer metadata.\n')
        if preflight['status'] == 'failed':
            body += '\nPreflight error: ' + preflight['error'].replace('`', "'")[:2000] + '\n'
        pr = api.request('POST', '/repos/' + repository + '/pulls', {
            'title': 'chore: update llama.cpp to ' + target['ref'].removeprefix('refs/tags/'),
            'head': branch, 'base': base, 'body': body, 'draft': preflight['status'] == 'failed',
        })
    result.update({'pullRequest': pr['number'], 'pullRequestUrl': pr['html_url']})
    if preflight['status'] == 'failed':
        return {**result, 'status': 'draft-needs-overlay-repair'}
    # GITHUB_TOKEN pushes do not trigger push workflows. Dispatch is explicit and
    # the build checks its event SHA against this exact expected source commit.
    api.request('POST', '/repos/' + repository + '/actions/workflows/build.yml/dispatches', {
        'ref': branch, 'inputs': {'expected_source': head},
    })
    return {**result, 'status': 'build-dispatched'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['latest', 'latest-unstable', 'custom'], default='latest')
    parser.add_argument('--custom-ref', default='')
    parser.add_argument('--base', required=True)
    parser.add_argument('--allow-non-fast-forward', action='store_true')
    args = parser.parse_args()
    api = GitHub()
    result = {'requested': args.target, 'customRef': args.custom_ref, 'base': args.base}
    failed = False
    try:
        target = resolve_target(api, args.target, args.custom_ref)
        result = propose(ROOT, api, os.environ['GITHUB_REPOSITORY'], args.base, target,
                         args.allow_non_fast_forward, progress=result)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        # A pushed branch / created PR can survive a later permissions or dispatch
        # failure. Its identity remains visible and a rerun reuses it safely.
        details = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        result.update({'status': 'failed', 'error': details.strip()[:4000]})
        failed = True
    # Report output stays outside the source index and is never a build input.
    folder = ROOT / 'build/upstream-update'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    text = '## llama.cpp update\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n'
    if result.get('pullRequestUrl'):
        text += '\n[Update pull request](' + result['pullRequestUrl'] + ')\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(text)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
