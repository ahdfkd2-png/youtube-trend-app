import streamlit as st
import pandas as pd
import math
import re
from datetime import datetime
from collections import Counter

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(
    page_title="YouTube 트렌드·채널 분석기",
    layout="wide",
)

st.title("📊 YouTube 트렌드·채널 분석기 (v2.0)")
st.caption("키워드 / 채널 기반으로 영상 성과와 패턴을 분석해주는 도구입니다.")


# -----------------------------
# YouTube API 유틸 함수
# -----------------------------
@st.cache_resource(show_spinner=False)
def get_youtube_client():
    api_key = st.secrets.get("YOUTUBE_API_KEY")
    if not api_key:
        st.error(
            "❌ YOUTUBE_API_KEY 시크릿이 설정되어 있지 않습니다.\n"
            "Streamlit Cloud > App settings > Secrets 에서 `YOUTUBE_API_KEY=\"...\"` 를 추가해 주세요."
        )
        st.stop()
    return build("youtube", "v3", developerKey=api_key)


def parse_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def parse_duration_to_minutes(iso_duration: str) -> float:
    """
    ISO 8601 형식의 duration (예: PT12M30S)을 분 단위(float)로 변환
    """
    if not iso_duration:
        return 0.0

    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, iso_duration)
    if not m:
        return 0.0

    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2)) if m.group(2) else 0
    seconds = int(m.group(3)) if m.group(3) else 0

    total_minutes = hours * 60 + minutes + seconds / 60
    return total_minutes


