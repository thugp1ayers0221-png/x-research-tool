"""X アナライザー - 目的ベースUI"""
import os
import re
from dotenv import load_dotenv
load_dotenv()

import streamlit as st

import io
import csv

from audience_analyzer import analyze_audience, AudienceResult
from kii_analyzer import analyze_brain_seo, BrainSEOResult, WORD_CLUSTERS, SEARCH_SEEDS
from account_analyzer import analyze_account, AccountResult
from post_analyzer import analyze_post, PostResult
from neta_analyzer import analyze_neta, NetaResult
from article_analyzer import analyze_articles, ArticleResult
from persona_analyzer import analyze_persona, PersonaResult
from competitor_analyzer import analyze_competitors, CompetitorResult
try:
    from deep_search import deep_search, DeepSearchResult
    _deep_search_ok = True
except ImportError:
    _deep_search_ok = False

# ─── ページ設定 ──────────────────────────────────────────────
st.set_page_config(
    page_title="X アナライザー",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  /* PC全振りレイアウト */
  .block-container { max-width: 1400px !important; padding: 1.5rem 2rem !important; }

  [data-testid="metric-container"] {
    background: #f8f9fa;
    border: 1px solid #e0e3e8;
    border-radius: 8px;
    padding: 10px 16px;
  }
  [data-testid="metric-container"]:hover { background: #f0f4ff; border-color: #4a90d9; }

  h1 { font-size: 1.4rem !important; font-weight: 700 !important; margin-bottom: 0.2rem !important; }
  h4 { font-size: 1.0rem !important; font-weight: 600 !important; margin-top: 0.8rem !important; }

  section[data-testid="stSidebar"] { width: 0 !important; }
  .stTabs [data-baseweb="tab"] { font-size: 1.0rem; font-weight: 600; padding: 8px 20px; }
  .stTabs [data-baseweb="tab-list"] { gap: 4px; }

  /* フォームの余白を詰める */
  .stForm { border: 1px solid #e0e3e8 !important; border-radius: 10px !important; padding: 1rem !important; }
  div[data-testid="stVerticalBlock"] > div { gap: 0.4rem; }

  /* バーグラフの見た目改善 */
  .bar-wrap { margin-bottom: 4px; }

  /* expanderをコンパクトに */
  .streamlit-expanderHeader { font-size: 0.9rem !important; padding: 6px 10px !important; }

  /* 類似アカウントカード */
  .similar-card {
    border: 1px solid #e0e3e8;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
    background: #fafbfc;
  }
  .similar-card:hover { background: #f0f4ff; border-color: #4a90d9; }

  /* dividerの余白を詰める */
  hr { margin: 0.8rem 0 !important; }

  /* caption文字を少し大きく */
  .stCaption { font-size: 0.82rem !important; }
</style>
""", unsafe_allow_html=True)

api_key = os.getenv("SOCIALDATA_API_KEY", "")


def _fmt_cost(jpy: float) -> str:
    """コスト表示: 1円未満は '< 1' と表示（0円誤表示を防ぐ）"""
    if jpy <= 0:
        return "0"
    if jpy < 1:
        return "< 1"
    return f"{jpy:.0f}"


def _make_csv(rows: list, headers: list[str]) -> bytes:
    """CSV バイト列を生成（BOM付きUTF-8）"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")

st.title("🔍 X アナライザー")
if not api_key:
    st.error("⚠️ SOCIALDATA_API_KEY が未設定です。`.env` ファイルに設定してください。")

if "session_total_calls" not in st.session_state:
    st.session_state.session_total_calls = 0
if "session_total_cost_jpy" not in st.session_state:
    st.session_state.session_total_cost_jpy = 0.0

if st.session_state.session_total_cost_jpy > 0:
    st.info(f"💰 このセッション累計: {st.session_state.session_total_calls}コール / 約¥{st.session_state.session_total_cost_jpy:.0f}")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "⚔️ 競合分析",
    "👤 アカウント分析",
    "💡 スタイル分析",
    "🧬 ペルソナ調査",
    "🔍 バズ探し",
    "📰 記事リサーチ",
    "🎯 投稿分析",
])


# ════════════════════════════════════════════════════════════
# TAB 5: バズ探し
# ════════════════════════════════════════════════════════════
with tab5:
    st.markdown("#### キーワードを入れて分析ボタンを押すだけ")
    st.caption("バズ投稿の検索 → コメント・引用RTの生の声 → ターゲットのニーズ → ネタ候補 まで自動で実行します")

    b_col1, b_col2, b_col3 = st.columns([3, 1, 1])
    with b_col1:
        buzz_keyword = st.text_input("キーワード", placeholder="例: 行動経済学, 副業, マーケティング")
    with b_col2:
        b_min_faves = st.number_input("最低いいね数", min_value=50, value=300, step=50)
    with b_col3:
        b_days = st.selectbox("期間", [7, 14, 30, 60, 90, 180, 270, 540], index=2, format_func=lambda d: f"直近{d}日")

    with st.expander("⚙️ 詳細設定"):
        dc1, dc2 = st.columns(2)
        with dc1:
            b_max_posts = st.selectbox("バズ投稿数", [5, 10, 20, 50, 100, 200, 500, 1000], index=1)
        with dc2:
            b_max_comments = st.selectbox("コメント取得数/投稿", [20, 50, 100], index=1)

    # 検索: max_posts×3件をページ20件単位で取得 + コメント: 1コール/投稿（初回・キャッシュなし）
    _b_search_calls = (b_max_posts * 3 + 19) // 20
    _b_api_calls = _b_search_calls + b_max_posts
    _b_cost_jpy = _b_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_b_api_calls:,}回（検索{_b_search_calls}回＋コメント{b_max_posts}回）／ 推定コスト: 約{_fmt_cost(_b_cost_jpy)}円")

    b_submitted = st.button("🔍 バズ探し開始", use_container_width=True, type="primary", key="btn_buzz")

    if b_submitted:
        if not buzz_keyword:
            st.error("キーワードを入力してください")
            st.stop()

        b_prog = st.progress(0, text="準備中... (目安: 30秒〜2分)")

        def on_b_prog(stage, count, message):
            pct = 0.2 if stage == "search" else min(0.2 + count / max(b_max_posts, 1) * 0.8, 1.0)
            b_prog.progress(pct, text=message)

        try:
            b_result = analyze_audience(
                api_key=api_key,
                seed_keyword=buzz_keyword,
                min_faves=b_min_faves,
                max_seed_posts=b_max_posts,
                max_comments_per_post=b_max_comments,
                days=b_days,
                progress_callback=on_b_prog,
            )
            b_prog.empty()
            st.session_state["b_result"] = b_result
            st.session_state.session_total_calls += b_result.api_calls
            st.session_state.session_total_cost_jpy += b_result.cost_jpy
            st.rerun()
        except Exception as e:
            b_prog.empty()
            st.error(f"エラー: {e}")

    if "b_result" in st.session_state:
        br: AudienceResult = st.session_state["b_result"]

        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("バズ投稿数", f"{br.seed_posts_count}件")
        m2.metric("収集コメント数", f"{br.comments_analyzed}件")
        m3.metric("APIコール数", f"{br.api_calls}回")
        m4.metric("推定コスト", f"約{_fmt_cost(br.cost_jpy)}円")

        st.divider()
        left, right = st.columns(2)

        with left:
            st.markdown("#### 💬 ターゲットの頻出ワード")
            if br.top_keywords:
                max_c = br.top_keywords[0][1]
                for word, count in br.top_keywords[:15]:
                    bar = int(count / max_c * 100)
                    st.markdown(
                        f"`{word}` **{count}回** "
                        f"<div style='background:#3498db;height:5px;width:{bar}%;border-radius:3px;margin-bottom:5px'></div>",
                        unsafe_allow_html=True
                    )

            st.markdown("#### 😟 悩み・ニーズ")
            if br.pain_points:
                for w, c in br.pain_points[:8]:
                    st.markdown(f"- **{w}** ({c}件)")
            else:
                st.caption("データなし")

        with right:
            st.markdown("#### ❓ ターゲットが聞いている質問")
            if br.questions:
                for q in br.questions[:10]:
                    st.markdown(f"- {q}")
            else:
                st.caption("質問文が検出されませんでした")

            st.markdown("#### 🏷️ よく使われるハッシュタグ")
            if br.top_hashtags:
                st.markdown(" ".join([f"`#{t}`" for t, _ in br.top_hashtags[:12]]))

        # バズ投稿サンプル
        if br.seed_posts:
            st.divider()
            st.markdown("#### 🔥 分析対象のバズ投稿")
            if "buzz_labels" not in st.session_state:
                st.session_state.buzz_labels = {}

            for p in br.seed_posts:
                post_key = p['url']
                col_info, col_label = st.columns([5, 2])
                with col_info:
                    eng_rate = f"{p['likes']/p['views']*100:.2f}%" if p.get('views', 0) > 0 else "—"
                    st.markdown(
                        f"❤️{p['likes']:,} 🔁{p['retweets']:,} 👁{p['views']/1000:.1f}k "
                        f"**ER:{eng_rate}** &nbsp; [@{p['author']}]({p['url']})  \n"
                        f"{p['text']}",
                        unsafe_allow_html=True,
                    )
                with col_label:
                    label = st.radio(
                        "ラベル",
                        ["未分類", "✅ 使う", "📌 あとで"],
                        key=f"label_{post_key}",
                        horizontal=True,
                        index=["未分類", "✅ 使う", "📌 あとで"].index(
                            st.session_state.buzz_labels.get(post_key, "未分類")
                        )
                    )
                    st.session_state.buzz_labels[post_key] = label

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button(f"👤 アカウント分析へ", key=f"to_ac_{post_key}"):
                        st.session_state["ac_prefill"] = p['author']
                        st.info(f"「アカウント分析」タブで @{p['author']} を分析できます")
                with btn_col2:
                    if st.button(f"💡 スタイル分析へ", key=f"to_neta_{post_key}"):
                        st.session_state["neta_prefill"] = p['author']
                        st.info(f"「スタイル分析」タブで @{p['author']} を分析できます")

                st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

            labeled_posts = [
                p for p in br.seed_posts
                if st.session_state.buzz_labels.get(p['url'], "未分類") != "未分類"
            ]
            if labeled_posts:
                st.download_button(
                    "📥 ラベル付き投稿CSV",
                    _make_csv(
                        [[p["url"], p["author"], p["likes"], p["views"],
                          st.session_state.buzz_labels.get(p['url'], "未分類"), p["text"]]
                         for p in labeled_posts],
                        ["URL", "著者", "いいね", "インプ", "ラベル", "テキスト"],
                    ),
                    file_name="labeled_buzz_posts.csv", mime="text/csv",
                )

        st.divider()
        st.markdown("#### ✍️ ネタ候補（バズる型×キーワード）")
        for i, t in enumerate(br.topic_suggestions):
            col_text, col_btn = st.columns([8, 1])
            col_text.markdown(f"**{i+1}.** {t}")
            col_btn.code(t, language=None)

        with st.expander("💬 コメントのサンプル（生の声）"):
            for c in br.raw_comments[:20]:
                st.caption(c)
                st.markdown("---")

        # CSV
        st.divider()
        _b_csv_cols = st.columns(3)
        with _b_csv_cols[0]:
            if br.seed_posts:
                st.download_button(
                    "📥 バズ投稿CSV",
                    _make_csv(
                        [[p["url"], p["author"], p["likes"], p["retweets"], p["views"], p["text"]] for p in br.seed_posts],
                        ["URL", "著者", "いいね", "RT", "インプレッション", "テキスト"],
                    ),
                    file_name="buzz_posts.csv", mime="text/csv",
                )
        with _b_csv_cols[1]:
            if br.top_keywords:
                st.download_button(
                    "📥 頻出ワードCSV",
                    _make_csv(br.top_keywords, ["キーワード", "出現回数"]),
                    file_name="buzz_keywords.csv", mime="text/csv",
                )
        with _b_csv_cols[2]:
            if br.topic_suggestions:
                st.download_button(
                    "📥 ネタ候補CSV",
                    _make_csv([[t] for t in br.topic_suggestions], ["ネタ候補"]),
                    file_name="buzz_neta.csv", mime="text/csv",
                )

