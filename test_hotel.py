import streamlit as st
import requests
import pandas as pd
import anthropic
import os
import calendar
import itertools
import math
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

st.markdown("##### 📍 自社ホテルの位置（競合との距離判定に使用）")
col_lat, col_lon, col_tier1, col_tier2 = st.columns(4)
with col_lat:
    own_lat = st.number_input("緯度", value=34.642325149435656, format="%.8f")
with col_lon:
    own_lon = st.number_input("経度", value=135.4975678362115, format="%.8f")
with col_tier1:
    walk_zone_km = st.number_input("徒歩圏内とみなす距離(km)", value=1.0, min_value=0.1, step=0.1)
with col_tier2:
    nearby_zone_km = st.number_input("近隣エリアとみなす距離(km)", value=3.0, min_value=0.1, step=0.1)


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """2点間の距離をkm単位で計算する（ハーバサイン公式）"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_area_tier(distance_km):
    """自社ホテルからの距離に応じてエリア区分を判定する"""
    if distance_km is None:
        return "不明"
    if distance_km <= walk_zone_km:
        return f"徒歩圏内（{walk_zone_km}km以内）"
    elif distance_km <= nearby_zone_km:
        return f"近隣エリア（{walk_zone_km}〜{nearby_zone_km}km）"
    else:
        return f"エリア外（{nearby_zone_km}km超）"

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
        gps = h.get("gps_coordinates", {})
        comp_lat, comp_lon = gps.get("latitude"), gps.get("longitude")
        if comp_lat is not None and comp_lon is not None:
            distance_km = round(haversine_distance_km(own_lat, own_lon, comp_lat, comp_lon), 2)
        else:
            distance_km = None

        info = {
            "日付": target_day.strftime("%Y-%m-%d"),
            "種別": prop_type,
            "施設名": h.get("name", "名称不明"),
            "距離(km)": distance_km,
            "エリア区分": get_area_tier(distance_km),
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
    # コード更新前に取得した古いデータには無い列がある場合に備え、無ければ補完する
    missing_cols = {"距離(km)": None, "エリア区分": "不明（再取得推奨）"}
    for col, default in missing_cols.items():
        if col not in df.columns:
            df = df.copy()
            df[col] = default

    summary = df.groupby(["種別", "施設名"]).agg(
        距離km=("距離(km)", "first"),
        エリア区分=("エリア区分", "first"),
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
    summary = summary.sort_values("距離km", na_position="last").reset_index(drop=True)
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
        "定価（日～木）": [7500, 6500, 7000],
        "定価（金・土・祝前日）": [9000, 8000, 8500],
        "最低販売価格（原価ベース・円）": [5000, 4500, 4800],
        "部屋数": [4, 4, 3],
        "ベッド数（シングル）": [0, 4, 0],
        "ベッド数（ダブル）": [1, 0, 0],
        "和式ふとん": [0, 0, 2],
    }),
    num_rows="dynamic",
    use_container_width=True,
    key="room_type_editor"
)
st.caption("「最低販売価格」は清掃費など変動費を踏まえた、これ以上は値引きしない下限ラインです。AIの割引提案がこれを下回らないようにするために使います。")

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
    if st.checkbox("💻 高速Wi-Fi"): strengths_list.append("高速Wi-Fi")
    if st.checkbox("🖥️ ワークスペース"): strengths_list.append("ワークスペース")

with chk_col2:
    if st.checkbox("🧴 充実したアメニティ"): strengths_list.append("充実したアメニティ")
    if st.checkbox("🍷 ウェルカムドリンク"): strengths_list.append("ウェルカムドリンク")
    if st.checkbox("🧹 清掃が徹底されている"): strengths_list.append("清掃が徹底されている")
    if st.checkbox("😊 アットホームな接客"): strengths_list.append("アットホームな接客")
    if st.checkbox("🅿️ 駐車場あり・安い"): strengths_list.append("駐車場あり・安い")

with chk_col3:
    if st.checkbox("🔑 セルフチェックアウト"): strengths_list.append("セルフチェックアウト")
    if st.checkbox("🔐 キーボックス"): strengths_list.append("キーボックス")
    if st.checkbox("🌅 アーリーチェックイン可能"): strengths_list.append("アーリーチェックイン可能")
    if st.checkbox("🧳 チェックアウト後の無料荷物預かり"): strengths_list.append("チェックアウト後の無料荷物預かり")

other_strength = st.text_input("その他、独自の強みがあれば入力（任意）", placeholder="例：レディースフロアを完備している")
if other_strength:
    strengths_list.append(other_strength)

own_strengths_str = "、".join(strengths_list)

st.markdown("##### 💳 OTAコミッション・Genius割引")
col7, col8, col9 = st.columns(3)
with col7:
    booking_commission_rate = st.number_input(
        "Booking.comのコミッション率（%）",
        min_value=0.0, max_value=100.0, value=22.0, step=0.5,
        help="宿泊料金に対する手数料率です。割引提案の実質的な手取り額を考慮するためにAIへ渡します。"
    )
with col8:
    genius_member_ratio = st.number_input(
        "予約者に占めるGenius会員比率（%）",
        min_value=0.0, max_value=100.0, value=90.0, step=1.0,
        help="Genius会員には、表示価格からさらに割引が適用されます。比率が高いほど、床値チェックでGeniusとの重複適用を重視する必要があります。"
    )
with col9:
    genius_discount_rate = st.number_input(
        "Genius割引率の目安（%）",
        min_value=0.0, max_value=100.0, value=15.0, step=1.0,
        help="Genius会員向けの追加割引率です（レベルにより10〜15%程度）。最悪ケースを想定する場合は15%を推奨します。"
    )
st.caption("Genius会員比率が高い場合、期間限定割引の価格からさらにGenius割引が重複適用される前提で床値をチェックする必要があります。")

st.markdown("##### 📋 収益改善策の検討にあたっての事前情報")
st.caption("AIが既存の取り組みと重複しない、あるいは過去に効果が薄かった施策を避けた提案をするための情報です。")

st.markdown("###### 現在実施中のプロモーション（割引系）")
st.caption("Genius併用可のプロモーションは、AIが新規割引の床値チェックの際に自動で重複適用を想定します。")
promotions_df = st.data_editor(
    pd.DataFrame({
        "プロモーション名": ["3連泊割", "4連泊割", "モバイル割", "地域割"],
        "割引率(%)": [20, 25, 10, 15],
        "Genius併用可否": ["可", "可", "可", "可"],
        "対象部屋タイプ": ["全タイプ", "全タイプ", "全タイプ", "全タイプ"],
    }),
    num_rows="dynamic",
    use_container_width=True,
    key="promotions_editor"
)

col5, col6 = st.columns(2)
with col5:
    current_amenities = st.text_area(
        "現在行っているアメニティ施策",
        placeholder="例：ウェルカムドリンクサービス\n大阪土産の割引クーポン配布",
        height=100,
    )
with col6:
    cancellation_policy = st.text_input(
        "現在のキャンセルポリシー",
        value="14日前まで無料キャンセル",
        help="深い割引とゆるいキャンセルポリシーの組み合わせは、ファントム予約（仮予約）のリスクを高めます。"
    )

st.markdown("###### 過去に実施した施策とその効果（任意）")
past_measures_df = st.data_editor(
    pd.DataFrame({"施策内容": [""], "実施時期": [""], "結果・効果": [""]}),
    num_rows="dynamic",
    use_container_width=True,
    key="past_measures_editor"
)

# ══════════════════════════════════════════════════════════════
# STEP 3: AI分析の実行
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("### STEP 3: AIによる月次収益改善策の生成")

SYSTEM_PROMPT = """あなたは小規模ビジネスホテル専門の辣腕レベニューマネージャー兼採用マーケターです。
提供されたデータをもとに、以下の構成で具体的な月次収益改善策を提案してください。

