# -*- coding: utf-8 -*-
"""SQLite接続・テーブル初期化・在庫/買い物/レシピ/プロフィールの読み書き。"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from fractions import Fraction

import pandas as pd

CATEGORY_LIST = [
    "農産物（野菜・果物）",
    "水産物（魚介類）",
    "畜産物（肉・卵・乳製品）",
    "調味料",
    "飲料",
    "加工食品・その他",
]
SHOPPING_AISLE_ORDER = [
    "野菜・果物",
    "魚介類",
    "肉類",
    "日配品・乳製品",
    "常温・調味料",
    "冷凍食品・アイス",
    "お菓子・飲料（レジ周辺）",
    "その他",
]
DAILY_AI_LIMIT = 10
DAILY_IMAGE_LIMIT = 3
AI_LIMIT_WARNING = (
    "⚠️ 本日のAI利用上限（10回）に達しました。APIコスト保護のため、明日以降に再度お試しください。"
)
AI_IMAGE_LIMIT_WARNING = (
    "⚠️ 本日の画像解析上限（3回）に達しました。APIコスト保護のため、明日以降に再度お試しください。"
)


class ApiQuotaExceeded(RuntimeError):
    """1日のGemini利用上限に達した。"""
_AISLE_FROZEN_KEYS = ("冷凍", "アイス", "氷菓", "冷食")
_AISLE_DAIRY_KEYS = (
    "牛乳", "ミルク", "ヨーグルト", "チーズ", "バター", "生クリーム",
    "卵", "たまご", "豆腐", "納豆", "豆乳", "厚揚げ", "油揚げ",
)
_AISLE_MEAT_KEYS = (
    "肉", "鶏", "豚", "牛", "ひき", "ベーコン", "ハム", "ソーセージ", "レバー",
)
_AISLE_FISH_KEYS = (
    "魚", "鮭", "サケ", "さば", "サバ", "まぐろ", "マグロ", "えび", "エビ",
    "いか", "イカ", "たこ", "タコ", "貝", "しらす", "ツナ", "いわし", "あじ",
    "ぶり", "さんま", "ほたて", "カニ", "かに",
)
_AISLE_VEG_KEYS = (
    "野菜", "果物", "フルーツ", "にんじん", "人参", "玉ねぎ", "じゃがいも",
    "キャベツ", "レタス", "白菜", "トマト", "きゅうり", "なす", "ピーマン",
    "もやし", "ねぎ", "りんご", "バナナ", "みかん", "キウイ", "ほうれん草", "小松菜",
)
_AISLE_SEASONING_KEYS = (
    "醤油", "みりん", "味噌", "砂糖", "塩", "酢", "こしょう", "油", "ソース",
    "ケチャップ", "マヨ", "めんつゆ", "コンソメ", "だし", "酒",
)
_AISLE_SNACK_DRINK_KEYS = (
    "ジュース", "お茶", "コーヒー", "紅茶", "ビール", "ワイン", "炭酸",
    "コーラ", "お菓子", "チョコ", "クッキー", "ポテチ", "せんべい", "スナック", "飲料",
)
_OLD_CAT_TO_AISLE = {
    "農産物（野菜・果物）": "野菜・果物",
    "水産物（魚介類）": "魚介類",
    "調味料": "常温・調味料",
    "飲料": "お菓子・飲料（レジ周辺）",
    "加工食品・その他": "その他",
    "野菜・果物": "野菜・果物",
    "魚介類": "魚介類",
    "肉類": "肉類",
    "日配品・乳製品": "日配品・乳製品",
    "常温・調味料": "常温・調味料",
    "冷凍食品・アイス": "冷凍食品・アイス",
    "お菓子・飲料（レジ周辺）": "お菓子・飲料（レジ周辺）",
    "その他": "その他",
}


def to_shopping_aisle(name, category=""):
    """買い物リスト用に、スーパー動線の8分類へ正規化する。"""
    n = str(name or "")
    cat = str(category or "").strip()
    if any(k in n for k in _AISLE_FROZEN_KEYS):
        return "冷凍食品・アイス"
    if cat == "畜産物（肉・卵・乳製品）":
        if any(k in n for k in _AISLE_DAIRY_KEYS):
            return "日配品・乳製品"
        return "肉類"
    if cat in SHOPPING_AISLE_ORDER:
        return cat
    mapped = _OLD_CAT_TO_AISLE.get(cat)
    if mapped:
        return mapped
    if any(k in n for k in _AISLE_FISH_KEYS):
        return "魚介類"
    if any(k in n for k in _AISLE_DAIRY_KEYS):
        return "日配品・乳製品"
    if any(k in n for k in _AISLE_MEAT_KEYS):
        return "肉類"
    if any(k in n for k in _AISLE_VEG_KEYS):
        return "野菜・果物"
    if any(k in n for k in _AISLE_SEASONING_KEYS):
        return "常温・調味料"
    if any(k in n for k in _AISLE_SNACK_DRINK_KEYS):
        return "お菓子・飲料（レジ周辺）"
    return "その他"


def shopping_aisle_rank(name, category=""):
    aisle = to_shopping_aisle(name, category)
    try:
        return SHOPPING_AISLE_ORDER.index(aisle)
    except ValueError:
        return len(SHOPPING_AISLE_ORDER)
INVENTORY_UNIT_OPTIONS = [
    "個", "本", "玉", "パック", "g", "ml", "大さじ", "小さじ", "袋", "切れ", "その他",
]
BASIC_SEASONING_OPTIONS = [
    "醤油",
    "酒",
    "みりん",
    "油",
    "砂糖",
    "塩",
    "酢",
    "こしょう",
    "味噌",
    "マヨネーズ",
    "ケチャップ",
    "めんつゆ",
    "コンソメ",
    "鶏ガラスープの素",
    "ごま油",
    "サラダ油",
    "オリーブオイル",
    "オイスターソース",
]
DEFAULT_OWNED_SEASONINGS = ["醤油", "酒", "みりん", "油", "砂糖", "塩", "酢", "こしょう"]

_COOKING_OILS = {"油", "サラダ油", "植物油", "食用油"}

RETAIL_PACKAGES = {
    "酢": {"qty": 500, "unit": "ml", "pack": "本"},
    "醤油": {"qty": 500, "unit": "ml", "pack": "本"},
    "みりん": {"qty": 500, "unit": "ml", "pack": "本"},
    "酒": {"qty": 500, "unit": "ml", "pack": "本"},
    "ごま油": {"qty": 200, "unit": "ml", "pack": "本"},
    "油": {"qty": 1000, "unit": "ml", "pack": "本"},
    "サラダ油": {"qty": 1000, "unit": "ml", "pack": "本"},
    "オリーブオイル": {"qty": 250, "unit": "ml", "pack": "本"},
    "オイスターソース": {"qty": 250, "unit": "g", "pack": "本"},
    "ケチャップ": {"qty": 400, "unit": "g", "pack": "本"},
    "マヨネーズ": {"qty": 400, "unit": "g", "pack": "本"},
    "めんつゆ": {"qty": 500, "unit": "ml", "pack": "本"},
    "味噌": {"qty": 500, "unit": "g", "pack": "パック"},
    "砂糖": {"qty": 1000, "unit": "g", "pack": "袋"},
    "塩": {"qty": 100, "unit": "g", "pack": "袋"},
    "こしょう": {"qty": 1, "unit": "本", "pack": "本"},
    "コンソメ": {"qty": 1, "unit": "箱", "pack": "箱"},
    "鶏ガラスープの素": {"qty": 1, "unit": "箱", "pack": "箱"},
    "レモン汁": {"qty": 120, "unit": "ml", "pack": "本"},
    "チューブにんにく": {"qty": 1, "unit": "本", "pack": "本"},
}

# スーパー想定の概算単価（円 / 基準単位）
INGREDIENT_UNIT_PRICES = {
    "トマト": (80, "個"),
    "ミニトマト": (198, "パック"),
    "にんじん": (50, "本"),
    "人参": (50, "本"),
    "玉ねぎ": (40, "個"),
    "じゃがいも": (40, "個"),
    "キャベツ": (150, "個"),
    "レタス": (150, "個"),
    "白菜": (200, "個"),
    "小松菜": (100, "袋"),
    "ほうれん草": (100, "袋"),
    "きゅうり": (50, "本"),
    "なす": (80, "本"),
    "ピーマン": (80, "袋"),
    "パプリカ": (150, "個"),
    "もやし": (30, "袋"),
    "ねぎ": (80, "本"),
    "長ねぎ": (80, "本"),
    "にんにく": (40, "個"),
    "しょうが": (50, "個"),
    "きのこ": (128, "パック"),
    "しめじ": (100, "パック"),
    "えのき": (80, "袋"),
    "豆腐": (80, "丁"),
    "納豆": (100, "パック"),
    "卵": (30, "個"),
    "牛乳": (200, "本"),
    "ヨーグルト": (120, "個"),
    "チーズ": (180, "袋"),
    "鶏むね肉": (2, "g"),
    "鶏もも肉": (2, "g"),
    "豚バラ肉": (3, "g"),
    "豚こま": (2, "g"),
    "牛肉": (5, "g"),
    "ひき肉": (2, "g"),
    "鮭": (250, "切"),
    "さば": (180, "切"),
    "ツナ": (120, "缶"),
    "米": (0.4, "g"),
    "パスタ": (150, "袋"),
    "うどん": (120, "袋"),
    "食パン": (150, "袋"),
    "醤油": (250, "本"),
    "みりん": (250, "本"),
    "味噌": (280, "パック"),
    "砂糖": (200, "袋"),
    "塩": (100, "袋"),
    "油": (300, "本"),
    "ごま油": (350, "本"),
    "マヨネーズ": (280, "本"),
    "ケチャップ": (220, "本"),
    "バナナ": (100, "本"),
    "りんご": (120, "個"),
    "みかん": (40, "個"),
    "キウイ": (80, "個"),
}

CATEGORY_FALLBACK_PRICE = {
    "農産物（野菜・果物）": 80,
    "水産物（魚介類）": 250,
    "畜産物（肉・卵・乳製品）": 180,
    "調味料": 250,
    "飲料": 150,
    "加工食品・その他": 160,
    "野菜・果物": 80,
    "魚介類": 250,
    "肉類": 180,
    "日配品・乳製品": 180,
    "常温・調味料": 250,
    "冷凍食品・アイス": 200,
    "お菓子・飲料（レジ周辺）": 150,
    "その他": 160,
}

UNIT_FALLBACK_PRICE = {
    "g": 1.5,
    "ml": 0.5,
    "個": 80,
    "本": 90,
    "枚": 60,
    "玉": 120,
    "パック": 180,
    "袋": 150,
    "缶": 130,
    "箱": 250,
    "切れ": 180,
    "丁": 80,
}


_PBKDF2_ROUNDS = 120000
_USER_DATA_TABLES = (
    "inventory",
    "recipes",
    "purchase_history",
    "shopping_list",
    "cooking_logs",
)


def get_connection():
    conn = sqlite3.connect("inventory.db", timeout=20, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=20000;")
    except sqlite3.Error:
        pass
    return conn


def get_current_user_id():
    """ログイン中の user_id。未ログインなら None。"""
    try:
        import streamlit as st

        uid = st.session_state.get("auth_user_id")
        if uid in (None, ""):
            return None
        return int(uid)
    except Exception:
        return None


def _hash_passcode(passcode, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(passcode or "").encode("utf-8"),
        salt,
        _PBKDF2_ROUNDS,
    )
    return salt.hex(), digest.hex()


def _claim_unassigned_rows(conn, user_id):
    """最初のユーザー登録時だけ、既存の未割り当てデータを引き継ぐ。"""
    n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if int(n_users or 0) != 1:
        return
    for table in _USER_DATA_TABLES:
        try:
            conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                (int(user_id),),
            )
        except sqlite3.Error:
            pass
    try:
        existing = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if existing:
            return
        old = conn.execute(
            "SELECT gender, age_group, activity_level FROM user_profile WHERE id = 1"
        ).fetchone()
        if old:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_profiles
                (user_id, gender, age_group, activity_level, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    old[0] or "",
                    old[1] or "",
                    old[2] or "",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
    except sqlite3.Error:
        pass


def login_or_register_user(username, passcode):
    """既存ユーザーは暗証キー照合。未登録の名前なら新規登録してログイン。"""
    name = str(username or "").strip()
    code = str(passcode or "")
    if not name:
        return None, "ユーザー名を入力してください。"
    if len(name) > 40:
        return None, "ユーザー名は40文字以内にしてください。"
    if not code.strip():
        return None, "暗証キーを入力してください。"
    if len(code) < 4:
        return None, "暗証キーは4文字以上にしてください。"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, password_salt, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row:
            uid, salt_hex, stored_hash = row
            _, check_hash = _hash_passcode(code, salt_hex)
            if not hmac.compare_digest(str(stored_hash), str(check_hash)):
                return None, "暗証キーが違います。"
            return int(uid), None
        salt_hex, hashed = _hash_passcode(code)
        try:
            cur = conn.execute(
                """
                INSERT INTO users (username, password_salt, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, salt_hex, hashed, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            uid = int(cur.lastrowid)
            _claim_unassigned_rows(conn, uid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id, password_salt, password_hash FROM users WHERE username = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if not row:
                return None, "登録に失敗しました。もう一度お試しください。"
            uid, salt_hex, stored_hash = row
            _, check_hash = _hash_passcode(code, salt_hex)
            if not hmac.compare_digest(str(stored_hash), str(check_hash)):
                return None, "暗証キーが違います。"
            return int(uid), None
    return uid, None


def get_or_create_user_id(username):
    """管理者ログイン後など、暗証キー照合なしで user_id を確保する。"""
    name = str(username or "").strip()
    if not name:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row:
            return int(row[0])
        salt_hex, hashed = _hash_passcode(secrets.token_urlsafe(24))
        cur = conn.execute(
            """
            INSERT INTO users (username, password_salt, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, salt_hex, hashed, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        uid = int(cur.lastrowid)
        _claim_unassigned_rows(conn, uid)
        return uid


def list_all_users():
    """管理者用: 全ユーザーと件数。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, created_at FROM users ORDER BY id"
        ).fetchall()
        out = []
        for uid, name, created in rows:
            inv = conn.execute(
                "SELECT COUNT(*) FROM inventory WHERE user_id = ?", (uid,)
            ).fetchone()[0]
            rec = conn.execute(
                "SELECT COUNT(*) FROM recipes WHERE user_id = ?", (uid,)
            ).fetchone()[0]
            shop = conn.execute(
                "SELECT COUNT(*) FROM shopping_list WHERE user_id = ?", (uid,)
            ).fetchone()[0]
            out.append(
                {
                    "id": int(uid),
                    "username": name,
                    "created_at": created or "",
                    "inventory_count": int(inv or 0),
                    "recipe_count": int(rec or 0),
                    "shopping_count": int(shop or 0),
                }
            )
        return out


def delete_user_and_data(user_id):
    """特定ユーザーの行を関連テーブルから削除する。既存他ユーザーは触らない。"""
    uid = int(user_id)
    with get_connection() as conn:
        for table in _USER_DATA_TABLES:
            try:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            except sqlite3.Error:
                pass
        try:
            conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (uid,))
        except sqlite3.Error:
            pass
        try:
            conn.execute("DELETE FROM user_api_usage WHERE user_id = ?", (uid,))
        except sqlite3.Error:
            pass
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    return True


def get_db_path():
    return os.path.abspath("inventory.db")


def export_sqlite_bytes():
    """WAL 込みで一貫した SQLite バイト列を返す。"""
    src = get_connection()
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
            with open(tmp_path, "rb") as handle:
                return handle.read()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    finally:
        src.close()


def consume_api_quota(is_image=False):
    """本日のAPI回数を確認して加算する。上限なら (False, 警告文)。日付ごとに自動リセット。"""
    uid = get_current_user_id()
    if uid is None:
        return False, AI_LIMIT_WARNING
    today = date.today().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT call_count, image_count FROM user_api_usage
            WHERE user_id = ? AND usage_date = ?
            """,
            (uid, today),
        ).fetchone()
        calls = int(row[0] or 0) if row else 0
        images = int(row[1] or 0) if row else 0
        if calls >= DAILY_AI_LIMIT:
            return False, AI_LIMIT_WARNING
        if is_image and images >= DAILY_IMAGE_LIMIT:
            return False, AI_IMAGE_LIMIT_WARNING
        conn.execute(
            """
            INSERT INTO user_api_usage (user_id, usage_date, call_count, image_count)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, usage_date) DO UPDATE SET
                call_count = call_count + 1,
                image_count = image_count + excluded.image_count
            """,
            (uid, today, 1 if is_image else 0),
        )
    return True, None

def _ensure_sqlite_column(conn, table, column, ddl):
    """既存DBを壊さず、無いカラムだけ ALTER TABLE で追加する"""
    try:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column in cols:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError:
        pass

def init_db():
    with get_connection() as conn:
        # 在庫テーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1.0,
                unit TEXT DEFAULT '',
                category TEXT DEFAULT '加工食品・その他',
                expiry_date DATE NOT NULL,
                created_at TEXT,
                price INTEGER DEFAULT 0
            )
        """)
        inventory_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(inventory)").fetchall()
        ]
        if "price" not in inventory_cols:
            conn.execute("ALTER TABLE inventory ADD COLUMN price INTEGER DEFAULT 0")

        # レシピ履歴・お気に入りテーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_name TEXT NOT NULL,
                category TEXT,
                time TEXT,
                details_json TEXT NOT NULL,
                is_favorite INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        # 食費の購入履歴
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchase_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER DEFAULT 0,
                category TEXT,
                purchased_at TEXT
            )
        """)
        # フェーズ3: 自動買い物リスト
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shopping_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                category TEXT DEFAULT '加工食品・その他',
                is_bought INTEGER DEFAULT 0,
                added_reason TEXT DEFAULT '',
                created_at TEXT,
                quantity REAL DEFAULT 1,
                unit TEXT DEFAULT '個',
                package_label TEXT DEFAULT '',
                estimated_price INTEGER DEFAULT 0
            )
        """)
        _ensure_sqlite_column(conn, "shopping_list", "quantity", "REAL DEFAULT 1")
        _ensure_sqlite_column(conn, "shopping_list", "unit", "TEXT DEFAULT '個'")
        _ensure_sqlite_column(conn, "shopping_list", "package_label", "TEXT DEFAULT ''")
        _ensure_sqlite_column(conn, "shopping_list", "estimated_price", "INTEGER DEFAULT 0")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cooking_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_name TEXT NOT NULL,
                used_ingredients_json TEXT NOT NULL DEFAULT '[]',
                cooked_at TEXT
            )
        """)
        # 栄養診断用プロフィール。既存テーブルの列や行は変更しない
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                gender TEXT NOT NULL DEFAULT '',
                age_group TEXT NOT NULL DEFAULT '',
                activity_level TEXT NOT NULL DEFAULT '',
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                gender TEXT NOT NULL DEFAULT '',
                age_group TEXT NOT NULL DEFAULT '',
                activity_level TEXT NOT NULL DEFAULT '',
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_api_usage (
                user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date)
            )
        """)
        for table in _USER_DATA_TABLES:
            _ensure_sqlite_column(conn, table, "user_id", "INTEGER")
            try:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_user_id ON {table}(user_id)"
                )
            except sqlite3.Error:
                pass