# ════════════════════════════════════════════════════════════
# TAB 2: アカウント分析
# ════════════════════════════════════════════════════════════
with tab2:
    st.markdown("#### @usernameを入れて分析ボタンを押すだけ")
    st.caption("プロフィール → 投稿傾向 → フォロワー属性 → 類似アカウント まで自動で実行します")

    ac_col1, ac_col2, ac_col3 = st.columns([3, 1, 1])
    with ac_col1:
        ac_prefill = st.session_state.pop("ac_prefill", "")
        ac_handle = st.text_input("アカウント名", value=ac_prefill, placeholder="例: kii_analytics（@なし）")
    with ac_col2:
        ac_max_followers = st.selectbox("フォロワーサンプル数", [100, 200, 300], index=1)
    with ac_col3:
        ac_max_tweets = st.selectbox("投稿取得数（傾向分析）", [60, 100, 200, 300], index=1)

    _ac_api_calls = 1 + ac_max_tweets // 20 + ac_max_followers // 20 + 1
    _ac_cost_jpy = _ac_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_ac_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_ac_cost_jpy)}円")

    ac_submitted = st.button("👤 アカウント分析を開始", use_container_width=True, type="primary", key="btn_account")

    if ac_submitted:
        if not ac_handle:
            st.error("アカウント名を入力してください")
            st.stop()

        import re as _re
        _url_match = _re.search(r"x\.com/([A-Za-z0-9_]+)", ac_handle)
        handle = _url_match.group(1) if _url_match else ac_handle.lstrip("@")
        ac_prog = st.progress(0, text="準備中... (目安: 30秒〜1分)")

        def on_ac_prog(pct: float, msg: str):
            ac_prog.progress(pct, text=msg)

        try:
            ac_result = analyze_account(
                api_key=api_key,
                handle=handle,
                max_followers=ac_max_followers,
                max_tweets=ac_max_tweets,
                progress_callback=on_ac_prog,
            )
            ac_prog.empty()
            st.session_state["ac_result"] = ac_result
            st.session_state.session_total_calls += ac_result.api_calls
            st.session_state.session_total_cost_jpy += ac_result.cost_jpy
            st.rerun()
        except Exception as e:
            ac_prog.empty()
            st.error(f"エラー: {e}")

    if "ac_result" in st.session_state:
        ac: AccountResult = st.session_state["ac_result"]

        st.divider()

        # ── プロフィールサマリー（6列）
        pc1, pc2, pc3, pc4, pc5, pc6 = st.columns(6)
        pc1.metric("フォロワー", f"{ac.followers_count:,}")
        pc2.metric("フォロー", f"{ac.following_count:,}")
        pc3.metric("総投稿数", f"{ac.tweet_count:,}")
        pc4.metric("平均いいね", f"{ac.tweet_analysis.get('avg_likes', 0):.0f}")
        pc5.metric("APIコール数", f"{ac.api_calls}回")
        pc6.metric("推定コスト", f"約{_fmt_cost(ac.cost_jpy)}円")

        st.info(f"**@{ac.handle}** ({ac.name})　{ac.bio if ac.bio else ''}")

        st.divider()

        # ── 3カラムレイアウト（投稿傾向 / フォロワー属性 / 類似アカウント）
        col_tweet, col_follower, col_similar = st.columns([2, 2, 2])

        # 投稿傾向
        ta = ac.tweet_analysis
        with col_tweet:
            st.markdown("#### 📊 投稿傾向")
            if ta:
                tm1, tm2, tm3 = st.columns(3)
                tm1.metric("平均RT", f"{ta.get('avg_rts', 0):.1f}")
                tm2.metric("平均閲覧数", f"{ta.get('avg_views', 0)/1000:.1f}k")
                tm3.metric("バズ率", f"{ta.get('buzz_rate', 0)*100:.0f}%")

                st.markdown("**頻出ワード**")
                if ta.get("top_keywords"):
                    st.markdown(" ".join([f"`{w}`" for w, _ in ta["top_keywords"][:12]]))

                st.markdown("**ハッシュタグ**")
                if ta.get("top_hashtags"):
                    st.markdown(" ".join([f"`#{h}`" for h, _ in ta["top_hashtags"][:8]]))

                st.markdown("**バズ投稿 TOP5**")
                for p in ta.get("top_posts", [])[:5]:
                    er = f"{p['likes']/p['views']*100:.2f}%" if p.get('views', 0) > 0 else "—"
                    st.markdown(
                        f"❤️{p['likes']:,} 🔁{p['rts']:,} 👁{p['views']/1000:.1f}k **ER:{er}** &nbsp; "
                        f"[{p['text'][:40]}...]({p['url']})",
                        unsafe_allow_html=True
                    )

                st.markdown("**投稿が多い時間帯（JST）**")
                if ta.get("posting_hours"):
                    hours_str = " / ".join([f"`{h}時`({c}件)" for h, c in ta["posting_hours"][:5]])
                    st.markdown(hours_str)
            else:
                st.caption("投稿データなし")

        # フォロワー属性
        fa = ac.follower_analysis
        with col_follower:
            st.markdown(f"#### 👥 フォロワー属性（{fa.get('total', 0)}件）")
            if fa:
                st.markdown("**規模分布**")
                tier = fa.get("tier", {})
                total_tier = sum(tier.values()) or 1
                for label, count in tier.items():
                    pct = count / total_tier * 100
                    st.markdown(
                        f"`{label}` {pct:.0f}% ({count}人)"
                        f"<div style='background:#2ecc71;height:5px;width:{pct:.0f}%;border-radius:3px;margin-bottom:4px'></div>",
                        unsafe_allow_html=True
                    )
                if fa.get("verified_count"):
                    st.caption(f"✅ 認証アカウント {fa['verified_count']}人含む")

                st.markdown("**ジャンル分布**")
                genre = fa.get("genre", {})
                total_genre = sum(genre.values()) or 1
                for label, count in list(genre.items())[:6]:
                    pct = count / total_genre * 100
                    st.markdown(
                        f"`{label}` {pct:.0f}%"
                        f"<div style='background:#9b59b6;height:5px;width:{pct:.0f}%;border-radius:3px;margin-bottom:4px'></div>",
                        unsafe_allow_html=True
                    )

                st.markdown("**bioキーワード**")
                if fa.get("bio_keywords"):
                    st.markdown(" ".join([f"`{w}`" for w, _ in fa["bio_keywords"][:16]]))
            else:
                st.caption("フォロワーデータなし")

        # 類似アカウント
        with col_similar:
            st.markdown("#### 🔗 類似アカウント")
            if ac.similar_accounts:
                _kws = [w for w, _ in (ac.tweet_analysis.get("top_keywords") or [])[:30]]
                for sim in ac.similar_accounts[:8]:
                    vmark = "✅ " if sim.get("verified") else ""
                    # 投稿テーマ一致（メイン）
                    theme = sim.get("theme_overlap") or []
                    # bio一致（サブ）
                    bio_lower = sim['bio'].lower()
                    bio_matched = [w for w in _kws if w.lower() in bio_lower and w not in theme]
                    if theme:
                        reason = "投稿テーマ: " + "　".join(f"`{w}`" for w in theme[:4])
                        if bio_matched:
                            reason += "　bio: " + "　".join(f"`{w}`" for w in bio_matched[:2])
                    elif bio_matched:
                        reason = "bio: " + "　".join(f"`{w}`" for w in bio_matched[:4])
                    else:
                        reason = "<span style='color:#aaa'>X内部アルゴリズムによる判定</span>"
                    st.markdown(
                        f"{vmark}**[{sim['name']}](https://x.com/{sim['handle']})** `@{sim['handle']}`  \n"
                        f"👥 {sim['followers']:,}  \n"
                        f"<span style='color:#666;font-size:0.82rem'>{sim['bio'][:55]}</span>  \n"
                        f"<span style='font-size:0.78rem;color:#888'>類似理由: {reason}</span>",
                        unsafe_allow_html=True
                    )
                    st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
            else:
                st.caption("類似アカウントが見つかりませんでした")

        # CSV
        st.divider()
        _ac_csv_cols = st.columns(3)
        with _ac_csv_cols[0]:
            _top_posts = (ac.tweet_analysis or {}).get("top_posts") or []
            if _top_posts:
                st.download_button(
                    "📥 TOP投稿CSV",
                    _make_csv(
                        [[p["url"], p["likes"], p["rts"], p["views"], p["text"]] for p in _top_posts],
                        ["URL", "いいね", "RT", "インプレッション", "テキスト"],
                    ),
                    file_name="account_top_posts.csv", mime="text/csv",
                )
        with _ac_csv_cols[1]:
            _kws2 = (ac.tweet_analysis or {}).get("top_keywords") or []
            if _kws2:
                st.download_button(
                    "📥 頻出ワードCSV",
                    _make_csv(_kws2, ["キーワード", "出現回数"]),
                    file_name="account_keywords.csv", mime="text/csv",
                )
        with _ac_csv_cols[2]:
            if ac.similar_accounts:
                st.download_button(
                    "📥 類似アカウントCSV",
                    _make_csv(
                        [[s["handle"], s["name"], s["followers"], s["bio"]] for s in ac.similar_accounts],
                        ["handle", "名前", "フォロワー数", "bio"],
                    ),
                    file_name="account_similar.csv", mime="text/csv",
                )


