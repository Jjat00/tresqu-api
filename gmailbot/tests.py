from types import SimpleNamespace

from django.core import signing
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from .oauth import generate_oauth_state, parse_oauth_state
from .views import GmailOAuthCallbackView


@override_settings(
    SECRET_KEY='test-secret-key',
    FRONTEND_URL='https://app.example.com',
)
class GmailOAuthStateTests(SimpleTestCase):
    def test_oauth_state_round_trips_signed_user_id(self):
        user = SimpleNamespace(id=123)

        state = generate_oauth_state(user)

        self.assertNotEqual(state, str(user.id))
        self.assertEqual(parse_oauth_state(state), user.id)

    def test_oauth_state_rejects_tampered_value(self):
        user = SimpleNamespace(id=123)
        state = generate_oauth_state(user)
        tampered_state = state[:-1] + ('a' if state[-1] != 'a' else 'b')

        with self.assertRaises(signing.BadSignature):
            parse_oauth_state(tampered_state)

    def test_oauth_callback_rejects_legacy_numeric_state(self):
        request = APIRequestFactory().get(
            '/api/gmail/oauth/callback/',
            {'code': 'auth-code', 'state': '123'},
        )

        response = GmailOAuthCallbackView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn('gmail=error', response.url)
        self.assertIn('reason=invalid_state', response.url)
