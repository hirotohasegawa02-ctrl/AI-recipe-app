# -*- coding: utf-8 -*-
"""Smart Kitchen AI — Streamlit UI とセッション状態。"""
import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

st.set_page_config(page_title="Smart Kitchen AI", page_icon="🥦", layout="wide")

from access_control import (
    DEMO_EXPIRATION_DATE,
    enforce_access_gate,
    get_admin_credentials,
    get_demo_expiration_datetime,
    is_admin_credentials,
    is_admin_username,
    is_maintenance_mode,
    set_demo_expiration_date,
    set_maintenance_mode,
)

enforce_access_gate()

from ai_services import (
    ACTIVITY_LEVEL_OPTIONS,
    AGE_GROUP_OPTIONS,
    ai_spinner,
    DEFAULT_MEAL_SLOTS,
    DEFAULT_PROFILE_ACTIVITY,
    DEFAULT_PROFILE_AGE_GROUP,
    DEFAULT_PROFILE_GENDER,
    GEMINI_KEY_MISSING_MSG,
    GENDER_OPTIONS,
    MEAL_CALENDAR_SLOTS,
    MEAL_SLOT_EMOJI,
    MEAL_SLOT_OPTIONS,
    MEAL_STYLE_OPTIONS,
    NUTRIENT_BOOST_SLOT_OPTIONS,
    SERVINGS_OPTIONS,
    STAPLE_TYPE_OPTIONS,
    WEEKDAY_JA,
    analyze_nutrition_balance,
    ask_cooking_question_with_ai,
    categorize_item_with_ai,
    generate_leftover_rescue_recipe,
    generate_nutrient_boost_recipe,
    generate_meal_calendar_with_ai,
    generate_recipe_from_inventory,
    get_configured_gemini_key,
    get_gemini_api_key,
    get_nutrition_reference,
    normalize_meal_recipe,
    normalize_slot_list,
    parse_audio_inventory_with_ai,
    parse_bulk_input_with_ai,
    parse_receipt_with_ai,
    prepare_image_for_ai,
    revise_meal_calendar_with_ai,
    revise_meal_recipe_with_ai,
    selected_meal_slots,
    selected_servings,
)
from database import (
    BASIC_SEASONING_OPTIONS,
    CATEGORY_LIST,
    DEFAULT_OWNED_SEASONINGS,
    INVENTORY_UNIT_OPTIONS,
    SHOPPING_AISLE_ORDER,
    add_item,
    add_missing_ingredients_to_shopping,
    add_shopping_item,
    apply_cook_complete,
    clean_ingredient_name,
    count_unbought_shopping_items,
    delete_item,
    delete_purchase_history,
    delete_recipe,
    delete_shopping_item,
    delete_user_and_data,
    export_sqlite_bytes,
    fill_missing_inventory_prices,
    format_missing_label,
    format_qty,
    get_current_month_spent,
    get_db_path,
    get_expiry_alert_items,
    get_inventory,
    get_or_create_user_id,
    get_purchase_history,
    get_recent_cooking_history,
    get_recipes,
    get_shopping_list,
    init_db,
    list_all_users,
    load_user_profile,
    login_or_register_user,
    move_bought_shopping_items_to_inventory,
    parse_expiry_date,
    parse_price,
    recalc_missing_against_inventory,
    save_recipe,
    save_user_profile,
    shopping_row_estimated_price,
    to_shopping_aisle,
    toggle_favorite,
    update_inventory_item,
    update_purchase_price,
    update_shopping_item,
    set_shopping_item_bought,
)

try:
    from database import ApiQuotaExceeded
except ImportError:
    class ApiQuotaExceeded(RuntimeError):
        """database 未再読込時のフォールバック。"""

init_db()

AI_GENERIC_ERROR = (
    "AIの応答処理で一時的なエラーが発生しました。時間をおいて再度お試しください。"
)

_configured_key, _configured_source = get_configured_gemini_key()
if _configured_key:
    st.session_state["gemini_key"] = _configured_key
    st.session_state["gemini_key_source"] = _configured_source
elif "gemini_key" not in st.session_state:
    st.session_state["gemini_key"] = ""
    st.session_state["gemini_key_source"] = "none"
elif "gemini_key_source" not in st.session_state:
    st.session_state["gemini_key_source"] = "manual" if st.session_state.get("gemini_key") else "none"
if "allergies" not in st.session_state:
    st.session_state["allergies"] = ""
if "meal_styles" not in st.session_state:
    old_style = st.session_state.get("cooking_style")
    if old_style == "高タンパク・ヘルシー":
        st.session_state["meal_styles"] = ["高タンパク"]
    else:
        st.session_state["meal_styles"] = []
if "bulk_ai_price_on" not in st.session_state:
    st.session_state["bulk_ai_price_on"] = True
if "cal_date_range" not in st.session_state:
    st.session_state["cal_date_range"] = (date.today(), date.today() + timedelta(days=2))
if "servings" not in st.session_state or st.session_state["servings"] not in SERVINGS_OPTIONS:
    st.session_state["servings"] = "2人分"
if "recipe_servings" not in st.session_state or st.session_state["recipe_servings"] not in SERVINGS_OPTIONS:
    st.session_state["recipe_servings"] = "2人分"
if "recipe_meal_slots" not in st.session_state:
    st.session_state["recipe_meal_slots"] = list(DEFAULT_MEAL_SLOTS)
if "nutrient_boost_slots" not in st.session_state:
    st.session_state["nutrient_boost_slots"] = ["副菜"]
if "recipe_staple_type" not in st.session_state or st.session_state["recipe_staple_type"] not in STAPLE_TYPE_OPTIONS:
    st.session_state["recipe_staple_type"] = "指定なし"
if "rescue_meal_slots" not in st.session_state:
    st.session_state["rescue_meal_slots"] = list(DEFAULT_MEAL_SLOTS)
if "monthly_food_budget" not in st.session_state:
    st.session_state["monthly_food_budget"] = 20000
if "has_basic_seasonings" not in st.session_state:
    st.session_state["has_basic_seasonings"] = True
if "bulk_voice_text" not in st.session_state:
    st.session_state["bulk_voice_text"] = ""
if "cook_step" not in st.session_state:
    st.session_state["cook_step"] = 0
if "cook_scale" not in st.session_state:
    st.session_state["cook_scale"] = 1.0
if "timer_end_ts" not in st.session_state:
    st.session_state["timer_end_ts"] = None
if "timer_total_sec" not in st.session_state:
    st.session_state["timer_total_sec"] = 0
if "timer_label" not in st.session_state:
    st.session_state["timer_label"] = ""
if "timer_done_notified" not in st.session_state:
    st.session_state["timer_done_notified"] = False
if "pending_recipe_free_request" in st.session_state:
    st.session_state["recipe_free_request"] = st.session_state.pop("pending_recipe_free_request")
if "owned_seasonings" not in st.session_state:
    st.session_state["owned_seasonings"] = list(DEFAULT_OWNED_SEASONINGS)

if "auth_user_id" not in st.session_state:
    st.session_state["auth_user_id"] = None
if "auth_username" not in st.session_state:
    st.session_state["auth_username"] = ""
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "admin_username" not in st.session_state:
    st.session_state["admin_username"] = ""
if st.session_state.get("is_admin") and not st.session_state.get("auth_user_id"):
    _admin_name = (
        st.session_state.get("admin_username") or get_admin_credentials()[0] or ""
    ).strip()
    if _admin_name:
        st.session_state["auth_user_id"] = get_or_create_user_id(_admin_name)
        st.session_state["auth_username"] = _admin_name
if "user_gender" not in st.session_state:
    st.session_state["user_gender"] = DEFAULT_PROFILE_GENDER
if "user_age_group" not in st.session_state:
    st.session_state["user_age_group"] = DEFAULT_PROFILE_AGE_GROUP
if "user_activity_level" not in st.session_state:
    st.session_state["user_activity_level"] = DEFAULT_PROFILE_ACTIVITY

FEEDBACK_FORM_URL = (
    "https://docs.google.com/forms/d/e/1FAIpQLSdFo87fzJSHRyzJR624tzMCir03QZL12YrN5f1II59XFewU2g/"
    "viewform?usp=publish-editor"
)
DISCLAIMER_TEXT = (
    "※レシピや献立などの提案はAIが生成しています。アレルギー食材や賞味期限等はご自身で最終確認してください。"
)


