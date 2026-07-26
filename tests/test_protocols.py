import unittest
from pathlib import Path
from unittest.mock import patch

from hospital_agent.polling.protocols import (
    classify_study_type,
    normalize_surgeon,
    parse_protocol,
)


class ProtocolMappingTests(unittest.TestCase):
    def test_study_type_classification(self):
        cases = {
            "КАГ": "каг",
            "ЦАГ": "цаг",
            "Стентирование ВСА слева": "стент_вса",
            "Стентирование коронарных артерий": "стент_кор",
            "Баллонная ангиопластика нижней конечности": "бап_периферии",
            "АГ балонная ангилопластика задней большеберцовой": "бап_периферии",
            "Тромбаспирация": "тромбаспирация",
            "ТА": "тромбаспирация",
        }
        for operation, expected in cases.items():
            with self.subTest(operation=operation):
                self.assertEqual(classify_study_type(operation), expected)

    def test_surgeon_is_normalized_to_backend_value(self):
        self.assertEqual(normalize_surgeon("Идрисов Р.Ш."), "идрисов")
        self.assertEqual(normalize_surgeon("неизвестный хирург"), "")

    def test_protocol_payload_contains_compact_name_and_description(self):
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
        self.assertEqual(payload["name_operation"], "КАГ. ТА I ВТК.")
        self.assertEqual(
            payload["descr_operation"],
            "Доступ: правой лучевой артерии, 6F. выполнена ТА из I ВТК.",
        )


if __name__ == "__main__":
    unittest.main()
