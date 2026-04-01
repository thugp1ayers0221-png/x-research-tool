"""キーワード時系列バズり推移 - キーワードのバズり波形を時系列で可視化"""
import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient


@dataclass
class TrendPoint:
    date_label: str       # "3/25〜3/31" など
    since: str            # "2025-03-25"
    until: str            # "2025-04-01"
    post_count: int
    avg_views: int
    max_views: int
    top_post_text: str
    top_post_author: str


@dataclass
class TrendResult:
    keyword: str
    days: int
    interval_days: int
    points: list = field(default_factory=list)  # list[TrendPoint]

    trend_direction: str = ""   # "上昇中" / "横ばい" / "下降中"
    peak_period: str = ""
    total_posts_analyzed: int = 0

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def analyze_trend(
    api_key: str,
    keyword: str,
    days: int = 90,
    interval_days: int = 7,
    lang: str = "ja",
    progress_callback: Optional[Callable] = None,
) -> TrendResult:
    import time as _time
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    result = TrendResult(keyword=keyword, days=days, interval_days=interval_days)
    today = datetime.date.today()
    points = []

    # days を interval_days ずつに分割
    intervals = []
    cur = today - datetime.timedelta(days=days)
    while cur < today:
        end = min(cur + datetime.timedelta(days=interval_days), today)
        intervals.append((cur, end))
        cur = end

    total = len(intervals)
    for i, (since, until) in enumerate(intervals):
        pct = 0.1 + 0.85 * (i / max(total, 1))
        label = f"{since.month}/{since.day}〜{until.month}/{until.day}"
        _cb(pct, f"「{keyword}」の推移を取得中... {label}")

        lang_filter = f" lang:{lang}" if lang else ""
        query = f"{keyword}{lang_filter} since:{since.isoformat()} until:{until.isoformat()} -filter:replies"
        try:
            tweets = client.search_all_tweets(query, max_results=100)
        except Exception:
            tweets = []

        if not tweets:
            points.append(TrendPoint(
                date_label=label,
                since=since.isoformat(),
                until=until.isoformat(),
                post_count=0,
                avg_views=0,
                max_views=0,
                top_post_text="",
                top_post_author="",
            ))
            continue

        views_list = []
        best_views = 0
        best_text = ""
        best_author = ""
        for tw in tweets:
            v = tw.get("views_count") or tw.get("view_count") or 0
            try:
                v = int(v)
            except Exception:
                v = 0
            views_list.append(v)
            if v > best_views:
                best_views = v
                best_text = (tw.get("full_text") or tw.get("text") or "")[:80]
                best_author = tw.get("user", {}).get("screen_name", "")

        points.append(TrendPoint(
            date_label=label,
            since=since.isoformat(),
            until=until.isoformat(),
            post_count=len(tweets),
            avg_views=int(sum(views_list) / len(views_list)) if views_list else 0,
            max_views=best_views,
            top_post_text=best_text,
            top_post_author=best_author,
        ))

    result.points = points
    result.total_posts_analyzed = sum(p.post_count for p in points)

    # トレンド方向判定（後半 vs 前半の平均インプレ比較・0件期間を除外）
    active_points = [p for p in points if p.post_count > 0]
    if len(active_points) >= 4:
        half = len(active_points) // 2
        first_avg = sum(p.avg_views for p in active_points[:half]) / half
        last_avg = sum(p.avg_views for p in active_points[half:]) / (len(active_points) - half)
        if last_avg > first_avg * 1.2:
            result.trend_direction = "上昇中"
        elif last_avg < first_avg * 0.8:
            result.trend_direction = "下降中"
        else:
            result.trend_direction = "横ばい"

    # ピーク期間
    if points:
        peak = max(points, key=lambda p: p.avg_views)
        result.peak_period = peak.date_label

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
