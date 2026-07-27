import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.polling.pacs_studies import run_modality_polling
from hospital_agent.state import AgentState


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)


class PACSPollingTests(unittest.TestCase):
    def test_polling_only_sends_studies_with_valid_time_after_enable(self):
        studies = [
            {
                "uid": "1.2.1",
                "date": "20260727",
                "time": "",
            },
            {
                "uid": "1.2.2",
                "date": "20260727",
                "time": "150000",
            },
            {
                "uid": "1.2.3",
                "date": "20260727",
                "time": "163000",
            },
        ]
        pacs_client = MagicMock()
        pacs_client.find_studies.return_value = studies
        polling = SimpleNamespace(state=True)

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                pacs_config_path=Path(directory) / "config.json",
            )
            state = AgentState(
                polling_enabled_at={
                    "CT": "2026-07-27T09:00:00+00:00",
                }
            )
            with patch(
                "hospital_agent.polling.pacs_studies.datetime",
                FrozenDatetime,
            ), patch(
                "hospital_agent.services.pacs.PACSClient",
                return_value=pacs_client,
            ), patch(
                "hospital_agent.support.dicom.load_pacs_config",
                return_value={},
            ), patch(
                "hospital_agent.polling.pacs_studies.get_dicom_study",
            ) as get_study:
                sent = run_modality_polling(
                    config,
                    polling,
                    "CT",
                    MagicMock(),
                    state,
                )

        self.assertEqual(sent, 1)
        self.assertEqual(get_study.call_args.args[1]["study_uid"], "1.2.3")
        self.assertEqual(state.processed_modality_studies["CT"], ["1.2.3"])


if __name__ == "__main__":
    unittest.main()
