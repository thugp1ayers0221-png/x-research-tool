"""kii専用 脳内SEOワード分析"""
import re
import time
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from api import SocialDataClient
from audience_analyzer import _extract_keywords, _extract_hashtags, _clean_text

OBSIDIAN_PATH = Path(
    "/Users/kiyomotoyuuki/Library/Mobile Documents/com~apple~CloudDocs"
    "/Obsidian/Second Brain/01_プロジェクト/kii_X運用/概要.md"
)

CACHE_DIR = Path(__file__).parent / "cache" / "kii"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── kiiのワードクラスター（Obsidianから導出） ─────────────────
WORD_CLUSTERS = {
    "コア概念": [
        "行動経済学", "認知バイアス", "言語化", "思考法",
    ],
    "親戚概念": [
        "心理学", "意思決定", "脳科学", "行動科学", "社会心理学",
        "バイアス", "ナッジ", "損失回避", "アンカリング",
    ],
    "活用・応用": [
        "マーケティング", "消費者心理", "説得", "価格設定",
        "集客", "売上", "顧客心理", "ビジネス心理",
    ],
    "読者属性": [
        "独立", "起業", "経営", "キャリア", "副業",
        "思考力", "教養", "読書", "ビジネス書",
    ],
}

# 検索に使うシードワード（クラスターから厳選）
SEARCH_SEEDS = [
    "行動経済学",
    "認知バイアス",
    "言語化",
    "思考法",
    "消費者心理",
]

# 引用テキストで「なぜ刺さったか」を判定するシグナルワード
REACTION_SIGNALS = {
    "実用性": ["使える", "使えそう", "活用", "実践", "役立つ", "仕事に", "業務に"],
    "気づき": ["なるほど", "確かに", "そういうこと", "腑に落ちた", "初めて知った", "知らなかった"],
    "共感": ["わかる", "自分も", "あるある", "まさに", "これ", "超わかる"],
    "保存欲求": ["保存", "メモ", "ブクマ", "スクショ", "永久保存", "残しておきたい"],
    "拡散欲求": ["みんなに", "伝えたい", "シェア", "教えてあげたい"],
    "深掘り欲求": ["もっと知りたい", "詳しく", "続き", "他には", "どういうこと"],
}


# ─── キャッシュ ───────────────────────────────────────────────
def _cache_path(key: str) -> Path:
    safe = re.sub(r'[^\w]', '_', key)
    return CACHE_DIR / f"{safe}.json"


def _load_cache(key: str) -> Optional[list]:
    p = _cache_path(key)
    if p.exists():
        return json.loads(p.read_text('utf-8'))
    return None


def _save_cache(key: str, data):
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), 'utf-8')


# ─── データクラス ─────────────────────────────────────────────
@dataclass
class BrainSEOResult:
    # 脳内SEOワード（引用RT＋バズ投稿テキストから）
    brain_seo_words: list[tuple[str, int]] = field(default_factory=list)

    # 反応シグナル別の分類
    reaction_signals: dict[str, list[str]] = field(default_factory=dict)

    # バズ投稿の共起ワード（「このワードと一緒に出てくる」）
    cooccurrence: list[tuple[str, int]] = field(default_factory=list)

    # 引用RT数が多かったバズ投稿トップ
    top_quoted_posts: list[dict] = field(default_factory=list)

    # 引用テキストのサンプル
    quote_samples: list[str] = field(default_factory=list)

    # ネタ角度の提案（「この切り口で刺さる」）
    angle_suggestions: list[str] = field(default_factory=list)

    # 使ったシード・統計
    seeds_used: list[str] = field(default_factory=list)
    posts_analyzed: int = 0
    quotes_analyzed: int = 0
    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


