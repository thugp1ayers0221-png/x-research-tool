"""ネタ発掘 - 競合/参考アカウントのいいね傾向からコンテンツヒントを抽出"""
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient
from audience_analyzer import _extract_keywords, _extract_hashtags, JP_STOP


TOPIC_CLUSTERS = {
    "思考法/マインドセット": ["思考", "メンタル", "マインド", "考え方", "習慣", "行動"],
    "ビジネス/起業": ["ビジネス", "起業", "経営", "事業", "マネタイズ", "収益"],
    "マーケティング/SNS": ["マーケ", "SNS", "集客", "ブランド", "発信", "フォロワー"],
    "生産性/仕事術": ["生産性", "効率", "仕事", "タスク", "時間", "習慣"],
    "お金/投資": ["投資", "資産", "収入", "副業", "節約", "お金"],
    "AI/テクノロジー": ["AI", "ChatGPT", "Claude", "自動化", "テック", "ツール"],
    "自己成長/学習": ["勉強", "学習", "スキル", "成長", "読書", "インプット"],
    "人間関係/コミュニケーション": ["コミュニケーション", "人間関係", "影響力", "説得", "交渉"],
}


def _cluster_keywords(keywords: list[tuple[str, int]]) -> dict[str, int]:
    """キーワードをトピッククラスターに分類"""
    cluster_scores: Counter = Counter()
    for kw, count in keywords:
        for cluster, words in TOPIC_CLUSTERS.items():
            if any(w in kw or kw in w for w in words):
                cluster_scores[cluster] += count
    return dict(cluster_scores.most_common())


def _generate_neta(
    handle: str,
    top_keywords: list[tuple[str, int]],
    top_hashtags: list[tuple[str, int]],
    cluster_scores: dict,
) -> list[str]:
    top_kws = [k for k, _ in top_keywords[:10]]
    top_clusters = list(cluster_scores.keys())[:3]
    neta = []

    # クラスターベースのネタ
    templates = [
        "【保存版】{kw}を使って{cluster}を加速する方法",
        "{kw}と{kw2}の意外な共通点",
        "{cluster}で成果を出している人がやっていること",
        "なぜ{kw}ができる人は{cluster}でも結果を出せるのか",
        "{kw}を習慣化したら人生が変わった話（具体的な数字で）",
        "【永久保存版】{cluster}の本質を1ポストで説明する",
        "{kw}の落とし穴：9割の人が知らない注意点",
    ]

    kw = top_kws[0] if top_kws else "ビジネス"
    kw2 = top_kws[1] if len(top_kws) > 1 else "思考"
    cluster = top_clusters[0] if top_clusters else "スキルアップ"

    for tmpl in templates:
        neta.append(tmpl.format(kw=kw, kw2=kw2, cluster=cluster, handle=handle))

    # 上位ハッシュタグからネタ
    for ht, _ in top_hashtags[:3]:
        neta.append(f"#{ht} について知っておくべき3つのこと")

    return neta[:12]


@dataclass
class NetaResult:
    handle: str
    likes_count: int

    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_hashtags: list[tuple[str, int]] = field(default_factory=list)
    topic_clusters: dict = field(default_factory=dict)
    neta_suggestions: list[str] = field(default_factory=list)
    sample_liked_posts: list[dict] = field(default_factory=list)

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def analyze_neta(
    api_key: str,
    handle: str,
    max_likes: int = 100,
    progress_callback: Optional[Callable] = None,
) -> NetaResult:
    import time as _time
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    _cb(0.05, f"@{handle} のプロフィールを取得中...")
    profile = client.get_user_profile(handle)
    uid = str(profile.get("id_str") or profile.get("id", ""))

    result = NetaResult(handle=handle, likes_count=0)

    # ① いいねした投稿を取得
    _cb(0.2, f"いいきした投稿を取得中（最大{max_likes}件）...")
    liked_tweets = client.get_all_tweets_from_path(f"/twitter/user/{uid}/likes", max_results=max_likes)

    # いいねが取れない場合はタイムラインにフォールバック
    if not liked_tweets:
        _cb(0.3, "いいねが非公開のため、タイムライン投稿で代替分析中...")
        liked_tweets = client.get_all_tweets_from_path(f"/twitter/user/{uid}/tweets", max_results=max_likes)

    result.likes_count = len(liked_tweets)

    # ② テキスト分析
    _cb(0.6, "コンテンツ傾向を分析中...")
    kw_counter: Counter = Counter()
    ht_counter: Counter = Counter()

    for tw in liked_tweets:
        text = tw.get("full_text", "") or tw.get("text", "")
        kw_counter.update(_extract_keywords(text))
        ht_counter.update(_extract_hashtags(text))

    # ストップワード除去
    for sw in list(kw_counter.keys()):
        if sw in JP_STOP or len(sw) < 2:
            del kw_counter[sw]

    result.top_keywords = kw_counter.most_common(25)
    result.top_hashtags = ht_counter.most_common(15)

    # ③ トピッククラスター分類
    result.topic_clusters = _cluster_keywords(result.top_keywords)

    # ④ ネタ候補生成
    _cb(0.85, "ネタ候補を生成中...")
    result.neta_suggestions = _generate_neta(
        handle, result.top_keywords, result.top_hashtags, result.topic_clusters
    )

    # ⑤ サンプル投稿（いいね数上位）
    top_sample = sorted(liked_tweets, key=lambda t: t.get("favorite_count", 0) or 0, reverse=True)[:8]
    result.sample_liked_posts = [
        {
            "text": (t.get("full_text", "") or t.get("text", ""))[:100],
            "likes": t.get("favorite_count", 0) or 0,
            "author": (t.get("user") or {}).get("screen_name", ""),
            "url": f"https://x.com/{(t.get('user') or {}).get('screen_name', '_')}/status/{t.get('id_str', '')}",
        }
        for t in top_sample
    ]

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
