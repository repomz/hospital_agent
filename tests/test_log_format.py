import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hospital_agent.app import AgentContextFilter, StructuredLineFormatter
from hospital_agent.support.dicom import load_pacs_config


class LogFormatTests(unittest.TestCase):
    def test_traceback_lines_keep_context(self):
        try:
            raise PermissionError("dicom")
        except PermissionError:
            import sys

            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "Failed", (), sys.exc_info()
            )
        AgentContextFilter("2").filter(record)
        lines = StructuredLineFormatter().format(record).splitlines()
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(" | ERROR | agent_2 | " in line for line in lines))
        self.assertIn("PermissionError", lines[-1])

    def test_dicom_directory_is_relative_to_configuration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"local": {"output_dir": "dicom"}}), encoding="utf-8")
            self.assertEqual(
                load_pacs_config(path)["local"]["output_dir"], str(path.resolve().parent / "dicom")
            )