def parse_price(val):
    """金額を整数円に変換する"""
    try:
        return max(0, int(float(str(val).replace(",", "").replace("円", "").strip() or 0)))
    except Exception:
        return 0

def add_purchase_history(name, price, category, purchased_at=None):
    """支出履歴を保存"""
    uid = get_current_user_id()
    if uid is None:
        return
    if purchased_at is None:
        purchased_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO purchase_history (name, price, category, purchased_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (name, parse_price(price), category, purchased_at, uid),
        )

def delete_purchase_history(item_id):
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM purchase_history WHERE id = ? AND user_id = ?",
            (int(item_id), uid),
        )

def update_purchase_price(item_id, price):
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE purchase_history SET price = ? WHERE id = ? AND user_id = ?",
            (parse_price(price), int(item_id), uid),
        )

def get_purchase_history():
    """購入履歴一覧"""
    uid = get_current_user_id()
    cols = ["id", "name", "price", "category", "purchased_at"]
    if uid is None:
        return pd.DataFrame(columns=cols)
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT id, name, price, category, purchased_at FROM purchase_history WHERE user_id = ? ORDER BY purchased_at DESC, id DESC",
            conn,
            params=(uid,),
        )
    return df

def get_current_month_spent():
    """今月の購入履歴合計（円）"""
    uid = get_current_user_id()
    if uid is None:
        return 0
    ym = datetime.now().strftime("%Y-%m")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(price), 0) FROM purchase_history WHERE user_id = ? AND substr(purchased_at, 1, 7) = ?",
            (uid, ym),
        ).fetchone()
    return parse_price(row[0] if row else 0)

