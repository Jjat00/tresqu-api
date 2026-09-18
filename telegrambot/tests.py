from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase, TransactionTestCase, override_settings
from telegram import Bot, Update
from telegram.ext import ConversationHandler, MessageHandler

from expenses.models import Expense
from income.models import Income
from telegrambot import bot
from telegrambot.tools import get_expense_totals, get_expenses_by_user, get_income_totals
from users.models import Chat, TrackingLink, User


def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=dt_timezone.utc)


class ExpenseQueryToolsTests(TestCase):
    """Las tools de consulta del agente deben responder lo mismo que el dashboard:
    filtro por fecha del gasto (spent_at) y totales por moneda calculados en BD."""

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001110000", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        rows = [
            (Decimal("1000"), "COP", date(2026, 7, 31)),   # fuera del período
            (Decimal("2500"), "COP", date(2026, 8, 1)),
            (Decimal("4000"), "COP", date(2026, 8, 15)),
            (Decimal("10.50"), "USD", date(2026, 8, 15)),
            (Decimal("7000"), "COP", date(2026, 8, 28)),   # fuera del período
        ]
        for amount, currency, spent in rows:
            Expense.objects.create(
                user=self.user, amount=amount, currency=currency,
                description="x", timestamp=_ts(spent), spent_at=spent,
            )
        Income.objects.create(
            user=self.user, amount=Decimal("900000"), currency="COP",
            description="salario", timestamp=_ts(date(2026, 8, 5)), received_at=date(2026, 8, 5),
        )

    def _call(self, tool, **kwargs):
        return tool.invoke({"user_external_id": self.user.external_id, **kwargs})

    def test_totales_de_gastos_por_moneda_en_el_periodo(self):
        result = self._call(get_expense_totals, start_date="2026-08-01", end_date="2026-08-27")
        by_currency = {t["currency"]: t for t in result["totals"]}
        self.assertEqual(by_currency["COP"], {"currency": "COP", "total": 6500.0, "count": 2})
        self.assertEqual(by_currency["USD"], {"currency": "USD", "total": 10.5, "count": 1})

    def test_totales_sin_fechas_es_el_historico(self):
        result = self._call(get_expense_totals)
        by_currency = {t["currency"]: t["total"] for t in result["totals"]}
        self.assertEqual(by_currency["COP"], 14500.0)

    def test_totales_de_ingresos(self):
        result = self._call(get_income_totals, start_date="2026-08-01", end_date="2026-08-31")
        self.assertEqual(result["totals"], [{"currency": "COP", "total": 900000.0, "count": 1}])

    def test_listar_gastos_respeta_el_rango_de_fechas(self):
        rows = self._call(get_expenses_by_user, start_date="2026-08-01", end_date="2026-08-27")
        self.assertEqual([r["spent_at"] for r in rows], ["2026-08-15", "2026-08-15", "2026-08-01"])

    def test_listar_gastos_sin_rango_devuelve_todo_con_tope(self):
        self.assertEqual(len(self._call(get_expenses_by_user)), 5)
        self.assertEqual(len(self._call(get_expenses_by_user, limit=2)), 2)

    def test_usuario_inexistente(self):
        self.assertEqual(get_expense_totals.invoke({"user_external_id": "nadie"}), {"error": "Usuario no encontrado"})


