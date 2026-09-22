"""Audit the named API surface and exercise its transport without network access."""
import ast
import inspect
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import github_api as github

A, B = 'a' * 40, 'b' * 40
REPO = 'example/core'
PREFIX = '/repos/' + REPO
API_URL = 'https://api.github.com'
OPERATIONS = {
    'get_latest_release', 'iter_releases', 'get_commit', 'get_ref', 'get_tag',
    'compare_commits', 'iter_pull_requests', 'get_pull_request',
    'iter_build_runs', 'iter_run_artifacts', 'download_artifact_archive',
    'iter_pull_request_comments', 'create_pull_request_comment', 'update_pull_request_comment',
}


class Response(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def http_error(status, headers=None):
    return HTTPError(API_URL + PREFIX, status, 'fixture error', headers or {},
                     io.BytesIO(b'{"message":"fixture error"}'))


class OperationSurface(unittest.TestCase):
    def setUp(self):
        self.api = github.GitHub(token='fixture-token')

    def test_only_audited_named_operations_are_public(self):
        methods = {name for name, method in inspect.getmembers(github.GitHub, inspect.isfunction)
                   if not name.startswith('_')}
        self.assertEqual(methods, OPERATIONS)
        for name in methods:
            with self.subTest(operation=name):
                # Public callers cannot add arbitrary endpoint/query/payload fields.
                self.assertTrue(all(parameter.kind not in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL)
                                    for parameter in inspect.signature(getattr(self.api, name)).parameters.values()))
        self.assertFalse(hasattr(self.api, 'request'))
        self.assertFalse(hasattr(self.api, 'pages'))
        self.assertFalse(hasattr(self.api, 'token'))

    def test_updater_and_reporter_only_use_the_named_surface(self):
        for name in ('update_llama_cpp.py', 'runtime_comments.py'):
            with self.subTest(script=name):
                source = (ROOT / 'scripts' / name).read_text()
                tree = ast.parse(source)
                operations = [node.func.attr for node in ast.walk(tree)
                              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                              and isinstance(node.func.value, ast.Name) and node.func.value.id == 'api']
                self.assertTrue(operations)
                self.assertLessEqual(set(operations), OPERATIONS)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute):
                        self.assertNotIn(node.attr, ('request', 'pages', '_request', '_pages', 'token', '_token'))
                self.assertNotIn('urllib.request', source)
                self.assertNotIn('https://api.github.com', source)
                self.assertNotIn('/repos/', source)

    def test_read_operations_have_fixed_get_endpoints(self):
        cases = [
            ('get_latest_release', (REPO,), PREFIX + '/releases/latest'),
            ('get_commit', (REPO, A), PREFIX + '/commits/' + A),
            ('get_ref', (REPO, 'tags/v1.2.3'), PREFIX + '/git/ref/tags/v1.2.3'),
            ('get_tag', (REPO, B), PREFIX + '/git/tags/' + B),
            ('compare_commits', (REPO, A, B), PREFIX + '/compare/' + A + '...' + B),
            ('get_pull_request', (REPO, 7), PREFIX + '/pulls/7'),
        ]
        for method, args, endpoint in cases:
            with self.subTest(method=method), patch.object(self.api, '_request', return_value={'fixture': True}) as request:
                self.assertEqual(getattr(self.api, method)(*args), {'fixture': True})
                request.assert_called_once_with('GET', endpoint)

    def test_mutations_have_fixed_endpoints_and_payload_shapes(self):
        with patch.object(self.api, '_request', return_value={'id': 42}) as request:
            self.api.create_pull_request_comment(REPO, 7, 'Published')
            self.api.update_pull_request_comment(REPO, 42, 'New report')
        self.assertEqual(request.call_args_list, [
            call('POST', PREFIX + '/issues/7/comments', {'body': 'Published'}),
            call('PATCH', PREFIX + '/issues/comments/42', {'body': 'New report'}),
        ])

    def test_client_has_no_pr_creation_or_build_dispatch_operation(self):
        self.assertFalse(hasattr(self.api, 'create_pull_request'))
        self.assertFalse(hasattr(self.api, 'dispatch_build'))
        source = inspect.getsource(github.GitHub)
        self.assertNotIn('/dispatches', source)

    def test_repository_segments_cannot_retarget_an_endpoint(self):
        for repository in ('owner/repo?x=y', 'owner/repo/extra', 'owner\n/repo', '../repo', 'owner/..', './repo', 'https://elsewhere'):
            with self.subTest(repository=repository), patch.object(self.api, '_request') as request:
                with self.assertRaises(ValueError):
                    self.api.get_latest_release(repository)
                request.assert_not_called()

    def test_ref_text_is_encoded_and_ref_traversal_is_rejected(self):
        with patch.object(self.api, '_request') as request:
            self.api.get_ref(REPO, 'heads/feature/a?state=all#fragment')
            request.assert_called_once_with('GET', PREFIX + '/git/ref/heads/feature/a%3Fstate%3Dall%23fragment')
        for ref in ('refs/heads/main', 'tags/', 'heads/a/../../issues', 'heads/a//b', 'heads/./b'):
            with self.subTest(ref=ref), patch.object(self.api, '_request') as request:
                with self.assertRaises(ValueError):
                    self.api.get_ref(REPO, ref)
                request.assert_not_called()

    def test_commit_operations_require_complete_lowercase_hashes(self):
        for commit in ('abc', 'main', 'A' * 40, A + '?page=2'):
            with self.subTest(commit=commit), patch.object(self.api, '_request') as request:
                for operation in (
                    lambda: self.api.get_commit(REPO, commit),
                    lambda: self.api.get_tag(REPO, commit),
                    lambda: self.api.compare_commits(REPO, commit, B),
                    lambda: self.api.compare_commits(REPO, A, commit),
                ):
                    with self.assertRaises(ValueError):
                        operation()
                request.assert_not_called()

    def test_resource_ids_are_positive_integers_not_endpoint_fragments(self):
        for identifier in (0, -1, True, 1.5, '7/comments', '42', None):
            with self.subTest(identifier=identifier), patch.object(self.api, '_request') as request, \
                    patch.object(github, 'build_opener') as opener:
                for operation in (
                    lambda: self.api.get_pull_request(REPO, identifier),
                    lambda: list(self.api.iter_pull_request_comments(REPO, identifier)),
                    lambda: self.api.create_pull_request_comment(REPO, identifier, 'body'),
                    lambda: self.api.update_pull_request_comment(REPO, identifier, 'body'),
                    lambda: list(self.api.iter_run_artifacts(REPO, identifier, name='report')),
                    lambda: self.api.download_artifact_archive(REPO, identifier, max_bytes=100),
                ):
                    with self.assertRaises(ValueError):
                        operation()
                request.assert_not_called()
                opener.assert_not_called()


