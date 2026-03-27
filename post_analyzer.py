"""投稿分析 - RTした人・引用RT・コメントから「誰に届いたか」を解析"""
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient
from audience_analyzer import _extract_keywords, _extract_hashtags, _extract_questions, _extract_pain
from account_analyzer import _classify_follower_tier, _classify_bio_genre


def _extract_tweet_id(url_or_id: str) -> Optional[str]:
    """投稿URLまたはIDからtweetIdを抽出"""
    url_or_id = url_or_id.strip()
    # 数字のみならそのままID
    if re.match(r"^\d+$", url_or_id):
        return url_or_id
    # x.com/user/status/ID 形式
    m = re.search(r"/status/(\d+)", url_or_id)
    if m:
        return m.group(1)
    return None


def _analyze_user_list(users: list[dict]) -> dict:
    """ユーザーリストの属性を分析"""
    if not users:
        return {}

    tier_counter = Counter()
    genre_counter = Counter()
    bio_keywords = Counter()
    verified_count = 0

    for user in users:
        fc = user.get("followers_count", 0) or 0
        tier_counter[_classify_follower_tier(fc)] += 1
        bio = user.get("description", "") or ""
        for genre in _classify_bio_genre(bio):
            genre_counter[genre] += 1
        bio_keywords.update(_extract_keywords(bio))
        if user.get("verified") or user.get("is_blue_verified"):
            verified_count += 1

    return {
        "total": len(users),
        "tier": dict(tier_counter.most_common()),
        "genre": dict(genre_counter.most_common(8)),
        "bio_keywords": bio_keywords.most_common(15),
        "verified_count": verified_count,
    }


@dataclass
class PostResult:
    tweet_id: str
    url: str
    text: str
    author_handle: str
    likes: int
    retweets: int
    quotes: int
    replies: int
    views: int

    # RTした人の属性
    retweeter_analysis: dict = field(default_factory=dict)

    # 引用RTの分析
    quote_analysis: dict = field(default_factory=dict)

    # コメントの分析
    comment_analysis: dict = field(default_factory=dict)

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def analyze_post(
    api_key: str,
    tweet_url: str,
    max_retweeters: int = 100,
    max_quotes: int = 50,
    max_comments: int = 50,
    progress_callback: Optional[Callable] = None,
) -> PostResult:
    import time as _time
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # tweet_id を抽出
    tweet_id = _extract_tweet_id(tweet_url)
    if not tweet_id:
        raise ValueError(f"投稿URLからIDを取得できませんでした: {tweet_url}")

    # ① 投稿データ取得
    _cb(0.05, "投稿データを取得中...")
    try:
        tw = client.get_tweet(tweet_id)
    except Exception:
        tw = {}

    user = tw.get("user") or {}
    result = PostResult(
        tweet_id=tweet_id,
        url=f"https://x.com/{user.get('screen_name', '_')}/status/{tweet_id}",
        text=(tw.get("full_text") or tw.get("text") or "")[:200],
        author_handle=user.get("screen_name", ""),
        likes=tw.get("favorite_count", 0) or 0,
        retweets=tw.get("retweet_count", 0) or 0,
        quotes=tw.get("quote_count", 0) or 0,
        replies=tw.get("reply_count", 0) or 0,
        views=tw.get("views_count", 0) or 0,
    )

    # ② RTした人の属性分析
    _cb(0.2, f"RTしたユーザーを取得中（最大{max_retweeters}件）...")
    try:
        retweeters = client.get_all_users(f"/twitter/tweets/{tweet_id}/retweeted_by", max_results=max_retweeters)
        result.retweeter_analysis = _analyze_user_list(retweeters)
    except Exception:
        result.retweeter_analysis = {}

    # ③ 引用RTの分析
    _cb(0.5, f"引用RTを取得中（最大{max_quotes}件）...")
    try:
        quotes_data = client.get_all_tweets_from_path(f"/twitter/tweets/{tweet_id}/quotes", max_results=max_quotes)
        kw_counter = Counter()
        ht_counter = Counter()
        pain_counter = Counter()
        question_list = []
        raw_quotes = []

        for q in quotes_data:
            text = q.get("full_text", "") or q.get("text", "")
            if text:
                raw_quotes.append(text[:120])
                kw_counter.update(_extract_keywords(text))
                ht_counter.update(_extract_hashtags(text))
                pain_counter.update(_extract_pain(text))
                question_list.extend(_extract_questions(text))

        result.quote_analysis = {
            "count": len(quotes_data),
            "top_keywords": kw_counter.most_common(15),
            "top_hashtags": ht_counter.most_common(8),
            "pain_points": [(k, v) for k, v in pain_counter.most_common(10) if v > 0],
            "questions": list(dict.fromkeys(q.strip() for q in question_list if len(q) > 5))[:10],
            "samples": raw_quotes[:15],
        }
    except Exception:
        result.quote_analysis = {}

    # ④ コメントの分析
    _cb(0.8, f"コメントを取得中（最大{max_comments}件）...")
    try:
        comments_data = client.get_tweet_comments(tweet_id)
        comment_tweets = (comments_data.get("tweets") or comments_data.get("comments") or [])[:max_comments]

        ckw_counter = Counter()
        cpain_counter = Counter()
        cquestion_list = []
        raw_comments = []

        for c in comment_tweets:
            text = c.get("full_text", "") or c.get("text", "")
            if text:
                raw_comments.append(text[:120])
                ckw_counter.update(_extract_keywords(text))
                cpain_counter.update(_extract_pain(text))
                cquestion_list.extend(_extract_questions(text))

        result.comment_analysis = {
            "count": len(comment_tweets),
            "top_keywords": ckw_counter.most_common(15),
            "pain_points": [(k, v) for k, v in cpain_counter.most_common(10) if v > 0],
            "questions": list(dict.fromkeys(q.strip() for q in cquestion_list if len(q) > 5))[:10],
            "samples": raw_comments[:15],
        }
    except Exception:
        result.comment_analysis = {}

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