# -----------------------------
# 데이터 수집 함수들
# -----------------------------
@st.cache_data(show_spinner="🔍 영상 데이터를 불러오는 중입니다...")
def search_videos(keyword: str, max_results: int = 20):
    youtube = get_youtube_client()

    try:
        search_response = youtube.search().list(
            part="snippet",
            q=keyword,
            type="video",
            order="relevance",
            maxResults=max_results,
        ).execute()
    except HttpError as e:
        st.error(f"유튜브 API 호출 중 오류가 발생했습니다: {e}")
        return pd.DataFrame()

    video_items = search_response.get("items", [])
    video_ids = [item["id"]["videoId"] for item in video_items]

    if not video_ids:
        return pd.DataFrame()

    videos_response = youtube.videos().list(
        part="snippet,statistics,contentDetails",
        id=",".join(video_ids),
    ).execute()

    rows = []
    for item in videos_response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        published_at_str = snippet.get("publishedAt")
        try:
            published_at = datetime.fromisoformat(
                published_at_str.replace("Z", "+00:00")
            )
        except Exception:
            published_at = None

        duration_minutes = parse_duration_to_minutes(content.get("duration"))

        rows.append(
            {
                "video_id": item.get("id"),
                "제목": snippet.get("title"),
                "채널명": snippet.get("channelTitle"),
                "업로드일": published_at,
                "조회수": parse_int(stats.get("viewCount")),
                "좋아요": parse_int(stats.get("likeCount")),
                "댓글수": parse_int(stats.get("commentCount")),
                "영상길이(분)": round(duration_minutes, 1),
                "썸네일": snippet.get("thumbnails", {})
                .get("high", {})
                .get("url"),
                "영상 링크": f"https://www.youtube.com/watch?v={item.get('id')}",
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty and df["업로드일"].notnull().any():
        df = df.sort_values("조회수", ascending=False)
    return df


@st.cache_data(show_spinner="📺 채널을 검색하는 중입니다...")
def search_channels(keyword: str, max_results: int = 5):
    youtube = get_youtube_client()

    try:
        search_response = youtube.search().list(
            part="snippet",
            q=keyword,
            type="channel",
            maxResults=max_results,
        ).execute()
    except HttpError as e:
        st.error(f"유튜브 API 호출 중 오류가 발생했습니다: {e}")
        return pd.DataFrame()

    channel_items = search_response.get("items", [])
    channel_ids = [item["id"]["channelId"] for item in channel_items]

    if not channel_ids:
        return pd.DataFrame()

    channels_response = youtube.channels().list(
        part="snippet,statistics",
        id=",".join(channel_ids),
    ).execute()

    rows = []
    for item in channels_response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})

        rows.append(
            {
                "channel_id": item.get("id"),
                "채널명": snippet.get("title"),
                "설명": snippet.get("description"),
                "썸네일": snippet.get("thumbnails", {})
                .get("high", {})
                .get("url"),
                "구독자수": parse_int(stats.get("subscriberCount")),
                "총조회수": parse_int(stats.get("viewCount")),
                "영상수": parse_int(stats.get("videoCount")),
                "채널 링크": f"https://www.youtube.com/channel/{item.get('id')}",
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("총조회수", ascending=False)
    return df


@st.cache_data(show_spinner="📈 채널 영상 패턴을 분석하는 중입니다...")
def fetch_channel_videos(channel_id: str, max_results: int = 50):
    """
    특정 채널의 최근 영상 목록 + 통계를 수집하여 DataFrame으로 반환
    """
    youtube = get_youtube_client()

    try:
        search_response = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=min(max_results, 50),
        ).execute()
    except HttpError as e:
        st.error(f"채널 영상 검색 중 오류가 발생했습니다: {e}")
        return pd.DataFrame()

    items = search_response.get("items", [])
    video_ids = [item["id"]["videoId"] for item in items]

    if not video_ids:
        return pd.DataFrame()

    videos_response = youtube.videos().list(
        part="snippet,statistics,contentDetails",
        id=",".join(video_ids),
    ).execute()

    rows = []
    for item in videos_response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        published_at_str = snippet.get("publishedAt")
        try:
            published_at = datetime.fromisoformat(
                published_at_str.replace("Z", "+00:00")
            )
        except Exception:
            published_at = None

        duration_minutes = parse_duration_to_minutes(content.get("duration"))

        rows.append(
            {
                "video_id": item.get("id"),
                "제목": snippet.get("title"),
                "업로드일": published_at,
                "조회수": parse_int(stats.get("viewCount")),
                "좋아요": parse_int(stats.get("likeCount")),
                "댓글수": parse_int(stats.get("commentCount")),
                "영상길이(분)": round(duration_minutes, 1),
                "영상 링크": f"https://www.youtube.com/watch?v={item.get('id')}",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 시간/요일 파생 컬럼
    df["업로드일시"] = df["업로드일"]
    df["업로드_요일"] = df["업로드일시"].dt.day_name(locale="ko_KR")
    df["업로드_시각"] = df["업로드일시"].dt.hour
    return df


# -----------------------------
# 텍스트 기반 키워드/SEO 분석
# -----------------------------
KOREAN_STOPWORDS = {
    "영상",
    "조회수",
    "구독",
    "채널",
    "브이로그",
    "일상",
    "시키기",
    "하기",
    "하는",
    "까지",
    "그리고",
    "근데",
    "오늘",
    "이번",
    "정말",
    "진짜",
    "완전",
}


def extract_keywords_from_titles(titles, top_n=15):
    tokens = []
    for t in titles:
        # 한글/영문/숫자만 남기고 나머지 제거
        cleaned = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", t)
        words = cleaned.split()
        for w in words:
            w = w.strip()
            if len(w) <= 1:
                continue
            if w in KOREAN_STOPWORDS:
                continue
            tokens.append(w)

    counter = Counter(tokens)
    return counter.most_common(top_n)


def suggest_title_templates(best_keywords, base="키워드"):
    # 아주 간단한 템플릿 몇 개
    templates = [
        f"{base}만 알면 조회수 터집니다 | {{키워드}}",
        f"초보도 따라하는 {{키워드}} 완전 정리",
        f"몰랐다면 손해였던 {{키워드}} 꿀팁 7가지",
        f"구독자들이 좋아하는 {{키워드}} 콘텐츠 비밀",
    ]

    # 가장 강한 키워드들로 치환 예시 생성
    suggestions = []
    for kw in best_keywords[:3]:
        for tpl in templates:
            suggestions.append(tpl.replace("{{키워드}}", kw))
    return suggestions


# -----------------------------
# 사이드바 입력 영역
# -----------------------------
with st.sidebar:
    st.header("🔧 분석 옵션")

    keyword = st.text_input(
        "검색 키워드 입력 (예: 시니어 쇼핑, 건강, 요리)",
        value="시니어 드라마",
    )

    max_video_results = st.slider(
        "영상 검색 개수",
        min_value=5,
        max_value=30,
        value=15,
        step=5,
    )

    st.markdown("---")
    st.markdown("**채널 분석 옵션**")
    max_channel_video_results = st.slider(
        "채널 패턴 분석 시 가져올 최근 영상 수",
        min_value=10,
        max_value=50,
        value=30,
        step=10,
    )

# -----------------------------
# 메인 탭 구성
# -----------------------------
tab_videos, tab_channels, tab_patterns, tab_seo = st.tabs(
    ["🎬 키워드 영상 분석", "📺 채널 검색·비교", "📈 채널 영상 패턴 분석", "🧠 SEO·키워드 추천"]
)

# --------------------------------------------------
# 🎬 1) 키워드 영상 분석 탭
# --------------------------------------------------
with tab_videos:
    st.subheader("🎬 키워드 기반 인기 영상 분석")

    if keyword:
        df_videos = search_videos(keyword, max_results=max_video_results)

        if df_videos.empty:
            st.warning("해당 키워드로 검색된 영상이 없습니다.")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown("**검색 결과 영상 리스트**")
                st.dataframe(
                    df_videos[
                        [
                            "제목",
                            "채널명",
                            "조회수",
                            "좋아요",
                            "댓글수",
                            "영상길이(분)",
                            "업로드일",
                            "영상 링크",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

            with c2:
                st.markdown("**요약 통계**")
                st.metric(
                    "총 조회수",
                    f"{df_videos['조회수'].sum():,}",
                )
                st.metric(
                    "평균 조회수",
                    f"{df_videos['조회수'].mean():,.0f}",
                )
                st.metric(
                    "평균 영상 길이(분)",
                    f"{df_videos['영상길이(분)'].mean():.1f}",
                )

            st.markdown("### 🔝 조회수 상위 5개 영상")
            st.dataframe(
                df_videos.sort_values("조회수", ascending=False)
                .head(5)[["제목", "채널명", "조회수", "좋아요", "영상길이(분)", "영상 링크"]],
                use_container_width=True,
                hide_index=True,
            )

    else:
        st.info("왼쪽 사이드바에서 **검색 키워드**를 입력해 주세요.")

# --------------------------------------------------
# 📺 2) 채널 검색·비교 탭
# --------------------------------------------------
with tab_channels:
    st.subheader("📺 채널 검색 및 기본 지표 비교")

    if keyword:
        df_channels = search_channels(keyword, max_results=5)
        if df_channels.empty:
            st.warning("해당 키워드와 연관된 채널이 없습니다.")
        else:
            st.dataframe(
                df_channels[
                    [
                        "채널명",
                        "구독자수",
                        "총조회수",
                        "영상수",
                        "채널 링크",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("#### 🔍 분석할 채널 선택")
            channel_options = {
                f"{row['채널명']} (구독자 {row['구독자수']:,})": row["channel_id"]
                for _, row in df_channels.iterrows()
            }
            selected_label = st.selectbox(
                "채널 선택",
                options=list(channel_options.keys()),
            )
            selected_channel_id = channel_options[selected_label]

            st.session_state["selected_channel_for_pattern"] = (
                selected_channel_id,
                selected_label,
            )
            st.success("이 채널이 **패턴 분석 탭**에서 기본 선택으로 사용됩니다.")

    else:
        st.info("왼쪽에서 키워드를 입력하면 관련 채널을 불러옵니다.")

# --------------------------------------------------
# 📈 3) 채널 영상 패턴 분석 탭
# --------------------------------------------------
with tab_patterns:
    st.subheader("📈 채널 영상 패턴 분석")

    default_label = None
    default_channel_id = None
    if "selected_channel_for_pattern" in st.session_state:
        default_channel_id, default_label = st.session_state[
            "selected_channel_for_pattern"
        ]

    st.markdown("**분석할 채널 ID 또는 URL을 직접 넣어도 됩니다.**")
    input_channel_id = st.text_input(
        "채널 ID 또는 채널 URL",
        value=default_channel_id or "",
        placeholder="예) UCxxxxxxxx 또는 https://www.youtube.com/channel/UCxxxx",
    )

    if input_channel_id:
        # URL 형태면 ID만 추출 시도
        if "http" in input_channel_id:
            m = re.search(r"/channel/([A-Za-z0-9_-]+)", input_channel_id)
            if m:
                channel_id = m.group(1)
            else:
                st.error("채널 URL 형식이 올바르지 않습니다. /channel/ 뒤의 ID를 사용해 주세요.")
                st.stop()
        else:
            channel_id = input_channel_id.strip()

        df_channel_videos = fetch_channel_videos(
            channel_id, max_results=max_channel_video_results
        )

        if df_channel_videos.empty:
            st.warning("채널에서 분석할 수 있는 영상 데이터를 가져오지 못했습니다.")
        else:
            st.markdown(
                f"최근 {len(df_channel_videos)}개 영상 기준으로 패턴을 분석합니다."
            )

            # 상위 영상 테이블
            st.markdown("### 🔝 조회수 상위 10개 영상")
            st.dataframe(
                df_channel_videos.sort_values("조회수", ascending=False)
                .head(10)[
                    [
                        "제목",
                        "조회수",
                        "좋아요",
                        "댓글수",
                        "영상길이(분)",
                        "업로드일",
                        "영상 링크",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

            # 업로드 요일/시간 패턴
            st.markdown("### 🕒 업로드 요일·시간 패턴")

            c1, c2 = st.columns(2)
            with c1:
                pivot_day = (
                    df_channel_videos.groupby("업로드_요일")["조회수"]
                    .mean()
                    .sort_values(ascending=False)
                )
                st.bar_chart(pivot_day, use_container_width=True)
                st.caption("요일별 평균 조회수")

            with c2:
                pivot_hour = (
                    df_channel_videos.groupby("업로드_시각")["조회수"]
                    .mean()
                    .sort_index()
                )
                st.line_chart(pivot_hour, use_container_width=True)
                st.caption("시간대별 평균 조회수")

            # 길이 vs 조회수
            st.markdown("### ⏱ 영상 길이 vs 조회수")
            scatter_df = df_channel_videos[
                ["영상길이(분)", "조회수"]
            ].dropna()
            if len(scatter_df) >= 2:
                st.scatter_chart(
                    scatter_df,
                    x="영상길이(분)",
                    y="조회수",
                    use_container_width=True,
                )
            else:
                st.info("산점도를 그리기에 데이터가 부족합니다.")

            # 상위 영상에서 키워드 패턴 추출
            st.markdown("### 🧩 상위 영상 제목 키워드 패턴")

            top_titles = (
                df_channel_videos.sort_values("조회수", ascending=False)
                .head(20)["제목"]
                .tolist()
            )
            kw_counts = extract_keywords_from_titles(top_titles, top_n=15)

            if kw_counts:
                kw_df = pd.DataFrame(
                    kw_counts, columns=["키워드", "빈도수"]
                )
                st.dataframe(
                    kw_df,
                    use_container_width=True,
                    hide_index=True,
                )
                best_keywords = [k for k, _ in kw_counts]
                st.success(
                    "이 키워드들은 이 채널에서 **조회수가 잘 나온 제목에 자주 등장한 단어**들입니다."
                )
            else:
                best_keywords = []
                st.info("키워드 패턴을 추출할 수 있는 제목이 충분하지 않습니다.")

    else:
        st.info(
            "분석할 채널 ID 또는 URL을 입력하거나, **채널 검색 탭에서 채널을 선택하면 자동으로 채워집니다.**"
        )

# --------------------------------------------------
# 🧠 4) SEO·키워드 추천 탭
# --------------------------------------------------
with tab_seo:
    st.subheader("🧠 SEO·키워드·제목 추천")

    st.markdown(
        "이 탭은 **키워드 또는 채널 상위 영상 제목**을 기반으로, "
        "활용하기 좋은 키워드와 제목 템플릿을 제안합니다."
    )

    base_text = st.text_area(
        "분석할 제목 모음 또는 키워드 (여러 줄 가능)",
        value=keyword or "",
        height=120,
        placeholder="예) 내가 실제로 썼던 영상 제목들, 혹은 관심 키워드들을 줄바꿈으로 나열",
    )

    if st.button("🔍 키워드/제목 분석 실행"):
        lines = [l.strip() for l in base_text.split("\n") if l.strip()]
        if not lines:
            st.warning("분석할 텍스트를 입력해 주세요.")
        else:
            kw_counts = extract_keywords_from_titles(lines, top_n=20)
            if not kw_counts:
                st.info("유의미한 키워드를 추출하지 못했습니다. 다른 텍스트로 시도해 보세요.")
            else:
                st.markdown("### 🔑 추천 키워드 (제목에 자주 쓰인 단어)")
                kw_df = pd.DataFrame(kw_counts, columns=["키워드", "빈도수"])
                st.dataframe(
                    kw_df,
                    use_container_width=True,
                    hide_index=True,
                )

                strong_keywords = [k for k, _ in kw_counts]
                st.markdown("### ✏ 추천 제목 템플릿 예시")
                title_suggestions = suggest_title_templates(
                    strong_keywords, base="조회수 올리는"
                )

                for i, t in enumerate(title_suggestions, start=1):
                    st.write(f"{i}. {t}")

                st.caption(
                    "※ 위 제목은 그대로 써도 되지만, 채널 톤앤매너에 맞게 살짝만 수정해서 쓰면 더 자연스럽습니다."
                )
    else:
        st.info("분석할 텍스트를 입력한 뒤, **[🔍 키워드/제목 분석 실행]** 버튼을 눌러 주세요.")
