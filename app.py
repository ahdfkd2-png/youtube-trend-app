import os
import math
from collections import Counter
from typing import List, Dict, Any, Tuple

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from googleapiclient.discovery import build
import isodate

# ============================
# 기본 설정
# ============================

st.set_page_config(
    page_title="YouTube 트렌드·채널 분석기 (v2.0)",
    page_icon="📊",
    layout="wide",
)

st.title("📊 YouTube 트렌드·채널 분석기 (v2.0)")
st.write("키워드 / 채널 단위로 트렌드, 패턴, SEO, 경쟁 분석까지 한 번에 보는 대시보드입니다.")

# ============================
# 유틸 함수
# ============================


@st.cache_resource(show_spinner=False)
def get_youtube_client():
    """Streamlit Secrets 에서 API 키를 읽어 YouTube 클라이언트 생성"""
    api_key = st.secrets.get("YOUTUBE_API_KEY")
    if not api_key:
        st.error(
            "❌ YOUTUBE_API_KEY 가 설정되지 않았습니다.\n\n"
            "▶ Streamlit Cloud → App settings → Secrets 에\n"
            '   `YOUTUBE_API_KEY = "당신의_API_키"` 를 입력해주세요.'
        )
        return None
    return build("youtube", "v3", developerKey=api_key)


def parse_duration_to_minutes(duration_str: str) -> float:
    """ISO 8601 영상 길이 문자열을 분으로 변환"""
    try:
        duration = isodate.parse_duration(duration_str)
        return duration.total_seconds() / 60
    except Exception:
        return np.nan


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def extract_keywords(texts: List[str], top_n: int = 30) -> List[Tuple[str, int]]:
    """아주 단순한 형태의 영어/숫자 키워드 추출기 (한국어는 형태소 분석 없이 단어 단위 분리)"""
    words = []
    for t in texts:
        if not isinstance(t, str):
            continue
        # 특수문자 제거 & 공백 기준 분리
        for w in t.replace("\n", " ").replace("|", " ").replace(",", " ").split(" "):
            w = w.strip().lower()
            if len(w) <= 1:
                continue
            # 해시태그, 기호 제거
            w = w.strip("#[](){}!:;\"'")
            if not w:
                continue
            words.append(w)
    counter = Counter(words)
    return counter.most_common(top_n)


def korean_day_name(weekday: int) -> str:
    """0=월 ... 6=일"""
    mapping = ["월", "화", "수", "목", "금", "토", "일"]
    if 0 <= weekday <= 6:
        return mapping[weekday]
    return ""


# ============================
# YouTube API 호출 함수
# ============================


