import streamlit as st
import requests

st.title("🏨 ホテル価格取得テスト")

# ご自身のSerpApiキーに書き換えてください
API_KEY = "f1f8fc43f8679a5f6b8f4d5d5195d22de0fa512f1f93017f1e24464c5a9c6d35"

# 検索パラメータの設定
params = {
    "engine": "google_hotels",
    "q": "スーパーホテル 大阪本町", # 計測したい特定のホテル名
    "check_in_date": "2026-09-09",        # 計測したい日付
    "check_out_date": "2026-09-10",       # チェックアウト日
    "adults": "1",                        # 宿泊人数
    "currency": "JPY",
    "gl": "jp",
    "hl": "ja",
    "api_key": API_KEY
}

url = "https://serpapi.com/search.json"

st.write(f"🔍 **「{params['q']}」の {params['check_in_date']} の価格を取得中...**")

try:
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    data = response.json()
    properties = data.get("properties", [])
    
    if properties:
        target_hotel = properties[0]
        hotel_name = target_hotel.get("name", "名称不明")
        
        price_info = target_hotel.get("rate_per_night", {})
        lowest_price = price_info.get("lowest", "価格情報なし")
        
        st.success("✅ 取得完了！")
        st.markdown("---")
        st.markdown(f"### 🏨 ホテル名: {hotel_name}")
        st.markdown(f"### 💰 最安値(1泊): {lowest_price}")
        
        if not price_info:
            st.warning("⚠️ 価格が出力されませんでした。満室（×表示）である可能性が高いです。")
        st.markdown("---")
        
    else:
        st.error("❌ 指定したホテルの情報が見つかりませんでした。")
        
except Exception as e:
    st.error(f"通信エラーが発生しました: {e}")
