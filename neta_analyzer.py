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


# ── ポストスタイル5パターン分類 ─────────────────────────────────────
STYLE_PATTERNS = {
    "リスト・まとめ型": {
        "desc": "箇条書き・番号付きリストで情報を整理する",
        "signals": ["①", "②", "③", "④", "⑤", "１.", "２.", "・\n", "1.", "2.", "3.", "【", "▶", "✅"],
    },
    "問いかけ・共感型": {
        "desc": "読者に問いかけ・共感を促す",
        "signals": ["？", "ですよね", "じゃないですか", "思いませんか", "ませんか", "どうですか", "ではないでしょうか", "あなたは"],
    },
    "ストーリー・体験談型": {
        "desc": "自身の体験・エピソードを語る",
        "signals": ["実は", "正直に", "先日", "昨日", "〜した話", "体験", "経験", "あの頃", "私が", "僕が", "失敗", "気づいた"],
    },
    "教育・解説型": {
        "desc": "知識・ノウハウを分かりやすく解説する",
        "signals": ["なぜなら", "理由は", "とは", "ポイントは", "方法", "解説", "仕組み", "原則", "法則", "のコツ", "やり方", "手順"],
    },
    "主張・断言型": {
        "desc": "強い意見・断定で読者を引きつける",
        "signals": ["すべき", "は間違い", "断言", "確信", "絶対に", "必ず", "これだけは", "言い切れる", "事実として", "はっきり"],
    },
}


def classify_post_style(text: str) -> str:
    """1ツイートをスタイル5パターンに分類"""
    scores = {style: 0 for style in STYLE_PATTERNS}
    for style, info in STYLE_PATTERNS.items():
        for sig in info["signals"]:
            if sig in text:
                scores[style] += 1
    best = max(scores, key=lambda s: scores[s])
    # スコアが0なら「主張・断言型」にフォールバック（デフォルト最多）
    return best if scores[best] > 0 else "主張・断言型"


@dataclass
class NetaResult:
    handle: str
    post_count: int

    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_hashtags: list[tuple[str, int]] = field(default_factory=list)
    topic_clusters: dict = field(default_factory=dict)
    neta_suggestions: list[str] = field(default_factory=list)
    sample_posts: list[dict] = field(default_factory=list)

    # ポストスタイル分類
    style_distribution: dict = field(default_factory=dict)   # style → count
    style_examples: dict = field(default_factory=dict)        # style → [{"text","views","url"}, ...]

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

    # ① 投稿を取得（検索APIでリプライ除外・ページネーション制限なし）
    _cb(0.2, f"投稿を取得中（最大{max_posts}件）...")
    raw_tweets = client.search_all_tweets(
        f"from:{handle} -filter:replies", max_results=max_posts
    )

    # 重複除去（id_strベース）
    seen_ids: set = set()
    liked_tweets = []
    for t in raw_tweets:
        tid = t.get("id_str") or str(t.get("id", ""))
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            liked_tweets.append(t)

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

    # ⑥ ポストスタイル5パターン分類
    _cb(0.92, "投稿スタイルを分類中...")
    from collections import defaultdict as _dd
    style_count: dict = {s: 0 for s in STYLE_PATTERNS}
    style_best: dict = {s: [] for s in STYLE_PATTERNS}  # top2 by views

    for tw in liked_tweets:
        text = tw.get("full_text", "") or tw.get("text", "")
        style = classify_post_style(text)
        views = tw.get("views_count", 0) or 0
        style_count[style] += 1
        style_best[style].append({
            "text": text[:120],
            "views": views,
            "url": f"https://x.com/{(tw.get('user') or {}).get('screen_name', '_')}/status/{tw.get('id_str', '')}",
        })

    result.style_distribution = style_count
    result.style_examples = {
        s: sorted(posts, key=lambda p: p["views"], reverse=True)[:2]
        for s, posts in style_best.items()
    }

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
