"""Streamlit AppTest flows against a live Django API."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Button, TextInput

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

APP_PATH = Path(__file__).resolve().parents[2] / "streamlit_app" / "app.py"
APP_TIMEOUT_SECONDS = 30


def test_unauthenticated_shows_login_form(mda_api_base_url: str) -> None:
    del mda_api_base_url
    app = AppTest.from_file(str(APP_PATH), default_timeout=APP_TIMEOUT_SECONDS).run()
    assert not app.exception
    assert any(title.value == "Market Data Analytics" for title in app.title)
    assert any("Sign in" in caption.value for caption in app.caption)
    assert len(app.text_input) >= 2
    assert _find_button(app, "Login") is not None


def test_register_and_login_reaches_home(
    mda_api_base_url: str,
    unique_username: str,
    unique_email: str,
    strong_password: str,
) -> None:
    del mda_api_base_url
    app = AppTest.from_file(str(APP_PATH), default_timeout=APP_TIMEOUT_SECONDS).run()
    assert not app.exception

    _require_text_input(app, key="reg_user").set_value(unique_username)
    _require_text_input(app, key="reg_email").set_value(unique_email)
    _require_text_input(app, key="reg_pass").set_value(strong_password)
    _require_text_input(app, key="reg_confirm").set_value(strong_password)
    _click_labeled_button(app, "Create account")
    assert not app.exception
    assert any("Account created" in message.value for message in app.success)

    _require_text_input(app, label="Username or email").set_value(unique_username)
    _login_password_input(app).set_value(strong_password)
    _click_labeled_button(app, "Login")
    assert not app.exception
    assert _find_button(app, "Logout") is not None
    assert _find_button(app, "Refresh") is not None


def _require_text_input(
    app: AppTest,
    *,
    key: str | None = None,
    label: str | None = None,
) -> TextInput:
    for field in app.text_input:
        if key is not None and field.key == key:
            return field
        if label is not None and field.label == label:
            return field
    raise AssertionError(f"text_input not found key={key!r} label={label!r}")


def _login_password_input(app: AppTest) -> TextInput:
    for field in app.text_input:
        if field.label == "Password" and field.key in {None, ""}:
            return field
    raise AssertionError("Login password input not found")


def _find_button(app: AppTest, label: str) -> Button | None:
    for button in app.button:
        if button.label == label:
            return button
    return None


def _click_labeled_button(app: AppTest, label: str) -> None:
    button = _find_button(app, label)
    if button is None:
        raise AssertionError(f"button not found: {label}")
    button.click().run()
