"""オーディエンス・インタレスト分析 - ターゲットの生の声を抽出"""
import re
import time
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from api import SocialDataClient

try:
    from janome.tokenizer import Tokenizer
    _tokenizer = Tokenizer()
    _janome_ok = True
except Exception:
    _janome_ok = False

CACHE_DIR = Path(__file__).parent / "cache" / "audience"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── ストップワード ───────────────────────────────────────────
JP_STOP = {
    'する', 'した', 'して', 'される', 'いる', 'いた', 'ある', 'あった',
    'なる', 'なった', 'もの', 'こと', 'ため', 'よう', 'ところ', 'とき',
    'これ', 'それ', 'あれ', 'ここ', 'そこ', 'この', 'その', 'あの',
    'です', 'ます', 'でした', 'ました', 'ません', 'だ', 'だった',
    'しかし', 'でも', 'また', 'そして', 'なので', 'だから', 'けど', 'ので',
    'から', 'まで', 'など', 'という', 'といった', 'について',
    'みんな', 'あなた', 'わたし', 'ぼく', 'おれ', 'わたし',
    'http', 'https', 't', 'co', 'RT', 'amp', 'pic', 'twitter',
    'ほんと', 'ほんとに', 'すごい', 'めちゃ', 'めっちゃ', 'やっぱ',
    'なんか', 'なんで', 'なんと', 'もっと', 'ちょっと', 'とても',
    'そういう', 'こういう', 'ような', 'ように', 'という', 'とか',
    'まじ', 'まじで', 'わかる', 'わかった', 'おもう', 'おもった',
}
EN_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
    'it', 'this', 'that', 'and', 'or', 'but', 'not', 'so', 'if',
    'i', 'me', 'my', 'we', 'you', 'he', 'she', 'they', 'them',
    'what', 'which', 'who', 'how', 'when', 'where', 'why', 'rt',
}

# ─── スパム判定ワード ─────────────────────────────────────────
# これらが2つ以上含まれる投稿はキャンペーン・スパムとして除外
SPAM_KEYWORDS = [
    'フォロー', 'リポスト', 'プレゼント', '懸賞', 'キャンペーン',
    '当選', '抽選', '当たる', 'もれなく', '全員', 'QUO', 'ギフト券',
    'フォロリポ', 'RTで当', 'リツイート', '応募', '締切',
]

# ─── 感情・悩みキーワード ─────────────────────────────────────
PAIN_WORDS = [
    '悩んでる', '悩んでいる', '悩み', '困ってる', '困っている', '困った',
    '知りたい', '教えて', 'どうすれば', 'どうやって', 'どうしたら',
    'できない', 'わからない', 'むずかしい', '難しい', 'つらい', '辛い',
    '失敗', '失敗した', '不安', '心配', '怖い', '怖くて',
    'やり方', '方法', 'コツ', 'ポイント', '秘訣', 'ヒント',
    'はじめて', '初めて', '初心者', '初めての', 'スタート',
    '稼ぎたい', '稼げない', '稼げる', '副業', '収入', '月収', '年収',
    '時間がない', 'お金がない', 'スキルがない',
]

DEMO_PATTERNS = [
    r'\d+代', r'\d+歳', r'会社員', r'サラリーマン', r'フリーランス',
    r'主婦', r'主夫', r'学生', r'大学生', r'社会人', r'新卒', r'転職',
    r'副業', r'起業', r'独立', r'育児', r'子育て', r'ママ', r'パパ',
]


# ─── スパム判定 ───────────────────────────────────────────────
def _is_spam(tweet: dict) -> bool:
    """キャンペーン・プレゼント系スパム投稿を除外"""
    text = tweet.get('full_text', '') or tweet.get('text', '')
    hit = sum(1 for w in SPAM_KEYWORDS if w in text)
    return hit >= 2


# ─── テキスト解析 ─────────────────────────────────────────────
def _clean_text(text: str) -> str:
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#(\w+)', r'\1', text)
    return text.strip()


def _extract_hashtags(text: str) -> list[str]:
    return re.findall(r'#([^\s#]+)', text)


def _extract_questions(text: str) -> list[str]:
    sentences = re.split(r'[。！!?\n]', text)
    return [s.strip() for s in sentences
            if ('?' in s or '？' in s or
                any(w in s for w in ['知りたい', 'どうすれば', 'どうやって', 'ですか', 'できますか', 'やり方']))]


def _extract_keywords(text: str, min_len: int = 2) -> list[str]:
    text = _clean_text(text)
    words = []

    if _janome_ok:
        for token in _tokenizer.tokenize(text):
            parts = token.part_of_speech.split(',')
            pos0 = parts[0]
            pos1 = parts[1] if len(parts) > 1 else ''
            base = token.base_form

            if pos0 != '名詞':
                continue
            if pos1 in ('代名詞', '非自立', '数', '接尾', 'サ変接続'):
                continue
            if len(base) < min_len:
                continue
            if base in JP_STOP:
                continue
            if re.match(r'^[0-9０-９]+$', base):
                continue
            words.append(base)
    else:
        words = re.findall(r'[一-龯]{2,}|[ァ-ヶー]{3,}', text)
        words = [w for w in words if w not in JP_STOP]

    en_words = re.findall(r'[a-zA-Z]{3,}', text)
    words += [w.lower() for w in en_words if w.lower() not in EN_STOP]

    return words


