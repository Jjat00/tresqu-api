from unittest.mock import patch

from django.test import TestCase

from users.models import User

from .email_processor import process_history_update
from .gmail_service import StaleGmailHistoryError
from .models import GmailWatch, GoogleAccount


class GmailHistoryProcessingTests(TestCase):
    def test_stale_history_does_not_advance_saved_history_id(self):
        user = User.objects.create(
            external_id='gmail-user',
            platform='WHATSAPP',
            username='gmail-user',
        )
        google_account = GoogleAccount.objects.create(
            user=user,
            google_email='gmail-user@gmail.com',
            access_token_encrypted=b'access-token',
            refresh_token_encrypted=b'refresh-token',
        )
        watch = GmailWatch.objects.create(
            google_account=google_account,
            history_id='old-history-id',
            is_active=True,
        )

        with patch(
            'gmailbot.email_processor.get_history',
            side_effect=StaleGmailHistoryError('history too old'),
        ):
            process_history_update(google_account, 'new-history-id')

        watch.refresh_from_db()
        self.assertEqual(watch.history_id, 'old-history-id')
