"""記事リサーチ - X Article 検索・本文取得"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Callable

from api import SocialDataClient


@dataclass
class ArticleResult:
    articles: list[dict] = field(default_factory=list)
    searched_count: int = 0
    articles_found: int = 0
    api_calls: int = 0
    cost_jpy: float = 0.0


def _blocks_to_text(blocks: list[dict]) -> str:
    """content_state.blocks → 読みやすいテキスト"""
    lines = []
    ordered_idx = 0
    for block in blocks:
        text = block.get("text", "")
        btype = block.get("type", "unstyled")
        if btype == "header-one":
            lines.append(f"## {text}")
            ordered_idx = 0
        elif btype == "header-two":
            lines.append(f"### {text}")
            ordered_idx = 0
        elif btype == "header-three":
            lines.append(f"#### {text}")
            ordered_idx = 0
        elif btype == "unordered-list-item":
            lines.append(f"- {text}")
        elif btype == "ordered-list-item":
            ordered_idx += 1
            lines.append(f"{ordered_idx}. {text}")
        elif btype == "blockquote":
            lines.append(f"> {text}")
            ordered_idx = 0
        elif btype == "atomic":
            ordered_idx = 0
        else:
            if text:
                lines.append(text)
            ordered_idx = 0
    return "\n\n".join(lines)


def _fetch_article_detail(client: SocialDataClient, tweet_id: str) -> Optional[dict]:
    """記事本文・メトリクスを取得"""
    try:
        data = client.get_article(tweet_id)
        article = data.get("article") or {}
        blocks = article.get("content_state", {}).get("blocks", [])
        text = _blocks_to_text(blocks)
        title = article.get("title", "")
        if not title and not text:
            return None
        return {
            "title": title,
            "preview_text": article.get("preview_text", ""),
            "cover_url": article.get("cover_url", ""),
            "text": text,
            "metrics": {
                "likes": data.get("favorite_count", 0),
                "retweets": data.get("retweet_count", 0),
                "replies": data.get("reply_count", 0),
                "quotes": data.get("quote_count", 0),
                "bookmarks": data.get("bookmark_count", 0),
                "views": data.get("views_count", 0),
            },
        }
    except Exception:
        return None


def analyze_articles(
    api_key: str,
    keyword: str = "",
    min_likes: int = 500,
    days: int = 30,
    max_articles: int = 20,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> ArticleResult:
    client = SocialDataClient(api_key)

    def prog(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    # 検索クエリ構築
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.now().strftime("%Y-%m-%d")

    query = f"url:x.com/i/article min_faves:{min_likes} -filter:replies since:{since} until:{until}"
    if keyword.strip():
        query = f"{keyword.strip()} {query}"

    prog(0.05, "記事を検索中...")

    # 検索（max_id ページネーション）
    seen_ids: set[str] = set()
    candidate_tweets: list[dict] = []
    cursor = None
    batch = 0

    while len(candidate_tweets) < max_articles * 3 and batch < 10:
        batch += 1
        try:
            data = client.search_tweets(query, next_cursor=cursor)
        except Exception as e:
            break

        tweets = data.get("tweets", [])
        if not tweets:
            break

        for t in tweets:
            tid = t.get("id_str", "")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                candidate_tweets.append(t)

        cursor = data.get("next_cursor")
        if not cursor:
            break

        if len(candidate_tweets) >= max_articles * 3:
            break
        time.sleep(0.3)

    searched_count = len(candidate_tweets)
    prog(0.3, f"{searched_count}件の候補を発見。記事本文を取得中...")

    # 記事詳細を取得
    articles = []
    total = min(len(candidate_tweets), max_articles * 2)

    for i, tweet in enumerate(candidate_tweets[:total]):
        tweet_id = tweet.get("id_str", "")
        if not tweet_id:
            continue

        prog(0.3 + (i / total) * 0.65, f"記事詳細を取得中... ({i+1}/{total})")

        detail = _fetch_article_detail(client, tweet_id)
        time.sleep(0.35)

        if not detail:
            continue

        articles.append({
            "tweet_id": tweet_id,
            "tweet_url": f"https://x.com/{tweet.get('user', {}).get('screen_name', 'i')}/status/{tweet_id}",
            "title": detail["title"],
            "preview_text": detail["preview_text"],
            "cover_url": detail["cover_url"],
            "text": detail["text"],
            "published_at": tweet.get("tweet_created_at", ""),
            "metrics": detail["metrics"],
            "author": {
                "screen_name": tweet.get("user", {}).get("screen_name", ""),
                "name": tweet.get("user", {}).get("name", ""),
                "followers_count": tweet.get("user", {}).get("followers_count", 0),
                "description": tweet.get("user", {}).get("description", ""),
            },
        })

        if len(articles) >= max_articles:
            break

    # いいね降順ソート
    articles.sort(key=lambda a: a["metrics"]["likes"], reverse=True)

    prog(1.0, "完了")

    return ArticleResult(
        articles=articles,
        searched_count=searched_count,
        articles_found=len(articles),
        api_calls=client._call_count,
        cost_jpy=client.estimated_cost_jpy,
    )
