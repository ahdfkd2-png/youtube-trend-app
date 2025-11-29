import streamlit as st
import re
import os
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ----------------------------
# 기본 설정
# ----------------------------

st.set_page_config(
    page_title="YouTube 트렌드·채널 분석기 (v3.0 - UPGRADED)",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* 전체 폰트/여백 조금 다듬기 */
    .main-block {padding-top: 0rem;}
    .block-container {padding-top: 1.5rem;}
    /* 전문 분석 도구 느낌을 위한 헤더 폰트 크기 조정 */
    h1 {font-size: 2.2rem;} 
    h2 {font-size: 1.7rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------
# 유틸 함수 (UTILITIES)
# ----------------------------

def get_api_key() -> str:
    """Streamlit Secrets 에서 API KEY 가져오기"""
    key = st.secrets.get("YOUTUBE_API_KEY", "")
    if not key:
        st.error("❌ YOUTUBE_API_KEY 가 설정되지 않았습니다. Streamlit → App Settings → Secrets 에서 설정해 주세요.")
    return key


def build_youtube(api_key: str):
    """YouTube 클라이언트 생성"""
    return build("youtube", "v3", developerKey=api_key)


def parse_iso_duration(duration: str) -> int:
    """ISO8601 duration(예: 'PT15M33S') → 초 단위 정수로 변환"""
    if not duration:
        return 0
    pattern = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
    match = pattern.match(duration)
    if not match:
        return 0
    hours, mins, secs = match.groups()
    hours = int(hours) if hours else 0
    mins = int(mins) if mins else 0
    secs = int(secs) if secs else 0
    return hours * 3600 + mins * 60 + secs


def weekday_kr_from_ts(ts: pd.Timestamp) -> str:
    """요일을 한국어 한 글자로 반환"""
    mapping = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    return mapping.get(ts.weekday(), "")


def extract_channel_id(raw: str) -> str:
    """사용자가 입력한 값에서 channelId 추출"""
    raw = raw.strip()
    if "youtube.com/channel/" in raw:
        return raw.split("youtube.com/channel/")[-1].split("/")[0].split("?")[0]
    if "youtube.com/" in raw:
        path = raw.split("youtube.com/")[-1]
        return path.split("/")[-1].split("?")[0]
    return raw


def safe_int(x):
    try:
        return int(x)
    except Exception:
        return 0


def format_korean_unit(number):
    """숫자를 한국어 단위(만, 억)로 포맷팅"""
    if number >= 100000000:
        return f"{number / 100000000:.1f}억"
    elif number >= 10000:
        return f"{number / 10000:.1f}만"
    else:
        return f"{number:,}"

# --- UPGRADE: 3단계 - 히스토리 저장/로드 및 등급 부여 함수 추가 ---

HISTORY_FILE = "channel_history.json"


def save_channel_history(history_data: Dict):
    """채널 히스토리 데이터를 JSON 파일에 저장"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"❌ 히스토리 저장 실패: {e}")


def load_channel_history() -> Dict:
    """JSON 파일에서 채널 히스토리 데이터를 불러오기"""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def assign_channel_grade(info: Dict, recent_df: pd.DataFrame) -> str:
    """채널 등급 (A1~C3)을 부여하는 간단한 로직"""
    if recent_df.empty or info['subscriber_count'] == 0:
        return "등급 외"

    # 1. 규모 점수 (구독자 수) - 40%
    sub_count = info['subscriber_count']
    if sub_count >= 100000: rank_sub = 3
    elif sub_count >= 10000: rank_sub = 2
    else: rank_sub = 1
    
    # 2. 활동 점수 (일 평균 조회수) - 60%
    avg_daily_views = recent_df['views_per_day'].mean()
    if sub_count > 0:
        daily_ratio = avg_daily_views / sub_count * 1000
    else:
        daily_ratio = 0
        
    if daily_ratio >= 10: rank_activity = 3
    elif daily_ratio >= 3: rank_activity = 2
    else: rank_activity = 1
    
    final_score = (rank_sub * 0.4 + rank_activity * 0.6)
    
    if final_score >= 2.6: grade_char = 'A'
    elif final_score >= 1.8: grade_char = 'B'
    else: grade_char = 'C'
        
    grade_num = 1
    if sub_count < 10000: grade_num = 3
    elif sub_count < 50000 and daily_ratio < 5: grade_num = 2 
    elif sub_count >= 100000 and daily_ratio >= 10: grade_num = 1
        
    return f"{grade_char}{grade_num}"


def get_channel_summary_row(info: Dict, df: pd.DataFrame) -> Dict:
    """채널 히스토리에 저장할 핵심 데이터 요약"""
    if df.empty:
        return {}
        
    now = datetime.now(timezone.utc)
    recent_30d = df[df["published_at"] > (now - timedelta(days=30))]

    row = {
        "channel_id": info["channel_id"],
        "title": info["title"],
        "subscriber_count": info["subscriber_count"],
        "total_views": info["view_count"],
        "video_count": info["video_count"],
        "analysis_date": now.strftime('%Y-%m-%d %H:%M'),
        "recent_video_count": len(df),
        "recent_avg_views": int(df["views"].mean()),
        "recent_avg_daily_views": int(df["views_per_day"].mean()),
        "videos_last_30d": len(recent_30d),
        "grade": assign_channel_grade(info, df)
    }
    return row

# ----------------------------
# 데이터 가져오기 (캐시 적용)
# ----------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_videos_by_keyword(api_key: str, keyword: str, max_results: int) -> pd.DataFrame:
    # (기존 코드와 동일하게 유지)
    youtube = build_youtube(api_key)
    max_results = max(1, min(max_results, 50))
    search_resp = youtube.search().list(
        part="snippet", q=keyword, type="video", order="relevance", maxResults=max_results,
    ).execute()

    video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
    if not video_ids: return pd.DataFrame()

    videos_resp = youtube.videos().list(
        part="snippet,contentDetails,statistics", id=",".join(video_ids), maxResults=len(video_ids),
    ).execute()

    rows = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {}); stats = item.get("statistics", {}); content = item.get("contentDetails", {})
        published_at = snippet.get("publishedAt")
        try: ts = pd.to_datetime(published_at).replace(tzinfo=timezone.utc)
        except Exception: ts = pd.NaT
        duration_sec = parse_iso_duration(content.get("duration", ""))

        rows.append(
            {
                "video_id": item.get("id"), "title": snippet.get("title"), "description": snippet.get("description", ""),
                "channel_title": snippet.get("channelTitle"), "channel_id": snippet.get("channelId"), "published_at": ts,
                "views": safe_int(stats.get("viewCount")), "likes": safe_int(stats.get("likeCount")), "comments": safe_int(stats.get("commentCount")),
                "duration_sec": duration_sec, "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty: return df
    now = datetime.now(timezone.utc)
    df["days_since_publish"] = (now - df["published_at"]).dt.total_seconds() / (3600 * 24)
    df["days_since_publish"] = df["days_since_publish"].replace(0, 0.1)
    df["views_per_day"] = df["views"] / df["days_since_publish"]
    df["duration_min"] = df["duration_sec"] / 60
    df["weekday"] = df["published_at"].apply(weekday_kr_from_ts)
    df["publish_hour"] = df["published_at"].dt.hour
    df["max_watch_time_min"] = df["duration_min"] * df["views"]
    return df.sort_values("views", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_basic(api_key: str, channel_id: str) -> Dict:
    # (기존 코드와 동일하게 유지)
    youtube = build_youtube(api_key)
    resp = youtube.channels().list(
        part="snippet,statistics,contentDetails", id=channel_id, maxResults=1,
    ).execute()

    items = resp.get("items", [])
    if not items: return {}

    item = items[0]
    stats = item.get("statistics", {}); snippet = item.get("snippet", {})

    return {
        "channel_id": item.get("id"), "title": snippet.get("title"), "description": snippet.get("description", ""),
        "published_at": pd.to_datetime(snippet.get("publishedAt")).replace(tzinfo=timezone.utc),
        "subscriber_count": safe_int(stats.get("subscriberCount")), "video_count": safe_int(stats.get("videoCount")),
        "view_count": safe_int(stats.get("viewCount")), "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_recent_videos(
    api_key: str, channel_id: str, max_results: int
) -> pd.DataFrame:
    # (기존 코드와 동일하게 유지)
    youtube = build_youtube(api_key)
    max_results = max(1, min(max_results, 50))
    search_resp = youtube.search().list(
        part="snippet", channelId=channel_id, type="video", order="date", maxResults=max_results,
    ).execute()

    video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
    if not video_ids: return pd.DataFrame()

    videos_resp = youtube.videos().list(
        part="snippet,contentDetails,statistics", id=",".join(video_ids), maxResults=len(video_ids),
    ).execute()

    rows = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {}); stats = item.get("statistics", {}); content = item.get("contentDetails", {})
        published_at = snippet.get("publishedAt")
        try: ts = pd.to_datetime(published_at).replace(tzinfo=timezone.utc)
        except Exception: ts = pd.NaT
        duration_sec = parse_iso_duration(content.get("duration", ""))

        rows.append(
            {
                "video_id": item.get("id"), "title": snippet.get("title"), "description": snippet.get("description", ""),
                "published_at": ts, "views": safe_int(stats.get("viewCount")), "likes": safe_int(stats.get("likeCount")),
                "comments": safe_int(stats.get("commentCount")), "duration_sec": duration_sec,
                "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty: return df
    now = datetime.now(timezone.utc)
    df["days_since_publish"] = (now - df["published_at"]).dt.total_seconds() / (3600 * 24)
    df["days_since_publish"] = df["days_since_publish"].replace(0, 0.1)
    df["views_per_day"] = df["views"] / df["days_since_publish"]
    df["duration_min"] = df["duration_sec"] / 60
    df["weekday"] = df["published_at"].apply(weekday_kr_from_ts)
    df["publish_hour"] = df["published_at"].dt.hour
    df["max_watch_time_min"] = df["duration_min"] * df["views"]
    return df.sort_values("published_at", ascending=False).reset_index(drop=True)


# ----------------------------
# SEO / 키워드 분석
# ----------------------------

def extract_keywords_from_titles(titles: List[str], top_n: int = 30) -> pd.DataFrame:
    # (기존 코드와 동일하게 유지)
    joined = " ".join(titles).lower()
    tokens = re.findall(r"[가-힣a-zA-Z0-9]+", joined)
    stopwords = {
        "영상", "official", "video", "the", "and", "for", "with", "full", "ver",
        "episode", "ep", "live", "tv", "show", "channel", "shorts",
    }
    filtered = [t for t in tokens if len(t) >= 2 and t not in stopwords]
    counts = Counter(filtered)
    if not counts: return pd.DataFrame(columns=["keyword", "count"])
    data = pd.DataFrame(
        [{"keyword": k, "count": v} for k, v in counts.most_common(top_n)]
    )
    return data


def render_keyword_suggestions(df: pd.DataFrame):
    # (기존 코드와 동일하게 유지)
    st.subheader("🔍 SEO 키워드/태그 아이디어")
    if df.empty: st.info("분석할 영상이 없습니다."); return
    kw_df = extract_keywords_from_titles(df["title"].tolist(), top_n=30)
    if kw_df.empty: st.info("추출된 키워드가 거의 없습니다. 제목 패턴이 너무 단순할 수 있습니다."); return

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("**제목에서 자주 등장하는 단어 TOP