def _extract_demo(text: str) -> list[str]:
    found = []
    for pattern in DEMO_PATTERNS:
        found.extend(re.findall(pattern, text))
    return found


def _extract_pain(text: str) -> list[str]:
    return [w for w in PAIN_WORDS if w in text]


# ─── データクラス ─────────────────────────────────────────────
@dataclass
class AudienceResult:
    seed_keyword: str
    seed_posts_count: int
    comments_analyzed: int

    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_hashtags: list[tuple[str, int]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    pain_points: list[tuple[str, int]] = field(default_factory=list)
    demographics: list[tuple[str, int]] = field(default_factory=list)

    viral_keywords: list[tuple[str, int]] = field(default_factory=list)
    viral_hashtags: list[tuple[str, int]] = field(default_factory=list)

    # バズ投稿サンプル（UIで表示用）
    seed_posts: list[dict] = field(default_factory=list)

    topic_suggestions: list[str] = field(default_factory=list)

    raw_comments: list[str] = field(default_factory=list)
    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


# ─── キャッシュ ───────────────────────────────────────────────
def _cache_path(key: str) -> Path:
    safe = re.sub(r'[^\w]', '_', key)
    return CACHE_DIR / f"{safe}.json"


def _load_cache(key: str) -> Optional[list]:
    p = _cache_path(key)
    if p.exists():
        return json.loads(p.read_text('utf-8'))
    return None


def _save_cache(key: str, data: list):
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), 'utf-8')


# ─── メイン分析関数 ───────────────────────────────────────────
def analyze_audience(
    api_key: str,
    seed_keyword: str,
    min_faves: int = 300,
    max_seed_posts: int = 10,
    max_comments_per_post: int = 50,
    days: int = 30,
    progress_callback: Optional[Callable] = None,
) -> AudienceResult:
    import time as _time
    from datetime import datetime, timedelta

    client = SocialDataClient(api_key)
    start = _time.time()

    result = AudienceResult(
        seed_keyword=seed_keyword,
        seed_posts_count=0,
        comments_analyzed=0,
    )

    since = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    # ① バズ投稿を検索（スパム除外 + 日本語限定）
    if progress_callback:
        progress_callback('search', 0, f'「{seed_keyword}」のバズ投稿を検索中...')

    # APIレベルでのスパム除外：キャンペーン・懸賞系を除外、日本語限定
    spam_exclude = '-プレゼント -懸賞 -キャンペーン -RTで当 -フォロリポ lang:ja'
    query = f"{seed_keyword} min_faves:{min_faves} -filter:replies since:{since} {spam_exclude}"

    raw_tweets = client.search_all_tweets(query, max_results=max_seed_posts * 3)

    # クライアントサイドでさらにスパム判定（2重フィルタ）
    seed_tweets = [t for t in raw_tweets if not _is_spam(t)][:max_seed_posts]
    result.seed_posts_count = len(seed_tweets)

    if not seed_tweets:
        result.api_calls = client._call_count
        result.cost_jpy = client.estimated_cost_jpy
        result.elapsed_sec = _time.time() - start
        return result

    # バズ投稿自体を解析（コンテンツの傾向を把握する）
    viral_kw_counter: Counter = Counter()
    viral_ht_counter: Counter = Counter()

    for tw in seed_tweets:
        text = tw.get('full_text', '') or tw.get('text', '')
        viral_kw_counter.update(_extract_keywords(text))
        viral_ht_counter.update(_extract_hashtags(text))

    result.viral_keywords = viral_kw_counter.most_common(30)
    result.viral_hashtags = viral_ht_counter.most_common(20)

    # バズ投稿サンプル（UI表示用：いいね順で上位5件）
    sorted_posts = sorted(seed_tweets, key=lambda t: t.get('favorite_count', 0), reverse=True)
    result.seed_posts = [
        {
            'text': (tw.get('full_text', '') or tw.get('text', ''))[:200],
            'likes': tw.get('favorite_count', 0),
            'retweets': tw.get('retweet_count', 0),
            'views': tw.get('views_count', 0),
            'author': tw.get('user', {}).get('screen_name', ''),
            'url': f"https://x.com/{tw.get('user', {}).get('screen_name', 'i')}/status/{tw.get('id_str', '')}",
        }
        for tw in sorted_posts[:5]
    ]

    # ② 各バズ投稿のコメントを収集
    kw_counter: Counter = Counter()
    ht_counter: Counter = Counter()
    pain_counter: Counter = Counter()
    demo_counter: Counter = Counter()
    all_questions: list[str] = []
    all_comments: list[str] = []

    for i, tw in enumerate(seed_tweets):
        tweet_id = str(tw.get('id') or tw.get('id_str', ''))
        if not tweet_id:
            continue

        if progress_callback:
            progress_callback('comments', i + 1, f'コメント取得中... {i+1}/{len(seed_tweets)}件目')

        cache_key = f"comments_{tweet_id}"
        cached = _load_cache(cache_key)
        if cached is not None:
            comments = cached
        else:
            try:
                data = client._get(f'/twitter/tweets/{tweet_id}/comments')
                comments = []
                for c in (data.get('tweets') or data.get('comments') or [])[:max_comments_per_post]:
                    text = c.get('full_text', '') or c.get('text', '')
                    if text:
                        comments.append(text)
                _save_cache(cache_key, comments)
            except Exception:
                comments = []

        for text in comments:
            all_comments.append(text)
            kw_counter.update(_extract_keywords(text))
            ht_counter.update(_extract_hashtags(text))
            pain_counter.update(_extract_pain(text))
            demo_counter.update(_extract_demo(text))
            all_questions.extend(_extract_questions(text))

        time.sleep(0.15)

    result.comments_analyzed = len(all_comments)
    result.top_keywords = kw_counter.most_common(30)
    result.top_hashtags = ht_counter.most_common(20)
    result.pain_points = [(k, v) for k, v in pain_counter.most_common(15) if v > 0]
    result.demographics = [(k, v) for k, v in demo_counter.most_common(15) if v > 0]

    seen = set()
    for q in all_questions:
        q = q.strip()
        if q and len(q) > 5 and q not in seen:
            seen.add(q)
            result.questions.append(q)
            if len(result.questions) >= 20:
                break

    result.raw_comments = all_comments[:100]

    # ③ ネタ候補を生成（バズ投稿キーワード＋コメントのペイン双方を活用）
    result.topic_suggestions = _generate_topics(
        seed_keyword,
        keywords=result.top_keywords,
        pains=result.pain_points,
        questions=result.questions,
        viral_keywords=result.viral_keywords,
    )

    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result


