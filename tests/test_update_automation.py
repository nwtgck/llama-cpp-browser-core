"""Offline release selection and real local-Git update/PR orchestration tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from github_api import ApiError, full_sha, repository_name
import update_llama_cpp as update

A, B = 'a' * 40, 'b' * 40


class ReleaseApi:
    def __init__(self, releases=None, latest=None, refs=None, tags=None):
        self.releases = releases or []
        self.latest = latest
        self.refs = refs or {}
        self.tags = tags or {}
        self.calls = []

    def pages(self, path, **params):
        self.calls.append(path)
        yield from self.releases

    def request(self, method, path, data=None):
        self.calls.append(path)
        suffix = path.removeprefix(update.PREFIX)
        if suffix == '/releases/latest':
            if self.latest is None:
                raise ApiError(404, 'No stable release')
            return self.latest
        if suffix.startswith('/git/ref/'):
            name = suffix.removeprefix('/git/ref/')
            if name not in self.refs:
                raise ApiError(404, 'Missing ref')
            return {'object': self.refs[name]}
        if suffix.startswith('/git/tags/'):
            return {'object': self.tags[suffix.rsplit('/', 1)[1]]}
        if suffix.startswith('/commits/'):
            return {'sha': suffix.rsplit('/', 1)[1]}
        raise AssertionError(path)


def release(name, prerelease=False, draft=False):
    return {'tag_name': name, 'draft': draft, 'prerelease': prerelease,
            'published_at': '2026-09-22T00:00:00Z', 'html_url': 'https://github.com/ggml-org/llama.cpp/releases/tag/' + name}


class ReleaseSelection(unittest.TestCase):
    def test_stable_uses_release_status_and_exact_tag(self):
        api = ReleaseApi(latest=release('v1.2.3'), refs={'tags/v1.2.3': {'type': 'commit', 'sha': A}})
        result = update.resolve_target(api, 'latest')
        self.assertEqual(result['commit'], A)
        self.assertEqual(result['ref'], 'refs/tags/v1.2.3')
        self.assertFalse(result['release']['prerelease'])

    def test_stable_never_falls_back_to_nightly_or_master(self):
        for value in [release('b999'), release('v1.2.3', True), release('v1.2.3', draft=True),
                      release('v1.2.3-rc1'), release('v01.2.3'), release('v1.2.3extra')]:
            with self.subTest(value=value):
                api = ReleaseApi(latest=value)
                with self.assertRaises(ValueError):
                    update.resolve_target(api, 'latest')
                self.assertEqual(api.calls, [update.PREFIX + '/releases/latest'])
        with self.assertRaises(ApiError):
            update.resolve_target(ReleaseApi(), 'latest')

    def test_unstable_uses_published_nightly_not_draft_or_other_tags(self):
        values = [release('b100', True, True), release('v2.0.0-rc1', True), release('v1.2.3'),
                  release('b99', True), release('b98', True)]
        api = ReleaseApi(releases=values, refs={'tags/b99': {'type': 'commit', 'sha': B}})
        result = update.resolve_target(api, 'latest-unstable')
        self.assertEqual(result['ref'], 'refs/tags/b99')
        self.assertNotIn(update.PREFIX + '/git/ref/heads/master', api.calls)

    def test_no_nightly_is_an_error(self):
        with self.assertRaises(ValueError):
            update.resolve_target(ReleaseApi(releases=[release('v1.0.0')]), 'latest-unstable')

    def test_annotated_tags_are_peeled(self):
        api = ReleaseApi(refs={'tags/v1.0.0': {'type': 'tag', 'sha': B}},
                         tags={B: {'type': 'commit', 'sha': A}})
        self.assertEqual(update.resolve_named_ref(api, 'refs/tags/v1.0.0'), A)

    def test_tag_loop_or_blob_is_rejected(self):
        for obj in ({'type': 'blob', 'sha': B}, {'type': 'tag', 'sha': B}):
            api = ReleaseApi(refs={'tags/loop': obj}, tags={B: obj})
            with self.assertRaises(ValueError):
                update.resolve_named_ref(api, 'refs/tags/loop')

    def test_custom_full_commit_and_explicit_master(self):
        self.assertEqual(update.resolve_target(ReleaseApi(), 'custom', A)['commit'], A)
        api = ReleaseApi(refs={'heads/master': {'type': 'commit', 'sha': B}})
        self.assertEqual(update.resolve_target(api, 'custom', 'master')['commit'], B)

    def test_ambiguous_ref_requires_qualification(self):
        api = ReleaseApi(refs={key: {'type': 'commit', 'sha': A} for key in ['heads/shared', 'tags/shared']})
        with self.assertRaises(ValueError):
            update.resolve_named_ref(api, 'shared')
        self.assertEqual(update.resolve_named_ref(api, 'refs/heads/shared'), A)

    def test_input_injection_and_revision_expressions_are_rejected(self):
        for value in ['', '-x', 'HEAD~1', 'x^{}', 'x\ny', 'https://evil/x', 'x;id', 'x..y', 'x@{1}', 'a.lock']:
            with self.subTest(value=value), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                update.checked_ref(value)
        with self.assertRaises(ValueError):
            update.resolve_target(ReleaseApi(), 'latest', 'master')

    def test_api_errors_are_not_treated_as_missing_tags(self):
        api = ReleaseApi()
        api.request = lambda *args: (_ for _ in ()).throw(ApiError(403, 'Rate limited'))
        with self.assertRaises(ApiError):
            update.resolve_named_ref(api, 'master')

    def test_identifiers_and_branch_deduplication(self):
        with self.assertRaises(ValueError):
            full_sha('abc')
        with self.assertRaises(ValueError):
            repository_name('owner/repo?token=secret')
        self.assertEqual(update.candidate_branch('develop', A, B), update.candidate_branch('develop', A, B))
        self.assertNotEqual(update.candidate_branch('develop', A, B), update.candidate_branch('main', A, B))
        self.assertNotEqual(update.candidate_branch('develop', A, B), update.candidate_branch('develop', B, B))


class ProposalApi:
    def __init__(self):
        self.prs = []
        self.dispatches = []
        self.relation = 'ahead'
        self.fail_create = False

    def pages(self, path, **params):
        return iter(self.prs)

    def request(self, method, path, data=None):
        if '/compare/' in path:
            return {'status': self.relation}
        if method == 'POST' and path.endswith('/pulls'):
            if self.fail_create:
                raise ApiError(403, 'Actions cannot create PRs')
            pr = {**data, 'number': 7, 'html_url': 'https://github.com/example/core/pull/7', 'state': 'open',
                  'head': {'ref': data['head'], 'repo': {'full_name': 'example/core'}}}
            self.prs.append(pr)
            return pr
        if path.endswith('/dispatches'):
            self.dispatches.append(data)
            return None
        raise AssertionError((method, path))


class LocalGitProposal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root, self.upstream, self.remote = [self.home / name for name in ('source', 'upstream', 'remote.git')]
        for directory in (self.root, self.upstream):
            directory.mkdir()
            self.git('init', '-q', cwd=directory)
            self.git('config', 'user.name', 'Fixture', cwd=directory)
            self.git('config', 'user.email', 'fixture@example.invalid', cwd=directory)
        clip = self.upstream / 'tools/mtmd/clip.cpp'
        clip.parent.mkdir(parents=True)
        clip.write_text('before\noriginal\nafter\n')
        self.git('add', '.', cwd=self.upstream)
        self.git('commit', '-qm', 'Upstream A', cwd=self.upstream)
        self.old = self.git('rev-parse', 'HEAD', cwd=self.upstream)
        (self.upstream / 'new-feature').write_text('B\n')
        self.git('add', '.', cwd=self.upstream)
        self.git('commit', '-qm', 'Upstream B', cwd=self.upstream)
        self.new = self.git('rev-parse', 'HEAD', cwd=self.upstream)
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(self.upstream), 'vendor/llama.cpp', cwd=self.root)
        self.git('checkout', '--detach', self.old, cwd=self.root / 'vendor/llama.cpp')
        (self.root / 'config').mkdir()
        (self.root / 'config/toolchain.json').write_text(json.dumps({'llamaCommit': self.old, 'otherPin': 'unchanged'}, indent=2) + '\n')
        (self.root / 'patches').mkdir()
        (self.root / 'patches/mtmd-webgpu-bf16.patch').write_text('--- a/clip.cpp\n+++ b/clip.cpp\n@@ -1,3 +1,3 @@\n before\n-original\n+patched\n after\n')
        self.git('add', '.', cwd=self.root)
        self.git('commit', '-qm', 'Core base', cwd=self.root)
        self.base = self.git('rev-parse', 'HEAD', cwd=self.root)
        self.git('init', '--bare', '-q', str(self.remote), cwd=self.home)
        self.api = ProposalApi()
        self.target = {'commit': self.new, 'ref': 'refs/tags/v1.0.0', 'requested': 'latest'}
        original = update.git
        def local_git(*args, **kwargs):
            replacements = {'https://github.com/example/core.git': str(self.remote),
                            'https://github.com/ggml-org/llama.cpp.git': str(self.upstream)}
            return original(*(replacements.get(arg, arg) for arg in args), **kwargs)
        self.addCleanup(patch.stopall)
        patch.object(update, 'git', side_effect=local_git).start()
        patch.dict(os.environ, {'GH_TOKEN': 'fixture-token', 'GIT_ALLOW_PROTOCOL': 'file'}).start()

    @staticmethod
    def git(*args, cwd):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()

    def propose(self, **kwargs):
        return update.propose(self.root, self.api, 'example/core', 'develop', self.target, **kwargs)

    def restore_base(self):
        self.git('checkout', '--detach', self.base, cwd=self.root)
        self.git('submodule', 'update', '--init', cwd=self.root)

    def test_real_git_changes_exactly_two_pins_and_dispatches(self):
        result = self.propose()
        self.assertEqual(result['status'], 'build-dispatched')
        self.assertEqual(result['preflight']['status'], 'passed')
        self.assertEqual(set(self.git('diff', '--name-only', self.base, 'HEAD', cwd=self.root).splitlines()), update.PINS)
        self.assertEqual(json.loads((self.root / 'config/toolchain.json').read_text())['otherPin'], 'unchanged')
        self.assertEqual((self.root / 'vendor/llama.cpp/tools/mtmd/clip.cpp').read_text(), 'before\noriginal\nafter\n')
        self.assertEqual(self.api.dispatches[0]['inputs']['expected_source'], result['sourceCommit'])
        self.assertFalse(self.api.prs[0]['draft'])

    def test_no_op_creates_no_pr_or_dispatch(self):
        self.target['commit'] = self.old
        self.assertEqual(self.propose()['status'], 'unchanged')
        self.assertFalse(self.api.prs)
        self.assertFalse(self.api.dispatches)

    def test_dirty_checkout_is_rejected(self):
        (self.root / 'untracked').write_text('not an update input')
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.propose()

    def test_mismatched_committed_pins_are_rejected(self):
        (self.root / 'config/toolchain.json').write_text(json.dumps({'llamaCommit': self.new}))
        self.git('add', '.', cwd=self.root)
        self.git('commit', '-qm', 'Mismatched pins', cwd=self.root)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.propose()

    def test_rollback_requires_explicit_opt_in(self):
        self.api.relation = 'behind'
        with self.assertRaisesRegex(ValueError, 'allow_non_fast_forward'):
            self.propose()
        self.assertEqual(self.propose(allow_non_fast_forward=True)['status'], 'build-dispatched')

    def test_conflicting_overlay_is_a_draft_without_build(self):
        (self.root / 'patches/mtmd-webgpu-bf16.patch').write_text('not a patch\n')
        self.git('add', '.', cwd=self.root)
        self.git('commit', '-qm', 'Broken patch fixture', cwd=self.root)
        result = self.propose()
        self.assertEqual(result['status'], 'draft-needs-overlay-repair')
        self.assertTrue(self.api.prs[0]['draft'])
        self.assertFalse(self.api.dispatches)
        self.assertIn('patch', result['preflight']['error'])

    def test_rerun_preserves_manual_repair_commit(self):
        first = self.propose()
        (self.root / 'manual-repair').write_text('human changes\n')
        self.git('add', '.', cwd=self.root)
        self.git('commit', '-qm', 'Human repair', cwd=self.root)
        repair = self.git('rev-parse', 'HEAD', cwd=self.root)
        self.git('push', str(self.remote), 'HEAD:refs/heads/' + first['branch'], cwd=self.root)
        self.restore_base()
        second = self.propose()
        self.assertEqual(second['sourceCommit'], repair)
        self.assertEqual(len(self.api.prs), 1)
        self.assertEqual(len(self.api.dispatches), 2)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.root), self.base)

    def test_existing_branch_with_different_pins_is_not_overwritten(self):
        first = self.propose()
        config = self.root / 'config/toolchain.json'
        config.write_text(json.dumps({'llamaCommit': A}))
        self.git('add', '.', cwd=self.root)
        self.git('commit', '-qm', 'Different human target', cwd=self.root)
        self.git('push', str(self.remote), 'HEAD:refs/heads/' + first['branch'], cwd=self.root)
        self.restore_base()
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            self.propose()

    def test_closed_pr_is_not_reopened(self):
        self.propose()
        self.api.prs[0]['state'] = 'closed'
        self.restore_base()
        with self.assertRaisesRegex(ValueError, 'closed PR'):
            self.propose()

    def test_pr_creation_failure_leaves_recoverable_branch(self):
        self.api.fail_create = True
        with self.assertRaises(ApiError):
            self.propose()
        branch = update.candidate_branch('develop', self.base, self.new)
        self.assertTrue(self.git('ls-remote', str(self.remote), 'refs/heads/' + branch, cwd=self.home))
        self.assertFalse(self.api.dispatches)
        self.api.fail_create = False
        self.restore_base()
        self.assertEqual(self.propose()['status'], 'build-dispatched')


class FailureReporting(unittest.TestCase):
    def test_resolution_failure_still_produces_a_browser_visible_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / 'summary.md'
            with patch.object(update, 'ROOT', root), \
                 patch.object(update, 'resolve_target', side_effect=ApiError(404, 'No stable release')), \
                 patch.object(sys, 'argv', ['update_llama_cpp.py', '--base', 'develop']), \
                 patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}), patch('builtins.print'):
                with self.assertRaises(SystemExit) as failure:
                    update.main()
            self.assertEqual(failure.exception.code, 1)
            report = json.loads((root / 'build/upstream-update/report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('No stable release', summary.read_text())


if __name__ == '__main__':
    unittest.main()
