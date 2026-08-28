#!/usr/bin/env python3
"""HubSpot と Googleスプレッドシートから data.json を組み立てる.

    python3 scripts/fetch_data.py -o data.json

このスクリプトは「生の実数を集めるだけ」で、CPL・商談化率・移動平均は
一切計算しない。それは build_dashboard.py の仕事。ここで率を先に計算すると、
同じ指標の定義が2箇所に分かれて必ずズレる。

Claude が MCP 経由でやっていた集計を、素の REST API に置き換えたもの。
MCP 側にあった制約（500行打ち切り・件数が静かに1件欠ける・関連オブジェクトの
日付WHEREが効かない・週次が勝手に年次に降格される）は素のAPIには無いので、
ページングを正しく回せばそのぶん確実になる。

必要な環境変数:
    HUBSPOT_TOKEN                 プライベートアプリのアクセストークン
    GOOGLE_SERVICE_ACCOUNT_JSON   サービスアカウントの鍵JSON（中身そのもの）
"""

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 定数

HUBSPOT_API = "https://api.hubapi.com"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# 介護校のパイプライン。他パイプライン（2次代理店専用・成約後〜・
# AIアプリ開発講座 など）の取引は数えない。代理店の商談もこの default の中から
# コンタクトの route で切り出す。そうしないと直契約と定義が揃わず比率が比較できない。
PIPELINE = "default"
STAGE_WON = "closedwon"

# 相談申込ステージ。面談予約数はこのステージ入り日で数える。
STAGE_APPT = "2055087803"
STAGE_MEETING_DONE = ["appointmentscheduled", "qualifiedtobuy"]

# 集計開始週（月曜）。ダッシュボードの左端。
PERIOD_START = dt.date(2025, 9, 1)

# 架電データは2026年6月から。それ以前を取りに行っても空なので範囲を切る。
CALLS_START = dt.date(2026, 6, 1)

# 介護校の架電だけを取るための絞り込み。これが無いと Kintone 移行由来の
# 介護施設運営側のコール（月2,000〜2,500件・一括移行で49,065件）が混ざり、
# 架電数が2桁変わって接続率が0.5%のような数字になる。
CALL_SOURCE = "CRM_UI"

# 接続と数える成果のGUID。判定は「本人と話せたか」。
# 3-x（リーチ・資料送付・日程調整）と 4-x（アポ獲得・明確なNG）は
# 本人と話していないと成立しないので接続に入れる。
# ここから 4-x を落とすと、アポが取れた架電が「繋がらなかった」側に入る。
CONNECTED_DISPOSITIONS = {
    "f240bbac-87c9-4f6e-bf70-924b57d47db7",  # 接続済み
    "0d82cbdd-9aa4-4bcb-8930-310b0b3103b1",  # 2-2 再架電（着電）
    "205e7e58-2aea-4ce1-862b-9aebec83a6f4",  # 3-1 リーチ（ヒアリングなし）
    "74436836-1849-4aa8-936e-570d14942e5a",  # 3-2 FC（リーチ ヒアリングなし）
    "d88f58c8-852f-428a-9e9d-cc9ef610f778",  # 3-3 リーチ（ヒアリングあり）
    "d1ef84af-76c6-4659-a54f-e727a76f9a33",  # 3-4 FC（リーチ ヒアリングあり）
    "cd2cdf86-2ee3-422b-b016-9af1a1b74d29",  # 3-5 資料送付・ウェビナー案内
    "221cf167-b937-46dc-bcff-0477c7011403",  # 3-6 日程調整URL送付
    "4333f2a1-6c85-439c-9061-a9077c11e661",  # 4-1 アポイント獲得（新規）
    "2c222002-dcb9-4c33-94f9-928791a5e93a",  # 4-2 アポイント獲得（FC）
    "2bd76f7c-55d5-475d-8d39-64d2cd6bf335",  # 4-3 明確なNG（絶対不要）
}

