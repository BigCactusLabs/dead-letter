"""Stdlib regressions for the installed-package probe itself."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    import package_probe as probe


class CliProbeTests(unittest.TestCase):
    def test_output_destination_is_explicit_and_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "synthetic.eml"
            fixture.write_text("Subject: a different name\n\n" + probe.BODY)
            def command(args, **kwargs):
                if args[-1] == "--help":
                    return subprocess.CompletedProcess(args, 0, stdout="convert")
                self.assertEqual(args[1:4], ["convert", str(fixture), "--output"])
                self.assertEqual(args[4], str(fixture.with_suffix(".md")))
                self.assertTrue(kwargs["check"])
                Path(args[4]).write_text(probe.BODY)
                return subprocess.CompletedProcess(args, 0)
            with patch.object(probe.subprocess, "run", side_effect=command) as run:
                probe.cli_probe(fixture)
            self.assertEqual(run.call_count, 2)

    def test_success_exit_without_output_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "synthetic.eml"
            with patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="convert")):
                with self.assertRaises(FileNotFoundError):
                    probe.cli_probe(fixture)

    def test_stale_output_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "synthetic.eml"
            fixture.with_suffix(".md").write_text(probe.BODY)
            with patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="convert")) as run:
                with self.assertRaisesRegex(RuntimeError, "fresh"):
                    probe.cli_probe(fixture)
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
