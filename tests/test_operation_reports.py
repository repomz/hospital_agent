import unittest
from datetime import datetime

from hospital_agent.services.operation_reports import (
    parse_birth_date_from_content,
    parse_operation_datetime,
    parse_recommendation,
)


class OperationReportParsingTests(unittest.TestCase):
    def test_operation_datetime_allows_spaces_around_separator(self):
        content = "Дата и время операции: 20 .04.2026 23:30"

        self.assertEqual(
            parse_operation_datetime(content),
            datetime(2026, 4, 20, 23, 30),
        )

    def test_operation_date_is_not_used_as_birth_date(self):
        content = (
            "Дата и время операции: 21.04.2026 21:45\n"
            "Ф.И.О. больного: Иванов Иван Иванович, возраст 37"
        )

        self.assertEqual(parse_birth_date_from_content(content), "")

    def test_birth_date_is_read_from_patient_line(self):
        content = (
            "Дата и время операции: 21.04.2026 21:45\n"
            "Ф.И.О. больного: Иванов Иван Иванович, "
            "дата рождения 02.03.1989, возраст 37"
        )

        self.assertEqual(parse_birth_date_from_content(content), "02.03.1989")

    def test_full_recommendation_label_is_supported(self):
        content = (
            "Рекомендовано: наблюдение дежурного врача "
            "Расходные материалы Йодсодержащий контраст"
        )

        self.assertEqual(parse_recommendation(content), "наблюдение дежурного врача")


if __name__ == "__main__":
    unittest.main()
