"""ペルソナ調査 - ターゲット層のいいね行動から興味・ペイン・インサイトを抽出"""
import re
import time
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from api import SocialDataClient

_tokenizer = None
_janome_ok = False

def _get_tokenizer():
    """Tokenizerを初回使用時だけロード（起動時間短縮）"""
    global _tokenizer, _janome_ok
    if _tokenizer is None:
        try:
            from janome.tokenizer import Tokenizer
            _tokenizer = Tokenizer()
            _janome_ok = True
        except Exception:
            _janome_ok = False
    return _tokenizer, _janome_ok

CACHE_DIR = Path(__file__).parent / "cache" / "persona"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── ストップワード ───────────────────────────────────────────
JP_STOP = {
    'する', 'した', 'して', 'される', 'いる', 'いた', 'ある', 'あった',
    'なる', 'なった', 'もの', 'こと', 'ため', 'よう', 'ところ', 'とき',
    'これ', 'それ', 'あれ', 'ここ', 'そこ', 'この', 'その', 'あの',
    'です', 'ます', 'でした', 'ました', 'ません', 'だ', 'だった',
    'しかし', 'でも', 'また', 'そして', 'なので', 'だから', 'けど', 'ので',
    'から', 'まで', 'など', 'という', 'といった', 'について',
    'みんな', 'あなた', 'わたし', 'ぼく', 'おれ', 'じぶん',
    'http', 'https', 't', 'co', 'RT', 'amp', 'pic', 'twitter',
    'ほんと', 'ほんとに', 'すごい', 'めちゃ', 'めっちゃ', 'やっぱ',
    'なんか', 'なんで', 'なんと', 'もっと', 'ちょっと', 'とても',
    'そういう', 'こういう', 'ような', 'ように', 'という', 'とか',
    'まじ', 'まじで', 'わかる', 'わかった', 'おもう', 'おもった',
    'ない', 'いい', 'よい', 'よく', 'だけ', 'しか', 'まで', 'より',
    'れる', 'られる', 'たい', 'ない', 'なく', 'なって', 'なかっ',
    'てる', 'てた', 'てい', 'てし', 'てき', 'ておく', 'ていく',
}
EN_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
    'it', 'this', 'that', 'and', 'or', 'but', 'not', 'so', 'if',
    'i', 'me', 'my', 'we', 'you', 'he', 'she', 'they', 'them',
    'what', 'which', 'who', 'how', 'when', 'where', 'why', 'rt',
    'just', 'can', 'get', 'go', 'one', 'all', 'more', 'also',
}


# ─── コンテンツ形式パターン ───────────────────────────────────
FORMAT_PATTERNS = {
    '問いかけ型': [r'[？?]', r'どう思', r'どうする', r'みんなは', r'あなたは'],
    'リスト型': [r'[①②③④⑤⑥⑦⑧⑨⑩]', r'^\d[\.．、]', r'・.+\n・', r'つの', r'ヶ条'],
    '経験談型': [r'した結果', r'やってみた', r'気づいた', r'わかった', r'実際に', r'正直に', r'告白'],
    '反論型': [r'ではない', r'じゃない', r'違う', r'勘違い', r'実は', r'意外と', r'逆に'],
    '数字型': [r'\d+[万億千百%％倍年ヶ月週日時間分]', r'\d+個', r'\d+つ', r'\d+選'],
}


# ─── データクラス ─────────────────────────────────────────────
@dataclass
class PersonaResult:
    user_count: int = 0
    like_count: int = 0
    top_keywords: list = field(default_factory=list)   # [(word, count), ...]
    bigrams: list = field(default_factory=list)         # [(phrase, count), ...]
    trigrams: list = field(default_factory=list)        # [(phrase, count), ...]
    top_accounts: list = field(default_factory=list)    # [(screen_name, count), ...]
    format_dist: dict = field(default_factory=dict)     # {"問いかけ型": 123, ...}
    sample_posts: dict = field(default_factory=dict)    # {"問いかけ型": [...], ...}
    estimated_cost_jpy: float = 0.0
    errors: list = field(default_factory=list)


