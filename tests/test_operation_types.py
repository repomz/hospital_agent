import unittest

from hospital_agent.clinical_terms import operation_type


class OperationTypesTests(unittest.TestCase):
    def test_coronary_priority(self):
        self.assertEqual(operation_type("КАГ, ТА и стент ПКА"), "стент_кор")
        self.assertEqual(operation_type("БАП, ТА и стент I ДА"), "стент_кор")
        self.assertEqual(operation_type("КАГ, ТА и БАП ПКА"), "бап_кор")

    def test_attempts_and_actual_results(self):
        cases = [
            ("КАГ. поп. стент ПКА", "Проведение стента безуспешно.", "каг"),
            (
                "КАГ, БАП ОА, поп. стент ОА",
                "Выполнена БАП ОА. Завести стент не удалось.",
                "бап_кор",
            ),
            (
                "поп. МР с БАП и стент ПКА",
                "Выполнена попытка БАП. Проведение баллонного катетера невозможно.",
                "каг",
            ),
            (
                "КАГ, попытка стентирования ПКА, стент ПНА",
                "Стент ПНА имплантирован. Стент ПКА провести не удалось.",
                "стент_кор",
            ),
            ("КАГ, стент ПКА", "Имплантация стента 3,0 х 24 мм безуспешна.", "каг"),
            ("КАГ", "Ранее выполнено стентирование ПКА. Стент проходим.", "каг"),
            ("КАГ", "После стентирования остаточный стеноз 0%.", "каг"),
            ("КАГ. ТА из стента ПНА", "После стентирования остаточный стеноз 0%.", "каг"),
        ]
        for name, description, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(operation_type(name, description), expected)

    def test_all_cerebral_arteries_and_mixed_territories(self):
        for artery in [
            "М1",
            "М2",
            "М 1-2",
            "ПМА М1-М2",
            "основной артерии",
            "базилярной",
            "БА",
            "позвоночной артерии",
            "ПА",
            "ВСА",
            "ОСА",
        ]:
            for action in ["ТА", "ТЭ"]:
                self.assertEqual(
                    operation_type(f"{action} из {artery}, БАП и стент НПА"), "инсульт"
                )

    def test_carotid_and_vertebral_stents(self):
        self.assertEqual(operation_type("ЦАГ. Стент ОСА"), "стент_вса")
        self.assertEqual(operation_type("КАГ. Стент ОСА"), "стент_вса")
        self.assertEqual(operation_type("Стент позвоночной артерии"), "стент_па")
        self.assertEqual(operation_type("АГ ПА. Стент устья ПА"), "стент_па")
        self.assertEqual(operation_type("ЦАГ", "ВСА после стентирования проходима."), "цаг")

    def test_pacing_and_additional_types(self):
        for name in [
            "Временный однокамерный ЭКС",
            "Установка ВЭКС",
            "Установка временного ЭКС, КАГ",
        ]:
            self.assertEqual(operation_type(name), "ВЭКС")
        self.assertEqual(operation_type("Имплантация ЭКС SR"), "ЭКС 1к")
        self.assertEqual(operation_type("Смена ЭКС DR"), "ЭКС 2к")
        self.assertEqual(operation_type("Ревизия ложа ЭКС"), "ЭКС ревизия")
        self.assertEqual(operation_type("Установка ВАБК"), "ВАБК")
        self.assertEqual(operation_type("Каротидография"), "цаг")
