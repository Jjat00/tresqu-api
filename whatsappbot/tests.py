from django.test import SimpleTestCase, override_settings

from .views import _is_valid_admin_api_key


class AdminApiKeyTests(SimpleTestCase):
    def test_default_admin_key_is_rejected_when_secret_is_not_configured(self):
        response = self.client.post(
            '/whatsapp/send-mass-message/',
            data='{"message": "hola", "dry_run": true}',
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer admin_secret_key',
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(ADMIN_API_KEY='configured-secret')
    def test_configured_admin_key_is_accepted(self):
        self.assertTrue(_is_valid_admin_api_key('configured-secret'))
