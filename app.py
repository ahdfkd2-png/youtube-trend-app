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
    page_title="YouTube 트렌드·채널 분석기 (v5.0 - FINAL)",
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

# --- UPGRADE: 4단계 - 성과 가중치 기반 키워드 추출 함수 ---

def extract_keywords_with_weight(df: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    """
    UPGRADE: 조회수(views)를 가중치로 사용하여 키워드 점수를 매기고 추출
    """
    if df.empty:
        return pd.DataFrame(columns=["keyword", "score"])

    # 1. 불용어(Stopwords) 확장 및 정의
    stopwords = {
        "영상", "official", "video", "the", "and", "for", "with", "full", "ver",
        "episode", "ep", "live", "tv", "show", "channel", "shorts", "공식", 
        "하이라이트", "클립", "무대", "최신", "today", "day", "in", "of", "a", 
        "이번주", "다시보기", "모음", "총정리", "최고", "오늘", "지금", "바로",
        "story", "log", "vlog", "asmr", "asmr", "tip", "꿀팁", "방법", "하는법",
        "저장", "구독", "좋아요", "댓글", "알림", "설정", "하나", "두개"
    }

    keyword_scores = Counter()

    for _, row in df.iterrows():
        title = row["title"].lower()
        views = row["views"]
        
        # 조회수의 제곱근을 가중치로 사용
        weight = views ** 0.5 
        
        # 정규식을 사용하여 한글, 영어, 숫자만 추출
        tokens = re.findall(r"[가-힣a-zA-Z0-9]+", title)
        
        # 필터링 및 점수 합산
        for t in tokens:
            if len(t) >= 2 and t not in stopwords:
                keyword_scores[t] += weight
    
    if not keyword_scores:
        return pd.DataFrame(columns=["keyword", "score"])
        
    data = pd.DataFrame(
        [{"keyword": k, "score": v} for k, v in keyword_scores.most_common(top_n)]
    )
    data["score"] = data["score"].round(0).astype(int)
    
    return data


# --- UPGRADE: 3단계 - 히스토리 저장/로드 및 등급 부여 함수 ---

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
def render_keyword_suggestions(df: pd.DataFrame):
    """
    UPGRADE: 가중치 기반으로 키워드 추천을 렌더링
    """
    st.subheader("🔍 SEO 키워드/태그 아이디어")
    if df.empty:
        st.info("분석할 영상이 없습니다.");
        return
        
    kw_df = extract_keywords_with_weight(df, top_n=30)
    
    if kw_df.empty:
        st.info("추출된 키워드가 거의 없습니다. 제목 패턴이 너무 단순하거나 불용어만 포함하고 있을 수 있습니다.");
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("**성과 가중치 기반 TOP 30 키워드**")
        st.dataframe(
            kw_df.rename(columns={"score": "성과 점수"}), 
            use_container_width=True, 
            hide_index=True
        )
        st.caption("※ '성과 점수'는 조회수가 높은 영상의 제목에 등장할수록 높아집니다.")

    with col2:
        st.markdown("**태그로 써볼 만한 후보**")
        tag_candidates = kw_df["keyword"].tolist()[:15]
        st.code(", ".join(tag_candidates), language="text")
        st.caption("※ 이 키워드를 제목, 설명, 태그에 활용해 보세요.")

# ----------------------------
# 요약 메시지 생성 (룰 기반)
# ----------------------------

def make_simple_summary_for_channel(df: pd.DataFrame) -> str:
    # (기존 코드와 동일하게 유지)
    if df.empty: return "최근 영상 데이터가 없어 패턴을 분석할 수 없습니다."
    n = len(df); avg_views = int(df["views"].mean()); median_views = int(df["views"].median())
    max_views = int(df["views"].max()); short = df[df["duration_min"] <= 8]; long = df[df["duration_min"] >= 20]
    parts = []
    parts.append(f"최근 {n}개 영상 기준으로 평균 조회수는 약 {avg_views:,}회, 중앙값은 {median_views:,}회입니다.")
    parts.append(f"가장 많이 본 영상은 약 {max_views:,}회까지 기록했습니다.")
    if not short.empty and not long.empty:
        short_avg = int(short["views"].mean()); long_avg = int(long["views"].mean())
        if short_avg > long_avg * 1.3:
            parts.append(f"8분 이하 짧은 영상의 평균 조회수가 {short_avg:,}회로, 20분 이상 긴 영상({long_avg:,}회)보다 꽤 잘 나오는 편입니다. " "짧은 길이의 콘텐츠 비중을 조금 더 늘려보는 것도 좋겠습니다.")
        elif long_avg > short_avg * 1.3:
            parts.append(f"20분 이상 긴 영상의 평균 조회수가 {long_avg:,}회로, 8분 이하 영상({short_avg:,}회)보다 유리합니다. " "깊이 있는 장편 콘텐츠가 채널에 잘 맞는 편으로 보입니다.")
    weekday_mean = df.groupby("weekday")["views"].mean().sort_values(ascending=False)
    if len(weekday_mean) >= 3:
        best_day = weekday_mean.index[0]
        parts.append(f"요일별 평균 조회수는 **{best_day}요일 업로드분**이 가장 높게 나타납니다. " "해당 요일 전후로 중요한 영상을 배치하는 전략을 고려해볼 만합니다.")
    return "\n\n".join(parts)


# ----------------------------
# 화면 구성 함수들
# ----------------------------

def render_channel_kpi_cards(info: Dict, df: pd.DataFrame):
    """
    UPGRADE: 채널 분석 페이지 상단에 구독자, 총 조회수, 평균 조회수, 성장률을 보여주는 KPI 카드 4개 배치
    """
    st.markdown("### 🏆 채널 핵심 지표 (Channel KPIs)")
    
    days_since_start = (datetime.now(timezone.utc) - info["published_at"]).total_seconds() / (3600 * 24)
    channel_avg_views_per_day = info["view_count"] / max(days_since_start, 1)

    recent_avg_views_per_day = df["views_per_day"].mean() if not df.empty else 0
    
    growth_delta = 0.0
    if channel_avg_views_per_day > 0:
        growth_delta = ((recent_avg_views_per_day - channel_avg_views_per_day) / channel_avg_views_per_day) * 100
        
    growth_str = f"{growth_delta:.1f}%"
    
    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        label="⭐ 구독자", 
        value=format_korean_unit(info["subscriber_count"]),
        delta=f"총 영상 수: {info['video_count']:,}",
        delta_color="off"
    )
    
    col2.metric(
        label="🌐 총 조회수", 
        value=format_korean_unit(info["view_count"]),
        delta=f"개설일: {info['published_at'].strftime('%Y.%m.%d')}",
        delta_color="off"
    )

    col3.metric(
        label=f"📊 최근 평균 조회수 (영상 {len(df)}개)", 
        value=f"{int(df['views'].mean()):,}" if not df.empty else "N/A",
        delta=f"중앙값: {int(df['views'].median()):,}" if not df.empty else "",
        delta_color="off"
    )

    col4.metric(
        label="🚀 일일 성과 변화율 (최근 영상)", 
        value=f"{int(recent_avg_views_per_day):,}회/일",
        delta=growth_str,
        delta_color="inverse" if growth_delta < 0 else "normal"
    )

