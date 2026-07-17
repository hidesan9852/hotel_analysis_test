import streamlit as st
import requests
import pandas as pd
import anthropic
import os
from datetime import datetime, timedelta

st.title("🏨 ホテル収益改善＆ペルソナ提案ツール")

# ── APIキーの設定（Streamlitの金庫から呼び出す） ────────────────
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except KeyError:
        st.error("⚠️ Anthropic APIキーが設定されていません。StreamlitのSecretsを確認してください。")
        st.stop()

try:
    SERP_API_KEY = st.secrets["SERP_API_KEY"]
except KeyError:
    st.error("⚠️ SerpApiキーが設定されていません。StreamlitのSecretsを確認してください。")
    st.stop()

# ── 1. 競合データの取得設定 ─────────────────────────────────
st.markdown("### STEP 1: 競合データの自動取得")
col1, col2 = st.columns(2)
with col1:
    search_area = st.text_input("検索エリア・キーワード", value="大阪本町 ホテル")
with col2:
    # 修正①: text_input(自由記述) → date_input に変更。
    # フォーマット崩れ（例: 2026/9/9 のような入力）でAPIエラーになるのを防ぐ
    target_date = st.date_input("計測する宿泊日", value=datetime(2026, 9, 9))

if "hotel_df" not in st.session_state:
    st.session_state.hotel_df = None

include_vacation_rentals = st.checkbox("🏠 民泊（バケーションレンタル）も含めて調査する", value=True)

if st.button("🔍 競合データを取得"):
    # 修正②: チェックアウト日が "2026-09-10" に固定されていたバグを修正。
    # チェックイン日(target_date)の翌日を動的に計算するよう変更。
    check_in_str = target_date.strftime("%Y-%m-%d")
    check_out_str = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")

    base_params = {
        "engine": "google_hotels",
        "q": search_area,
        "check_in_date": check_in_str,
        "check_out_date": check_out_str,
        "adults": "1",
        "currency": "JPY",
        "gl": "jp",
        "hl": "ja",
        "api_key": SERP_API_KEY
    }

    def fetch_properties(params):
        """SerpApiのGoogle Hotelsエンジンを呼び出し、properties配列を返す"""
        resp = requests.get("https://serpapi.com/search.json", params=params)
        resp.raise_for_status()
        return resp.json().get("properties", [])

    def format_rating(value):
        """評価値を小数第1位に丸める。値が無い場合は「―」を返す"""
        return round(value, 1) if isinstance(value, (int, float)) else "―"

    def extract_property_info(h, prop_type):
        """ホテル/民泊のプロパティ情報から、比較分析に使う項目を抽出する"""
        info = {
            "種別": prop_type,
            "施設名": h.get("name", "名称不明"),
            "最安値(1泊)": h.get("rate_per_night", {}).get("lowest", "価格なし(満室の可能性)"),
            "評価スコア": format_rating(h.get("overall_rating")),
            "レビュー数": h.get("reviews", "―"),
            "立地評価": format_rating(h.get("location_rating")),
        }
        if prop_type == "ホテル":
            # ホテルは "5-star hotel" のような星クラス表記
            info["特徴"] = h.get("hotel_class", "―")
        else:
            # 民泊は "Entire villa", "Sleeps 4", "2 bedrooms" などのリストで返ってくる
            essential = h.get("essential_info", [])
            info["特徴"] = "、".join(essential) if essential else "―"
        return info

    with st.spinner("競合ホテル・民泊データを取得中..."):
        try:
            hotel_list = []

            # ① 通常のホテル検索（vacation_rentalsパラメータなし＝デフォルトでホテルのみ）
            for h in fetch_properties(base_params):
                hotel_list.append(extract_property_info(h, "ホテル"))

            # ② 民泊（バケーションレンタル）検索
            # SerpApiのGoogle Hotels APIは vacation_rentals=true を渡すと
            # ホテルではなく民泊のみの結果を返す仕様のため、別リクエストとして呼び出す
            if include_vacation_rentals:
                vr_params = {**base_params, "vacation_rentals": "true"}
                for h in fetch_properties(vr_params):
                    hotel_list.append(extract_property_info(h, "民泊"))

            if hotel_list:
                df = pd.DataFrame(hotel_list)
                df.index = df.index + 1
                st.session_state.hotel_df = df
                n_hotel = sum(1 for r in hotel_list if r["種別"] == "ホテル")
                n_vr = sum(1 for r in hotel_list if r["種別"] == "民泊")
                st.success(f"✅ {len(df)}件のデータを取得しました！（ホテル {n_hotel}件・民泊 {n_vr}件）")
            else:
                st.error("該当するデータが見つかりませんでした。")
        # 修正③: 通信系エラーとその他エラーを分けて捕捉し、原因を切り分けやすくした
        except requests.exceptions.RequestException as e:
            st.error(f"⚠️ SerpApiへの通信でエラーが発生しました: {e}")
        except Exception as e:
            st.error(f"⚠️ データ取得処理でエラーが発生しました: {e}")

