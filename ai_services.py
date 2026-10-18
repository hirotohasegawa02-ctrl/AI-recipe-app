# -*- coding: utf-8 -*-
"""Gemini API 呼び出し、プロンプト組み立て、レシート/一括解析、レシピ生成。"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from io import BytesIO

import pandas as pd
import streamlit as st
from google import genai
from PIL import Image, ImageOps

from database import (
    CATEGORY_LIST,
    ApiQuotaExceeded,
    _ingredient_names_from_recipe,
    _normalize_shopping_name,
    consume_api_quota,
    estimate_ingredient_price,
    get_inventory,
    is_owned_seasoning,
    parse_price,
    parse_shopping_ingredient_entry,
)

MODEL_NAME = "gemini-3.6-flash"
GEMINI_API_KEY_NAME = "GEMINI_API_KEY"
GEMINI_KEY_MISSING_MSG = (
    "Gemini API Key が未設定です。"
    "Streamlit Secrets または環境変数 `GEMINI_API_KEY` を設定するか、サイドバーから入力してください。"
)
AI_SPINNER_DEFAULT = "AIが処理中です..."
AI_GENERIC_ERROR = (
    "AIの応答処理で一時的なエラーが発生しました。時間をおいて再度お試しください。"
)
AI_IMAGE_MAX_SIDE = 1024
_DOTENV_LOADED = False
_AI_SPINNER_DEPTH = 0


class GeminiCallError(RuntimeError):
    """Gemini 呼び出し失敗。ユーザーには生の例外文を出さない。"""


def _load_local_dotenv():
    """プロジェクト直下の `.env` を os.environ に取り込む（既存の環境変数は上書きしない）。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def _gemini_key_from_secrets():
    try:
        secrets = st.secrets
        if GEMINI_API_KEY_NAME in secrets:
            return str(secrets[GEMINI_API_KEY_NAME] or "").strip()
    except Exception:
        return ""
    return ""


def get_configured_gemini_key():
    """Secrets → 環境変数の順でキーを返す。どちらも無ければ空文字と none。"""
    _load_local_dotenv()
    secret_key = _gemini_key_from_secrets()
    if secret_key:
        return secret_key, "secrets"
    env_key = (os.environ.get(GEMINI_API_KEY_NAME) or "").strip()
    if env_key:
        return env_key, "env"
    return "", "none"


def get_gemini_api_key(manual_key=""):
    """APIキー取得: Streamlit Secrets → 環境変数 → 手動入力。"""
    configured, _source = get_configured_gemini_key()
    if configured:
        return configured
    manual = str(manual_key or "").strip()
    if manual:
        return manual
    try:
        return str(st.session_state.get("gemini_key") or "").strip()
    except Exception:
        return ""


@contextmanager
def ai_spinner(message=None):
    """Gemini呼び出し用スピナー。入れ子では内側を出さず、二重表示を避ける。"""
    global _AI_SPINNER_DEPTH
    text = (message or AI_SPINNER_DEFAULT).strip() or AI_SPINNER_DEFAULT
    _AI_SPINNER_DEPTH += 1
    try:
        if _AI_SPINNER_DEPTH == 1:
            with st.spinner(text):
                yield
        else:
            yield
    finally:
        _AI_SPINNER_DEPTH -= 1


def _gemini_generate(client, contents, spinner_message=None, is_image=False):
    ok, msg = consume_api_quota(is_image=is_image)
    if not ok:
        st.warning(msg)
        raise ApiQuotaExceeded(msg)
    try:
        with ai_spinner(spinner_message or AI_SPINNER_DEFAULT):
            return client.models.generate_content(model=MODEL_NAME, contents=contents)
    except ApiQuotaExceeded:
        raise
    except Exception as exc:
        raise GeminiCallError(AI_GENERIC_ERROR) from exc


def prepare_image_for_ai(image, max_side=AI_IMAGE_MAX_SIDE, quality=85):
    """長辺を max_side px 以下にリサイズし、JPEG圧縮してから返す。"""
    if image is None:
        return None
    try:
        image = ImageOps.exif_transpose(image) or image
    except Exception:
        pass
    img = image.convert("RGB")
    width, height = img.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / float(longest)
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        img = img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resample,
        )
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=int(quality), optimize=True)
    buffer.seek(0)
    out = Image.open(buffer)
    out.load()
    return out

MEAL_STYLE_OPTIONS = [
    "減塩",
    "低糖質",
    "低脂質",
    "低カロリー",
    "高タンパク",
]
MEAL_STYLE_HINTS = {
    "減塩": "塩分を控えめにし、醤油・塩・加工食品を増やさない",
    "低糖質": "ご飯・麺・砂糖・甘い味付けを控えめにする",
    "低脂質": "油・脂身・揚げ物を控えめにする",
    "低カロリー": "高カロリーな調理を避け、野菜中心で満腹感を出す",
    "高タンパク": "肉・魚・卵・大豆製品を十分に使う",
}
GENDER_OPTIONS = ["男性", "女性", "回答しない/その他"]
AGE_GROUP_OPTIONS = ["10代", "20代", "30代", "40代", "50代", "60代以上"]
ACTIVITY_LEVEL_OPTIONS = [
    "低い（デスクワーク中心）",
    "普通（立ち仕事・通勤移動あり）",
    "高い（運動習慣・力仕事）",
]
DEFAULT_PROFILE_GENDER = "回答しない/その他"
DEFAULT_PROFILE_AGE_GROUP = "30代"
DEFAULT_PROFILE_ACTIVITY = "普通（立ち仕事・通勤移動あり）"
SERVINGS_OPTIONS = ["1人分", "2人分", "3人分", "4人分"]
MEAL_SLOT_OPTIONS = ["主食", "主菜", "副菜", "汁物", "付け合わせ", "デザート"]
STAPLE_TYPE_OPTIONS = ["指定なし", "ご飯・米", "麺類", "パン", "粉もの（お好み焼き等）", "シリアル・その他"]
DEFAULT_MEAL_SLOTS = ["主菜", "副菜"]
MEAL_SLOT_EMOJI = {
    "主食": "🍚",
    "主菜": "🍖",
    "副菜": "🥗",
    "汁物": "🍲",
    "付け合わせ": "🥒",
    "デザート": "🍰",
}
MEAL_SLOT_YOMI = {
    "主食": "しゅしょく。",
    "主菜": "しゅさい。",
    "副菜": "ふくさい。",
    "汁物": "しるもの。",
    "付け合わせ": "つけあわせ。",
    "デザート": "でざーと。",
}
# Gemini / 旧JSONが使いがちなキー → 献立カテゴリ
MEAL_SLOT_ALIASES = {
    "主食": ["主食", "staple", "shushoku", "rice", "main_carb"],
    "主菜": ["主菜", "main", "main_dish"],
    "副菜": ["副菜", "side", "side_dish"],
    "汁物": ["汁物", "soup"],
    "付け合わせ": ["付け合わせ", "garnish", "accompaniment"],
    "デザート": ["デザート", "dessert"],
}

