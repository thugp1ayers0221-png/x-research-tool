"""SocialData API クライアント"""
import os
import time
import requests
from typing import Optional

SOCIALDATA_API_BASE = "https://api.socialdata.tools"


class SocialDataClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SOCIALDATA_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        self._call_count = 0
        self._cost_per_call = 0.0002  # USD per call

    @property
    def estimated_cost_usd(self) -> float:
        return self._call_count * self._cost_per_call

    @property
    def estimated_cost_jpy(self) -> float:
        return self.estimated_cost_usd * 150  # 1 USD = 150 JPY

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{SOCIALDATA_API_BASE}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        self._call_count += 1
        resp.raise_for_status()
        return resp.json()

    def search_tweets(self, query: str, next_cursor: str = None) -> dict:
        """ツイート検索 (ページネーション対応)"""
        params = {"query": query, "type": "Latest"}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get("/twitter/search", params)

    def get_article(self, tweet_id: str) -> dict:
        """記事の詳細を取得 (本文・エンゲージメント全部入り)"""
        return self._get(f"/twitter/article/{tweet_id}")

    def get_user_profile(self, screen_name: str) -> dict:
        """ユーザープロフィール取得"""
        return self._get(f"/twitter/user/{screen_name}")

    def get_user_followers(self, user_id: str, next_cursor: str = None) -> dict:
        """フォロワー一覧（ページネーション対応）"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/user/{user_id}/followers", params)

    def get_user_following(self, user_id: str, next_cursor: str = None) -> dict:
        """フォロイー一覧（ページネーション対応）"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/user/{user_id}/following", params)

    def get_user_likes(self, user_id: str, next_cursor: str = None) -> dict:
        """いいねした投稿一覧"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/user/{user_id}/likes", params)

    def get_user_tweets(self, user_id: str, next_cursor: str = None) -> dict:
        """ユーザーの投稿一覧"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/user/{user_id}/tweets", params)

    def get_user_similar(self, user_id: str) -> dict:
        """類似アカウント一覧"""
        return self._get(f"/twitter/user/{user_id}/similar")

    def get_tweet(self, tweet_id: str) -> dict:
        """ツイート詳細取得"""
        return self._get(f"/twitter/tweets/{tweet_id}")

    def get_tweet_retweeted_by(self, tweet_id: str, next_cursor: str = None) -> dict:
        """RTしたユーザー一覧"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/tweets/{tweet_id}/retweeted_by", params)

    def get_tweet_quotes(self, tweet_id: str, next_cursor: str = None) -> dict:
        """引用RT一覧"""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get(f"/twitter/tweets/{tweet_id}/quotes", params)

    def get_tweet_comments(self, tweet_id: str) -> dict:
        """コメント（リプライ）一覧"""
        return self._get(f"/twitter/tweets/{tweet_id}/comments")

    def get_all_users(self, path: str, max_results: int = 200) -> list[dict]:
        """ユーザーリストを全ページ取得するヘルパー"""
        results = []
        cursor = None
        while len(results) < max_results:
            params = {}
            if cursor:
                params["next_cursor"] = cursor
            data = self._get(path, params)
            users = data.get("users", [])
            if not users:
                break
            results.extend(users)
            cursor = data.get("next_cursor")
            if not cursor:
                break
            time.sleep(0.2)
        return results[:max_results]

    def get_all_tweets_from_path(self, path: str, max_results: int = 100) -> list[dict]:
        """投稿リストを全ページ取得するヘルパー（重複ループ検出付き）"""
        results = []
        seen_ids: set = set()
        cursor = None
        while len(results) < max_results:
            params = {}
            if cursor:
                params["next_cursor"] = cursor
            data = self._get(path, params)
            tweets = data.get("tweets", [])
            if not tweets:
                break
            # 新規ツイートが1件もなければAPIが同じページを返しているので打ち切り
            new_tweets = [
                t for t in tweets
                if (t.get("id_str") or str(t.get("id", ""))) not in seen_ids
            ]
            if not new_tweets:
                break
            for t in new_tweets:
                seen_ids.add(t.get("id_str") or str(t.get("id", "")))
            results.extend(new_tweets)
            cursor = data.get("next_cursor")
            if not cursor:
                break
            time.sleep(0.2)
        return results[:max_results]

    def search_all_tweets(
        self,
        query: str,
        max_results: int = 500,
        progress_callback=None,
    ) -> list[dict]:
        """全ページ分のツイートを収集"""
        results = []
        cursor = None

        while len(results) < max_results:
            data = self.search_tweets(query, cursor)
            tweets = data.get("tweets", [])
            if not tweets:
                break

            results.extend(tweets)
            if progress_callback:
                progress_callback(len(results))

            cursor = data.get("next_cursor")
            if not cursor:
                break

            time.sleep(0.2)  # レート制限対策

        return results[:max_results]
