import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.polling.pacs_studies import (
    disable_expired_polling,
    run_modality_polling,
)
from hospital_agent.state import AgentState


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)

class FrozenWednesdayDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
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

    def test_xa_polling_sends_the_current_week(self):
        studies = [
            {"uid": "1.2.0", "date": "20260726", "time": "120000", "series": "4", "instances": "120"},
            {"uid": "1.2.1", "date": "20260727", "time": "120000", "series": "4", "instances": "120"},
            {"uid": "1.2.2", "date": "20260729", "time": "120000", "series": "6", "instances": "180"},
        ]
        pacs_client = MagicMock()
        pacs_client.find_studies.return_value = studies

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                pacs_config_path=Path(directory) / "config.json",
            )
            state = AgentState(pending_xa_studies={
                "1.2.1": {"series": 4, "instances": 120, "unchanged_since": "2026-07-29T09:30:00+00:00"},
                "1.2.2": {"series": 6, "instances": 180, "unchanged_since": "2026-07-29T09:30:00+00:00"},
            })
            with patch(
                "hospital_agent.polling.pacs_studies.datetime",
                FrozenWednesdayDatetime,
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
                    SimpleNamespace(state=True),
                    "XA",
                    MagicMock(),
                    state,
                )

        self.assertEqual(sent, 2)
        pacs_client.find_studies.assert_called_once_with(
            modality="XA",
            date_range="20260727-20260729",
        )
        self.assertEqual(
            [call.args[1]["study_uid"] for call in get_study.call_args_list],
            ["1.2.1", "1.2.2"],
        )
        self.assertEqual(state.pending_xa_studies, {})

    def test_xa_polling_waits_for_unchanged_pacs_counts(self):
        study = {
            "uid": "1.2.7",
            "date": "20260729",
            "time": "120000",
            "series": "5",
            "instances": "140",
        }
        pacs_client = MagicMock()
        pacs_client.find_studies.return_value = [study]

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                pacs_config_path=Path(directory) / "config.json",
            )
            state = AgentState()
            with patch(
                "hospital_agent.polling.pacs_studies.datetime",
                FrozenWednesdayDatetime,
            ), patch(
                "hospital_agent.services.pacs.PACSClient",
                return_value=pacs_client,
            ), patch(
                "hospital_agent.support.dicom.load_pacs_config",
                return_value={},
            ), patch(
                "hospital_agent.polling.pacs_studies.get_dicom_study",
            ) as get_study:
                first = run_modality_polling(
                    config, SimpleNamespace(state=True), "XA", MagicMock(), state
                )
                # More instances arrived: the 20-minute stability window restarts.
                state.pending_xa_studies["1.2.7"]["unchanged_since"] = (
                    "2026-07-29T09:00:00+00:00"
                )
                study["instances"] = "160"
                second = run_modality_polling(
                    config, SimpleNamespace(state=True), "XA", MagicMock(), state
                )

        self.assertEqual((first, second), (0, 0))
        get_study.assert_not_called()
        self.assertEqual(state.pending_xa_studies["1.2.7"]["instances"], 160)
        self.assertEqual(
            state.pending_xa_studies["1.2.7"]["unchanged_since"],
            "2026-07-29T10:00:00+00:00",
        )

    def test_polling_does_not_start_another_study_after_shutdown_request(self):
        studies = [
            {"uid": "1.2.1", "date": "20260727", "time": "120000", "series": "4", "instances": "120"},
            {"uid": "1.2.2", "date": "20260729", "time": "120000", "series": "5", "instances": "150"},
        ]
        pacs_client = MagicMock()
        pacs_client.find_studies.return_value = studies
        stopping = False

        def finish_first_study(*_args, **_kwargs):
            nonlocal stopping
            stopping = True

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                pacs_config_path=Path(directory) / "config.json",
            )
            with patch(
                "hospital_agent.polling.pacs_studies.datetime",
                FrozenWednesdayDatetime,
            ), patch(
                "hospital_agent.services.pacs.PACSClient",
                return_value=pacs_client,
            ), patch(
                "hospital_agent.support.dicom.load_pacs_config",
                return_value={},
            ), patch(
                "hospital_agent.polling.pacs_studies.get_dicom_study",
                side_effect=finish_first_study,
            ) as get_study:
                state = AgentState(pending_xa_studies={
                    "1.2.1": {"series": 4, "instances": 120, "unchanged_since": "2026-07-29T09:30:00+00:00"},
                    "1.2.2": {"series": 5, "instances": 150, "unchanged_since": "2026-07-29T09:30:00+00:00"},
                })
                sent = run_modality_polling(
                    config,
                    SimpleNamespace(state=True),
                    "XA",
                    MagicMock(),
                    state,
                    stop_requested=lambda: stopping,
                )

        self.assertEqual(sent, 1)
        self.assertEqual(get_study.call_count, 1)

    def test_expiration_does_not_disable_persistent_xa_polling(self):
        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                ct_polling=SimpleNamespace(state=False),
                xa_polling=SimpleNamespace(state=True),
            )
            state = AgentState(
                polling_enabled_at={"XA": "2026-07-26T09:00:00+00:00"}
            )
            with patch(
                "hospital_agent.polling.pacs_studies.datetime",
                FrozenWednesdayDatetime,
            ), patch(
                "hospital_agent.polling.pacs_studies.update_polling_state",
            ) as update:
                disabled = disable_expired_polling(config, state)

        self.assertEqual(disabled, 0)
        update.assert_not_called()
        self.assertIn("XA", state.polling_enabled_at)


if __name__ == "__main__":
    unittest.main()