NUTRIENT_BOOST_SLOT_OPTIONS = ["副菜", "汁物", "付け合わせ", "デザート"]

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
MEAL_CALENDAR_SLOTS = [("breakfast", "朝"), ("lunch", "昼"), ("dinner", "晩")]


# 厚生労働省「日本人の食事摂取基準（2025年版）」を年代・性別・活動量に対応させた目安。
# 10代は15〜17歳、20代は18〜29歳、30代・40代は30〜49歳、50代は50〜64歳、60代以上は65〜74歳を代表値とする。
_ENERGY_KCAL = {
    "男性": {
        "10代": (2500, 2850, 3150),
        "20代": (2250, 2600, 3000),
        "30代": (2350, 2750, 3150),
        "40代": (2350, 2750, 3150),
        "50代": (2250, 2650, 3000),
        "60代以上": (2100, 2350, 2650),
    },
    "女性": {
        "10代": (2050, 2300, 2550),
        "20代": (1700, 1950, 2250),
        "30代": (1750, 2050, 2350),
        "40代": (1750, 2050, 2350),
        "50代": (1700, 1950, 2250),
        "60代以上": (1650, 1850, 2050),
    },
}
_PROTEIN_G = {
    "男性": {"10代": 65, "20代": 65, "30代": 65, "40代": 65, "50代": 65, "60代以上": 60},
    "女性": {"10代": 55, "20代": 50, "30代": 50, "40代": 50, "50代": 50, "60代以上": 50},
}
_IRON_MG = {
    "男性": {"10代": 9.0, "20代": 7.0, "30代": 7.5, "40代": 7.5, "50代": 7.0, "60代以上": 7.0},
    "女性": {"10代": 11.0, "20代": 10.0, "30代": 10.5, "40代": 10.5, "50代": 10.5, "60代以上": 6.0},
}
_SALT_G = {"男性": 7.5, "女性": 6.5}
_FIBER_G = {
    "男性": {"10代": 19, "20代": 20, "30代": 22, "40代": 22, "50代": 22, "60代以上": 21},
    "女性": {"10代": 18, "20代": 18, "30代": 18, "40代": 18, "50代": 18, "60代以上": 18},
}
_ACTIVITY_INDEX = {
    "低い（デスクワーク中心）": 0,
    "普通（立ち仕事・通勤移動あり）": 1,
    "高い（運動習慣・力仕事）": 2,
}


def _avg_pair(a, b):
    if isinstance(a, tuple):
        return tuple(int(round((x + y) / 2)) for x, y in zip(a, b))
    return round((float(a) + float(b)) / 2, 1)


def get_nutrition_reference(gender="", age_group="", activity_level=""):
    """性別・年代・活動量から、診断プロンプト用の基準値を返す。"""
    age = age_group if age_group in AGE_GROUP_OPTIONS else "30代"
    act_idx = _ACTIVITY_INDEX.get(activity_level, 1)
    if gender == "男性":
        sexes = ["男性"]
        salt = _SALT_G["男性"]
        pfc_note = "男性"
    elif gender == "女性":
        sexes = ["女性"]
        salt = _SALT_G["女性"]
        pfc_note = "女性"
    else:
        sexes = ["男性", "女性"]
        salt = round((_SALT_G["男性"] + _SALT_G["女性"]) / 2, 1)
        pfc_note = "性別未回答のため男女の中間値"

    def _pick(table):
        vals = [table[s][age] for s in sexes]
        if len(vals) == 1:
            return vals[0]
        return _avg_pair(vals[0], vals[1])

    energy_tuple = _pick(_ENERGY_KCAL)
    energy = int(energy_tuple[act_idx])
    protein = _pick(_PROTEIN_G)
    iron = _pick(_IRON_MG)
    fiber = _pick(_FIBER_G)
    if age in ("50代", "60代以上"):
        protein_pct = "14〜20%（65歳以上は15〜20%）" if age == "60代以上" else "14〜20%"
    else:
        protein_pct = "13〜20%"
    return {
        "gender_label": gender or "回答しない/その他",
        "age_group": age,
        "activity_level": activity_level or "普通（立ち仕事・通勤移動あり）",
        "energy_kcal": energy,
        "protein_g": protein,
        "fat_pct": "20〜30%",
        "carb_pct": "50〜65%",
        "protein_pct": protein_pct,
        "salt_g": salt,
        "iron_mg": iron,
        "fiber_g": fiber,
        "sex_note": pfc_note,
        "source": "日本人の食事摂取基準（2025年版）に基づく目安（身長・体重の個人差は考慮しない）",
    }


def format_nutrition_reference_block(ref):
    if not ref:
        return "（プロフィール未設定のため、成人の一般的な目安で評価）"
    return (
        f"対象: {ref['sex_note']}／{ref['age_group']}／身体活動レベル {ref['activity_level']}\n"
        f"- 推定エネルギー必要量: 約 {ref['energy_kcal']} kcal/日\n"
        f"- たんぱく質推奨量: 約 {ref['protein_g']} g/日（目標量 {ref['protein_pct']}エネルギー）\n"
        f"- 脂質: {ref['fat_pct']}エネルギー／炭水化物: {ref['carb_pct']}エネルギー\n"
        f"- 食塩相当量の目標量: {ref['salt_g']} g/日未満\n"
        f"- 鉄の推奨量: 約 {ref['iron_mg']} mg/日\n"
        f"- 食物繊維の目標量: 約 {ref['fiber_g']} g/日以上\n"
        f"- 出典: {ref['source']}"
    )


def get_ng_foods_text():
    try:
        return (st.session_state.get("allergies") or "").strip()
    except Exception:
        return ""


def allergy_ban_prompt_line():
    """全レシピ生成プロンプトに入れるアレルギー・NG食材の絶対遵守行。"""
    ng_foods = get_ng_foods_text() or "（指定なし）"
    return (
        "【絶対遵守】以下の食材・アレルギー物質は絶対にレシピや献立に使用しないでください: "
        f"{ng_foods}"
    )


def build_personalize_prompt_lines(servings, slots, extra_lines=None):
    """レシピ生成で共通利用するパーソナライズ行。"""
    lines = [
        composition_instruction(servings, slots),
        servings_calc_instruction(),
        allergy_ban_prompt_line(),
    ]
    style_line = meal_style_prompt_line()
    if style_line:
        lines.append(style_line)
    season_line = owned_seasonings_prompt_line()
    if season_line:
        lines.append(season_line)
    staple_line = staple_type_prompt_line(slots)
    if staple_line:
        lines.append(staple_line)
    for line in extra_lines or []:
        if line:
            lines.append(line)
    return lines