# ════════════════════════════════════════════════════════════
# TAB 7: 投稿分析
# ════════════════════════════════════════════════════════════
with tab7:
    st.markdown("#### 投稿URLを入れて分析ボタンを押すだけ")
    st.caption("RTした人の属性 → 引用RTの反応パターン → コメントの声 → 「誰に届いたか」を可視化します")

    p_url = st.text_input(
        "投稿URL",
        placeholder="例: https://x.com/kii_analytics/status/1234567890"
    )

    with st.expander("⚙️ 詳細設定"):
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            p_max_rt = st.selectbox("RTした人の最大取得数", [50, 100, 200], index=1)
        with pc2:
            p_max_quotes = st.selectbox("引用RTの最大取得数", [30, 50, 100], index=1)
        with pc3:
            p_max_comments = st.selectbox("コメントの最大取得数", [30, 50, 100], index=1)

    _p_api_calls = 1 + p_max_rt // 20 + p_max_quotes // 20 + 1  # コメントは get_tweet_comments で1コール固定
    _p_cost_jpy = _p_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_p_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_p_cost_jpy)}円")

    p_submitted = st.button("🎯 投稿を分析", use_container_width=True, type="primary", key="btn_post")

    if p_submitted:
        if not p_url:
            st.error("投稿URLを入力してください")
            st.stop()

        p_prog = st.progress(0, text="準備中... (目安: 30秒〜1分)")

        def on_p_prog(pct: float, msg: str):
            p_prog.progress(pct, text=msg)

        try:
            p_result = analyze_post(
                api_key=api_key,
                tweet_url=p_url,
                max_retweeters=p_max_rt,
                max_quotes=p_max_quotes,
                max_comments=p_max_comments,
                progress_callback=on_p_prog,
            )
            p_prog.empty()
            st.session_state["p_result"] = p_result
            st.session_state.session_total_calls += p_result.api_calls
            st.session_state.session_total_cost_jpy += p_result.cost_jpy
            st.rerun()
        except Exception as e:
            p_prog.empty()
            st.error(f"エラー: {e}")

    if "p_result" in st.session_state:
        pr: PostResult = st.session_state["p_result"]

        st.divider()

        # 投稿サマリー
        st.markdown(f"#### 分析対象投稿")
        st.info(f"[{pr.text or '（テキスト取得なし）'}]({pr.url})")
        pm1, pm2, pm3, pm4, pm5 = st.columns(5)
        pm1.metric("❤️ いいね", f"{pr.likes:,}")
        pm2.metric("🔁 RT", f"{pr.retweets:,}")
        pm3.metric("💬 引用RT", f"{pr.quotes:,}")
        pm4.metric("💭 コメント", f"{pr.replies:,}")
        pm5.metric("👁 閲覧数", f"{pr.views:,}")

        st.divider()

        col_l, col_r = st.columns(2)

        with col_l:
            # RTした人の属性
            ra = pr.retweeter_analysis
            st.markdown(f"#### 🔁 RTした人の属性（{ra.get('total', 0)}人）")
            if ra and ra.get("total", 0) > 0:
                st.markdown("**フォロワー規模**")
                tier = ra.get("tier", {})
                total_t = sum(tier.values()) or 1
                for label, count in tier.items():
                    pct = count / total_t * 100
                    st.markdown(
                        f"`{label}` {pct:.0f}% ({count}人)"
                        f"<div style='background:#e74c3c;height:5px;width:{pct:.0f}%;border-radius:3px;margin-bottom:5px'></div>",
                        unsafe_allow_html=True
                    )
                if ra.get("verified_count"):
                    st.markdown(f"✅ 認証アカウント: **{ra['verified_count']}人**")

                st.markdown("**ジャンル分布**")
                genre = ra.get("genre", {})
                for label, count in list(genre.items())[:6]:
                    st.markdown(f"- {label}: {count}人")

                st.markdown("**RTした人のbioワード**")
                if ra.get("bio_keywords"):
                    st.markdown(" ".join([f"`{w}`" for w, _ in ra["bio_keywords"][:15]]))
            else:
                st.caption("RTデータが取得できませんでした")

            # コメント
            ca = pr.comment_analysis
            st.markdown(f"#### 💭 コメントの声（{ca.get('count', 0)}件）")
            if ca and ca.get("count", 0) > 0:
                st.markdown("**頻出ワード**")
                if ca.get("top_keywords"):
                    st.markdown(" ".join([f"`{w}`" for w, _ in ca["top_keywords"][:12]]))
                if ca.get("pain_points"):
                    st.markdown("**悩み・ニーズ**")
                    for w, c in ca["pain_points"][:5]:
                        st.markdown(f"- {w} ({c}件)")
                if ca.get("questions"):
                    st.markdown("**聞かれた質問**")
                    for q in ca["questions"][:5]:
                        st.markdown(f"- {q}")
                with st.expander("💬 コメントサンプル"):
                    for c in ca.get("samples", [])[:10]:
                        st.caption(c)
                        st.markdown("---")
            else:
                st.caption("コメントデータが取得できませんでした")

        with col_r:
            # 引用RTの分析
            qa = pr.quote_analysis
            st.markdown(f"#### 💬 引用RTの反応パターン（{qa.get('count', 0)}件）")
            if qa and qa.get("count", 0) > 0:
                st.markdown("**引用RTで使われたワード**")
                if qa.get("top_keywords"):
                    max_qc = qa["top_keywords"][0][1] if qa["top_keywords"] else 1
                    for w, c in qa["top_keywords"][:12]:
                        bar = int(c / max_qc * 100)
                        st.markdown(
                            f"`{w}` **{c}回** "
                            f"<div style='background:#f39c12;height:5px;width:{bar}%;border-radius:3px;margin-bottom:5px'></div>",
                            unsafe_allow_html=True
                        )

                if qa.get("pain_points"):
                    st.markdown("**引用RTに含まれる悩み**")
                    for w, c in qa["pain_points"][:5]:
                        st.markdown(f"- {w} ({c}件)")

                if qa.get("questions"):
                    st.markdown("**引用RTで出た質問**")
                    for q in qa["questions"][:5]:
                        st.markdown(f"- {q}")

                with st.expander("💬 引用RTのサンプル"):
                    for q in qa.get("samples", [])[:10]:
                        st.caption(q)
                        st.markdown("---")
            else:
                st.caption("引用RTデータが取得できませんでした")

        # CSV
        st.divider()
        _p_csv_cols = st.columns(3)
        with _p_csv_cols[0]:
            _qt_samples = (pr.quote_analysis or {}).get("samples") or []
            if _qt_samples:
                st.download_button(
                    "📥 引用RTサンプルCSV",
                    _make_csv([[s] for s in _qt_samples], ["引用RTテキスト"]),
                    file_name="post_quotes.csv", mime="text/csv",
                )
        with _p_csv_cols[1]:
            _cm_samples = (pr.comment_analysis or {}).get("samples") or []
            if _cm_samples:
                st.download_button(
                    "📥 コメントサンプルCSV",
                    _make_csv([[s] for s in _cm_samples], ["コメントテキスト"]),
                    file_name="post_comments.csv", mime="text/csv",
                )
        with _p_csv_cols[2]:
            _qt_kws = (pr.quote_analysis or {}).get("top_keywords") or []
            if _qt_kws:
                st.download_button(
                    "📥 頻出ワードCSV",
                    _make_csv(_qt_kws, ["キーワード", "出現回数"]),
                    file_name="post_keywords.csv", mime="text/csv",
                )


