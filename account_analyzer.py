"""アカウント丸裸分析 - フォロワー属性・投稿傾向・類似アカウント"""
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient
from audience_analyzer import _extract_keywords, _extract_hashtags, JP_STOP


# ─── フォロワー属性分析 ────────────────────────────────────────

FOLLOWER_TIER = [
    (0, 100, "一般"),
    (100, 1000, "マイクロ"),
    (1000, 10000, "スモール"),
    (10000, 100000, "ミドル"),
    (100000, float("inf"), "インフルエンサー"),
]

BIO_GENRE_PATTERNS = {
    "ビジネス/起業": ["起業", "経営", "CEO", "代表", "社長", "ビジネス", "事業"],
    "マーケティング": ["マーケ", "マーケティング", "SNS運用", "ブランディング", "集客"],
    "エンジニア/IT": ["エンジニア", "開発", "プログラム", "AI", "IT", "テック", "developer"],
    "フリーランス": ["フリーランス", "フリーランサー", "副業", "独立", "個人事業"],
    "投資/資産運用": ["投資", "株", "資産", "FX", "仮想通貨", "NISA"],
    "クリエイター": ["デザイン", "クリエイター", "動画", "YouTube", "コンテンツ"],
    "サラリーマン": ["会社員", "サラリーマン", "会社勤め", "勤務"],
    "コンサル/士業": ["コンサル", "コーチ", "弁護士", "税理士", "コンサルタント"],
}


def _classify_follower_tier(count: int) -> str:
    for lo, hi, label in FOLLOWER_TIER:
        if lo <= count < hi:
            return label
    return "不明"


def _classify_bio_genre(bio: str) -> list[str]:
    genres = []
    for genre, keywords in BIO_GENRE_PATTERNS.items():
        if any(kw in bio for kw in keywords):
            genres.append(genre)
    return genres or ["その他"]


def _analyze_followers(followers: list[dict]) -> dict:
    tier_counter = Counter()
    genre_counter = Counter()
    bio_keywords = Counter()
    verified_count = 0

    for user in followers:
        fc = user.get("followers_count", 0) or 0
        tier_counter[_classify_follower_tier(fc)] += 1

        bio = user.get("description", "") or ""
        for genre in _classify_bio_genre(bio):
            genre_counter[genre] += 1
        bio_keywords.update(_extract_keywords(bio))

        if user.get("verified") or user.get("is_blue_verified"):
            verified_count += 1

    # bio_keywordsからストップワードを除く
    for sw in list(bio_keywords.keys()):
        if sw in JP_STOP or len(sw) < 2:
            del bio_keywords[sw]

    return {
        "tier": dict(tier_counter.most_common()),
        "genre": dict(genre_counter.most_common(10)),
        "bio_keywords": bio_keywords.most_common(20),
        "verified_count": verified_count,
        "total": len(followers),
    }


# ─── 投稿傾向分析 ──────────────────────────────────────────────

def _analyze_user_tweets(tweets: list[dict]) -> dict:
    if not tweets:
        return {}
    # tweet_idで重複除去
    seen_ids = set()
    unique = []
    for t in tweets:
        tid = str(t.get("id_str") or t.get("id", ""))
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique.append(t)
    tweets = unique

    total = len(tweets)
    total_likes = sum(t.get("favorite_count", 0) or 0 for t in tweets)
    total_rts = sum(t.get("retweet_count", 0) or 0 for t in tweets)
    total_views = sum(t.get("views_count", 0) or 0 for t in tweets)

    # バズ率（いいね100以上の投稿の割合）
    buzz_threshold = 100
    buzz_posts = [t for t in tweets if (t.get("favorite_count", 0) or 0) >= buzz_threshold]

    # TOP投稿（インプレッション順）
    top_posts = sorted(tweets, key=lambda t: t.get("views_count", 0) or 0, reverse=True)[:5]

    # ハッシュタグ・キーワード傾向
    kw_counter = Counter()
    ht_counter = Counter()
    for tw in tweets:
        text = tw.get("full_text", "") or tw.get("text", "")
        kw_counter.update(_extract_keywords(text))
        ht_counter.update(_extract_hashtags(text))

    # 投稿時間帯（JST=UTC+9）
    hour_counter = Counter()
    for tw in tweets:
        created = tw.get("tweet_created_at", "")
        if created:
            try:
                # "Fri Mar 21 12:34:56 +0000 2025" 形式
                import datetime
                dt = datetime.datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
                jst_hour = (dt.hour + 9) % 24
                hour_counter[jst_hour] += 1
            except Exception:
                pass

    return {
        "total_posts": total,
        "avg_likes": total_likes / total if total else 0,
        "avg_rts": total_rts / total if total else 0,
        "avg_views": total_views / total if total else 0,
        "buzz_rate": len(buzz_posts) / total if total else 0,
        "top_posts": [
            {
                "text": (t.get("full_text", "") or t.get("text", ""))[:80],
                "likes": t.get("favorite_count", 0) or 0,
                "rts": t.get("retweet_count", 0) or 0,
                "views": t.get("views_count", 0) or 0,
                "url": f"https://x.com/{(t.get('user') or {}).get('screen_name', '_')}/status/{t.get('id_str', '')}",
            }
            for t in top_posts
        ],
        "top_keywords": kw_counter.most_common(20),
        "top_hashtags": ht_counter.most_common(10),
        "posting_hours": hour_counter.most_common(5),
    }


