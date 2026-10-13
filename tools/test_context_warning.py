import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('warning', Path(__file__).with_name('context-warning.py'))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class ContextWarning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcript = self.root / 'metadata.jsonl'
        self.data = {'session_id': 'test', 'hook_event_name': 'Stop',
                     'transcript_path': str(self.transcript)}

    def metric(self, used, window=100):
        self.transcript.write_text(json.dumps({'type': 'event_msg', 'payload': {
            'type': 'token_count', 'info': {'last_token_usage': {'total_tokens': used},
            'total_token_usage': {'total_tokens': 9999999}, 'model_context_window': window}}}) + '\n')

    def run_hook(self, **extra):
        return hook.handle({**self.data, **extra}, self.root)

    def test_threshold_and_dedup(self):
        self.metric(49)
        self.assertEqual(self.run_hook(), {})
        self.metric(50)
        output = self.run_hook()
        self.assertEqual(output['decision'], 'block')
        self.assertIn('50%', output['reason'])
        self.assertIn('-handoff.md', output['reason'])
        self.assertEqual(self.run_hook(), {})

    def test_no_continuation_loop(self):
        self.metric(90)
        self.assertEqual(self.run_hook(stop_hook_active=True), {})

    def test_missing_or_invalid_metrics(self):
        self.transcript.write_text('{}\n')
        self.assertEqual(self.run_hook(), {})
        self.metric(100, 0)
        self.assertEqual(self.run_hook(), {})

    def test_latest_request_not_cumulative(self):
        self.metric(20)
        self.assertEqual(self.run_hook(), {})

    def test_compact_fallback_without_metrics(self):
        out = self.run_hook(hook_event_name='PreCompact')
        self.assertNotIn('continue', out)
        out = self.run_hook(hook_event_name='SessionStart', source='compact')
        text = out['hookSpecificOutput']['additionalContext']
        self.assertIn('Сохрани или обнови', text)
        self.assertNotIn('%', text)

    def test_independent_sessions_and_subagents(self):
        self.metric(60)
        self.assertEqual(self.run_hook(agent_id='child'), {})
        self.assertEqual(self.run_hook()['decision'], 'block')
        self.assertEqual(self.run_hook(session_id='other')['decision'], 'block')

    def test_does_not_copy_messages(self):
        self.metric(60)
        with self.transcript.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {
                'role': 'user', 'content': 'PRIVATE_SENTINEL'}}) + '\n')
        out = self.run_hook()
        state = list((self.root / 'progress/context').glob('*.json'))[0].read_text()
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(out) + state)
        self.assertFalse(list((self.root / 'progress/context').glob('*.md')))


if __name__ == '__main__':
    unittest.main()
