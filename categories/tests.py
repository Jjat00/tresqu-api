"""Tests de normalización y resolución de categorías por usuario.

Cubren el bug por el que TODO ingreso registrado por chat caía en
"Otros Ingresos": ``create_income`` buscaba la categoría con un match exacto
sobre un nombre pasado por ``str.title()``, que mayusculiza los conectores
("Salario o Trabajo Fijo" → "Salario O Trabajo Fijo") y por tanto nunca
coincidía con las categorías predefinidas.
"""

from django.test import TransactionTestCase

from categories.models import UserExpenseCategory, UserIncomeCategory
from categories.utils import (
    get_or_create_user_income_category,
    normalize_category_name,
)
from income.models import Income
from users.models import User


class NormalizeCategoryNameTests(TransactionTestCase):
    def test_respeta_conectores_en_minuscula(self):
        self.assertEqual(
            normalize_category_name("salario o trabajo fijo"),
            "Salario o Trabajo Fijo",
        )
        self.assertEqual(
            normalize_category_name("REGALÍAS Y DERECHOS"),
            "Regalías y Derechos",
        )
        self.assertEqual(
            normalize_category_name("  venta   de bienes "),
            "Venta de Bienes",
        )

    def test_primera_palabra_siempre_capitaliza(self):
        self.assertEqual(normalize_category_name("el arriendo"), "El Arriendo")

    def test_coincide_con_las_predefinidas(self):
        for name in UserIncomeCategory.PREDEFINED_CATEGORIES:
            self.assertEqual(normalize_category_name(name), name)
        for name in UserExpenseCategory.PREDEFINED_CATEGORIES:
            self.assertEqual(normalize_category_name(name), name)


class CreateIncomeCategoryResolutionTests(TransactionTestCase):
    """El ingreso creado por el agente debe caer en la categoría que dijo."""

    def setUp(self):
        self.user = User.objects.create(
            username="cat-tester",
            platform="whatsapp",
            external_id="cat-tester-ext",
            default_currency="COP",
        )

    def _create_income(self, category: str) -> Income:
        from telegrambot.tools import create_income

        create_income.invoke({
            "user_external_id": self.user.external_id,
            "amount": 1000.0,
            "currency": "COP",
            "category": category,
            "note": "test",
        })
        return Income.objects.filter(user=self.user).order_by("-id").first()

    def test_categoria_predefinida_con_conector(self):
        income = self._create_income("Salario o Trabajo Fijo")

        self.assertIsNotNone(income)
        self.assertEqual(
            income.user_income_category.name, "Salario o Trabajo Fijo")
        self.assertTrue(income.user_income_category.is_default)

    def test_categoria_en_minusculas_reutiliza_la_existente(self):
        income = self._create_income("trabajo independiente o freelance")

        self.assertEqual(
            income.user_income_category.name,
            "Trabajo Independiente o Freelance",
        )
        # No debe haber creado un duplicado con otra capitalización.
        self.assertEqual(
            UserIncomeCategory.objects.filter(
                user=self.user,
                name__iexact="Trabajo Independiente o Freelance",
            ).count(),
            1,
        )

    def test_categoria_nueva_se_crea_con_nombre_normalizado(self):
        income = self._create_income("ventas de ropa")

        self.assertEqual(income.user_income_category.name, "Ventas de Ropa")
        self.assertFalse(income.user_income_category.is_default)

    def test_no_cae_en_otros_ingresos(self):
        income = self._create_income("Regalías y Derechos")

        self.assertNotEqual(
            income.user_income_category.name, "Otros Ingresos")
        self.assertEqual(income.category_str, "Regalías y Derechos")


class GetOrCreateUserIncomeCategoryTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="cat-utils",
            platform="whatsapp",
            external_id="cat-utils-ext",
            default_currency="COP",
        )

    def test_no_duplica_por_capitalizacion(self):
        category, created = get_or_create_user_income_category(
            self.user, "apoyos o subsidios")

        self.assertFalse(created)
        self.assertEqual(category.name, "Apoyos o Subsidios")

    def test_crea_con_metadatos(self):
        category, created = get_or_create_user_income_category(
            self.user,
            "consultorías express",
            description="Trabajos cortos de asesoría",
            example="Asesoría puntual de 2 horas",
            color="#123456",
        )

        self.assertTrue(created)
        self.assertEqual(category.name, "Consultorías Express")
        self.assertEqual(category.color, "#123456")
        self.assertEqual(category.description, "Trabajos cortos de asesoría")
