"""Guard the security and event boundaries of the three automation workflows."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowBoundaries(unittest.TestCase):
    def setUp(self):
        folder = ROOT / '.github/workflows'
        self.build = (folder / 'build.yml').read_text()
        self.update = (folder / 'update-llama-cpp.yml').read_text()
        self.report = (folder / 'runtime-comments.yml').read_text()

    def test_existing_push_publish_and_full_matrix_remain(self):
        for profile in ['cpu-wasm32', 'cpu-wasm64', 'webgpu-wasm32-asyncify', 'webgpu-wasm32-jspi', 'webgpu-wasm64-jspi']:
            self.assertIn('- ' + profile, self.build)
        self.assertIn('needs: [test, compile]', self.build)
        self.assertIn('max-parallel: 4', self.build)
        self.assertIn('fail-fast: false', self.build)
        self.assertNotIn('pull_request:', self.build)
        self.assertNotIn('concurrency:', self.build)

    def test_expected_source_guard_precedes_host_tests(self):
        self.assertLess(self.build.index('if [[ -n "$EXPECTED_SOURCE"'), self.build.index('          npm test'))
        self.assertIn('EXPECTED_SOURCE: ${{ inputs.expected_source }}', self.build)

    def test_lock_resolution_is_after_publication_and_report_is_attempt_bound(self):
        self.assertLess(self.build.index('scripts/publish_artifacts.py'), self.build.index('scripts/consumer_metadata.py'))
        self.assertIn('consumer-update-${{ github.run_attempt }}', self.build)
        self.assertIn('steps.publish.outputs.commit', self.build)
        self.assertIn('consumer reporting failed', self.build)

    def test_updater_inputs_are_data_not_shell_interpolation(self):
        for item in ['options: [latest, latest-unstable, custom]', 'allow_non_fast_forward:', 'actions: write', 'pull-requests: write']:
            self.assertIn(item, self.update)
        run = self.update.split('        run: |', 1)[1].split('      - uses:', 1)[0]
        self.assertNotIn('${{', run)
        self.assertIn('--custom-ref "$CUSTOM_REF"', run)
        self.assertNotIn('secrets.', self.update)
        self.assertNotIn('npm ', self.update)

    def test_privileged_reporter_always_checks_out_the_default_branch(self):
        self.assertIn('ref: ${{ github.event.repository.default_branch }}', self.report)
        self.assertIn('persist-credentials: false', self.report)
        self.assertNotIn('ref: ${{ github.event.pull_request.head', self.report)
        self.assertNotIn('submodules:', self.report)
        self.assertNotIn('contents: write', self.report)
        self.assertNotIn('npm install', self.report)
        self.assertNotIn('actions: write', self.report)
        self.assertIn('head.repo.full_name == github.repository', self.report)

    def test_reporter_covers_late_prs_reruns_and_manual_repair(self):
        self.assertIn('types: [opened, reopened, synchronize]', self.report)
        self.assertIn('types: [requested, in_progress, completed]', self.report)
        self.assertIn('workflow_dispatch:', self.report)
        self.assertIn('cancel-in-progress: false', self.report)

    def test_generated_consumer_knowledge_is_not_part_of_the_package_builder(self):
        generator = (ROOT / 'scripts/consumer_metadata.py').read_text()
        self.assertIn('Intentional policy exception', generator)
        for relative in ['scripts/build.py', 'scripts/package_runtime.py', 'scripts/stage_ci_build.py', 'CMakeLists.txt']:
            code = (ROOT / relative).read_text().lower()
            self.assertNotIn('naidan', code)
            self.assertNotIn('consumer_knowledge', code)
        self.assertNotIn('must not normally', generator.split('def consumer_knowledge()', 1)[1].split('return {', 1)[1])


if __name__ == '__main__':
    unittest.main()