def add_item(name, quantity, unit, category, expiry_date, price=0):
    """在庫追加（分数を数値変換して保存）し、購入履歴にも記録する"""
    uid = get_current_user_id()
    if uid is None:
        return
    name = clean_ingredient_name(name) or str(name or "").strip()
    try:
        qty_val = float(Fraction(str(quantity)))
    except Exception:
        qty_val = 1.0

    price_val = parse_price(price)
    purchased_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO inventory (name, quantity, unit, category, expiry_date, created_at, price, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                qty_val,
                unit,
                category,
                str(expiry_date),
                purchased_at,
                price_val,
                uid,
            ),
        )
        conn.execute(
            "INSERT INTO purchase_history (name, price, category, purchased_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (name, price_val, category, purchased_at, uid),
        )
    remove_shopping_items_matching_name(name)

def get_inventory():
    """在庫取得"""
    uid = get_current_user_id()
    cols = ["id", "name", "quantity", "unit", "category", "expiry_date", "price"]
    if uid is None:
        return pd.DataFrame(columns=cols)
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT id, name, quantity, unit, category, expiry_date, price FROM inventory WHERE user_id = ? ORDER BY expiry_date ASC",
            conn,
            params=(uid,),
        )
    return df

def delete_item(item_id):
    """在庫削除"""
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM inventory WHERE id = ? AND user_id = ?", (item_id, uid))