class Pagination(unittest.TestCase):
    def setUp(self):
        self.api = github.GitHub(token='fixture-token')

    def test_release_listing_paginates_without_exposing_paths_to_callers(self):
        first, second = [{'id': i} for i in range(100)], [{'id': 100}]
        with patch.object(self.api, '_request', side_effect=[first, second]) as request:
            self.assertEqual(list(self.api.iter_releases(REPO)), first + second)
        self.assertEqual(request.call_args_list, [
            call('GET', PREFIX + '/releases?per_page=100&page=1'),
            call('GET', PREFIX + '/releases?per_page=100&page=2'),
        ])

    def test_pull_request_filters_are_encoded_query_values(self):
        with patch.object(self.api, '_request', return_value=[]) as request:
            self.assertEqual(list(self.api.iter_pull_requests(REPO, state='all', head='example:feature/a&state=closed', base='feature/base')), [])
        method, path = request.call_args.args
        self.assertEqual(method, 'GET')
        self.assertEqual(urlparse(path).path, PREFIX + '/pulls')
        self.assertEqual(parse_qs(urlparse(path).query), {
            'state': ['all'], 'head': ['example:feature/a&state=closed'], 'base': ['feature/base'],
            'page': ['1'], 'per_page': ['100'],
        })
        with self.assertRaises(ValueError):
            self.api.iter_pull_requests(REPO, state='unknown')

    def test_default_pull_request_filters_omit_unused_values(self):
        with patch.object(self.api, '_request', return_value=[]) as request:
            list(self.api.iter_pull_requests(REPO))
        request.assert_called_once_with('GET', PREFIX + '/pulls?state=open&per_page=100&page=1')

    def test_conversation_comments_use_the_issues_endpoint(self):
        with patch.object(self.api, '_request', side_effect=[[{'id': 1}] * 100, [{'id': 101}]]) as request:
            comments = list(self.api.iter_pull_request_comments(REPO, 7))
        self.assertEqual(len(comments), 101)
        self.assertEqual(comments[-1]['id'], 101)
        request.assert_called_with('GET', PREFIX + '/issues/7/comments?per_page=100&page=2')

    def test_artifact_name_and_later_pages_are_preserved(self):
        responses = [{'artifacts': [{'id': i} for i in range(100)]}, {'artifacts': [{'id': 101}]}]
        with patch.object(self.api, '_request', side_effect=responses) as request:
            artifacts = list(self.api.iter_run_artifacts(REPO, 123, name='consumer-update-2'))
        self.assertEqual(len(artifacts), 101)
        self.assertEqual(artifacts[-1], {'id': 101})
        request.assert_called_with('GET', PREFIX + '/actions/runs/123/artifacts?name=consumer-update-2&per_page=100&page=2')

    def test_paginated_responses_must_be_arrays(self):
        with patch.object(self.api, '_request', return_value={'not': 'an array'}):
            with self.assertRaisesRegex(ValueError, 'array'):
                list(self.api.iter_releases(REPO))
        with patch.object(self.api, '_request', return_value={'artifacts': {}}):
            with self.assertRaisesRegex(ValueError, 'array'):
                list(self.api.iter_run_artifacts(REPO, 123, name='report'))

    def test_array_pagination_limit_refuses_a_partial_result(self):
        with patch.object(self.api, '_request', return_value=[{}] * 100) as request:
            with self.assertRaisesRegex(RuntimeError, 'refusing a partial result'):
                list(self.api.iter_releases(REPO))
        self.assertEqual(request.call_count, 100)

    def test_build_run_query_and_complete_thousand_result_boundary(self):
        with patch.object(self.api, '_request', return_value={'total_count': 1000, 'workflow_runs': [{}] * 100}) as request:
            self.assertEqual(len(list(self.api.iter_build_runs(REPO, branch='feature/a&x=y', head_sha=A))), 1000)
        self.assertEqual(request.call_count, 10)
        method, path = request.call_args.args
        self.assertEqual(method, 'GET')
        self.assertEqual(urlparse(path).path, PREFIX + '/actions/workflows/build.yml/runs')
        self.assertEqual(parse_qs(urlparse(path).query), {'branch': ['feature/a&x=y'], 'head_sha': [A], 'per_page': ['100'], 'page': ['10']})

    def test_build_history_overflow_or_unknown_full_history_is_not_partial_success(self):
        for response, count, message in (
            ({'total_count': 1001, 'workflow_runs': [{}] * 100}, 1, 'Too many matching'),
            ({'workflow_runs': [{}] * 100}, 10, 'pagination limit'),
        ):
            with self.subTest(response=response.keys()), patch.object(self.api, '_request', return_value=response) as request:
                with self.assertRaisesRegex(ValueError, message):
                    list(self.api.iter_build_runs(REPO, branch='feature/update', head_sha=A))
            self.assertEqual(request.call_count, count)

    def test_build_history_must_be_an_array(self):
        with patch.object(self.api, '_request', return_value={'workflow_runs': {}}):
            with self.assertRaisesRegex(ValueError, 'array'):
                list(self.api.iter_build_runs(REPO, branch='feature/update', head_sha=A))