# ════════════════════════════════════════════════════════════
# TAB 3: スタイル分析
# ════════════════════════════════════════════════════════════
with tab3:
    st.markdown("#### 参考にしたいアカウントを入れて分析ボタンを押すだけ")
    st.caption("そのアカウントの投稿傾向 → トピッククラスター分析 → 「このアカウントが好むコンテンツ＝ネタ候補」を抽出します")

    n_col1, n_col2 = st.columns([3, 1])
    with n_col1:
        neta_prefill = st.session_state.pop("neta_prefill", "")
        n_handle = st.text_input("アカウント名", value=neta_prefill, placeholder="例: competitor_account（@なし）")
    with n_col2:
        n_max_posts = st.selectbox("取得件数", [100, 200, 500, 1000], index=1)

    _n_api_calls = 1 + n_max_posts // 20
    _n_cost_jpy = _n_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_n_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_n_cost_jpy)}円")

    n_submitted = st.button("💡 スタイルを分析", use_container_width=True, type="primary", key="btn_neta")

    if n_submitted:
        if not n_handle:
            st.error("アカウント名を入力してください")
            st.stop()

        # URL形式（https://x.com/username）でも受け付ける
        import re as _re
        _url_match = _re.search(r"x\.com/([A-Za-z0-9_]+)", n_handle)
        handle_n = _url_match.group(1) if _url_match else n_handle.lstrip("@")
        n_prog = st.progress(0, text="準備中... (目安: 15秒〜1分)")

        def on_n_prog(pct: float, msg: str):
            n_prog.progress(pct, text=msg)

        try:
            n_result = analyze_neta(
                api_key=api_key,
                handle=handle_n,
                max_posts=n_max_posts,
                progress_callback=on_n_prog,
            )
            n_prog.empty()
            st.session_state["n_result"] = n_result
            st.session_state.session_total_calls += n_result.api_calls
            st.session_state.session_total_cost_jpy += n_result.cost_jpy
            st.rerun()
        except Exception as e:
            n_prog.empty()
            st.error(f"エラー: {e}")

    if "n_result" in st.session_state:
        nr: NetaResult = st.session_state["n_result"]

        st.divider()
        nm1, nm2, nm3 = st.columns(3)
        nm1.metric("分析した投稿数", f"{nr.post_count}件")
        nm2.metric("APIコール数", f"{nr.api_calls}回")
        nm3.metric("推定コスト", f"約{_fmt_cost(nr.cost_jpy)}円")

        st.divider()

        nl, nr_col = st.columns(2)

        with nl:
            st.markdown("#### 📊 トピッククラスター")
            st.caption("どのジャンルのコンテンツに反応しているか")
            if nr.topic_clusters:
                max_score = max(nr.topic_clusters.values()) or 1
                for cluster, score in nr.topic_clusters.items():
                    bar = int(score / max_score * 100)
                    st.markdown(
                        f"`{cluster}` スコア:{score}"
                        f"<div style='background:#e74c3c;height:6px;width:{bar}%;border-radius:3px;margin-bottom:6px'></div>",
                        unsafe_allow_html=True
                    )
            else:
                st.caption("クラスターデータなし")

            st.markdown("#### 🏷️ 頻出ハッシュタグ")
            if nr.top_hashtags:
                st.markdown(" ".join([f"`#{h}`" for h, _ in nr.top_hashtags[:12]]))

        with nr_col:
            st.markdown("#### 🔑 頻出ワード")
            if nr.top_keywords:
                max_kc = nr.top_keywords[0][1] if nr.top_keywords else 1
                for w, c in nr.top_keywords[:15]:
                    bar = int(c / max_kc * 100)
                    st.markdown(
                        f"`{w}` **{c}回** "
                        f"<div style='background:#3498db;height:5px;width:{bar}%;border-radius:3px;margin-bottom:5px'></div>",
                        unsafe_allow_html=True
                    )

        st.divider()
        st.markdown("#### ✍️ このアカウントの投稿傾向から生成したネタ候補")
        for i, t in enumerate(nr.neta_suggestions):
            col_text, col_btn = st.columns([8, 1])
            col_text.markdown(f"**{i+1}.** {t}")
            col_btn.code(t, language=None)

        with st.expander(f"📌 インプレッション高い投稿 TOP8（取得{nr.post_count}件の中から）"):
            for p in nr.sample_posts:
                st.markdown(
                    f"👁 {p.get('views', 0):,} &nbsp; ❤️ {p['likes']:,} &nbsp; [@{p['author']}]({p['url']})  \n"
                    f"{p['text']}"
                )
                st.markdown("---")

        # CSV
        st.divider()
        _n_csv_cols = st.columns(3)
        with _n_csv_cols[0]:
            if nr.neta_suggestions:
                st.download_button(
                    "📥 ネタ候補CSV",
                    _make_csv([[t] for t in nr.neta_suggestions], ["ネタ候補"]),
                    file_name="neta_suggestions.csv", mime="text/csv",
                )
        with _n_csv_cols[1]:
            if nr.top_keywords:
                st.download_button(
                    "📥 頻出ワードCSV",
                    _make_csv(nr.top_keywords, ["キーワード", "スコア"]),
                    file_name="neta_keywords.csv", mime="text/csv",
                )
        with _n_csv_cols[2]:
            if nr.sample_posts:
                st.download_button(
                    "📥 高インプ投稿CSV",
                    _make_csv(
                        [[p["url"], p.get("views", 0), p["likes"], p["text"]] for p in nr.sample_posts],
                        ["URL", "インプレッション", "いいね", "テキスト"],
                    ),
                    file_name="neta_top_posts.csv", mime="text/csv",
                )


