from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Organizer(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organizer_profile"
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    stripe_account_id = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_organizer"
        ordering = ["name"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            base = slugify(self.name)[:200] or "organizer"
            slug, suffix = base, 1
            while Organizer.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
