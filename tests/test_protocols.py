import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.config import PollingConfig
from hospital_agent.polling.protocols import (
    classify_study_type,
    iter_protocol_files,
    normalize_surgeon,
    parse_protocol,
    parse_study_id,
    poll_operation_protocols,
)
from hospital_agent.state import AgentState


class ProtocolMappingTests(unittest.TestCase):
    def test_study_id_allows_spaces_inside_operation_label(self):
        self.assertEqual(parse_study_id("О перация: 559"), "559")

    def test_study_type_classification(self):
        cases = {
            "КАГ": "каг",
            "ЦАГ": "цаг",
            "Стентирование ВСА слева": "стент_вса",
            "Стентирование коронарных артерий": "стент_кор",
            "Баллонная ангиопластика нижней конечности": "бап_периферии",
            "БАП артерий НК": "бап_периферии",
            "АГ балонная ангилопластика задней большеберцовой": "бап_периферии",
            "Тромбаспирация": "тромбаспирация",
            "ТА": "тромбаспирация",
        }
        for operation, expected in cases.items():
            with self.subTest(operation=operation):
                self.assertEqual(classify_study_type(operation), expected)

    def test_surgeon_is_normalized_to_backend_value(self):
        self.assertEqual(normalize_surgeon("Идрисов Р.Ш."), "идрисов")
        self.assertEqual(normalize_surgeon("Новый-Хирург А.Б."), "новый-хирург")

    def test_unknown_operation_becomes_a_valid_study_type(self):
        self.assertEqual(
            classify_study_type("Имплантация двухкамерного ЭКС Apollo DR"),
            "имплантация двухкамерного экс apollo dr",
        )

    def test_protocol_payload_contains_full_name_and_description(self):
        content = (
            "Операция: 125 Коронарография. Локальная эндоваскулярная "
            "трансартериальная тромбоаспирация из I ветки тупого края.\n"
            "Дата и время операции: 22.01.2026 10:30\n"
            "Ф.И.О. больного: Иванов Иван Иванович, возраст 55\n"
            "Описание операции: Под МИА 0,5% новокаина, в положении на спине "
            "выполнена пункция правой лучевой артерии по «Сельдингеру» "
            "с установкой интродьюсера 6F. "
            "В ходе исследования выявлено: выполнена тромбоаспирация "
            "из I ветки тупого края. Исход: удовлетворительный\n"
            "Опер.:_______Идрисов Р.Ш."
        )

        with patch(
            "hospital_agent.polling.protocols.read_docx_text",
            return_value=content,
        ):
            payload = parse_protocol(Path("protocol.docx"), "1")

        self.assertIsNotNone(payload)
        self.assertEqual(
            payload["name_operation"],
            "Коронарография. Локальная эндоваскулярная "
            "трансартериальная тромбоаспирация из I ветки тупого края",
        )
        self.assertEqual(
            payload["descr_operation"],
            "ЗАКЛЮЧЕНИЕ:\nВыполнена ТА из I ВТК.\n\n"
            "ХОД ОПЕРАЦИИ:\nДоступ: правой лучевой артерии, 6F.",
        )

    def test_protocol_accepts_new_operation_type_and_surgeon(self):
        content = (
            "Операция: 77 Имплантация двухкамерного ЭКС Apollo DR\n"
            "Дата и время операции: 22.01.2026 10:30\n"
            "Ф.И.О. больного: Иванов Иван Иванович, возраст 55\n"
            "Описание операции: Имплантирован двухкамерный ЭКС. "
            "Исход: удовлетворительный\n"
            "Опер.:_______Петров А.Б."
        )

        with patch(
            "hospital_agent.polling.protocols.read_docx_text",
            return_value=content,
        ):
            payload = parse_protocol(Path("protocol.docx"), "1")

        self.assertIsNotNone(payload)
        self.assertEqual(
            payload["study_type"],
            "имплантация двухкамерного экс apollo dr",
        )
        self.assertEqual(payload["surgeon"], "петров")

    def test_protocol_uses_placeholder_when_surgeon_is_missing(self):
        content = (
            "Операция: 738 Операционная №1. Коронарография\n"
            "Дата и время операции: 09.04.2026 14:35\n"
            "Ф.И.О. больного: Иванова Нина Алексеевна, возраст 75 лет\n"
            "Описание операции: выполнена селективная коронарография. "
            "Исход: удовлетворительный"
        )

        with patch(
            "hospital_agent.polling.protocols.read_docx_text",
            return_value=content,
        ):
            payload = parse_protocol(Path("protocol.docx"), "1")

        self.assertIsNotNone(payload)
        self.assertEqual(payload["surgeon"], "не указано")

    def test_empty_and_word_lock_files_are_not_discovered(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.docx").touch()
            (root / "~$locked.docx").write_bytes(b"temporary")
            expected = root / "operation.docx"
            expected.write_bytes(b"non-empty")

            self.assertEqual(iter_protocol_files([root]), [expected])

    def test_rejected_unchanged_protocol_is_not_reparsed_each_poll(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "invalid.docx"
            path.write_bytes(b"invalid but non-empty")
            config = SimpleNamespace(
                agent_id="2",
                state_file=root / "state.json",
            )
            polling = PollingConfig(
                state=True,
                interval_min=1,
                operations_dirs=[root],
            )
            state = AgentState()

            with patch(
                "hospital_agent.polling.protocols.parse_protocol",
                return_value=None,
            ) as parser:
                poll_operation_protocols(config, polling, object(), state)
                poll_operation_protocols(config, polling, object(), state)

            parser.assert_called_once_with(path, "2")

    def test_duplicate_operation_is_sent_only_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.docx"
            second = root / "copy.docx"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            config = SimpleNamespace(
                agent_id="2",
                state_file=root / "state.json",
            )
            polling = PollingConfig(
                state=True,
                interval_min=1,
                operations_dirs=[root],
            )
            state = AgentState()
            viewer = SimpleNamespace(post_json=MagicMock(return_value=True))
            payload = {
                "study_id": "217",
                "time_beginning": "2026-04-28T04:05:00Z",
                "patient": "Иванова Н.Г.",
            }

            with patch(
                "hospital_agent.polling.protocols.parse_protocol",
                return_value=payload,
            ):
                sent = poll_operation_protocols(
                    config,
                    polling,
                    viewer,
                    state,
                    now=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
                )

            self.assertEqual(sent, 1)
            viewer.post_json.assert_called_once_with("/studies", payload)
            self.assertEqual(len(state.processed_protocols), 2)
            self.assertEqual(len(state.processed_protocol_keys), 1)

    def test_polling_only_sends_protocols_from_current_monday(self):
        local_tz = timezone(timedelta(hours=7))
        now = datetime(2026, 7, 29, 12, 0, tzinfo=local_tz)
        payloads = {
            "old.docx": {
                "study_id": "1",
                "time_beginning": "2026-07-26T12:00:00+07:00",
                "patient": "Старый Пациент",
            },
            "monday.docx": {
                "study_id": "2",
                "time_beginning": "2026-07-27T00:00:00+07:00",
                "patient": "Текущий Пациент",
            },
            "future.docx": {
                "study_id": "3",
                "time_beginning": "2026-07-30T12:00:00+07:00",
                "patient": "Будущий Пациент",
            },
        }

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in payloads:
                (root / filename).write_bytes(filename.encode("ascii"))
            config = SimpleNamespace(agent_id="2", state_file=root / "state.json")
            polling = PollingConfig(state=True, interval_min=1, operations_dirs=[root])
            state = AgentState()
            viewer = SimpleNamespace(post_json=MagicMock(return_value=True))

            with patch(
                "hospital_agent.polling.protocols.parse_protocol",
                side_effect=lambda path, _agent_id: payloads[path.name],
            ) as parser:
                sent = poll_operation_protocols(
                    config,
                    polling,
                    viewer,
                    state,
                    now=now,
                )
                sent_again = poll_operation_protocols(
                    config,
                    polling,
                    viewer,
                    state,
                    now=now,
                )

            self.assertEqual(sent, 1)
            self.assertEqual(sent_again, 0)
            viewer.post_json.assert_called_once_with("/studies", payloads["monday.docx"])
            self.assertEqual(parser.call_count, 4)
            self.assertIn(str((root / "old.docx").resolve()), state.processed_protocols)
            self.assertIn(str((root / "monday.docx").resolve()), state.processed_protocols)
            self.assertNotIn(str((root / "future.docx").resolve()), state.processed_protocols)


if __name__ == "__main__":
    unittest.main()
