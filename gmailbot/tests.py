from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from gmailbot.oauth import (
    OAUTH_STATE_SALT,
    generate_auth_url,
    generate_oauth_state,
    parse_oauth_state,
)
from gmailbot.views import GmailOAuthCallbackView


class GmailOAuthStateTests(SimpleTestCase):
    def test_generate_oauth_state_signs_user_id(self):
        user = type('UserStub', (), {'id': 123})()

        state = generate_oauth_state(user)

        self.assertNotEqual(state, '123')
        self.assertEqual(parse_oauth_state(state), 123)
        with self.assertRaises(signing.BadSignature):
            parse_oauth_state('456')

    @override_settings(
        GOOGLE_CLIENT_ID='client-id',
        GOOGLE_CLIENT_SECRET='client-secret',
        GOOGLE_REDIRECT_URI='https://api.example.com/api/gmail/oauth/callback/',
    )
    def test_generate_auth_url_uses_signed_state(self):
        user = type('UserStub', (), {'id': 123})()

        auth_url = generate_auth_url(user)
        params = parse_qs(urlparse(auth_url).query)

        state = params['state'][0]
        self.assertNotEqual(state, '123')
        self.assertEqual(
            signing.loads(state, salt=OAUTH_STATE_SALT, max_age=15 * 60),
            123,
        )

    @override_settings(FRONTEND_URL='https://app.example.com')
    @patch('gmailbot.views.exchange_code')
    def test_callback_rejects_tampered_state_before_token_exchange(
        self, mock_exchange_code
    ):
        request = APIRequestFactory().get(
            '/api/gmail/oauth/callback/',
            {'code': 'auth-code', 'state': '123'},
        )

        response = GmailOAuthCallbackView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn('gmail=error', response.url)
        self.assertIn('reason=exchange_failed', response.url)
        mock_exchange_code.assert_not_called()
