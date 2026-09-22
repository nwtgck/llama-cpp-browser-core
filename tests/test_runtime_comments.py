"""Exercise stale-event, rerun, late-PR and untrusted-report handling offline."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import runtime_comments as comments

A, B, C = 'a' * 40, 'b' * 40, 'c' * 40
REPO = 'example/core'


def pr(head=A, number=7, repository=REPO):
    return {'number': number, 'state': 'open', 'head': {'sha': head, 'ref': 'feature/update', 'repo': {'full_name': repository}}}


def run(head=A, id=123, attempt=1, status='completed', conclusion='success', repository=REPO):
    return {'id': id, 'run_attempt': attempt, 'head_sha': head, 'head_branch': 'feature/update',
            'head_repository': {'full_name': repository}, 'event': 'push', 'status': status, 'conclusion': conclusion,
            'html_url': f'https://github.com/{REPO}/actions/runs/{id}'}


def payload(head=A):
    return f'## Runtime artifact published\n\nSource: {head}\n\nnpm install github:{REPO}#{C}\n\n<details>\n<summary>Metadata</summary>\n\n```yaml\nfixture: true\n```\n\n</details>\n'


def archive_for(workflow_run, changes=None, extras=None, body=None):
    body = body if body is not None else payload(workflow_run['head_sha'])
    encoded = body.encode('utf-8')
    envelope = {'schemaVersion': 1, 'repository': REPO, 'sourceCommit': workflow_run['head_sha'],
                'artifactCommit': C, 'runId': workflow_run['id'], 'runAttempt': workflow_run['run_attempt'],
                'markdownSha256': hashlib.sha256(encoded).hexdigest()}
    envelope.update(changes or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.json', json.dumps(envelope))
        archive.writestr('consumer-update.md', encoded)
        archive.writestr('consumer-update.yaml', 'fixture: true\n')
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def bot_comment(body, id=42):
    return {'id': id, 'user': {'login': 'github-actions[bot]', 'type': 'Bot'}, 'body': body}


class FakeApi:
    def __init__(self, pull_requests=None, runs=None, existing=None):
        self.pull_requests = pull_requests or [pr()]
        self.runs = runs or []
        self.comments = existing or []
        self.writes = []
        self.calls = []
        self.fresh_head = None
        self.fresh_run = None
        self.run_reads = 0
        self.expired = False
        self.missing_report = False

    def pages(self, path, **params):
        self.calls.append(('GET pages', path))
        if path.endswith('/pulls'):
            return iter(copy.deepcopy(self.pull_requests))
        if path.endswith('/comments'):
            return iter(copy.deepcopy(self.comments))
        raise AssertionError(path)

    def request(self, method, path, data=None):
        self.calls.append((method, path))
        if method in ('PATCH', 'POST'):
            self.writes.append((method, path, data))
            return {'id': 42}
        if '/actions/workflows/build.yml/runs?' in path:
            self.run_reads += 1
            if self.run_reads > 1 and self.fresh_run:
                return {'workflow_runs': self.fresh_run}
            return {'workflow_runs': copy.deepcopy(self.runs)}
        if '/actions/runs/' in path and '/artifacts?' in path:
            name = parse_qs(urlparse(path).query)['name'][0]
            return {'artifacts': [] if self.missing_report else [
                {'id': 91, 'name': name, 'expired': self.expired},
                {'id': 90, 'name': 'consumer-update-0', 'expired': False},
            ]}
        if '/pulls/' in path:
            number = int(path.rsplit('/', 1)[1])
            value = copy.deepcopy(next(p for p in self.pull_requests if p['number'] == number))
            if self.fresh_head:
                value['head']['sha'] = self.fresh_head
            return value
        raise AssertionError((method, path))


class ReportDecoder(unittest.TestCase):
    def test_valid_bounded_archive(self):
        value = run()
        self.assertEqual(comments.decode_report(archive_for(value), REPO, value), payload())

    def test_identity_mismatch_is_rejected(self):
        for change in [{'repository': 'fork/core'}, {'sourceCommit': B}, {'runId': 124},
                       {'runAttempt': 2}, {'schemaVersion': 2}, {'artifactCommit': 'moving-branch'},
                       {'markdownSha256': '0' * 64}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                comments.decode_report(archive_for(run(), change), REPO, run())

    def test_path_traversal_and_extra_files_are_rejected_without_extraction(self):
        for name in ['../evil.py', '/tmp/evil', 'workflow.sh']:
            with self.assertRaises(ValueError):
                comments.decode_report(archive_for(run(), extras={name: 'not executed'}), REPO, run())

    def test_duplicate_files_are_rejected(self):
        content = io.BytesIO(archive_for(run()))
        with zipfile.ZipFile(content, 'a') as archive:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                archive.writestr('consumer-update.md', 'duplicate')
        with self.assertRaises(ValueError):
            comments.decode_report(content.getvalue(), REPO, run())

    def test_zip_bomb_and_oversized_body_are_rejected(self):
        with self.assertRaises(ValueError):
            comments.decode_report(archive_for(run(), body='x' * 1000000), REPO, run())
        with self.assertRaises(ValueError):
            comments.decode_report(b'0' * (comments.MAX_ARCHIVE + 1), REPO, run())

    def test_symlink_and_delimiter_injection_are_rejected(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            entry = zipfile.ZipInfo('consumer-update.md')
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, 'report.json')
            archive.writestr('report.json', '{}')
            archive.writestr('consumer-update.yaml', 'test: true')
        with self.assertRaises(ValueError):
            comments.decode_report(content.getvalue(), REPO, run())
        with self.assertRaises(ValueError):
            comments.decode_report(archive_for(run(), body=payload() + comments.START), REPO, run())


class CommentReconciliation(unittest.TestCase):
    def reconcile(self, api, workflow=None):
        workflow = workflow or (api.runs[0] if api.runs else run())
        return comments.reconcile(api, REPO, lambda *_: archive_for(workflow))

    def test_pr_opened_after_build_gets_its_existing_result(self):
        api = FakeApi(runs=[run()])
        self.assertEqual(self.reconcile(api), 1)
        body = api.writes[0][2]['body']
        self.assertIn('succeeded for this PR head', body)
        self.assertIn('npm install', body)
        self.assertIn('<details>', body)

    def test_existing_owned_comment_is_edited_not_appended(self):
        pending = comments.comment_body(pr(), run(status='in_progress'), None)
        api = FakeApi(runs=[run()], existing=[bot_comment(pending)])
        self.reconcile(api)
        self.assertEqual(api.writes[0][0], 'PATCH')
        self.assertEqual(api.writes[0][1], '/repos/example/core/issues/comments/42')

    def test_identical_report_causes_no_write(self):
        body = comments.comment_body(pr(), run(), payload())
        api = FakeApi(runs=[run()], existing=[bot_comment(body)])
        self.assertEqual(self.reconcile(api), 0)

    def test_user_marker_spoof_does_not_allow_editing_their_comment(self):
        other = bot_comment(comments.MARKER + '\nUser text')
        other['user'] = {'login': 'someone', 'type': 'User'}
        api = FakeApi(runs=[run()], existing=[other])
        self.reconcile(api)
        self.assertEqual(api.writes[0][0], 'POST')

    def test_new_head_marks_previous_success_as_previous_not_current(self):
        old = comments.comment_body(pr(A), run(A), payload(A))
        api = FakeApi(pull_requests=[pr(B)], runs=[run(B, id=124, status='in_progress', conclusion=None)], existing=[bot_comment(old)])
        self.reconcile(api)
        body = api.writes[0][2]['body']
        self.assertIn('Current PR head:** `' + B, body)
        self.assertIn('not confirmation of the current head/run', body)
        self.assertIn(A, body)
        self.assertEqual(body.count(comments.START), 1)
        self.assertNotIn('succeeded for this PR head', body)

    def test_out_of_order_old_completion_does_not_replace_new_running_head(self):
        api = FakeApi(pull_requests=[pr(B)], runs=[run(A), run(B, id=124, status='queued', conclusion=None)])
        self.reconcile(api)
        body = api.writes[0][2]['body']
        self.assertIn('**queued**', body)
        self.assertNotIn('npm install', body)

    def test_rerun_attempt_has_its_own_report_identity(self):
        api = FakeApi(runs=[run(attempt=2)])
        self.reconcile(api)
        self.assertTrue(any('name=consumer-update-2' in path for _, path in api.calls))
        self.assertIn('(attempt 2)', api.writes[0][2]['body'])

    def test_older_attempt_archive_is_not_accepted_for_new_rerun(self):
        api = FakeApi(runs=[run(attempt=2)])
        self.reconcile(api, workflow=run(attempt=1))
        self.assertIn('unavailable', api.writes[0][2]['body'])
        self.assertNotIn('npm install', api.writes[0][2]['body'])

    def test_latest_run_search_includes_later_pages(self):
        class PagedApi:
            def request(self, method, path):
                page = int(parse_qs(urlparse(path).query)['page'][0])
                entries = [{**run(id=200 + i), 'run_started_at': '2026-09-22T10:00:00Z'} for i in range(100)]
                if page == 2:
                    entries = [{**run(id=100, attempt=3), 'run_started_at': '2026-09-22T12:00:00Z'}]
                return {'workflow_runs': entries, 'total_count': 101}
        self.assertEqual(comments.latest_run(PagedApi(), REPO, pr())['id'], 100)

    def test_incomplete_filtered_build_history_is_rejected(self):
        class OverflowApi:
            def request(self, method, path):
                return {'workflow_runs': [], 'total_count': 1001}
        with self.assertRaisesRegex(ValueError, 'Too many matching'):
            comments.latest_run(OverflowApi(), REPO, pr())

    def test_rerun_of_an_older_run_id_can_be_the_newest_attempt(self):
        old_rerun = {**run(id=120, attempt=2), 'run_started_at': '2026-09-22T12:00:00Z'}
        later_id = {**run(id=123), 'run_started_at': '2026-09-22T10:00:00Z'}
        api = FakeApi(runs=[later_id, old_rerun])
        self.reconcile(api, workflow=old_rerun)
        self.assertIn('(attempt 2)', api.writes[0][2]['body'])
        self.assertIn('/actions/runs/120', api.writes[0][2]['body'])

    def test_fork_and_non_source_event_runs_are_ignored(self):
        for wrong in [run(repository='attacker/core'), {**run(), 'event': 'pull_request'}]:
            api = FakeApi(runs=[wrong])
            self.reconcile(api)
            self.assertIn('No matching runtime build', api.writes[0][2]['body'])

    def test_fork_pr_is_not_processed(self):
        api = FakeApi(pull_requests=[pr(repository='fork/core')], runs=[run()])
        self.assertEqual(self.reconcile(api), 0)
        self.assertEqual(len(api.calls), 1)

    def test_head_race_skips_a_stale_write(self):
        api = FakeApi(runs=[run()]); api.fresh_head = B
        self.assertEqual(self.reconcile(api), 0)

    def test_run_race_skips_a_stale_write(self):
        api = FakeApi(runs=[run()]); api.fresh_run = [run(id=124, status='queued', conclusion=None)]
        self.assertEqual(self.reconcile(api), 0)

    def test_expired_or_missing_metadata_is_explicit(self):
        for key in ['expired', 'missing_report']:
            api = FakeApi(runs=[run()]); setattr(api, key, True)
            self.reconcile(api)
            self.assertIn('unavailable or expired', api.writes[0][2]['body'])

    def test_failure_preserves_previous_payload_without_claiming_new_success(self):
        old = comments.comment_body(pr(), run(), payload())
        api = FakeApi(runs=[run(id=124, conclusion='failure')], existing=[bot_comment(old)])
        self.reconcile(api)
        body = api.writes[0][2]['body']
        self.assertIn('**failure**', body)
        self.assertIn('not confirmation of the current head/run', body)
        self.assertIn('npm install', body)

    def test_repeated_pending_states_do_not_nest_previous_sections(self):
        successful = comments.comment_body(pr(), run(), payload())
        pending = comments.comment_body(pr(B), run(B, status='queued'), None, successful)
        again = comments.comment_body(pr(B), run(B, status='in_progress'), None, pending)
        self.assertEqual(again.count('Previous successful publication'), 1)
        self.assertEqual(again.count(comments.START), 1)

    def test_reconciliation_handles_more_than_the_triggering_pr(self):
        api = FakeApi(pull_requests=[pr(number=7), pr(number=8)], runs=[run()])
        self.assertEqual(self.reconcile(api), 2)
        self.assertEqual(len(api.writes), 2)


if __name__ == '__main__':
    unittest.main()
