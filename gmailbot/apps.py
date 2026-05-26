from django.apps import AppConfig


class GmailbotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gmailbot'
    verbose_name = 'Gmail Bot'

    def ready(self) -> None:
        """Registrar el handler Gmail en el registry de composio_integration.

        Se hace dentro de ``ready()`` (no a nivel modulo) para evitar
        circular imports: los modelos de Gmail/Composio se importan
        diferidos en el handler, no en este modulo.
        """
        try:
            from composio_integration.registry import register_toolkit

            from gmailbot.composio_handler import GmailComposioHandler

            register_toolkit("gmail", GmailComposioHandler())
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to register Gmail Composio handler"
            )

        # Importar signals (pre_delete cleanup en Composio)
        try:
            import gmailbot.signals  # noqa: F401
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to import gmailbot.signals"
            )
