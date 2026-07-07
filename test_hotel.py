import streamlit as st
import requests
import pandas as pd
import anthropic
import os

st.title("🏨 ホテル収益改善＆ペルソナ提案ツール")

# ── APIキーの設定（Streamlitの金庫から呼び出す） ────────────────
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except:
        st.error("⚠️ Anthropic APIキーが設定されていません。StreamlitのSecretsを確認してください。")
        st.stop()

try:
    SERP_API_KEY = st.secrets["SERP_API_KEY"]
except:
    st.error("⚠️ SerpApiキーが設定されていません。StreamlitのSecretsを確認してください。")
    st.stop()

# ── 1. 競合データの取得設定 ─────────────────────────────────
st.markdown("### STEP 1: 競合データの自動取得")
col1, col2 = st.columns(2)
with col1:
    search_area = st.text_input("検索エリア・キーワード", value="大阪本町 ホテル")
with col2:
    target_date = st.text_input("計測する宿泊日", value="2026-09-09")

if "hotel_df" not in st.session_state:
    st.session_state.hotel_df = None

if st.button("🔍 競合ホテルの価格を取得"):
    params = {
        "engine": "google_hotels",
        "q": search_area,
        "check_in_date": target_date,
        "check_out_date": "2026-09-10",
        "adults": "1",
        "currency": "JPY",
        "gl": "jp",
        "hl": "ja",
        "api_key": SERP_API_KEY
    }
    
    with st.spinner("競合データを取得中..."):
        try:
            resp = requests.get("https://serpapi.com/search.json", params=params)
            resp.raise_for_status()
            properties = resp.json().get("properties", [])
            
            if properties:
                hotel_list = []
                for h in properties:
                    name = h.get("name", "名称不明")
                    price = h.get("rate_per_night", {}).get("lowest", "価格なし(満室の可能性)")
                    hotel_list.append({"ホテル名": name, "最安値(1泊)": price})
                
                df = pd.DataFrame(hotel_list)
                df.index = df.index + 1
                st.session_state.hotel_df = df
                st.success(f"✅ {len(df)}件の競合データを取得しました！")
            else:
                st.error("ホテルが見つかりませんでした。")
        except Exception as e:
            st.error(f"エラー: {e}")

if st.session_state.hotel_df is not None:
    st.dataframe(st.session_state.hotel_df, use_container_width=True)

# ── 2. 自社データの入力とAI分析 ──────────────────────────────
st.markdown("---")
st.markdown("### STEP 2: 自社の状況入力とAI分析")
col3, col4 = st.columns(2)
with col3:
    own_price = st.text_input("自社ホテルの現在の設定価格（円）", placeholder="例: 7500")
with col4:
    own_occ = st.text_input("現在の予約稼働率（OCC）", placeholder="例: 40%")

# AIへの指示書（システムプロンプト）
SYSTEM_PROMPT = """あなたは小規模ビジネスホテル専門の辣腕レベニューマネージャー兼マーケターです。
提供された【自社データ】と【競合20件の価格データ】をもとに、以下の3点を論理的に提案してください。

1. 【ダイナミックプライシング提案】
競合の価格帯（満室表示を含む）から現在の市場の需要逼迫度を推測し、自社の適正な販売価格をズバリ提案し、その根拠を解説してください。

2. 【新規ターゲットのペルソナ開発】
激戦エリアにおいて価格競争に巻き込まれないための、新しい顧客ペルソナを2パターン提案してください。このペルソナは、将来的なホテルの独自性強化や、求職者に対する魅力的な「採用ブランディング」にも直結するような、具体的でエッジの効いたものにしてください。

3. 【具体的な宿泊プラン・アメニティ施策】
提案したペルソナに向けた、明日から実行できる具体的な宿泊プランのタイトル案や、アメニティのアイデアを提案してください。"""

if st.button("🚀 AIで収益改善策を生成する", type="primary"):
    if st.session_state.hotel_df is None:
        st.warning("先にSTEP1で競合データを取得してください。")
    elif not own_price or not own_occ:
        st.warning("自社の価格と稼働率を入力してください。")
    else:
        user_message = f"【自社ホテルの現状】\n設定価格: {own_price}円\n現在の稼働率: {own_occ}\n\n【競合ホテルの状況（20件）】\n{st.session_state.hotel_df.to_string()}"
        
        with st.spinner("AIが競合データと市場動向を分析・立案中...（数十秒かかります）"):
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
