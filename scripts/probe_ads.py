#!/usr/bin/env python3
"""HubSpot から日次の広告費が取れるかを調べる（読み取りのみ・何も変更しない）.

    HUBSPOT_TOKEN=<トークン> python3 scripts/probe_ads.py

やっていること:
  1. トークンが通るか確認し、付与されているスコープを一覧する
  2. 広告系のエンドポイントを順に叩いて、到達できるものを探す
  3. 到達できたら中身の形（日付ごとの消費金額があるか）を表示する

エンドポイントは「これが正解」と決め打ちせず、候補を順に試して
実際に返ってきたものだけを報告する。HubSpotの広告APIは
プランと権限で見え方が変わるため、手元で確かめるのが確実。

トークンは一切表示しない。
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.hubapi.com"

# Windowsのコンソールは既定がUTF-8でないことがあり、日本語の出力が
# 文字化けして読めなくなる。判定結果を読ませるのが目的なので明示しておく。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def call(path, token, method="GET", payload=None):
    """(status, body) を返す。失敗しても例外を投げない。"""
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"_error": str(e)}


def main():
    # 環境変数が無ければその場で聞く。コマンドに書かせると
    # シェルの履歴にトークンが残るので、入力は伏せ字で受け取る。
    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        try:
            token = getpass.getpass("HubSpotのトークンを貼り付けてEnter（表示されません）: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\n中断しました。")
    if not token:
        sys.exit("トークンが空です。")

    print("=" * 62)
    print("1. トークンの確認")
    print("=" * 62)
    st, body = call("/crm/v3/objects/contacts?limit=1", token)
    print(f"  CRM読み取り: {'OK' if st == 200 else 'NG'} (status {st})")
    if st != 200:
        print(f"  {json.dumps(body, ensure_ascii=False)[:300]}")
        sys.exit("トークンが通りません。ここで中断します。")

    st, body = call("/oauth/v2/private-apps/get/access-token-info", token,
                    method="POST", payload={"tokenKey": token})
    if st == 200:
        scopes = body.get("scopes") or []
        print(f"  付与スコープ: {len(scopes)}件")
        ad_scopes = [s for s in scopes if "ad" in s.lower() or "marketing" in s.lower()]
        print(f"  広告/マーケ関連: {ad_scopes if ad_scopes else '（なし）'}")
    else:
        print(f"  スコープ一覧は取得できず (status {st})。"
              "OAuthトークンの場合はこの方法では見えない。")

    print()
    print("=" * 62)
    print("2. 広告系エンドポイントの探索")
    print("=" * 62)
    candidates = [
        "/marketing/v3/ads/accounts",
        "/marketing/v3/ads/campaigns",
        "/marketing/v3/ads/ad-accounts",
        "/ads/v1/ad-accounts",
        "/ads/v1/ad-campaigns",
        "/marketing/v3/campaigns",
    ]
    reachable = []
    for path in candidates:
        st, body = call(path, token)
        mark = "到達" if st == 200 else ("権限不足" if st in (401, 403) else "なし")
        print(f"  [{st:>3}] {mark:<6} {path}")
        if st == 200:
            reachable.append((path, body))

    print()
    print("=" * 62)
    print("3. 到達できたものの中身")
    print("=" * 62)
    if not reachable:
        print("  広告データに到達できるエンドポイントはありませんでした。")
        print("  → HubSpot経由は諦めて、Meta広告APIから直接取る方式に切り替える。")
        return
    for path, body in reachable:
        print(f"\n--- {path} ---")
        text = json.dumps(body, ensure_ascii=False, indent=1)
        print(text[:1200] + ("\n  …(以下略)" if len(text) > 1200 else ""))
        has_date = any(k in text for k in ("date", "Date", "日付"))
        has_spend = any(k in text for k in ("spend", "cost", "amount", "budget"))
        print(f"  日付らしき項目: {'あり' if has_date else 'なし'} / "
              f"金額らしき項目: {'あり' if has_spend else 'なし'}")

    print()
    print("判定の目安: 日付と金額の両方があるエンドポイントが見つかれば、")
    print("            シートをやめて HubSpot から日次の広告費を取れる。")


if __name__ == "__main__":
    main()
