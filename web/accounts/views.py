"""Server-rendered account pages (session auth) alongside the JSON auth API."""

from __future__ import annotations

import logging
from typing import Any, cast

from common.security import encode_token
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from web.accounts import emails
from web.accounts.forms import LoginForm, SignupForm
from web.accounts.models import User
from web.accounts.readmodel import account_orders, account_refunds, account_tickets
from web.accounts.throttling import clear_login_failures, is_login_blocked, register_failed_login
from web.events.models import Event, TicketType
from web.venues.models import Seat

logger = logging.getLogger(__name__)


def _ip(request: HttpRequest) -> str:
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
    return (
        forwarded.split(",")[0].strip()
        if forwarded
        else str(request.META.get("REMOTE_ADDR", "unknown"))
    )


@require_http_methods(["GET", "POST"])
def signup_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("accounts:account")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth_login(request, user)
        emails.send_verification_email(user.pk)
        messages.success(
            request, "Welcome! Check your inbox to verify your email before buying tickets."
        )
        return redirect("accounts:account")
    return render(request, "accounts/signup.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("accounts:account")
    form = LoginForm(request.POST or None, request=request)
    next_url = request.GET.get("next", "")
    if request.method == "POST":
        email = request.POST.get("email", "").lower()
        ip = _ip(request)
        if email and is_login_blocked(ip, email):
            messages.error(request, "Too many failed attempts. Try again in 15 minutes.")
            return render(request, "accounts/login.html", {"form": form}, status=429)
        if form.is_valid():
            if form.user is None:  # pragma: no cover - defensive
                raise RuntimeError("authenticated form without a user")
            auth_login(request, form.user)
            clear_login_failures(ip, email)
            return redirect(next_url or "accounts:account")
        if email:
            register_failed_login(ip, email)
    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    auth_logout(request)
    return redirect("events:index")


@require_http_methods(["GET"])
def verify_email_view(request: HttpRequest, token: str) -> HttpResponse:
    try:
        user_id = emails.read_verification_token(token)
    except Exception:
        messages.error(request, "That verification link is invalid or has expired.")
        return redirect("accounts:login")
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        messages.error(request, "That account no longer exists.")
        return redirect("accounts:signup")
    if not user.is_verified:
        user.is_verified = True
        user.save(update_fields=["is_verified"])
        messages.success(request, "Email verified. You can buy tickets now.")
    if request.user.is_authenticated:
        return redirect("accounts:account")
    return redirect("accounts:login")


REFUND_LOCKED = {"requested", "processing", "succeeded"}


def _order_cards(user_id: int) -> list[dict[str, Any]]:
    """Join checkout orders/tickets with catalogue data for the tickets page.

    Three checkout SELECTs plus three catalogue queries, however many orders
    the customer has: no N+1.
    """
    orders = account_orders(user_id)
    tickets = account_tickets(user_id)
    refunds = account_refunds(user_id)

    events = Event.objects.select_related("venue").in_bulk({o["event_id"] for o in orders})
    type_names = dict(
        TicketType.objects.filter(pk__in={t["ticket_type_id"] for t in tickets}).values_list(
            "pk", "name"
        )
    )
    seats = Seat.objects.select_related("section").in_bulk(
        {t["seat_id"] for t in tickets if t["seat_id"]}
    )

    by_order: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        seat = seats.get(ticket["seat_id"]) if ticket["seat_id"] else None
        ticket["type_name"] = type_names.get(ticket["ticket_type_id"], "Ticket")
        ticket["seat"] = (
            f"{seat.section.name}, row {seat.row}, seat {seat.number}" if seat else None
        )
        by_order.setdefault(str(ticket["order_id"]), []).append(ticket)

    now = timezone.now()
    cards = []
    for order in orders:
        event = events.get(order["event_id"])
        refund = refunds.get(str(order["id"]))
        started = event is None or event.starts_at <= now
        order_tickets = by_order.get(str(order["id"]), [])
        scanned = any(ticket["checked_in_at"] for ticket in order_tickets)
        cards.append(
            {
                "order": order,
                "event": event,
                "tickets": order_tickets,
                "refund": refund,
                "upcoming": event is not None and event.ends_at >= now,
                "can_refund": order["status"] == "paid"
                and not started
                and not scanned
                and not (refund and refund["status"] in REFUND_LOCKED),
            }
        )
    return cards


@login_required
def account_view(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    cards = _order_cards(int(user.pk))
    token = encode_token(user.pk, user.role, "access", ttl_seconds=3600) if user.is_verified else ""
    return render(
        request,
        "accounts/account.html",
        {
            "upcoming": [card for card in cards if card["upcoming"]],
            "past": [card for card in cards if not card["upcoming"]],
            "account_config": {"token": token},
        },
    )


@login_required
def ticket_pdf_view(request: HttpRequest, ticket_id: Any) -> HttpResponse:
    user = cast(User, request.user)
    if not user.is_verified:
        messages.error(request, "Confirm your email address before downloading tickets.")
        return redirect("accounts:account")

    from api.app.db import session_scope
    from api.app.services import tickets as ticket_service
    from api.app.services.errors import NotFound, TicketInvalid
    from worker.pdf import render_ticket_pdf

    try:
        with session_scope() as session:
            fields = ticket_service.ticket_pdf_payload(
                session, user_id=int(user.pk), ticket_id=str(ticket_id)
            )
    except NotFound as exc:
        raise Http404("Ticket not found") from exc
    except TicketInvalid as exc:
        raise PermissionDenied("This ticket is no longer downloadable.") from exc

    response = HttpResponse(render_ticket_pdf(**fields), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ticket-{ticket_id}.pdf"'
    return response


RESET_SALT = "ticketforge.password-reset"


@require_http_methods(["GET", "POST"])
def password_reset_request_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        email = request.POST.get("email", "").lower()
        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None:
            from worker.tasks import send_password_reset_email

            send_password_reset_email.delay(user.pk)
        messages.success(
            request, "If that account exists, a reset link is on its way. Check Mailpit locally."
        )
        return redirect("accounts:login")
    return render(request, "accounts/password_reset.html")


@require_http_methods(["GET", "POST"])
def password_reset_confirm_view(request: HttpRequest) -> HttpResponse:
    token = request.POST.get("token") or request.GET.get("token", "")
    try:
        data = signing.loads(token, salt=RESET_SALT, max_age=3600)
        user = User.objects.get(pk=int(data["uid"]))
    except Exception:
        messages.error(request, "That reset link is invalid or has expired.")
        return redirect("accounts:password-reset")

    if request.method == "POST":
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")
        if password1 != password2:
            messages.error(request, "The two passwords do not match.")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError as DjangoValidationError

            try:
                validate_password(password1, user)
            except DjangoValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                user.set_password(password1)
                user.save(update_fields=["password"])
                # Following the emailed link proves who owns the account, so a
                # lockout caused by someone else guessing ends here.
                clear_login_failures(_ip(request), user.email)
                messages.success(request, "Password updated. You can log in now.")
                return redirect("accounts:login")
    return render(request, "accounts/password_reset_confirm.html", {"token": token})


@login_required
def resend_verification_view(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if not user.is_verified:
        emails.send_verification_email(int(user.pk))
        messages.success(
            request, "Verification email sent. Check Mailpit at http://localhost:8025."
        )
    return redirect("accounts:account")