def render_basic_stats_cards_for_videos(df: pd.DataFrame, title: str):
    """
    UPGRADE: 키워드 분석 페이지의 기본 통계 카드를 깔끔하게 재구성
    """
    st.subheader(title)

    if df.empty: st.info("표시할 데이터가 없습니다."); return

    total_views = int(df["views"].sum())
    avg_views = int(df["views"].mean())
    median_views = int(df["views"].median())
    total_max_watch_min = int(df["max_watch_time_min"].sum())
    
    col1, col2, col3 = st.columns(3)
    
    col1.metric("영상 수", f"{len(df):,}")
    col2.metric("총 조회수", f"{total_views:,}")
    col3.metric("평균 조회수", f"{avg_views:,}")
    
    st.caption(f"※ 분석된 영상의 중앙값 조회수는 {median_views:,}회이며, 이론상 최대 시청시간은 {total_max_watch_min:,}분입니다. ")


def render_video_table(df: pd.DataFrame):
    # (기존 코드에서 채널명 칼럼 추가)
    if df.empty: return

    show_cols = [
        "title", "channel_title", "views", "views_per_day",
        "duration_min", "weekday", "publish_hour", "published_at",
    ]
    rename = {
        "title": "제목", "channel_title": "채널명", "views": "조회수",
        "views_per_day": "일 평균 조회수", "duration_min": "길이(분)",
        "weekday": "요일", "publish_hour": "업로드 시간(시)", "published_at": "업로드 일시",
    }

    st.markdown("#### 📋 상세 영상 리스트")
    st.dataframe(
        df[[c for c in show_cols if c in df.columns]].rename(columns=rename),
        use_container_width=True, hide_index=True,
    )