# 接続に数えないと分かっているGUID。ここにも上にも無いGUIDが出てきたら
# 接続側に入れずに警告する（黙って接続扱いにすると接続率が水増しされる）。
KNOWN_NOT_CONNECTED = {
    "73a0d17f-9ea9-410a-8d5e-ade68ba9b5cb",  # 応答なし
    "b2cf5968-551e-4856-9783-52b3da59a7d0",  # 留守録を残した
    "a4c4c377-d246-4b32-a13b-75a56a4cd0ff",  # 伝言を残した
}
KNOWN_NOT_CONNECTED_PREFIX = {
    "73a0d17f", "b2cf5968", "a4c4c377", "dd9628ed", "9d9162e7",
    "6590e4e2", "17b47fee", "980c20eb", "97db3e14", "c8088d85", "82438db7",
}

SHEET_WEEKLY_AD = "1VSB7cykhDu2HHfPcCm-YItRU5zAfiwhSVeVGcfgq_qQ"  # 週次分析テンプレ(1年分)
SHEET_EXPO = "1dILS3Wavlrfrd8W_DHIvsSuRtsTxrDnjmLPIrhhFIAc"        # 展示会リード獲得単価

# 週次分析テンプレの「全体集計（週次）」は W1 = 2026-04-07（火）始まり。
# これより前の週の web 費用は「存在しない」ので null にする。0 にはしない。
AD_DATA_FIRST_MONDAY = dt.date(2026, 4, 6)

JST = dt.timezone(dt.timedelta(hours=9))

WARNINGS = []


