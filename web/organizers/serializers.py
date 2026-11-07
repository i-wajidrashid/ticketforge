from __future__ import annotations

from typing import Any

from rest_framework import serializers

from web.events.models import Event, StaffAssignment, TicketType
from web.venues.models import Seat, Section, Venue


class VenueSerializer(serializers.ModelSerializer[Venue]):
    class Meta:
        model = Venue
        fields = ["id", "name", "address", "city", "country", "capacity"]


class SeatSerializer(serializers.ModelSerializer[Seat]):
    class Meta:
        model = Seat
        fields = ["id", "row", "number"]


class SectionSerializer(serializers.ModelSerializer[Section]):
    seats = SeatSerializer(many=True, read_only=True)

    class Meta:
        model = Section
        fields = ["id", "venue", "name", "seats"]
        read_only_fields = ["venue"]


class TicketTypeSerializer(serializers.ModelSerializer[TicketType]):
    class Meta:
        model = TicketType
        fields = [
            "id",
            "name",
            "price_cents",
            "currency",
            "quantity_total",
            "sale_starts_at",
            "sale_ends_at",
            "max_per_order",
            "inventory_mode",
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts = attrs.get("sale_starts_at")
        ends = attrs.get("sale_ends_at")
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"sale_ends_at": "Sales must end after they start."})
        return attrs


class EventSerializer(serializers.ModelSerializer[Event]):
    venue = serializers.PrimaryKeyRelatedField(queryset=Venue.objects.all())
    ticket_types = TicketTypeSerializer(many=True, read_only=True)
    organizer_name = serializers.CharField(source="organizer.name", read_only=True)
    venue_name = serializers.CharField(source="venue.name", read_only=True)
    min_price_cents = serializers.IntegerField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "slug",
            "description",
            "category",
            "venue",
            "venue_name",
            "organizer_name",
            "starts_at",
            "ends_at",
            "status",
            "cover_image",
            "min_price_cents",
            "ticket_types",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["slug", "status", "created_at", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts = attrs.get("starts_at") or getattr(self.instance, "starts_at", None)
        ends = attrs.get("ends_at") or getattr(self.instance, "ends_at", None)
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "The event must end after it starts."})
        return attrs


class StaffAssignmentSerializer(serializers.ModelSerializer[StaffAssignment]):
    email = serializers.EmailField(write_only=True)
    user_email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = StaffAssignment
        fields = ["id", "email", "user_email", "created_at"]
        read_only_fields = ["created_at"]


class RefundSerializer(serializers.Serializer[Any]):
    id = serializers.IntegerField()
    order_id = serializers.UUIDField()
    amount_cents = serializers.IntegerField()
    status = serializers.CharField()
    reason = serializers.CharField()
    created_at = serializers.DateTimeField()
    event_id = serializers.IntegerField()
    user_id = serializers.IntegerField()


class PublishResultSerializer(serializers.Serializer[Any]):
    status = serializers.CharField()
    indexed = serializers.BooleanField()


class EventCancelSerializer(serializers.Serializer[Any]):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