def inject_responsive_css():
    """狭い画面で列・ボタン・入力が崩れないようにする。"""
    st.markdown(
        """
<style>
@media (max-width: 768px) {
  .block-container {
    padding-left: 0.7rem !important;
    padding-right: 0.7rem !important;
    padding-top: 1rem !important;
  }
  div[data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
    row-gap: 0.55rem !important;
  }
  div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    width: 100% !important;
    min-width: 100% !important;
    flex: 1 1 100% !important;
  }
  div.stButton > button,
  div.stFormSubmitButton > button,
  div.stDownloadButton > button,
  div[data-testid="stLinkButton"] a {
    width: 100% !important;
    min-height: 2.75rem !important;
  }
  div[data-testid="stTextInput"] input,
  div[data-testid="stNumberInput"] input,
  div[data-testid="stTextArea"] textarea,
  div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
  div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    min-height: 2.5rem !important;
  }
  div[data-testid="stFileUploader"] {
    width: 100% !important;
  }
  div[data-testid="stDataFrame"],
  div[data-testid="stTable"],
  div[data-testid="stElementContainer"] table {
    display: block;
    max-width: 100%;
    overflow-x: auto !important;
  }
  div[data-testid="stTabs"] [data-baseweb="tab-list"] {
    flex-wrap: wrap !important;
  }
  [data-testid="stImage"] img {
    max-width: 100% !important;
    height: auto !important;
  }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def render_disclaimer_and_feedback():
    st.sidebar.caption(DISCLAIMER_TEXT)
    st.sidebar.link_button(
        "💬 ご意見・バグ報告はこちら",
        FEEDBACK_FORM_URL,
        use_container_width=True,
    )


inject_responsive_css()


def _ai_exc(e, fallback=None):
    """API上限は _gemini_generate 側で警告済み。生のPythonエラーは出さない。"""
    if isinstance(e, ApiQuotaExceeded):
        return
    st.error(AI_GENERIC_ERROR)


def _ai_signature(*parts):
    payload = json.dumps(parts, ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ai_run_cached(result_key, signature, producer):
    """同じ signature の結果が session にあれば Gemini を再呼び出ししない。"""
    sig_key = f"{result_key}__sig"
    if (
        signature
        and st.session_state.get(sig_key) == signature
        and st.session_state.get(result_key) is not None
    ):
        return st.session_state[result_key], True
    value = producer()
    st.session_state[result_key] = value
    st.session_state[sig_key] = signature
    return value, False


def render_inventory_item_editor(row, key_prefix, compact=False):
    """在庫1件の表示行と、編集時のUPDATEフォーム"""
    item_id = int(row["id"])
    edit_key = f"{key_prefix}_{item_id}"
    is_editing = st.session_state.get("editing_inventory_key") == edit_key

    if compact:
        cols = st.columns([3, 2, 2, 1, 1])
        cols[0].write(f"**{row['name']}**")
        cols[1].write(f"{format_qty(row['quantity'])} {row['unit']}")
        cols[2].write(f"⏳ {row['expiry_date']}")
        edit_col, del_col = cols[3], cols[4]
    else:
        cols = st.columns([2, 1, 2, 2, 1, 1, 1])
        cols[0].write(f"**{row['name']}**")
        cols[1].write(f"{format_qty(row['quantity'])} {row['unit']}")
        cols[2].write(f"🏷️ {row['category']}")
        cols[3].write(f"⏳ {row['expiry_date']}")
        cols[4].write(f"¥{parse_price(row.get('price', 0))}")
        edit_col, del_col = cols[5], cols[6]
    if edit_col.button("編集", key=f"edit_{edit_key}"):
        st.session_state["editing_inventory_key"] = None if is_editing else edit_key
        st.rerun()
    if del_col.button("削除", key=f"del_{edit_key}"):
        delete_item(item_id)
        if st.session_state.get("editing_inventory_key") == edit_key:
            st.session_state["editing_inventory_key"] = None
        st.rerun()

    if not is_editing:
        return

    unit_options = list(INVENTORY_UNIT_OPTIONS)
    current_unit = str(row.get("unit") or "個")
    if current_unit not in unit_options:
        unit_options = [current_unit] + unit_options
    cat_idx = CATEGORY_LIST.index(row["category"]) if row["category"] in CATEGORY_LIST else 0
    expiry_val = parse_expiry_date(row.get("expiry_date")) or date.today()
    with st.form(f"inv_edit_form_{edit_key}"):
        e1, e2, e3, e4 = st.columns([2, 1, 1, 2])
        new_name = e1.text_input("食材名", value=str(row["name"]))
        new_qty = e2.text_input("数量", value=format_qty(row["quantity"]))
        new_unit = e3.selectbox("単位", unit_options, index=unit_options.index(current_unit))
        new_cat = e4.selectbox("カテゴリ", CATEGORY_LIST, index=cat_idx)
        e5, e6 = st.columns(2)
        new_exp = e5.date_input("賞味期限", value=expiry_val)
        new_price = e6.number_input(
            "購入金額（円）",
            min_value=0,
            step=1,
            value=int(parse_price(row.get("price", 0))),
        )
        save_col, cancel_col = st.columns(2)
        saved = save_col.form_submit_button("更新する")
        cancelled = cancel_col.form_submit_button("キャンセル")
        if cancelled:
            st.session_state["editing_inventory_key"] = None
            st.rerun()
        if saved:
            if update_inventory_item(
                item_id, new_name, new_qty, new_unit, new_cat, new_exp, new_price
            ):
                st.session_state["editing_inventory_key"] = None
                st.success("在庫を更新しました。")
                st.rerun()
            else:
                st.error("食材名を入力してください。")


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


def render_shopping_forecast_banner(df_shop=None):
    """次回買い物の合計予想金額と、今月の残り予算の比較"""
    if df_shop is None:
        df_shop = get_shopping_list()
    unbought = df_shop
    if unbought is not None and not unbought.empty:
        unbought = unbought[unbought["is_bought"].astype(int) == 0]
        total = int(sum(shopping_row_estimated_price(row) for _, row in unbought.iterrows()))
    else:
        total = 0
    budget = parse_price(st.session_state.get("monthly_food_budget") or 0)
    spent = get_current_month_spent()
    remaining = budget - spent
    st.markdown(
        f"<div style='font-size:2.1rem;font-weight:800;line-height:1.3;margin:0.2rem 0 0.6rem 0;'>"
        f"次回買い物の合計予想金額：¥{total:,}</div>",
        unsafe_allow_html=True,
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("今月の食費予算", f"¥{budget:,}")
    m2.metric("今月の支出済", f"¥{spent:,}")
    m3.metric("残り予算", f"¥{remaining:,}")
    if total <= 0:
        st.caption("未購入の買い物リストから概算しています。")
    elif remaining < 0:
        st.error(f"今月の食費はすでに予算を ¥{abs(remaining):,} オーバーしています。追加の買い物は控えましょう。")
    elif total > remaining:
        st.error(
            f"予算オーバーの可能性があります。予想買い物額 ¥{total:,} が残り予算 ¥{remaining:,} を超えています。"
        )
    elif remaining > 0 and total >= remaining * 0.8:
        st.warning(f"残り予算の8割に近づいています（予想 ¥{total:,} / 残り ¥{remaining:,}）。")
    else:
        st.success(f"予算内に収まりそうです（予想 ¥{total:,} / 残り ¥{remaining:,}）。")
    return total


def render_shopping_list_panel():
    """買い物リスト画面"""
    st.header("🛒 買い物リスト")
    st.caption("足りない食材や在庫切れをまとめて管理し、買ったら在庫へ移せます。サイドバーの「今月の食費予算」と比べます。")
    df_shop = get_shopping_list()
    render_shopping_forecast_banner(df_shop)

    with st.expander("➕ 欲しい食材を手動追加", expanded=False):
        m1, m2, m3, m4 = st.columns([2, 1, 1, 2])
        with m1:
            manual_name = st.text_input("食材名", placeholder="例: トマト", key="shop_manual_name")
        with m2:
            manual_qty = st.number_input(
                "数量",
                min_value=0.0,
                value=1.0,
                step=0.5,
                key="shop_manual_qty",
            )
        with m3:
            manual_unit = st.selectbox(
                "単位",
                INVENTORY_UNIT_OPTIONS,
                index=0,
                key="shop_manual_unit",
            )
        with m4:
            manual_cat = st.selectbox("カテゴリ", SHOPPING_AISLE_ORDER, key="shop_manual_cat")
        if st.button("リストに追加", key="shop_manual_add"):
            if not (manual_name or "").strip():
                st.warning("食材名を入力してください。")
            elif add_shopping_item(
                clean_ingredient_name(manual_name),
                category=manual_cat,
                added_reason="手動追加",
                quantity=manual_qty,
                unit=manual_unit,
            ):
                qty_txt = format_qty(manual_qty)
                st.success(
                    f"「{clean_ingredient_name(manual_name)} {qty_txt} {manual_unit}」を買い物リストに追加しました。"
                )
                st.rerun()
            else:
                st.info("同じ未購入の食材がすでにリストにあります。")

    unbought_n = int((df_shop["is_bought"] == 0).sum()) if not df_shop.empty else 0
    bought_n = int((df_shop["is_bought"] == 1).sum()) if not df_shop.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("未購入", unbought_n)
    c2.metric("購入済み", bought_n)
    c3.metric("合計", len(df_shop))

    if df_shop.empty:
        st.info("買い物リストは空です。レシピの不足食材から追加するか、上から手動追加してください。")
        return

    st.subheader("一覧")
    st.caption("スーパーの動線順（野菜→魚→肉→日配→調味料→冷凍→レジ周辺）に並べています。買ったらチェック → 下のボタンで在庫へ一括登録します。")

    def _render_shop_row(row):
        item_id = int(row["id"])
        col_chk, col_name, col_edit, col_del = st.columns([1, 3, 1, 1])
        with col_chk:
            checked = st.checkbox(
                "購入済み",
                value=bool(int(row["is_bought"])),
                key=f"shop_bought_{item_id}",
            )
        if checked != bool(int(row["is_bought"])):
            set_shopping_item_bought(item_id, checked)
            st.rerun()
        pkg = row.get("package_label") or ""
        qty = row.get("quantity")
        unit = row.get("unit") or ""
        cat_label = to_shopping_aisle(row.get("item_name"), row.get("category"))
        qty_txt = format_qty(qty) if pd.notna(qty) else ""
        qty_unit = f"{qty_txt} {unit}".strip() if (qty_txt or unit) else ""
        line = f"**{row['item_name']}**"
        if qty_unit:
            line += f" {qty_unit}"
        line += f"（{cat_label}）"
        est = shopping_row_estimated_price(row)
        line += f" ／ 概算 ¥{est:,}"
        col_name.write(line)
        if pkg and pkg.replace(" ", "") != f"{qty_txt}{unit}".replace(" ", ""):
            col_name.caption(pkg)
        if col_edit.button("✏️ 編集", key=f"shop_edit_{item_id}"):
            st.session_state["shop_edit_id"] = item_id
            st.rerun()
        if col_del.button("削除", key=f"shop_del_{item_id}"):
            delete_shopping_item(item_id)
            if st.session_state.get("shop_edit_id") == item_id:
                st.session_state.pop("shop_edit_id", None)
            st.rerun()

        if st.session_state.get("shop_edit_id") == item_id:
            with st.container():
                e1, e2, e3, e4 = st.columns([2, 1, 1, 2])
                new_name = e1.text_input("食材名", value=str(row["item_name"]), key=f"shop_e_name_{item_id}")
                new_qty = e2.number_input(
                    "数量",
                    min_value=0.0,
                    value=float(qty) if pd.notna(qty) else 1.0,
                    step=0.1,
                    key=f"shop_e_qty_{item_id}",
                )
                new_unit = e3.text_input("単位", value=str(unit or "個"), key=f"shop_e_unit_{item_id}")
                aisle_val = to_shopping_aisle(row.get("item_name"), row.get("category"))
                new_cat = e4.selectbox(
                    "カテゴリ",
                    SHOPPING_AISLE_ORDER,
                    index=SHOPPING_AISLE_ORDER.index(aisle_val),
                    key=f"shop_e_cat_{item_id}",
                )
                s1, s2 = st.columns(2)
                if s1.button("保存", key=f"shop_e_save_{item_id}"):
                    if update_shopping_item(item_id, new_name, new_qty, new_unit, new_cat):
                        st.session_state.pop("shop_edit_id", None)
                        st.success("買い物リストを更新しました。")
                        st.rerun()
                    else:
                        st.warning("食材名を入力してください。")
                if s2.button("キャンセル", key=f"shop_e_cancel_{item_id}"):
                    st.session_state.pop("shop_edit_id", None)
                    st.rerun()

    last_aisle = None
    for _, row in df_shop.iterrows():
        aisle = to_shopping_aisle(row.get("item_name"), row.get("category"))
        bought_mark = "（購入済み）" if int(row["is_bought"]) else ""
        header = f"{aisle}{bought_mark}"
        if header != last_aisle:
            st.markdown(f"**{aisle}**" + (" ・購入済み" if int(row["is_bought"]) else ""))
            last_aisle = header
        _render_shop_row(row)

    st.divider()
    if st.button("🛒 チェックした食材を在庫に一括登録", type="primary", key="shop_move_bought"):
        moved = move_bought_shopping_items_to_inventory()
        if moved:
            st.success(f"{moved}件を在庫に登録し、買い物リストから削除しました。（賞味期限は本日+7日。同じ食材があれば数量を加算します）")
            st.rerun()
        else:
            st.warning("購入済みにチェックした食材がありません。")


def render_nutrition_dashboard():
    """フェーズ4: 栄養バランス診断画面"""
    st.header("📊 栄養バランス診断")
    st.caption("「🍽️ この料理を作った！」した献立だけが対象です。未調理の提案は含めません。")
    profile = {
        "gender": st.session_state.get("user_gender") or DEFAULT_PROFILE_GENDER,
        "age_group": st.session_state.get("user_age_group") or DEFAULT_PROFILE_AGE_GROUP,
        "activity_level": st.session_state.get("user_activity_level") or DEFAULT_PROFILE_ACTIVITY,
    }
    ref = get_nutrition_reference(
        profile["gender"], profile["age_group"], profile["activity_level"]
    )
    styles = [s for s in (st.session_state.get("meal_styles") or []) if s]
    st.caption(
        f"診断基準: {profile['gender']}／{profile['age_group']}／{profile['activity_level']}"
        + (f"／食事スタイル: {'、'.join(styles)}" if styles else "")
        + f"　（目安 {ref['energy_kcal']} kcal/日・たんぱく質 {ref['protein_g']} g・食塩 {ref['salt_g']} g未満）"
    )

    history = get_recent_cooking_history(days=7, limit=10)
    if not history:
        st.info("まずは料理を作って履歴を貯めましょう！「🍽️ この料理を作った！」を押すと診断に使われます。")
        return

    with st.expander("診断に使う直近の食事", expanded=False):
        for item in history:
            ings = "、".join(item.get("ingredients") or []) or "食材情報なし"
            st.write(
                f"- **{item.get('recipe_name')}**（{item.get('cooked_at') or '日時不明'} / {item.get('source')}）: {ings}"
            )

    if st.button("📊 過去の食事から栄養バランスを診断する", type="primary", key="run_nutrition"):
        if not st.session_state.get("gemini_key"):
            st.error(GEMINI_KEY_MISSING_MSG)
        else:
            try:
                hist_fp = [
                    (item.get("recipe_name"), item.get("cooked_at"), tuple(item.get("ingredients") or []))
                    for item in history
                ]
                sig = _ai_signature("nutrition", hist_fp, profile, styles)
                _ai_run_cached(
                    "nutrition_result",
                    sig,
                    lambda: analyze_nutrition_balance(
                        history,
                        st.session_state["gemini_key"],
                        profile=profile,
                        meal_styles=styles,
                    ),
                )
            except Exception as e:
                _ai_exc(e)

    result = st.session_state.get("nutrition_result")
    if not result:
        return

    score = result.get("score", 0)
    st.metric("総合栄養スコア", f"{score} / 100")
    used_ref = result.get("reference") or ref
    st.caption(
        f"比較した基準: 推定エネルギー約 {used_ref.get('energy_kcal')} kcal／"
        f"たんぱく質約 {used_ref.get('protein_g')} g／鉄約 {used_ref.get('iron_mg')} mg／"
        f"食塩 {used_ref.get('salt_g')} g未満（{used_ref.get('source') or ''}）"
    )
    if result.get("summary"):
        st.write(result["summary"])

    col_good, col_lack = st.columns(2)
    with col_good:
        st.subheader("👍 良い点")
        goods = result.get("good_points") or []
        if goods:
            for g in goods:
                st.success(g)
        else:
            st.caption("特記事項はありません")
    with col_lack:
        st.subheader("⚠️ 不足ポイント")
        lacks = result.get("lacking_nutrients") or []
        if lacks:
            for x in lacks:
                st.warning(x)
        else:
            st.caption("大きな不足は見当たりません")

    recs = result.get("recommended_ingredients") or []
    st.subheader("🛒 おすすめ食材")
    if recs:
        st.write("、".join(recs))
        if st.button("🚨 この食材でレシピを作成", type="primary", key="nutrition_to_recipe"):
            st.session_state["pending_recipe_free_request"] = (
                "栄養バランス改善のため、次の食材を優先して使ってください: " + "、".join(recs)
            )
            st.session_state["nutrition_priority_ingredients"] = recs
            st.session_state["nutrition_handoff"] = True
            st.rerun()
    else:
        st.caption("おすすめ食材はありません")

    if result.get("age_group_advice"):
        st.success("🧑‍⚕️ 世代別アドバイス: " + result["age_group_advice"])
    if result.get("advice"):
        st.info("💡 " + result["advice"])


def _qp_get(name):
    if name not in st.query_params:
        return None
    val = st.query_params[name]
    if isinstance(val, list):
        return val[0] if val else ""
    return val


def _qp_delete(*names):
    for name in names:
        if name in st.query_params:
            del st.query_params[name]


def render_recipe_nutrition_snapshot(recipe):
    """生成直後の献立に含まれる栄養素・不足栄養素を簡易表示する"""
    included = (recipe or {}).get("included_nutrients") or []
    missing = (recipe or {}).get("missing_nutrients") or []
    if not included and not missing:
        return
    st.markdown("##### 🥗 この献立の栄養素")
    col_n1, col_n2 = st.columns(2)
    with col_n1:
        st.success("含まれる主な栄養素")
        if included:
            st.markdown(" ".join(f"`{n}`" for n in included))
        else:
            st.caption("情報なし")
    with col_n2:
        st.warning("不足しやすい栄養素")
        if missing:
            st.markdown(" ".join(f"`{n}`" for n in missing))
        else:
            st.caption("特に目立つ不足はありません")

    if missing:
        st.multiselect(
            "栄養補完の構成（複数選択可）",
            NUTRIENT_BOOST_SLOT_OPTIONS,
            key="nutrient_boost_slots",
            help="不足栄養素を補う品目を選んでください。AIレシピ提案と同じく複数選択できます。デフォルトは副菜です。",
        )
        if st.button(
            "💡 不足している栄養素を補う一品を追加提案する",
            key="nutrient_boost_suggest_btn",
        ):
            if not st.session_state.get("gemini_key"):
                st.error(GEMINI_KEY_MISSING_MSG)
            else:
                slots = [
                    s
                    for s in normalize_slot_list(
                        st.session_state.get("nutrient_boost_slots")
                    )
                    if s in NUTRIENT_BOOST_SLOT_OPTIONS
                ]
                if not slots:
                    st.warning("栄養補完の品目を少なくとも1つ選んでください。")
                else:
                    try:
                        sig = _ai_signature(
                            "boost",
                            recipe.get("recipe_name"),
                            recipe.get("missing_nutrients"),
                            slots,
                        )
                        boost, cached = _ai_run_cached(
                            "nutrient_boost_recipe",
                            sig,
                            lambda: generate_nutrient_boost_recipe(
                                recipe,
                                st.session_state["gemini_key"],
                                meal_slots=slots,
                            ),
                        )
                        st.session_state["boost_cook_step"] = 0
                        st.session_state["boost_cook_scale"] = 1.0
                        if not cached:
                            save_recipe(boost, is_favorite=0)
                        st.rerun()
                    except Exception as e:
                        _ai_exc(e)
    else:
        st.success("栄養バランスはバッチリです！")


def render_dish_card(dish, heading):
    if not dish:
        st.info(f"{heading}はありません")
        return
    st.markdown(f"#### {heading} {dish.get('recipe_name', '')}")
    st.caption(f"{dish.get('category', 'ジャンル不明')} ／ {dish.get('time', '時間不明')}")
    st.write("**材料**")
    ings = dish.get("used_ingredients") or []
    if not ings:
        st.caption("材料情報がありません")
    for ing in ings:
        st.write(f"- **{ing.get('name')}**: {ing.get('display_amount', ing.get('used_quantity'))}")
    st.write("**手順**")
    for i, step in enumerate(dish.get("steps") or [], 1):
        st.write(f"{i}. {step}")
    if dish.get("point"):
        st.caption("💡 " + str(dish.get("point")))


def render_current_shortage_panel(recipe, button_key, show_ok=True):
    """現在の在庫と照合した不足材料を表示し、買い物リストへ追加する"""
    missing = recalc_missing_against_inventory(recipe)
    if not missing:
        if show_ok:
            st.success("現在の在庫で、このレシピの必要材料は足りています。")
        return
    st.warning("現在の不足材料: " + "、".join(format_missing_label(x) for x in missing))
    if st.button("🛒 現在の不足材料を買い物リストへ追加", key=button_key):
        added = add_missing_ingredients_to_shopping(missing, added_reason="在庫照合")
        if added:
            st.success(f"{added}件を買い物リストに追加しました（市販パッケージ単位）。")
        else:
            st.info("すでにリストにあるか、追加できる食材がありませんでした。")


def render_meal_dishes(recipe, key_prefix="meal"):
    """選択された構成の品目をタブ（複数）またはカード（1品）で表示する"""
    dishes = [d for d in (recipe.get("dishes") or []) if isinstance(d, dict)]
    if not dishes:
        st.info("品目情報がありません")
        return
    labels = []
    for d in dishes:
        slot = d.get("slot") or "料理"
        emoji = MEAL_SLOT_EMOJI.get(slot, "🍽️")
        labels.append(f"{emoji} {slot}")
    if len(dishes) == 1:
        render_dish_card(dishes[0], labels[0])
        return
    tabs = st.tabs(labels)
    for tab, dish, label in zip(tabs, dishes, labels):
        with tab:
            render_dish_card(dish, label)


def render_nutrient_boost_recipe_panel(boost):
    """栄養補完レシピの表示と、買い物・タイマー・お気に入り・調理完了"""
    if not isinstance(boost, dict) or not boost:
        return
    boost = normalize_meal_recipe(boost)
    st.session_state["nutrient_boost_recipe"] = boost

    st.divider()
    col_h1, col_h2 = st.columns([4, 1])
    col_h1.subheader(f"🥗 栄養補完レシピ　{boost.get('recipe_name', '追加の一品')}")
    if col_h2.button("⭐ お気に入りに保存", key="fav_boost_recipe"):
        save_recipe(boost, is_favorite=1)
        st.toast("栄養補完レシピをお気に入りに保存しました！", icon="⭐")

    targets = boost.get("boosts_nutrients") or []
    if targets:
        st.caption("補う栄養素: " + " ／ ".join(targets))
    col_b1, col_b2, col_b3 = st.columns(3)
    col_b1.info(f"⏱️ {boost.get('time', '10〜15分')}")
    col_b2.success(f"🍽️ {boost.get('category', '追加一品')}")
    col_b3.info(f"👥 {boost.get('servings', selected_servings())}")
    if boost.get("composition"):
        st.caption("構成: " + " ／ ".join(boost.get("composition")))

    render_meal_dishes(boost, key_prefix="boost")
    render_current_shortage_panel(boost, "add_missing_boost_shop")

    with st.expander("🔊 栄養補完レシピの音声調理・タイマー", expanded=False):
        render_cooking_timer_hud()
        voice_slots = boost.get("composition") or []
        slot_hint = "、".join([f"「{s}へ」" for s in voice_slots]) if voice_slots else "「品目名へ」"
        st.caption(
            f"品目の順に手順が進みます。「次へ」「前へ」{slot_hint}"
            "「3分タイマー」「タイマー止めて」が使えます。"
        )
        if st.session_state.get("boost_cook_ai_question") and st.session_state.get(
            "boost_cook_ai_answer"
        ):
            st.info(
                f"❓ {st.session_state['boost_cook_ai_question']}\n\n"
                f"**AI:** {st.session_state['boost_cook_ai_answer']}"
            )
        if st.session_state.get("boost_cook_ai_error"):
            st.caption(f"詳細: {st.session_state['boost_cook_ai_error']}")

        step_timers = extract_step_timer_suggestions(boost.get("steps") or [])
        if step_timers:
            st.write("手順から検出したタイマー")
            tcols = st.columns(min(4, len(step_timers)))
            for i, (tlabel, tsec) in enumerate(step_timers):
                with tcols[i % len(tcols)]:
                    if st.button(
                        f"⏱️ {tlabel}タイマーを開始",
                        key=f"step_timer_btn_boost_{tsec}_{i}",
                    ):
                        start_cooking_timer(tsec, tlabel)
                        st.rerun()

        cook_event = cooking_assistant_component(
            name=boost.get("recipe_name", "栄養補完レシピ"),
            steps=boost.get("steps") or ["手順が登録されていません"],
            speech_steps=boost.get("speech_steps") or [],
            step_roles=boost.get("step_roles") or [],
            dish_slots=boost.get("composition") or [],
            ingredients=boost.get("used_ingredients") or [],
            servings=boost.get("servings", selected_servings()),
            initialStep=int(st.session_state.get("boost_cook_step", 0) or 0),
            scale=float(st.session_state.get("boost_cook_scale", 1.0) or 1.0),
            aiAnswer=st.session_state.get("boost_cook_ai_answer", "") or "",
            speakId=st.session_state.get("boost_cook_ai_speak_id", "") or "",
            fromAi=bool(st.session_state.get("boost_cook_ai_answer")),
            key="cooking_assistant_boost_cmp",
            default=None,
        )
        cook_evt = consume_component_event(cook_event, "last_cook_boost_evt_ts")
        if cook_evt and str(cook_evt.get("text") or "").strip():
            user_speech = str(cook_evt.get("text")).strip()
            if handle_timer_command(user_speech):
                st.rerun()
            else:
                apply_cooking_ai_question(
                    user_speech,
                    boost,
                    step=cook_evt.get("step"),
                    scale=cook_evt.get("scale"),
                    state_prefix="boost_",
                )
                st.rerun()

        fallback_q = st.text_input(
            "❓ キーボードでAIに質問 / タイマー",
            placeholder="例: 3分タイマー / しょうゆの量は？ / タイマー止めて",
            key="cook_boost_q_text",
        )
        if st.button("質問して音声で回答", key="cook_boost_q_btn"):
            if not (fallback_q or "").strip():
                st.warning("質問またはタイマー指示を入力してください。")
            elif handle_timer_command(fallback_q):
                st.rerun()
            else:
                apply_cooking_ai_question(fallback_q, boost, state_prefix="boost_")
                st.rerun()

    if st.button(
        "🍽️ この料理を作った！（在庫を自動で更新する）",
        type="primary",
        key="cooked_boost_recipe",
    ):
        depleted = apply_cook_complete(boost)
        st.balloons()
        if depleted:
            names = "、".join(x["name"] for x in depleted)
            st.success(
                f"栄養補完レシピの調理完了！在庫を減算更新しました。在庫切れ（{names}）を買い物リストに追加しました。"
            )
        else:
            st.success("栄養補完レシピの調理完了！在庫を減算更新しました！")
        st.session_state.pop("nutrient_boost_recipe", None)
        st.rerun()


def render_expiry_alert_panel(key_prefix):
    """在庫タブ用: 賞味期限アラートのみ（レスキュー生成はレシピタブ）"""
    df_inv = get_inventory()
    expired, soon = get_expiry_alert_items(df_inv)
    if not expired and not soon:
        return

    with st.expander("🚨 賞味期限アラート", expanded=True):
        if expired:
            lines = []
            for x in expired:
                over = abs(x["days_left"])
                lines.append(
                    f"- **{x['name']}**（{format_qty(x['quantity'])}{x['unit']}）: "
                    f"**{over}日超過**（期限 {x['expiry_date']}）"
                )
            st.error("賞味期限切れの食材があります\n\n" + "\n".join(lines))

        if soon:
            lines = []
            for x in soon:
                if x["days_left"] == 0:
                    remain = "今日まで"
                else:
                    remain = f"残り{x['days_left']}日"
                lines.append(
                    f"- **{x['name']}**（{format_qty(x['quantity'])}{x['unit']}）: "
                    f"**{remain}**（期限 {x['expiry_date']}）"
                )
            st.warning("賞味期限が近い食材（今日〜3日以内）があります\n\n" + "\n".join(lines))
        st.caption("期限間近の食材で献立を作る場合は「🍳 AIレシピ提案」タブの残り物レスキューを使ってください。")


def render_leftover_rescue_panel():
    """AIレシピ提案タブ用: 残り物レスキュー生成"""
    df_inv = get_inventory()
    expired, soon = get_expiry_alert_items(df_inv)
    if not expired and not soon:
        return

    with st.expander("🚨 残り物レスキュー（期限が近い食材で献立作成）", expanded=True):
        names = [x["name"] for x in (expired + soon)]
        st.caption("対象: " + "、".join(names))
        st.caption("人数・献立構成は、このタブ上部の設定が使われます。")
        if st.button("🚨 期限が近い食材でレシピを自動作成", type="primary", key="rescue_btn_recipe"):
            if not st.session_state.get("gemini_key"):
                st.error(GEMINI_KEY_MISSING_MSG)
                return
            if df_inv.empty:
                st.warning("在庫がありません。")
                return
            slots = selected_meal_slots("recipe")
            if not slots:
                st.warning("少なくとも1つ選択してください")
                return
            try:
                inv_fp = [
                    (int(r["id"]), r["name"], float(r["quantity"]))
                    for _, r in df_inv.iterrows()
                ]
                sig = _ai_signature(
                    "rescue",
                    [(x.get("name"), x.get("expiry_date")) for x in (expired + soon)],
                    inv_fp,
                    slots,
                )
                res_json, cached = _ai_run_cached(
                    "recipe_result",
                    sig,
                    lambda: generate_leftover_rescue_recipe(
                        expired + soon, df_inv, st.session_state["gemini_key"], meal_slots=slots
                    ),
                )
                st.session_state["cook_step"] = 0
                st.session_state["cook_scale"] = 1.0
                st.session_state["rescue_recipe_ready"] = True
                st.session_state.pop("nutrient_boost_recipe", None)
                st.session_state["recipe_opened_from_history"] = False
                if not cached:
                    save_recipe(res_json, is_favorite=0)
                st.rerun()
            except Exception as e:
                _ai_exc(e)


def _coerce_calendar_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def ensure_calendar_date_state():
    """開始日・終了日の初期値。旧い期間タプルがあれば引き継ぐ。"""
    if "cal_start_date" in st.session_state and "cal_end_date" in st.session_state:
        return
    today = date.today()
    start, end = today, today + timedelta(days=2)
    raw = st.session_state.get("cal_date_range")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        start = _coerce_calendar_date(raw[0]) or start
        end = _coerce_calendar_date(raw[1]) or end
    elif isinstance(raw, (date, datetime, str)):
        one = _coerce_calendar_date(raw)
        if one:
            start = end = one
    if "cal_start_date" not in st.session_state:
        st.session_state["cal_start_date"] = start
    if "cal_end_date" not in st.session_state:
        st.session_state["cal_end_date"] = end


def read_calendar_date_selection():
    """ユーザーが選んだ開始日と終了日（日数の上限はかけない）"""
    ensure_calendar_date_state()
    today = date.today()
    start = _coerce_calendar_date(st.session_state.get("cal_start_date")) or today
    end = _coerce_calendar_date(st.session_state.get("cal_end_date")) or start
    if start > end:
        start, end = end, start
    return start, end


def resolve_calendar_date_range():
    """献立に使う開始日〜終了日（最大14日）"""
    start, end = read_calendar_date_selection()
    if (end - start).days > 13:
        end = start + timedelta(days=13)
    return start, end


def iter_calendar_dates(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def get_calendar_schedule(start=None, end=None):
    """選択日付ごとの必要な食事枠（チェックONのものだけ）"""
    if start is None or end is None:
        start, end = resolve_calendar_date_range()
    schedule = []
    for i, d in enumerate(iter_calendar_dates(start, end)):
        iso = d.strftime("%Y-%m-%d")
        meals = []
        for key, _label in MEAL_CALENDAR_SLOTS:
            if st.session_state.get(f"cal_slot_{iso}_{key}", True):
                meals.append(key)
        schedule.append(
            {
                "date": iso,
                "weekday": WEEKDAY_JA[d.weekday()],
                "day_index": i,
                "meals": meals,
                "request": str(st.session_state.get(f"cal_day_req_{iso}") or "").strip(),
            }
        )
    return schedule


def render_meal_calendar_panel():
    """予算と在庫を連動させた自動献立カレンダー"""
    st.header("📅 自動献立カレンダー")
    st.caption("開始日と終了日を選び、その範囲の朝・昼・晩と自由リクエストを指定して献立を作ります。")

    ensure_calendar_date_state()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.date_input(
            "開始日",
            key="cal_start_date",
            help="献立の最初の日です。",
        )
    with c2:
        st.date_input(
            "終了日",
            key="cal_end_date",
            help="献立の最後の日です。開始日より前を選ぶと、自動で入れ替えます。",
        )
    with c3:
        extra_budget = st.number_input(
            "使える追加予算（円）",
            min_value=0,
            value=2000,
            step=100,
            key="cal_extra_budget",
        )
    raw_start, raw_end = read_calendar_date_selection()
    start, end = resolve_calendar_date_range()
    span = (end - start).days + 1
    if raw_end != end or raw_start != start:
        st.warning(
            f"対象期間は最大14日です。"
            f"{start.strftime('%Y/%m/%d')} から {end.strftime('%Y/%m/%d')} までを対象にします。"
        )
    st.caption(
        f"選択中: {start.strftime('%Y/%m/%d')} 〜 {end.strftime('%Y/%m/%d')}（{span}日間）"
    )

    st.text_area(
        "全体への自由リクエスト",
        key="cal_free_request",
        height=70,
        placeholder="例: できるだけ和食、金曜は外食なので夕食なし（日付ごとの欄でも指定可）",
        help="期間全体に共通する希望です。日付ごとの自由リクエストと合わせてAIに渡します。",
    )

    st.subheader("日ごとの食事枠と自由リクエスト")
    st.caption("選んだ日付のぶんだけ、朝・昼・晩のチェックと自由リクエスト欄を出します。外食などで不要な時間帯はチェックを外してください。")
    for i, d in enumerate(iter_calendar_dates(start, end)):
        iso = d.strftime("%Y-%m-%d")
        with st.container(border=True):
            st.markdown(
                f"**{i + 1}日目　{d.strftime('%Y/%m/%d')}（{WEEKDAY_JA[d.weekday()]}）**"
            )
            dc1, dc2, dc3 = st.columns(3)
            dc1.checkbox("朝", value=True, key=f"cal_slot_{iso}_breakfast")
            dc2.checkbox("昼", value=True, key=f"cal_slot_{iso}_lunch")
            dc3.checkbox("晩", value=True, key=f"cal_slot_{iso}_dinner")
            st.text_input(
                "自由リクエスト",
                key=f"cal_day_req_{iso}",
                placeholder="例: 晩はパスタ / 昼は外食",
            )

    if st.button("献立を自動生成", type="primary", key="cal_generate"):
        if not st.session_state.get("gemini_key"):
            st.error(GEMINI_KEY_MISSING_MSG)
        else:
            df_inv = get_inventory()
            if df_inv.empty:
                st.warning("在庫がありません。先に在庫を登録してください。")
            else:
                schedule = get_calendar_schedule(start, end)
                if not any(day.get("meals") for day in schedule):
                    st.warning("少なくとも1つの食事枠（朝食・昼食・夕食）を選んでください。")
                else:
                    day_notes = "／".join(
                        f"{day['date']}: {day['request']}" for day in schedule if day.get("request")
                    )
                    overall = st.session_state.get("cal_free_request") or ""
                    free_request = overall
                    if day_notes:
                        free_request = (overall + "\n" + "日付ごとのリクエスト: " + day_notes).strip()
                    try:
                        inv_fp = [
                            (int(r["id"]), r["name"], float(r["quantity"]))
                            for _, r in df_inv.iterrows()
                        ]
                        sig = _ai_signature(
                            "calendar",
                            schedule,
                            extra_budget,
                            inv_fp,
                            free_request,
                        )
                        plan, _cached = _ai_run_cached(
                            "meal_calendar_plan",
                            sig,
                            lambda: generate_meal_calendar_with_ai(
                                schedule,
                                extra_budget,
                                df_inv,
                                st.session_state["gemini_key"],
                                free_request=free_request,
                            ),
                        )
                        plan["free_request"] = free_request
                        st.session_state["meal_calendar_plan"] = plan
                        st.rerun()
                    except Exception as e:
                        _ai_exc(e)

    plan = st.session_state.get("meal_calendar_plan")
    if not plan:
        st.info("期間と追加予算を決めて「献立を自動生成」を押してください。")
        return

    if plan.get("summary"):
        st.info(plan["summary"])
    if plan.get("free_request"):
        st.caption("反映したリクエスト: " + plan["free_request"])
    extra_total = int(plan.get("extra_total") or 0)
    budget = int(plan.get("budget") or extra_budget)
    t1, t2 = st.columns(2)
    t1.metric("買い足し概算合計", f"¥{extra_total:,}")
    t2.metric("指定した追加予算", f"¥{budget:,}")
    if extra_total > budget:
        st.warning("AIの買い足し合計が指定予算を超えています。必要なら期間や予算を変えて再生成してください。")

    day_cards = plan.get("days") or []
    if day_cards:
        st.subheader("献立カレンダー")
        for i in range(0, len(day_cards), 3):
            cols = st.columns(min(3, len(day_cards) - i))
            for col, day in zip(cols, day_cards[i : i + 3]):
                with col:
                    with st.container(border=True):
                        label = day.get("day_label") or ""
                        head = f"{label} " if label else ""
                        st.markdown(f"**{head}{day.get('date')}（{day.get('weekday')}）**")
                        for key, label in MEAL_CALENDAR_SLOTS:
                            meal = day.get(key) or {}
                            if meal.get("skipped"):
                                st.caption(f"{label}: 予定なし")
                                continue
                            st.markdown(f"**{label}** {meal.get('name') or '未設定'}")
                            uses = meal.get("uses_inventory") or []
                            if uses:
                                st.caption("在庫: " + "、".join(uses))
                            if meal.get("note"):
                                st.caption(meal["note"])

    st.divider()
    st.subheader("献立の修正リクエスト")
    st.text_area(
        "直したい点を書いてください",
        key="cal_revision_text",
        height=80,
        placeholder="例: 2日目の夕食を魚料理に変えて / 全体的に時短メニューにして",
    )
    if st.button("この内容でAIに再提案してもらう", key="cal_revise_btn"):
        req = (st.session_state.get("cal_revision_text") or "").strip()
        if not req:
            st.warning("修正リクエストを入力してください。")
        elif not st.session_state.get("gemini_key"):
            st.error(GEMINI_KEY_MISSING_MSG)
        else:
            try:
                sig = _ai_signature("calendar_revise", plan.get("summary"), req, plan.get("budget"))
                _ai_run_cached(
                    "meal_calendar_plan",
                    sig,
                    lambda: revise_meal_calendar_with_ai(
                        plan,
                        req,
                        st.session_state["gemini_key"],
                        extra_budget=plan.get("budget") or extra_budget,
                    ),
                )
                st.success("献立を更新しました。")
                st.rerun()
            except Exception as e:
                _ai_exc(e)

    extras = plan.get("extra_ingredients") or []
    st.subheader("不足している買い足し食材")
    if not extras:
        st.success("追加の買い足しは不要です。在庫だけで組めました。")
        return
    for item in extras:
        st.write(
            f"- **{item['name']}** {format_qty(item.get('quantity'))} {item.get('unit') or ''} "
            f"（カテゴリ: {item.get('category') or '未分類'}／概算 ¥{int(item.get('estimated_price') or 0):,}）"
        )
    if st.button("🛒 買い足し食材を買い物リストへ一括追加", type="primary", key="cal_add_shop"):
        added = 0
        for item in extras:
            if add_shopping_item(
                item["name"],
                category=item.get("category") or "加工食品・その他",
                added_reason="献立カレンダー",
                quantity=item.get("quantity") or 1,
                unit=item.get("unit") or "個",
                estimated_price=item.get("estimated_price"),
            ):
                added += 1
        if added:
            st.success(f"{added}件を買い物リストに追加しました（概算価格つき）。")
        else:
            st.info("すでにリストにあるか、追加できる食材がありませんでした。")


def format_duration_label(sec):
    sec = max(0, int(round(float(sec))))
    minutes, seconds = divmod(sec, 60)
    if minutes and seconds:
        return f"{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分"
    return f"{seconds}秒"


def parse_jp_duration_seconds(text):
    """『3分タイマー』『1分30秒測って』などから秒数を取り出す"""
    t = re.sub(r"[\s　]", "", str(text or ""))
    t = t.replace("分間", "分")
    m = re.search(r"(\d+(?:\.\d+)?)分(\d+(?:\.\d+)?)秒", t)
    if m:
        return int(round(float(m.group(1)) * 60 + float(m.group(2))))
    m = re.search(r"(\d+(?:\.\d+)?)分", t)
    if m:
        return int(round(float(m.group(1)) * 60))
    m = re.search(r"(\d+(?:\.\d+)?)秒", t)
    if m:
        return int(round(float(m.group(1))))
    return None


def extract_step_timer_suggestions(steps):
    """手順テキストから分・秒の記述を拾い、タイマー候補にする"""
    found = []
    seen = set()
    for item in steps or []:
        text = item if isinstance(item, str) else str(item)
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*分\s*(\d+(?:\.\d+)?)\s*秒", text):
            sec = int(round(float(m.group(1)) * 60 + float(m.group(2))))
            if sec > 0 and sec not in seen:
                seen.add(sec)
                found.append((format_duration_label(sec), sec))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*分間?(?!間)(?!\s*\d)", text):
            # 「2人分」は除外
            start = m.start()
            if start > 0 and text[start - 1] == "人":
                continue
            sec = int(round(float(m.group(1)) * 60))
            if sec > 0 and sec not in seen:
                seen.add(sec)
                found.append((format_duration_label(sec), sec))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*秒", text):
            # すでに「分○秒」で取った秒は lookbehind できないので、直前が「分」ならスキップ
            start = m.start()
            if start >= 1 and text[max(0, start - 2):start].endswith("分"):
                continue
            sec = int(round(float(m.group(1))))
            if sec > 0 and sec not in seen:
                seen.add(sec)
                found.append((format_duration_label(sec), sec))
    return found


def start_cooking_timer(seconds, label=None):
    seconds = int(max(1, seconds))
    st.session_state["timer_end_ts"] = time.time() + seconds
    st.session_state["timer_total_sec"] = seconds
    st.session_state["timer_label"] = label or format_duration_label(seconds)
    st.session_state["timer_done_notified"] = False
    st.toast(f"{st.session_state['timer_label']}のタイマーを開始しました", icon="⏱️")


def cancel_cooking_timer():
    st.session_state["timer_end_ts"] = None
    st.session_state["timer_total_sec"] = 0
    st.session_state["timer_label"] = ""
    st.session_state["timer_done_notified"] = False
    st.toast("タイマーを停止しました", icon="⏹️")


def handle_timer_command(user_speech):
    """音声・テキストがタイマー操作なら True（開始／停止）。それ以外は False。"""
    raw = str(user_speech or "").strip()
    if not raw:
        return False
    compact = re.sub(r"[\s　]", "", raw)
    if re.search(
        r"タイマー.{0,8}(止め|停止|キャンセル|解除|消し)|"
        r"(止め|停止|キャンセル).{0,8}タイマー|"
        r"タイマーストップ",
        compact,
    ):
        cancel_cooking_timer()
        return True

    is_timer_phrase = bool(
        re.search(r"タイマー|測って|はかって|セット|カウント|アラーム", compact)
    )
    seconds = parse_jp_duration_seconds(compact)
    if seconds and seconds > 0 and is_timer_phrase:
        start_cooking_timer(seconds)
        return True
    return False


@st.fragment(run_every=1)
def render_cooking_timer_hud():
    """稼働中タイマーの残り時間とプログレスバーを1秒ごとに更新する"""
    end_ts = st.session_state.get("timer_end_ts")
    if not end_ts:
        return

    total = float(st.session_state.get("timer_total_sec") or 1) or 1.0
    remain = end_ts - time.time()
    label = st.session_state.get("timer_label") or "タイマー"

    if remain <= 0:
        st.error(f"⏰ {label} 終了！")
        st.progress(1.0)
        if not st.session_state.get("timer_done_notified"):
            st.session_state["timer_done_notified"] = True
            st.session_state["timer_end_ts"] = None
            st.balloons()
        return

    remain_i = int(math.ceil(remain))
    minutes, seconds = divmod(remain_i, 60)
    elapsed_ratio = min(1.0, max(0.0, 1.0 - (remain / total)))
    st.info(f"⏱️ **{label}**  残り **{minutes:02d}:{seconds:02d}**")
    st.progress(elapsed_ratio)
    if st.button("⏹️ タイマー停止", key="timer_stop_btn"):
        cancel_cooking_timer()
        st.rerun()


_COMPONENT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "st_components")
cooking_assistant_component = components.declare_component(
    "cooking_assistant",
    path=os.path.join(_COMPONENT_ROOT, "cooking_assistant"),
)


def consume_component_event(event, ts_key):
    """コンポーネントからの新規イベントだけを取り出す（同じ ts の再処理を防ぐ）"""
    if not event:
        return None
    if isinstance(event, str):
        event = {"text": event, "ts": None}
    if not isinstance(event, dict):
        return None
    ts = event.get("ts")
    if ts is not None and ts == st.session_state.get(ts_key):
        return None
    if ts is not None:
        st.session_state[ts_key] = ts
    return event

def apply_cooking_ai_question(question, recipe, step=None, scale=None, state_prefix=""):
    step_key = f"{state_prefix}cook_step"
    scale_key = f"{state_prefix}cook_scale"
    q_key = f"{state_prefix}cook_ai_question"
    a_key = f"{state_prefix}cook_ai_answer"
    speak_key = f"{state_prefix}cook_ai_speak_id"
    err_key = f"{state_prefix}cook_ai_error"
    if step is not None:
        try:
            st.session_state[step_key] = int(step)
        except Exception:
            pass
    if scale is not None:
        try:
            st.session_state[scale_key] = float(scale)
        except Exception:
            pass
    question = (question or "").strip()
    if not question:
        return
    q_sig = _ai_signature("cook_q", question, recipe.get("recipe_name"), step, scale)
    q_sig_key = f"{state_prefix}cook_ai_q_sig"
    if st.session_state.get(q_sig_key) == q_sig and st.session_state.get(a_key):
        return
    st.session_state[q_key] = question
    if not st.session_state.get("gemini_key"):
        st.session_state[a_key] = GEMINI_KEY_MISSING_MSG
        st.session_state[speak_key] = str(uuid.uuid4())
        return
    try:
        with ai_spinner("調理中の質問にAIが回答中..."):
            st.session_state[a_key] = ask_cooking_question_with_ai(
                question, recipe, st.session_state["gemini_key"]
            )
        st.session_state[speak_key] = str(uuid.uuid4())
        st.session_state[q_sig_key] = q_sig
        st.session_state.pop(err_key, None)
    except ApiQuotaExceeded as e:
        st.session_state[a_key] = str(e)
        st.session_state[speak_key] = str(uuid.uuid4())
        st.session_state.pop(err_key, None)
    except Exception:
        st.error(AI_GENERIC_ERROR)
        st.session_state[a_key] = "すみません、今は質問に答えられませんでした。"
        st.session_state[speak_key] = str(uuid.uuid4())
        st.session_state.pop(err_key, None)


_USER_SCOPED_SESSION_KEYS = (
    "recipe_result",
    "nutrition_result",
    "meal_calendar_plan",
    "parsed_items",
    "nutrition_priority_ingredients",
    "nutrition_handoff",
    "rescue_recipe_ready",
    "nutrient_boost_recipe",
    "recipe_opened_from_history",
    "editing_inventory_key",
    "shop_edit_id",
    "detected_cat",
    "cook_step",
    "cook_scale",
    "cook_ai_question",
    "cook_ai_answer",
    "cook_ai_error",
    "boost_cook_step",
    "boost_cook_scale",
    "boost_cook_ai_question",
    "boost_cook_ai_answer",
    "boost_cook_ai_error",
    "pending_recipe_free_request",
    "recipe_free_request",
    "profile_loaded_for",
    "bulk_voice_text",
    "inv_price_hint",
    "inv_audio_digest",
    "recipe_result__sig",
    "nutrition_result__sig",
    "meal_calendar_plan__sig",
    "parsed_items__sig",
    "nutrient_boost_recipe__sig",
    "cook_ai_q_sig",
    "boost_cook_ai_q_sig",
)


def _reset_user_scoped_session():
    for key in _USER_SCOPED_SESSION_KEYS:
        st.session_state.pop(key, None)
    st.session_state["allergies"] = ""
    st.session_state["meal_styles"] = []
    st.session_state["owned_seasonings"] = list(DEFAULT_OWNED_SEASONINGS)
    st.session_state["monthly_food_budget"] = 20000
    st.session_state["user_gender"] = DEFAULT_PROFILE_GENDER
    st.session_state["user_age_group"] = DEFAULT_PROFILE_AGE_GROUP
    st.session_state["user_activity_level"] = DEFAULT_PROFILE_ACTIVITY


def render_admin_control_panel():
    """管理者だけに出すホスト用コントロール。一般ユーザーの描画経路には置かない。"""
    st.subheader("👑 管理者コントロールパネル")
    if "admin_maintenance_toggle" not in st.session_state:
        st.session_state["admin_maintenance_toggle"] = is_maintenance_mode()
    new_maint = st.checkbox(
        "緊急メンテナンスモード",
        key="admin_maintenance_toggle",
        help="オンにすると、管理者以外はアプリを使えなくなります。",
    )
    if new_maint != is_maintenance_mode():
        set_maintenance_mode(new_maint)
        st.rerun()
    st.caption("現在: " + ("メンテナンス中" if is_maintenance_mode() else "公開中"))

    expire_at = get_demo_expiration_datetime()
    if "admin_demo_expire_date" not in st.session_state:
        st.session_state["admin_demo_expire_date"] = expire_at.date()
    new_expire = st.date_input(
        "デモ利用期限",
        key="admin_demo_expire_date",
        help="指定日の 23:59:59 まで利用できます。",
    )
    if st.button("デモ利用期限を保存", key="admin_save_expire"):
        set_demo_expiration_date(new_expire)
        st.success(f"デモ利用期限を {new_expire.isoformat()} 23:59:59 に更新しました。")
        st.rerun()
    st.caption(f"既定値: {DEMO_EXPIRATION_DATE} ／ 現在の期限: {get_demo_expiration_datetime()}")

    st.markdown("##### ユーザー＆データ管理")
    users = list_all_users()
    if not users:
        st.info("登録ユーザーはいません。")
    else:
        st.dataframe(
            pd.DataFrame(users).rename(
                columns={
                    "id": "ID",
                    "username": "ユーザー名",
                    "created_at": "登録日時",
                    "inventory_count": "在庫",
                    "recipe_count": "レシピ",
                    "shopping_count": "買い物",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        options = {f"{u['id']}: {u['username']}": u["id"] for u in users}
        selected_label = st.selectbox(
            "削除するユーザー",
            list(options.keys()),
            key="admin_delete_user_select",
        )
        current_uid = st.session_state.get("auth_user_id")
        confirm = st.checkbox(
            "このユーザーの在庫・レシピ・買い物・履歴をすべて削除することを確認しました。この操作は取り消せません。",
            key="admin_delete_user_confirm",
        )
        if st.button("このユーザーのデータを削除する", type="primary", key="admin_delete_user_btn"):
            target_uid = options[selected_label]
            if current_uid is not None and int(target_uid) == int(current_uid):
                st.error("ログイン中の自分自身は削除できません。")
            elif not confirm:
                st.error("先に確認チェックを入れてください。")
            else:
                delete_user_and_data(target_uid)
                st.success("ユーザーと関連データを削除しました。")
                st.rerun()

    st.markdown("##### DBバックアップ")
    db_path = get_db_path()
    if os.path.isfile(db_path):
        st.download_button(
            "inventory.db をダウンロード",
            data=export_sqlite_bytes(),
            file_name="inventory.db",
            mime="application/x-sqlite3",
            key="admin_db_download",
        )
        st.caption(db_path)
    else:
        st.warning("inventory.db が見つかりません。")


def _apply_logged_in_profile():
    uid = st.session_state.get("auth_user_id")
    if not uid:
        return
    if st.session_state.get("profile_loaded_for") == uid:
        return
    profile = load_user_profile()
    gender = profile.get("gender") or ""
    age_group = profile.get("age_group") or ""
    activity = profile.get("activity_level") or ""
    st.session_state["user_gender"] = gender if gender in GENDER_OPTIONS else DEFAULT_PROFILE_GENDER
    st.session_state["user_age_group"] = (
        age_group if age_group in AGE_GROUP_OPTIONS else DEFAULT_PROFILE_AGE_GROUP
    )
    st.session_state["user_activity_level"] = (
        activity if activity in ACTIVITY_LEVEL_OPTIONS else DEFAULT_PROFILE_ACTIVITY
    )
    st.session_state["profile_loaded_for"] = uid


# --------------------------------------------------
# 4. サイドバー
# --------------------------------------------------
_configured_key, _configured_source = get_configured_gemini_key()
if _configured_key:
    st.session_state["gemini_key"] = _configured_key
    st.session_state["gemini_key_source"] = _configured_source
else:
    st.sidebar.title("⚙️ 設定")
    api_key_input = st.sidebar.text_input(
        "Gemini API Key",
        type="password",
        key="gemini_key_manual_input",
    )
    st.session_state["gemini_key"] = get_gemini_api_key(api_key_input)
    st.session_state["gemini_key_source"] = "manual" if st.session_state["gemini_key"] else "none"
    st.sidebar.divider()
st.sidebar.header("👤 ユーザー認証")
if st.session_state.get("auth_user_id"):
    login_label = st.session_state.get("auth_username") or ""
    if st.session_state.get("is_admin"):
        st.sidebar.success(f"ログイン中: {login_label}（管理者）")
    else:
        st.sidebar.success(f"ログイン中: {login_label}")
    if st.sidebar.button("ログアウト", use_container_width=True):
        st.session_state["auth_user_id"] = None
        st.session_state["auth_username"] = ""
        st.session_state["is_admin"] = False
        st.session_state["admin_username"] = ""
        _reset_user_scoped_session()
        st.rerun()
else:
    with st.sidebar.form("auth_login_form", clear_on_submit=False):
        login_name = st.text_input("ユーザー名", placeholder="")
        login_pin = st.text_input(
            "暗証キー",
            type="password",
            help="初めてのユーザー名は、この暗証キーで自動登録されます。他人には教えないでください。",
        )
        login_submit = st.form_submit_button("ログイン / 利用開始", use_container_width=True)
    if login_submit:
        name = str(login_name or "").strip()
        if is_admin_credentials(name, login_pin):
            _reset_user_scoped_session()
            st.session_state["is_admin"] = True
            st.session_state["admin_username"] = name
            st.session_state["auth_user_id"] = get_or_create_user_id(name)
            st.session_state["auth_username"] = name
            _apply_logged_in_profile()
            st.rerun()
        elif is_admin_username(name):
            st.sidebar.error("暗証キーが違います。")
        else:
            st.session_state["is_admin"] = False
            uid, err = login_or_register_user(login_name, login_pin)
            if err:
                st.sidebar.error(err)
            else:
                _reset_user_scoped_session()
                st.session_state["auth_user_id"] = uid
                st.session_state["auth_username"] = name
                _apply_logged_in_profile()
                st.rerun()

if not st.session_state.get("auth_user_id"):
    st.sidebar.divider()
    render_disclaimer_and_feedback()
    st.title("Smart Kitchen AI")
    st.caption("〜冷蔵庫の在庫からつくる、予算と健康に優しいスマート食生活アシスタント〜")
    st.info("ユーザー名と暗証キーを入力してログインしてください")
    st.caption("初めてのユーザー名は、入力した暗証キーで自動登録されます。既存の名前は暗証キーが一致する場合のみ入れます。")
    st.stop()

_apply_logged_in_profile()

st.sidebar.divider()
st.sidebar.header("👤 パーソナライズ設定")
st.sidebar.selectbox(
    "性別",
    GENDER_OPTIONS,
    key="user_gender",
    help="栄養診断の推定エネルギー・鉄分・塩分などの基準に使います。",
)
st.sidebar.selectbox(
    "年代",
    AGE_GROUP_OPTIONS,
    key="user_age_group",
    help="食事摂取基準の年齢区分に対応する目安です。",
)
st.sidebar.selectbox(
    "身体活動レベル",
    ACTIVITY_LEVEL_OPTIONS,
    key="user_activity_level",
    help="低い＝デスクワーク中心、普通＝立ち仕事・通勤移動あり、高い＝運動習慣・力仕事。",
)
save_user_profile(
    st.session_state.get("user_gender"),
    st.session_state.get("user_age_group"),
    st.session_state.get("user_activity_level"),
)
st.sidebar.text_input(
    "アレルギー・NG食材",
    placeholder="例: 甲殻類、生玉ねぎ、辛いもの",
    key="allergies",
    help="カンマ区切りで複数指定できます。レシピには一切使いません。",
)
st.sidebar.multiselect(
    "食事スタイル",
    MEAL_STYLE_OPTIONS,
    key="meal_styles",
    help="選んだ条件は、AIレシピ提案と自動献立カレンダーに反映されます。複数選択可。",
)
st.sidebar.multiselect(
    "常備している基本調味料",
    BASIC_SEASONING_OPTIONS,
    key="owned_seasonings",
    help="サラダ油・オリーブオイルも含めて選べます。選んだ調味料は家にあるものとして、レシピと献立の買い足しには入れません。",
)
st.sidebar.number_input(
    "今月の食費予算（円）",
    min_value=0,
    step=500,
    key="monthly_food_budget",
    help="買い物リストの予想金額と、家計の今月支出を比べる基準です。",
)
st.sidebar.divider()
if st.session_state.get("is_admin"):
    st.sidebar.markdown("**👑 管理者コントロールパネル**")
    st.sidebar.caption("専用タブ「👑 管理者」から操作できます。一般ユーザーには表示されません。")
    st.sidebar.divider()
render_disclaimer_and_feedback()

st.title("Smart Kitchen AI")
st.caption("〜冷蔵庫の在庫からつくる、予算と健康に優しいスマート食生活アシスタント〜")

if st.session_state.get("rescue_recipe_ready"):
    st.success(
        "残り物レスキュー献立を作成しました。"
        "「🍳 AIレシピ提案」タブで品目ごとのレシピと音声調理モードが使えます。"
    )

_expired_home, _soon_home = get_expiry_alert_items()
if _expired_home or _soon_home:
    st.warning(
        f"賞味期限の確認が必要な食材が {len(_expired_home) + len(_soon_home)} 件あります。"
        "詳細とレスキューは「📦 在庫管理」タブを開いてください。"
    )

_shop_n = count_unbought_shopping_items()
if _shop_n:
    st.info(f"🛒 買い物リストに未購入が {_shop_n} 件あります。「🛒 買い物リスト」タブで確認できます。")

if st.session_state.get("nutrition_handoff"):
    st.success(
        "栄養診断のおすすめ食材をレシピ提案に引き継ぎました。"
        "「🍳 AIレシピ提案」タブで献立を作成してください。"
    )

# --------------------------------------------------
# 5. メイン画面（タブ切り替え）
# --------------------------------------------------
_tab_labels = [
    "📦 在庫管理",
    "🍳 AIレシピ提案",
    "🛒 買い物リスト",
    "📅 自動献立カレンダー",
    "📊 栄養診断",
    "💰 家計・支出",
]
if st.session_state.get("is_admin"):
    _tab_labels.append("👑 管理者")
_tabs = st.tabs(_tab_labels)
tab1, tab2, tab_shop, tab_cal, tab_nut, tab4 = _tabs[:6]

# ==========================================
# タブ 1: 在庫管理
# ==========================================
with tab1:
    st.header("在庫の登録・一覧")
    render_expiry_alert_panel("inventory")

    voice_bulk = _qp_get("voice_bulk")
    if voice_bulk:
        st.session_state["bulk_voice_text"] = voice_bulk
        _qp_delete("voice_bulk")

    inv_add_tab, inv_list_tab = st.tabs(["➕ 食材を追加", "📋 在庫一覧"])

    with inv_add_tab:
        top1, top2 = st.columns(2)
        with top1:
            with st.expander("✨ テキストでまとめて登録", expanded=True):
                bulk_input = st.text_input(
                    "食材をまとめて入力（例: にんじん 2本 128円, 玉ねぎ 3個 98円）"
                )
                if st.button("🪄 AIで一括解析する", type="primary"):
                    if not st.session_state["gemini_key"]:
                        st.error(GEMINI_KEY_MISSING_MSG)
                    elif not bulk_input.strip():
                        st.warning("食材テキストを入力してください。")
                    else:
                        try:
                            sig = _ai_signature("bulk", bulk_input.strip())
                            _ai_run_cached(
                                "parsed_items",
                                sig,
                                lambda: parse_bulk_input_with_ai(
                                    bulk_input, st.session_state["gemini_key"]
                                ),
                            )
                        except Exception as e:
                            _ai_exc(e)

        with top2:
            with st.expander("📷 レシートから登録", expanded=True):
                uploaded_file = st.file_uploader(
                    "レシート画像をアップロードしてください",
                    type=["jpg", "jpeg", "png"],
                    key="receipt_upload_inventory",
                )
                if uploaded_file is not None:
                    image = prepare_image_for_ai(Image.open(uploaded_file))
                    st.image(
                        image,
                        caption="アップロードされたレシート（長辺1024px以下に自動調整）",
                        use_container_width=True,
                    )
                    if st.button("🔍 レシートを解析して在庫登録へ", key="receipt_parse_inventory"):
                        if not st.session_state["gemini_key"]:
                            st.error(GEMINI_KEY_MISSING_MSG)
                        else:
                            with ai_spinner("レシート画像から食材を解析中..."):
                                try:
                                    sig = _ai_signature(
                                        "receipt",
                                        hashlib.sha256(image.tobytes()).hexdigest(),
                                    )
                                    _ai_run_cached(
                                        "parsed_items",
                                        sig,
                                        lambda: parse_receipt_with_ai(
                                            image, st.session_state["gemini_key"]
                                        ),
                                    )
                                    st.success("解析完了！下の確認画面から在庫一覧へ登録できます。")
                                    st.rerun()
                                except Exception as e:
                                    _ai_exc(e)
                else:
                    st.caption("写真を選ぶと、解析結果が下の確認画面に入り、在庫へ一括登録できます。")

        with st.expander("📝 AI解析結果の確認・編集", expanded=True):
            if "parsed_items" in st.session_state and st.session_state["parsed_items"]:
                st.info("賞味期限・数量・金額を確認してから登録してください。金額が空の食材は、在庫一覧で概算できます。")
                with st.form("bulk_add_form"):
                    confirmed_data = []
                    today = date.today()
                    for idx, item in enumerate(st.session_state["parsed_items"]):
                        st.markdown(f"**食材 {idx+1}**")
                        c1, c2, c3, c4, c5, c6 = st.columns([2, 1, 1, 2, 2, 1])
                        b_name = c1.text_input("名前", value=item.get("name"), key=f"b_name_{idx}")
                        b_qty = c2.text_input("数量", value=str(item.get("quantity", 1)), key=f"b_qty_{idx}")
                        b_unit = c3.text_input("単位", value=item.get("unit", "個"), key=f"b_unit_{idx}")
                        cat_idx = CATEGORY_LIST.index(item.get("category")) if item.get("category") in CATEGORY_LIST else 0
                        b_cat = c4.selectbox("カテゴリ", CATEGORY_LIST, index=cat_idx, key=f"b_cat_{idx}")
                        days = item.get("days_to_expiry", 7)
                        if not isinstance(days, (int, float)):
                            days = 7
                        default_exp = today + timedelta(days=int(days))
                        b_exp = c5.date_input("賞味期限", value=default_exp, key=f"b_exp_{idx}")
                        b_price = c6.number_input(
                            "金額(円)", min_value=0, step=1,
                            value=parse_price(item.get("price", 0)), key=f"b_price_{idx}",
                        )
                        confirmed_data.append({
                            "name": b_name, "quantity": b_qty, "unit": b_unit,
                            "category": b_cat, "expiry_date": b_exp, "price": b_price
                        })
                    if st.form_submit_button("✅ この内容でデータベースに登録"):
                        for p in confirmed_data:
                            add_item(p["name"], p["quantity"], p["unit"], p["category"], p["expiry_date"], p["price"])
                        n_zero = sum(1 for p in confirmed_data if parse_price(p["price"]) <= 0)
                        st.session_state["inv_price_hint"] = n_zero
                        st.success(f"{len(confirmed_data)} 件の食材をまとめて追加しました！")
                        if n_zero:
                            st.info(
                                f"金額が未入力の食材が {n_zero} 件あります。"
                                "「📋 在庫一覧」の「AIで概算価格を自動予測する」から補完できます。"
                            )
                        del st.session_state["parsed_items"]
                        st.rerun()
            else:
                st.caption("テキストまたはレシートで解析すると、ここに確認フォームが出ます。")

        bot1, bot2 = st.columns(2)
        with bot1:
            with st.expander("🎙️ 音声でまとめて登録", expanded=False):
                st.caption("マイクで食材と数量を話してください。同じ録音の再実行ではAIを呼びません。")
                audio_file = st.audio_input("食材を話す", key="inv_audio_input")
                if audio_file is not None:
                    audio_bytes = audio_file.getvalue()
                    audio_digest = hashlib.sha256(audio_bytes).hexdigest()
                    if audio_digest != st.session_state.get("inv_audio_digest"):
                        if not st.session_state.get("gemini_key"):
                            st.warning("音声は取得済みです。解析するには API Key を設定してください。")
                        else:
                            try:
                                _ai_run_cached(
                                    "parsed_items",
                                    audio_digest,
                                    lambda: parse_audio_inventory_with_ai(
                                        audio_bytes,
                                        st.session_state["gemini_key"],
                                        getattr(audio_file, "type", None) or "audio/wav",
                                    ),
                                )
                                st.session_state["inv_audio_digest"] = audio_digest
                                st.success("音声入力を解析しました。上の確認画面から登録できます。")
                            except Exception as e:
                                _ai_exc(e)
                    else:
                        st.caption("この録音は解析済みです。上の確認画面を使ってください。")
                st.text_area(
                    "テキストで補正（編集してから再解析できます）",
                    key="bulk_voice_text",
                    height=80,
                    placeholder="例: 牛肉300グラムと玉ねぎ2個と人参1本を買ったよ",
                )
                if st.button("🪄 音声テキストをAIで一括解析する"):
                    voice_text = (st.session_state.get("bulk_voice_text") or "").strip()
                    if not st.session_state["gemini_key"]:
                        st.error(GEMINI_KEY_MISSING_MSG)
                    elif not voice_text:
                        st.warning("先に音声入力するか、テキストを入力してください。")
                    else:
                        try:
                            sig = _ai_signature("voice_text", voice_text)
                            _ai_run_cached(
                                "parsed_items",
                                sig,
                                lambda: parse_bulk_input_with_ai(
                                    voice_text, st.session_state["gemini_key"]
                                ),
                            )
                        except Exception as e:
                            _ai_exc(e)

        with bot2:
            with st.expander("➕ 1件ずつ手動追加", expanded=False):
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    name_input = st.text_input("食材名（例: にんじん, しょうゆ）", key="manual_name_preview")
                with col_b:
                    st.write(" ")
                    st.write(" ")
                    ai_cat_btn = st.button("🪄 AIでカテゴリ自動判定")

                if "detected_cat" not in st.session_state:
                    st.session_state["detected_cat"] = CATEGORY_LIST[0]

                if ai_cat_btn and name_input:
                    if st.session_state["gemini_key"]:
                        try:
                            with ai_spinner("AIが処理中です..."):
                                st.session_state["detected_cat"] = categorize_item_with_ai(
                                    name_input, st.session_state["gemini_key"]
                                )
                            st.success(f"AI判定結果: **{st.session_state['detected_cat']}**")
                        except Exception as e:
                            _ai_exc(e)
                    else:
                        st.warning("APIキーを設定するとAI分類が使えます。")

                col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
                with col1:
                    item_name = st.text_input("確定用 食材名", value=name_input, key="manual_item_name")
                with col2:
                    quantity = st.text_input("数量", value="1", key="manual_item_qty")
                with col3:
                    unit = st.selectbox(
                        "単位",
                        ["個", "本", "玉", "パック", "g", "ml", "大さじ", "小さじ", "袋", "切れ", "その他"],
                        key="manual_item_unit",
                    )
                with col4:
                    cat_idx = (
                        CATEGORY_LIST.index(st.session_state["detected_cat"])
                        if st.session_state["detected_cat"] in CATEGORY_LIST
                        else 0
                    )
                    category = st.selectbox("カテゴリ", CATEGORY_LIST, index=cat_idx, key="manual_item_cat")
                col5, col6 = st.columns(2)
                with col5:
                    expiry_date = st.date_input("賞味期限", value=date.today(), key="manual_item_exp")
                with col6:
                    if "manual_item_price" not in st.session_state:
                        st.session_state["manual_item_price"] = 0
                    price = st.number_input(
                        "購入金額（円）",
                        min_value=0,
                        step=1,
                        key="manual_item_price",
                    )
                if st.button("在庫に追加する", key="manual_add_submit"):
                    if item_name:
                        add_item(item_name, quantity, unit, category, expiry_date, price)
                        if parse_price(price) <= 0:
                            st.session_state["inv_price_hint"] = int(
                                st.session_state.get("inv_price_hint") or 0
                            ) + 1
                        st.success(f"「{item_name}」を追加しました！")
                        st.rerun()
                    else:
                        st.error("食材名を入力してください。")

    with inv_list_tab:
        df = get_inventory()
        hint_n = int(st.session_state.get("inv_price_hint") or 0)
        if hint_n:
            st.info(
                f"登録した食材のうち金額が未入力のものが {hint_n} 件あります。"
                "下のボタンで概算価格を入れられます。"
            )
        zero_n = 0
        if df is not None and not df.empty:
            zero_n = int((df["price"].fillna(0).apply(parse_price) <= 0).sum())
        if zero_n:
            if st.button("AIで概算価格を自動予測する", key="inv_fill_prices"):
                filled = fill_missing_inventory_prices()
                st.session_state.pop("inv_price_hint", None)
                if filled:
                    st.success(f"{filled} 件の概算価格を在庫に反映しました。")
                else:
                    st.info("概算できる未入力の金額はありませんでした。")
                st.rerun()
        if df.empty:
            st.info("現在、在庫はありません。")
        else:
            cat_tabs = st.tabs(["全件表示"] + CATEGORY_LIST)
            with cat_tabs[0]:
                for _, row in df.iterrows():
                    render_inventory_item_editor(row, "all", compact=False)
            for idx, cat_name in enumerate(CATEGORY_LIST):
                with cat_tabs[idx + 1]:
                    sub_df = df[df["category"] == cat_name]
                    if sub_df.empty:
                        st.caption(f"「{cat_name}」の在庫はありません。")
                    else:
                        for _, row in sub_df.iterrows():
                            render_inventory_item_editor(row, cat_name, compact=True)

def render_ai_recipe_result_panel():
    """AIレシピ提案直後と同じ献立結果（お気に入り・不足・音声調理・調理完了）"""
    if "recipe_result" not in st.session_state:
        return
    recipe = normalize_meal_recipe(st.session_state["recipe_result"])
    st.session_state["recipe_result"] = recipe
    st.divider()

    col_head1, col_head2 = st.columns([4, 1])
    col_head1.subheader(f"📖 {recipe.get('recipe_name', '今日の献立')}")
    if col_head2.button("⭐ お気に入りに保存", key="fav_new_recipe"):
        save_recipe(recipe, is_favorite=1)
        st.toast("お気に入りに保存しました！", icon="⭐")

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.info(f"⏱️ {recipe.get('time', '不明')}")
    col_m2.success(f"🍽️ {recipe.get('category', '献立')}")
    col_m3.info(f"👥 {recipe.get('servings', selected_servings())}")
    if recipe.get("composition"):
        st.caption("構成: " + " ／ ".join(recipe.get("composition")))
    render_recipe_nutrition_snapshot(recipe)

    render_meal_dishes(recipe, key_prefix="result")
    render_current_shortage_panel(recipe, "add_missing_to_shop")

    st.divider()
    st.subheader("レシピの修正リクエスト")
    st.text_area(
        "直したい点を書いてください",
        key="recipe_revision_text",
        height=80,
        placeholder="例: メインの食材をもっと少なくして / 全体的に手軽な時短メニューにして",
    )
    if st.button("この内容でAIに再提案してもらう", key="recipe_revise_btn"):
        req = (st.session_state.get("recipe_revision_text") or "").strip()
        if not req:
            st.warning("修正リクエストを入力してください。")
        elif not st.session_state.get("gemini_key"):
            st.error(GEMINI_KEY_MISSING_MSG)
        else:
            try:
                sig = _ai_signature(
                    "recipe_revise",
                    recipe.get("recipe_name"),
                    recipe.get("steps"),
                    req,
                )
                updated, cached = _ai_run_cached(
                    "recipe_result",
                    sig,
                    lambda: revise_meal_recipe_with_ai(
                        recipe, req, st.session_state["gemini_key"]
                    ),
                )
                st.session_state["cook_step"] = 0
                st.session_state["cook_scale"] = 1.0
                st.session_state.pop("nutrient_boost_recipe", None)
                if not cached:
                    save_recipe(updated, is_favorite=0)
                st.success("レシピを更新しました。")
                st.rerun()
            except Exception as e:
                _ai_exc(e)

    st.divider()
    with st.expander("🔊 音声調理モードを開始", expanded=False):
        render_cooking_timer_hud()
        voice_slots = recipe.get("composition") or []
        slot_hint = "、".join([f"「{s}へ」" for s in voice_slots]) if voice_slots else "「品目名へ」"
        st.caption(
            f"品目の順に手順が進みます。「次へ」「前へ」{slot_hint}"
            "「3分タイマー」「タイマー止めて」が使えます。"
        )
        if st.session_state.get("cook_ai_question") and st.session_state.get("cook_ai_answer"):
            st.info(
                f"❓ {st.session_state['cook_ai_question']}\n\n"
                f"**AI:** {st.session_state['cook_ai_answer']}"
            )
        if st.session_state.get("cook_ai_error"):
            st.caption(f"詳細: {st.session_state['cook_ai_error']}")

        step_timers = extract_step_timer_suggestions(recipe.get("steps") or [])
        if step_timers:
            st.write("手順から検出したタイマー")
            tcols = st.columns(min(4, len(step_timers)))
            for i, (tlabel, tsec) in enumerate(step_timers):
                with tcols[i % len(tcols)]:
                    if st.button(
                        f"⏱️ {tlabel}タイマーを開始",
                        key=f"step_timer_btn_{tsec}_{i}",
                    ):
                        start_cooking_timer(tsec, tlabel)
                        st.rerun()

        cook_event = cooking_assistant_component(
            name=recipe.get("recipe_name", "レシピ"),
            steps=recipe.get("steps") or ["手順が登録されていません"],
            speech_steps=recipe.get("speech_steps") or [],
            step_roles=recipe.get("step_roles") or [],
            dish_slots=recipe.get("composition") or [],
            ingredients=recipe.get("used_ingredients") or [],
            servings=recipe.get("servings", selected_servings()),
            initialStep=int(st.session_state.get("cook_step", 0) or 0),
            scale=float(st.session_state.get("cook_scale", 1.0) or 1.0),
            aiAnswer=st.session_state.get("cook_ai_answer", "") or "",
            speakId=st.session_state.get("cook_ai_speak_id", "") or "",
            fromAi=bool(st.session_state.get("cook_ai_answer")),
            key="cooking_assistant_cmp",
            default=None,
        )
        cook_evt = consume_component_event(cook_event, "last_cook_evt_ts")
        if cook_evt and str(cook_evt.get("text") or "").strip():
            user_speech = str(cook_evt.get("text")).strip()
            if handle_timer_command(user_speech):
                st.rerun()
            else:
                apply_cooking_ai_question(
                    user_speech,
                    recipe,
                    step=cook_evt.get("step"),
                    scale=cook_evt.get("scale"),
                )
                st.rerun()

        fallback_q = st.text_input(
            "❓ キーボードでAIに質問 / タイマー",
            placeholder="例: 3分タイマー / しょうゆの量は？ / タイマー止めて",
            key="cook_q_text",
        )
        if st.button("質問して音声で回答", key="cook_q_btn"):
            if not (fallback_q or "").strip():
                st.warning("質問またはタイマー指示を入力してください。")
            elif handle_timer_command(fallback_q):
                st.rerun()
            else:
                apply_cooking_ai_question(fallback_q, recipe)
                st.rerun()

    st.divider()
    if st.button("🍽️ この料理を作った！（在庫を自動で更新する）", type="primary"):
        depleted = apply_cook_complete(recipe)
        st.balloons()
        if depleted:
            names = "、".join(x["name"] for x in depleted)
            st.success(
                f"調理完了！在庫を減算更新しました。在庫切れ（{names}）を買い物リストに追加しました。"
            )
        else:
            st.success("調理完了！在庫を減算更新しました！")
        del st.session_state["recipe_result"]
        st.session_state.pop("nutrient_boost_recipe", None)
        st.session_state.pop("recipe_opened_from_history", None)
        st.rerun()

    if st.session_state.get("nutrient_boost_recipe"):
        render_nutrient_boost_recipe_panel(
            st.session_state["nutrient_boost_recipe"]
        )


# ==========================================
# タブ 2: AIレシピ提案 & 履歴・お気に入り
# ==========================================
with tab2:
    cook_q = _qp_get("cook_ai_q")
    if cook_q:
        apply_cooking_ai_question(
            cook_q,
            st.session_state.get("recipe_result") or {},
            step=_qp_get("cook_step"),
            scale=_qp_get("cook_scale"),
        )
        _qp_delete("cook_ai_q", "cook_step", "cook_scale")

    recipe_sub_tab1, recipe_sub_tab2 = st.tabs(["🪄 新規提案 & 自動減算", "📚 履歴・お気に入り"])

    # ----------------------------------
    # サブタブ 2-1: 新規提案
    # ----------------------------------
    with recipe_sub_tab1:
        st.header("🍳 AIレシピ提案")
        df_inv = get_inventory()

        if df_inv.empty:
            st.warning("在庫がありません。まずは「在庫管理」タブで食材を登録してください。")
        else:
            st.subheader("1. 献立の条件")
            col_r1, col_r2, col_r3 = st.columns(3)
            with col_r1:
                genre = st.selectbox(
                    "ジャンル・気分",
                    ["指定なし", "和食", "洋食", "中華", "エスニック", "デザート・スイーツ", "おつまみ"],
                )
            with col_r2:
                cooking_time = st.selectbox(
                    "調理時間",
                    ["指定なし", "10分以内（時短）", "20分以内", "じっくり本格的"],
                )
            with col_r3:
                st.selectbox(
                    "何人分作るか",
                    SERVINGS_OPTIONS,
                    key="recipe_servings",
                    help="選択した品目すべて、この人数分で分量を計算します。",
                )

            st.multiselect(
                "献立の構成（複数選択可）",
                MEAL_SLOT_OPTIONS,
                key="recipe_meal_slots",
                help="作りたい品目だけを選んでください。デフォルトは主菜と副菜です。",
            )
            if "主食" in (st.session_state.get("recipe_meal_slots") or []):
                st.selectbox(
                    "主食のタイプ",
                    STAPLE_TYPE_OPTIONS,
                    key="recipe_staple_type",
                    help="主食を選んだときだけ、ご飯・麺・パンなどを指定できます。",
                )

            free_request = st.text_input(
                "追加のリクエスト（例: ガッツリ系がいい、包丁使わない、ヘルシーに）",
                key="recipe_free_request",
            )
            if st.session_state.get("nutrition_priority_ingredients"):
                st.caption(
                    "栄養診断からの優先食材: "
                    + "、".join(st.session_state["nutrition_priority_ingredients"])
                )

            render_leftover_rescue_panel()

            with st.expander("2. 使いたい食材（任意）", expanded=False):
                prioritize_expiry = st.checkbox(
                    "賞味期限が近い食材を優先して使う", value=True
                )
                inventory_options = {
                    f"{row['name']} ({format_qty(row['quantity'])}{row['unit']}) [カテゴリ: {row['category']}] [期限: {row['expiry_date']}]": row["id"]
                    for _, row in df_inv.iterrows()
                }
                selected_display = st.multiselect(
                    "必ず使いたい食材があれば選択（未選択ならAIが自動選択）",
                    list(inventory_options.keys()),
                )
            selected_ids = [inventory_options[k] for k in selected_display]

            if st.button("🪄 選択した構成で献立を提案してもらう", type="primary"):
                if not st.session_state["gemini_key"]:
                    st.error(GEMINI_KEY_MISSING_MSG)
                else:
                    slots = selected_meal_slots("recipe")
                    if not slots:
                        st.warning("少なくとも1つ選択してください")
                    else:
                        try:
                            inv_fp = [
                                (int(r["id"]), r["name"], float(r["quantity"]))
                                for _, r in df_inv.iterrows()
                            ]
                            sig = _ai_signature(
                                "recipe",
                                genre,
                                cooking_time,
                                free_request,
                                selected_ids,
                                prioritize_expiry,
                                st.session_state.get("nutrition_priority_ingredients"),
                                slots,
                                inv_fp,
                            )
                            res_json, cached = _ai_run_cached(
                                "recipe_result",
                                sig,
                                lambda: generate_recipe_from_inventory(
                                    df_inv,
                                    st.session_state["gemini_key"],
                                    genre=genre,
                                    cooking_time=cooking_time,
                                    free_request=free_request,
                                    selected_ids=selected_ids,
                                    prioritize_expiry=prioritize_expiry,
                                    nutrition_priority_ingredients=st.session_state.get(
                                        "nutrition_priority_ingredients"
                                    ),
                                    meal_slots=slots,
                                ),
                            )
                            st.session_state.pop("nutrient_boost_recipe", None)
                            st.session_state["recipe_opened_from_history"] = False
                            if not cached:
                                save_recipe(res_json, is_favorite=0)
                        except Exception as e:
                            _ai_exc(e)

            if not st.session_state.get("recipe_opened_from_history"):
                render_ai_recipe_result_panel()

    # ----------------------------------
    # サブタブ 2-2: 履歴 & お気に入り
    # ----------------------------------
    with recipe_sub_tab2:
        st.header("📚 レシピ履歴 & お気に入り")

        fav_filter = st.checkbox("⭐ お気に入りのみ表示", value=False)
        df_recipes = get_recipes(favorite_only=fav_filter)

        if df_recipes.empty:
            st.info("保存されたレシピ履歴はありません。")
        else:
            for _, r_row in df_recipes.iterrows():
                try:
                    details = normalize_meal_recipe(json.loads(r_row["details_json"]))
                except Exception:
                    details = {}

                is_fav = bool(r_row["is_favorite"])
                star = "⭐" if is_fav else "☆"
                rid = r_row["id"]

                row_c1, row_c2, row_c3 = st.columns([6, 1, 1])
                with row_c1:
                    st.write(f"**{star} {r_row['recipe_name']}**  \n{r_row['created_at']} ／ {r_row['category']}")
                if row_c2.button(star, key=f"fav_list_{rid}", help="お気に入り切り替え"):
                    toggle_favorite(rid, r_row["is_favorite"])
                    st.rerun()
                if row_c3.button("🗑️", key=f"del_list_{rid}", help="履歴から削除"):
                    delete_recipe(rid)
                    st.rerun()

                with st.expander("詳細を見る"):
                    st.write(f"**ジャンル:** {r_row['category']} | **調理時間:** {r_row['time']}")
                    if st.button(
                        "🍽️ この料理を作った！（在庫を自動減算）",
                        key=f"cooked_hist_{rid}",
                        type="primary",
                    ):
                        depleted = apply_cook_complete(details)
                        st.balloons()
                        if depleted:
                            names = "、".join(x["name"] for x in depleted)
                            st.success(
                                f"調理完了！在庫を減算更新しました。在庫切れ（{names}）を買い物リストに追加しました。"
                            )
                        else:
                            st.success("調理完了！在庫を減算更新しました！")
                        st.rerun()
                    if st.button("このレシピを作る", key=f"cook_hist_{rid}"):
                        st.session_state["recipe_result"] = normalize_meal_recipe(details)
                        st.session_state["cook_step"] = 0
                        st.session_state["cook_scale"] = 1.0
                        st.session_state.pop("nutrient_boost_recipe", None)
                        st.session_state["recipe_opened_from_history"] = True
                        st.rerun()
                    st.markdown("---")
                    render_meal_dishes(details, key_prefix=f"hist_{rid}")
                    render_current_shortage_panel(details, f"hist_shop_{rid}", show_ok=False)

        if st.session_state.get("recipe_opened_from_history"):
            render_ai_recipe_result_panel()

# ==========================================
# タブ: 買い物リスト
# ==========================================
with tab_shop:
    render_shopping_list_panel()

# ==========================================
# タブ: 自動献立カレンダー
# ==========================================
with tab_cal:
    render_meal_calendar_panel()

# ==========================================
# タブ: 栄養診断
# ==========================================
with tab_nut:
    render_nutrition_dashboard()

# ==========================================
# タブ 4: 家計・支出
# ==========================================
with tab4:
    st.header("💰 家計・支出")
    df_hist = get_purchase_history()
    now = datetime.now()

    if df_hist.empty:
        st.info("まだ購入履歴がありません。在庫登録やレシート読み取りから食材を追加すると、ここに支出が記録されます。")
    else:
        df_hist["price"] = df_hist["price"].apply(parse_price)
        parsed_dates = pd.to_datetime(df_hist["purchased_at"], errors="coerce")
        df_hist = df_hist.assign(_dt=parsed_dates)
        valid = df_hist.dropna(subset=["_dt"])
        months = sorted(valid["_dt"].dt.to_period("M").unique().tolist(), reverse=True)
        if not months:
            months = [pd.Period(now.strftime("%Y-%m"), freq="M")]
        current_period = pd.Period(now.strftime("%Y-%m"), freq="M")
        month_labels = [f"{p.year}年{p.month}月" for p in months]
        default_idx = month_labels.index(f"{current_period.year}年{current_period.month}月") if current_period in months else 0
        selected_label = st.selectbox(
            "表示する月",
            month_labels,
            index=default_idx,
            key="expense_month",
        )
        selected_period = months[month_labels.index(selected_label)]
        month_df = valid[valid["_dt"].dt.to_period("M") == selected_period]
        month_total = int(month_df["price"].sum()) if not month_df.empty else 0

        st.metric(
            label=f"{selected_period.year}年{selected_period.month}月の合計支出",
            value=f"¥{month_total:,}",
        )

        st.subheader("カテゴリごとの支出")
        if month_df.empty or month_df["price"].sum() == 0:
            st.caption("この月の支出データはありません。")
        else:
            cat_sum = (
                month_df.groupby("category", dropna=False)["price"]
                .sum()
                .reset_index()
                .rename(columns={"price": "支出額（円）"})
            )
            cat_sum["category"] = cat_sum["category"].fillna("未分類")
            pie_fig = px.pie(
                cat_sum,
                names="category",
                values="支出額（円）",
                title="カテゴリ別支出",
            )
            st.plotly_chart(pie_fig, use_container_width=True)

        st.subheader(f"{selected_period.year}年{selected_period.month}月の購入履歴")
        st.caption("金額の修正は入力後「保存」、誤登録は削除できます。")
        for _, prow in month_df.iterrows():
            pid = int(prow["id"])
            pc1, pc2, pc3, pc4 = st.columns([3, 2, 1, 1])
            pc1.write(f"**{prow['name']}**")
            pc1.caption(f"{prow.get('category') or '未分類'} ／ {prow.get('purchased_at')}")
            new_price = pc2.number_input(
                "金額（円）",
                min_value=0,
                step=1,
                value=int(parse_price(prow.get("price"))),
                key=f"hist_price_{pid}",
            )
            if pc3.button("保存", key=f"hist_price_save_{pid}"):
                update_purchase_price(pid, new_price)
                st.toast("金額を更新しました", icon="💰")
                st.rerun()
            if pc4.button("🗑️", key=f"hist_del_{pid}", help="この購入履歴を削除"):
                delete_purchase_history(pid)
                st.rerun()

if st.session_state.get("is_admin") and len(_tabs) > 6:
    with _tabs[6]:
        render_admin_control_panel()