def render_pattern_charts(df: pd.DataFrame):
    # (기존 코드와 동일하게 유지)
    if df.empty: return
    st.subheader("📈 패턴 분석")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**요일별 평균 조회수**")
        weekday_order = ["월", "화", "수", "목", "금", "토", "일"]
        weekday_mean = (
            df.groupby("weekday")["views"].mean().reindex(weekday_order).dropna().astype(int)
        )
        if not weekday_mean.empty: st.bar_chart(weekday_mean)
    with c2:
        st.markdown("**업로드 시간대별 평균 조회수**")
        hour_mean = df.groupby("publish_hour")["views"].mean().astype(int)
        if not hour_mean.empty: st.bar_chart(hour_mean)
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**영상 길이(분) vs 조회수**")
        st.scatter_chart(
            df[["duration_min", "views"]].rename(
                columns={"duration_min": "길이(분)", "views": "조회수"}
            )
        )
    with c4:
        st.markdown("**업로드 후 경과일 vs 일 평균 조회수**")
        st.scatter_chart(
            df[["days_since_publish", "views_per_day"]].rename(
                columns={"days_since_publish": "업로드 후 경과일", "views_per_day": "일 평균 조회수",}
            )
        )


def render_top_thumbnails(df: pd.DataFrame):
    # (기존 코드와 동일하게 유지)
    if df.empty: return
    st.subheader("🏆 상위 성과 영상 썸네일 (TOP 3)")
    top3 = df.sort_values("views", ascending=False).head(3)
    cols = st.columns(3)
    for col, (_, row) in zip(cols, top3.iterrows()):
        with col:
            if row["thumbnail_url"]: st.image(row["thumbnail_url"])
            st.markdown(f"**{row['title']}**")
            st.caption(f"조회수: {row['views']:,}회")


# ----------------------------
# 각 분석 모드 렌더링
# ----------------------------

def page_keyword_trend(api_key: str, video_limit: int):
    st.title("🎯 키워드 트렌드 분석")
    st.markdown("##### 현재 검색 키워드를 중심으로 유튜브 트렌드를 분석합니다.")

    keyword = st.text_input("분석할 키워드를 입력하세요 (예: 시니어 쇼핑, 건강, 요리 등)", key="kw_input")
    st.caption(f"※ 가져올 영상 수: {video_limit}개. API 할당량 소모에 주의하세요.")

    if not keyword: st.info("키워드를 입력한 뒤 Enter 를 눌러주세요."); return

    try:
        with st.spinner(f"키워드 '{keyword}' 관련 YouTube 데이터 불러오는 중..."):
            df = fetch_videos_by_keyword(api_key, keyword, video_limit)
    except HttpError as e:
        msg = str(e)
        if "quotaExceeded" in msg: st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 내일 다시 시도하거나, 가져올 영상 수를 줄여 주세요.")
        elif "keyInvalid" in msg: st.error("❌ YouTube API 키가 유효하지 않습니다. 키를 다시 확인해 주세요.")
        else: st.error(f"API 호출 중 오류가 발생했습니다: {msg}")
        return

    if df.empty: st.warning("검색된 영상이 없습니다."); return

    st.markdown("---")
    render_basic_stats_cards_for_videos(df, f"'{keyword}' 관련 영상 요약")
    st.markdown("---")
    render_top_thumbnails(df)
    st.markdown("---")
    
    c_chart, c_seo = st.columns([3, 2])
    with c_chart: render_pattern_charts(df)
    with c_seo: render_keyword_suggestions(df)
        
    st.markdown("---")
    render_video_table(df)


