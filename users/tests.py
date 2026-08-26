from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from users.models import User


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
