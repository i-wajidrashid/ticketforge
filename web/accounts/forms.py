from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

from web.accounts.models import User


class SignupForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True})
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 10 characters, not entirely numeric.",
    )
    password2 = forms.CharField(
        label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    def clean_email(self) -> str:
        email = str(self.cleaned_data["email"]).lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match.")
        if password1:
            try:
                validate_password(password1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self) -> User:
        return User.objects.create_user(
            email=self.cleaned_data["email"], password=self.cleaned_data["password1"]
        )


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def __init__(self, *args: Any, request: Any | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request = request
        self.user: User | None = None

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        email = str(cleaned.get("email", "")).lower()
        password = str(cleaned.get("password", ""))
        if email and password:
            self.user = authenticate(self.request, username=email, password=password)
            if self.user is None:
                raise forms.ValidationError("Email or password is incorrect.")
        return cleaned