def warn(msg):
    WARNINGS.append(msg)
    print(f"[warn] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- 日付ユーティリティ


def monday(d):
    """その日を含む週の月曜日."""
    return d - dt.timedelta(days=d.weekday())


def parse_hs_datetime(value):
    """HubSpot の datetime 文字列 → JST の日付.

    HubSpot は UTC で返す。JST に直してから日付を取らないと、
    日本時間の朝9時より前の登録が前日に寄る。
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():  # ミリ秒エポックで返ってくる場合がある
        ts = int(s) / 1000.0
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(JST).date()
    s = s.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(JST).date()


def parse_hs_date(value):
    """HubSpot の date 型 → 日付.

    date 型は「UTCの深夜0時」で暦日を表しているので、JSTに直すと1日ずれる。
    そのまま暦日として読む。獲得日（kakutokubishokaicvbi）がこれに当たる。
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        ts = int(s) / 1000.0
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def effective_date(props):
    """実効獲得日 = 獲得日（初回CV日）があればそれ、無ければ作成日.

    展示会の名刺や取りこぼし案件は、会期の数ヶ月後にHubSpotへ入力される。
    createdate をそのまま使うと入力作業をした週にリードが山積みになり、
    実際に獲得した週が空になる。
    """
    acquired = parse_hs_date(props.get("kakutokubishokaicvbi"))
    if acquired:
        return acquired
    return parse_hs_datetime(props.get("createdate"))


# ---------------------------------------------------------------- HTTP


def http_json(url, token=None, method="GET", payload=None, tries=5):
    """JSONを返すHTTP。429/5xx は指数バックオフで待つ.

    ここで諦めて空を返さないこと。空のまま先に進むと「取得できなかった週」が
    「実績0の週」として data.json に入り、ダッシュボードが静かに嘘をつく。
    """
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {e.code} {url}\n{detail}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
        except Exception as e:  # noqa: BLE001 - ネットワーク断も再試行する
            last = f"{type(e).__name__}: {e} ({url})"
            time.sleep(2 ** attempt)
    raise RuntimeError(f"リトライ上限。最後のエラー: {last}")


# ---------------------------------------------------------------- HubSpot 取得


def hs_search_all(token, object_type, filter_groups, properties, page=100):
    """Search API を最後まで回す.

    Search API は 10,000件で頭打ちになる。上限に当たったら黙って
    切り捨てず、例外で止める（切れたまま集計すると件数が静かに減る）。
    """
    url = f"{HUBSPOT_API}/crm/v3/objects/{object_type}/search"
    out, after, guard = [], None, 0
    while True:
        payload = {
            "filterGroups": filter_groups,
            "properties": properties,
            "limit": page,
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
        }
        if after:
            payload["after"] = after
        data = http_json(url, token, "POST", payload)
        out.extend(data.get("results", []))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        guard += 1
        if not after:
            break
        if guard > 200:
            raise RuntimeError(
                f"{object_type} のページングが200周を超えた。"
                "Search APIの1万件上限に当たっている可能性がある。期間分割が必要。"
            )
    total = data.get("total")
    if total is not None and len(out) < total:
        raise RuntimeError(
            f"{object_type}: total={total} に対して取得 {len(out)} 件。"
            "ページングが途中で切れている。"
        )
    return out


def hs_list_all(token, object_type, properties, associations=None, page=100):
    """List API を最後まで回す（associations を一緒に取れるのが利点）."""
    out, after, guard = [], None, 0
    while True:
        params = {"limit": page, "properties": ",".join(properties)}
        if associations:
            params["associations"] = associations
        if after:
            params["after"] = after
        url = f"{HUBSPOT_API}/crm/v3/objects/{object_type}?" + urllib.parse.urlencode(params)
        data = http_json(url, token)
        out.extend(data.get("results", []))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        guard += 1
        if not after:
            break
        if guard > 500:
            raise RuntimeError(f"{object_type} の list ページングが500周を超えた。")
    return out


def fetch_contacts(token):
    """route が入っているコンタクトを全部取る.

    route が空のコンタクトはリード数に含めない（週次表・展示会表とも同じ定義）。
    """
    groups = [{"filters": [{"propertyName": "route", "operator": "HAS_PROPERTY"}]}]
    props = ["hs_object_id", "route", "createdate", "kakutokubishokaicvbi"]
    rows = hs_search_all(token, "contacts", groups, props)
    print(f"[info] contacts(route有): {len(rows)}件", file=sys.stderr)
    return rows


def fetch_deals(token):
    """介護校パイプラインの取引を、紐づくコンタクトIDごと取る.

    取引に獲得経路のプロパティが無いので、コンタクトを横断参照する必要がある。
    Search API は associations を返さないため、associations を返す List API を使い
    パイプラインでの絞り込みは手元でやる。
    """
    props = [
        "hs_object_id", "dealname", "pipeline", "dealstage",
        "amount_in_home_currency", "createdate",
        f"hs_v2_date_entered_{STAGE_APPT}",
        "hs_v2_date_entered_appointmentscheduled",
        "hs_v2_date_entered_qualifiedtobuy",
    ]
    rows = hs_list_all(token, "deals", props, associations="contacts")
    kept = [r for r in rows if (r.get("properties") or {}).get("pipeline") == PIPELINE]
    print(f"[info] deals: 全{len(rows)}件 → {PIPELINE} {len(kept)}件", file=sys.stderr)
    return kept


def fetch_calls(token, end_date):
    """介護校の架電（CRM_UI）を取り、コンタクトIDを紐づける."""
    start_ms = int(dt.datetime.combine(CALLS_START, dt.time(), JST).timestamp() * 1000)
    end_ms = int(
        dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time(), JST).timestamp() * 1000
    )
    groups = [{
        "filters": [
            {"propertyName": "hs_object_source_label", "operator": "EQ", "value": CALL_SOURCE},
            {"propertyName": "hs_timestamp", "operator": "BETWEEN",
             "value": str(start_ms), "highValue": str(end_ms)},
        ]
    }]
    props = [
        "hs_object_id", "hs_timestamp", "hs_call_disposition",
        "hs_call_direction", "hs_object_source_label",
    ]
    calls = hs_search_all(token, "calls", groups, props)
    print(f"[info] calls({CALL_SOURCE}): {len(calls)}件", file=sys.stderr)

    # Search は associations を返さないので、バッチ関連付け読み取りで補う。
    ids = [c["id"] for c in calls]
    contact_of = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = http_json(
            f"{HUBSPOT_API}/crm/v4/associations/calls/contacts/batch/read",
            token, "POST", {"inputs": [{"id": cid} for cid in chunk]},
        )
        for row in data.get("results", []):
            targets = row.get("to") or []
            if targets:
                contact_of[str(row["from"]["id"])] = str(targets[0]["toObjectId"])
    for c in calls:
        c["_contact_id"] = contact_of.get(str(c["id"]))
    return calls


# ---------------------------------------------------------------- Google Sheets


