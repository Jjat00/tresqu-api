from django.db import models
from pgvector.django import VectorField, CosineDistance, HnswIndex
from users.models import User
from categories.models import Category


class Expense(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='COP')

    # CAMPO ORIGINAL - Se mantendrá temporalmente durante la migración
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True)
    category_str = models.CharField(
        max_length=100, blank=True, null=True)

    # NUEVO CAMPO - Categoría por usuario
    user_expense_category = models.ForeignKey(
        'categories.UserExpenseCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
        help_text='Categoría de gasto personalizada por usuario'
    )

    # Mensaje saliente (confirmación del bot) que registró este gasto;
    # permite resolverlo de forma determinista al citar (swipe to reply)
    # o reaccionar a ese mensaje.
    source_message = models.ForeignKey(
        'users.Message',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_expenses',
    )

    description = models.TextField(blank=True)
    timestamp = models.DateTimeField()
    spent_at = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    raw_message = models.TextField(blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            HnswIndex(
                name='expense_embedding_idx',
                fields=['embedding'],
                m=16,                     # Cantidad de conexiones por nodo
                ef_construction=64,       # Factor de exploración durante construcción
                # Operador de distancia coseno
                opclasses=['vector_cosine_ops'],
            ),
        ]

    def __str__(self):
        # Priorizar user_expense_category sobre category
        if self.user_expense_category:
            category_name = self.user_expense_category.name
        elif self.category:
            category_name = self.category.name
        else:
            category_name = self.category_str
        return f"{category_name}: {self.amount} {self.currency} ({self.timestamp})"

    @classmethod
    def find_similar(cls, user, embedding, limit=5):
        """
        Encuentra gastos similares basados en el embedding proporcionado
        """
        if not embedding:
            return cls.objects.none()

        return cls.objects.filter(
            user=user,
            embedding__isnull=False
        ).annotate(
            distance=CosineDistance('embedding', embedding)
        ).order_by('distance')[:limit]
