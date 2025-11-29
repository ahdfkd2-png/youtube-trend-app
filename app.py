import os
import streamlit as st
from googleapiclient.discovery import build

# 1) API 키 가져오기
API_KEY = os.getenv("YOUTUBE_API_KEY")

st.title("📊 YouTube 트렌드 분석기 (v1.0)")
st.write("키워드를 입력하면 관련 영상/채널 데이터를 분석합니다.")

# API 키 없을 때 안내
if not API_KEY:
    st.error("❗ 환경 변수 `YOUTUBE_API_KEY`가 설정되지 않았습니다.\n"
             "Streamlit Cloud → App Settings → Secrets 에서 추가하세요.")
    st.stop()

# YouTube API 클라이언트 생성
def create_youtube_client():
    return build("youtube", "v3", developerKey=API_KEY)

youtube = create_youtube_client()

# 검색창
keyword = st.text_input("🔍 검색 키워드 입력 (예: 시니어 쇼핑, 건강, 요리)")

# 영상 검색 함수
def search_videos(q, max_results=20):
    request = youtube.search().list(
        part="snippet",
        type="video",
        q=q,
        maxResults=max_results
    )
    response = request.execute()

    video_data = []
    for item in response["items"]:
        video_data.append({
            "제목": item["snippet"]["title"],
            "영상 ID": item["id"]["videoId"],
            "채널명": item["snippet"]["channelTitle"],
            "업로드 날짜": item["snippet"]["publishedAt"],
            "영상 링크": f"https://youtu.be/{item['id']['videoId']}"
        })
    return video_data

# 채널 검색 함수
def search_channels(q, max_results=10):
    request = youtube.search().list(
        part="snippet",
        type="channel",
        q=q,
        maxResults=max_results
    )
    response = request.execute()

    channel_data = []
    for item in response["items"]:
        channel_data.append({
            "채널명": item["snippet"]["title"],
            "설명": item["snippet"]["description"],
            "채널 ID": item["id"]["channelId"],
            "채널 링크": f"https://youtube.com/channel/{item['id']['channelId']}"
        })
    return channel_data


# 검색 실행
if keyword:
    st.subheader(f"📺 '{keyword}' 인기 영상 Top 20")
    videos = search_videos(keyword)
    st.dataframe(videos)

    st.subheader(f"📌 '{keyword}' 관련 채널 Top 10")
    channels = search_channels(keyword)
    st.dataframe(channels)
