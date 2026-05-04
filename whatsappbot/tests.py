from unittest.mock import Mock

from django.test import SimpleTestCase

from whatsappbot.bot import _resolve_gmail_categorization_target


class GmailCategorizationTargetTests(SimpleTestCase):
    def test_swipe_reply_without_match_does_not_fall_back_to_latest_pending(self):
        user = object()
        latest_pending = object()
        find_by_notification = Mock(return_value=None)
        check_pending = Mock(return_value=latest_pending)
        looks_like_categorization = Mock(return_value=True)

        target = _resolve_gmail_categorization_target(
            user,
            "wamid.missing",
            "Comida",
            find_by_notification,
            check_pending,
            looks_like_categorization,
        )

        self.assertIsNone(target)
        find_by_notification.assert_called_once_with(user, "wamid.missing")
        check_pending.assert_not_called()
        looks_like_categorization.assert_not_called()

    def test_swipe_reply_uses_matched_notification_even_when_pending_exists(self):
        user = object()
        matched_email = object()
        find_by_notification = Mock(return_value=matched_email)
        check_pending = Mock(return_value=object())
        looks_like_categorization = Mock(return_value=True)

        target = _resolve_gmail_categorization_target(
            user,
            "wamid.match",
            "Comida",
            find_by_notification,
            check_pending,
            looks_like_categorization,
        )

        self.assertIs(target, matched_email)
        find_by_notification.assert_called_once_with(user, "wamid.match")
        check_pending.assert_not_called()
        looks_like_categorization.assert_not_called()

    def test_plain_text_category_can_use_latest_pending_email(self):
        user = object()
        latest_pending = object()
        find_by_notification = Mock()
        check_pending = Mock(return_value=latest_pending)
        looks_like_categorization = Mock(return_value=True)

        target = _resolve_gmail_categorization_target(
            user,
            None,
            "Comida",
            find_by_notification,
            check_pending,
            looks_like_categorization,
        )

        self.assertIs(target, latest_pending)
        find_by_notification.assert_not_called()
        check_pending.assert_called_once_with(user)
        looks_like_categorization.assert_called_once_with("Comida")
