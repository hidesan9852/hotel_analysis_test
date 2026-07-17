import streamlit as st
import requests
import pandas as pd
import anthropic
import os
import calendar
import itertools
import jpholiday
from datetime import datetime, timedelta, date

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

# ══════════════════════════════════════════════════════════════
# STEP 1: 分析対象月の設定 ＋ 競合データの取得
# ══════════════════════════════════════════════════════════════
st.markdown("### STEP 1: 分析対象月の設定と競合データ取得")

col1, col2, col3 = st.columns(3)
with col1:
    search_area = st.text_input("検索エリア・キーワード", value="大阪本町 ホテル")
with col2:
    current_year = date.today().year
    year = st.selectbox("分析対象年", options=list(range(current_year, current_year + 2)), index=0)
with col3:
    month = st.selectbox(
        "分析対象月", options=list(range(1, 13)),
        index=date.today().month - 1, format_func=lambda m: f"{m}月"
    )

days_in_month = calendar.monthrange(year, month)[1]
month_dates = [date(year, month, d) for d in range(1, days_in_month + 1)]


def get_price_tier(d):
    """日付から定価の適用区分（日～木／金・土・祝前日）を判定する"""
    is_weekend_or_eve = d.weekday() in (4, 5) or jpholiday.is_holiday(d + timedelta(days=1))
    return "金・土・祝前日" if is_weekend_or_eve else "日～木"


tier_counts = pd.Series([get_price_tier(d) for d in month_dates]).value_counts()
tier_summary_str = (
    f"日～木: {tier_counts.get('日～木', 0)}日 / "
    f"金・土・祝前日: {tier_counts.get('金・土・祝前日', 0)}日"
)

include_vacation_rentals = st.checkbox("🏠 民泊（バケーションレンタル）も含めて調査する", value=True)

fetch_mode = st.radio(
    "競合データの取得方式",
    ["代表日サンプリング（コスト重視）", "全日程取得（精度重視）"],
    help="サンプリングは月内から平日・週末をバランスよく6〜8日程度選び、価格傾向を推測します。"
         "全日程取得は月内すべての日を取得するため精度は高い一方、SerpApiのリクエスト数が大きく増えます。"
)