# ─── メイン分析 ───────────────────────────────────────────────
def analyze_brain_seo(
    api_key: str,
    seeds: Optional[list[str]] = None,
    min_faves: int = 300,
    min_retweets: int = 30,
    days: int = 30,
    max_posts_per_seed: int = 20,
    max_quotes_per_post: int = 30,
    progress_callback: Optional[Callable] = None,
) -> BrainSEOResult:
    from datetime import datetime, timedelta

    client = SocialDataClient(api_key)
    result = BrainSEOResult()
    start = time.time()

    seeds = seeds or SEARCH_SEEDS
    result.seeds_used = seeds

    since = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    # ① マルチシード検索
    all_posts = []
    for i, seed in enumerate(seeds):
        if progress_callback:
            progress_callback('search', i, f'「{seed}」のバズ投稿を検索中... ({i+1}/{len(seeds)})')

        cache_key = f"search_{seed}_{min_faves}_{min_retweets}_{since}"
        cached = _load_cache(cache_key)
        if cached is not None:
            posts = cached
        else:
            query = (
                f"{seed} (ビジネス OR 経営 OR 仕事 OR 思考 OR 心理 OR 意思決定 OR マーケティング) "
                f"min_faves:{min_faves} min_retweets:{min_retweets} "
                f"-filter:replies since:{since} lang:ja"
            )
            posts = client.search_all_tweets(query, max_results=max_posts_per_seed)
            _save_cache(cache_key, posts)

        all_posts.extend(posts)
        time.sleep(0.2)

    # 重複除去（tweet_id）
    seen_ids = set()
    unique_posts = []
    for p in all_posts:
        tid = str(p.get('id') or p.get('id_str', ''))
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique_posts.append(p)

    # ドメイン関連性フィルタ（kiiのワードクラスターに1つも入らない投稿を除外）
    domain_words = {w for ws in WORD_CLUSTERS.values() for w in ws}
    domain_words.update(seeds)

    def _relevance_score(post: dict) -> int:
        text = post.get('full_text', '') or post.get('text', '')
        return sum(1 for w in domain_words if w in text)

    # 2ワード以上含む投稿のみ（1ワードは偶然一致が多い）
    relevant_posts = [p for p in unique_posts if _relevance_score(p) >= 2]
    # 関連投稿が5件未満なら閾値を下げてフォールバック
    if len(relevant_posts) < 5:
        relevant_posts = [p for p in unique_posts if _relevance_score(p) >= 1]
    unique_posts = relevant_posts if relevant_posts else unique_posts

    # quote_count 降順でソート → 引用されやすかった投稿を優先
    unique_posts.sort(
        key=lambda p: p.get('quote_count', 0) or 0,
        reverse=True
    )
    result.posts_analyzed = len(unique_posts)

    # ② バズ投稿テキストの共起ワード分析
    post_kw_counter: Counter = Counter()
    for p in unique_posts:
        text = p.get('full_text', '') or p.get('text', '')
        post_kw_counter.update(_extract_keywords(text))

    # シードワード自体は除外（自明すぎる）
    seed_set = {s for seed in seeds for s in _extract_keywords(seed)}
    result.cooccurrence = [
        (w, c) for w, c in post_kw_counter.most_common(40)
        if w not in seed_set
    ]

    # ③ 引用RTの取得・分析（quote_count が多い上位10件）
    top_posts = unique_posts[:10]
    result.top_quoted_posts = [
        {
            'text': (p.get('full_text', '') or p.get('text', ''))[:100],
            'likes': p.get('favorite_count', 0) or 0,
            'retweets': p.get('retweet_count', 0) or 0,
            'quotes': p.get('quote_count', 0) or 0,
            'url': f"https://x.com/{(p.get('user') or {}).get('screen_name', '')}/status/{p.get('id') or p.get('id_str', '')}",
            'author': (p.get('user') or {}).get('screen_name', ''),
        }
        for p in top_posts
    ]

    quote_kw_counter: Counter = Counter()
    all_quote_texts: list[str] = []
    reaction_counter: dict[str, Counter] = {k: Counter() for k in REACTION_SIGNALS}

    for i, post in enumerate(top_posts):
        tid = str(post.get('id') or post.get('id_str', ''))
        if not tid:
            continue

        if progress_callback:
            progress_callback('quotes', i + 1, f'引用ツイートを取得中... {i+1}/{len(top_posts)}件目')

        cache_key = f"quotes_{tid}"
        cached = _load_cache(cache_key)
        if cached is not None:
            quotes = cached
        else:
            try:
                data = client._get(f'/twitter/tweets/{tid}/quotes')
                quotes = []
                for q in (data.get('tweets') or [])[:max_quotes_per_post]:
                    text = q.get('full_text', '') or q.get('text', '')
                    if text:
                        quotes.append(text)
                _save_cache(cache_key, quotes)
            except Exception:
                quotes = []

        for text in quotes:
            all_quote_texts.append(text)
            quote_kw_counter.update(_extract_keywords(text))

            # 反応シグナルの検出
            for signal_name, signal_words in REACTION_SIGNALS.items():
                for sw in signal_words:
                    if sw in text:
                        reaction_counter[signal_name][sw] += 1

        time.sleep(0.15)

    result.quotes_analyzed = len(all_quote_texts)
    result.quote_samples = all_quote_texts[:30]

    # ④ 脳内SEOワード = 引用RT内ワード + 共起ワードの統合
    combined_counter: Counter = Counter()
    for w, c in quote_kw_counter.items():
        combined_counter[w] += c * 2  # 引用RTは重み2倍（より生の声）
    for w, c in post_kw_counter.items():
        combined_counter[w] += c

    result.brain_seo_words = [
        (w, c) for w, c in combined_counter.most_common(35)
        if w not in seed_set
    ]

    # 反応シグナルの集計
    result.reaction_signals = {
        name: [f"{w}({c}回)" for w, c in cnt.most_common(5)]
        for name, cnt in reaction_counter.items()
        if cnt
    }

    # ⑤ ネタ角度の提案
    result.angle_suggestions = _suggest_angles(
        result.brain_seo_words,
        result.reaction_signals,
        result.cooccurrence,
    )

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = time.time() - start
    return result


