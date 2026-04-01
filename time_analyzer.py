"""最適投稿時間帯分析 - インプレッションが高い時間帯・曜日を特定"""
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


@dataclass
class TimeAnalysisResult:
    handle: str
    post_count: int

    # hour(0-23) → avg_views
    hour_avg_views: dict = field(default_factory=dict)
    # hour(0-23) → post_count
    hour_post_count: dict = field(default_factory=dict)

    # weekday(0=月〜6=日) → avg_views
    weekday_avg_views: dict = field(default_factory=dict)
    # weekday(0=月〜6=日) → post_count
    weekday_post_count: dict = field(default_factory=dict)

    best_hours: list = field(default_factory=list)   # top3 [(hour, avg_views), ...]
    best_weekdays: list = field(default_factory=list) # top3 [(weekday, avg_views), ...]

    sample_posts_by_hour: dict = field(default_factory=dict)  # hour → best post text

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def _parse_created_at(created: str) -> Optional[datetime.datetime]:
    """'Fri Mar 21 12:34:56 +0000 2025' → datetime(UTC)"""
    try:
        return datetime.datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        return None


def analyze_posting_time(
    api_key: str,
    handle: str,
    max_posts: int = 500,
    progress_callback: Optional[Callable] = None,
) -> TimeAnalysisResult:
    import time as _time
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    _cb(0.1, f"@{handle} の投稿を取得中（最大{max_posts}件）...")
    tweets = client.search_all_tweets(
        f"from:{handle} -filter:replies", max_results=max_posts
    )

    result = TimeAnalysisResult(handle=handle, post_count=len(tweets))

    _cb(0.7, "時間帯・曜日ごとに集計中...")

    hour_views: dict = defaultdict(list)
    weekday_views: dict = defaultdict(list)
    hour_best: dict = {}  # hour → best tweet text

    for tw in tweets:
        created = tw.get("tweet_created_at") or tw.get("created_at", "")
        dt = _parse_created_at(created)
        if not dt:
            continue

        # JST変換
        jst = dt + datetime.timedelta(hours=9)
        hour = jst.hour
        weekday = jst.weekday()  # 0=月〜6=日

        views = tw.get("views_count") or tw.get("view_count") or 0
        try:
            views = int(views)
        except Exception:
            views = 0

        hour_views[hour].append(views)
        weekday_views[weekday].append(views)

        # 各時間帯のベスト投稿（インプレ最大）
        text = tw.get("full_text") or tw.get("text") or ""
        if hour not in hour_best or views > hour_best[hour][0]:
            hour_best[hour] = (views, text[:80])

    # 平均計算
    result.hour_avg_views = {h: int(sum(v) / len(v)) for h, v in hour_views.items()}
    result.hour_post_count = {h: len(v) for h, v in hour_views.items()}
    result.weekday_avg_views = {w: int(sum(v) / len(v)) for w, v in weekday_views.items()}
    result.weekday_post_count = {w: len(v) for w, v in weekday_views.items()}

    # TOP3
    result.best_hours = sorted(
        result.hour_avg_views.items(), key=lambda x: x[1], reverse=True
    )[:3]
    result.best_weekdays = sorted(
        result.weekday_avg_views.items(), key=lambda x: x[1], reverse=True
    )[:3]

    result.sample_posts_by_hour = {h: t for h, (_, t) in hour_best.items()}

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
