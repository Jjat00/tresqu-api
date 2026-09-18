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


class IncomeCategoryApiTests(TransactionTestCase):
    """La web debe poder crear/editar/borrar categorías de ingresos."""

    def setUp(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        self.user = User.objects.create(
            username="cat-api",
            platform="web",
            external_id="cat-api-ext",
            default_currency="COP",
        )
        # El proyecto no usa AUTH_USER_MODEL: la autenticación real pasa por
        # CustomJWTAuthentication, así que el test usa un JWT de verdad en
        # vez de force_authenticate (que dejaría el User sin is_authenticated).
        refresh = RefreshToken()
        refresh["user_id"] = self.user.id
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_crea_categoria_de_ingreso(self):
        response = self.client.post(
            "/api/categories/incomes/",
            {
                "name": "clases particulares",
                "description": "Tutorías por hora",
                "example": "Clase de matemáticas",
                "color": "#123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["name"], "Clases Particulares")
        self.assertFalse(response.data["is_default"])
        self.assertTrue(
            UserIncomeCategory.objects.filter(
                user=self.user, name="Clases Particulares").exists()
        )

    def test_rechaza_duplicado_de_predefinida(self):
        response = self.client.post(
            "/api/categories/incomes/",
            {"name": "salario o trabajo fijo"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_lista_solo_las_del_usuario(self):
        otro = User.objects.create(
            username="otro", platform="web", external_id="otro-ext")
        UserIncomeCategory.objects.create(user=otro, name="Ajena")

        response = self.client.get("/api/categories/incomes/")

        self.assertEqual(response.status_code, 200)
        nombres = [c["name"] for c in response.data]
        self.assertNotIn("Ajena", nombres)
        self.assertIn("Salario o Trabajo Fijo", nombres)

    def test_edita_y_elimina_categoria_personalizada(self):
        creada = self.client.post(
            "/api/categories/incomes/", {"name": "Rifas"}, format="json"
        ).data

        editada = self.client.patch(
            f"/api/categories/incomes/{creada['id']}/",
            {"color": "#00FF00"},
            format="json",
        )
        self.assertEqual(editada.status_code, 200)
        self.assertEqual(editada.data["color"], "#00FF00")

        borrada = self.client.delete(f"/api/categories/incomes/{creada['id']}/")
        self.assertEqual(borrada.status_code, 204)
        self.assertFalse(
            UserIncomeCategory.objects.filter(id=creada["id"]).exists())

    def test_endpoint_custom_solo_devuelve_personalizadas(self):
        self.client.post(
            "/api/categories/incomes/", {"name": "Rifas"}, format="json")

        response = self.client.get("/api/categories/incomes/custom/")

        self.assertEqual(response.status_code, 200)
        nombres = [c["name"] for c in response.data["categories"]]
        self.assertEqual(nombres, ["Rifas"])