# ─── ユーティリティ ───────────────────────────────────────────
def _extract_words(text: str) -> list[str]:
    """テキストから有効な単語を抽出"""
    # URLを除去
    text = re.sub(r'https?://\S+', '', text)
    # メンション・ハッシュタグを除去
    text = re.sub(r'[@＠#＃]\S+', '', text)

    words = []
    tokenizer, janome_ok = _get_tokenizer()
    if janome_ok:
        try:
            for token in tokenizer.tokenize(text):
                w = token.surface
                pos = token.part_of_speech.split(',')[0]
                if pos in ('名詞', '動詞', '形容詞') and len(w) >= 2:
                    base = token.part_of_speech.split(',')[6] if token.part_of_speech.split(',')[6] != '*' else w
                    if base not in JP_STOP and w not in JP_STOP:
                        words.append(base)
        except Exception:
            pass
    else:
        # janome未インストール時はシンプルな分割
        for chunk in re.split(r'[\s、。！？!?,，\n]+', text):
            chunk = chunk.strip()
            if len(chunk) >= 2 and chunk not in JP_STOP:
                words.append(chunk)

    # 英単語
    for w in re.findall(r'[a-zA-Z]{3,}', text.lower()):
        if w not in EN_STOP:
            words.append(w)

    return words


def _classify_format(text: str) -> str:
    """投稿形式を判定"""
    for fmt, patterns in FORMAT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.MULTILINE):
                return fmt
    return '意見・解説型'


def _extract_ngrams(words: list[str], n: int) -> list[str]:
    """単語リストからn-gramフレーズを生成"""
    return [" ".join(words[i:i+n]) for i in range(len(words) - n + 1)]


def _is_valid_user(user: dict, min_followers: int, max_followers: int) -> bool:
    """フォロワー数条件チェック（ボット除外含む）"""
    fc = user.get('followers_count', 0)
    ff = user.get('friends_count', 0)  # following数
    verified = user.get('verified', False)
    # フォロワー数範囲チェック
    if not (min_followers <= fc <= max_followers):
        return False
    # フォロー数が異常に多い（フォロバ乞食ボット）を除外
    if ff > 0 and fc > 0 and ff / fc > 5 and ff > 3000:
        return False
    return True


