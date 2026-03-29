"""競合アカウント分析 - フォロワー重複から真の競合を特定"""
import time as _time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Callable

from api import SocialDataClient


@dataclass
class CompetitorResult:
    handle: str
    target_followers: int

    competitors: list[dict] = field(default_factory=list)
    # competitors の各要素:
    # {
    #   "screen_name": str,
    #   "name": str,
    #   "followers_count": int,
    #   "description": str,
    #   "overlap_count": int,   # サンプルフォロワーの何人がフォローしているか
    #   "overlap_pct": float,   # overlap_count / sampled_followers * 100
    #   "url": str,
    # }

    sampled_followers: int = 0
    api_calls: int = 0
    cost_jpy: float = 0.0
    elapsed_sec: float = 0.0


def analyze_competitors(
    api_key: str,
    handle: str,
    max_followers: int = 200,
    following_pages: int = 1,
    min_followers_ratio: float = 0.1,
    max_followers_ratio: float = 10.0,
    progress_callback: Optional[Callable] = None,
) -> CompetitorResult:
    """
    Parameters
    ----------
    handle              : 調査対象アカウント（@なし）
    max_followers       : サンプリングするフォロワー数
    following_pages     : 各フォロワーのフォロー先取得ページ数（1ページ=20件）
    min_followers_ratio : 競合候補の最小フォロワー数 = 対象フォロワー数 × ratio
    max_followers_ratio : 競合候補の最大フォロワー数 = 対象フォロワー数 × ratio
    """
    client = SocialDataClient(api_key)
    start = _time.time()

    def _cb(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # ① プロフィール取得
    _cb(0.02, f"@{handle} のプロフィールを取得中...")
    profile = client.get_user_profile(handle)
    uid = str(profile.get("id_str") or profile.get("id", ""))
    target_followers = profile.get("followers_count", 0) or 0

    result = CompetitorResult(handle=handle, target_followers=target_followers)

    # ② フォロワーをサンプリング
    _cb(0.05, f"フォロワーを最大{max_followers}人サンプリング中...")
    follower_data = client.get_all_users(
        f"/twitter/user/{uid}/followers", max_results=max_followers
    )
    follower_ids = [
        str(u.get("id_str") or u.get("id", ""))
        for u in follower_data
        if u.get("id_str") or u.get("id")
    ]
    result.sampled_followers = len(follower_ids)

    # ③ 各フォロワーのフォロー先を収集してカウント
    following_counter: Counter = Counter()
    following_profiles: dict = {}  # screen_name → プロフィールdict
    total = len(follower_ids)

    for i, fid in enumerate(follower_ids):
        progress = 0.05 + 0.85 * (i / max(total, 1))
        _cb(progress, f"フォロー先を収集中... ({i + 1}/{total}人)")
        cursor = None
        for _ in range(following_pages):
            try:
                params = {}
                if cursor:
                    params["next_cursor"] = cursor
                data = client._get(f"/twitter/user/{fid}/following", params)
                users = data.get("users", [])
                for u in users:
                    sn = u.get("screen_name", "")
                    if sn and sn.lower() != handle.lower():
                        following_counter[sn] += 1
                        if sn not in following_profiles:
                            following_profiles[sn] = u
                cursor = data.get("next_cursor")
                if not cursor:
                    break
                _time.sleep(0.1)
            except Exception:
                break
        _time.sleep(0.1)

    # ④ フォロワー数フィルタ適用 & TOP20抽出
    _cb(0.92, "競合アカウントをランキング中...")
    min_fc = int(target_followers * min_followers_ratio)
    max_fc = int(target_followers * max_followers_ratio)

    competitors = []
    for sn, count in following_counter.most_common(500):
        prof = following_profiles.get(sn, {})
        fc = prof.get("followers_count", 0) or 0
        if min_fc <= fc <= max_fc:
            competitors.append({
                "screen_name": sn,
                "name": prof.get("name", sn),
                "followers_count": fc,
                "description": (prof.get("description") or "")[:120],
                "overlap_count": count,
                "overlap_pct": round(count / max(result.sampled_followers, 1) * 100, 1),
                "url": f"https://x.com/{sn}",
            })
        if len(competitors) >= 20:
            break

    result.competitors = competitors
    result.api_calls = client._call_count
    result.cost_jpy = client.estimated_cost_jpy
    result.elapsed_sec = _time.time() - start
    return result