# ════════════════════════════════════════════════════════════
# TAB 6: 記事リサーチ
# ════════════════════════════════════════════════════════════
with tab6:
    st.markdown("#### キーワード（任意）を入れて分析ボタンを押すだけ")
    st.caption("X Article を検索 → 本文・エンゲージメントを一括取得。トレンド記事のリサーチに使えます")

    ar_col1, ar_col2, ar_col3, ar_col4 = st.columns([3, 1, 1, 1])
    with ar_col1:
        ar_keyword = st.text_input("キーワード（空白でも可）", placeholder="例: マーケティング, 副業, AI")
    with ar_col2:
        ar_min_likes = st.number_input("最低いいね数", min_value=50, value=100, step=50)
    with ar_col3:
        ar_days = st.selectbox("期間", [7, 14, 30, 60, 90], index=2, format_func=lambda d: f"直近{d}日")
    with ar_col4:
        ar_max = st.selectbox("最大取得件数", [10, 20, 30], index=1)

    _ar_api_calls = 1 + ar_max * 2
    _ar_cost_jpy = _ar_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_ar_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_ar_cost_jpy)}円")

    ar_submitted = st.button("📰 記事を探す", use_container_width=True, type="primary", key="btn_article")

    if ar_submitted:
        ar_prog = st.progress(0, text="準備中...")

        def on_ar_prog(pct: float, msg: str):
            ar_prog.progress(pct, text=msg)

        try:
            ar_result = analyze_articles(
                api_key=api_key,
                keyword=ar_keyword,
                min_likes=ar_min_likes,
                days=ar_days,
                max_articles=ar_max,
                progress_callback=on_ar_prog,
            )
            ar_prog.empty()
            st.session_state["ar_result"] = ar_result
            st.session_state.session_total_calls += ar_result.api_calls
            st.session_state.session_total_cost_jpy += ar_result.cost_jpy
            st.rerun()
        except Exception as e:
            ar_prog.empty()
            st.error(f"エラー: {e}")

    if "ar_result" in st.session_state:
        ar: ArticleResult = st.session_state["ar_result"]

        st.divider()
        am1, am2, am3, am4 = st.columns(4)
        am1.metric("検索ヒット数", f"{ar.searched_count}件")
        am2.metric("記事取得数", f"{ar.articles_found}件")
        am3.metric("APIコール数", f"{ar.api_calls}回")
        am4.metric("推定コスト", f"約{_fmt_cost(ar.cost_jpy)}円")

        if not ar.articles:
            st.warning("記事が見つかりませんでした。キーワードや期間・いいね数を変えて試してください。")
        else:
            st.divider()

            # 記事一覧テーブル
            st.markdown(f"#### 📋 記事一覧（いいね順 / {len(ar.articles)}件）")
            header_cols = st.columns([3, 1, 1, 1, 1, 1, 2])
            header_cols[0].markdown("**タイトル**")
            header_cols[1].markdown("**❤️ いいね**")
            header_cols[2].markdown("**🔁 RT**")
            header_cols[3].markdown("**🔖 BM**")
            header_cols[4].markdown("**👁 閲覧**")
            header_cols[5].markdown("**👥 F数**")
            header_cols[6].markdown("**著者**")
            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

            for a in ar.articles:
                m = a["metrics"]
                row = st.columns([3, 1, 1, 1, 1, 1, 2])
                title_disp = (a["title"] or "（タイトルなし）")[:45]
                row[0].markdown(f"[{title_disp}]({a['tweet_url']})")
                row[1].markdown(f"{m['likes']:,}")
                row[2].markdown(f"{m['retweets']:,}")
                row[3].markdown(f"{m['bookmarks']:,}")
                row[4].markdown(f"{m['views']/1000:.1f}k" if m['views'] else "—")
                row[5].markdown(f"{a['author']['followers_count']:,}")
                row[6].markdown(f"@{a['author']['screen_name']}")

            st.divider()

            # 記事詳細（展開式）
            st.markdown("#### 📄 記事詳細")
            for i, a in enumerate(ar.articles):
                m = a["metrics"]
                title = a["title"] or "（タイトルなし）"
                label = f"{i+1}. {title[:60]}　❤️{m['likes']:,} 👁{m['views']/1000:.1f}k　@{a['author']['screen_name']}"
                with st.expander(label):
                    info_l, info_r = st.columns([2, 1])
                    with info_l:
                        if a["preview_text"]:
                            st.caption(a["preview_text"])
                        if a["text"]:
                            st.markdown(a["text"])
                        else:
                            st.caption("（本文取得できませんでした）")
                    with info_r:
                        dm1, dm2 = st.columns(2)
                        dm1.metric("❤️ いいね", f"{m['likes']:,}")
                        dm2.metric("🔁 RT", f"{m['retweets']:,}")
                        dm1.metric("🔖 BM", f"{m['bookmarks']:,}")
                        dm2.metric("👁 閲覧", f"{m['views']/1000:.1f}k" if m['views'] else "—")
                        st.markdown(f"**著者:** @{a['author']['screen_name']}  \n"
                                    f"👥 {a['author']['followers_count']:,}")
                        if a["author"]["description"]:
                            st.caption(a["author"]["description"][:80])
                        st.markdown(f"[ポストを開く]({a['tweet_url']})")

        # CSV
        st.divider()
        _ar_csv_cols = st.columns(2)
        with _ar_csv_cols[0]:
            if ar.articles:
                st.download_button(
                    "📥 記事一覧CSV",
                    _make_csv(
                        [[a["tweet_url"], a["title"] or "", a["metrics"]["likes"], a["metrics"]["retweets"], a["metrics"]["views"], a["author"]["screen_name"]] for a in ar.articles],
                        ["URL", "タイトル", "いいね", "RT", "インプレッション", "著者"],
                    ),
                    file_name="articles.csv", mime="text/csv",
                )


