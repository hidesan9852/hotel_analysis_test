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
# （これ以降のコードは、前回のテストコードのまま繋げてください）