# ─── データクラス ──────────────────────────────────────────────

@dataclass
class AccountResult:
    handle: str
    name: str
    bio: str
    followers_count: int
    following_count: int
    tweet_count: int
    verified: bool

    # フォロワー属性
    follower_analysis: dict = field(default_factory=dict)

    # 投稿傾向
    tweet_analysis: dict = field(default_factory=dict)

    # 類似アカウント
    similar_accounts: list[dict] = field(default_factory=list)

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


# ─── メイン分析関数 ────────────────────────────────────────────

def analyze_account(
    api_key: str,
    handle: str,
    max_followers: int = 200,
    max_tweets: int = 60,
    progress_callback: Optional[Callable] = None,
) -> AccountResult:
    import time as _time
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # ① プロフィール取得
    _cb(0.05, f"@{handle} のプロフィールを取得中...")
    profile = client.get_user_profile(handle)
    uid = str(profile.get("id_str") or profile.get("id", ""))

    result = AccountResult(
        handle=handle,
        name=profile.get("name", handle),
        bio=profile.get("description", ""),
        followers_count=profile.get("followers_count", 0) or 0,
        following_count=profile.get("friends_count", 0) or 0,
        tweet_count=profile.get("statuses_count", 0) or 0,
        verified=bool(profile.get("verified") or profile.get("is_blue_verified")),
    )

    # ② 最近の投稿を取得して傾向分析
    _cb(0.2, "最近の投稿を取得中...")
    tweets = client.get_all_tweets_from_path(f"/twitter/user/{uid}/tweets", max_results=max_tweets)
    result.tweet_analysis = _analyze_user_tweets(tweets)

    # ③ フォロワーサンプルを取得して属性分析
    _cb(0.4, f"フォロワーを取得中（最大{max_followers}件）...")
    followers = client.get_all_users(f"/twitter/user/{uid}/followers", max_results=max_followers)
    result.follower_analysis = _analyze_followers(followers)

    # ④ 類似アカウント取得
    _cb(0.7, "類似アカウントを探索中...")
    try:
        similar_data = client.get_user_similar(uid)
        result.similar_accounts = [
            {
                "name": u.get("name", ""),
                "handle": u.get("screen_name", ""),
                "uid": str(u.get("id_str") or u.get("id") or ""),
                "followers": u.get("followers_count", 0) or 0,
                "bio": (u.get("description", "") or "")[:80],
                "verified": bool(u.get("verified") or u.get("is_blue_verified")),
                "theme_overlap": [],
            }
            for u in (similar_data.get("users") or [])[:10]
        ]
    except Exception:
        result.similar_accounts = []

    # 類似アカウントの投稿テーマ照合（各1コール）
    target_kws = set(w for w, _ in (result.tweet_analysis.get("top_keywords") or [])[:30])
    if target_kws and result.similar_accounts:
        _cb(0.75, "類似アカウントの投稿テーマを照合中...")
        for sim in result.similar_accounts:
            sim_uid = sim.get("uid")
            if not sim_uid:
                continue
            try:
                sim_data = client.get_user_tweets(sim_uid)
                sim_tweets = sim_data.get("tweets") or []
                sim_counter: Counter = Counter()
                for tw in sim_tweets[:20]:
                    text = tw.get("full_text", "") or tw.get("text", "")
                    sim_counter.update(_extract_keywords(text))
                sim_top = set(w for w, _ in sim_counter.most_common(30))
                sim["theme_overlap"] = sorted(target_kws & sim_top)[:5]
            except Exception:
                pass

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