def _generate_topics(
    seed: str,
    keywords: list[tuple[str, int]],
    pains: list[tuple[str, int]],
    questions: list[str],
    viral_keywords: list[tuple[str, int]] = None,
) -> list[str]:
    """
    バズる投稿の「型」×キーワードでネタ候補を生成する。
    コメントの生テキストをそのまま流用しない。
    """
    topics = []

    # バズ投稿のキーワードを優先、なければコメントのキーワード
    source_kws = [k for k, _ in (viral_keywords or keywords)[:15] if k != seed and len(k) >= 2]
    comment_kws = [k for k, _ in keywords[:10] if k != seed and len(k) >= 2]

    kw1 = source_kws[0] if source_kws else seed
    kw2 = source_kws[1] if len(source_kws) > 1 else kw1
    kw3 = source_kws[2] if len(source_kws) > 2 else kw1
    pain_word = pains[0][0] if pains else None

    # ── 型1: 経験談・告白型（リアルさでバズる）
    topics.append(f"{seed}を本気でやって気づいた「やめてよかったこと」")
    topics.append(f"「{kw1}」について正直に言う。{seed}の現実")
    topics.append(f"{seed}を1年やった人間が、今ゼロに戻るなら最初にやること")

    # ── 型2: 反論・逆張り型（議論を呼ぶ）
    topics.append(f"「{seed}は{kw1}が大事」は半分ウソ。本当に効くのは〇〇")
    topics.append(f"みんなが信じてる{seed}の「常識」、実は逆効果だった")

    # ── 型3: 対比・格差型（共感と危機感を生む）
    if len(source_kws) >= 2:
        topics.append(f"{seed}で成果が出る人と出ない人。違いは「{kw1}」だけ")
    topics.append(f"同じ{seed}をやって、伸びた人と伸びなかった人の差")

    # ── 型4: リスト・保存型（拡散されやすい）
    topics.append(f"{seed}初心者が最初の3ヶ月でやるべき5つのこと")
    topics.append(f"今すぐやめるべき{seed}の習慣。上位3位を公開")
    if len(source_kws) >= 2:
        topics.append(f"{seed}で使える「{kw1}×{kw2}」の組み合わせ術")

    # ── 型5: 数字・証明型（信頼を生む）
    topics.append(f"{seed}を90日続けた結果を正直に公開します")
    if kw3:
        topics.append(f"「{kw3}」に本気で取り組んで変わった{seed}の話")

    # ── 型6: ペイン解消型（悩み検索からの流入）
    if pain_word:
        topics.append(f"{pain_word}なら、まず{seed}の「{kw1}」を見直してみて")
    else:
        topics.append(f"{seed}がうまくいかない人の99%が見落としていること")

    # ── 型7: 問いかけ型（コメントを引き出す）
    if comment_kws:
        ck = comment_kws[0]
        topics.append(f"あなたの{seed}、「{ck}」になってませんか？")

    return topics[:15]
