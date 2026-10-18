# -*- coding: utf-8 -*-
"""デモ期限・メンテナンス・管理者認証。DB / Gemini には触れない。"""
from __future__ import annotations

import hmac
import json
import os
from datetime import datetime

import streamlit as st

DEMO_EXPIRATION_DATE = "2026-10-31 23:59:59"
RUNTIME_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "runtime_settings.json"
)
MAINTENANCE_MESSAGE = (
    "現在システムメンテナンス中です。恐れ入りますが、しばらく経ってから再度アクセスしてください。"
)
DEMO_EXPIRED_MESSAGE = (
    "🎉 デモ公開期間は終了いたしました。ご体験・フィードバックをいただき、誠にありがとうございました！"
)


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return default


def _secret_value(key, default=None):
    try:
        secrets = st.secrets
        if key in secrets:
            return secrets[key]
    except Exception:
        pass
    return default


def _safe_equals(left, right):
    a = str(left or "").encode("utf-8")
    b = str(right or "").encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def load_runtime_settings():
    if not os.path.isfile(RUNTIME_SETTINGS_PATH):
        return {}
    try:
        with open(RUNTIME_SETTINGS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_runtime_settings(updates):
    data = load_runtime_settings()
    data.update(updates or {})
    with open(RUNTIME_SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def get_admin_credentials():
    username = str(
        _secret_value("ADMIN_USERNAME", os.environ.get("ADMIN_USERNAME", "")) or ""
    ).strip()
    password = str(
        _secret_value("ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD", "")) or ""
    )
    return username, password


def is_admin_credentials(username, password):
    admin_user, admin_pass = get_admin_credentials()
    if not admin_user or not admin_pass:
        return False
    name = str(username or "").strip()
    if not _safe_equals(name, admin_user) and name.casefold() != admin_user.casefold():
        return False
    return _safe_equals(password, admin_pass)


def is_admin_username(username):
    admin_user, _admin_pass = get_admin_credentials()
    name = str(username or "").strip()
    if not admin_user or not name:
        return False
    return _safe_equals(name, admin_user) or name.casefold() == admin_user.casefold()


def is_maintenance_mode():
    settings = load_runtime_settings()
    if "maintenance_mode" in settings:
        return _as_bool(settings.get("maintenance_mode"), False)
    secret_val = _secret_value("MAINTENANCE_MODE", None)
    if secret_val is not None:
        return _as_bool(secret_val, False)
    return _as_bool(os.environ.get("MAINTENANCE_MODE"), False)


def set_maintenance_mode(enabled):
    save_runtime_settings({"maintenance_mode": bool(enabled)})


def get_demo_expiration_text():
    settings = load_runtime_settings()
    text = str(settings.get("demo_expiration") or "").strip()
    return text or DEMO_EXPIRATION_DATE


def get_demo_expiration_datetime():
    text = get_demo_expiration_text()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            return datetime.strptime(DEMO_EXPIRATION_DATE, "%Y-%m-%d %H:%M:%S")


def set_demo_expiration_date(value):
    if hasattr(value, "strftime"):
        text = value.strftime("%Y-%m-%d") + " 23:59:59"
    else:
        text = str(value or "").strip() or DEMO_EXPIRATION_DATE
        if len(text) == 10:
            text = text + " 23:59:59"
    save_runtime_settings({"demo_expiration": text})


def is_demo_expired(now=None):
    current = now or datetime.now()
    return current > get_demo_expiration_datetime()


def _render_admin_gate_login():
    admin_user, admin_pass = get_admin_credentials()
    if not admin_user or not admin_pass:
        return
    with st.expander("管理者の方はこちら"):
        with st.form("gate_admin_login"):
            username = st.text_input("管理者ユーザー名")
            password = st.text_input("管理者パスワード", type="password")
            submitted = st.form_submit_button("管理者ログイン")
        if submitted:
            if is_admin_credentials(username, password):
                st.session_state["is_admin"] = True
                st.session_state["admin_username"] = str(username or "").strip()
                st.rerun()
            st.error("管理者認証に失敗しました。")


def enforce_access_gate():
    """メイン処理の最初で遮断する。該当時はログイン・DB・Gemini に進まない。"""
    if "is_admin" not in st.session_state:
        st.session_state["is_admin"] = False
    if "admin_username" not in st.session_state:
        st.session_state["admin_username"] = ""

    is_admin = bool(st.session_state.get("is_admin"))

    if is_demo_expired() and not is_admin:
        st.title("Smart Kitchen AI")
        st.success(DEMO_EXPIRED_MESSAGE)
        _render_admin_gate_login()
        st.stop()

    if is_maintenance_mode() and not is_admin:
        st.title("Smart Kitchen AI")
        st.warning(MAINTENANCE_MESSAGE)
        _render_admin_gate_login()
        st.stop()