if st.session_state.hotel_df is not None:
    st.dataframe(st.session_state.hotel_df, use_container_width=True)

# ── 2. 自社データの入力とAI分析 ──────────────────────────────
st.markdown("---")
st.markdown("### STEP 2: 自社の状況・強みの入力とAI分析")
col3, col4 = st.columns(2)
with col3:
    own_price = st.text_input("自社ホテルの現在の設定価格（円）", placeholder="例: 7500")
with col4:
    own_occ = st.text_input("現在の予約稼働率（OCC）", placeholder="例: 40%")

st.markdown("##### 🏨 自社ホテルの「強み・特徴」（あてはまるものを全てチェック）")

# チェックボックスを3列に分けて綺麗に配置
chk_col1, chk_col2, chk_col3 = st.columns(3)

strengths_list = []

with chk_col1:
    if st.checkbox("🍳 朝食無料（手作り・焼き立て）"): strengths_list.append("朝食無料（手作り・焼き立て）")
    if st.checkbox("♨️ 大浴場・サウナあり"): strengths_list.append("大浴場・サウナあり")
    if st.checkbox("🚉 駅から徒歩5分以内"): strengths_list.append("駅から徒歩5分以内")

with chk_col2:
    if st.checkbox("🧴 充実したアメニティ"): strengths_list.append("充実したアメニティ")
    if st.checkbox("💻 高速Wi-Fi・ワークスペース"): strengths_list.append("高速Wi-Fi・ワークスペース")
    if st.checkbox("🍷 ウェルカムサービス"): strengths_list.append("ウェルカムサービス")

with chk_col3:
    if st.checkbox("🧹 清掃が徹底されている"): strengths_list.append("清掃が徹底されている")
    if st.checkbox("😊 アットホームな接客"): strengths_list.append("アットホームな接客")
    if st.checkbox("🅿️ 駐車場あり・安い"): strengths_list.append("駐車場あり・安い")

# 独自の強みを追加できるフリー欄（任意）
other_strength = st.text_input("その他、独自の強みがあれば入力（任意）", placeholder="例：レディースフロアを完備している")
if other_strength:
    strengths_list.append(other_strength)

# チェックされたリストを「、」で繋いで一つの文字列にする
own_strengths_str = "、".join(strengths_list)

# AIへの指示書（システムプロンプト）
SYSTEM_PROMPT = """あなたは小規模ビジネスホテル専門の辣腕レベニューマネージャー兼採用マーケターです。
提供された【自社データ（強み・価格・稼働率）】と【競合（ホテル・民泊）の価格データ（外部環境）】を掛け合わせ、以下の3点を論理的に提案してください。

1. 【ダイナミックプライシング提案】
競合の価格帯（満室表示を含む）や評価スコア・レビュー数から現在の市場の需要逼迫度・ポジショニングを推測し、自社の適正な販売価格をズバリ提案し、その根拠を解説してください。

2. 【強みを活かした新規ペルソナ開発】
入力された「自社の強み（内部環境）」と「競合の価格状況（外部環境）」の隙間を突く、新しい顧客ペルソナを2パターン提案してください。
激戦エリアにおいて価格競争に巻き込まれないためのエッジの効いたターゲット設定とし、このペルソナ像が、将来的なホテルの独自性強化や、求職者に対する魅力的な「採用ブランディング」の軸としても機能するよう具体的に描写してください。

3. 【具体的な宿泊プラン・アメニティ施策】
提案したペルソナに向けた、自社の強みを最大化する明日から実行可能な宿泊プランのタイトル案や、アメニティ・サービス施策を提案してください。"""

if st.button("🚀 AIで収益改善策を生成する", type="primary"):
    if st.session_state.hotel_df is None:
        st.warning("先にSTEP1で競合データを取得してください。")
    elif not own_price or not own_occ:
        st.warning("自社の価格と稼働率を入力してください。")
    elif len(strengths_list) == 0:
        st.warning("自社の強み・特徴を少なくとも1つ選択（または入力）してください。")
    else:
        user_message = f"【自社ホテルの現状と強み】\n設定価格: {own_price}円\n現在の稼働率: {own_occ}\n自社の強み・特徴: {own_strengths_str}\n\n【競合の状況（ホテル・民泊 計{len(st.session_state.hotel_df)}件）】\n{st.session_state.hotel_df.to_string()}"

        with st.spinner("AIが自社の強みと市場動向を掛け合わせ、戦略を立案中...（数十秒かかります）"):
            try:
                client = anthropic.Anthropic(api_key=api_key)
                result_placeholder = st.empty()
                full_response = ""

                with client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        result_placeholder.markdown(full_response)

            except Exception as e:
                st.error(f"AI分析中にエラーが発生しました: {e}")