def search_videos_by_keyword(youtube, keyword: str, max_results: int = 50) -> pd.DataFrame:
    """키워드 기반 인기 영상 검색 후 상세 정보 수집"""
    search_res = (
        youtube.search()
        .list(
            part="snippet",
            q=keyword,
            type="video",
            order="viewCount",
            maxResults=min(max_results, 50),
        )
        .execute()
    )

    video_ids = [item["id"]["videoId"] for item in search_res.get("items", [])]

    if not video_ids:
        return pd.DataFrame()

    video_res = (
        youtube.videos()
        .list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        )
        .execute()
    )

    rows = []
    for item in video_res.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        duration_min = parse_duration_to_minutes(content.get("duration", "PT0M"))
        published_at = pd.to_datetime(snippet.get("publishedAt"))

        rows.append(
            {
                "영상ID": item.get("id"),
                "제목": snippet.get("title"),
                "채널명": snippet.get("channelTitle"),
                "채널ID": snippet.get("channelId"),
                "업로드일시": published_at,
                "업로드일": published_at.date(),
                "업로드_연도": published_at.year,
                "업로드_월": published_at.month,
                "업로드_요일": korean_day_name(published_at.weekday()),
                "업로드_시각": published_at.hour,
                "영상길이_분": duration_min,
                "조회수": safe_int(stats.get("viewCount")),
                "좋아요": safe_int(stats.get("likeCount")),
                "댓글수": safe_int(stats.get("commentCount")),
                "태그": ", ".join(snippet.get("tags", [])) if snippet.get("tags") else "",
                "설명": snippet.get("description", ""),
                "썸네일": snippet.get("thumbnails", {})
                .get("high", {})
                .get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values("조회수", ascending=False, inplace=True)
        df.reset_index(drop=True, inplace=True)

    return df


def search_channels(youtube, keyword: str, max_results: int = 10) -> pd.DataFrame:
    """키워드로 채널 검색"""
    res = (
        youtube.search()
        .list(
            part="snippet",
            q=keyword,
            type="channel",
            maxResults=max_results,
        )
        .execute()
    )

    rows = []
    for item in res.get("items", []):
        snippet = item.get("snippet", {})
        rows.append(
            {
                "채널명": snippet.get("title"),
                "채널ID": item["id"]["channelId"],
                "설명": snippet.get("description"),
            }
        )
    return pd.DataFrame(rows)


def fetch_channel_basic_info(youtube, channel_id: str) -> Dict[str, Any]:
    """채널 기본 정보"""
    res = (
        youtube.channels()
        .list(
            part="snippet,statistics",
            id=channel_id,
        )
        .execute()
    )
    items = res.get("items", [])
    if not items:
        return {}
    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "채널명": snippet.get("title"),
        "설명": snippet.get("description"),
        "구독자": safe_int(stats.get("subscriberCount")),
        "총조회수": safe_int(stats.get("viewCount")),
        "영상수": safe_int(stats.get("videoCount")),
        "생성일": snippet.get("publishedAt"),
    }


def fetch_channel_videos(youtube, channel_id: str, max_results: int = 120) -> pd.DataFrame:
    """
    채널의 최근 영상들 정보를 가져온다.
    실제로는 playlistItems API로 'uploads' 재생목록에서 가져오는 게 더 정확하지만,
    여기서는 search.list 로 간단히 구현 (최근 영상 기준)
    """
    search_res = (
        youtube.search()
        .list(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=min(max_results, 50),
        )
        .execute()
    )

    video_ids = [item["id"]["videoId"] for item in search_res.get("items", [])]
    if not video_ids:
        return pd.DataFrame()

    video_res = (
        youtube.videos()
        .list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        )
        .execute()
    )

    rows = []
    for item in video_res.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        published_at = pd.to_datetime(snippet.get("publishedAt"))
        duration_min = parse_duration_to_minutes(content.get("duration", "PT0M"))

        rows.append(
            {
                "영상ID": item.get("id"),
                "제목": snippet.get("title"),
                "업로드일시": published_at,
                "업로드일": published_at.date(),
                "업로드_연도": published_at.year,
                "업로드_월": published_at.month,
                "업로드_요일": korean_day_name(published_at.weekday()),
                "업로드_시각": published_at.hour,
                "영상길이_분": duration_min,
                "조회수": safe_int(stats.get("viewCount")),
                "좋아요": safe_int(stats.get("likeCount")),
                "댓글수": safe_int(stats.get("commentCount")),
                "태그": ", ".join(snippet.get("tags", [])) if snippet.get("tags") else "",
                "설명": snippet.get("description", ""),
                "썸네일": snippet.get("thumbnails", {})
                .get("high", {})
                .get("url", ""),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values("업로드일시", ascending=False, inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


# ============================
# 분석 함수들
# ============================


def summarize_basic_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """영상 데이터 기본 통계"""
    if df.empty:
        return {}
    return {
        "영상수": len(df),
        "평균조회수": round(df["조회수"].mean()),
        "중간값조회수": int(df["조회수"].median()),
        "최고조회수": int(df["조회수"].max()),
        "평균좋아요": round(df["좋아요"].mean()),
        "평균댓글": round(df["댓글수"].mean()),
        "평균영상길이(분)": round(df["영상길이_분"].mean(), 1),
    }


def recommend_best_upload_times(df: pd.DataFrame) -> pd.DataFrame:
    """
    요일/시간대별 평균 조회수를 기준으로
    '추천 업로드 시간대'를 뽑는다.
    """
    if df.empty:
        return pd.DataFrame()

    # 시간대를 4구간으로 나눔
    def bucket_hour(h):
        if 6 <= h < 12:
            return "아침(06-12)"
        elif 12 <= h < 18:
            return "낮(12-18)"
        elif 18 <= h < 24:
            return "저녁(18-24)"
        else:
            return "심야(00-06)"

    temp = df.copy()
    temp["시간대구간"] = temp["업로드_시각"].apply(bucket_hour)
    grp = temp.groupby(["업로드_요일", "시간대구간"])["조회수"].mean().reset_index()
    grp.rename(columns={"조회수": "평균조회수"}, inplace=True)
    grp.sort_values("평균조회수", ascending=False, inplace=True)
    return grp


def generate_content_ideas(keyword: str, df: pd.DataFrame) -> List[str]:
    """간단 규칙 기반 콘텐츠 기획 아이디어"""
    ideas = []
    base = keyword.strip()
    if not base:
        base = "당신의 주제"

    ideas.append(f"📌 {base} 관련 '실제 사례·썰' 형식의 스토리텔링 영상")
    ideas.append(f"📌 '{base}' 잘못된 상식 TOP5 / 흔한 실수 정리 영상")
    ideas.append(f"📌 구독자 Q&A: '{base}'에 대해 자주 묻는 질문 모아서 답변")
    ideas.append(f"📌 '{base}' 초보자용 입문 가이드 (완전 기초부터 알려주기)")
    ideas.append(f"📌 '{base}' 최신 트렌드와 과거 비교 (전/후 변화 분석)")
    if not df.empty:
        short_ones = df.sort_values("영상길이_분").head(10)
        if not short_ones.empty:
            ideas.append(
                "📌 조회수 잘 나온 '짧은 영상' 포맷을 활용해서 Shorts 시리즈 만들어보기"
            )
        high_like = df.sort_values("좋아요", ascending=False).head(5)
        if not high_like.empty:
            ideas.append(
                "📌 좋아요 비율 높은 영상들의 공통 포맷(제목 구조/썸네일 스타일)을 따라가는 시리즈"
            )
    return ideas


def generate_title_tag_templates(keyword: str, top_keywords: List[Tuple[str, int]]) -> Dict[str, List[str]]:
    """간단 제목/태그 템플릿"""
    core = keyword.strip()
    if not core and top_keywords:
        core = top_keywords[0][0]
    if not core:
        core = "이 주제"

    title_templates = [
        f"{core} 덕분에 인생이 바뀐 이야기",
        f"아무도 안 알려주는 {core} 현실 조언",
        f"처음부터 다시 배우는 {core} (완전 기초편)",
        f"{core} 할 때 꼭 알아야 하는 5가지",
        f"요즘 다들 하는 {core}, 하지만 여러분이 모르는 진실",
    ]

    # 태그: 키워드 + 상위 단어들 조합
    tags = [core]
    for w, _ in top_keywords[:15]:
        if w not in tags:
            tags.append(w)

    return {"titles": title_templates, "tags": tags}


# ============================
# 메인 UI
# ============================

youtube = get_youtube_client()

with st.sidebar:
    st.header("⚙️ 분석 모드 선택")
    mode = st.radio(
        "분석 대상",
        ["키워드 트렌드 분석", "특정 채널 분석", "경쟁 채널 벤치마킹"],
    )

    max_results = st.slider("가져올 영상 개수 (대략)", 20, 150, 60, 10)
    st.caption("※ 너무 많이 가져오면 YouTube API 쿼터를 빨리 소모할 수 있습니다.")

if youtube is None:
    st.stop()

# -----------------------------
# 1) 키워드 트렌드 분석
# -----------------------------
if mode == "키워드 트렌드 분석":
    keyword = st.text_input("🔍 분석할 키워드를 입력하세요 (예: 시니어 쇼핑, 건강, 요리)", value="")
    if not keyword:
        st.info("좌측 사이드바 설정 후, 키워드를 입력하면 분석을 시작합니다.")
        st.stop()

    with st.spinner("YouTube에서 데이터를 가져오는 중입니다..."):
        df = search_videos_by_keyword(youtube, keyword, max_results=max_results)

    if df.empty:
        st.warning("해당 키워드로 영상 데이터를 찾지 못했습니다.")
        st.stop()

    st.subheader(f"🎬 '{keyword}' 키워드 인기 영상 데이터 ({len(df)}개)")
    st.dataframe(
        df[
            [
                "제목",
                "채널명",
                "조회수",
                "좋아요",
                "댓글수",
                "영상길이_분",
                "업로드일",
                "업로드_요일",
                "업로드_시각",
            ]
        ],
        use_container_width=True,
    )

    # 기본 통계 카드
    stats = summarize_basic_stats(df)
    if stats:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("영상수", f"{stats['영상수']}개")
        c2.metric("평균 조회수", f"{stats['평균조회수']:,}")
        c3.metric("중간값 조회수", f"{stats['중간값조회수']:,}")
        c4.metric("최고 조회수", f"{stats['최고조회수']:,}")

    # 그래프들
    st.markdown("### 📈 조회수 분포")

    fig_hist = px.histogram(df, x="조회수", nbins=20, title="조회수 분포")
    st.plotly_chart(fig_hist, use_container_width=True)

    st.markdown("### ⏱ 영상 길이 vs 조회수")
    fig_len = px.scatter(
        df,
        x="영상길이_분",
        y="조회수",
        hover_data=["제목", "채널명"],
        trendline="ols",
        title="영상 길이(분) vs 조회수",
    )
    st.plotly_chart(fig_len, use_container_width=True)

    st.markdown("### 📆 요일·시간대별 업로드 패턴")

    # 요일별 영상 수
    fig_dow = px.histogram(
        df,
        x="업로드_요일",
        title="요일별 업로드 영상 수",
    )
    st.plotly_chart(fig_dow, use_container_width=True)

    # 시간대 히스토그램
    fig_hour = px.histogram(
        df,
        x="업로드_시각",
        title="시간대별 업로드 영상 수",
    )
    st.plotly_chart(fig_hour, use_container_width=True)

    # 키워드/태그 분석
    st.markdown("### 🔍 제목/태그 키워드 분석")

    title_keywords = extract_keywords(df["제목"].tolist(), top_n=30)
    tag_keywords = extract_keywords(df["태그"].tolist(), top_n=30)

    col1, col2 = st.columns(2)
    with col1:
        st.write("**제목에서 자주 등장하는 단어 Top 30**")
        st.table(pd.DataFrame(title_keywords, columns=["단어", "빈도"]))
    with col2:
        st.write("**태그에서 자주 등장하는 단어 Top 30**")
        st.table(pd.DataFrame(tag_keywords, columns=["단어", "빈도"]))

    # 제목/태그 템플릿 + 콘텐츠 아이디어
    st.markdown("### 🧠 콘텐츠 기획 & SEO 도움")

    templates = generate_title_tag_templates(keyword, title_keywords or tag_keywords)
    ideas = generate_content_ideas(keyword, df)

    c1, c2 = st.columns(2)
    with c1:
        st.write("**추천 제목 템플릿**")
        for t in templates["titles"]:
            st.write("- ", t)
    with c2:
        st.write("**추천 태그 후보**")
        st.write(", ".join(templates["tags"]))

    st.write("**향후 콘텐츠 기획 아이디어**")
    for idea in ideas:
        st.write(idea)

    # CSV 다운로드
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 이 키워드 영상 데이터 CSV 다운로드",
        csv,
        file_name=f"youtube_keyword_{keyword}.csv",
        mime="text/csv",
    )

# -----------------------------
# 2) 특정 채널 분석
# -----------------------------
elif mode == "특정 채널 분석":
    st.markdown("### 🔍 채널 검색 또는 채널ID 직접 입력")

    tab1, tab2 = st.tabs(["채널 검색", "채널 ID 직접 입력"])

    channel_id = None
    channel_keyword = None

    with tab1:
        channel_keyword = st.text_input(
            "채널을 검색할 키워드 (채널명 / 주제 등)",
            value="",
        )
        if channel_keyword:
            if st.button("채널 검색하기"):
                with st.spinner("채널을 검색하는 중입니다..."):
                    ch_df = search_channels(youtube, channel_keyword, max_results=15)
                if ch_df.empty:
                    st.warning("검색 결과가 없습니다.")
                else:
                    st.write("검색 결과에서 분석할 채널을 선택하세요.")
                    selected = st.selectbox(
                        "채널 선택", [f"{r['채널명']} ({r['채널ID']})" for _, r in ch_df.iterrows()]
                    )
                    if selected:
                        channel_id = selected.split("(")[-1].replace(")", "").strip()

    with tab2:
        manual_id = st.text_input("YouTube 채널 ID 직접 입력", value="")
        if manual_id:
            channel_id = manual_id.strip()

    if not channel_id:
        st.info("채널을 선택하거나 채널 ID를 입력하면 분석을 시작합니다.")
        st.stop()

    with st.spinner("채널 기본 정보와 영상 데이터를 가져오는 중입니다..."):
        basic = fetch_channel_basic_info(youtube, channel_id)
        df = fetch_channel_videos(youtube, channel_id, max_results=max_results)

    if not basic:
        st.error("채널 정보를 가져오지 못했습니다. 채널 ID를 다시 확인해주세요.")
        st.stop()

    st.subheader(f"📺 채널 정보: {basic['채널명']}")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("구독자", f"{basic['구독자']:,}")
    col2.metric("총 조회수", f"{basic['총조회수']:,}")
    col3.metric("영상 수(전체)", f"{basic['영상수']:,}")
    col4.metric("분석 영상 수(최근)", f"{len(df)}개")

    st.write("**채널 설명**")
    st.write(basic.get("설명") or "(설명 없음)")

    if df.empty:
        st.warning("이 채널에서 최근 영상을 가져오지 못했습니다.")
        st.stop()

    st.markdown("### 🎬 최근 영상 데이터")
    st.dataframe(
        df[
            [
                "제목",
                "조회수",
                "좋아요",
                "댓글수",
                "영상길이_분",
                "업로드일",
                "업로드_요일",
                "업로드_시각",
            ]
        ],
        use_container_width=True,
    )

    stats = summarize_basic_stats(df)
    if stats:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("평균 조회수", f"{stats['평균조회수']:,}")
        c2.metric("중간값 조회수", f"{stats['중간값조회수']:,}")
        c3.metric("평균 좋아요", f"{stats['평균좋아요']:,}")
        c4.metric("평균 영상 길이(분)", f"{stats['평균영상길이(분)']}")

    # 인기 영상 TOP 10
    st.markdown("### 🔥 인기 영상 TOP 10")
    top_videos = df.sort_values("조회수", ascending=False).head(10)[
        ["제목", "조회수", "좋아요", "댓글수", "영상길이_분", "업로드일"]
    ]
    st.table(top_videos)

    # 업로드 패턴 분석
    st.markdown("### 📆 업로드 패턴 분석 (요일 / 시간대)")

    fig_dow = px.histogram(
        df,
        x="업로드_요일",
        title="요일별 업로드 영상 수",
    )
    st.plotly_chart(fig_dow, use_container_width=True)

    fig_hour = px.histogram(
        df,
        x="업로드_시각",
        title="시간대별 업로드 영상 수",
    )
    st.plotly_chart(fig_hour, use_container_width=True)

    # 요일/시간대별 평균 조회수 히트맵
    temp = df.copy()
    if not temp.empty:
        pivot = (
            temp.groupby(["업로드_요일", "업로드_시각"])["조회수"]
            .mean()
            .reset_index()
        )
        heat = pivot.pivot(index="업로드_요일", columns="업로드_시각", values="조회수")
        fig_heat = px.imshow(
            heat,
            aspect="auto",
            color_continuous_scale="Blues",
            labels=dict(color="평균 조회수"),
            title="요일 / 시간대별 평균 조회수 히트맵",
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    # 추천 업로드 시간
    st.markdown("### ⏰ 추천 업로드 시간대")
    best_times = recommend_best_upload_times(df)
    if not best_times.empty:
        st.write("평균 조회수 상위 10개 시간대:")
        st.table(best_times.head(10))
    else:
        st.write("추천 시간대를 계산할 수 있는 데이터가 부족합니다.")

    # 제목/태그 키워드 분석 + 기획 아이디어
    st.markdown("### 🔍 제목/태그 분석 및 콘텐츠 아이디어")

    title_keywords = extract_keywords(df["제목"].tolist(), top_n=30)
    tag_keywords = extract_keywords(df["태그"].tolist(), top_n=30)

    col1, col2 = st.columns(2)
    with col1:
        st.write("**제목에서 자주 등장하는 단어 Top 30**")
        st.table(pd.DataFrame(title_keywords, columns=["단어", "빈도"]))
    with col2:
        st.write("**태그에서 자주 등장하는 단어 Top 30**")
        st.table(pd.DataFrame(tag_keywords, columns=["단어", "빈도"]))

    templates = generate_title_tag_templates(
        basic["채널명"], title_keywords or tag_keywords
    )
    ideas = generate_content_ideas(basic["채널명"], df)

    c1, c2 = st.columns(2)
    with c1:
        st.write("**이 채널에 어울리는 제목 템플릿**")
        for t in templates["titles"]:
            st.write("- ", t)
    with c2:
        st.write("**추천 태그 후보**")
        st.write(", ".join(templates["tags"]))

    st.write("**향후 콘텐츠 기획 아이디어**")
    for idea in ideas:
        st.write(idea)

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 이 채널 최근 영상 데이터 CSV 다운로드",
        csv,
        file_name=f"youtube_channel_{basic['채널명']}.csv",
        mime="text/csv",
    )

# -----------------------------
# 3) 경쟁 채널 벤치마킹
# -----------------------------
elif mode == "경쟁 채널 벤치마킹":
    st.markdown("### 🥊 경쟁 채널 벤치마킹 (간단 버전)")

    keyword = st.text_input(
        "경쟁 채널을 찾을 키워드 (예: '시니어 드라마', '부동산 투자', '다이어트')",
        value="",
    )
    if not keyword:
        st.info("키워드를 입력하면 관련 채널들을 찾아서 비교합니다.")
        st.stop()

    with st.spinner("경쟁 채널 후보를 찾는 중입니다..."):
        ch_df = search_channels(youtube, keyword, max_results=8)

    if ch_df.empty:
        st.warning("관련 채널을 찾지 못했습니다.")
        st.stop()

    st.write("**관련 채널 후보**")
    st.table(ch_df[["채널명", "채널ID", "설명"]])

    selected_ids = st.multiselect(
        "벤치마킹할 채널 2~5개를 선택하세요.",
        options=ch_df["채널ID"].tolist(),
        default=ch_df["채널ID"].tolist()[:3],
        format_func=lambda cid: ch_df[ch_df["채널ID"] == cid]["채널명"].iloc[0],
    )

    if len(selected_ids) < 2:
        st.info("두 개 이상 선택해야 비교가 의미 있습니다.")
        st.stop()

    results = []
    with st.spinner("선택한 채널의 기본 정보를 가져오는 중입니다..."):
        for cid in selected_ids:
            info = fetch_channel_basic_info(youtube, cid)
            if not info:
                continue
            info["채널ID"] = cid
            results.append(info)

    if not results:
        st.error("선택한 채널들의 정보를 가져오지 못했습니다.")
        st.stop()

    bench_df = pd.DataFrame(results)[
        ["채널명", "채널ID", "구독자", "총조회수", "영상수"]
    ]
    st.subheader("📊 경쟁 채널 기본 지표 비교")
    st.dataframe(bench_df, use_container_width=True)

    fig_sub = px.bar(
        bench_df,
        x="채널명",
        y="구독자",
        title="채널별 구독자 수 비교",
        text="구독자",
    )
    st.plotly_chart(fig_sub, use_container_width=True)

    fig_view = px.bar(
        bench_df,
        x="채널명",
        y="총조회수",
        title="채널별 총 조회수 비교",
        text="총조회수",
    )
    st.plotly_chart(fig_view, use_container_width=True)

    st.markdown("### 🧾 요약 코멘트 (규칙 기반)")

    # 아주 간단한 규칙 기반 요약
    top_sub = bench_df.sort_values("구독자", ascending=False).iloc[0]
    top_view = bench_df.sort_values("총조회수", ascending=False).iloc[0]
    top_prod = bench_df.sort_values("영상수", ascending=False).iloc[0]

    st.write(
        f"- **구독자 기준 1위 채널**: {top_sub['채널명']} (구독자 {top_sub['구독자']:,}명)"
    )
    st.write(
        f"- **총 조회수 기준 1위 채널**: {top_view['채널명']} (총 조회수 {top_view['총조회수']:,}회)"
    )
    st.write(
        f"- **영상 생산량(영상 수) 기준 1위 채널**: {top_prod['채널명']} (영상 {top_prod['영상수']:,}개)"
    )
    st.write(
        "- 구독자 대비 조회수 비율이 높은 채널은 **충성도가 높은 시청자층**을 갖고 있을 가능성이 큽니다.\n"
        "  → 이 채널들의 제목 구조, 썸네일 스타일, 업로드 빈도를 집중적으로 참고해보세요."
    )
