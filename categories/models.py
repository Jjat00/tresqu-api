from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    metadata = models.JSONField(blank=True, null=True)