def selected_meal_styles():
    return [s for s in (st.session_state.get("meal_styles") or []) if s in MEAL_STYLE_OPTIONS]

def meal_style_prompt_line():
    styles = selected_meal_styles()
    if not styles:
        return None
    joined = "、".join(styles)
    rules = "／".join(MEAL_STYLE_HINTS.get(s, s) for s in styles)
    return f"【食事スタイル】{joined}。制約: {rules}。この条件に合う献立にすること。"

def owned_seasonings_prompt_line():
    owned = [x for x in (st.session_state.get("owned_seasonings") or []) if x]
    if not owned:
        return None
    joined = "、".join(owned)
    return (
        f"以下の常備調味料は家にあり、買い足し不要な食材です。"
        f"不足材料（missing_ingredients）や買い足し食材（extra_ingredients）には絶対に含めないでください: {joined}"
    )

def selected_staple_type():
    val = st.session_state.get("recipe_staple_type") or "指定なし"
    if val not in STAPLE_TYPE_OPTIONS:
        return "指定なし"
    return val

def staple_type_prompt_line(slots=None):
    slots = slots if slots is not None else selected_meal_slots("recipe")
    if "主食" not in (slots or []):
        return None
    staple = selected_staple_type()
    if staple == "指定なし":
        return "献立に主食を含めること。種類は在庫に合わせて選んでよい。"
    return f"献立の主食は「{staple}」を必ず提案してください（それ以外の主食タイプは出さない）。"

def predict_inventory_price(name, quantity=1, unit="個", category="", api_key=""):
    """在庫登録用の概算価格。単価マスタを基本にし、APIキーがあればGeminiで補正する。"""
    fallback = estimate_ingredient_price(name, quantity, unit, category)
    if not api_key or not str(name or "").strip():
        return fallback
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""日本の一般的なスーパーの店頭価格として、次の食材の合計金額を円の整数だけで答えてください。
食材名: {name}
数量: {quantity}
単位: {unit}
カテゴリ: {category or "不明"}
説明・単位・記号は不要。数字のみ。不明なら {fallback} と答えてください。
"""
        response = _gemini_generate(client, prompt)
        text = (response.text or "").strip().replace(",", "").replace("円", "")
        m = re.search(r"\d+", text)
        if m:
            return max(0, int(m.group(0)))
    except Exception:
        pass
    return fallback

def analyze_nutrition_balance(history, api_key, profile=None, meal_styles=None):
    """調理履歴をGeminiに渡し、年齢・性別・活動量・食事スタイルを踏まえた栄養診断JSONを返す。"""
    profile = profile or {}
    styles = [s for s in (meal_styles or []) if s]
    ref = get_nutrition_reference(
        profile.get("gender") or "",
        profile.get("age_group") or "",
        profile.get("activity_level") or "",
    )
    style_text = "、".join(styles) if styles else "指定なし"
    style_rules = "／".join(MEAL_STYLE_HINTS.get(s, s) for s in styles) if styles else "特になし"
    client = genai.Client(api_key=api_key)
    prompt = f"""あなたは管理栄養士です。以下の【調理履歴】から、過去の食事の栄養バランスを診断してください。
日本の一般的な食生活（主食・主菜・副菜・野菜・たんぱく質・脂質）に加え、
厚生労働省「日本人の食事摂取基準（2025年版）」に基づく【個人の基準値】と比較して評価してください。
食事記録は調理履歴ベースのため正確なグラム計算はできない前提で、食材構成から推定し、根拠を明示してください。

【ユーザープロフィール】
- 性別: {ref["gender_label"]}
- 年代: {ref["age_group"]}
- 身体活動レベル: {ref["activity_level"]}
- 食事スタイル: {style_text}
- 食事スタイルの制約: {style_rules}

【この人の1日あたりの栄養基準（目安）】
{format_nutrition_reference_block(ref)}

【評価の観点】
- 推定エネルギーが活動量に見合っているか
- PFCバランス（たんぱく質 {ref["protein_pct"]}、脂質 {ref["fat_pct"]}、炭水化物 {ref["carb_pct"]}）
- たんぱく質が推奨量（約 {ref["protein_g"]} g/日）に近いか
- 鉄分（約 {ref["iron_mg"]} mg/日）・食塩相当量（{ref["salt_g"]} g/日未満）・食物繊維
- 食事スタイル（減塩・高タンパク等）との整合
- {ref["age_group"]} に特有の注意（成長、貧血、生活習慣病、フレイル等）

【調理履歴】
{json.dumps(history, ensure_ascii=False, indent=2)}

JSON形式のみで返してください（Markdownの記法は不要）。
{{
  "score": 75,
  "summary": "過去の食事傾向に対する簡潔な総評（プロフィールを踏まえる）",
  "good_points": ["良く摂れている栄養素や評価ポイント"],
  "lacking_nutrients": ["不足している栄養素や食材グループ"],
  "recommended_ingredients": ["次の買い物や料理でおすすめしたい食材"],
  "age_group_advice": "この年代・性別・活動量向けの具体的なアドバイス",
  "advice": "具体的なアドバイス"
}}

ルール:
- score は 0〜100 の整数。上記の個人基準との適合度で採点する
- good_points, lacking_nutrients, recommended_ingredients は日本語の配列
- recommended_ingredients は具体的な食材名（例: ほうれん草、鮭、納豆）にする
- age_group_advice は世代別の注意点を1〜3文で書く
- 履歴が少ない場合も、分かる範囲で診断すること
- 医療診断ではなく食事の目安であることに触れてよい
"""
    response = _gemini_generate(client, prompt)
    data = parse_gemini_json(response.text)
    if not isinstance(data, dict):
        raise ValueError("診断結果の形式が不正です")
    try:
        score = int(round(float(data.get("score", 0))))
    except Exception:
        score = 0
    data["score"] = max(0, min(100, score))
    data["summary"] = str(data.get("summary") or "").strip()
    data["advice"] = str(data.get("advice") or "").strip()
    data["age_group_advice"] = str(data.get("age_group_advice") or "").strip()
    data["reference"] = ref
    for key in ("good_points", "lacking_nutrients", "recommended_ingredients"):
        val = data.get(key) or []
        if isinstance(val, str):
            val = [val]
        data[key] = [str(x).strip() for x in val if str(x).strip()]
    return data

def categorize_item_with_ai(item_name, api_key):
    if not api_key or not item_name:
        return "加工食品・その他"
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""
以下の食材を次のカテゴリから1つだけ選んで回答してください。
カテゴリ候補: {", ".join(CATEGORY_LIST)}
対象食材: {item_name}
回答はカテゴリ名のみを出力してください。
"""
        response = _gemini_generate(client, prompt)
        res = response.text.strip()
        for cat in CATEGORY_LIST:
            if cat in res:
                return cat
        return "加工食品・その他"
    except ApiQuotaExceeded:
        raise
    except Exception:
        return "加工食品・その他"

