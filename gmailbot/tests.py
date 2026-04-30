from unittest.mock import patch

from django.test import SimpleTestCase

from .email_processor import process_history_update
from .gmail_service import StaleGmailHistoryError


class GmailHistoryProcessingTests(SimpleTestCase):
    def test_stale_history_does_not_advance_saved_history_id(self):
        class Watch:
            history_id = 'old-history-id'
            save_called = False

            def save(self, *args, **kwargs):
                self.save_called = True

        class GoogleAccount:
            google_email = 'gmail-user@gmail.com'
            watch = Watch()

        google_account = GoogleAccount()

        with patch(
            'gmailbot.email_processor.get_history',
            side_effect=StaleGmailHistoryError('history too old'),
        ):
            process_history_update(google_account, 'new-history-id')

        self.assertEqual(google_account.watch.history_id, 'old-history-id')
        self.assertFalse(google_account.watch.save_called)