【入力データの内容】
・自社の部屋タイプ別定価（日～木／金・土・祝前日の2区分、最低販売価格、部屋数・ベッド構成を含む）
・Booking.comのコミッション率、Genius会員比率、Genius割引率の目安
・現在実施中のプロモーション一覧（プロモーション名・割引率・Genius併用可否・対象部屋タイプ）
・現在のキャンセルポリシー
・対象月の日別×部屋タイプ別の稼働ペース（実績稼働率・目標稼働率との差分・フェーズ判定）
・自社の強み・特徴
・現在実施中のアメニティ施策、および過去に実施した施策とその効果
・競合（ホテル・民泊）の月内の価格傾向・評価データ（自社からの距離・エリア区分を含み、施設ごとに集約済み）

【参考:大阪府宿泊税（1人1泊あたり、素泊まり料金基準）】
5,000円未満：非課税／5,000円以上15,000円未満：200円／15,000円以上20,000円未満：400円／20,000円以上：500円

【厳守事項】
・期間限定割引を提案する際は、必ず各部屋タイプの「最低販売価格」を下回らない範囲で価格を設計してください。
・Booking.com経由での予約を想定した施策では、コミッション控除後の実質手取り額が最低販売価格を下回らないよう注意してください。
・Genius会員比率が一定以上ある場合、期間限定割引の価格に対してさらにGenius割引が重複適用される前提で床値をチェックしてください。「表示価格→Genius割引控除→Booking.comコミッション控除」の順で実質手取りを計算し、それでも最低販売価格を上回ることを確認したうえで割引率を決定してください。この重複適用チェックを省略しないでください。
・現在実施中のプロモーション一覧のうち「Genius併用可」のものについては、新規に提案する割引・値上げ日にもそのプロモーションが有効に働くことを踏まえ、床値チェックは新規施策単体ではなく「新規施策×既存の併用可プロモーション×Genius」が同時に重なる最悪ケースで行ってください。複数の併用可プロモーションが同時に該当しうる場合は、それらも重ねて計算してください。
・深い割引率（目安30%超）を、リードタイムが長く（目安30日超）、かつ現在のキャンセルポリシーが緩い（無料キャンセル期限が宿泊日に近い）条件で提案する場合は、ファントム予約（仮予約からの直前キャンセル）のリスクを一言指摘し、非返金プランの併設を検討するよう促してください。
・ある部屋タイプを値上げする一方で、同時期に別の部屋タイプを大幅値引きする施策を組み合わせないでください。値上げした部屋タイプの需要が、安い部屋タイプに流出する「共食い（カニバリゼーション）」が起きないよう、値上げ対象日と大幅値引き対象日が重ならないようにするか、重ねる場合はその共食いリスクに一言言及してください。
・提案する価格が大阪府宿泊税の階層（上記参考情報）をまたぐ場合、部屋代の値上げ・値引き幅に加えて宿泊税額も変わり、ゲストの総支払額への心理的インパクトが変わる点を踏まえてください。階層をまたぐ直前で価格を止める、またはまたぐことを許容して上質な価格帯に振り切るか、狙いを明確にして価格を設計してください。
・現在実施中の施策とは重複しない、新しい切り口を提案してください。
・過去に実施して効果が薄かった施策と同種のものは避け、理由も一言添えてください。過去に効果が高かった施策は、発展させる形で活用してください。