def update_inventory_item(item_id, name, quantity, unit, category, expiry_date, price=None):
    """在庫1件を手動更新する"""
    uid = get_current_user_id()
    if uid is None:
        return False
    name = str(name or "").strip()
    if not name:
        return False
    try:
        qty_val = float(Fraction(str(quantity)))
    except Exception:
        qty_val = 1.0
    cat = category if category in CATEGORY_LIST else "加工食品・その他"
    unit_val = str(unit or "").strip() or "個"
    with get_connection() as conn:
        if price is None:
            conn.execute(
                """
                UPDATE inventory
                SET name = ?, quantity = ?, unit = ?, category = ?, expiry_date = ?
                WHERE id = ? AND user_id = ?
                """,
                (name, qty_val, unit_val, cat, str(expiry_date), int(item_id), uid),
            )
        else:
            conn.execute(
                """
                UPDATE inventory
                SET name = ?, quantity = ?, unit = ?, category = ?, expiry_date = ?, price = ?
                WHERE id = ? AND user_id = ?
                """,
                (name, qty_val, unit_val, cat, str(expiry_date), parse_price(price), int(item_id), uid),
            )
    remove_shopping_items_matching_name(name)
    return True


def update_inventory_price(item_id, price):
    uid = get_current_user_id()
    if uid is None:
        return False
    with get_connection() as conn:
        conn.execute(
            "UPDATE inventory SET price = ? WHERE id = ? AND user_id = ?",
            (parse_price(price), int(item_id), uid),
        )
    return True


def fill_missing_inventory_prices():
    """金額0円の在庫へ概算価格を入れる。"""
    df = get_inventory()
    if df is None or df.empty:
        return 0
    updated = 0
    for _, row in df.iterrows():
        if parse_price(row.get("price")) > 0:
            continue
        price = estimate_ingredient_price(
            row.get("name"),
            row.get("quantity"),
            row.get("unit") or "個",
            row.get("category") or "",
        )
        if price > 0:
            update_inventory_price(row["id"], price)
            updated += 1
    return updated

def _normalize_shopping_name(name):
    return re.sub(r"\s+", "", str(name or "").strip())

def clean_ingredient_name(text):
    """材料テキストから括弧書き・分量・『お好みで』などを除き、純粋な食材名にする"""
    s = str(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(][^（）()]*[）)]", "", s)
    s = re.sub(r"(大さじ|小さじ|カップ)\s*[\d./／]+", "", s)
    s = re.sub(
        r"\d+(?:\.\d+)?\s*(?:ml|mL|ミリリットル|cc|g|グラム|kg|キロ|L|リットル|個|本|枚|玉|片|缶|袋|パック|箱|束|房)",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"(少々|適量|お好みで|各適量|適宜|お好み)", "", s)
    s = re.sub(r"[\s　]+", "", s)
    s = s.strip(" 　・,、./")
    aliases = {
        "しょうゆ": "醤油",
        "醤油": "醤油",
        "こしょう": "こしょう",
        "胡椒": "こしょう",
        "黒胡椒": "こしょう",
        "ブラックペッパー": "こしょう",
        "お酢": "酢",
        "食酢": "酢",
        "穀物酢": "酢",
        "米酢": "酢",
        "お味噌": "味噌",
        "みそ": "味噌",
        "白砂糖": "砂糖",
        "グラニュー糖": "砂糖",
        "サラダ油": "サラダ油",
        "植物油": "油",
        "食用油": "油",
        "ごま油": "ごま油",
        "オリーブオイル": "オリーブオイル",
        "オリーヴオイル": "オリーブオイル",
        "オリーブ油": "オリーブオイル",
        "エクストラバージンオリーブオイル": "オリーブオイル",
        "胡麻油": "ごま油",
        "料理酒": "酒",
        "鶏がらスープの素": "鶏ガラスープの素",
        "鶏ガラスープ": "鶏ガラスープの素",
        "チューブのにんにく": "チューブにんにく",
        "チューブニンニク": "チューブにんにく",
        "レモンの汁": "レモン汁",
    }
    return aliases.get(s, s)

def _frac_to_float(text):
    t = str(text or "").replace("／", "/").strip()
    try:
        return float(Fraction(t))
    except Exception:
        try:
            return float(t)
        except Exception:
            return None

def normalize_unit(unit):
    u = str(unit or "").strip().lower().replace("ｍ", "m")
    mapping = {
        "ml": "ml",
        "ミリリットル": "ml",
        "cc": "ml",
        "l": "L",
        "リットル": "L",
        "g": "g",
        "グラム": "g",
        "kg": "kg",
        "キロ": "kg",
        "キログラム": "kg",
        "大さじ": "大さじ",
        "小さじ": "小さじ",
        "カップ": "カップ",
    }
    return mapping.get(u, unit or "")

def convert_qty(qty, from_unit, to_unit):
    """単位を揃えて数量を変換。できない場合は None"""
    try:
        q = float(qty)
    except Exception:
        return None
    src = normalize_unit(from_unit)
    dst = normalize_unit(to_unit)
    if not dst:
        return q
    if src == dst or not src:
        return q
    to_ml = {"ml": 1.0, "cc": 1.0, "L": 1000.0, "大さじ": 15.0, "小さじ": 5.0, "カップ": 200.0}
    to_g = {"g": 1.0, "kg": 1000.0}
    if src in to_ml and dst in to_ml:
        return q * to_ml[src] / to_ml[dst]
    if src in to_g and dst in to_g:
        return q * to_g[src] / to_g[dst]
    return None

def parse_qty_unit_from_text(text):
    t = str(text or "")
    m = re.search(r"大さじ\s*([\d./／]+)", t)
    if m:
        v = _frac_to_float(m.group(1))
        if v is not None:
            return v * 15.0, "ml"
    m = re.search(r"小さじ\s*([\d./／]+)", t)
    if m:
        v = _frac_to_float(m.group(1))
        if v is not None:
            return v * 5.0, "ml"
    m = re.search(
        r"([\d./／]+)\s*(ml|mL|ミリリットル|cc|g|グラム|kg|L|リットル|個|本|枚|玉|片|缶|袋|パック|箱)",
        t,
        re.IGNORECASE,
    )
    if m:
        v = _frac_to_float(m.group(1))
        if v is not None:
            return v, normalize_unit(m.group(2))
    return None