def parse_bulk_input_with_ai(user_text, api_key):
    """まとめて自動入力の解析"""
    client = genai.Client(api_key=api_key)
    prompt = f"""
テキストから食材、数量（数値または分数）、単位、カテゴリ、今日からの賞味期限目安日数(days_to_expiry)、購入金額(price: 円の整数)を抽出してください。
金額が書かれていない場合は price を 0 にしてください。
カテゴリ候補: {", ".join(CATEGORY_LIST)}

【テキスト】
{user_text}

【出力形式】
JSON配列のみで出力してください（Markdownの記法は含めないでください）。
[
  {{"name": "にんじん", "quantity": 2.0, "unit": "本", "category": "農産物（野菜・果物）", "days_to_expiry": 7, "price": 128}}
]
"""
    response = _gemini_generate(client, prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return json.loads(text)


def parse_audio_inventory_with_ai(audio_bytes, api_key, mime_type="audio/wav"):
    """st.audio_input の音声から食材JSONを抽出する（追加ライブラリなし）。"""
    from google.genai import types

    client = genai.Client(api_key=api_key)
    mime = (mime_type or "audio/wav").split(";")[0].strip() or "audio/wav"
    prompt = f"""
音声の内容から食材、数量（数値または分数）、単位、カテゴリ、今日からの賞味期限目安日数(days_to_expiry)、購入金額(price: 円の整数)を抽出してください。
金額が話されていない場合は price を 0 にしてください。
カテゴリ候補: {", ".join(CATEGORY_LIST)}

【出力形式】
JSON配列のみで出力してください（Markdownの記法は含めないでください）。
[
  {{"name": "にんじん", "quantity": 2.0, "unit": "本", "category": "農産物（野菜・果物）", "days_to_expiry": 7, "price": 128}}
]
"""
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime)
    response = _gemini_generate(client, [audio_part, prompt])
    text = (response.text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("音声解析結果の形式が不正です")
    return data


def ask_cooking_question_with_ai(question, recipe, api_key):
    """調理中の材料・代用品などの質問に、読み上げ向けの短い日本語で答える"""
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたはハンズフリー調理アシスタントです。料理中のユーザーの質問に答えてください。
回答は音声で読み上げるため、200文字以内の自然な日本語で、ですます調にしてください。
Markdownや箇条書き記号は使わないでください。

【料理名】{recipe.get("recipe_name", "")}
【人数】{recipe.get("servings", "")}
【献立構成】{json.dumps(recipe.get("composition") or [], ensure_ascii=False)}
【各品目】{json.dumps(recipe.get("dishes") or [], ensure_ascii=False)}
【材料】{json.dumps(recipe.get("used_ingredients", []), ensure_ascii=False)}
【足りないと良いもの】{json.dumps(recipe.get("missing_ingredients", []), ensure_ascii=False)}
【手順】{json.dumps(recipe.get("steps", []), ensure_ascii=False)}
【ワンポイント】{recipe.get("point", "")}

【質問】
{question}
"""
    response = _gemini_generate(client, prompt)
    return (response.text or "").strip()

def normalize_recipe_speech_fields(recipe):
    """steps を画面用の漢字テキストに揃え、読み上げ用 speech_steps を並行配列にする"""
    if not isinstance(recipe, dict):
        return recipe
    raw_steps = recipe.get("steps") or []
    raw_speech = recipe.get("speech_steps") or []
    display = []
    speech = []
    for i, item in enumerate(raw_steps):
        if isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("instruction")
                or item.get("instructions")
                or item.get("step")
                or ""
            )
            sp = item.get("speech_text") or item.get("speech") or ""
            display.append(str(text))
            speech.append(str(sp).strip())
        else:
            display.append(str(item))
            if i < len(raw_speech):
                sp_item = raw_speech[i]
                if isinstance(sp_item, dict):
                    speech.append(str(sp_item.get("speech_text") or sp_item.get("speech") or "").strip())
                else:
                    speech.append(str(sp_item).strip())
            else:
                speech.append("")
    if len(display) == 1 and not speech[0] and recipe.get("speech_text"):
        speech[0] = str(recipe.get("speech_text")).strip()
    recipe["steps"] = display
    recipe["speech_steps"] = speech
    return recipe

def parse_gemini_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return json.loads(text)

def normalize_slot_list(slots):
    out = []
    for s in slots or []:
        if s in MEAL_SLOT_OPTIONS and s not in out:
            out.append(s)
    return out

def selected_meal_slots(source="recipe"):
    key = "rescue_meal_slots" if source == "rescue" else "recipe_meal_slots"
    return normalize_slot_list(st.session_state.get(key) or [])

def composition_instruction(servings, slots):
    slot_list = json.dumps(normalize_slot_list(slots), ensure_ascii=False)
    return (
        f"指定された人数（{servings}）に合わせて、以下の【構成】に含まれる料理の"
        f"レシピセットを作成してください。【構成】: {slot_list}"
    )

def canonical_slot_name(value):
    text = str(value or "").strip()
    if text in MEAL_SLOT_OPTIONS:
        return text
    low = text.lower()
    for slot, aliases in MEAL_SLOT_ALIASES.items():
        for alias in aliases:
            if text == alias or low == str(alias).lower():
                return slot
    return ""

def recipe_json_format_instructions(servings, slots):
    """選択された献立カテゴリだけの dishes 配列を返すJSON指定"""
    slots = normalize_slot_list(slots) or list(DEFAULT_MEAL_SLOTS)
    dish_examples = []
    for slot in slots:
        dish_examples.append(
            f"""    {{
      "slot": "{slot}",
      "recipe_name": "{slot}の料理名",
      "category": "ジャンル",
      "time": "調理時間",
      "used_ingredients": [
        {{"id": 在庫ID(数値), "name": "食材名", "used_quantity": 使用量数値(例:0.5や1.0), "display_amount": "表示用(例:1/2個)"}}
      ],
      "missing_ingredients": [
        {{"name": "トマト", "quantity": 2, "unit": "個", "category": "農産物（野菜・果物）"}}
      ],
      "steps": [
        {{"text": "中火で香ばしく炒めます。", "speech_text": "ちゅうびで こうばしく いためます。"}}
      ],
      "point": "{slot}のワンポイントアドバイス"
    }}"""
        )
    dishes_block = ",\n".join(dish_examples)
    composition_json = json.dumps(slots, ensure_ascii=False)
    return f"""
JSON形式のみで返してください（Markdownの記法は不要）。
{{
  "recipe_name": "献立セット名",
  "servings": "{servings}",
  "composition": {composition_json},
  "included_nutrients": ["たんぱく質", "食物繊維"],
  "missing_nutrients": ["カルシウム", "ビタミンC"],
  "dishes": [
{dishes_block}
  ]
}}

【人数・分量】
- {composition_instruction(servings, slots)}
- 必ず {servings} の分量で材料を計算して出力してください。
- servings フィールドは必ず "{servings}" にしてください。

【献立】
- dishes は【構成】と同じ品目だけを、同じ順番・同じ slot 名で出力してください。
- 構成に含まれないカテゴリの料理は出力しないでください。
- 在庫を分け合って使い、同じ食材を過剰に使わないこと。
- missing_ingredients は買い物リスト用です。各要素は必ず {{"name": "食材名", "quantity": 数値, "unit": "個/g/ml等", "category": "カテゴリ候補のいずれか"}} のオブジェクトにしてください。
- quantity は 2 や 1.5 などの数値、unit は 個・g・ml・本・パック などにしてください。文字列（例: "トマト 2個"）だけは返さないでください。

【栄養】
- included_nutrients に、この献立で摂れる主な栄養素（例: たんぱく質、食物繊維、ビタミンC、鉄分）を3〜6個入れてください。
- missing_nutrients に、この献立だけでは不足しやすい栄養素を2〜5個入れてください。

【speech_text のルール】
- steps の各要素に、画面表示用の text（漢字かな交じり）と、音声読み上げ専用の speech_text を必ず両方入れてください。
- speech_text は全文ひらがな（数字・単位の読みも含む）にしてください。
- 誤読しやすい語は読みを明示すること。例: 中火→ちゅうび、強火→つよび、弱火→よわび、大さじ→おおさじ、小さじ→こさじ、少々→しょうしょう、適量→てきりょう、香ばしく→こうばしく、1人分→ひとりぶん、ひと煮立ち→ひとにたち。
- speech_text は読みやすさのため、意味の切れ目でスペースを入れてください。
"""

def flatten_meal_fields(recipe):
    """各品目を音声調理用の通し手順と、在庫減算用の材料一覧にまとめる"""
    dishes = recipe.get("dishes") or []
    steps, speech, roles = [], [], []
    ings, missing = [], []
    names = []
    times = []
    composition = []

    for dish in dishes:
        if not isinstance(dish, dict):
            continue
        slot = canonical_slot_name(dish.get("slot")) or dish.get("slot") or "料理"
        dish["slot"] = slot
        if slot not in composition:
            composition.append(slot)
        dish_name = dish.get("recipe_name") or slot
        names.append(dish_name)
        if dish.get("time"):
            times.append(f"{slot} {dish.get('time')}")
        yomi = MEAL_SLOT_YOMI.get(slot, "")
        for i, s in enumerate(dish.get("steps") or []):
            text = s.get("text", s) if isinstance(s, dict) else s
            steps.append(f"【{slot}・{dish_name}】{text}")
            sp_list = dish.get("speech_steps") or []
            sp = sp_list[i] if i < len(sp_list) else ""
            if isinstance(s, dict) and not sp:
                sp = s.get("speech_text") or ""
            speech.append((yomi + " " + str(sp)).strip() if sp else yomi or str(text))
            roles.append(slot)
        for ing in dish.get("used_ingredients") or []:
            row = dict(ing) if isinstance(ing, dict) else {"name": str(ing)}
            row["dish"] = slot
            ings.append(row)
        missing.extend(dish.get("missing_ingredients") or [])

    recipe["dishes"] = dishes
    recipe["composition"] = recipe.get("composition") or composition
    recipe["steps"] = steps
    recipe["speech_steps"] = speech
    recipe["step_roles"] = roles
    recipe["used_ingredients"] = ings
    recipe["missing_ingredients"] = missing
    if not recipe.get("recipe_name") and names:
        recipe["recipe_name"] = " ＋ ".join(names)
    if not recipe.get("category"):
        recipe["category"] = " / ".join(composition) if composition else "献立"
    if times:
        recipe["time"] = " / ".join(times)

    by_slot = {d.get("slot"): d for d in dishes if isinstance(d, dict)}
    recipe["main"] = by_slot.get("主菜")
    recipe["side"] = by_slot.get("副菜")
    return recipe

def _dish_from_mapping(raw, slot):
    dish = dict(raw)
    dish["slot"] = slot
    return normalize_recipe_speech_fields(dish)

def collect_dishes_from_recipe(recipe):
    """dishes 配列、日本語キー、main/side のどれからでも品目リストを作る"""
    wanted = normalize_slot_list(recipe.get("composition"))
    dishes = []
    seen_slots = set()

    raw_list = recipe.get("dishes")
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            slot = canonical_slot_name(item.get("slot") or item.get("type") or item.get("kind"))
            if not slot:
                if len(dishes) < len(wanted):
                    slot = wanted[len(dishes)]
                elif len(dishes) < len(MEAL_SLOT_OPTIONS):
                    slot = MEAL_SLOT_OPTIONS[len(dishes)]
                else:
                    slot = "主菜"
            dishes.append(_dish_from_mapping(item, slot))
            seen_slots.add(slot)

    for slot, aliases in MEAL_SLOT_ALIASES.items():
        if slot in seen_slots:
            continue
        found = None
        for key in aliases:
            val = recipe.get(key)
            if isinstance(val, dict) and (
                val.get("recipe_name") or val.get("steps") or val.get("used_ingredients")
            ):
                found = val
                break
        if found:
            dishes.append(_dish_from_mapping(found, slot))
            seen_slots.add(slot)

    if not dishes:
        dishes.append(
            _dish_from_mapping(
                {
                    "recipe_name": recipe.get("recipe_name"),
                    "category": recipe.get("category"),
                    "time": recipe.get("time"),
                    "used_ingredients": list(recipe.get("used_ingredients") or []),
                    "missing_ingredients": list(recipe.get("missing_ingredients") or []),
                    "steps": list(recipe.get("steps") or []),
                    "speech_steps": list(recipe.get("speech_steps") or []),
                    "point": recipe.get("point"),
                },
                "主菜",
            )
        )
    return dishes

def normalize_meal_recipe(recipe):
    """単品・主菜+副菜・可変構成JSONを dishes 配列に正規化する"""
    if not isinstance(recipe, dict):
        return recipe
    recipe["dishes"] = collect_dishes_from_recipe(recipe)
    wanted = normalize_slot_list(recipe.get("composition"))
    if wanted:
        by_slot = {d.get("slot"): d for d in recipe["dishes"]}
        recipe["dishes"] = [by_slot[s] for s in wanted if s in by_slot]
        recipe["composition"] = wanted
    else:
        recipe["composition"] = [d.get("slot") for d in recipe["dishes"] if d.get("slot")]
    recipe = flatten_meal_fields(recipe)
    recipe["included_nutrients"] = _normalize_nutrient_list(
        recipe.get("included_nutrients")
        or recipe.get("含まれる栄養素")
        or recipe.get("includedNutrients")
    )
    recipe["missing_nutrients"] = _normalize_nutrient_list(
        recipe.get("missing_nutrients")
        or recipe.get("不足栄養素")
        or recipe.get("missingNutrients")
    )
    for dish in recipe.get("dishes") or []:
        if isinstance(dish, dict):
            dish["missing_ingredients"] = strip_owned_seasoning_entries(
                dish.get("missing_ingredients")
            )
    recipe["missing_ingredients"] = strip_owned_seasoning_entries(
        recipe.get("missing_ingredients")
    )
    return recipe

def strip_owned_seasoning_entries(items):
    """常備調味料を不足・買い足しリストから除く"""
    kept = []
    for raw in items or []:
        if isinstance(raw, dict):
            name = raw.get("name") or raw.get("item") or ""
        else:
            name = raw
        if is_owned_seasoning(name):
            continue
        kept.append(raw)
    return kept

def _normalize_nutrient_list(raw):
    items = []
    if isinstance(raw, str):
        raw = [p.strip() for p in re.split(r"[,、/／]", raw) if p.strip()]
    if not isinstance(raw, list):
        return []
    seen = set()
    for x in raw:
        if isinstance(x, dict):
            name = str(x.get("name") or x.get("nutrient") or x.get("label") or "").strip()
        else:
            name = str(x or "").strip()
        if name and name not in seen:
            seen.add(name)
            items.append(name)
    return items

def selected_servings():
    val = st.session_state.get("recipe_servings") or st.session_state.get("servings") or "2人分"
    if val not in SERVINGS_OPTIONS:
        return "2人分"
    return val

def servings_calc_instruction():
    """Gemini向け: 『2人分の分量で計算して出力してください』（二重の『人分』を避ける）"""
    m = re.search(r"\d+", selected_servings())
    num = m.group(0) if m else "2"
    return f"{num}人分の分量で計算して出力してください"

def generate_leftover_rescue_recipe(priority_items, inventory_df, api_key, meal_slots=None):
    """期限間近の食材を優先消費するレシピを Gemini で生成する"""
    servings = selected_servings()
    slots = normalize_slot_list(meal_slots) or selected_meal_slots("rescue")
    extra = []
    extra.append(servings_calc_instruction())
    extra.append(allergy_ban_prompt_line())
    style_line = meal_style_prompt_line()
    if style_line:
        extra.append(style_line)
    season_line = owned_seasonings_prompt_line()
    if season_line:
        extra.append(season_line)
    staple_line = staple_type_prompt_line(slots)
    if staple_line:
        extra.append(staple_line)

    all_items = [
        {
            "id": int(row["id"]) if pd.notna(row.get("id")) else None,
            "name": row["name"],
            "quantity": row["quantity"],
            "unit": row.get("unit") or "",
            "expiry_date": str(row.get("expiry_date")),
            "priority": int(row["id"]) in {int(p["id"]) for p in priority_items if p.get("id") is not None},
        }
        for _, row in inventory_df.iterrows()
    ]

    prompt = f"""以下の期限間近の食材を優先的に消費する料理レシピを作成してください。
プロの料理人として、【優先食材】を必ず使い切りに近い形で活かし、残り物をレスキューする献立を提案してください。

【優先食材】
{json.dumps(priority_items, ensure_ascii=False)}

【その他の在庫】
{json.dumps(all_items, ensure_ascii=False)}

【条件】
- {composition_instruction(servings, slots)}
- {servings_calc_instruction()}
- 優先食材（priority: true / 【優先食材】）を必ず使うこと。
- 賞味期限が切れている、または近いものから積極的に消費すること。
- 人数: {servings}

【パーソナライズ設定】
{chr(10).join(f"- {line}" for line in extra)}

【回答形式】
{recipe_json_format_instructions(servings, slots)}
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    recipe = normalize_meal_recipe(parse_gemini_json(response.text))
    recipe["servings"] = servings
    recipe["composition"] = slots
    return recipe

def generate_nutrient_boost_recipe(main_recipe, api_key, meal_slots=None):
    """不足栄養素を補う、在庫優先の手軽な追加レシピを生成する"""
    servings = (main_recipe or {}).get("servings") or selected_servings()
    if servings not in SERVINGS_OPTIONS:
        servings = selected_servings()
    missing = _normalize_nutrient_list((main_recipe or {}).get("missing_nutrients"))
    slots = [
        s
        for s in normalize_slot_list(meal_slots)
        if s in NUTRIENT_BOOST_SLOT_OPTIONS
    ]
    if not slots:
        slots = ["副菜"]
    main_names = _ingredient_names_from_recipe(main_recipe)
    df_inv = get_inventory()
    inventory_items = [
        {
            "id": int(row["id"]) if pd.notna(row.get("id")) else None,
            "name": row["name"],
            "quantity": row["quantity"],
            "unit": row.get("unit") or "",
            "expiry_date": str(row.get("expiry_date")),
            "category": row.get("category") or "",
        }
        for _, row in df_inv.iterrows()
    ]
    extra = [servings_calc_instruction(), allergy_ban_prompt_line()]
    style_line = meal_style_prompt_line()
    if style_line:
        extra.append(style_line)
    season_line = owned_seasonings_prompt_line()
    if season_line:
        extra.append(season_line)

    prompt = f"""プロの栄養士兼料理人として、直前の献立で不足している栄養素を補う【追加の料理】を提案してください。

【不足している栄養素】
{json.dumps(missing, ensure_ascii=False)}

【直前の献立】
名前: {(main_recipe or {}).get("recipe_name") or "無題"}
構成: {json.dumps((main_recipe or {}).get("composition") or [], ensure_ascii=False)}
すでに使う予定の材料: {json.dumps(main_names, ensure_ascii=False)}

【現在の在庫】
{json.dumps(inventory_items, ensure_ascii=False)}

【条件】
- {composition_instruction(servings, slots)}
- dishes は【構成】と同じ品目だけを、同じ順番・同じ slot 名で出力してください。
- 主食・主菜は出さないでください。ユーザーが選んだ副菜・汁物・付け合わせ・デザートだけを作ること。
- 不足栄養素（{ '、'.join(missing) or '全体のバランス' }）を重点的に補うこと。
- 現在の在庫を優先的に使うこと。在庫で足りない材料は missing_ingredients に入れてください（買い物リスト用）。
- 直前の献立ですでに使う予定の材料は、残量が少ない場合は過剰に使わないこと。
- 調理時間は10〜15分以内の手軽なものを優先すること。
- {servings_calc_instruction()}
- servings は "{servings}" にしてください。

【パーソナライズ設定】
{chr(10).join(f"- {line}" for line in extra)}

【回答形式】
{recipe_json_format_instructions(servings, slots)}
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    recipe = normalize_meal_recipe(parse_gemini_json(response.text))
    recipe["servings"] = servings
    recipe["composition"] = slots
    recipe["is_nutrient_boost"] = True
    recipe["boosts_nutrients"] = missing
    if not recipe.get("recipe_name"):
        recipe["recipe_name"] = "栄養補完の一品"
    return recipe

def generate_meal_calendar_with_ai(
    schedule, extra_budget, inventory_df, api_key, free_request=""
):
    """在庫優先・予算内の買い足しで、指定された食事枠だけ献立を生成する"""
    servings = selected_servings()
    items = []
    if inventory_df is not None and not inventory_df.empty:
        for _, row in inventory_df.iterrows():
            items.append(
                {
                    "name": row.get("name"),
                    "quantity": row.get("quantity"),
                    "unit": row.get("unit") or "",
                    "category": row.get("category") or "",
                    "expiry_date": str(row.get("expiry_date") or ""),
                }
            )
    extra = [allergy_ban_prompt_line()]
    season_line = owned_seasonings_prompt_line()
    if season_line:
        extra.append(season_line)
    extra.append(servings_calc_instruction())
    style_line = meal_style_prompt_line()
    if style_line:
        extra.append(style_line)
    request_text = str(free_request or "").strip()
    if request_text:
        extra.append(f"ユーザーの自由リクエストを最優先で守ること: {request_text}")

    required_lines = []
    for day in schedule:
        labels = [lab for key, lab in MEAL_CALENDAR_SLOTS if key in (day.get("meals") or [])]
        day_req = str(day.get("request") or "").strip()
        nth = int(day.get("day_index") or 0) + 1
        head = f"{nth}日目 {day['date']}（{day['weekday']}）"
        if labels:
            line = head + ": " + "・".join(labels)
            if day_req:
                line += f" ／ この日の自由リクエスト: {day_req}"
            required_lines.append(line)
        else:
            required_lines.append(f"{head}: 食事なし（献立不要）")

    prompt = f"""あなたは家庭の献立プランナーです。現在の冷蔵庫在庫を最優先で使い切り、
買い足しは「使える追加予算」以内の最小限にしてください。

【必要な食事枠】チェックが入っている時間帯だけ献立を作ること。外れている枠は作らない。
{chr(10).join(required_lines)}

【人数】{servings}
【使える追加予算】{int(extra_budget)}円（買い足し食材の概算合計がこの金額を超えないこと）

【現在の在庫】
{json.dumps(items, ensure_ascii=False)}

【自由リクエスト】
{request_text or "（指定なし）"}

【条件】
{chr(10).join(f"- {line}" for line in extra) if extra else "- 特になし"}
- 【必要な食事枠】に書かれた食事だけを提案すること。記載のない朝/昼/晩は JSON に null を入れるか省略する。
- 在庫にある食材をできるだけ使い、賞味期限が近いものから優先すること。
- 自由リクエスト（和食メイン、金曜夜はパスタ等）は必ず反映すること。
- 買い足しが必要な食材だけを extra_ingredients に入れること。
- extra_ingredients の estimated_price は日本のスーパーの概算（円の整数）。
- extra_total は extra_ingredients の estimated_price 合計で、{int(extra_budget)} 以下。
- 常備調味料（サラダ油・オリーブオイルを含む、家にあると指定されたもの）は買い足し不要とし、extra_ingredients に入れないこと。
- 「2日目」は上の【必要な食事枠】の2日目を指す。日付と食事枠を取り違えないこと。
- JSONのみで返すこと。Markdownは不要。

【出力形式】
{{
  "summary": "在庫の使い方と予算・リクエストへの一言",
  "extra_total": 0,
  "extra_ingredients": [
    {{"name": "トマト", "quantity": 2, "unit": "個", "category": "農産物（野菜・果物）", "estimated_price": 160}}
  ],
  "days": [
    {{
      "date": "{schedule[0]['date']}",
      "weekday": "{schedule[0]['weekday']}",
      "breakfast": {{"name": "メニュー名", "uses_inventory": ["在庫食材"], "note": "一言"}},
      "lunch": {{"name": "メニュー名", "uses_inventory": ["在庫食材"], "note": "一言"}},
      "dinner": {{"name": "メニュー名", "uses_inventory": ["在庫食材"], "note": "一言"}}
    }}
  ]
}}
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    data = parse_gemini_json(response.text)
    if not isinstance(data, dict):
        raise ValueError("献立カレンダーの形式が不正です")
    return normalize_meal_calendar(data, schedule, extra_budget)

def revise_meal_recipe_with_ai(recipe, revision_request, api_key):
    """既存レシピを修正リクエストに沿って再生成する"""
    servings = (recipe or {}).get("servings") or selected_servings()
    if servings not in SERVINGS_OPTIONS:
        servings = selected_servings()
    slots = normalize_slot_list((recipe or {}).get("composition")) or selected_meal_slots("recipe")
    extra = [servings_calc_instruction(), allergy_ban_prompt_line()]
    style_line = meal_style_prompt_line()
    if style_line:
        extra.append(style_line)
    season_line = owned_seasonings_prompt_line()
    if season_line:
        extra.append(season_line)
    prompt = f"""あなたは家庭料理のアシスタントです。既存の献立JSONを、ユーザーの修正リクエストに従って更新してください。
変更不要な部分はできるだけ残し、指定された箇所を重点的に直してください。

【現在の献立】
{json.dumps(recipe, ensure_ascii=False)}

【修正リクエスト】
{revision_request}

【条件】
{chr(10).join(f"- {line}" for line in extra)}
- 常備調味料は missing_ingredients に入れないこと。
- JSONのみで返すこと。Markdownは不要。

【回答形式】
{recipe_json_format_instructions(servings, slots)}
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    updated = normalize_meal_recipe(parse_gemini_json(response.text))
    updated["servings"] = servings
    updated["composition"] = slots
    return updated

def revise_meal_calendar_with_ai(plan, revision_request, api_key, extra_budget=None):
    """既存の献立カレンダーを修正リクエストに沿って更新する"""
    days = plan.get("days") or []
    schedule = []
    for i, day in enumerate(days):
        meals = day.get("meals")
        if not meals:
            meals = [k for k, _ in MEAL_CALENDAR_SLOTS if not (day.get(k) or {}).get("skipped")]
        schedule.append(
            {
                "date": day.get("date"),
                "weekday": day.get("weekday"),
                "day_index": i,
                "meals": meals,
                "request": day.get("request") or "",
            }
        )
    if extra_budget is None:
        extra_budget = plan.get("budget") or 0
    extra = [allergy_ban_prompt_line()]
    season_line = owned_seasonings_prompt_line()
    if season_line:
        extra.append(season_line)
    style_line = meal_style_prompt_line()
    if style_line:
        extra.append(style_line)
    index_lines = []
    for i, day in enumerate(days, 1):
        index_lines.append(
            f"{i}日目 = {day.get('date')}（{day.get('weekday')}）"
        )
    prompt = f"""あなたは家庭の献立プランナーです。既存の献立カレンダーを、ユーザーの修正リクエストに従って更新してください。
指定された日・食事だけを変え、他はできるだけ残してください。

【日付の対応】「2日目」はこの対応表の2日目です。
{chr(10).join(index_lines) if index_lines else "（日付なし）"}

【現在の献立カレンダー】
{json.dumps(plan, ensure_ascii=False)}

【修正リクエスト】
{revision_request}

【条件】
{chr(10).join(f"- {line}" for line in extra) if extra else "- 特になし"}
- 常備調味料は買い足し不要なので extra_ingredients に入れないこと。
- 買い足し概算の合計は {int(extra_budget)} 円以下。
- JSONのみで返すこと。Markdownは不要。

【出力形式】は元のカレンダーと同じ keys（summary, extra_total, extra_ingredients, days）にしてください。
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    data = parse_gemini_json(response.text)
    if not isinstance(data, dict):
        raise ValueError("献立カレンダーの形式が不正です")
    updated = normalize_meal_calendar(data, schedule, extra_budget)
    updated["free_request"] = plan.get("free_request") or ""
    return updated

def normalize_meal_calendar(data, dates, extra_budget):
    extras = []
    seen = set()
    extra_total = 0
    for raw in data.get("extra_ingredients") or []:
        parsed = parse_shopping_ingredient_entry(raw)
        name = parsed.get("name") or ""
        key = _normalize_shopping_name(name)
        if not name or key in seen or is_owned_seasoning(name):
            continue
        seen.add(key)
        if isinstance(raw, dict) and raw.get("estimated_price") not in (None, ""):
            price = parse_price(raw.get("estimated_price"))
        else:
            price = 0
        if price <= 0:
            price = estimate_ingredient_price(
                name, parsed["quantity"], parsed["unit"], parsed.get("category") or ""
            )
        extras.append(
            {
                "name": name,
                "quantity": parsed["quantity"],
                "unit": parsed["unit"],
                "category": parsed.get("category") or "加工食品・その他",
                "estimated_price": price,
            }
        )
        extra_total += price
    days_out = []
    by_date = {}
    for d in data.get("days") or []:
        if isinstance(d, dict) and d.get("date"):
            by_date[str(d.get("date"))[:10]] = d
    for meta in dates:
        src = by_date.get(meta["date"]) or {}
        wanted = set(meta.get("meals") or ["breakfast", "lunch", "dinner"])
        nth = int(meta.get("day_index") or 0) + 1
        entry = {
            "date": meta["date"],
            "weekday": meta["weekday"],
            "day_index": nth,
            "day_label": f"{nth}日目",
            "request": str(meta.get("request") or "").strip(),
            "meals": list(meta.get("meals") or []),
        }
        for key, _label in MEAL_CALENDAR_SLOTS:
            if key not in wanted:
                entry[key] = {
                    "name": "（予定なし）",
                    "uses_inventory": [],
                    "note": "外食・スキップ",
                    "skipped": True,
                }
                continue
            meal = src.get(key) if isinstance(src.get(key), dict) else {}
            uses = meal.get("uses_inventory") or meal.get("ingredients") or []
            if isinstance(uses, str):
                uses = [uses]
            entry[key] = {
                "name": str(meal.get("name") or meal.get("menu") or "未設定").strip() or "未設定",
                "uses_inventory": [str(x).strip() for x in uses if str(x).strip()],
                "note": str(meal.get("note") or meal.get("point") or "").strip(),
                "skipped": False,
            }
        days_out.append(entry)
    return {
        "summary": str(data.get("summary") or "").strip(),
        "extra_ingredients": extras,
        "extra_total": int(extra_total),
        "budget": int(extra_budget),
        "free_request": str(data.get("free_request") or "").strip(),
        "days": days_out,
    }


def parse_receipt_with_ai(image, api_key):
    """レシート画像から食材JSON配列を抽出する。"""
    image = prepare_image_for_ai(image)
    client = genai.Client(api_key=api_key)
    prompt = f"""
レシート画像から食品・食材のみを抽出し、JSON形式で出力してください。
カテゴリ候補: {", ".join(CATEGORY_LIST)}

レシートに金額が書いてあれば price（円の整数）も抽出してください。不明な場合は 0 にしてください。

出力形式:
[
  {{"name": "食材名", "quantity": 1.0, "unit": "個", "category": "農産物（野菜・果物）", "days_to_expiry": 7, "price": 198}}
]
"""
    response = _gemini_generate(client, [image, prompt], is_image=True)
    text = (response.text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("レシート解析結果の形式が不正です")
    return data


def generate_recipe_from_inventory(
    inventory_df,
    api_key,
    genre="指定なし",
    cooking_time="指定なし",
    free_request="",
    selected_ids=None,
    prioritize_expiry=True,
    nutrition_priority_ingredients=None,
    meal_slots=None,
):
    """在庫から献立セットを1つ生成する。"""
    selected_ids = set(selected_ids or [])
    servings = selected_servings()
    slots = normalize_slot_list(meal_slots) or selected_meal_slots("recipe")
    target_items = [
        {
            "id": row["id"],
            "name": row["name"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "expiry_date": row["expiry_date"],
            "priority": row["id"] in selected_ids,
        }
        for _, row in inventory_df.iterrows()
    ]
    extra = []
    nut_ings = nutrition_priority_ingredients or []
    if nut_ings:
        extra.append(
            "栄養診断で推奨された次の食材を、在庫にあれば優先的に使ってください: "
            + "、".join(nut_ings)
        )
    personalize_lines = build_personalize_prompt_lines(servings, slots, extra)
    prompt = f"""
プロの料理人として、以下の【在庫リスト】から献立セットを1つ提案してください。

【在庫リスト】
{json.dumps(target_items, ensure_ascii=False)}

【条件】
- {composition_instruction(servings, slots)}
- ジャンル: {genre}
- 調理時間: {cooking_time}
- 主食タイプ: {selected_staple_type() if "主食" in slots else "（主食なし）"}
- リクエスト: {free_request}
- 賞味期限優先: {"賞味期限が近いものを優先" if prioritize_expiry else "特になし"}
- priority: true の食材はできる限り優先的に使ってください。
- {servings_calc_instruction()}
- servings は "{servings}" にしてください。

【パーソナライズ設定】
{chr(10).join(f"- {line}" for line in personalize_lines)}

【回答形式】
{recipe_json_format_instructions(servings, slots)}
"""
    client = genai.Client(api_key=api_key)
    response = _gemini_generate(client, prompt)
    recipe = normalize_meal_recipe(parse_gemini_json(response.text))
    recipe["servings"] = servings
    recipe["composition"] = slots
    return recipe