def page_single_channel(api_key: str, video_limit: int):
    st.title("🎯 특정 채널 심층 분석")
    st.markdown("##### 채널의 기본 지표, 최근 영상 패턴, SEO 전략을 분석합니다.")

    raw_input = st.text_input(
        "채널 ID 또는 채널 URL을 입력하세요", key="ch_input",
        help="UC 로 시작하는 ID, 또는 https://www.youtube.com/channel/ 형태를 입력하세요.",
    )

    if not raw_input: st.info("분석할 채널을 입력해 주세요."); return

    channel_id = extract_channel_id(raw_input)

    try:
        with st.spinner("채널/영상 데이터 수집 중..."):
            info = fetch_channel_basic(api_key, channel_id)
            df = fetch_channel_recent_videos(api_key, channel_id, video_limit)
    except HttpError as e:
        msg = str(e)
        if "quotaExceeded" in msg: st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 내일 다시 시도하거나, 가져올 영상 수를 줄여 주세요.")
        elif "keyInvalid" in msg: st.error("❌ YouTube API 키가 유효하지 않습니다. 키를 다시 확인해 주세요.")
        else: st.error(f"API 호출 중 오류가 발생했습니다: {msg}")
        return

    if not info: st.error("채널 정보를 가져오지 못했습니다. 채널 ID/URL을 다시 확인해 주세요."); return
    
    # --- UPGRADE: 채널 히스토리 저장 기능 추가 및 등급 표시 ---
    history_data = load_channel_history()

    st.markdown("---")
    col_save, col_grade = st.columns([1, 4])
    
    with col_save:
        save_button = st.button("💾 이 채널 히스토리에 저장", type="secondary")

    if save_button:
        summary_data = get_channel_summary_row(info, df)
        if summary_data:
            history_data[channel_id] = summary_data
            save_channel_history(history_data)
            st.success(f"✅ 채널 '{info['title']}' 정보가 히스토리에 저장되었습니다!")
        else:
            st.warning("저장할 최근 영상 데이터가 충분하지 않습니다.")

    # 1. 채널 헤더 (썸네일 + 기본 정보)
    st.markdown("---")
    c1, c2 = st.columns([1, 4])
    with c1:
        if info.get("thumbnail_url"):
            st.image(info["thumbnail_url"], caption="채널 썸네일", use_column_width=True)
    with c2:
        st.markdown(f"## 📺 {info['title']}")
        grade = assign_channel_grade(info, df)
        st.caption(f"**ID**: {info['channel_id']} | **개설일**: {info['published_at'].strftime('%Y년 %m월 %d일')} | **채널 등급**: ⭐ **{grade}**")
        st.markdown(info.get("description", "")[:250].replace('\n', ' ') + "...")

    st.markdown("---")
    
    # 2. KPI 카드
    render_channel_kpi_cards(info, df)

    st.markdown("---")

    # 3. 인사이트 및 패턴 분석
    st.subheader("🧠 채널 운영 인사이트 (룰 기반 요약)")
    st.info(make_simple_summary_for_channel(df))
    
    st.markdown("---")

    render_top_thumbnails(df)
    render_pattern_charts(df)
    render_keyword_suggestions(df)
    render_video_table(df)


def page_channel_history():
    """UPGRADE: 3단계 - 히스토리 저장 채널 목록을 보여주는 페이지"""
    st.title("📚 채널 히스토리 및 비교 분석")
    st.markdown("##### 저장된 채널들을 확인하고 핵심 지표를 비교 분석합니다.")
    
    history_data = load_channel_history()
    
    if not history_data:
        st.info("저장된 채널 히스토리가 없습니다. '특정 채널 심층 분석' 페이지에서 채널을 분석하고 저장해 보세요.")
        return

    history_list = list(history_data.values())
    df_history = pd.DataFrame(history_list)

    show_cols = [
        "title", "grade", "subscriber_count", "total_views", 
        "recent_avg_views", "recent_avg_daily_views", "videos_last_30d", "analysis_date"
    ]
    rename = {
        "title": "채널명", "grade": "등급", "subscriber_count": "구독자 수",
        "total_views": "총 조회수", "recent_avg_views": "최근 영상 평균 조회수",
        "recent_avg_daily_views": "최근 영상 일 평균 조회수", "videos_last_30d": "최근 30일 영상 수",
        "analysis_date": "분석일",
    }
    
    st.subheader("🏁 저장된 채널 요약 테이블")
    st.dataframe(
        df_history[show_cols].rename(columns=rename).sort_values("subscriber_count", ascending=False),
        use_container_width=True, hide_index=True
    )

    st.subheader("📈 채널별 핵심 지표 비교")
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**구독자 수**")
        st.bar_chart(df_history.set_index("title")["subscriber_count"])

    with c2:
        st.markdown("**최근 영상 평균 조회수**")
        st.bar_chart(df_history.set_index("title")["recent_avg_views"])
        
    st.markdown("---")
    
    if st.button("🗑️ 저장된 히스토리 전체 삭제", type="danger"):
        save_channel_history({})
        st.success("✅ 채널 히스토리가 모두 삭제되었습니다. 페이지를 새로고침합니다.")
        st.rerun()