def parse_shopping_quantity(quantity, default=1.0):
    """買い物リスト用の数量（2, 1.5, 1/2 など）を数値化する"""
    try:
        val = float(Fraction(str(quantity).replace("／", "/").strip()))
        return val if val > 0 else default
    except Exception:
        return default

def parse_shopping_ingredient_entry(raw):
    """Geminiや不足リストの1件から name / quantity / unit / category を取り出す"""
    qty = None
    unit = ""
    category = ""
    name = ""
    if isinstance(raw, dict):
        name = (
            raw.get("name")
            or raw.get("item")
            or raw.get("ingredient")
            or raw.get("item_name")
            or ""
        )
        category = raw.get("category") or ""
        qty = None
        for key in ("quantity", "qty", "used_quantity", "need_qty"):
            if raw.get(key) not in (None, ""):
                qty = raw.get(key)
                break
        unit = raw.get("unit") or raw.get("need_unit") or ""
        parsed = parse_qty_unit_from_text(
            raw.get("display_amount") or raw.get("amount") or str(name)
        )
        if parsed:
            if qty in (None, "", 0, 0.0):
                qty = parsed[0]
            if not unit:
                unit = parsed[1]
    else:
        text = str(raw or "").strip()
        name = text
        parsed = parse_qty_unit_from_text(text)
        if parsed:
            qty, unit = parsed
            name = re.sub(
                r"[\d./／]+\s*(ml|mL|ミリリットル|cc|g|グラム|kg|L|リットル|個|本|枚|玉|片|缶|袋|パック|箱).*$",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip(" 　・,，")
            name = re.sub(r"(大さじ|小さじ)\s*[\d./／]+.*$", "", name).strip(" 　・,，") or text
    name = clean_ingredient_name(name) or str(name or "").strip()
    unit = normalize_unit(unit) or "個"
    qty_val = parse_shopping_quantity(qty if qty not in (None, "") else 1)
    if category not in CATEGORY_LIST:
        category = ""
    return {"name": name, "quantity": qty_val, "unit": unit, "category": category}

def names_match_ingredient(a, b):
    na = _normalize_shopping_name(clean_ingredient_name(a))
    nb = _normalize_shopping_name(clean_ingredient_name(b))
    if not na or not nb:
        return False
    if na == nb:
        return True
    # サラダ油は調理用の「油」として在庫照合する。オリーブオイルは別物。
    if na in _COOKING_OILS and nb in _COOKING_OILS:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 2 and shorter in longer

def owned_seasoning_names():
    try:
        import streamlit as st

        owned = st.session_state.get("owned_seasonings") or []
    except Exception:
        owned = []
    return [clean_ingredient_name(x) for x in owned]

def is_owned_seasoning(name):
    """選択済みの常備調味料（サラダ油・オリーブオイルを含む）なら買い足し不要"""
    cleaned = clean_ingredient_name(name)
    if not cleaned:
        return False
    owned = owned_seasoning_names()
    for item in owned:
        if names_match_ingredient(cleaned, item):
            return True
    # 「油」を選んでいるときだけ、サラダ油・植物油も家にあるものとして扱う
    generic_oil = {"油", "植物油", "食用油"}
    if "油" in owned and cleaned in generic_oil:
        return True
    if "サラダ油" in owned and cleaned in {"サラダ油", "植物油", "食用油"}:
        return True
    if cleaned == "オリーブオイル" and "オリーブオイル" in owned:
        return True
    if "オリーブ" in cleaned and "オイル" in cleaned and "オリーブオイル" in owned:
        return True
    return False

def apply_estimated_prices_to_parsed_items(items, enabled=True):
    """一括解析結果のうち、金額が未設定/0円の食材だけ概算価格を補完する"""
    if not enabled or not items:
        return items
    out = []
    for item in items:
        row = dict(item) if isinstance(item, dict) else {"name": str(item)}
        if parse_price(row.get("price")) <= 0:
            row["price"] = estimate_ingredient_price(
                row.get("name"),
                row.get("quantity", 1),
                row.get("unit") or "個",
                row.get("category") or "",
            )
        out.append(row)
    return out

def to_shopping_package(name, need_qty=0, need_unit=""):
    """レシピ単位の不足を、市販パッケージの数量・単位に換算する"""
    clean = clean_ingredient_name(name) or str(name or "").strip()
    pack = RETAIL_PACKAGES.get(clean)
    try:
        need = float(need_qty or 0)
    except Exception:
        need = 0.0
    if pack:
        converted = convert_qty(need, need_unit, pack["unit"])
        if converted is None:
            converted = need if need > 0 else pack["qty"]
        bottles = max(1, int(math.ceil(converted / pack["qty"]))) if pack["qty"] else 1
        total = bottles * pack["qty"]
        label = f"{format_qty(pack['qty'])}{pack['unit']} {bottles}{pack['pack']}"
        return {
            "name": clean,
            "quantity": total,
            "unit": pack["unit"],
            "package_label": label,
        }
    unit = normalize_unit(need_unit) or "個"
    qty = max(1.0, math.ceil(need)) if need > 0 else 1.0
    return {
        "name": clean,
        "quantity": qty,
        "unit": unit,
        "package_label": f"{format_qty(qty)}{unit}",
    }

def estimate_ingredient_price(name, quantity=1, unit="個", category=""):
    """食材名・数量・単位から概算価格（円）を出す"""
    qty = parse_shopping_quantity(quantity)
    unit_val = normalize_unit(unit) or "個"
    clean = clean_ingredient_name(name) or str(name or "").strip()
    if not clean:
        return 0
    for key, (unit_price, base_unit) in INGREDIENT_UNIT_PRICES.items():
        if not names_match_ingredient(clean, key):
            continue
        converted = convert_qty(qty, unit_val, base_unit)
        if converted is None:
            converted = max(1.0, qty)
        return max(10, int(round(float(unit_price) * float(converted))))
    if unit_val in UNIT_FALLBACK_PRICE:
        per = UNIT_FALLBACK_PRICE[unit_val]
        amount = qty if unit_val in ("g", "ml") else max(1.0, qty)
        return max(10, int(round(per * amount)))
    fallback = CATEGORY_FALLBACK_PRICE.get(category) or 100
    return max(10, int(round(fallback * max(1.0, qty))))

def shopping_row_estimated_price(row):
    stored = parse_price(row.get("estimated_price") if hasattr(row, "get") else 0)
    if stored > 0:
        return stored
    return estimate_ingredient_price(
        row.get("item_name"),
        row.get("quantity"),
        row.get("unit"),
        row.get("category") or "",
    )

def parse_recipe_need(ing):
    if not isinstance(ing, dict):
        name = clean_ingredient_name(ing)
        return {"name": name, "qty": 0.0, "unit": "", "raw": str(ing)}
    raw_name = ing.get("name") or ing.get("item") or ""
    name = clean_ingredient_name(raw_name)
    qty = ing.get("used_quantity")
    unit = normalize_unit(ing.get("unit") or "")
    parsed = parse_qty_unit_from_text(ing.get("display_amount") or "")
    if parsed:
        if not qty:
            qty = parsed[0]
        if not unit:
            unit = parsed[1]
    try:
        qty = float(qty or 0)
    except Exception:
        qty = 0.0
    return {"name": name or str(raw_name).strip(), "qty": qty, "unit": unit, "raw": raw_name}

def collect_required_ingredients(recipe):
    ings = list(recipe.get("used_ingredients") or [])
    if not ings:
        for dish in recipe.get("dishes") or []:
            if isinstance(dish, dict):
                ings.extend(dish.get("used_ingredients") or [])
    return ings

def recalc_missing_against_inventory(recipe, inventory_df=None):
    """保存済みの不足リストではなく、現在の在庫と必要量を照合して不足を再計算する"""
    if inventory_df is None:
        inventory_df = get_inventory()
    required = [parse_recipe_need(x) for x in collect_required_ingredients(recipe)]
    seen = {_normalize_shopping_name(x["name"]) for x in required if x.get("name")}
    for extra in collect_missing_ingredient_entries(recipe):
        cleaned = extra.get("name") or ""
        key = _normalize_shopping_name(cleaned)
        if cleaned and key not in seen:
            required.append(
                {
                    "name": cleaned,
                    "qty": extra.get("quantity") or 0.0,
                    "unit": extra.get("unit") or "",
                    "raw": cleaned,
                    "category": extra.get("category") or "",
                }
            )
            seen.add(key)

    stock = []
    if inventory_df is not None and not inventory_df.empty:
        for _, row in inventory_df.iterrows():
            stock.append(
                {
                    "name": row.get("name"),
                    "qty": float(row.get("quantity") or 0),
                    "unit": row.get("unit") or "",
                }
            )

    missing = []
    for need in required:
        name = need["name"]
        if not name or is_owned_seasoning(name):
            continue
        matched = [s for s in stock if names_match_ingredient(s["name"], name)]
        if not matched:
            missing.append(
                {
                    "name": name,
                    "need_qty": need["qty"],
                    "need_unit": need["unit"],
                    "category": need.get("category") or "",
                    "reason": "在庫なし",
                }
            )
            continue
        if need["qty"] <= 0:
            continue
        have_total = 0.0
        comparable = True
        for s in matched:
            conv = convert_qty(s["qty"], s["unit"], need["unit"] or s["unit"])
            if conv is None:
                comparable = False
                break
            have_total += conv
        if comparable and have_total + 1e-6 < need["qty"]:
            missing.append(
                {
                    "name": name,
                    "need_qty": need["qty"] - have_total,
                    "need_unit": need["unit"],
                    "category": need.get("category") or "",
                    "reason": "数量不足",
                }
            )
    return missing

def format_missing_label(item):
    name = item.get("name") or ""
    qty = item.get("need_qty") or 0
    unit = item.get("need_unit") or ""
    reason = item.get("reason") or ""
    if qty:
        return f"{name}（不足 {format_qty(qty)}{unit}・{reason}）"
    return f"{name}（{reason}）" if reason else name

def add_shopping_item(
    item_name,
    category="",
    added_reason="",
    skip_duplicate=True,
    quantity=1,
    unit="個",
    package_label="",
    estimated_price=None,
):
    """買い物リストへ1件追加。未購入の同名があれば重複追加しない。"""
    name = clean_ingredient_name(item_name) or str(item_name or "").strip()
    if not name:
        return False
    cat = to_shopping_aisle(name, category)
    reason = str(added_reason or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    qty_val = parse_shopping_quantity(quantity)
    unit_val = str(unit or "").strip() or "個"
    label = package_label or f"{format_qty(qty_val)}{unit_val}"
    price_val = parse_price(estimated_price) if estimated_price not in (None, "") else 0
    if price_val <= 0:
        price_val = estimate_ingredient_price(name, qty_val, unit_val, cat)
    uid = get_current_user_id()
    if uid is None:
        return False
    with get_connection() as conn:
        if skip_duplicate:
            rows = conn.execute(
                "SELECT id, item_name FROM shopping_list WHERE is_bought = 0 AND user_id = ?",
                (uid,),
            ).fetchall()
            target = _normalize_shopping_name(name)
            for _, existing in rows:
                if _normalize_shopping_name(clean_ingredient_name(existing)) == target:
                    return False
        conn.execute(
            """
            INSERT INTO shopping_list
            (item_name, category, is_bought, added_reason, created_at, quantity, unit, package_label, estimated_price, user_id)
            VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, cat, reason, now, qty_val, unit_val, label, price_val, uid),
        )
    return True

def update_shopping_item(item_id, item_name, quantity, unit, category):
    uid = get_current_user_id()
    if uid is None:
        return False
    name = clean_ingredient_name(item_name) or str(item_name or "").strip()
    if not name:
        return False
    cat = to_shopping_aisle(name, category)
    qty_val = parse_shopping_quantity(quantity)
    unit_val = str(unit or "個").strip() or "個"
    label = f"{format_qty(qty_val)}{unit_val}"
    price_val = estimate_ingredient_price(name, qty_val, unit_val, cat)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE shopping_list
            SET item_name = ?, category = ?, quantity = ?, unit = ?, package_label = ?, estimated_price = ?
            WHERE id = ? AND user_id = ?
            """,
            (name, cat, qty_val, unit_val, label, price_val, int(item_id), uid),
        )
    return True

def get_shopping_list(unbought_only=False):
    uid = get_current_user_id()
    cols = [
        "id",
        "item_name",
        "category",
        "is_bought",
        "added_reason",
        "created_at",
        "quantity",
        "unit",
        "package_label",
        "estimated_price",
    ]
    if uid is None:
        return pd.DataFrame(columns=cols)
    query = (
        "SELECT id, item_name, category, is_bought, added_reason, created_at, "
        "quantity, unit, package_label, estimated_price FROM shopping_list WHERE user_id = ?"
    )
    params = [uid]
    if unbought_only:
        query += " AND is_bought = 0"
    query += " ORDER BY is_bought ASC, id DESC"
    with get_connection() as conn:
        df = pd.read_sql_query(query, conn, params=params)
    if df is None or df.empty:
        return df
    df["_rank"] = [
        shopping_aisle_rank(row.get("item_name"), row.get("category"))
        for _, row in df.iterrows()
    ]
    df = df.sort_values(["is_bought", "_rank", "id"], ascending=[True, True, False])
    return df.drop(columns=["_rank"]).reset_index(drop=True)

def set_shopping_item_bought(item_id, is_bought):
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE shopping_list SET is_bought = ? WHERE id = ? AND user_id = ?",
            (1 if is_bought else 0, int(item_id), uid),
        )

def delete_shopping_item(item_id):
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM shopping_list WHERE id = ? AND user_id = ?",
            (int(item_id), uid),
        )

