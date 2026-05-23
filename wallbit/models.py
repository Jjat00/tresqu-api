from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class WallbitAccount(models.Model):
    CONNECTED = "connected"
    REVOKED = "revoked"
    ERROR = "error"
    STATUS_CHOICES = [
        (CONNECTED, "Connected"),
        (REVOKED, "Revoked"),
        (ERROR, "Error"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallbit_account",
    )
    encrypted_api_key = models.TextField()
    scope_hint = models.CharField(max_length=32, default="read,trade")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=CONNECTED)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    kill_switch_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"WallbitAccount(user={self.user_id}, status={self.status})"


class WallbitTxMirror(models.Model):
    account = models.ForeignKey(
        WallbitAccount, on_delete=models.CASCADE, related_name="txs"
    )
    wallbit_uuid = models.CharField(max_length=64, unique=True)
    tx_type = models.CharField(max_length=32)
    status = models.CharField(max_length=32)
    source_currency = models.CharField(max_length=8)
    dest_currency = models.CharField(max_length=8)
    source_amount = models.DecimalField(max_digits=18, decimal_places=8)
    dest_amount = models.DecimalField(max_digits=18, decimal_places=8)
    external_address = models.CharField(max_length=256, blank=True, null=True)
    created_at_wallbit = models.DateTimeField()
    comment = models.TextField(blank=True)
    raw = models.JSONField()
    embedding = VectorField(dimensions=1536, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["account", "-created_at_wallbit"]),
            models.Index(fields=["tx_type"]),
        ]

    def __str__(self) -> str:
        return f"WallbitTxMirror({self.tx_type} {self.wallbit_uuid})"


class WallbitChestLink(models.Model):
    savings_goal = models.OneToOneField(
        "savings.SavingsGoal", on_delete=models.CASCADE, related_name="wallbit_chest"
    )
    wallbit_chest_id = models.IntegerField()
    auto_top_up_amount_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    auto_top_up_cron = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return f"WallbitChestLink(goal={self.savings_goal_id}, chest={self.wallbit_chest_id})"


class Investment(models.Model):
    STOCK = "STOCK"
    ETF = "ETF"
    BOND = "BOND"
    ROBO = "ROBO"
    CHEST = "CHEST"
    KIND_CHOICES = [
        (STOCK, "Stock"),
        (ETF, "ETF"),
        (BOND, "Bond"),
        (ROBO, "Robo-advisor"),
        (CHEST, "Chest"),
    ]

    BUY = "BUY"
    SELL = "SELL"
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    ACTION_CHOICES = [
        (BUY, "Buy"),
        (SELL, "Sell"),
        (DEPOSIT, "Deposit"),
        (WITHDRAW, "Withdraw"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investments",
    )
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    action = models.CharField(max_length=8, choices=ACTION_CHOICES)
    symbol = models.CharField(max_length=16, blank=True)
    chest_category = models.CharField(max_length=32, blank=True)
    amount_usd = models.DecimalField(max_digits=14, decimal_places=2)
    shares = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True
    )
    wallbit_tx = models.OneToOneField(
        WallbitTxMirror,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investment",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["kind", "action"]),
        ]

    def __str__(self) -> str:
        symbol_or_chest = self.symbol or self.chest_category or "-"
        return f"Investment({self.action} {self.kind} {symbol_or_chest} ${self.amount_usd})"


class AgentDecision(models.Model):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    WEB = "web"
    VOICE = "voice"
    CHANNEL_CHOICES = [
        (TELEGRAM, "Telegram"),
        (WHATSAPP, "WhatsApp"),
        (WEB, "Web"),
        (VOICE, "Voice"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_decisions",
    )
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES)
    user_message = models.TextField()
    agent_reasoning = models.TextField(blank=True)
    tools_called = models.JSONField(default=list)
    requires_confirmation = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    executed = models.BooleanField(default=False)
    wallbit_tx_uuid = models.CharField(max_length=64, null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["requires_confirmation", "executed"]),
        ]

    def __str__(self) -> str:
        return f"AgentDecision(user={self.user_id}, channel={self.channel}, executed={self.executed})"


class AgentLimits(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_limits",
    )
    max_trade_usd = models.DecimalField(max_digits=10, decimal_places=2, default=500)
    max_daily_move_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=1000
    )
    allowed_symbols = models.JSONField(default=list)
    blocked_symbols = models.JSONField(default=list)
    require_2step_above_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=200
    )

    def __str__(self) -> str:
        return f"AgentLimits(user={self.user_id})"


class ParsedStatement(models.Model):
    BANK = "bank"
    CARD = "card"
    SOURCE_CHOICES = [(BANK, "Bank"), (CARD, "Card")]

    QUEUED = "queued"
    PARSING = "parsing"
    DONE = "done"
    ERROR = "error"
    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (PARSING, "Parsing"),
        (DONE, "Done"),
        (ERROR, "Error"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parsed_statements",
    )
    file = models.FileField(upload_to="statements/")
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    bank_name = models.CharField(max_length=64, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    txs_count = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"ParsedStatement(user={self.user_id}, status={self.status})"
