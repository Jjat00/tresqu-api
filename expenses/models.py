from django.db import models

from users.models import User
from categories.models import Category


class Expense(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='COP')
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField()
    raw_message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