def remove_shopping_items_matching_name(ingredient_name):
    """在庫に入った／更新された食材と同名の未購入買い物リストを削除する"""
    uid = get_current_user_id()
    if uid is None:
        return 0
    target = clean_ingredient_name(ingredient_name) or str(ingredient_name or "").strip()
    if not target:
        return 0
    removed = 0
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, item_name FROM shopping_list WHERE is_bought = 0 AND user_id = ?",
            (uid,),
        ).fetchall()
        for item_id, item_name in rows:
            if names_match_ingredient(target, item_name):
                conn.execute(
                    "DELETE FROM shopping_list WHERE id = ? AND user_id = ?",
                    (int(item_id), uid),
                )
                removed += 1
    return removed

def deduct_inventory_for_recipe(recipe):
    """調理完了時、必要量を在庫単位に換算して減算する。0以下は削除して在庫切れリストを返す。"""
    uid = get_current_user_id()
    if uid is None:
        return []
    depleted = []
    with get_connection() as conn:
        inv_rows = conn.execute(
            "SELECT id, name, quantity, unit, category FROM inventory WHERE user_id = ?",
            (uid,),
        ).fetchall()
        for ing in collect_required_ingredients(recipe):
            need = parse_recipe_need(ing)
            used_qty = need["qty"]
            used_unit = need["unit"]
            item_id = ing.get("id") if isinstance(ing, dict) else None
            row = None
            if item_id is not None:
                for r in inv_rows:
                    if r[0] == item_id:
                        row = r
                        break
            if row is None:
                for r in inv_rows:
                    if names_match_ingredient(r[1], need["name"]):
                        row = r
                        break
            if row is None:
                continue
            rid, item_name, current_qty, inv_unit, item_cat = row
            converted = convert_qty(used_qty, used_unit, inv_unit or used_unit)
            deduct = converted if converted is not None else used_qty
            new_qty = float(current_qty) - float(deduct or 0)
            if new_qty <= 0.05:
                depleted.append(
                    {
                        "name": clean_ingredient_name(item_name) or item_name,
                        "category": item_cat or "加工食品・その他",
                        "unit": inv_unit or used_unit,
                    }
                )
                conn.execute(
                    "DELETE FROM inventory WHERE id = ? AND user_id = ?",
                    (rid, uid),
                )
                inv_rows = [r for r in inv_rows if r[0] != rid]
            else:
                conn.execute(
                    "UPDATE inventory SET quantity = ? WHERE id = ? AND user_id = ?",
                    (new_qty, rid, uid),
                )
                inv_rows = [
                    (rid, item_name, new_qty, inv_unit, item_cat) if r[0] == rid else r
                    for r in inv_rows
                ]
        conn.commit()
    return depleted

