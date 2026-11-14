"""Regression checks for servers that ignore byte ranges; no credentials/network."""
import ast
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock


class FetchTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name("matterport_quickstart.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch")
        self.response = Mock(status_code=200)
        self.response.iter_content.return_value = iter([b"abcdef", b"ghijkl"])
        self.namespace = dict(requests=Mock(), SITE="https://example.test", SITE_AUTH=None, DOWNLOADED=[0],
                              FETCH_LOCK=threading.Lock(), FETCH_LAST=[0.0], time=time)
        self.namespace["requests"].get.return_value = self.response
        exec(compile(ast.Module(body=[function], type_ignores=[]), "fetch", "exec"), self.namespace)

    def test_ignored_prefix_range_is_bounded(self):
        self.assertEqual(self.namespace["fetch"]("https://cdn.test/file", 0, 3), b"abcd")
        self.assertEqual(self.namespace["DOWNLOADED"][0], 4)
        self.response.close.assert_called_once()

    def test_ignored_nonzero_range_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "nonzero byte range"):
            self.namespace["fetch"]("https://cdn.test/file", 5, 8)
        self.response.close.assert_called_once()

    def test_partial_response_is_read(self):
        self.response.status_code = 206
        self.assertEqual(self.namespace["fetch"]("https://cdn.test/file", 5, 8), b"abcd")
        self.response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