# ─── メイン収集関数 ───────────────────────────────────────────
def analyze_persona(
    api_key: str,
    bio_keywords: list[str],
    min_followers: int = 1000,
    max_followers: int = 10000,
    target_users: int = 500,
    likes_per_user: int = 100,
    progress_callback: Optional[Callable] = None,
) -> PersonaResult:
    """
    ターゲット層のいいね行動からペルソナデータを収集・集計する。

    Parameters
    ----------
    bio_keywords  : 検索キーワード（プロフィール的な語）
    min/max_followers : フォロワー数フィルタ
    target_users  : 収集目標ユーザー数
    likes_per_user: 1人あたりいいね収集数（5ページ×20件）
    """
    client = SocialDataClient(api_key)
    result = PersonaResult()

    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    # ─── STEP 1: ターゲットユーザーを収集 ────────────────────
    _progress("👥 ターゲットユーザーを収集中...")
    seen_ids = set()
    target_user_list = []

    for kw in bio_keywords:
        if len(target_user_list) >= target_users:
            break
        query = f'"{kw}" lang:ja -filter:replies'
        cursor = None
        search_rounds = 0
        while len(target_user_list) < target_users and search_rounds < 20:
            try:
                data = client.search_tweets(query, cursor)
                tweets = data.get('tweets', [])
                if not tweets:
                    break
                for t in tweets:
                    author = t.get('user', {})
                    uid = author.get('id_str', '')
                    if uid and uid not in seen_ids:
                        if _is_valid_user(author, min_followers, max_followers):
                            seen_ids.add(uid)
                            target_user_list.append(author)
                cursor = data.get('next_cursor')
                if not cursor:
                    break
                search_rounds += 1
                time.sleep(0.2)
            except Exception as e:
                result.errors.append(f"ユーザー検索エラー ({kw}): {e}")
                break

        _progress(f"  ✓ {kw}: 累計 {len(target_user_list)}人")

    result.user_count = len(target_user_list)
    if result.user_count == 0:
        result.errors.append("ターゲットユーザーが見つかりませんでした")
        return result

    _progress(f"✅ {result.user_count}人のユーザーを収集しました")

    # ─── STEP 2: 投稿テキストを収集 ──────────────────────────
    # ※ X社が2024年6月にいいねを全ユーザー非公開化したため、
    #    いいね取得APIは恒久的に0件となった。
    #    代替としてユーザーの投稿テキストを分析する。
    _progress("📝 ターゲット層の投稿を収集中...")
    all_liked_posts = []  # 変数名はそのまま流用（集計ロジック共通）
    liked_account_counter = Counter()
    pages_per_user = max(1, likes_per_user // 20)  # 20件/ページ想定

    for i, user in enumerate(target_user_list):
        uid = user.get('id_str', '')
        screen_name = user.get('screen_name', '')
        if not uid:
            continue

        if (i + 1) % 50 == 0:
            _progress(f"  📝 {i+1}/{result.user_count}人処理中... ({len(all_liked_posts)}件収集済)")

        cursor = None
        user_posts = []
        for _ in range(pages_per_user):
            try:
                data = client.get_user_tweets(uid, cursor)
                tweets = data.get('tweets', [])
                if not tweets:
                    break
                user_posts.extend(tweets)
                # リプライ・RTは除外（オリジナル投稿のみ）
                for t in tweets:
                    text = t.get('full_text', '') or t.get('text', '')
                    if text and not text.startswith('RT @') and not text.startswith('@'):
                        liked_account_counter[screen_name] += 1
                cursor = data.get('next_cursor')
                if not cursor:
                    break
                time.sleep(0.15)
            except Exception as e:
                result.errors.append(f"投稿取得エラー (@{screen_name}): {e}")
                break

        all_liked_posts.extend(user_posts)

    result.like_count = len(all_liked_posts)
    _progress(f"✅ {result.like_count}件の投稿を収集しました")

    # ─── STEP 3: テキスト集計 ────────────────────────────────
    _progress("📊 データを集計中...")
    word_counter = Counter()
    bigram_counter = Counter()
    trigram_counter = Counter()
    format_counter = Counter()
    format_samples: dict[str, list] = defaultdict(list)

    for post in all_liked_posts:
        text = post.get('full_text', '') or post.get('text', '')
        if not text or len(text) < 10:
            continue

        # 単語・n-gram抽出
        words = _extract_words(text)
        word_counter.update(words)
        if len(words) >= 2:
            bigram_counter.update(_extract_ngrams(words, 2))
        if len(words) >= 3:
            trigram_counter.update(_extract_ngrams(words, 3))

        # 形式分類
        fmt = _classify_format(text)
        format_counter[fmt] += 1

        # サンプル収集（形式別最大15件）
        if len(format_samples[fmt]) < 15:
            format_samples[fmt].append({
                'text': text[:280],
                'likes': post.get('favorite_count', 0),
                'retweets': post.get('retweet_count', 0),
                'author': post.get('user', {}).get('screen_name', ''),
            })

    # ─── STEP 4: 結果整形 ────────────────────────────────────
    result.top_keywords = word_counter.most_common(40)
    # n-gramは最低3回以上出現したものだけ（ノイズ除去）
    result.bigrams = [(p, c) for p, c in bigram_counter.most_common(30) if c >= 3]
    result.trigrams = [(p, c) for p, c in trigram_counter.most_common(20) if c >= 3]
    result.top_accounts = liked_account_counter.most_common(15)
    result.format_dist = dict(format_counter)
    result.sample_posts = dict(format_samples)
    result.estimated_cost_jpy = client.estimated_cost_jpy

    _progress(f"🎉 完了！（推定コスト: ¥{result.estimated_cost_jpy:.1f}）")
    return result