出力は必ず以下の4部構成にしてください。

1.【競合ポジショニング分析】
提供された競合データ（自社ホテルからの距離・エリア区分を含む）をもとに、以下の3つに分類し、それぞれ根拠とともに提示してください。
・上位互換：価格が同等以下で、評価・立地・設備が自社を上回り、直接的な脅威となる施設
・直接競合：価格帯・ターゲット層が自社とほぼ重なる施設
・競合外：価格帯やターゲット層が大きく異なり、実質的に競合しない施設
距離が近いほど直接競合になりやすい一方、距離が離れていても価格帯・客層が近い施設は間接的な競合として言及してください。
自社から最も距離が近い競合施設は、価格帯や評価にかかわらず必ずいずれかのカテゴリで言及してください（脅威が小さい場合はその旨も含めて構いません）。名称の一部が共通し同一運営者と推測される複数出品（同じ建物の部屋違いなど）は、個別に列挙せず「〇〇シリーズ」としてまとめて扱ってください。

2.【稼働ペースの評価】
提供された日別のフェーズ判定を踏まえ、対象月全体としてペースが順調か遅れているかを総括してください。
特に「大幅に遅れ」の日がある場合は、その日付とリードタイムに具体的に言及してください。

3.【収益改善策】
上記の分析を踏まえ、以下の3カテゴリに分けて具体策を提示してください。
稼働ペースが遅れている日ほど積極的な施策を、順調な日はむしろ値上げや上質な訴求を検討してください。
  a. 期間限定割引（プロモーション）：具体的な割引率・対象日・訴求文言案（最低販売価格を下回らないこと）
  b. アメニティ施策：追加コストが低く実行しやすい施策
  c. それ以外の施策：客室稼働以外の視点（採用ブランディング、口コミ施策等）を含む中長期的な施策
値上げを提案する日付については、現在実施中のプロモーション一覧の中に、その日にも適用されうる長期滞在割・地域割・モバイル割等が存在する場合、値上げの効果を打ち消さないよう「該当プロモーションの対象外（ブラックアウト）に追加する」旨を明記してください。
値上げを提案する部屋タイプと、同時期に大幅値引きを提案する部屋タイプが重ならないか最後に確認し、重なる場合はその意図（狙って併用しているのか、調整漏れか）を明記してください。

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

        # 過去の施策履歴は「施策内容」が空の行を除外してから渡す
        valid_past_measures = past_measures_df[
            past_measures_df["施策内容"].astype(str).str.strip() != ""
        ]
        past_measures_str = (
            valid_past_measures.to_string(index=False)
            if not valid_past_measures.empty else "特になし"
        )

        # 割引率が入力されていないプロモーション行は除外
        valid_promotions = promotions_df[
            promotions_df["プロモーション名"].astype(str).str.strip() != ""
        ]
        promotions_str = (
            valid_promotions.to_string(index=False)
            if not valid_promotions.empty else "現在実施中のプロモーションなし"
        )

        user_message = (
            f"【対象月】{year}年{month}月（{tier_summary_str}）\n\n"
            f"【自社の部屋タイプ別定価】\n{room_type_df.to_string(index=False)}\n\n"
            f"【Booking.comのコミッション率】{booking_commission_rate}%\n"
            f"【Genius会員比率】{genius_member_ratio}%\n"
            f"【Genius割引率の目安】{genius_discount_rate}%\n\n"
            f"【自社の強み・特徴】\n{own_strengths_str}\n\n"
            f"【現在実施中のプロモーション一覧】\n{promotions_str}\n\n"
            f"【現在のキャンセルポリシー】{cancellation_policy or '未設定'}\n\n"
            f"【現在実施中のアメニティ施策】\n{current_amenities or '特になし'}\n\n"
            f"【過去に実施した施策とその効果】\n{past_measures_str}\n\n"
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
                    max_tokens=20000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        result_placeholder.markdown(full_response)

            except Exception as e:
                st.error(f"AI分析中にエラーが発生しました: {e}")
