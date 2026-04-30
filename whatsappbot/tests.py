from django.test import TestCase, override_settings
from django.urls import reverse


class AdminApiKeyTests(TestCase):
    def test_default_admin_key_is_rejected_when_secret_is_not_configured(self):
        response = self.client.post(
            reverse('send_mass_message_api'),
            data='{"message": "hola", "dry_run": true}',
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer admin_secret_key',
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(ADMIN_API_KEY='configured-secret')
    def test_configured_admin_key_is_accepted(self):
        response = self.client.post(
            reverse('send_mass_message_api'),
            data='{"message": "hola", "dry_run": true}',
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer configured-secret',
        )

        # Auth succeeded; the request reaches endpoint validation/query logic.
        self.assertNotEqual(response.status_code, 401)
