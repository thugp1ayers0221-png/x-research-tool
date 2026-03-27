"""フレーズ深堀り検索 - Claude APIで関連クエリを生成 → 高インプレ投稿を収集"""
import os
import time
import json
from dataclasses import dataclass, field
from typing import Optional, Callable

import anthropic

from api import SocialDataClient


@dataclass
class DeepSearchResult:
    seed_phrase: str = ""
    generated_queries: list[str] = field(default_factory=list)
    posts: list[dict] = field(default_factory=list)          # views降順
    total_searched: int = 0
    llm_cost_jpy: float = 0.0
    api_cost_jpy: float = 0.0


def _generate_queries(seed_phrase: str, context_keywords: list[str], anthropic_key: str) -> tuple[list[str], float]:
    """
    Claude Haikusを使って関連クエリを生成する。
    context_keywords はペルソナ調査で出た頻出キーワード（文脈補強用）。
    戻り値: (クエリリスト, 推定コスト円)
    """
    client = anthropic.Anthropic(api_key=anthropic_key)

    context_str = "、".join(context_keywords[:20]) if context_keywords else "なし"

    prompt = f"""あなたはX（旧Twitter）のコンテンツリサーチャーです。

ターゲット層の調査で判明したフレーズ: 「{seed_phrase}」
同じターゲット層の頻出キーワード: {context_str}

このフレーズに関連する話題で、X上で実際に議論・バズりやすいテーマを考えてください。
完全一致ではなく、同じ感情や文脈を持つ隣接トピックも含めてください。

X検索クエリを8個生成してください。
- 各クエリは日本語で、2〜4語程度の自然な組み合わせ
- 同じ表現の言い換えではなく、角度の違う切り口にすること
- ハッシュタグ(#)や特殊演算子は使わないこと

以下のJSON形式のみで返答してください（説明文不要）:
{{"queries": ["クエリ1", "クエリ2", "クエリ3", "クエリ4", "クエリ5", "クエリ6", "クエリ7", "クエリ8"]}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # JSON部分だけ抽出
    start = raw.find('{')
    end = raw.rfind('}') + 1
    data = json.loads(raw[start:end])
    queries = data.get("queries", [])

    # コスト計算
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost_usd = (input_tokens * 0.80 + output_tokens * 4.0) / 1_000_000
    cost_jpy = cost_usd * 150

    return queries, cost_jpy


def deep_search(
    api_key: str,
    anthropic_key: str,
    seed_phrase: str,
    context_keywords: list[str] = None,
    min_views: int = 30000,
    max_results: int = 30,
    progress_callback: Optional[Callable] = None,
) -> DeepSearchResult:
    """
    フレーズ深堀り検索のメイン関数。

    Parameters
    ----------
    seed_phrase      : 起点フレーズ（ペルソナ調査の頻出フレーズ等）
    context_keywords : 同じ調査で得た頻出キーワード（クエリ生成の文脈補強）
    min_views        : インプレッション最低値（デフォルト3万）
    max_results      : 最大返却件数
    """
    result = DeepSearchResult(seed_phrase=seed_phrase)
    context_keywords = context_keywords or []

    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    # ─── STEP 1: Claude で関連クエリ生成 ─────────────────────
    _progress("🤖 関連クエリを生成中（Claude Haiku）...")
    try:
        queries, llm_cost = _generate_queries(seed_phrase, context_keywords, anthropic_key)
        result.generated_queries = queries
        result.llm_cost_jpy = llm_cost
        _progress(f"  ✓ {len(queries)}件のクエリを生成（LLMコスト: ¥{llm_cost:.2f}）")
    except Exception as e:
        result.generated_queries = [seed_phrase]  # フォールバック
        _progress(f"  ⚠️ Claude API エラー、元フレーズのみで検索: {e}")

    # ─── STEP 2: 各クエリで X 検索 ───────────────────────────
    _progress("🔍 X を検索中...")
    client = SocialDataClient(api_key)
    seen_ids = set()
    all_posts = []

    for i, query in enumerate(result.generated_queries):
        _progress(f"  [{i+1}/{len(result.generated_queries)}] 「{query}」を検索...")
        try:
            # lang:ja で日本語限定、最新順で2ページ取得
            full_query = f"{query} lang:ja -filter:replies"
            data = client.search_tweets(full_query)
            tweets = data.get("tweets", [])

            # 2ページ目も取得
            cursor = data.get("next_cursor")
            if cursor:
                data2 = client.search_tweets(full_query, cursor)
                tweets += data2.get("tweets", [])

            result.total_searched += len(tweets)

            for t in tweets:
                tid = t.get("id_str", "")
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)

                views = t.get("views_count") or 0
                if views >= min_views:
                    all_posts.append({
                        "id": tid,
                        "text": t.get("full_text", t.get("text", ""))[:280],
                        "views": views,
                        "likes": t.get("favorite_count", 0),
                        "retweets": t.get("retweet_count", 0),
                        "bookmarks": t.get("bookmark_count", 0),
                        "author": t.get("user", {}).get("screen_name", ""),
                        "followers": t.get("user", {}).get("followers_count", 0),
                        "matched_query": query,
                        "url": f"https://x.com/{t.get('user', {}).get('screen_name', '')}/status/{tid}",
                    })
            time.sleep(0.2)
        except Exception as e:
            _progress(f"  ⚠️ 検索エラー ({query}): {e}")

    # ─── STEP 3: 整形 ────────────────────────────────────────
    # views降順でソート
    all_posts.sort(key=lambda x: x["views"], reverse=True)
    result.posts = all_posts[:max_results]
    result.api_cost_jpy = client.estimated_cost_jpy

    _progress(
        f"✅ 完了！{len(result.posts)}件ヒット "
        f"（検索{result.total_searched}件中 / 合計コスト: ¥{result.llm_cost_jpy + result.api_cost_jpy:.1f}）"
    )
    return result
