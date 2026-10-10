import unittest

from hospital_agent.clinical_terms import (
    normalize_terms,
    operation_type,
    performed_assist_option,
    performed_ivus,
)


class ClinicalTermsTests(unittest.TestCase):
    def test_counterpulsation_spelling_variants(self):
        for spelling in ("контрпульсация", "контрапульсация", "контропульсация"):
            self.assertTrue(performed_assist_option("", "Выполнена " + spelling, "vabk"))

    def test_ivus_variants(self):
        for value in (
            "ВСУЗИ",
            "внутрисосудистое исследование",
            "внутрисосудистая визуализация",
            "внутри сосудистое ультразвуковое исследование",
            "внутрисосудистое иследование",
            "ivus",
        ):
            self.assertTrue(performed_ivus("КАГ", "Выполнено " + value), value)
        for value in (
            "ВСУЗИ не выполнено",
            "рекомендовано ВСУЗИ",
            "ранее выполнено ВСУЗИ",
            "без ВСУЗИ",
        ):
            self.assertFalse(performed_ivus("КАГ", value), value)

    def test_types(self):
        cases = {
            "АГ + стент подключичной справа": "стент_вк",
            "ЦАГ + ТА + стент ВСА": "инсульт",
            "БАП фистулы": "бап_фистулы",
            "Смена ЭКС 460DR": "ЭКС 2к",
            "ЭМА": "эма",
            "КАГ + ВСУЗИ": "каг",
        }
        for name, expected in cases.items():
            self.assertEqual(operation_type(name), expected, name)

    def test_corrected_spelling(self):
        self.assertEqual(normalize_terms("стентирвоание аретрии"), "стентирование артерии")

    def test_superficial_femoral_is_not_upper_limb(self):
        self.assertEqual(operation_type("АГ + стент поверхностной бедренной"), "стент_нк")
        self.assertEqual(
            operation_type("Окклюзия полости аневризмы ВСА микроспиралями"), "аневризма"
        )