def _suggest_angles(
    brain_seo: list[tuple[str, int]],
    signals: dict[str, list[str]],
    cooccurrence: list[tuple[str, int]],
) -> list[str]:
    """脳内SEOワードと反応シグナルからネタ角度を提案"""
    angles = []
    top_words = [w for w, _ in brain_seo[:8]]
    top_cooc = [w for w, _ in cooccurrence[:5]]

    # 実用性シグナルが強い → 「使い方」「活用法」系
    if signals.get('実用性'):
        for w in top_words[:3]:
            angles.append(f"【実践型】{w}をビジネスで使う具体的な方法")

    # 気づきシグナルが強い → 「逆説・意外性」系
    if signals.get('気づき'):
        for w in top_words[:2]:
            angles.append(f"【逆説型】{w}について、ほとんどの人が誤解していること")

    # 共感シグナルが強い → 「あるある」系
    if signals.get('共感'):
        angles.append(f"【あるある型】{top_words[0] if top_words else ''}を知ってから変わったこと")

    # 保存欲求が強い → 「まとめ・構造化」系
    if signals.get('保存欲求'):
        for w in top_words[:2]:
            angles.append(f"【保存型】{w}の構造を一言で言うと")

    # 共起ワードから複合ネタ（top_words[0]と同じワードは除外）
    top_word = top_words[0] if top_words else ''
    for w in top_cooc[:5]:
        if top_words and w != top_word:
            angles.append(f"【掛け合わせ型】{top_word}×{w}の意外な関係")
            if len([a for a in angles if '掛け合わせ型' in a]) >= 3:
                break

    # デフォルトネタ
    for w in top_words[3:6]:
        angles.append(f"{w}の話をしたら、意外と誰も知らなかった")

    return angles[:12]