def restock_depleted_to_shopping(depleted):
    for item in depleted:
        pkg = to_shopping_package(item["name"], 1, item.get("unit") or "")
        add_shopping_item(
            pkg["name"],
            category=item.get("category") or "加工食品・その他",
            added_reason="在庫切れ",
            quantity=pkg["quantity"],
            unit=pkg["unit"],
            package_label=pkg["package_label"],
        )

def count_unbought_shopping_items():
    uid = get_current_user_id()
    if uid is None:
        return 0
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM shopping_list WHERE is_bought = 0 AND user_id = ?",
            (uid,),
        ).fetchone()
    return int(row[0] or 0) if row else 0

def collect_missing_ingredient_entries(recipe):
    """レシピ全体と各品目の missing_ingredients から name/quantity/unit/category を集める"""
    entries = []
    seen = set()

    def _push(raw):
        parsed = parse_shopping_ingredient_entry(raw)
        name = parsed.get("name") or ""
        key = _normalize_shopping_name(name)
        if not name or key in seen:
            return
        seen.add(key)
        entries.append(parsed)

    for item in recipe.get("missing_ingredients") or []:
        _push(item)
    for dish in recipe.get("dishes") or []:
        if isinstance(dish, dict):
            for item in dish.get("missing_ingredients") or []:
                _push(item)
    return entries

def collect_missing_ingredient_names(recipe):
    """レシピ全体と各品目の missing_ingredients を名前リストにまとめる"""
    return [x["name"] for x in collect_missing_ingredient_entries(recipe)]

def add_missing_ingredients_to_shopping(items, added_reason="レシピ不足"):
    """不足リスト（文字列または照合結果dict）を数量・単位付きで買い物リストへ追加"""
    added = 0
    for item in items or []:
        parsed = parse_shopping_ingredient_entry(item)
        name = parsed["name"]
        if isinstance(item, dict):
            if item.get("need_qty") not in (None, ""):
                parsed["quantity"] = parse_shopping_quantity(item.get("need_qty"))
            if item.get("need_unit"):
                parsed["unit"] = normalize_unit(item.get("need_unit")) or parsed["unit"]
            incoming = item.get("category") or ""
            if incoming in CATEGORY_LIST or incoming in SHOPPING_AISLE_ORDER:
                parsed["category"] = incoming
        if not name or is_owned_seasoning(name):
            continue
        pkg = to_shopping_package(name, parsed["quantity"], parsed["unit"])
        category = parsed.get("category") or ""
        if name in RETAIL_PACKAGES or name in BASIC_SEASONING_OPTIONS:
            category = category or "常温・調味料"
        category = to_shopping_aisle(pkg["name"], category)
        if add_shopping_item(
            pkg["name"],
            category=category,
            added_reason=added_reason,
            quantity=pkg["quantity"],
            unit=pkg["unit"],
            package_label=pkg["package_label"],
        ):
            added += 1
    return added

def add_or_merge_inventory(name, quantity, unit, category, expiry_date, price=0):
    """同名かつ同じ単位系の在庫があれば数量を加算し、なければ新規追加する"""
    uid = get_current_user_id()
    if uid is None:
        return
    name = clean_ingredient_name(name) or str(name or "").strip()
    try:
        qty_val = float(Fraction(str(quantity)))
    except Exception:
        qty_val = 1.0
    unit_val = unit or "個"
    price_val = parse_price(price)
    purchased_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    merged = False
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, quantity, unit FROM inventory WHERE user_id = ?",
            (uid,),
        ).fetchall()
        for rid, existing_name, existing_qty, existing_unit in rows:
            if not names_match_ingredient(existing_name, name):
                continue
            converted = convert_qty(qty_val, unit_val, existing_unit or unit_val)
            if converted is None:
                continue
            conn.execute(
                "UPDATE inventory SET quantity = ? WHERE id = ? AND user_id = ?",
                (float(existing_qty) + converted, rid, uid),
            )
            conn.execute(
                "INSERT INTO purchase_history (name, price, category, purchased_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (name, price_val, category, purchased_at, uid),
            )
            merged = True
            break
    if merged:
        remove_shopping_items_matching_name(name)
        return
    add_item(name, qty_val, unit_val, category, expiry_date, price_val)

def move_bought_shopping_items_to_inventory():
    """購入済みの買い物リストを在庫へ登録し、リストから削除する"""
    df = get_shopping_list()
    if df.empty:
        return 0
    bought = df[df["is_bought"].astype(int) == 1]
    moved = 0
    expiry = date.today() + timedelta(days=7)
    for _, row in bought.iterrows():
        qty = row.get("quantity") if "quantity" in row else 1
        unit = row.get("unit") if "unit" in row else "個"
        add_or_merge_inventory(
            clean_ingredient_name(row["item_name"]),
            qty if pd.notna(qty) else 1,
            unit if pd.notna(unit) and str(unit).strip() else "個",
            row.get("category") or "加工食品・その他",
            expiry,
            0,
        )
        delete_shopping_item(row["id"])
        moved += 1
    return moved