class TelegramRegistrationTests(TransactionTestCase):
    """El alta por Telegram tiene que resolverse dentro de un solo update.

    El webhook construye una Application nueva por cada mensaje, así que ningún
    paso del registro puede depender de algo recordado entre updates: si vuelve
    a hacerlo, quien comparte su contacto se queda sin cuenta (el bucle
    "No se encontró cuenta existente" -> "usa /registrar" de agosto de 2026).
    """

    def _update_con_contacto(self, phone="+56942479733", telegram_id=8926044722,
                             contact_owner=None, first_name="Matias"):
        """Update mínimo con un contacto compartido."""
        contact_owner = telegram_id if contact_owner is None else contact_owner

        mensaje = SimpleNamespace(
            message_id=3779,
            text=None,
            contact=SimpleNamespace(user_id=contact_owner, phone_number=phone),
            reply_text=AsyncMock(),
        )
        return SimpleNamespace(
            effective_chat=SimpleNamespace(id=telegram_id),
            effective_user=SimpleNamespace(
                id=telegram_id, first_name=first_name, username="matias"),
            message=mensaje,
        )

    def _update_con_callback(self, data, telegram_id=8926044722):
        """Update mínimo con la pulsación de un botón inline."""
        return SimpleNamespace(
            effective_chat=SimpleNamespace(id=telegram_id),
            effective_user=SimpleNamespace(
                id=telegram_id, first_name="Matias", username="matias"),
            callback_query=SimpleNamespace(
                data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
        )

    def _contexto(self, args=None):
        return SimpleNamespace(args=args or [], user_data={}, bot=AsyncMock())

    def _respuestas(self, update):
        return [llamada.args[0] for llamada in update.message.reply_text.call_args_list]

    def test_contacto_de_alguien_sin_cuenta_crea_la_cuenta(self):
        update = self._update_con_contacto()

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        user = User.objects.get(external_id="8926044722")
        self.assertEqual(user.phone_number, "56942479733")
        self.assertEqual(user.default_currency, "CLP")      # deducido del prefijo
        self.assertEqual(user.timezone, "America/Santiago")
        self.assertEqual(
            Chat.objects.get(platform="TELEGRAM",
                             platform_chat_id="8926044722").user_id,
            user.id,
        )

        respuestas = " ".join(self._respuestas(update))
        self.assertIn("cuenta ya está creada", respuestas)
        self.assertNotIn("/registrar", respuestas)

    def test_contacto_de_alguien_con_cuenta_vincula_sin_duplicar(self):
        existente = User.objects.create(
            external_id="56942479733", platform="whatsapp",
            phone_number="56942479733", default_currency="CLP",
        )
        update = self._update_con_contacto()

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            Chat.objects.get(platform_chat_id="8926044722").user_id, existente.id)
        self.assertIn("vinculada", " ".join(self._respuestas(update)))

    def test_cuenta_mexicana_de_whatsapp_se_vincula_aunque_falte_el_1(self):
        """El caso real del 17-09: alguien con cuenta de WhatsApp en México.

        WhatsApp guarda el móvil mexicano como "521..." y el contacto de
        Telegram llega como "52...". Buscando solo por la forma exacta se le
        crearía una segunda cuenta y sus gastos quedarían partidos en dos.
        """
        de_whatsapp = User.objects.create(
            external_id="wa_5214812413697", platform="WHATSAPP",
            phone_number="5214812413697", default_currency="MXN",
        )
        update = self._update_con_contacto(phone="+524812413697")

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            Chat.objects.get(platform_chat_id="8926044722").user_id, de_whatsapp.id)
        self.assertIn("vinculada", " ".join(self._respuestas(update)))

    def test_cuenta_antigua_guardada_sin_el_1_tambien_se_encuentra(self):
        """Las cuentas mexicanas creadas antes (sin el "1") siguen siendo suyas."""
        antigua = User.objects.create(
            external_id="52481241369", platform="telegram",
            phone_number="524812413697", default_currency="MXN",
        )
        update = self._update_con_contacto(phone="+5214812413697")

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            Chat.objects.get(platform_chat_id="8926044722").user_id, antigua.id)

    def test_contacto_ajeno_no_crea_cuenta(self):
        update = self._update_con_contacto(contact_owner=111222333)

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        self.assertFalse(User.objects.exists())
        self.assertIn("no es el tuyo", " ".join(self._respuestas(update)))

    def test_cuenta_a_medias_del_mismo_telegram_se_completa(self):
        a_medias = User.objects.create(
            external_id="8926044722", platform="telegram", phone_number=None)
        update = self._update_con_contacto()

        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        a_medias.refresh_from_db()
        self.assertEqual(User.objects.count(), 1)          # no IntegrityError
        self.assertEqual(a_medias.phone_number, "56942479733")

    def test_los_botones_ajustan_moneda_y_zona_horaria(self):
        async_to_sync(bot.handle_contact_shared)(
            self._update_con_contacto(), self._contexto())

        async_to_sync(bot.handle_currency_callback)(
            self._update_con_callback("cur:USD"), self._contexto())
        async_to_sync(bot.handle_timezone_selection)(
            self._update_con_callback("tz:America/Bogota"), self._contexto())

        user = User.objects.get(external_id="8926044722")
        self.assertEqual(user.default_currency, "USD")
        self.assertEqual(user.timezone, "America/Bogota")

    def test_comando_moneda_con_codigo_aplica_directo(self):
        async_to_sync(bot.handle_contact_shared)(
            self._update_con_contacto(), self._contexto())

        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=8926044722),
            effective_user=SimpleNamespace(
                id=8926044722, first_name="Matias", username="matias"),
            message=SimpleNamespace(
                message_id=4000, text="/moneda EUR", reply_text=AsyncMock()),
        )

        async_to_sync(bot.currency_command)(update, self._contexto(args=["eur"]))

        self.assertEqual(
            User.objects.get(external_id="8926044722").default_currency, "EUR")

    @override_settings(CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_el_referido_sobrevive_entre_updates(self):
        enlace = TrackingLink.objects.create(code="empresa_abc", name="Empresa ABC")

        # /start llega en un update y el contacto en otro: la Application ya no
        # es la misma, así que el referido no puede vivir en context.user_data.
        async_to_sync(bot.remember_tracking_link_async)(8926044722, enlace.id)
        async_to_sync(bot.handle_contact_shared)(
            self._update_con_contacto(), self._contexto())

        self.assertEqual(
            User.objects.get(external_id="8926044722").source_tracking_link_id,
            enlace.id,
        )

    def test_ningun_paso_del_registro_depende_de_una_conversacion(self):
        """Candado de regresión: el alta no puede volver a un ConversationHandler."""
        with override_settings(TELEGRAM_BOT_TOKEN="123456:TESTTOKEN"):
            application = bot.setup_bot()

        handlers = application.handlers[0]
        conversacionales = [h for h in handlers if isinstance(h, ConversationHandler)]
        comandos_conversacionales = {
            cmd
            for h in conversacionales
            for entry in h.entry_points
            for cmd in getattr(entry, "commands", [])
        }
        self.assertNotIn("registrar", comandos_conversacionales)
        self.assertNotIn("timezone", comandos_conversacionales)

        # Y el contacto tiene que tener su propio handler global
        self.assertTrue(any(
            isinstance(h, MessageHandler) and h.callback is bot.handle_contact_shared
            for h in handlers
        ))

    def test_cuenta_del_chat_sin_telefono_se_completa_y_no_se_duplica(self):
        """Sin esto, "falta tu teléfono" y /registrar se remitían el uno al otro."""
        cuenta_web = User.objects.create(
            external_id="web-1234", platform="web", phone_number=None,
            default_currency="CLP",
        )
        Chat.objects.create(
            platform="TELEGRAM", platform_chat_id="8926044722", user=cuenta_web)

        update = self._update_con_contacto()
        async_to_sync(bot.handle_contact_shared)(update, self._contexto())

        cuenta_web.refresh_from_db()
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(cuenta_web.phone_number, "56942479733")
        self.assertEqual(
            Chat.objects.get(platform_chat_id="8926044722").user_id, cuenta_web.id)

    def _primer_handler(self, application, update):
        """Reproduce el despacho de PTB: gana el primer handler que acepta el update."""
        for grupo in sorted(application.handlers):
            for handler in application.handlers[grupo]:
                comprobacion = handler.check_update(update)
                if comprobacion is not None and comprobacion is not False:
                    return handler
        return None

    def test_el_contacto_y_los_botones_llegan_a_su_handler(self):
        """El bug de producción fue de ruteo: el contacto lo atrapaba el handler
        equivocado porque el ConversationHandler había perdido su estado."""
        with override_settings(TELEGRAM_BOT_TOKEN="123456:TESTTOKEN"):
            application = bot.setup_bot()

        api_bot = Bot(token="123456:TESTTOKEN")
        quien = {"id": 8926044722, "is_bot": False, "first_name": "Matias"}
        chat = {"id": 8926044722, "first_name": "Matias", "type": "private"}

        contacto = Update.de_json({
            "update_id": 1,
            "message": {
                "message_id": 3779, "from": quien, "chat": chat, "date": 1756360032,
                "contact": {"phone_number": "+56942479733",
                            "first_name": "Matias", "user_id": 8926044722},
            },
        }, api_bot)

        def callback(data):
            return Update.de_json({
                "update_id": 2,
                "callback_query": {
                    "id": "1", "from": quien, "chat_instance": "x", "data": data,
                    "message": {"message_id": 3780, "chat": chat, "date": 1756360032},
                },
            }, api_bot)

        self.assertEqual(
            self._primer_handler(application, contacto).callback,
            bot.handle_contact_shared,
        )
        self.assertEqual(
            self._primer_handler(application, callback("cur:USD")).callback,
            bot.handle_currency_callback,
        )
        self.assertEqual(
            self._primer_handler(application, callback("tz:America/Santiago")).callback,
            bot.handle_timezone_selection,
        )
