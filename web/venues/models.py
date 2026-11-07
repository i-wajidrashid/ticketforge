"""Venues and their physical seat maps."""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models


class Venue(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=120, db_index=True)
    country = models.CharField(max_length=120, default="Pakistan")
    capacity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_venue"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"


class Section(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "core_section"
        unique_together = [("venue", "name")]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.venue.name} / {self.name}"


class Seat(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="seats")
    # ``row`` is a reserved word in MySQL 8, so the physical column is row_label.
    row = models.CharField(max_length=10, db_column="row_label")
    number = models.CharField(max_length=10)

    class Meta:
        db_table = "core_seat"
        unique_together = [("section", "row", "number")]
        ordering = ["section", "row", "number"]

    def __str__(self) -> str:
        return f"{self.row}{self.number}"
