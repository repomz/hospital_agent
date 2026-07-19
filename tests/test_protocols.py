import unittest

from hospital_agent.polling.protocols import classify_study_type, normalize_surgeon


class ProtocolMappingTests(unittest.TestCase):
    def test_study_type_classification(self):
        cases = {
            "КАГ": "каг",
            "ЦАГ": "цаг",
            "Стентирование ВСА слева": "стент_вса",
            "Стентирование коронарных артерий": "стент_кор",
            "Баллонная ангиопластика нижней конечности": "бап_периферии",
            "Тромбаспирация": "тромбаспирация",
        }
        for operation, expected in cases.items():
            with self.subTest(operation=operation):
                self.assertEqual(classify_study_type(operation), expected)

    def test_surgeon_is_normalized_to_backend_value(self):
        self.assertEqual(normalize_surgeon("Идрисов Р.Ш."), "идрисов")
        self.assertEqual(normalize_surgeon("неизвестный хирург"), "")


if __name__ == "__main__":
    unittest.main()
