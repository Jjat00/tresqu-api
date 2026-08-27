from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from users.models import MonthlyUsage, SubscriptionPlan, User
from users.plan_limits import get_max_expenses, get_max_incomes, is_unlimited_plan


class TokenRefreshTests(TransactionTestCase):
    """`/api/token/refresh/` debe resolver el usuario contra `users.User`,
    no contra `auth_user` (que es donde miraba la vista de serie de simplejwt).

    TransactionTestCase y no TestCase: `DatabaseConnectionMiddleware` cierra la
    conexión tras cada petición a /api/, lo que rompe la transacción que
    envuelve a un TestCase normal."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("token_refresh")
        self.user = User.objects.create(
            external_id="573001112233", platform="whatsapp", first_name="Jaime"
        )

    def test_refresh_emite_access_y_rota_el_refresh(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertNotEqual(response.data["refresh"], str(refresh))
        # El access nuevo sigue apuntando al mismo users.User.
        self.assertEqual(str(AccessToken(response.data["access"])["user_id"]), str(self.user.id))

    def test_refresh_de_usuario_inexistente_responde_401(self):
        refresh = RefreshToken.for_user(self.user)
        self.user.delete()

        response = self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(response.data["code"], "user_not_found")

    def test_refresh_con_token_basura_responde_401(self):
        response = self.client.post(self.url, {"refresh": "abc.def.ghi"}, format="json")

        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(response.data["code"], "token_not_valid")


class PlanLimitsTests(TestCase):
    """Límites mensuales por plan: Basic tope de gastos configurable (mínimo 100
    por defecto), Premium sin tope aunque `unlimited_records` esté apagado."""

    def _user(self, plan):
        user = User.objects.create(
            external_id=f"57300{plan.name.lower()}", platform="whatsapp", first_name="Jaime"
        )
        user.subscription_plan = plan
        user.subscription_active = True
        user.save()
        return user

    def test_basic_ofrece_al_menos_100_gastos_por_defecto(self):
        self.assertGreaterEqual(
            settings.BASIC_PLAN_MAX_EXPENSES, 100,
            "El plan Basic debe permitir al menos 100 gastos al mes (BASIC_PLAN_MAX_EXPENSES)",
        )
        self.assertEqual(get_max_expenses("BASIC"), settings.BASIC_PLAN_MAX_EXPENSES)
        self.assertEqual(get_max_incomes("BASIC"), settings.BASIC_PLAN_MAX_INCOMES)

    def test_basic_bloquea_al_llegar_al_tope_de_gastos(self):
        user = self._user(SubscriptionPlan.get_basic_plan())
        usage = MonthlyUsage.get_current_usage(user)

        usage.expenses_count = settings.BASIC_PLAN_MAX_EXPENSES - 1
        usage.save()
        can_add, _ = user.can_add_expense()
        self.assertTrue(can_add)

        usage.expenses_count = settings.BASIC_PLAN_MAX_EXPENSES
        usage.save()
        can_add, message = user.can_add_expense()
        self.assertFalse(can_add)
        self.assertIn(str(settings.BASIC_PLAN_MAX_EXPENSES), message)

    def test_premium_no_tiene_tope(self):
        self.assertIsNone(get_max_expenses("PREMIUM"))
        self.assertIsNone(get_max_incomes("PREMIUM"))
        self.assertTrue(is_unlimited_plan("PREMIUM"))

        plan = SubscriptionPlan.get_premium_plan()
        plan.unlimited_records = False  # incluso sin el flag, el plan no limita
        plan.save()
        user = self._user(plan)
        usage = MonthlyUsage.get_current_usage(user)
        usage.expenses_count = 10_000
        usage.incomes_count = 10_000
        usage.save()

        self.assertEqual(user.can_add_expense(), (True, ""))
        self.assertEqual(user.can_add_income(), (True, ""))
