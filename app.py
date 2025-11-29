import streamlit as st

# ---------------------------
# UI 기본 틀 (대시보드 헤더 + 메뉴)
# ---------------------------

# 화면 상단 큰 제목
st.markdown("<h1 style='text-align:center;'>📊 YouTube Analytics Dashboard</h1>", unsafe_allow_html=True)

# 좌측 메뉴
menu = st.sidebar.radio(
    "📁 메뉴 선택",
    ["Dashboard 홈", "채널 분석", "영상 분석", "SEO 분석", "경쟁 채널"]
)

import re
from collections import Counter
from datetime import datetime, timezone
from typing import List, Dict, Tuple

import pandas as pd
import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ----------------------------
# 기본 설정
# ----------------------------

st.set_page_config(
    page_title="YouTube 트렌드·채널 분석기 (v3.0)",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* 전체 폰트/여백 조금 다듬기 */
    .main-block {padding-top: 0rem;}
    .block-container {padding-top: 1.5rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------
# 유틸 함수
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
    """
    ISO8601 duration(예: 'PT15M33S') → 초 단위 정수로 변환
    """
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
    """
    사용자가 입력한 값에서 channelId 추출
    - UC 로 시작하면 그대로 사용
    - https://www.youtube.com/channel/UCxxxx 형식 지원
    그 외 복잡한 경우는 지원하지 않고 그대로 반환
    """
    raw = raw.strip()
    if "youtube.com/channel/" in raw:
        return raw.split("youtube.com/channel/")[-1].split("/")[0].split("?")[0]
    if "youtube.com/" in raw:
        # 기타 URL 의 마지막 path 를 ID 로 간주
        path = raw.split("youtube.com/")[-1]
        return path.split("/")[-1].split("?")[0]
    return raw


def safe_int(x):
    try:
        return int(x)
    except Exception:
        return 0


# ----------------------------
# 데이터 가져오기 (캐시 적용)
# ----------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_videos_by_keyword(api_key: str, keyword: str, max_results: int) -> pd.DataFrame:
    """
    키워드 기반 영상 목록 조회 (최대 30개 정도 권장)
    search.list → videos.list 1회만 사용해서 쿼터 절약
    """
    youtube = build_youtube(api_key)

    # search.list (max 50)
    max_results = max(1, min(max_results, 50))
    search_resp = youtube.search().list(
        part="snippet",
        q=keyword,
        type="video",
        order="relevance",
        maxResults=max_results,
    ).execute()

    video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
    if not video_ids:
        return pd.DataFrame()

    # videos.list
    videos_resp = youtube.videos().list(
        part="snippet,contentDetails,statistics",
        id=",".join(video_ids),
        maxResults=len(video_ids),
    ).execute()

    rows = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        published_at = snippet.get("publishedAt")
        try:
            ts = pd.to_datetime(published_at)
        except Exception:
            ts = pd.NaT

        duration_sec = parse_iso_duration(content.get("duration", ""))

        rows.append(
            {
                "video_id": item.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description", ""),
                "channel_title": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
                "published_at": ts,
                "views": safe_int(stats.get("viewCount")),
                "likes": safe_int(stats.get("likeCount")),
                "comments": safe_int(stats.get("commentCount")),
                "duration_sec": duration_sec,
                "thumbnail_url": snippet.get("thumbnails", {})
                .get("medium", {})
                .get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    now = datetime.now(timezone.utc)
    df["days_since_publish"] = (now - df["published_at"]).dt.total_seconds() / (3600 * 24)
    df["days_since_publish"] = df["days_since_publish"].replace(0, 0.1)
    df["views_per_day"] = df["views"] / df["days_since_publish"]
    df["duration_min"] = df["duration_sec"] / 60
    df["weekday"] = df["published_at"].apply(weekday_kr_from_ts)
    df["publish_hour"] = df["published_at"].dt.hour

    # 이론상 최대 시청시간(분) = 영상 길이(분) * 조회수
    df["max_watch_time_min"] = df["duration_min"] * df["views"]

    return df.sort_values("views", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_basic(api_key: str, channel_id: str) -> Dict:
    """채널 기본 정보 + 통계"""
    youtube = build_youtube(api_key)
    resp = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        id=channel_id,
        maxResults=1,
    ).execute()

    items = resp.get("items", [])
    if not items:
        return {}

    item = items[0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})

    return {
        "channel_id": item.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description", ""),
        "published_at": pd.to_datetime(snippet.get("publishedAt")),
        "subscriber_count": safe_int(stats.get("subscriberCount")),
        "video_count": safe_int(stats.get("videoCount")),
        "view_count": safe_int(stats.get("viewCount")),
        "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_recent_videos(
    api_key: str, channel_id: str, max_results: int
) -> pd.DataFrame:
    """채널 최근 업로드 영상들 (max_results ≦ 50 권장)"""
    youtube = build_youtube(api_key)

    max_results = max(1, min(max_results, 50))
    search_resp = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        type="video",
        order="date",
        maxResults=max_results,
    ).execute()

    video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
    if not video_ids:
        return pd.DataFrame()

    videos_resp = youtube.videos().list(
        part="snippet,contentDetails,statistics",
        id=",".join(video_ids),
        maxResults=len(video_ids),
    ).execute()

    rows = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        published_at = snippet.get("publishedAt")
        try:
            ts = pd.to_datetime(published_at)
        except Exception:
            ts = pd.NaT

        duration_sec = parse_iso_duration(content.get("duration", ""))

        rows.append(
            {
                "video_id": item.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description", ""),
                "published_at": ts,
                "views": safe_int(stats.get("viewCount")),
                "likes": safe_int(stats.get("likeCount")),
                "comments": safe_int(stats.get("commentCount")),
                "duration_sec": duration_sec,
                "thumbnail_url": snippet.get("thumbnails", {})
                .get("medium", {})
                .get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

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
    """
    제목 리스트에서 단어 빈도 분석 (아주 단순한 방식, 참고용)
    """
    joined = " ".join(titles).lower()
    tokens = re.findall(r"[가-힣a-zA-Z0-9]+", joined)

    stopwords = {
        "영상",
        "official",
        "video",
        "the",
        "and",
        "for",
        "with",
        "full",
        "ver",
        "episode",
        "ep",
        "live",
        "tv",
        "show",
        "channel",
        "shorts",
    }

    filtered = [t for t in tokens if len(t) >= 2 and t not in stopwords]
    counts = Counter(filtered)

    if not counts:
        return pd.DataFrame(columns=["keyword", "count"])

    data = pd.DataFrame(
        [{"keyword": k, "count": v} for k, v in counts.most_common(top_n)]
    )
    return data


def render_keyword_suggestions(df: pd.DataFrame):
    st.subheader("🔍 SEO 키워드/태그 아이디어")

    if df.empty:
        st.info("분석할 영상이 없습니다.")
        return

    kw_df = extract_keywords_from_titles(df["title"].tolist(), top_n=30)
    if kw_df.empty:
        st.info("추출된 키워드가 거의 없습니다. 제목 패턴이 너무 단순할 수 있습니다.")
        return

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("**제목에서 자주 등장하는 단어 TOP 30**")
        st.dataframe(kw_df, use_container_width=True, hide_index=True)

    with col2:
        st.markdown("**태그로 써볼 만한 후보**")
        tag_candidates = kw_df.query("count >= 2")["keyword"].tolist()[:15]
        st.code(", ".join(tag_candidates), language="text")

        st.caption("※ 단순 빈도 기준이므로 실제 검색량/경쟁도와는 다를 수 있습니다.")


# ----------------------------
# 요약 메시지 생성 (룰 기반)
# ----------------------------

def make_simple_summary_for_channel(df: pd.DataFrame) -> str:
    if df.empty:
        return "최근 영상 데이터가 없어 패턴을 분석할 수 없습니다."

    n = len(df)
    avg_views = int(df["views"].mean())
    median_views = int(df["views"].median())
    max_views = int(df["views"].max())

    # 길이 관련
    short = df[df["duration_min"] <= 8]
    long = df[df["duration_min"] >= 20]

    parts = []
    parts.append(f"최근 {n}개 영상 기준으로 평균 조회수는 약 {avg_views:,}회, 중앙값은 {median_views:,}회입니다.")
    parts.append(f"가장 많이 본 영상은 약 {max_views:,}회까지 기록했습니다.")

    if not short.empty and not long.empty:
        short_avg = int(short["views"].mean())
        long_avg = int(long["views"].mean())
        if short_avg > long_avg * 1.3:
            parts.append(
                f"8분 이하 짧은 영상의 평균 조회수가 {short_avg:,}회로, 20분 이상 긴 영상({long_avg:,}회)보다 꽤 잘 나오는 편입니다. "
                "짧은 길이의 콘텐츠 비중을 조금 더 늘려보는 것도 좋겠습니다."
            )
        elif long_avg > short_avg * 1.3:
            parts.append(
                f"20분 이상 긴 영상의 평균 조회수가 {long_avg:,}회로, 8분 이하 영상({short_avg:,}회)보다 유리합니다. "
                "깊이 있는 장편 콘텐츠가 채널에 잘 맞는 편으로 보입니다."
            )

    # 요일 패턴
    weekday_mean = df.groupby("weekday")["views"].mean().sort_values(ascending=False)
    if len(weekday_mean) >= 3:
        best_day = weekday_mean.index[0]
        parts.append(
            f"요일별 평균 조회수는 **{best_day}요일 업로드분**이 가장 높게 나타납니다. "
            "해당 요일 전후로 중요한 영상을 배치하는 전략을 고려해볼 만합니다."
        )

    return "\n\n".join(parts)


# ----------------------------
# 화면 구성 함수들
# ----------------------------

def render_header():
    st.title("📊 YouTube 트렌드·채널 분석기 (v3.0)")
    st.caption("키워드 / 채널 단위로 트렌드, 패턴, SEO, 경쟁까지 한 번에 분석하는 대시보드입니다.")


def render_basic_stats_cards_for_videos(df: pd.DataFrame, title: str):
    st.subheader(title)

    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return

    total_views = int(df["views"].sum())
    avg_views = int(df["views"].mean())
    median_views = int(df["views"].median())
    total_max_watch_min = int(df["max_watch_time_min"].sum())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 조회수", f"{total_views:,}")
    col2.metric("영상 수", f"{len(df):,}")
    col3.metric("평균 조회수", f"{avg_views:,}")
    col4.metric("이론상 최대 시청시간(분)", f"{total_max_watch_min:,}")

    st.caption("※ '이론상 최대 시청시간'은 영상 전체를 끝까지 본다고 가정했을 때의 값으로, 실제 시청시간/유지율과는 다를 수 있습니다.")


def render_video_table(df: pd.DataFrame):
    if df.empty:
        return

    show_cols = [
        "title",
        "views",
        "views_per_day",
        "duration_min",
        "weekday",
        "publish_hour",
        "published_at",
    ]
    rename = {
        "title": "제목",
        "views": "조회수",
        "views_per_day": "일 평균 조회수",
        "duration_min": "길이(분)",
        "weekday": "요일",
        "publish_hour": "업로드 시간(시)",
        "published_at": "업로드 일시",
    }

    st.markdown("#### 📋 상세 영상 리스트")
    st.dataframe(
        df[show_cols].rename(columns=rename),
        use_container_width=True,
        hide_index=True,
    )


def render_pattern_charts(df: pd.DataFrame):
    if df.empty:
        return

    st.subheader("📈 패턴 분석")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**요일별 평균 조회수**")
        weekday_order = ["월", "화", "수", "목", "금", "토", "일"]
        weekday_mean = (
            df.groupby("weekday")["views"]
            .mean()
            .reindex(weekday_order)
            .dropna()
            .astype(int)
        )
        if not weekday_mean.empty:
            st.bar_chart(weekday_mean)

    with c2:
        st.markdown("**업로드 시간대별 평균 조회수**")
        hour_mean = df.groupby("publish_hour")["views"].mean().astype(int)
        if not hour_mean.empty:
            st.bar_chart(hour_mean)

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
                columns={
                    "days_since_publish": "업로드 후 경과일",
                    "views_per_day": "일 평균 조회수",
                }
            )
        )


def render_top_thumbnails(df: pd.DataFrame):
    if df.empty:
        return
    st.subheader("🏆 상위 성과 영상 썸네일 (TOP 3)")
    top3 = df.sort_values("views", ascending=False).head(3)
    cols = st.columns(3)
    for col, (_, row) in zip(cols, top3.iterrows()):
        with col:
            if row["thumbnail_url"]:
                st.image(row["thumbnail_url"])
            st.markdown(f"**{row['title']}**")
            st.caption(f"조회수: {row['views']:,}회")


# ----------------------------
# 각 분석 모드 렌더링
# ----------------------------

def page_keyword_trend(api_key: str, video_limit: int):
    render_header()
    st.markdown("### 🎯 키워드 트렌드 분석")

    keyword = st.text_input("분석할 키워드를 입력하세요 (예: 시니어 쇼핑, 건강, 요리 등)")
    st.caption("※ 가져올 영상 수를 너무 크게 설정하면 YouTube API 할당량이 빨리 소모됩니다. (5~15개 권장)")

    if not keyword:
        st.info("왼쪽 상단에 키워드를 입력한 뒤 Enter 를 눌러주세요.")
        return

    if st.button("🔍 키워드 분석 실행", type="primary"):
        try:
            with st.spinner("YouTube 데이터 불러오는 중..."):
                df = fetch_videos_by_keyword(api_key, keyword, video_limit)
        except HttpError as e:
            msg = str(e)
            if "quotaExceeded" in msg:
                st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 내일 다시 시도하거나, 가져올 영상 수를 줄여 주세요.")
            elif "keyInvalid" in msg:
                st.error("❌ YouTube API 키가 유효하지 않습니다. 키를 다시 확인해 주세요.")
            else:
                st.error(f"API 호출 중 오류가 발생했습니다: {msg}")
            return

        if df.empty:
            st.warning("검색된 영상이 없습니다.")
            return

        render_basic_stats_cards_for_videos(df, f"'{keyword}' 관련 영상 요약")
        render_top_thumbnails(df)
        render_pattern_charts(df)
        render_keyword_suggestions(df)
        render_video_table(df)


def page_single_channel(api_key: str, video_limit: int):
    render_header()
    st.markdown("### 🎯 특정 채널 심층 분석")

    raw_input = st.text_input(
        "채널 ID 또는 채널 URL을 입력하세요 (예: UC 로 시작하는 ID, 또는 https://www.youtube.com/channel/ 형태)",
        help="복잡한 URL(커스텀 핸들 등)은 단순 채널 ID 로 변환되지 않을 수 있습니다. 안 될 경우, 유튜브 스튜디오에서 '채널 ID(UC...)' 를 복사해 오세요.",
    )

    if not raw_input:
        st.info("분석할 채널을 입력해 주세요.")
        return

    channel_id = extract_channel_id(raw_input)

    if st.button("📊 채널 분석 실행", type="primary"):
        try:
            with st.spinner("채널/영상 데이터 수집 중..."):
                info = fetch_channel_basic(api_key, channel_id)
                df = fetch_channel_recent_videos(api_key, channel_id, video_limit)
        except HttpError as e:
            msg = str(e)
            if "quotaExceeded" in msg:
                st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 내일 다시 시도하거나, 가져올 영상 수를 줄여 주세요.")
            elif "keyInvalid" in msg:
                st.error("❌ YouTube API 키가 유효하지 않습니다. 키를 다시 확인해 주세요.")
            else:
                st.error(f"API 호출 중 오류가 발생했습니다: {msg}")
            return

        if not info:
            st.error("채널 정보를 가져오지 못했습니다. 채널 ID/URL을 다시 확인해 주세요.")
            return

        # 채널 헤더
        c1, c2 = st.columns([1, 3])
        with c1:
            if info.get("thumbnail_url"):
                st.image(info["thumbnail_url"], caption=info["title"])
        with c2:
            st.markdown(f"## 📺 {info['title']}")
            st.markdown(info.get("description", "")[:250] + "...")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("구독자 수", f"{info['subscriber_count']:,}")
            col_b.metric("총 조회수", f"{info['view_count']:,}")
            col_c.metric("총 업로드 수", f"{info['video_count']:,}")

        render_basic_stats_cards_for_videos(df, "최근 업로드 영상 요약")
        render_top_thumbnails(df)
        render_pattern_charts(df)

        st.subheader("🧠 채널 운영 인사이트 (룰 기반 요약)")
        st.write(make_simple_summary_for_channel(df))

        render_keyword_suggestions(df)
        render_video_table(df)


def page_competitive_channels(api_key: str, video_limit: int):
    render_header()
    st.markdown("### 🎯 경쟁 채널 벤치마킹")

    st.write(
        "비슷한 주제의 채널 여러 개를 넣어두고, 최근 영상 성과를 간단히 비교해볼 수 있습니다.\n"
        "각 줄에 하나씩 채널 ID 또는 URL 을 적어 주세요."
    )

    raw = st.text_area(
        "채널 ID / URL 목록 (한 줄에 하나씩)",
        height=150,
        placeholder="예)\nhttps://www.youtube.com/channel/UCxxxxxxxxxx1\nhttps://www.youtube.com/channel/UCxxxxxxxxxx2",
    )

    if not raw.strip():
        st.info("채널 목록을 입력해 주세요.")
        return

    lines = [extract_channel_id(line) for line in raw.splitlines() if line.strip()]
    lines = list(dict.fromkeys(lines))  # 중복 제거

    if len(lines) < 2:
        st.info("최소 2개 이상의 채널을 입력해 주세요.")
        return

    if st.button("📊 경쟁 채널 비교 실행", type="primary"):
        rows = []
        error_channels = []

        for cid in lines:
            try:
                with st.spinner(f"채널 {cid} 분석 중..."):
                    info = fetch_channel_basic(api_key, cid)
                    df = fetch_channel_recent_videos(api_key, cid, video_limit)
            except HttpError as e:
                msg = str(e)
                if "quotaExceeded" in msg:
                    st.error("❌ YouTube API 일일 할당량이 초과되었습니다. 더 이상 채널을 분석할 수 없습니다.")
                    return
                else:
                    error_channels.append(f"{cid} (오류: {msg})")
                    continue

            if not info or df.empty:
                error_channels.append(f"{cid} (데이터 없음)")
                continue

            row = {
                "채널명": info["title"],
                "채널ID": info["channel_id"],
                "구독자 수": info["subscriber_count"],
                "총 조회수": info["view_count"],
                "총 업로드 수": info["video_count"],
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
            if error_channels:
                st.write("오류 채널 목록:")
                st.write("\n".join(error_channels))
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
    if not api_key:
        st.stop()

    # 왼쪽 사이드바 - 분석 모드 & 공통 옵션
    st.sidebar.header("🔎 분석 모드 선택")

    mode = st.sidebar.radio(
        "분석 대상",
        ["키워드 트렌드 분석", "특정 채널 분석", "경쟁 채널 벤치마킹"],
        index=0,
    )

    st.sidebar.markdown("---")
    video_limit = st.sidebar.slider(
        "가져올 영상 개수 (1회 분석당)",
        min_value=5,
        max_value=30,
        value=10,
        help="값이 클수록 분석은 풍부해지지만, YouTube API 일일 할당량이 더 빨리 소모됩니다. 5~15 권장.",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        **YouTube API 쿼터 절약 팁**
        - 영상 개수는 5~15개 정도로 유지  
        - 같은 키워드/채널을 반복해서 새로고침하지 않기  
        - 오늘 쿼터가 초과되면 내일 자동으로 초기화됨
        """
    )

    if mode == "키워드 트렌드 분석":
        page_keyword_trend(api_key, video_limit)
    elif mode == "특정 채널 분석":
        page_single_channel(api_key, video_limit)
    elif mode == "경쟁 채널 벤치마킹":
        page_competitive_channels(api_key, video_limit)


if __name__ == "__main__":
    main()
