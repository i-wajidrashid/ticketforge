"""Server-rendered account pages (session auth) alongside the JSON auth API."""

from __future__ import annotations

import logging
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from web.accounts import emails
from web.accounts.forms import LoginForm, SignupForm
from web.accounts.models import User
from web.accounts.readmodel import account_orders, account_tickets, owned_ticket
from web.accounts.throttling import clear_login_failures, is_login_blocked, register_failed_login

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


@login_required
def account_view(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    return render(
        request,
        "accounts/account.html",
        {"orders": account_orders(int(user.pk)), "tickets": account_tickets(int(user.pk))},
    )


@login_required
def ticket_pdf_view(request: HttpRequest, ticket_id: Any) -> HttpResponse:
    user = cast(User, request.user)
    ticket = owned_ticket(int(user.pk), str(ticket_id))
    if ticket is None:
        raise Http404("Ticket not found")
    if ticket["status"] not in {"valid", "checked_in"}:
        raise PermissionDenied("This ticket is no longer downloadable.")
    if not user.is_verified:
        messages.error(request, "Verify your email before downloading tickets.")
        return redirect("accounts:account")

    from worker.pdf import render_ticket_pdf

    pdf_bytes = render_ticket_pdf(
        ticket_id=str(ticket["id"]),
        qr_token=ticket["qr_token"],
        owner_email=user.email,
        order_id=str(ticket["order_id"]),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
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
