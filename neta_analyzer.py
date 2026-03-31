"""ネタ発掘 - 競合/参考アカウントの投稿傾向からコンテンツヒントを抽出"""
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient
from audience_analyzer import _extract_keywords, _extract_hashtags, JP_STOP


TOPIC_CLUSTERS = {
    "思考法/マインドセット": ["思考", "メンタル", "マインド", "考え方", "習慣", "行動", "意識", "本質"],
    "ビジネス/経営": ["ビジネス", "起業", "経営", "事業", "収益", "会社", "社内", "組織", "経営者", "社長", "企業"],
    "財務/数字": ["売上", "赤字", "黒字", "利益", "コスト", "資金", "財務", "決算", "損益", "収支", "原価"],
    "外食/小売/流通": ["吉野家", "すき家", "マクドナルド", "コンビニ", "飲食", "外食", "小売", "店舗", "チェーン", "流通", "tower"],
    "マーケティング/集客": ["マーケ", "SNS", "集客", "ブランド", "発信", "フォロワー", "広告", "PR", "認知"],
    "生産性/仕事術": ["生産性", "効率", "仕事", "タスク", "時間", "管理", "改善"],
    "お金/投資": ["投資", "資産", "収入", "副業", "節約", "お金", "株", "不動産"],
    "AI/テクノロジー": ["AI", "ChatGPT", "Claude", "自動化", "テック", "ツール", "DX", "システム"],
    "自己成長/学習": ["勉強", "学習", "スキル", "成長", "読書", "インプット", "知識"],
    "社会/業界": ["社会", "政治", "経済", "ニュース", "問題", "課題", "業界", "市場", "郊外", "地域"],
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
    """
    投稿傾向の分析から、バズ構造（型）×キーワードでネタ候補を生成する。
    特定アカウント専用ではなく、分析データに基づく汎用高精度版。
    """
    top_kws = [k for k, _ in top_keywords[:20]]
    top_clusters = list(cluster_scores.keys())[:3]
    neta = []

    if len(top_kws) < 2:
        return []

    kw1 = top_kws[0]
    kw2 = top_kws[1]
    kw3 = top_kws[2] if len(top_kws) > 2 else top_kws[0]
    kw4 = top_kws[3] if len(top_kws) > 3 else top_kws[1]
    cluster1 = top_clusters[0] if top_clusters else kw1

    # ── 型1: 経験・告白型（リアルさでバズる）
    neta += [
        f"「{kw1}」について正直に言う。多くの人が誤解していること",
        f"{kw1}を本気でやって気づいた「やめてよかったこと」",
        f"同じ{kw1}をやって、伸びた人と伸びなかった人の決定的な違い",
    ]

    # ── 型2: 逆張り・反論型（議論を呼ぶ）
    neta += [
        f"「{kw1}は{kw2}が大事」は半分ウソ。本当に効くのは別のこと",
        f"みんなが信じてる{kw1}の「常識」、実は逆効果だった",
        f"{kw2}より先に{kw1}を理解した方がいい理由",
    ]

    # ── 型3: 対比・格差型（共感と危機感）
    neta += [
        f"{kw1}で成果が出る人と出ない人。違いは「{kw2}」だけ",
        f"{kw3}に気づいた人と気づかなかった人で、{kw1}の結果が変わる",
    ]

    # ── 型4: リスト・保存型（拡散される）
    neta += [
        f"今すぐやめるべき{kw1}の習慣TOP3",
        f"{kw1}と{kw2}を同時に伸ばせる人が持っている3つの視点",
    ]

    # ── 型5: 数字・実証型（信頼性）
    neta += [
        f"{kw1}を90日続けた結果を正直に公開する",
        f"「{kw3}」に本気で取り組んで変わった{kw1}の話",
    ]

    # ── クラスター追加
    if top_clusters:
        neta += [
            f"{cluster1}の世界で今起きていることを{kw1}の観点から語る",
            f"大半の人が勘違いしている{cluster1}の本質",
        ]

    # ハッシュタグがあれば文脈に追加
    for ht, _ in top_hashtags[:2]:
        neta.append(f"#{ht} が注目されている理由と、{kw1}との関係性")

    return neta[:15]


@dataclass
class NetaResult:
    handle: str
    post_count: int

    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_hashtags: list[tuple[str, int]] = field(default_factory=list)
    topic_clusters: dict = field(default_factory=dict)
    neta_suggestions: list[str] = field(default_factory=list)
    sample_posts: list[dict] = field(default_factory=list)

    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def analyze_neta(
    api_key: str,
    handle: str,
    max_posts: int = 100,
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

    result = NetaResult(handle=handle, post_count=0)

    # ① 投稿を取得（タイムラインAPIで全期間遡る）
    _cb(0.2, f"投稿を取得中（最大{max_posts}件）...")
    raw_tweets = client.get_all_tweets_from_path(
        f"/twitter/user/{uid}/tweets", max_results=max_posts
    )

    # リプライを除外（in_reply_to_status_id_str があるものはリプライ）
    liked_tweets = [
        t for t in raw_tweets
        if not (t.get("in_reply_to_status_id_str") or t.get("in_reply_to_status_id"))
    ]

    result.post_count = len(liked_tweets)

    # ② テキスト分析（インプレッション加重）
    _cb(0.6, "コンテンツ傾向を分析中...")
    kw_counter: Counter = Counter()
    ht_counter: Counter = Counter()

    for tw in liked_tweets:
        text = tw.get("full_text", "") or tw.get("text", "")
        views = tw.get("views_count", 0) or 0
        # インプ加重: 1000インプ単位で+1（最低weight=1）
        weight = max(1, 1 + views // 1000)
        for kw in _extract_keywords(text):
            kw_counter[kw] += weight
        for ht in _extract_hashtags(text):
            ht_counter[ht] += weight

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

    # ⑤ サンプル投稿（インプレッション上位 / 重複除去済み）
    top_sample = sorted(liked_tweets, key=lambda t: t.get("views_count", 0) or 0, reverse=True)[:8]
    result.sample_posts = [
        {
            "text": (t.get("full_text", "") or t.get("text", ""))[:100],
            "views": t.get("views_count", 0) or 0,
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