class Transport(unittest.TestCase):
    def setUp(self):
        self.api = github.GitHub(token='fixture-secret')
        self.opener = Mock()
        self.build_opener = patch.object(github, 'build_opener', return_value=self.opener).start()
        self.storage = patch.object(github, 'urlopen', side_effect=AssertionError('Unexpected storage request')).start()
        self.sleep = patch.object(github.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def test_json_requests_keep_host_headers_and_payload_in_the_client(self):
        self.opener.open.return_value = Response(b'{"id":42}')
        self.assertEqual(self.api.create_pull_request_comment(REPO, 7, 'Body'), {'id': 42})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, API_URL + PREFIX + '/issues/7/comments')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'body': 'Body'})
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers['authorization'], 'Bearer fixture-secret')
        self.assertEqual(headers['content-type'], 'application/json')
        self.assertEqual(headers['accept'], 'application/vnd.github+json')
        self.assertEqual(headers['x-github-api-version'], '2022-11-28')
        self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 60})

    def test_missing_and_explicit_tokens_are_handled_privately(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'environment-secret'}):
            for token, expected in ((None, 'Bearer environment-secret'), ('', None), ('explicit', 'Bearer explicit')):
                with self.subTest(token=token):
                    self.opener.open.return_value = Response(b'{}')
                    github.GitHub(token).get_latest_release(REPO)
                    headers = {key.lower(): value for key, value in self.opener.open.call_args.args[0].header_items()}
                    self.assertEqual(headers.get('authorization'), expected)

    def test_empty_success_response_needs_no_json_decoder(self):
        self.opener.open.return_value = Response(b'')
        self.assertIsNone(self.api.update_pull_request_comment(REPO, 42, 'Body'))

    def test_read_retries_are_bounded(self):
        self.opener.open.side_effect = [http_error(503), http_error(429), Response(b'{"ok":true}')]
        self.assertEqual(self.api.get_latest_release(REPO), {'ok': True})
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_exhausted_read_retries_raise(self):
        self.opener.open.side_effect = [http_error(503) for _ in range(3)]
        with self.assertRaises(github.ApiError) as failure:
            self.api.get_latest_release(REPO)
        self.assertEqual(failure.exception.status, 503)
        self.assertEqual(self.opener.open.call_count, 3)

    def test_mutation_failures_are_never_automatically_retried(self):
        for operation in (
            lambda: self.api.create_pull_request_comment(REPO, 7, 'Body'),
            lambda: self.api.update_pull_request_comment(REPO, 42, 'Body'),
        ):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = http_error(503)
            with self.assertRaises(github.ApiError):
                operation()
            self.assertEqual(self.opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_nontransient_read_errors_are_not_retried(self):
        self.opener.open.side_effect = http_error(403)
        with self.assertRaises(github.ApiError) as failure:
            self.api.get_latest_release(REPO)
        self.assertEqual(failure.exception.status, 403)
        self.assertEqual(self.opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_connection_failure_does_not_expose_request_or_token(self):
        self.opener.open.side_effect = URLError('fixture-secret at an internal URL')
        with self.assertRaisesRegex(RuntimeError, '^GitHub API connection failed$'):
            self.api.get_latest_release(REPO)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_authenticated_json_requests_do_not_follow_redirects(self):
        self.opener.open.side_effect = http_error(302, {'Location': 'https://storage.invalid/signed'})
        with self.assertRaises(github.ApiError):
            self.api.get_latest_release(REPO)
        handler = self.build_opener.call_args.args[0]
        self.assertIsInstance(handler, github._NoRedirect)
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://storage.invalid/signed'))
        self.storage.assert_not_called()

    def test_artifact_download_is_bounded_at_the_api_response(self):
        response = Response(b'zip')
        self.opener.open.return_value = response
        self.assertEqual(self.api.download_artifact_archive(REPO, 91, max_bytes=3), b'zip')
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, API_URL + PREFIX + '/actions/artifacts/91/zip')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(response.read_sizes, [4])
        self.storage.assert_not_called()

    def test_signed_storage_download_has_no_api_authentication_header(self):
        location = 'https://storage.invalid/signed?signature=opaque'
        self.opener.open.side_effect = http_error(302, {'Location': location})
        response = Response(b'zip')
        self.storage.side_effect = None
        self.storage.return_value = response
        self.assertEqual(self.api.download_artifact_archive(REPO, 91, max_bytes=3), b'zip')
        initial = self.opener.open.call_args.args[0]
        download = self.storage.call_args.args[0]
        self.assertEqual(initial.get_header('Authorization'), 'Bearer fixture-secret')
        self.assertEqual(download.full_url, location)
        self.assertEqual(download.header_items(), [])
        self.assertIsNone(download.data)
        self.assertEqual(response.read_sizes, [4])
        self.assertIsInstance(self.build_opener.call_args.args[0], github._NoRedirect)

    def test_oversized_archives_are_rejected_on_both_download_paths(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                response = Response(b'x' * 100)
                self.opener.open.side_effect = http_error(302, {'Location': 'https://storage.invalid/signed'}) if redirected else None
                self.opener.open.return_value = response
                self.storage.side_effect = None
                self.storage.return_value = response
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    self.api.download_artifact_archive(REPO, 91, max_bytes=3)
                self.assertEqual(response.read_sizes, [4])

    def test_unsafe_or_missing_storage_redirect_is_rejected(self):
        for location in ('', 'http://storage.invalid/signed', 'file:///etc/passwd', 'https://user:password@storage.invalid/path', 'https:///missing-host'):
            with self.subTest(location=location):
                self.opener.open.side_effect = http_error(302, {'Location': location})
                with self.assertRaisesRegex(ValueError, 'redirect'):
                    self.api.download_artifact_archive(REPO, 91, max_bytes=100)
        self.storage.assert_not_called()

    def test_artifact_http_failures_do_not_try_the_storage_download(self):
        self.opener.open.side_effect = http_error(404)
        with self.assertRaisesRegex(RuntimeError, 'Artifact download failed: HTTP 404'):
            self.api.download_artifact_archive(REPO, 91, max_bytes=100)
        self.storage.assert_not_called()

    def test_archive_size_limit_is_required_and_positive(self):
        for limit in (0, -1, 1.5, True, '100'):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'size limit'):
                self.api.download_artifact_archive(REPO, 91, max_bytes=limit)
        self.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
