"""X記事リサーチのコアロジック"""
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from api import SocialDataClient

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# 日本語文字 (ひらがな・カタカナ・CJK漢字)
_JP_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]")


@dataclass
class Article:
    tweet_id: str
    url: str
    title: str
    text: str
    preview_text: str
    thumbnail_url: str
    author_name: str
    author_handle: str
    author_followers: int
    posted_at: str
    likes: int
    retweets: int
    replies: int
    quotes: int
    bookmarks: int
    views: int
    is_japanese: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _is_japanese_profile(user: dict) -> bool:
    name = user.get("name", "") or ""
    description = user.get("description", "") or ""
    return bool(_JP_RE.search(name + description))


def _draft_js_to_text(content) -> str:
    """Draft.js ブロック → プレーンテキスト変換"""
    if not content:
        return ""
    if isinstance(content, str):
        return content

    lines = []
    blocks = content.get("blocks", []) if isinstance(content, dict) else []
    for block in blocks:
        text = block.get("text", "")
        if text:
            lines.append(text)
    return "\n".join(lines)


def _parse_article(raw: dict, tweet_id: str, tweet_url: str) -> Article:
    user = raw.get("user") or raw.get("author") or {}
    metrics = raw.get("public_metrics") or {}
    content = raw.get("content") or raw.get("body") or {}

    body_text = _draft_js_to_text(content)

    return Article(
        tweet_id=tweet_id,
        url=tweet_url,
        title=raw.get("title", ""),
        text=body_text,
        preview_text=raw.get("preview_text", ""),
        thumbnail_url=raw.get("thumbnail_image_original", "") or raw.get("thumbnail_url", ""),
        author_name=user.get("name", ""),
        author_handle=user.get("screen_name", "") or user.get("username", ""),
        author_followers=user.get("followers_count", 0) or 0,
        posted_at=raw.get("created_at", ""),
        likes=metrics.get("favorite_count", 0) or raw.get("favorite_count", 0) or 0,
        retweets=metrics.get("retweet_count", 0) or raw.get("retweet_count", 0) or 0,
        replies=metrics.get("reply_count", 0) or raw.get("reply_count", 0) or 0,
        quotes=metrics.get("quote_count", 0) or raw.get("quote_count", 0) or 0,
        bookmarks=metrics.get("bookmark_count", 0) or raw.get("bookmark_count", 0) or 0,
        views=metrics.get("impression_count", 0) or raw.get("views_count", 0) or 0,
    )


def _cache_path(tweet_id: str) -> Path:
    return CACHE_DIR / f"{tweet_id}.json"


def _load_cache(tweet_id: str) -> Optional[dict]:
    p = _cache_path(tweet_id)
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return None


def _save_cache(tweet_id: str, data: dict):
    _cache_path(tweet_id).write_text(json.dumps(data, ensure_ascii=False), "utf-8")


@dataclass
class ResearchResult:
    articles: list[Article] = field(default_factory=list)
    total_hits: int = 0
    japanese_count: int = 0
    other_count: int = 0
    api_calls: int = 0
    cache_hits: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def build_query(
    min_faves: int = 1000,
    min_retweets: int = 0,
    since_date: Optional[str] = None,
    until_date: Optional[str] = None,
    keyword: Optional[str] = None,
) -> str:
    parts = ["url:x.com/i/article", f"min_faves:{min_faves}", "-filter:replies"]
    if min_retweets > 0:
        parts.append(f"min_retweets:{min_retweets}")
    if since_date:
        parts.append(f"since:{since_date}")
    if until_date:
        parts.append(f"until:{until_date}")
    if keyword:
        parts.append(keyword)
    return " ".join(parts)


def research(
    api_key: str,
    min_faves: int = 1000,
    min_retweets: int = 0,
    days: int = 30,
    keyword: Optional[str] = None,
    max_results: int = 500,
    japanese_only: bool = True,
    progress_callback=None,
) -> ResearchResult:
    client = SocialDataClient(api_key)
    result = ResearchResult()
    start = time.time()

    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.utcnow().strftime("%Y-%m-%d")
    query = build_query(min_faves, min_retweets, since, until, keyword)

    # ① 検索
    if progress_callback:
        progress_callback("search", 0, "検索中...")

    raw_tweets = client.search_all_tweets(
        query,
        max_results=max_results,
        progress_callback=lambda n: progress_callback("search", n, f"検索中... {n}件") if progress_callback else None,
    )
    result.total_hits = len(raw_tweets)

    # ② 日本語フィルタ (コスト0)
    ja_tweets = []
    other_tweets = []
    for tw in raw_tweets:
        user = tw.get("user") or tw.get("author") or {}
        if _is_japanese_profile(user):
            ja_tweets.append(tw)
        else:
            other_tweets.append(tw)

    result.japanese_count = len(ja_tweets)
    result.other_count = len(other_tweets)

    target_tweets = ja_tweets if japanese_only else raw_tweets

    # ③ 記事詳細取得
    seen: dict[str, Article] = {}  # tweet_id → Article (重複除去)

    for i, tw in enumerate(target_tweets):
        tweet_id = str(tw.get("id") or tw.get("id_str", ""))
        tweet_url = f"https://x.com/{(tw.get('user') or {}).get('screen_name', '')}/status/{tweet_id}"

        if progress_callback:
            progress_callback("detail", i + 1, f"記事取得中... {i+1}/{len(target_tweets)}")

        cached = _load_cache(tweet_id)
        if cached:
            result.cache_hits += 1
            article = Article(**cached)
        else:
            try:
                raw_article = client.get_article(tweet_id)
                # ユーザー情報をマージ
                if "user" not in raw_article and "user" in tw:
                    raw_article["user"] = tw["user"]
                article = _parse_article(raw_article, tweet_id, tweet_url)
                article.is_japanese = True
                _save_cache(tweet_id, article.to_dict())
            except Exception:
                # 記事取得失敗 → ツイートのメタデータだけで組み立て
                user = tw.get("user") or {}
                metrics = tw.get("public_metrics") or {}
                article = Article(
                    tweet_id=tweet_id,
                    url=tweet_url,
                    title=tw.get("full_text", "")[:80],
                    text="",
                    preview_text="",
                    thumbnail_url="",
                    author_name=user.get("name", ""),
                    author_handle=user.get("screen_name", ""),
                    author_followers=user.get("followers_count", 0) or 0,
                    posted_at=tw.get("tweet_created_at", ""),
                    likes=tw.get("favorite_count", 0) or 0,
                    retweets=tw.get("retweet_count", 0) or 0,
                    replies=metrics.get("reply_count", 0) or 0,
                    quotes=metrics.get("quote_count", 0) or 0,
                    bookmarks=tw.get("bookmark_count", 0) or 0,
                    views=tw.get("views_count", 0) or 0,
                    is_japanese=True,
                )

        # 重複除去 (title+著者でユニーク化、いいね最大を残す)
        key = f"{article.title}|{article.author_handle}" if article.title else tweet_id
        if key not in seen or article.likes > seen[key].likes:
            seen[key] = article

        time.sleep(0.1)

    # ④ ソート (いいね降順)
    result.articles = sorted(seen.values(), key=lambda a: a.likes, reverse=True)
    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = time.time() - start

    return result