# ════════════════════════════════════════════════════════════
# TAB 4: ペルソナ調査
# ════════════════════════════════════════════════════════════
with tab4:
    st.markdown("#### ターゲット層の投稿行動からペルソナデータを収集")
    st.caption("プロフィールキーワードでユーザーをサンプリング → 投稿テキストを大量収集 → 興味・ペイン・インサイトの種を抽出します")

    # スライダーはform外でリアルタイム更新
    pa1, pa2 = st.columns(2)
    with pa1:
        p_users = st.slider("サンプルユーザー数", min_value=50, max_value=2000, value=100, step=50)
    with pa2:
        p_likes = st.slider("投稿収集数/人", min_value=20, max_value=500, value=40, step=20)

    _api_calls = p_users * (1 + p_likes // 20)
    _cost_jpy = _api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_cost_jpy)}円")
    if _cost_jpy > 500:
        st.warning(f"⚠️ 推定コストが高額です（約¥{_cost_jpy:.0f}）。設定を下げることを推奨します。")

    with st.form("persona_form"):
        pc1, pc2, pc3 = st.columns([4, 1, 1])
        with pc1:
            p_keywords_raw = st.text_input(
                "ターゲット像キーワード（カンマ区切り）",
                value="マーケター,SNS運用,個人事業主,経営者,メタ認知",
                placeholder="例: マーケター, SNS運用, 個人事業主, 経営者, メタ認知"
            )
        with pc2:
            p_min_f = st.number_input("フォロワー最小", min_value=100, value=1000, step=100)
        with pc3:
            p_max_f = st.number_input("フォロワー最大", min_value=1000, value=10000, step=1000)

        p_submitted = st.form_submit_button("🧬 ペルソナ調査を開始", use_container_width=True, type="primary")

    if p_submitted:
        bio_kws = [k.strip() for k in p_keywords_raw.split(",") if k.strip()]
        if not bio_kws:
            st.warning("キーワードを1つ以上入力してください")
        else:
            p_status = st.empty()
            p_log = st.empty()
            log_lines = []

            def on_persona_progress(msg: str):
                log_lines.append(msg)
                p_log.markdown("\n\n".join(log_lines[-6:]))

            p_status.info("🔄 収集中... 数分かかります（設定によっては10分以上）")
            try:
                pr = analyze_persona(
                    api_key=api_key,
                    bio_keywords=bio_kws,
                    min_followers=p_min_f,
                    max_followers=p_max_f,
                    target_users=p_users,
                    likes_per_user=p_likes,
                    progress_callback=on_persona_progress,
                )
                p_status.empty()
                p_log.empty()
                st.session_state["persona_result"] = pr
                st.session_state.session_total_calls += pr.user_count
                st.session_state.session_total_cost_jpy += pr.estimated_cost_jpy
                st.rerun()
            except Exception as e:
                p_status.empty()
                st.error(f"エラー: {e}")

    if "persona_result" in st.session_state:
        pr: PersonaResult = st.session_state["persona_result"]

        st.divider()

        # ─── サマリー ────────────────────────────────────────
        pm1, pm2, pm3, pm4 = st.columns(4)
        pm1.metric("サンプルユーザー数", f"{pr.user_count:,}人")
        pm2.metric("収集投稿数", f"{pr.like_count:,}件")
        pm3.metric("抽出キーワード", f"{len(pr.top_keywords)}語")
        pm4.metric("推定コスト", f"約{_fmt_cost(pr.estimated_cost_jpy)}円")

        if pr.errors:
            for err in pr.errors:
                st.warning(err)

        # ─── CSV書き出し ──────────────────────────────────────
        st.divider()

        dl1, dl2, dl3, dl4 = st.columns(4)
        with dl1:
            if pr.top_keywords:
                st.download_button(
                    "📥 キーワードCSV",
                    _make_csv(pr.top_keywords, ["キーワード", "出現回数"]),
                    file_name="persona_keywords.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        with dl2:
            if pr.bigrams or pr.trigrams:
                ngram_rows = [(p, c, "2gram") for p, c in pr.bigrams] + [(p, c, "3gram") for p, c in pr.trigrams]
                st.download_button(
                    "📥 フレーズCSV",
                    _make_csv(ngram_rows, ["フレーズ", "出現回数", "種別"]),
                    file_name="persona_phrases.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        with dl3:
            if pr.top_accounts:
                st.download_button(
                    "📥 収集アカウントCSV",
                    _make_csv(pr.top_accounts, ["アカウント", "投稿数"]),
                    file_name="persona_accounts.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        with dl4:
            # サンプル投稿の全データCSV
            all_posts_rows = []
            for fmt, posts in pr.sample_posts.items():
                for p in posts:
                    all_posts_rows.append([fmt, p.get("text", ""), p.get("likes", 0), p.get("retweets", 0), p.get("author", "")])
            if all_posts_rows:
                st.download_button(
                    "📥 サンプル投稿CSV",
                    _make_csv(all_posts_rows, ["形式", "投稿テキスト", "いいね", "RT", "著者"]),
                    file_name="persona_sample_posts.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

        if not pr.top_keywords:
            st.warning("データが収集できませんでした。キーワードや設定を変えて再試行してください。")
        else:
            st.divider()

            left_col, right_col = st.columns(2)

            # ─── 頻出キーワード ───────────────────────────────
            with left_col:
                st.markdown("#### 🔑 頻出キーワード TOP30")
                if pr.top_keywords:
                    max_cnt = pr.top_keywords[0][1] if pr.top_keywords else 1
                    for word, cnt in pr.top_keywords[:30]:
                        bar_pct = int(cnt / max_cnt * 100)
                        st.markdown(
                            f"<div class='bar-wrap'>"
                            f"<span style='display:inline-block;width:120px;font-weight:600'>{word}</span>"
                            f"<span style='display:inline-block;background:#4a90d9;height:14px;width:{bar_pct}%;border-radius:3px;vertical-align:middle'></span>"
                            f"<span style='margin-left:6px;color:#666;font-size:0.85em'>{cnt:,}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            # ─── コンテンツ形式分布 ───────────────────────────
            with right_col:
                st.markdown("#### 📝 ターゲット層の投稿形式")
                if pr.format_dist:
                    total_fmt = sum(pr.format_dist.values())
                    for fmt, cnt in sorted(pr.format_dist.items(), key=lambda x: -x[1]):
                        pct = cnt / total_fmt * 100 if total_fmt > 0 else 0
                        bar_w = int(pct)
                        st.markdown(
                            f"<div class='bar-wrap'>"
                            f"<span style='display:inline-block;width:110px;font-weight:600'>{fmt}</span>"
                            f"<span style='display:inline-block;background:#22c55e;height:14px;width:{bar_w}%;border-radius:3px;vertical-align:middle'></span>"
                            f"<span style='margin-left:6px;color:#666;font-size:0.85em'>{pct:.1f}% ({cnt:,}件)</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            st.divider()

            # ─── 頻出フレーズ（n-gram）────────────────────────
            bigram_col, trigram_col = st.columns(2)
            with bigram_col:
                st.markdown("#### 🔗 頻出フレーズ（2語）TOP20")
                st.caption("ターゲット投稿に繰り返し登場する2語の組み合わせ")
                if pr.bigrams:
                    max_bc = pr.bigrams[0][1] if pr.bigrams else 1
                    for phrase, cnt in pr.bigrams[:20]:
                        bar_pct = int(cnt / max_bc * 100)
                        st.markdown(
                            f"<div class='bar-wrap'>"
                            f"<span style='display:inline-block;width:140px;font-weight:600'>{phrase}</span>"
                            f"<span style='display:inline-block;background:#f59e0b;height:14px;width:{bar_pct}%;border-radius:3px;vertical-align:middle'></span>"
                            f"<span style='margin-left:6px;color:#666;font-size:0.85em'>{cnt:,}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("データが不足しています")

            with trigram_col:
                st.markdown("#### 🔗 頻出フレーズ（3語）TOP15")
                st.caption("より具体的な文脈・関心テーマが見えるフレーズ")
                if pr.trigrams:
                    max_tc = pr.trigrams[0][1] if pr.trigrams else 1
                    for phrase, cnt in pr.trigrams[:15]:
                        bar_pct = int(cnt / max_tc * 100)
                        st.markdown(
                            f"<div class='bar-wrap'>"
                            f"<span style='display:inline-block;width:160px;font-weight:600'>{phrase}</span>"
                            f"<span style='display:inline-block;background:#ef4444;height:14px;width:{bar_pct}%;border-radius:3px;vertical-align:middle'></span>"
                            f"<span style='margin-left:6px;color:#666;font-size:0.85em'>{cnt:,}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("データが不足しています")

            st.divider()

            # ─── よくいいねされるアカウント ───────────────────
            st.markdown("#### 👤 投稿数の多いアカウント TOP15")
            st.caption("ターゲット層のサンプルアカウント = 彼らのプロフィール・発信傾向の参考")
            if pr.top_accounts:
                acc_cols = st.columns(3)
                for i, (screen_name, cnt) in enumerate(pr.top_accounts[:15]):
                    acc_cols[i % 3].markdown(
                        f"**@{screen_name}** &nbsp; `{cnt}件投稿収集`  \n"
                        f"[Xで見る](https://x.com/{screen_name})"
                    )

            st.divider()

            # ─── サンプル投稿（形式別）───────────────────────
            st.markdown("#### 🗂 サンプル投稿（ターゲット層の実際の投稿）")
            st.caption("形式ごとにターゲット層の実際の投稿を表示します。彼らの言葉・関心・悩みを直接読んで分析に使ってください")
            for fmt, posts in pr.sample_posts.items():
                with st.expander(f"▶ {fmt}（{len(posts)}件）"):
                    for p in posts:
                        st.markdown(
                            f"❤️ {p['likes']:,} &nbsp; 🔁 {p['retweets']:,} &nbsp; "
                            f"[@{p['author']}](https://x.com/{p['author']})"
                        )
                        st.markdown(f"> {p['text'].replace(chr(10), ' ')}")
                        st.markdown("---")

        # ════════════════════════════════════════════════════
        # フレーズ深堀り検索
        # ════════════════════════════════════════════════════
        st.divider()
        st.markdown("#### 🔍 フレーズ深堀り検索")
        st.caption("調査で気になったフレーズを入力 → Claude が関連クエリを生成 → 3万インプレ以上の投稿を収集")

        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

        with st.form("deep_search_form"):
            ds_col1, ds_col2, ds_col3 = st.columns([4, 1, 1])
            with ds_col1:
                ds_phrase = st.text_input(
                    "起点フレーズ（頻出フレーズ・キーワードをコピペ）",
                    placeholder="例: デートの作法 / SNS 発信 / 思考力 鍛える"
                )
            with ds_col2:
                ds_min_views = st.number_input("最低インプレ数", min_value=10000, value=30000, step=10000)
            with ds_col3:
                ds_max = st.selectbox("最大取得件数", [20, 30, 50], index=1)

            ds_submitted = st.form_submit_button("🔍 深堀り検索", use_container_width=True, type="primary")

        if ds_submitted:
            if not ds_phrase.strip():
                st.warning("フレーズを入力してください")
            elif not anthropic_key:
                st.error("ANTHROPIC_API_KEY が未設定です")
            else:
                ds_status = st.empty()
                ds_log = st.empty()
                ds_log_lines = []

                def on_ds_progress(msg: str):
                    ds_log_lines.append(msg)
                    ds_log.markdown("\n\n".join(ds_log_lines[-5:]))

                ds_status.info("🔄 検索中...")

                # ペルソナ調査のキーワードを文脈として渡す
                ctx_kws = []
                if "persona_result" in st.session_state:
                    ctx_kws = [w for w, _ in st.session_state["persona_result"].top_keywords[:20]]

                try:
                    ds_result = deep_search(
                        api_key=api_key,
                        anthropic_key=anthropic_key,
                        seed_phrase=ds_phrase,
                        context_keywords=ctx_kws,
                        min_views=ds_min_views,
                        max_results=ds_max,
                        progress_callback=on_ds_progress,
                    )
                    ds_status.empty()
                    ds_log.empty()
                    st.session_state["ds_result"] = ds_result
                except Exception as e:
                    ds_status.empty()
                    st.error(f"エラー: {e}")

        if "ds_result" in st.session_state:
            ds: DeepSearchResult = st.session_state["ds_result"]

            dm1, dm2, dm3, dm4 = st.columns(4)
            dm1.metric("ヒット件数", f"{len(ds.posts)}件")
            dm2.metric("検索投稿数", f"{ds.total_searched:,}件")
            dm3.metric("LLMコスト", f"¥{ds.llm_cost_jpy:.2f}")
            dm4.metric("合計コスト", f"¥{ds.llm_cost_jpy + ds.api_cost_jpy:.1f}")

            if ds.generated_queries:
                with st.expander("🤖 Claudeが生成したクエリ"):
                    for q in ds.generated_queries:
                        st.markdown(f"- `{q}`")

            if not ds.posts:
                st.warning(f"{ds_min_views:,}インプレ以上の投稿が見つかりませんでした。インプレ数を下げるか、フレーズを変えて試してください。")
            else:
                st.markdown(f"#### 📊 結果（インプレ降順 / {len(ds.posts)}件）")
                for p in ds.posts:
                    with st.expander(
                        f"👁 {p['views']:,}  ❤️ {p['likes']:,}  🔁 {p['retweets']:,}  "
                        f"@{p['author']} — {p['text'][:50]}..."
                    ):
                        st.markdown(f"**マッチクエリ:** `{p['matched_query']}`")
                        st.markdown(f"> {p['text'].replace(chr(10), ' ')}")
                        mc1, mc2, mc3, mc4 = st.columns(4)
                        mc1.metric("👁 インプレ", f"{p['views']:,}")
                        mc2.metric("❤️ いいね", f"{p['likes']:,}")
                        mc3.metric("🔁 RT", f"{p['retweets']:,}")
                        mc4.metric("👥 フォロワー", f"{p['followers']:,}")
                        st.markdown(f"[投稿を開く]({p['url']})")


# ════════════════════════════════════════════════════════════
# TAB 1: 競合分析
# ════════════════════════════════════════════════════════════
with tab1:
    st.markdown("#### @usernameを入れて分析ボタンを押すだけ")
    st.caption("フォロワーが他にフォローしているアカウントを集計 → 同じオーディエンスを取り合っている競合を特定します")

    cm_col1, cm_col2 = st.columns([3, 1])
    with cm_col1:
        cm_handle = st.text_input(
            "アカウント名",
            placeholder="例: competitor_account（@なし、URLも可）",
            key="cm_handle"
        )
    with cm_col2:
        cm_max_followers = st.selectbox(
            "フォロワーサンプル数",
            [100, 200, 300, 500],
            index=1,
            key="cm_max_followers"
        )

    cm_pages = st.radio(
        "フォロー先取得ページ数（1ページ=20件）",
        [1, 2],
        index=0,
        horizontal=True,
        key="cm_pages"
    )

    with st.expander("⚙️ フォロワー数フィルタ（競合の規模感）"):
        filt_col1, filt_col2 = st.columns(2)
        with filt_col1:
            cm_min_ratio = st.slider(
                "最小フォロワー比率（対象の何倍以上）",
                min_value=0.05, max_value=1.0, value=0.1, step=0.05,
                key="cm_min_ratio"
            )
        with filt_col2:
            cm_max_ratio = st.slider(
                "最大フォロワー比率（対象の何倍以下）",
                min_value=1.0, max_value=20.0, value=10.0, step=0.5,
                key="cm_max_ratio"
            )

    _cm_api_calls = 1 + cm_max_followers // 20 + cm_max_followers * cm_pages
    _cm_cost_jpy = _cm_api_calls * 0.001 * 150
    st.caption(f"推定APIコール: 約{_cm_api_calls:,}回 ／ 推定コスト: 約{_fmt_cost(_cm_cost_jpy)}円")

    cm_submitted = st.button("⚔️ 競合を分析", use_container_width=True, type="primary", key="btn_competitor")

    if cm_submitted:
        if not cm_handle:
            st.error("アカウント名を入力してください")
            st.stop()

        import re as _re2
        _url_m = _re2.search(r"x\.com/([A-Za-z0-9_]+)", cm_handle)
        handle_cm = _url_m.group(1) if _url_m else cm_handle.lstrip("@")

        cm_prog = st.progress(0, text="準備中...")

        def on_cm_prog(pct: float, msg: str):
            cm_prog.progress(pct, text=msg)

        try:
            cm_result = analyze_competitors(
                api_key=api_key,
                handle=handle_cm,
                max_followers=cm_max_followers,
                following_pages=cm_pages,
                min_followers_ratio=cm_min_ratio,
                max_followers_ratio=cm_max_ratio,
                progress_callback=on_cm_prog,
            )
            cm_prog.empty()
            st.session_state["cm_result"] = cm_result
            st.session_state.session_total_calls += cm_result.api_calls
            st.session_state.session_total_cost_jpy += cm_result.cost_jpy
            st.rerun()
        except Exception as e:
            cm_prog.empty()
            st.error(f"エラー: {e}")

    if "cm_result" in st.session_state:
        cr: CompetitorResult = st.session_state["cm_result"]

        st.divider()

        # サマリー
        cm1, cm2, cm3, cm4 = st.columns(4)
        cm1.metric("対象フォロワー数", f"{cr.target_followers:,}")
        cm2.metric("サンプル数", f"{cr.sampled_followers:,}人")
        cm3.metric("APIコール数", f"{cr.api_calls}回")
        cm4.metric("推定コスト", f"約{_fmt_cost(cr.cost_jpy)}円")

        st.divider()

        if not cr.competitors:
            st.warning("競合アカウントが見つかりませんでした。フォロワー数フィルタの範囲を広げてみてください。")
        else:
            st.markdown(f"#### ⚔️ 競合アカウント TOP{len(cr.competitors)}（フォロワー重複率順）")
            st.caption(f"サンプルしたフォロワー{cr.sampled_followers}人のうち何人が各アカウントをフォローしているか")

            for i, c in enumerate(cr.competitors):
                with st.expander(
                    f"{i + 1}. @{c['screen_name']}　"
                    f"重複率 {c['overlap_pct']}%（{c['overlap_count']}/{cr.sampled_followers}人）　"
                    f"フォロワー {c['followers_count']:,}"
                ):
                    exp_l, exp_r = st.columns([3, 1])
                    with exp_l:
                        st.markdown(f"**{c['name']}**　[@{c['screen_name']}]({c['url']})")
                        st.markdown(c["description"] or "（bio なし）")
                    with exp_r:
                        st.metric("重複率", f"{c['overlap_pct']}%")
                        st.metric("フォロワー", f"{c['followers_count']:,}")

        # CSV
        st.divider()
        st.download_button(
            "📥 競合アカウントCSV",
            _make_csv(
                [[c["screen_name"], c["name"], c["followers_count"], c["overlap_count"], c["overlap_pct"], c["description"], c["url"]] for c in cr.competitors],
                ["handle", "名前", "フォロワー数", "重複人数", "重複率(%)", "bio", "URL"],
            ),
            file_name="competitors.csv", mime="text/csv",
        )
