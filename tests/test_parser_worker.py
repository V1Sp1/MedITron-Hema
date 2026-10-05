"""Verify parser timeout cleanup for both process termination policies."""
import subprocess
from datetime import date
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import parser_service


class WorkerTimeoutTests(unittest.TestCase):
    def test_windows_timeout_terminates_tree_and_fallback_worker(self):
        process = Mock(pid=1234)
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 180), None]
        process.poll.return_value = None
        with patch.object(parser_service, 'os', SimpleNamespace(name='nt', environ={})), \
                patch.object(parser_service.subprocess, 'Popen', return_value=process), \
                patch.object(parser_service.subprocess, 'run') as terminate_tree:
            with self.assertRaises(TimeoutError):
                parser_service.run_parser([], ocr_mode='off', assessed_on=date.today(), freshness_policy={})
        self.assertEqual(terminate_tree.call_args.args[0], ['taskkill', '/PID', '1234', '/T', '/F'])
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_posix_timeout_terminates_process_group(self):
        process = Mock(pid=5678)
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 180), None]
        terminate_group = Mock()
        with patch.object(parser_service, 'os', SimpleNamespace(name='posix', killpg=terminate_group, environ={})), \
                patch.object(parser_service.subprocess, 'Popen', return_value=process):
            with self.assertRaises(TimeoutError):
                parser_service.run_parser([], ocr_mode='off', assessed_on=date.today(), freshness_policy={})
        terminate_group.assert_called_once_with(5678, parser_service.signal.SIGKILL)
        process.kill.assert_not_called()
