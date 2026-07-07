import streamlit as st
import requests
import pandas as pd

st.title("🏨 競合ホテル価格一覧・自動取得テスト")

# ご自身のSerpApiキーに書き換えてください
API_KEY = "f1f8fc43f8679a5f6b8f4d5d5195d22de0fa512f1f93017f1e24464c5a9c6d35"

# 検索パラメータの設定
params = {
    "engine": "google_hotels",
    "q": "大阪本町 ホテル",         # エリア検索キーワード
    "check_in_date": "2026-09-09", # 計測したい日付
    "check_out_date": "2026-09-10",
    "adults": "1",                 # 宿泊人数
    "currency": "JPY",
    "gl": "jp",
    "hl": "ja",
    "api_key": API_KEY
}

url = "https://serpapi.com/search.json"

st.write(f"🔍 **「{params['q']}」の {params['check_in_date']} の価格リストを取得中...**")

try:
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    data = response.json()
    properties = data.get("properties", [])
    
    if properties:
        st.success(f"✅ {len(properties)}件のホテル情報を取得しました！")
        
        # 取得したデータをリストにまとめる
        hotel_list = []
        for hotel in properties:
            name = hotel.get("name", "名称不明")
            price_info = hotel.get("rate_per_night", {})
            
            # 価格がない場合は「満室の可能性」として処理
            lowest_price = price_info.get("lowest", "価格情報なし(満室の可能性)")
            
            hotel_list.append({
                "ホテル名": name,
                "最安値(1泊)": lowest_price
            })
        
        # データフレーム（表）にして画面に表示
        df = pd.DataFrame(hotel_list)
        df.index = df.index + 1 # 番号を1から始める
        
        st.dataframe(df, use_container_width=True)
        
    else:
        st.error("❌ 指定した条件でホテルが見つかりませんでした。")
        
except Exception as e:
    st.error(f"通信エラーが発生しました: {e}")
