#!/usr/bin/env python3
"""自動更新に必要なHubSpotのアクセスが揃っているかを確認する（読み取りのみ）.

    python scripts/check_hubspot_access.py

fetch_data.py が実際に叩く4種類のリクエストを、それぞれ1ページだけ試す。
全部OKなら、あとは Google の認証情報だけで自動更新が動く状態。

通話（calls）に専用スコープが無いため、コンタクトの読み取り権限で
読めるかどうかがここで確定する。読めなければ設計を変える必要がある。

キーは表示しない。
"""

import datetime as dt
import getpass
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.hubapi.com"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def call(path, token, method="GET", payload=None):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
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


def get_token():
    t = os.environ.get("HUBSPOT_TOKEN")
    if t:
        return t
    kf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      ".hubspot_key")
    if os.path.exists(kf):
        with open(kf, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            print("[info] .hubspot_key からキーを読み込みました。")
            return t
    try:
        return getpass.getpass("HubSpotのキーを貼り付けてEnter（表示されません）: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("中断しました。")


def show(label, ok, detail=""):
    print(f"  [{'OK' if ok else 'NG'}] {label}{('  ' + detail) if detail else ''}")
    return ok


def main():
    token = get_token()
    if not token:
        sys.exit("キーが空です。")

    print("=" * 60)
    print("自動更新に必要なアクセスの確認")
    print("=" * 60)
    results = []

    # 1. コンタクト（route が入っているもの）
    st, body = call("/crm/v3/objects/contacts/search", token, "POST", {
        "filterGroups": [{"filters": [
            {"propertyName": "route", "operator": "HAS_PROPERTY"}]}],
        "properties": ["route", "createdate", "kakutokubishokaicvbi"],
        "limit": 1,
    })
    n = body.get("total")
    results.append(show("コンタクト検索（route有）", st == 200,
                        f"該当 {n} 件" if st == 200 else f"status {st}"))

    # 2. 取引（コンタクトの紐付きごと）
    st, body = call("/crm/v3/objects/deals?limit=1&associations=contacts"
                    "&properties=dealname,pipeline,dealstage", token)
    has_assoc = bool((body.get("results") or [{}])[0].get("associations")) if st == 200 else False
    results.append(show("取引の取得（コンタクト紐付き）", st == 200,
                        ("紐付きあり" if has_assoc else "紐付きなし(この1件には)")
                        if st == 200 else f"status {st}"))

    # 3. 通話（専用スコープが無いのでここが焦点）
    start = int(dt.datetime(2026, 6, 1).timestamp() * 1000)
    st, body = call("/crm/v3/objects/calls/search", token, "POST", {
        "filterGroups": [{"filters": [
            {"propertyName": "hs_timestamp", "operator": "GTE", "value": str(start)}]}],
        "properties": ["hs_call_disposition", "hs_call_source", "hs_timestamp"],
        "limit": 1,
    })
    ok3 = st == 200
    results.append(show("通話の検索", ok3,
                        f"該当 {body.get('total')} 件" if ok3 else f"status {st} ← 権限不足の可能性"))

    # 4. 通話→コンタクトの紐付け
    if ok3 and (body.get("results") or []):
        cid = body["results"][0]["id"]
        st4, b4 = call("/crm/v4/associations/calls/contacts/batch/read", token, "POST",
                       {"inputs": [{"id": cid}]})
        results.append(show("通話→コンタクトの紐付け", st4 == 200,
                            "" if st4 == 200 else f"status {st4}"))
    else:
        results.append(show("通話→コンタクトの紐付け", False, "通話が読めないため未確認"))

    print()
    if all(results):
        print("すべて通りました。HubSpot側の準備は完了です。")
        print("残るは Google の認証情報（GOOGLE_SERVICE_ACCOUNT_JSON）だけです。")
    else:
        print("通らなかった項目があります。この結果をそのまま共有してください。")
        print("（キーは出力に含まれていません）")


if __name__ == "__main__":
    main()
