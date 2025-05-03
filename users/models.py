from django.db import models
from pgvector.django import VectorField


class User(models.Model):
    external_id = models.CharField(max_length=100, unique=True)
    platform = models.CharField(max_length=50)
    first_name = models.CharField(max_length=100, blank=True)
    username = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    # Campo para almacenar embeddings (opcional)
    embedding = VectorField(dimensions=1536, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.first_name or self.username or self.external_id}"
