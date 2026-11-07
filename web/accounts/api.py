"""JSON auth endpoints used by the browser app (JWT access + refresh)."""

from __future__ import annotations

from typing import Any

from common.env import env_int
from common.security import TokenError, decode_token, encode_token
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from web.accounts import emails
from web.accounts.models import User
from web.accounts.serializers import SignupSerializer, TokenRequestSerializer, UserSerializer
from web.accounts.throttling import clear_login_failures, is_login_blocked, register_failed_login


def client_ip(request: Any) -> str:
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR", "unknown"))


def token_response(user: User) -> dict[str, Any]:
    return {
        "access": encode_token(user.pk, user.role, "access"),
        "refresh": encode_token(user.pk, user.role, "refresh"),
        "token_type": "Bearer",
        "expires_in": env_int("JWT_ACCESS_TTL_SECONDS", 900),
        "user": UserSerializer(user).data,
    }


@method_decorator(csrf_exempt, name="dispatch")
class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    def post(self, request: Any) -> Response:
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        emails.send_verification_email(user.pk)
        return Response(
            {"user": UserSerializer(user).data, "verification_sent": True},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class TokenObtainView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request: Any) -> Response:
        serializer = TokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        password = serializer.validated_data["password"]
        ip = client_ip(request)

        if is_login_blocked(ip, email):
            return Response(
                {
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many failed attempts. Try again later.",
                        "details": {},
                    }
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        user = authenticate(request, username=email, password=password)
        if user is None:
            register_failed_login(ip, email)
            return Response(
                {
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Email or password is incorrect.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        clear_login_failures(ip, email)
        return Response(token_response(user))


@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Any) -> Response:
        raw = str(request.data.get("refresh", ""))
        try:
            payload = decode_token(raw, expected_type="refresh")
        except TokenError:
            return Response(
                {
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "Refresh token is invalid or expired.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user = User.objects.filter(pk=payload.user_id, is_active=True).first()
        if user is None:
            return Response(
                {
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "User no longer exists.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {
                "access": encode_token(user.pk, user.role, "access"),
                "expires_in": 900,
            }
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Any) -> Response:
        return Response(UserSerializer(request.user).data)


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request: Any) -> Response:
        email = str(request.data.get("email", "")).lower()
        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None:
            from worker.tasks import send_password_reset_email

            send_password_reset_email.delay(user.pk)
        # Always 202: never leak whether an account exists.
        return Response(
            {"detail": "If that account exists, a reset email is on its way."},
            status=status.HTTP_202_ACCEPTED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request: Any) -> Response:
        from django.core import signing

        token = str(request.data.get("token", ""))
        password = str(request.data.get("password", ""))
        try:
            data = signing.loads(token, salt="ticketforge.password-reset", max_age=3600)
            user = User.objects.get(pk=int(data["uid"]))
        except (
            signing.BadSignature,
            signing.SignatureExpired,
            User.DoesNotExist,
            KeyError,
            ValueError,
        ):
            return Response(
                {
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "Reset link is invalid or expired.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(password, user)
        except DjangoValidationError as exc:
            return Response(
                {
                    "error": {
                        "code": "WEAK_PASSWORD",
                        "message": " ".join(exc.messages),
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(password)
        user.save(update_fields=["password"])
        return Response({"detail": "Password updated."})