def save_recipe(recipe_dict, is_favorite=0):
    """レシピを履歴・お気に入りに保存"""
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO recipes (recipe_name, category, time, details_json, is_favorite, created_at, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                recipe_dict.get("recipe_name", "無題レシピ"),
                recipe_dict.get("category", "指定なし"),
                recipe_dict.get("time", "不明"),
                json.dumps(recipe_dict, ensure_ascii=False),
                is_favorite,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                uid,
            ),
        )

def get_recipes(favorite_only=False):
    """レシピ履歴・お気に入り一覧の取得"""
    uid = get_current_user_id()
    cols = ["id", "recipe_name", "category", "time", "details_json", "is_favorite", "created_at"]
    if uid is None:
        return pd.DataFrame(columns=cols)
    query = "SELECT id, recipe_name, category, time, details_json, is_favorite, created_at FROM recipes WHERE user_id = ?"
    params = [uid]
    if favorite_only:
        query += " AND is_favorite = 1"
    query += " ORDER BY id DESC"
    
    with get_connection() as conn:
        df = pd.read_sql_query(query, conn, params=params)
    return df

def toggle_favorite(recipe_id, current_status):
    """お気に入りフラグの切り替え"""
    uid = get_current_user_id()
    if uid is None:
        return
    new_status = 0 if current_status == 1 else 1
    with get_connection() as conn:
        conn.execute(
            "UPDATE recipes SET is_favorite = ? WHERE id = ? AND user_id = ?",
            (new_status, recipe_id, uid),
        )

def delete_recipe(recipe_id):
    """レシピ履歴の削除"""
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, uid))

def _ingredient_names_from_recipe(recipe):
    names = []
    seen = set()
    for ing in collect_required_ingredients(recipe or {}):
        if isinstance(ing, dict):
            raw = ing.get("name") or ""
        else:
            raw = ing
        name = clean_ingredient_name(raw) or str(raw or "").strip()
        key = _normalize_shopping_name(name)
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names

def log_cooked_recipe(recipe):
    """「作った」押下時の調理ログを保存する"""
    payload = {
        "recipe_name": (recipe or {}).get("recipe_name") or "無題レシピ",
        "ingredients": _ingredient_names_from_recipe(recipe),
        "composition": (recipe or {}).get("composition") or [],
        "category": (recipe or {}).get("category") or "",
    }
    uid = get_current_user_id()
    if uid is None:
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO cooking_logs (recipe_name, used_ingredients_json, cooked_at, user_id) VALUES (?, ?, ?, ?)",
            (
                payload["recipe_name"],
                json.dumps(payload, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                uid,
            ),
        )

def apply_cook_complete(recipe):
    """在庫減算・買い物リスト補充・調理完了ログ記録（生成画面・履歴共通）"""
    depleted = deduct_inventory_for_recipe(recipe)
    restock_depleted_to_shopping(depleted)
    try:
        log_cooked_recipe(recipe)
    except Exception:
        pass
    return depleted

def _parse_log_payload(recipe_name, raw_json, cooked_at, source):
    ingredients = []
    try:
        data = json.loads(raw_json) if raw_json else {}
        if isinstance(data, dict):
            ingredients = data.get("ingredients") or [
                (x.get("name") if isinstance(x, dict) else x)
                for x in (data.get("used_ingredients") or [])
            ]
            recipe_name = data.get("recipe_name") or recipe_name
        elif isinstance(data, list):
            ingredients = [
                (x.get("name") if isinstance(x, dict) else x) for x in data
            ]
    except Exception:
        ingredients = []
    cleaned = []
    for n in ingredients:
        name = clean_ingredient_name(n) or str(n or "").strip()
        if name:
            cleaned.append(name)
    return {
        "recipe_name": recipe_name or "無題レシピ",
        "ingredients": cleaned,
        "cooked_at": cooked_at,
        "source": source,
    }

def get_recent_cooking_history(days=7, limit=10):
    """「この料理を作った！」で記録された調理完了ログのみを返す"""
    uid = get_current_user_id()
    if uid is None:
        return []
    history = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        log_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(cooking_logs)").fetchall()
        ]
        if log_cols:
            rows = conn.execute(
                """
                SELECT recipe_name, used_ingredients_json, cooked_at
                FROM cooking_logs
                WHERE user_id = ? AND cooked_at >= ?
                ORDER BY id DESC
                """,
                (uid, cutoff),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT recipe_name, used_ingredients_json, cooked_at
                    FROM cooking_logs
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (uid, limit),
                ).fetchall()
            for name, payload, cooked_at in rows:
                history.append(_parse_log_payload(name, payload, cooked_at, "調理完了"))
    return history[:limit]

def format_qty(val):
    """数値表示を整形（例: 1.0 -> 1, 0.5 -> 0.5）"""
    try:
        f_val = float(val)
        if f_val.is_integer():
            return str(int(f_val))
        return f"{f_val:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)

def parse_expiry_date(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()[:10].replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

def get_expiry_alert_items(df=None, within_days=3):
    """賞味期限切れ、および今日〜within_days 日以内の食材を返す"""
    if df is None:
        df = get_inventory()
    expired, soon = [], []
    if df is None or df.empty:
        return expired, soon
    today = date.today()
    for _, row in df.iterrows():
        exp = parse_expiry_date(row.get("expiry_date"))
        if exp is None:
            continue
        days_left = (exp - today).days
        item = {
            "id": int(row["id"]) if pd.notna(row.get("id")) else None,
            "name": row["name"],
            "quantity": row["quantity"],
            "unit": row.get("unit") or "",
            "category": row.get("category") or "",
            "expiry_date": str(exp),
            "days_left": int(days_left),
        }
        if days_left < 0:
            expired.append(item)
        elif days_left <= within_days:
            soon.append(item)
    return expired, soon


def load_user_profile():
    """ログイン中ユーザーの栄養診断用プロフィール。未設定なら空文字。"""
    uid = get_current_user_id()
    if uid is None:
        return {"gender": "", "age_group": "", "activity_level": ""}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT gender, age_group, activity_level FROM user_profiles WHERE user_id = ?",
            (uid,),
        ).fetchone()
    if not row:
        return {"gender": "", "age_group": "", "activity_level": ""}
    return {
        "gender": row[0] or "",
        "age_group": row[1] or "",
        "activity_level": row[2] or "",
    }


def save_user_profile(gender, age_group, activity_level):
    """ログイン中ユーザーのプロフィールを upsert する。"""
    uid = get_current_user_id()
    if uid is None:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, gender, age_group, activity_level, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                gender = excluded.gender,
                age_group = excluded.age_group,
                activity_level = excluded.activity_level,
                updated_at = excluded.updated_at
            """,
            (uid, str(gender or ""), str(age_group or ""), str(activity_level or ""), now),
        )


# init_db() はアプリのアクセスゲート通過後に呼ぶ。インポート時点では実行しない。