def google_access_token(sa_json):
    """サービスアカウントの鍵で読み取り専用のアクセストークンを取る."""
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError(
            "google-auth が入っていません。`pip install google-auth requests` が必要です。"
        )
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    creds.refresh(Request())
    return creds.token


def sheet_tabs(token, sheet_id):
    data = http_json(f"{SHEETS_API}/{sheet_id}?fields=sheets.properties.title", token)
    return [s["properties"]["title"] for s in data.get("sheets", [])]


def sheet_values(token, sheet_id, tab):
    rng = urllib.parse.quote(f"'{tab}'")
    data = http_json(
        f"{SHEETS_API}/{sheet_id}/values/{rng}?valueRenderOption=UNFORMATTED_VALUE", token
    )
    return data.get("values", [])


def find_tab(tabs, *needles):
    """タブ名を部分一致で探す。タブ名の表記ゆれで落ちないようにするため."""
    for t in tabs:
        flat = t.replace(" ", "").replace("　", "")
        if all(n in flat for n in needles):
            return t
    return None


def to_number(value):
    """`¥16,749` のような表記も数値にする。読めなければ None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d.\-]", "", str(value))
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_sheet_date(value):
    """シートの日付セル（シリアル値・文字列どちらも）→ 日付."""
    if isinstance(value, (int, float)):
        # Google Sheets のシリアル値は 1899-12-30 起点
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def fetch_web_cost(token, week_starts):
    """web の週次広告費を月曜週に割り当てる.

    シート側は火曜始まりで、指定の月曜始まりと1日ズレている。
    さらに日次入力タブがある期間は、日単位で月曜週に組み直したほうが正確。

    - 日次入力にデータがある期間 … 日別の消費金額を月曜週で再集計
    - それ以前で全体集計（週次）にデータがある期間 … 火曜週を月曜週に寄せる
    - さらに前 … null（不明）。0 にはしない
    """
    tabs = sheet_tabs(token, SHEET_WEEKLY_AD)
    daily_tab = find_tab(tabs, "日次入力") or find_tab(tabs, "日次")
    weekly_tab = find_tab(tabs, "全体集計") or find_tab(tabs, "週次")
    if not daily_tab and not weekly_tab:
        raise RuntimeError(f"週次分析テンプレのタブが見つかりません。実際のタブ: {tabs}")

    cost = {w: None for w in week_starts}
    weekset = set(week_starts)

    # --- 全体集計（週次・火曜始まり）
    weekly_first_monday = None
    if weekly_tab:
        rows = sheet_values(token, SHEET_WEEKLY_AD, weekly_tab)
        header_idx, cols = None, {}
        for i, row in enumerate(rows[:40]):
            flat = [str(c).replace(" ", "").replace("　", "") for c in row]
            if any("期間" in c for c in flat) and any("消費金額" in c for c in flat):
                header_idx = i
                for j, c in enumerate(flat):
                    if "期間" in c:
                        cols["period"] = j
                    elif "消費金額" in c:
                        cols["spend"] = j
                break
        if header_idx is None:
            warn(f"『{weekly_tab}』のヘッダー行（期間／消費金額）が見つかりません。")
        else:
            # 期間は `4/7–4/13` の形で年が無い。W1 = 2026-04-07 から1週ずつ進む前提で
            # 月曜週に寄せる（開始日の前日を含む月曜週）。
            wk = 0
            for row in rows[header_idx + 1:]:
                if len(row) <= max(cols.values()):
                    continue
                period = str(row[cols["period"]]).strip()
                if not re.match(r"^\d{1,2}/\d{1,2}", period):
                    continue
                spend = to_number(row[cols["spend"]])
                tuesday = dt.date(2026, 4, 7) + dt.timedelta(weeks=wk)
                wk += 1
                mon = monday(tuesday - dt.timedelta(days=1))
                if weekly_first_monday is None:
                    weekly_first_monday = mon
                if mon in weekset and spend is not None:
                    cost[mon] = int(round(spend))

    # --- 日次入力（この期間は日別で組み直す。こちらを優先して上書きする）
    daily_first = None
    if daily_tab:
        rows = sheet_values(token, SHEET_WEEKLY_AD, daily_tab)
        header_idx, cols = None, {}
        for i, row in enumerate(rows[:40]):
            flat = [str(c).replace(" ", "").replace("　", "") for c in row]
            if any("日付" in c for c in flat) and any("消費金額" in c for c in flat):
                header_idx = i
                for j, c in enumerate(flat):
                    if "日付" in c:
                        cols.setdefault("date", j)
                    elif "消費金額" in c:
                        cols.setdefault("spend", j)
                break
        if header_idx is None:
            warn(f"『{daily_tab}』のヘッダー行（日付／消費金額）が見つかりません。")
        else:
            per_week, last_day = {}, None
            for row in rows[header_idx + 1:]:
                if len(row) <= max(cols.values()):
                    continue
                d = parse_sheet_date(row[cols["date"]])
                if not d:
                    continue
                spend = to_number(row[cols["spend"]]) or 0.0
                mon = monday(d)
                per_week[mon] = per_week.get(mon, 0.0) + spend
                daily_first = d if daily_first is None else min(daily_first, d)
                last_day = d if last_day is None else max(last_day, d)
            # 日次があるのに全体集計より後ろで途切れているとき、その先を0で
            # 埋めると「広告を止めた週」に見える。実際は未入力なので触らない。
            for mon, total in per_week.items():
                if mon in weekset:
                    cost[mon] = int(round(total))
            if last_day:
                print(f"[info] 日次入力の最終日: {last_day}", file=sys.stderr)

    # --- データが始まる前の週は null のまま残す
    first_known = AD_DATA_FIRST_MONDAY
    if weekly_first_monday:
        first_known = min(first_known, weekly_first_monday)
    for w in week_starts:
        if w < first_known:
            cost[w] = None
    return cost


def fetch_expo_costs(token):
    """展示会シートから 展示会名 → 費用合計 を取る."""
    tabs = sheet_tabs(token, SHEET_EXPO)
    rows = sheet_values(token, SHEET_EXPO, tabs[0])
    header_idx, cols = None, {}
    for i, row in enumerate(rows[:20]):
        flat = [str(c).replace(" ", "").replace("　", "") for c in row]
        if any("展示会名" in c for c in flat):
            header_idx = i
            for j, c in enumerate(flat):
                if "展示会名" in c:
                    cols["name"] = j
                elif "費用合計" in c:
                    cols["cost"] = j
            break
    if header_idx is None or "cost" not in cols:
        raise RuntimeError(f"展示会シートのヘッダーが読めません。1行目付近: {rows[:3]}")
    out = {}
    for row in rows[header_idx + 1:]:
        if len(row) <= max(cols.values()):
            continue
        name = str(row[cols["name"]]).strip()
        cost = to_number(row[cols["cost"]])
        if name and cost:
            out[name] = int(round(cost))
    print(f"[info] 展示会費用: {out}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- 集計


def build(token, sheets_token, channel_map, end_date):
    channels = channel_map["channels"]
    route_to_channel = {}
    for key, spec in channels.items():
        for route in spec["routes"]:
            route_to_channel[route] = key
    excluded = set(channel_map["excluded"]["routes"])
    expo_map = {
        k: v for k, v in channel_map["expo_route_to_sheet_name"].items()
        if not k.startswith("_")
    }

    week_starts = []
    w = PERIOD_START
    last_week = monday(end_date)
    while w <= last_week:
        week_starts.append(w)
        w += dt.timedelta(days=7)
    weekset = set(week_starts)

    contacts = fetch_contacts(token)
    deals = fetch_deals(token)
    calls = fetch_calls(token, end_date)

    # --- コンタクト → (実効獲得日, route)
    cinfo = {}
    unknown_routes = {}
    for c in contacts:
        p = c.get("properties") or {}
        route = (p.get("route") or "").strip()
        if not route or route in excluded:
            continue
        d = effective_date(p)
        if not d:
            continue
        ch = route_to_channel.get(route)
        if ch is None:
            unknown_routes[route] = unknown_routes.get(route, 0) + 1
            continue
        cinfo[str(c["id"])] = {"date": d, "route": route, "channel": ch}
    if unknown_routes:
        warn(
            "channel_map.json に無い route があります（どのチャネルにも入れていません）: "
            + ", ".join(f"{k}={v}件" for k, v in sorted(unknown_routes.items()))
        )

    direct = {
        w: {k: {"leads": 0, "cost": 0, "deals": 0, "won": 0, "won_amount": 0}
            for k in ["event", "web", "line", "referral", "other"]}
        for w in week_starts
    }
    agency = {w: {"leads": 0, "deals": 0, "won": 0, "won_amount": 0} for w in week_starts}

    # --- リード数
    route_leads_total = {}
    route_week_leads = {}
    daily_leads = {}
    for info in cinfo.values():
        d, ch, route = info["date"], info["channel"], info["route"]
        mon = monday(d)
        route_week_leads.setdefault(route, {})
        route_week_leads[route][mon] = route_week_leads[route].get(mon, 0) + 1
        if mon not in weekset:
            continue
        route_leads_total[route] = route_leads_total.get(route, 0) + 1
        if ch == "agency":
            agency[mon]["leads"] += 1
        else:
            direct[mon][ch]["leads"] += 1
        if d >= CALLS_START:
            daily_leads[d] = daily_leads.get(d, 0) + 1

    # --- 商談・成約（週はコンタクトの実効獲得日。取引の作成日ではない）
    route_deals, route_won = {}, {}
    orphan_deals = 0
    daily_appts = {}
    daily_mtgs = {}
    for deal in deals:
        p = deal.get("properties") or {}
        assoc = ((deal.get("associations") or {}).get("contacts") or {}).get("results") or []
        info = None
        for a in assoc:
            info = cinfo.get(str(a.get("id")))
            if info:
                break
        # 面談予約数は取引側の「相談申込ステージ入り日」で数える。
        # コンタクトが紐づいていなくても成立するので先に処理する。
        appt_day = parse_hs_datetime(p.get(f"hs_v2_date_entered_{STAGE_APPT}"))
        if appt_day and appt_day >= CALLS_START:
            daily_appts[appt_day] = daily_appts.get(appt_day, 0) + 1
        # 面談実施は「相談済み」か「提案・見積もり」に入った最初の日で数える。
        # 相談済みを飛ばして提案・見積もりへ動かされる取引があるため、
        # 相談済みだけを見ると面談実施が実態より少なく出る。早い方を採り、
        # 1つの取引を2回数えないようにする。
        mtg_days = [
            d for d in (
                parse_hs_datetime(p.get("hs_v2_date_entered_" + st))
                for st in STAGE_MEETING_DONE
            ) if d
        ]
        if mtg_days:
            md = min(mtg_days)
            if md >= CALLS_START:
                daily_mtgs[md] = daily_mtgs.get(md, 0) + 1
        if not info:
            orphan_deals += 1
            continue
        mon = monday(info["date"])
        if mon not in weekset:
            continue
        route, ch = info["route"], info["channel"]
        route_deals[route] = route_deals.get(route, 0) + 1
        won = p.get("dealstage") == STAGE_WON
        amount = int(to_number(p.get("amount_in_home_currency")) or 0) if won else 0
        if won:
            route_won[route] = route_won.get(route, 0) + 1
        target = agency[mon] if ch == "agency" else direct[mon][ch]
        target["deals"] += 1
        if won:
            target["won"] += 1
            target["won_amount"] += amount
    if orphan_deals:
        warn(
            f"コンタクト未紐付け、または route 未設定/除外の取引 {orphan_deals} 件を"
            "どのチャネルにも入れていません（入れると商談化率の分子だけ増えて率が歪むため）。"
        )

    # --- 展示会（開催週 = その route のリードが最も多い週）
    expo_costs = fetch_expo_costs(sheets_token)
    expos = []
    for route, sheet_name in expo_map.items():
        cost = expo_costs.get(sheet_name)
        if not cost:
            warn(f"展示会シートに『{sheet_name}』の費用がありません。テーブルから除外します。")
            continue
        weeks = route_week_leads.get(route) or {}
        if not weeks:
            warn(f"route『{route}』のリードが0件です。展示会テーブルから除外します。")
            continue
        peak = max(weeks.items(), key=lambda kv: (kv[1], kv[0]))[0]
        if peak not in weekset:
            peak = min(weekset, key=lambda w: abs((w - peak).days))
        expos.append({
            "name": sheet_name,
            "date": peak.isoformat(),
            "cost": cost,
            "leads": route_leads_total.get(route, 0),
            "deals": route_deals.get(route, 0),
            "won": route_won.get(route, 0),
        })
        # イベント費用は開催週にだけ全額を計上する
        direct[peak]["event"]["cost"] += cost
    expos.sort(key=lambda e: e["date"])

    # --- web 費用
    web_cost = fetch_web_cost(sheets_token, week_starts)
    for w in week_starts:
        direct[w]["web"]["cost"] = web_cost[w]
    # LINE・紹介・その他は常に費用0（不明ではなく、発生していないことが分かっている）
    for w in week_starts:
        for ch in ("line", "referral", "other"):
            direct[w][ch]["cost"] = 0

    # --- 日次架電
    daily_calls, daily_conn = {}, {}
    first_call_of_contact = {}
    unknown_disp = {}
    no_direction = 0
    for c in calls:
        p = c.get("properties") or {}
        ts = parse_hs_datetime(p.get("hs_timestamp"))
        if not ts or ts > end_date:
            continue
        direction = (p.get("hs_call_direction") or "").upper()
        if "OUTBOUND" not in direction:
            if not direction:
                no_direction += 1
            continue
        daily_calls[ts] = daily_calls.get(ts, 0) + 1
        disp = (p.get("hs_call_disposition") or "").strip()
        if disp in CONNECTED_DISPOSITIONS:
            daily_conn[ts] = daily_conn.get(ts, 0) + 1
        elif disp and disp[:8] not in KNOWN_NOT_CONNECTED_PREFIX:
            unknown_disp[disp] = unknown_disp.get(disp, 0) + 1
        cid = c.get("_contact_id")
        if cid:
            prev = first_call_of_contact.get(cid)
            first_call_of_contact[cid] = ts if prev is None else min(prev, ts)
    if no_direction:
        warn(
            f"hs_call_direction が未設定のコールが {no_direction} 件あります。"
            "発信と判定できないため架電数・接続数・転換率の分母から外しています"
            "（そのぶん転換率は高めに出ます）。"
        )
    if unknown_disp:
        warn(
            "対応表に無いコール成果GUIDがありました（接続に数えていません）: "
            + ", ".join(f"{k}={v}件" for k, v in unknown_disp.items())
        )

    # 日次の「架電したリード数」＝その日に初めて架電したユニークのコンタクト数。
    # コール件数（架電数）と対になる「何人に当たったか」。同じ人に同じ日に
    # 3回かけても1人と数える。累計側と定義を揃えるため「初回架電日」で持つ。
    daily_called = {}
    for _cid, _first in first_call_of_contact.items():
        daily_called[_first] = daily_called.get(_first, 0) + 1

    calls_out = {}
    all_days = (set(daily_calls) | set(daily_conn) | set(daily_appts)
                | set(daily_mtgs) | set(daily_called) | set(daily_leads))
    for d in sorted(all_days):
        if d < CALLS_START or d > end_date:
            continue
        calls_out[d.isoformat()] = {
            "calls": daily_calls.get(d, 0),
            "connected": daily_conn.get(d, 0),
            "appts": daily_appts.get(d, 0),
            "mtgs": daily_mtgs.get(d, 0),
            "called": daily_called.get(d, 0),
            "leads": daily_leads.get(d, 0),
        }

    # --- 架電 → 面談予約の週次転換
    # 分母は「発信の架電をしたユニークコンタクト数」。コール件数ではない。
    # 分子は「そのコンタクトの面談予約のうち、初回架電日以降に立ったもの」。
    # アポ成立後の追客架電を数えないために日付条件が要る。
    appt_days_of_contact = {}
    for deal in deals:
        p = deal.get("properties") or {}
        appt_day = parse_hs_datetime(p.get(f"hs_v2_date_entered_{STAGE_APPT}"))
        if not appt_day:
            continue
        assoc = ((deal.get("associations") or {}).get("contacts") or {}).get("results") or []
        for a in assoc:
            appt_days_of_contact.setdefault(str(a.get("id")), []).append(appt_day)

    conv = {}
    for cid, first_day in first_call_of_contact.items():
        mon = monday(first_day)
        if mon not in weekset:
            continue
        row = conv.setdefault(mon, {"called": 0, "appointed": 0})
        row["called"] += 1
        if any(d >= first_day for d in appt_days_of_contact.get(cid, [])):
            row["appointed"] += 1

    return {
        "title": "ホリエモンAI学校 介護校 ファネルダッシュボード",
        "generated_at": end_date.isoformat(),
        "week_starts": [w.isoformat() for w in week_starts],
        "direct": {w.isoformat(): direct[w] for w in week_starts},
        "agency": {w.isoformat(): agency[w] for w in week_starts},
        "expos": expos,
        "calls": calls_out,
        "call_conversion": {w.isoformat(): v for w, v in sorted(conv.items())},
    }


# ---------------------------------------------------------------- 妥当性チェック


def sanity_check(data, previous):
    """前回の正常なデータと突き合わせ、明らかにおかしければ False を返す.

    ここで止めれば、公開中のダッシュボードは前回の内容のまま残る。
    「取れなかった」を「0件だった」として公開してしまうのが一番まずい。
    """
    ok = True
    leads = sum(
        ch["leads"] for wk in data["direct"].values() for ch in wk.values()
    ) + sum(wk["leads"] for wk in data["agency"].values())
    deals = sum(
        ch["deals"] for wk in data["direct"].values() for ch in wk.values()
    ) + sum(wk["deals"] for wk in data["agency"].values())

    if leads < 100:
        print(f"[NG] 総リード数が {leads} 件しかありません。取得失敗の可能性。", file=sys.stderr)
        ok = False
    if deals < 10:
        print(f"[NG] 総商談数が {deals} 件しかありません。取得失敗の可能性。", file=sys.stderr)
        ok = False
    if not data["expos"]:
        print("[NG] 展示会が0件です。シートが読めていません。", file=sys.stderr)
        ok = False

    if previous:
        prev_leads = sum(
            ch["leads"] for wk in previous.get("direct", {}).values() for ch in wk.values()
        ) + sum(wk["leads"] for wk in previous.get("agency", {}).values())
        # リードは積み上がる一方なので、前回より大きく減るのは異常。
        # 5%の遊びは、重複マージや削除で数件減ることがあるため。
        if prev_leads and leads < prev_leads * 0.95:
            print(
                f"[NG] 総リード数が前回 {prev_leads} → 今回 {leads} と大きく減りました。"
                "公開を見送ります。",
                file=sys.stderr,
            )
            ok = False
        else:
            print(f"[info] 総リード数 前回 {prev_leads} → 今回 {leads}", file=sys.stderr)
    return ok


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="HubSpot/Sheets から data.json を作る")
    ap.add_argument("-o", "--output", default="data.json")
    ap.add_argument("--channel-map", default="config/channel_map.json")
    ap.add_argument("--previous", help="前回の data.json（妥当性チェックの比較対象）")
    ap.add_argument("--end", help="集計終端 YYYY-MM-DD（既定: 今日 JST）")
    args = ap.parse_args()

    token = os.environ.get("HUBSPOT_TOKEN")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not token:
        sys.exit("HUBSPOT_TOKEN が設定されていません。")
    if not sa_json:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON が設定されていません。")

    end_date = (
        dt.date.fromisoformat(args.end) if args.end
        else dt.datetime.now(JST).date()
    )
    with open(args.channel_map, encoding="utf-8") as f:
        channel_map = json.load(f)

    previous = None
    if args.previous and os.path.exists(args.previous):
        try:
            with open(args.previous, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            warn(f"前回の data.json を読めませんでした（比較なしで続行）: {e}")

    sheets_token = google_access_token(sa_json)
    data = build(token, sheets_token, channel_map, end_date)

    if not sanity_check(data, previous):
        sys.exit(2)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[ok] {args.output} を書きました（終端 {end_date}）", file=sys.stderr)

    if WARNINGS:
        print("\n--- 気づいた点 ---", file=sys.stderr)
        for w in WARNINGS:
            print(f"* {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
