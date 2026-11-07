from __future__ import annotations

from django.urls import path

from web.accounts import api, views

app_name = "accounts"

urlpatterns = [
    # HTML
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("verify/<str:token>/", views.verify_email_view, name="verify"),
    path("password-reset/", views.password_reset_request_view, name="password-reset"),
    path(
        "password-reset/confirm/", views.password_reset_confirm_view, name="password-reset-confirm"
    ),
    path("resend-verification/", views.resend_verification_view, name="resend-verification"),
    path("", views.account_view, name="account"),
    path("tickets/<uuid:ticket_id>/pdf/", views.ticket_pdf_view, name="ticket-pdf"),
    # JSON (JWT)
    path("api/signup/", api.SignupView.as_view(), name="api-signup"),
    path("api/token/", api.TokenObtainView.as_view(), name="api-token"),
    path("api/token/refresh/", api.TokenRefreshView.as_view(), name="api-token-refresh"),
    path("api/me/", api.MeView.as_view(), name="api-me"),
    path("api/password-reset/", api.PasswordResetRequestView.as_view(), name="api-password-reset"),
    path(
        "api/password-reset/confirm/",
        api.PasswordResetConfirmView.as_view(),
        name="api-password-reset-confirm",
    ),
]
