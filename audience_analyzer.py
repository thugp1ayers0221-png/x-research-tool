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

# ─── 感情・悩みキーワード（生の声検出用） ─────────────────────
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

# 属性ワード（デモグラ検出用）
DEMO_PATTERNS = [
    r'\d+代', r'\d+歳', r'会社員', r'サラリーマン', r'フリーランス',
    r'主婦', r'主夫', r'学生', r'大学生', r'社会人', r'新卒', r'転職',
    r'副業', r'起業', r'独立', r'育児', r'子育て', r'ママ', r'パパ',
]


# ─── テキスト解析 ─────────────────────────────────────────────
def _clean_text(text: str) -> str:
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#(\w+)', r'\1', text)
    return text.strip()


def _extract_hashtags(text: str) -> list[str]:
    return re.findall(r'#([^\s#]+)', text)


def _extract_questions(text: str) -> list[str]:
    """質問文を抽出"""
    sentences = re.split(r'[。！!?\n]', text)
    return [s.strip() for s in sentences
            if ('?' in s or '？' in s or
                any(w in s for w in ['知りたい', 'どうすれば', 'どうやって', 'ですか', 'できますか', 'やり方']))]


def _extract_keywords(text: str, min_len: int = 2) -> list[str]:
    """形態素解析でキーワード抽出（名詞-一般・名詞-固有名詞のみ）"""
    text = _clean_text(text)
    words = []

    if _janome_ok:
        for token in _tokenizer.tokenize(text):
            parts = token.part_of_speech.split(',')
            pos0 = parts[0]   # 品詞（名詞/動詞/etc）
            pos1 = parts[1] if len(parts) > 1 else ''  # 品詞細分類1
            base = token.base_form

            # 名詞のみ、かつ意味のある細分類のみ
            if pos0 != '名詞':
                continue
            # 除外する名詞細分類
            if pos1 in ('代名詞', '非自立', '数', '接尾', 'サ変接続'):
                continue
            if len(base) < min_len:
                continue
            if base in JP_STOP:
                continue
            if re.match(r'^[0-9０-９]+$', base):  # 数字のみ除外
                continue

            words.append(base)
    else:
        # fallback: 2文字以上の漢字・カタカナ連続
        words = re.findall(r'[一-龯]{2,}|[ァ-ヶー]{3,}', text)
        words = [w for w in words if w not in JP_STOP]

    # 英単語（3文字以上、大文字始まりの固有名詞は原形で保持）
    en_words = re.findall(r'[a-zA-Z]{3,}', text)
    words += [w.lower() for w in en_words if w.lower() not in EN_STOP]

    return words


def _extract_demo(text: str) -> list[str]:
    """属性ワードを抽出"""
    found = []
    for pattern in DEMO_PATTERNS:
        found.extend(re.findall(pattern, text))
    return found


def _extract_pain(text: str) -> list[str]:
    """悩み・ニーズワードを抽出"""
    return [w for w in PAIN_WORDS if w in text]


# ─── データクラス ─────────────────────────────────────────────
@dataclass
class AudienceResult:
    seed_keyword: str
    seed_posts_count: int
    comments_analyzed: int

    # 生の声（コメント・引用から）
    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_hashtags: list[tuple[str, int]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    pain_points: list[tuple[str, int]] = field(default_factory=list)
    demographics: list[tuple[str, int]] = field(default_factory=list)

    # バズ投稿自体のキーワード（コンテンツ傾向）
    viral_keywords: list[tuple[str, int]] = field(default_factory=list)
    viral_hashtags: list[tuple[str, int]] = field(default_factory=list)

    # ネタ候補
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

    # ① バズ投稿を検索
    if progress_callback:
        progress_callback('search', 0, f'「{seed_keyword}」のバズ投稿を検索中...')

    query = f"{seed_keyword} min_faves:{min_faves} -filter:replies since:{since}"
    seed_tweets = client.search_all_tweets(query, max_results=max_seed_posts)
    result.seed_posts_count = len(seed_tweets)

    if not seed_tweets:
        result.api_calls = client._call_count
        result.cost_jpy = client.estimated_cost_jpy
        result.elapsed_sec = _time.time() - start
        return result

    # バズ投稿のテキストを分析
    viral_kw_counter: Counter = Counter()
    viral_ht_counter: Counter = Counter()

    for tw in seed_tweets:
        text = tw.get('full_text', '') or tw.get('text', '')
        viral_kw_counter.update(_extract_keywords(text))
        viral_ht_counter.update(_extract_hashtags(text))

    result.viral_keywords = viral_kw_counter.most_common(30)
    result.viral_hashtags = viral_ht_counter.most_common(20)

    # ② 各バズ投稿のコメント（リプライ）を収集
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
                data = client._get(f'/twitter/tweet/{tweet_id}/comments')
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

    # 質問を重複排除して最大20件
    seen = set()
    for q in all_questions:
        q = q.strip()
        if q and len(q) > 5 and q not in seen:
            seen.add(q)
            result.questions.append(q)
            if len(result.questions) >= 20:
                break

    result.raw_comments = all_comments[:100]

    # ③ ネタ候補を生成
    result.topic_suggestions = _generate_topics(
        seed_keyword, result.top_keywords, result.pain_points, result.questions
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
) -> list[str]:
    """頻出ワード×悩みパターンからネタ候補を生成"""
    topics = []
    top_kws = [k for k, _ in keywords[:10]]
    top_pains = [k for k, _ in pains[:5]]

    # 質問ベースのネタ
    for q in questions[:5]:
        if len(q) > 8:
            topics.append(f"【Q&A型】{q}")

    # キーワード×悩みの組み合わせ
    pain_templates = [
        '{seed}で{pain}人が最初にやるべきこと',
        '{seed}が{pain}理由と解決策',
        '{kw}で{seed}をうまくやる方法',
        '{seed}初心者が陥る{kw}の罠',
        '{kw}を使った{seed}の具体的なやり方',
    ]

    kw = top_kws[0] if top_kws else seed
    pain = top_pains[0] if top_pains else 'うまくいかない'

    for tmpl in pain_templates:
        t = tmpl.format(seed=seed, kw=kw, pain=pain)
        topics.append(t)

    # 属性 × シード
    attribute_templates = [
        '会社員が{seed}で月収を増やす方法',
        '初心者が{seed}で結果を出した話',
        '{seed}を3ヶ月でマスターした具体的なステップ',
        '失敗から学んだ{seed}の本当のコツ',
        '{seed}で変わった実体験（数字で証明）',
    ]
    for tmpl in attribute_templates:
        topics.append(tmpl.format(seed=seed))

    return topics[:15]