def page_competitive_channels(api_key: str, video_limit: int):
    """UPGRADE: 5단계 - 히스토리 기반으로 경쟁 채널을 선택하고 벤치마킹"""
    st.title("🎯 경쟁 채널 벤치마킹")
    st.markdown("##### 히스토리에 저장된 채널들을 비교하여 벤치마킹합니다.")

    history_data = load_channel_history()
    
    if not history_data:
        st.info("비교할 채널이 없습니다. '특정 채널 심층 분석' 페이지에서 채널을 분석하고 저장해 주세요.")
        return

    channel_options = {
        data['title']: data['channel_id']
        for data in history_data.values()
    }
    
    selected_titles = st.multiselect(
        "🔎 비교할 채널을 선택하세요 (최소 2개)",
        options=list(channel_options.keys()),
        default=list(channel_options.keys())[:2],
        key="comp_select"
    )

    if len(selected_titles) < 2:
        st.warning("비교 분석을 위해 최소 2개 이상의 채널을 선택해야 합니다.")
        return

    selected_ids = [channel_options[title] for title in selected_titles]

    if st.button("📊 경쟁 채널 비교 실행", type="primary"):
        rows = []
        error_channels = []

        for title, cid in zip(selected_titles, selected_ids):
            try:
                with st.spinner(f"채널 '{title}' 분석 중..."):
                    info = fetch_channel_basic(api_key, cid)
                    df = fetch_channel_recent_videos(api_key, cid, video_limit)
            except HttpError as e:
                msg = str(e)
                if "quotaExceeded" in msg:
                    st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 더 이상 채널을 분석할 수 없습니다."); return
                else:
                    error_channels.append(f"{title} (ID: {cid}, 오류: {msg})"); continue

            if not info or df.empty:
                error_channels.append(f"{title} (ID: {cid}, 데이터 부족)"); continue

            row = {
                "채널명": info["title"], "채널ID": info["channel_id"],
                "구독자 수": info["subscriber_count"], "총 조회수": info["view_count"], "총 업로드 수": info["video_count"],
                "최근 영상 수(분석)": len(df), 
                "최근 평균 조회수": int(df["views"].mean()),
                "최근 중앙값 조회수": int(df["views"].median()), 
                "최근 최고 조회수": int(df["views"].max()),
                "최근 평균 길이(분)": round(df["duration_min"].mean(), 1),
                "최근 일 평균 조회수(평균)": int(df["views_per_day"].mean()),
            }
            rows.append(row)

        if not rows:
            st.error("성공적으로 분석된 채널이 없습니다.")
            if error_channels: st.write("오류 채널 목록:"); st.write("\n".join(error_channels))
            return

        result_df = pd.DataFrame(rows)
        
        st.subheader("🏁 경쟁 채널 요약 비교 테이블")
        st.dataframe(result_df, use_container_width=True, hide_index=True)

        st.subheader("📈 채널별 최근 평균 조회수 비교")
        st.bar_chart(result_df.set_index("채널명")["최근 평균 조회수"])

        st.subheader("📈 채널별 최근 일 평균 조회수(영상 1개 기준) 비교")
        st.bar_chart(result_df.set_index("채널명")["최근 일 평균 조회수(평균)"])

        if error_channels:
            with st.expander("⚠ 분석 실패/데이터 부족 채널 보기"):
                st.write("\n".join(error_channels))


# ----------------------------
# 메인
# ----------------------------

def main():
    api_key = get_api_key()
    if not api_key: st.stop()

    st.sidebar.header("🔎 분석 모드 선택")

    mode = st.sidebar.radio(
        "분석 대상",
        ["특정 채널 심층 분석", "채널 히스토리 및 비교 분석", "키워드 트렌드 분석", "경쟁 채널 벤치마킹"], 
        index=0,
    )

    st.sidebar.markdown("---")
    video_limit = st.sidebar.slider(
        "가져올 영상 개수 (1회 분석당)",
        min_value=5, max_value=30, value=10,
        help="값이 클수록 분석은 풍부해지지만, YouTube API 일일 할당량이 더 빨리 소모됩니다. 5~15 권장.",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        ### 🚨 API 쿼터 절약 가이드
        - **추천값 유지**: 영상 개수는 **5~15개** 정도로 유지하세요.
        - **캐시 활용**: 동일한 채널/키워드는 1시간 동안 API를 재사용하지 않습니다.
        - **초과 시**: 쿼터는 매일 자동으로 초기화됩니다.
        """
    )
    
    st.markdown("---")

    if mode == "키워드 트렌드 분석":
        page_keyword_trend(api_key, video_limit)
    elif mode == "특정 채널 심층 분석":
        page_single_channel(api_key, video_limit)
    elif mode == "채널 히스토리 및 비교 분석": 
        page_channel_history()
    elif mode == "경쟁 채널 벤치마킹":
        page_competitive_channels(api_key, video_limit)


if __name__ == "__main__":
    main()
