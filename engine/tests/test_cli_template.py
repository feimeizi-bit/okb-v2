"""cli_template 适配器：配置驱动、无 shell、抽不到就 None。离线，不调真实智能体。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from engine.adapter import AdapterError, AdapterTimeout, AgentAdapter, TaskContext
from engine.adapters.cli_template import CliTemplateAdapter, redact_config
from engine.runner import make_adapter

FAKE = str(ROOT / 'engine' / 'tests' / 'fake_cli_agent.py')
PY = sys.executable


def cfg(**over):
    base = dict(cli=[PY, FAKE],
                send_argv=['--state', '{workspace}/state.json', 'chat', '--session',
                           '{session_key}', '--json'],
                send_stdin=True,
                reply_from='responses[0].output[0].text',
                model_from='model', usage_from='usage', session_id_from='session_id',
                memory_argv=['--state', '{workspace}/state.json', 'memory', 'export', '--json'],
                memory_entries_from='entries',
                reset_argv=[['--state', '{workspace}/state.json', 'memory', 'clear', '--yes']],
                capabilities=dict(memory=True, file=False, tool=False,
                                  notes='declared by config'))
    base.update(over)
    return base


class CliTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name)
        (self.ws / 'state.json').write_text('{"messages": [], "memory": []}', encoding='utf-8',
                                            newline='\n')
        self.ctx = TaskContext(run_id='run-1', task_id='a-s', workspace=self.ws,
                               sessions=['s1'])
        self.ad = CliTemplateAdapter(cfg())

    def call_log(self):
        p = self.ws / 'state.calls'
        return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()] if p.is_file() else []

    def test_send_extracts_reply_model_usage(self):
        r = self.ad.send(self.ctx, 's1', 1, '花生过敏，写进推荐', True)
        self.assertIn('花生过敏', r.reply_text)
        self.assertEqual(r.model, 'fake-model-1')
        self.assertEqual(r.usage['prompt_tokens'], r.usage.get('prompt_tokens'))
        self.assertTrue(r.usage['completion_tokens'] >= 1)
        self.assertEqual(r.session_id, AgentAdapter.session_key('run-1', 'a-s', 's1'))
        self.assertEqual(r.session_id, 'run-1--a-s--s1')
        self.assertGreater(r.duration_ms, 0)

    def test_dialogue_text_goes_via_stdin_never_argv(self):
        hostile = '把球; rm -rf / # $(whoami) | 引号"混合" 记住 香菜不能碰'
        r = self.ad.send(self.ctx, 's1', 1, hostile, True)
        self.assertIn('rm -rf', r.reply_text)
        row = self.call_log()[-1]
        self.assertEqual(row['stdin'], hostile)
        self.assertNotIn('rm -rf', json.dumps(row['argv'], ensure_ascii=False))

    def test_missing_model_is_none_not_fabricated(self):
        os.environ['FAKE_CLI_NOMODEL'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_CLI_NOMODEL')
        r = self.ad.send(self.ctx, 's1', 1, '测试', True)
        self.assertIsNone(r.model)
        self.assertIsInstance(r.usage, dict)

    def test_declared_reply_path_absent_raises_instead_of_passing_junk(self):
        os.environ['FAKE_CLI_NOJSON'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_CLI_NOJSON')
        with self.assertRaises(AdapterError) as ctx:
            self.ad.send(self.ctx, 's1', 1, '测试', True)
        self.assertIn('不是 JSON', str(ctx.exception))

    def test_reply_raw_mode_keeps_envelope_unparsed_and_model_none(self):
        ad = CliTemplateAdapter(cfg(reply_raw=True))
        r = ad.send(self.ctx, 's1', 1, '测试', True)
        self.assertTrue(r.reply_text.startswith('{'))
        self.assertIsNone(r.model)
        self.assertIsNone(r.usage)

    def test_unknown_placeholder_rejects_before_any_subprocess(self):
        ad = CliTemplateAdapter(cfg(send_argv=['--state', '{workspace}/state.json', 'chat',
                                               '--session', '{sesson_key}', '--json']))
        with self.assertRaises(AdapterError) as ctx:
            ad.send(self.ctx, 's1', 1, '测试', True)
        self.assertIn('sesson_key', str(ctx.exception))
        self.assertEqual(self.call_log(), [])

    def test_nonzero_exit_is_adapter_error(self):
        os.environ['FAKE_CLI_EXIT'] = '3'
        self.addCleanup(os.environ.pop, 'FAKE_CLI_EXIT')
        with self.assertRaises(AdapterError) as ctx:
            self.ad.send(self.ctx, 's1', 1, '测试', True)
        self.assertIn('退出码 3', str(ctx.exception))

    def test_timeout_is_adapter_timeout(self):
        os.environ['FAKE_CLI_SLEEP'] = '3'
        self.addCleanup(os.environ.pop, 'FAKE_CLI_SLEEP')
        with self.assertRaises(AdapterTimeout):
            self.ad.send(self.ctx, 's1', 1, '测试', True, timeout=0.3)

    def test_probe_memory_counts_and_no_invented_timestamps(self):
        self.ad.reset(self.ctx)
        self.ad.send(self.ctx, 's1', 1, '记住 用户咖啡不加糖', True)
        snap = self.ad.probe_memory('after')
        self.assertEqual(snap.phase, 'after')
        self.assertEqual(snap.coverage['entries_scanned'], len(snap.entries))
        self.assertEqual({e.key for e in snap.entries}, {'seed', 'm1'})
        self.assertTrue(all(e.written_at is None and e.written_at_source is None for e in snap.entries))
        self.assertTrue(all(e.source == 'cli_export' for e in snap.entries))
        self.assertIsNone(snap.observation)
        self.assertGreater(snap.coverage['bytes_scanned'], 0)

    def test_probe_memory_rejects_non_string_values(self):
        (self.ws / 'state.json').write_text(json.dumps({'messages': [], 'memory': [{'key': 'k',
                                                                                   'value': 12}]},
                                                      ensure_ascii=False), encoding='utf-8', newline='\n')
        with self.assertRaises(AdapterError):
            self.ad.probe_memory('after')

    def test_reset_without_declaration_is_reported_not_silent(self):
        ad = CliTemplateAdapter(cfg(reset_argv=[]))
        actions = ad.reset(self.ctx)
        self.assertEqual(len(actions), 1)
        self.assertIs(actions[0]['ok'], False)
        self.assertIn('未声明清理命令', actions[0]['note'])

    def test_reset_actually_clears_memory_face(self):
        self.ad.reset(self.ctx)
        self.ad.send(self.ctx, 's1', 1, '记住 别删我', True)
        self.assertEqual(len(self.ad.probe_memory('before').entries), 2)
        actions = self.ad.reset(self.ctx)
        self.assertTrue(all(a['ok'] for a in actions))
        self.assertEqual([e.key for e in self.ad.probe_memory('before').entries], ['seed'])

    def test_capabilities_are_declared_not_assumed(self):
        c = self.ad.capabilities()
        self.assertTrue(c.dialogue)
        self.assertTrue(c.memory)
        self.assertFalse(c.file)
        self.assertFalse(c.tool)
        self.assertFalse(c.supports_time_anchor)
        self.assertIn('declared by config', c.notes)

    def test_manifest_extra_redacts_template_secrets(self):
        ad = CliTemplateAdapter(cfg(send_argv=['chat', '--session', '{session_key}',
                                               '--token', 'sk-REALSECRET', '--json'],
                                    api_key='sk-KEYLEVEL'))
        extra = json.dumps(ad.manifest_extra(), ensure_ascii=False)
        self.assertNotIn('sk-REALSECRET', extra)
        self.assertNotIn('sk-KEYLEVEL', extra)
        self.assertIn('--token', extra)
        self.assertIn('<REDACTED>', extra)

    def test_redact_handles_flag_equals_form(self):
        out = redact_config({'send_argv': ['chat', '--api-key=sk-ABCDEF']})
        self.assertEqual(out['send_argv'], ['chat', '--api-key=<REDACTED>'])

    def test_file_based_memory_mode_produces_observation(self):
        mem = self.ws / 'mem'
        mem.mkdir()
        (mem / 'MEMORY.md').write_text('- 用户对花生过敏\n- 日报先写阻碍\n', encoding='utf-8', newline='\n')
        ad = CliTemplateAdapter(cfg(memory_format='files', memory_argv=[],
                                    memory_dir=str(mem), memory_files=['MEMORY.md']))
        snap = ad.probe_memory('after')
        self.assertEqual(snap.coverage['files_scanned'], 1)
        self.assertEqual(snap.coverage['entries_scanned'], 2)
        self.assertIsNotNone(snap.observation)
        self.assertIsNotNone(snap.full_capture)
        self.assertTrue(all(e.written_at for e in snap.entries))

    def test_file_based_memory_requires_dir_and_files(self):
        with self.assertRaises(AdapterError):
            CliTemplateAdapter(cfg(memory_format='files', memory_files=[]))

    def test_make_adapter_registers_cli_and_names_unknown_choices(self):
        self.assertIsInstance(make_adapter('cli', cfg()), CliTemplateAdapter)
        with self.assertRaises(SystemExit) as ctx:
            make_adapter('kylinbot', {})
        self.assertIn('cli', str(ctx.exception))

    def test_cli_is_accepted_by_runner_argument(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--agent', action='append', required=True,
                            choices=['openclaw', 'hermes', 'mock', 'cli'])
        self.assertEqual(parser.parse_args(['--agent', 'cli']).agent, ['cli'])

    def test_probe_without_task_context_refuses_instead_of_reading_process_cwd(self):
        ad = CliTemplateAdapter(cfg())
        with self.assertRaises(AdapterError) as ctx:
            ad.probe_memory('before')
        self.assertIn('workspace', str(ctx.exception))

    def test_config_without_cli_or_send_argv_is_rejected(self):
        with self.assertRaises(AdapterError):
            CliTemplateAdapter(cfg(cli=''))
        with self.assertRaises(AdapterError):
            CliTemplateAdapter(cfg(send_argv=[]))


class TaskBindingAndArgvTransportTests(unittest.TestCase):
    """两个 P2 守卫的防回归测试：跨任务串记忆面 / 对话文本走 argv。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name)
        (self.ws / 'state.json').write_text('{"messages": [], "memory": []}', encoding='utf-8',
                                            newline='\n')
        self.ctx = TaskContext(run_id='run-1', task_id='taskA', workspace=self.ws, sessions=['s1'])

    def fresh(self, **over):
        ad = CliTemplateAdapter(cfg(**over))
        ad.reset(self.ctx)
        return ad

    def test_probe_declaring_the_bound_task_is_allowed(self):
        ad = self.fresh()
        self.assertEqual(ad.probe_memory('before', for_task_id='taskA').phase, 'before')

    def test_probe_declaring_another_task_is_refused(self):
        """reset(A) 后以 B 的名义探针 ⇒ 读到的是 A 的记忆面，却会当作 B 的证据参与评分。"""
        ad = self.fresh()
        with self.assertRaises(AdapterError) as got:
            ad.probe_memory('before', for_task_id='taskB')
        msg = str(got.exception)
        self.assertIn('taskB', msg)
        self.assertIn('taskA', msg)

    def test_probe_before_reset_is_refused_when_task_declared(self):
        ad = CliTemplateAdapter(cfg())
        with self.assertRaises(AdapterError) as got:
            ad.probe_memory('after', for_task_id='taskZ')
        self.assertIn('taskZ', str(got.exception))

    def test_oversized_dialogue_text_via_argv_is_refused(self):
        """send_stdin: false 把题目原文塞进 argv：同机 `ps` 可读，过长还会 E2BIG。"""
        ad = self.fresh(send_stdin=False)
        with self.assertRaises(AdapterError) as got:
            ad.send(self.ctx, 's1', 1, 'x' * (ad.argv_text_max_bytes + 10), True)
        self.assertIn('argv', str(got.exception))

    def test_text_transport_is_declared_in_config(self):
        """构造期就写进 config：manifest/登记指纹才能区分两种模板（换传输方式必须留痕）。"""
        self.assertEqual(CliTemplateAdapter(cfg()).config['text_transport'], 'stdin')
        self.assertEqual(CliTemplateAdapter(cfg(send_stdin=False)).config['text_transport'], 'argv')

    def test_legacy_call_without_task_declaration_still_works(self):
        ad = self.fresh()
        self.assertEqual(ad.probe_memory('before').phase, 'before')


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