def get_sample_dates(dates_in_month):
    """月内から代表的な平日・週末を、月初・月中・月末それぞれからピックアップする"""
    n = len(dates_in_month)
    thirds = [dates_in_month[:n // 3], dates_in_month[n // 3:2 * n // 3], dates_in_month[2 * n // 3:]]
    picks = []
    for chunk in thirds:
        wd = next((d for d in chunk if d.weekday() < 5), None)
        we = next((d for d in chunk if d.weekday() >= 5), None)
        if wd:
            picks.append(wd)
        if we:
            picks.append(we)
    return sorted(set(picks))


if fetch_mode.startswith("代表日"):
    fetch_dates = get_sample_dates(month_dates)
else:
    fetch_dates = month_dates

n_calls = len(fetch_dates) * (2 if include_vacation_rentals else 1)
st.info(
    f"📊 この設定でのSerpApiリクエスト数：最大 **{n_calls}回**"
    f"（対象日 {len(fetch_dates)}日 × {'ホテル＋民泊' if include_vacation_rentals else 'ホテルのみ'}）"
)

if "hotel_df" not in st.session_state:
    st.session_state.hotel_df = None

if st.button("🔍 競合データを取得"):

    def fetch_properties(params):
        """SerpApiのGoogle Hotelsエンジンを呼び出し、properties配列を返す"""
        resp = requests.get("https://serpapi.com/search.json", params=params)
        resp.raise_for_status()
        return resp.json().get("properties", [])

    def format_rating(value):
        """評価値を小数第1位に丸める。値が無い場合は「―」を返す"""
        return round(value, 1) if isinstance(value, (int, float)) else "―"

    def extract_property_info(h, prop_type, target_day):
        """ホテル/民泊のプロパティ情報から、比較分析に使う項目を抽出する"""
        rate_info = h.get("rate_per_night", {})
        info = {
            "日付": target_day.strftime("%Y-%m-%d"),
            "種別": prop_type,
            "施設名": h.get("name", "名称不明"),
            "最安値(1泊)": rate_info.get("lowest", "価格なし(満室の可能性)"),
            "最安値_数値": rate_info.get("extracted_lowest"),  # 集計用の数値（満室時はNone）
            "評価スコア": format_rating(h.get("overall_rating")),
            "レビュー数": h.get("reviews", "―"),
            "立地評価": format_rating(h.get("location_rating")),
        }
        if prop_type == "ホテル":
            info["特徴"] = h.get("hotel_class", "―")
        else:
            essential = h.get("essential_info", [])
            info["特徴"] = "、".join(essential) if essential else "―"
        return info

    with st.spinner(f"競合ホテル・民泊データを取得中...（最大{n_calls}回のリクエスト）"):
        try:
            hotel_list = []
            progress = st.progress(0.0)

            for i, d in enumerate(fetch_dates):
                check_in_str = d.strftime("%Y-%m-%d")
                check_out_str = (d + timedelta(days=1)).strftime("%Y-%m-%d")

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

                for h in fetch_properties(base_params):
                    hotel_list.append(extract_property_info(h, "ホテル", d))

                if include_vacation_rentals:
                    vr_params = {**base_params, "vacation_rentals": "true"}
                    for h in fetch_properties(vr_params):
                        hotel_list.append(extract_property_info(h, "民泊", d))

                progress.progress((i + 1) / len(fetch_dates))

            if hotel_list:
                df = pd.DataFrame(hotel_list)
                st.session_state.hotel_df = df
                n_hotel = sum(1 for r in hotel_list if r["種別"] == "ホテル")
                n_vr = sum(1 for r in hotel_list if r["種別"] == "民泊")
                st.success(
                    f"✅ {len(df)}件のデータを取得しました！"
                    f"（ホテル {n_hotel}件・民泊 {n_vr}件／{len(fetch_dates)}日分）"
                )
            else:
                st.error("該当するデータが見つかりませんでした。")
        except requests.exceptions.RequestException as e:
            st.error(f"⚠️ SerpApiへの通信でエラーが発生しました: {e}")
        except Exception as e:
            st.error(f"⚠️ データ取得処理でエラーが発生しました: {e}")

if st.session_state.hotel_df is not None:
    st.dataframe(st.session_state.hotel_df, use_container_width=True)


def summarize_competitors(df):
    """日別に取得した競合データを施設単位で集約する（AIへの入力を軽量化するため）"""
    summary = df.groupby(["種別", "施設名"]).agg(
        平均価格=("最安値_数値", "mean"),
        最安値=("最安値_数値", "min"),
        最高値=("最安値_数値", "max"),
        満室日数=("最安値_数値", lambda s: s.isna().sum()),
        評価スコア=("評価スコア", "first"),
        レビュー数=("レビュー数", "first"),
        立地評価=("立地評価", "first"),
        特徴=("特徴", "first"),
    ).reset_index()
    for col in ["平均価格", "最安値", "最高値"]:
        summary[col] = summary[col].round(0)
    return summary


# ══════════════════════════════════════════════════════════════
# STEP 2: 自社データの入力（部屋タイプ・稼働率・強み・ブッキングカーブ）
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("### STEP 2: 自社の状況・強みの入力")

st.markdown("##### 🏷️ 部屋タイプ別の定価")
st.caption(f"対象月（{year}年{month}月）の日数内訳：{tier_summary_str}")
room_type_df = st.data_editor(
    pd.DataFrame({
        "部屋タイプ": ["ダブルルーム", "2段ベッド", "和室"],
        "定価（日～木）": [12000, 15000, 22000],
        "定価（金・土・祝前日）": [15000, 18000, 30000],
        "部屋数": [6, 3, 2],
        "ベッド数（シングル）": [0, 4, 0],
        "ベッド数（ダブル）": [1, 0, 0],
        "和式ふとん": [0, 0, 4],
    }),
    num_rows="dynamic",
    use_container_width=True,
    key="room_type_editor"
)

st.markdown("##### 📅 対象月の日別稼働率（部屋タイプ別）")

room_types = [rt for rt in room_type_df["部屋タイプ"].tolist() if rt]

if not room_types:
    st.warning("先に部屋タイプ別の定価を1行以上入力してください。")
    occ_df = None
else:
    occ_input_method = st.radio("入力方法", ["CSVアップロード", "手動入力"], horizontal=True)

    occ_df = None
    combo_rows = list(itertools.product(month_dates, room_types))

    if occ_input_method == "CSVアップロード":
        template_df = pd.DataFrame({
            "日付": [d.strftime("%Y-%m-%d") for d, _ in combo_rows],
            "部屋タイプ": [rt for _, rt in combo_rows],
            "稼働率(%)": [0] * len(combo_rows),
        })
        st.download_button(
            "📥 入力用テンプレートCSVをダウンロード",
            data=template_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"occupancy_template_{year}{month:02d}.csv",
            mime="text/csv",
        )
        uploaded = st.file_uploader("「日付」「部屋タイプ」「稼働率(%)」の3列を含むCSVをアップロード", type="csv")
        if uploaded is not None:
            try:
                parsed = pd.read_csv(uploaded)
                required_cols = {"日付", "部屋タイプ", "稼働率(%)"}
                if not required_cols.issubset(parsed.columns):
                    st.error("⚠️ CSVには「日付」「部屋タイプ」「稼働率(%)」の列が必要です。テンプレートをご利用ください。")
                else:
                    occ_df = parsed
                    st.success(f"✅ {len(occ_df)}件の稼働率データを読み込みました")
                    # CSVは編集画面を持たないため、確認用に読み取り専用で1回だけ表示する
                    st.dataframe(occ_df, use_container_width=True)
            except Exception as e:
                st.error(f"⚠️ CSV読み込みエラー: {e}")
    else:
        default_occ = pd.DataFrame({
            "日付": [d.strftime("%Y-%m-%d") for d, _ in combo_rows],
            "部屋タイプ": [rt for _, rt in combo_rows],
            "稼働率(%)": [0] * len(combo_rows),
        })
        # 部屋タイプの構成（種類・数）が変わると行数が変わるため、キーに反映して
        # data_editorの内部状態と表の形が食い違わないようにする
        editor_key = f"occ_editor_{year}_{month}_{'_'.join(room_types)}"
        # data_editor自体が編集可能な表として画面に表示されるため、重複するst.dataframeでの再表示はしない
        occ_df = st.data_editor(default_occ, use_container_width=True, num_rows="fixed", key=editor_key)

st.markdown("##### 📈 稼働率の目標ペース（ブッキングカーブ）")
st.caption(
    "リードタイム（宿泊日までの日数）ごとの目標稼働率です。まずは一般的な目安を初期値にしていますが、"
    "実績が溜まってきたら実際の御社の数値に調整してください。"
)
curve_df = st.data_editor(
    pd.DataFrame({
        "リードタイム(〜日前)": [90, 60, 45, 30, 21, 14, 7, 3, 0],
        "目標稼働率(%)": [10, 25, 35, 50, 60, 70, 80, 90, 97],
    }),
    num_rows="dynamic",
    use_container_width=True,
    key="curve_editor"
)


def get_target_occ(lead_time_days, curve_points):
    """リードタイムから目標稼働率を線形補間で計算する"""
    curve_points = sorted(curve_points, key=lambda x: -x[0])
    lead_times = [c[0] for c in curve_points]
    targets = [c[1] for c in curve_points]
    if lead_time_days >= lead_times[0]:
        return targets[0]
    if lead_time_days <= lead_times[-1]:
        return targets[-1]
    for i in range(len(lead_times) - 1):
        if lead_times[i] >= lead_time_days >= lead_times[i + 1]:
            ratio = (lead_times[i] - lead_time_days) / (lead_times[i] - lead_times[i + 1])
            return targets[i] + ratio * (targets[i + 1] - targets[i])
    return targets[-1]


def compute_phase_table(occ_data, curve_data):
    """各日×部屋タイプについて、リードタイム・目標稼働率・実績との差分・フェーズ判定をまとめる"""
    today = date.today()
    curve_points = list(zip(curve_data["リードタイム(〜日前)"], curve_data["目標稼働率(%)"]))
    rows = []
    for _, r in occ_data.iterrows():
        try:
            d = pd.to_datetime(r["日付"]).date()
            actual = float(r["稼働率(%)"])
        except (ValueError, TypeError):
            continue
        lead_time = (d - today).days
        target = get_target_occ(lead_time, curve_points)
        diff = actual - target
        if lead_time < 0:
            phase = "対象外（過去日）"
        elif diff >= 10:
            phase = "絶好調（値上げ余地あり）"
        elif diff >= -5:
            phase = "順調"
        elif diff >= -20:
            phase = "やや遅れ"
        else:
            phase = "大幅に遅れ（要テコ入れ）"
        rows.append({
            "日付": r["日付"], "部屋タイプ": r.get("部屋タイプ", "―"), "リードタイム(日)": lead_time,
            "実績稼働率(%)": round(actual, 1), "目標稼働率(%)": round(target, 1),
            "差分(pt)": round(diff, 1), "フェーズ": phase,
        })
    return pd.DataFrame(rows)


st.markdown("##### 🏨 自社ホテルの「強み・特徴」（あてはまるものを全てチェック）")
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

other_strength = st.text_input("その他、独自の強みがあれば入力（任意）", placeholder="例：レディースフロアを完備している")
if other_strength:
    strengths_list.append(other_strength)

own_strengths_str = "、".join(strengths_list)

# ══════════════════════════════════════════════════════════════
# STEP 3: AI分析の実行
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("### STEP 3: AIによる月次収益改善策の生成")

SYSTEM_PROMPT = """あなたは小規模ビジネスホテル専門の辣腕レベニューマネージャー兼採用マーケターです。
提供されたデータをもとに、以下の構成で具体的な月次収益改善策を提案してください。

【入力データの内容】
・自社の部屋タイプ別定価（日～木／金・土・祝前日の2区分、部屋数・ベッド構成を含む）
・対象月の日別×部屋タイプ別の稼働ペース（実績稼働率・目標稼働率との差分・フェーズ判定）
・自社の強み・特徴
・競合（ホテル・民泊）の月内の価格傾向・評価データ（施設ごとに集約済み）

出力は必ず以下の4部構成にしてください。

1.【競合ポジショニング分析】
提供された競合データを、以下の3つに分類し、それぞれ根拠とともに提示してください。
・上位互換：価格が同等以下で、評価・立地・設備が自社を上回り、直接的な脅威となる施設
・直接競合：価格帯・ターゲット層が自社とほぼ重なる施設
・競合外：価格帯やターゲット層が大きく異なり、実質的に競合しない施設

2.【稼働ペースの評価】
提供された日別のフェーズ判定を踏まえ、対象月全体としてペースが順調か遅れているかを総括してください。
特に「大幅に遅れ」の日がある場合は、その日付とリードタイムに具体的に言及してください。

3.【収益改善策】
上記の分析を踏まえ、以下の3カテゴリに分けて具体策を提示してください。
稼働ペースが遅れている日ほど積極的な施策を、順調な日はむしろ値上げや上質な訴求を検討してください。
  a. 期間限定割引（プロモーション）：具体的な割引率・対象日・訴求文言案
  b. アメニティ施策：追加コストが低く実行しやすい施策
  c. それ以外の施策：客室稼働以外の視点（採用ブランディング、口コミ施策等）を含む中長期的な施策

4.【新規ペルソナ開発】
自社の強み（内部環境）と競合状況（外部環境）の隙間を突く、新しい顧客ペルソナを2パターン提案してください。
将来的な採用ブランディングの軸としても機能するよう具体的に描写してください。"""

if st.button("🚀 AIで月次収益改善策を生成する", type="primary"):
    if st.session_state.hotel_df is None:
        st.warning("先にSTEP1で競合データを取得してください。")
    elif room_type_df is None or room_type_df.empty:
        st.warning("部屋タイプ別の定価を入力してください。")
    elif occ_df is None or occ_df.empty:
        st.warning("対象月の日別稼働率を入力（またはCSVアップロード）してください。")
    elif len(strengths_list) == 0:
        st.warning("自社の強み・特徴を少なくとも1つ選択（または入力）してください。")
    else:
        competitor_summary = summarize_competitors(st.session_state.hotel_df)
        phase_table = compute_phase_table(occ_df, curve_df)

        user_message = (
            f"【対象月】{year}年{month}月（{tier_summary_str}）\n\n"
            f"【自社の部屋タイプ別定価】\n{room_type_df.to_string(index=False)}\n\n"
            f"【自社の強み・特徴】\n{own_strengths_str}\n\n"
            f"【対象月の稼働ペース（日別×部屋タイプ）】\n{phase_table.to_string(index=False)}\n\n"
            f"【競合の状況（ホテル・民泊、施設ごとに集約）】\n{competitor_summary.to_string(index=False)}"
        )

        with st.spinner("AIが月次データを分析し、戦略を立案中...（1分ほどかかる場合があります）"):
            try:
                client = anthropic.Anthropic(api_key=api_key)
                result_placeholder = st.empty()
                full_response = ""

                with client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=8192,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        result_placeholder.markdown(full_response)

            except Exception as e:
                st.error(f"AI分析中にエラーが発生しました: {e}")
