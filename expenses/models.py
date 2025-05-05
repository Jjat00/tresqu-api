from django.db import models
from pgvector.django import VectorField, CosineDistance
from users.models import User
from categories.models import Category


class Expense(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='COP')
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True)
    category_str = models.CharField(
        max_length=100, blank=True, null=True)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField()
    spent_at = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    raw_message = models.TextField(blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        category_name = self.category.name if self.category else self.category_str
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
