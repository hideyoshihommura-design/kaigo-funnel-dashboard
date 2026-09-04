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

# 広告日次データ（ダッシュボード専用。1行 = 1日 × 1媒体 × 1キャンペーン）。
# 旧「週次分析テンプレ(1年分)」は火曜始まりの週次タブと日次入力タブが混在し、
# 日次が始まる 2026-06-09 より前は日単位に切れなかった。こちらは配信初日
# （2026-04-07）から全期間が日次なので、期間指定を任意の日で切れる。
SHEET_AD_DAILY = "1w2BnLqWotS3O5VwT5hdCngD1DvFjP0DNF_twOlaS0mM"
SHEET_EXPO = "1dILS3Wavlrfrd8W_DHIvsSuRtsTxrDnjmLPIrhhFIAc"        # 展示会リード獲得単価

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
        # 取引を作った人。架電業者ぶんと社内ぶんを分けるのに使う。
        # 取引は相談申込に入った瞬間に作られる（作成日時とステージ入り日時が
        # ミリ秒まで一致する）ので、作成者＝面談予約を取った人。
        # IDの体系は hubspot_owner_id と同じなので config/callers.json を
        # そのまま引き当てられる。
        "hs_created_by_user_id",
        f"hs_v2_date_entered_{STAGE_APPT}",
        "hs_v2_date_entered_appointmentscheduled",
        "hs_v2_date_entered_qualifiedtobuy",
        f"hs_v2_date_entered_{STAGE_WON}",
    ]
    rows = hs_list_all(token, "deals", props, associations="contacts")
    kept = [r for r in rows if (r.get("properties") or {}).get("pipeline") == PIPELINE]
    print(f"[info] deals: 全{len(rows)}件 → {PIPELINE} {len(kept)}件", file=sys.stderr)
    return kept


def fetch_calls(token, end_date):
    """介護校の架電（CRM_UI）を取り、コンタクトIDを紐づける.

    業者ぶんと社内ぶんの切り分けにコールは使わない（取引の作成者で判定する）。
    ここで取るのはIS活動量ブロック（架電数・接続率・転換率）のためだけなので、
    表示開始日 CALLS_START 以降でよい。
    """
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


def row_num(row, idx):
    """行の idx 番目を数値で取る。列が足りない・空欄なら 0."""
    if len(row) <= idx:
        return 0.0
    return to_number(row[idx]) or 0.0


def fetch_ad_daily(token, week_starts, campaign_cfg):
    """広告日次シート（1タブ・1行 = 1日 × 1媒体 × 1キャンペーン）を読む.

    旧「週次分析テンプレ」の 全体集計（火曜始まり）／日次入力 の2タブ読みを
    置き換えたもの。旧方式には次の問題があった。

    - タブ名も列名も部分一致で探し、週次側は行数を数えて日付を推定していた。
      シートに空行が1つ入るだけで、以降の全週が静かに1週ずれる。
    - 日次入力タブが 2026-06-09 始まりで、それ以前は週次（火曜始まり）しか
      無かった。各週が月曜週と1日ずれ、期間指定を日単位で切れなかった。
    - 媒体の区別が無く、キャンペーン名の文字列に「ウェビナー」が含まれるかで
      ウェビナー配信を判定していた。Google/LINE のウェビナー配信は日次タブに
      存在せず、拾えていなかった。

    新シートは全期間が日次で、媒体とキャンペーンが別の列にある。ここでは
    1行目のヘッダーを完全一致で引き、日付はセルの値をそのまま使う。
    読めなければ黙って空を返さず例外で止める（空のまま進むと、取得できな
    かった期間が「実績0の期間」として data.json に入る）。

    返すのは実数だけ。CPL・CPM・CTR・CVR は build_dashboard.py の担当。

    最後に返す ad_day は 日 × 媒体/キャンペーン の生データ。集計も除外もして
    いないので、媒体別・キャンペーン別・その掛け合わせのどれでも作れる。
    """
    tabs = sheet_tabs(token, SHEET_AD_DAILY)
    if not tabs:
        raise RuntimeError("広告日次シートにタブがありません。")
    # 広告日次専用のシートなので1枚目で確定させる。タブ名には依存しない。
    tab = tabs[0]
    rows = sheet_values(token, SHEET_AD_DAILY, tab)
    if len(rows) < 2:
        raise RuntimeError(
            f"広告日次シート『{tab}』にデータ行がありません（ヘッダーのみ）。"
            "0円で埋めると広告を止めた期間に見えるため、ここで止めます。"
        )

    header = [str(c).strip() for c in rows[0]]
    cols = {}
    for name in ("日付", "媒体", "キャンペーン", "消費金額", "IMP", "クリック", "CV"):
        if name not in header:
            raise RuntimeError(
                f"広告日次シート『{tab}』の1行目に『{name}』列がありません。"
                f"実際のヘッダー: {header}"
            )
        cols[name] = header.index(name)

    campaigns = {
        k: v for k, v in (campaign_cfg.get("campaigns") or {}).items()
        if not k.startswith("_")
    }
    known_pairs = set(campaign_cfg.get("known_pairs") or [])

    cost_by_day, cv_by_day = {}, {}
    # ウェビナーキャンペーンだけの日次。回ごとの掲載期間で切って使う。
    wcost_by_day, wcv_by_day = {}, {}
    wimp_by_day, wclk_by_day = {}, {}
    adperf, adperf_day = {}, {}
    # 日 × 媒体 × キャンペーンの生データ。ここだけは集計せずに一番細かい粒度で
    # 残す。媒体別・キャンペーン別・その掛け合わせ・全体のどれでも後から計算
    # できるようにするため。
    # 媒体を混ぜた率は実態を表さない。LINEはクリック単価がMetaの70分の1で、
    # 2026-04のクリックの約94%を占めていた。CVはMetaにしか入っていない。
    # 足した数字は大きい方の媒体の数字になるので、媒体で割れるようにしておく。
    ad_day = {}
    unknown_campaigns, unknown_pairs = {}, {}
    seen = set()
    dup_rows = 0
    bad_dates = 0
    first_day = last_day = None

    for row in rows[1:]:
        if len(row) <= cols["日付"]:
            continue
        d = parse_sheet_date(row[cols["日付"]])
        if not d:
            if any(str(c).strip() for c in row):
                bad_dates += 1
            continue
        media = (str(row[cols["媒体"]]).strip()
                 if len(row) > cols["媒体"] else "")
        camp = (str(row[cols["キャンペーン"]]).strip()
                if len(row) > cols["キャンペーン"] else "")
        pair = f"{media}/{camp}"

        # 同じ日・同じ媒体・同じキャンペーンが2行あると二重計上になる。
        # 貼り付けのやり直しで起きやすいので、気づけるようにしておく。
        key = (d, media, camp)
        if key in seen:
            dup_rows += 1
        seen.add(key)

        spend = row_num(row, cols["消費金額"])
        imp = row_num(row, cols["IMP"])
        clicks = row_num(row, cols["クリック"])
        cvv = row_num(row, cols["CV"])

        # web費用と全体CVは媒体・区分に関係なく全行を合計する。
        cost_by_day[d] = cost_by_day.get(d, 0.0) + spend
        cv_by_day[d] = cv_by_day.get(d, 0.0) + cvv

        # 生データ。この時点では何も除外しない（キャンペーン定義に無いものも
        # 残す。除外すると媒体別の費用合計が総web費用と合わなくなる）。
        slot = ad_day.setdefault(d.isoformat(), {}).setdefault(
            pair, {"spend": 0.0, "imp": 0.0, "clicks": 0.0, "cv": 0.0})
        slot["spend"] += spend
        slot["imp"] += imp
        slot["clicks"] += clicks
        slot["cv"] += cvv
        first_day = d if first_day is None else min(first_day, d)
        last_day = d if last_day is None else max(last_day, d)

        if known_pairs and pair not in known_pairs:
            unknown_pairs[pair] = unknown_pairs.get(pair, 0) + 1

        # ウェビナー別の集計はウェビナー集客のキャンペーンだけ。媒体はまたぐ。
        # 全キャンペーンを足すと費用が2倍以上になる。
        kind = campaigns.get(camp)
        if kind is None:
            key2 = camp or "(空欄)"
            unknown_campaigns[key2] = unknown_campaigns.get(key2, 0) + 1
            continue
        if kind != "webinar":
            continue
        wcost_by_day[d] = wcost_by_day.get(d, 0.0) + spend
        wcv_by_day[d] = wcv_by_day.get(d, 0.0) + cvv
        wimp_by_day[d] = wimp_by_day.get(d, 0.0) + imp
        wclk_by_day[d] = wclk_by_day.get(d, 0.0) + clicks
        # 週次と日次の両方に積む。週次だけにすると、期間指定を1日に絞った
        # ときにその週まるごとの値が出てしまう。
        for bucket, bkey in (
            (adperf, monday(d).isoformat()),
            (adperf_day, d.isoformat()),
        ):
            a = bucket.setdefault(
                bkey, {"spend": 0.0, "imp": 0.0, "clicks": 0.0, "cv": 0.0})
            a["spend"] += spend
            a["imp"] += imp
            a["clicks"] += clicks
            a["cv"] += cvv

    if bad_dates:
        warn(
            f"広告日次シートで日付が読めなかった行が {bad_dates} 行あります"
            "（費用にもCVにも入れていません）。"
        )
    if dup_rows:
        warn(
            f"広告日次シートに同じ日付×媒体×キャンペーンの行が {dup_rows} 組"
            "あります。二重計上になっているので、シート側を確認してください。"
        )
    if unknown_pairs:
        warn(
            "config/ad_campaigns.json の known_pairs に無い媒体×キャンペーンの"
            "組み合わせがあります（費用には入れています。入力ミスでなければ"
            "known_pairs に足してください）: "
            + ", ".join(f"{k}={v}行" for k, v in sorted(unknown_pairs.items()))
        )
    if unknown_campaigns:
        warn(
            "config/ad_campaigns.json に無いキャンペーン名があります"
            "（web費用の合計には入れていますが、ウェビナー別の集計からは外して"
            "います）: "
            + ", ".join(f"{k}={v}行" for k, v in sorted(unknown_campaigns.items()))
        )
    print(f"[info] 広告日次: {first_day} 〜 {last_day}（{len(rows) - 1}行）",
          file=sys.stderr)

    # 週次は日次から組み立てる。行が1つも無い週は None のまま残す。
    # 0 にすると「広告を止めた週」に見えるが、実際は未入力なだけ。
    weekset = set(week_starts)
    cost = {w: None for w in week_starts}
    cv = {w: None for w in week_starts}
    cost_week, cv_week = {}, {}
    for d, v in cost_by_day.items():
        cost_week[monday(d)] = cost_week.get(monday(d), 0.0) + v
    for d, v in cv_by_day.items():
        cv_week[monday(d)] = cv_week.get(monday(d), 0.0) + v
    for w, v in cost_week.items():
        if w in weekset:
            cost[w] = int(round(v))
    for w, v in cv_week.items():
        if w in weekset:
            cv[w] = int(round(v))

    return (cost, cost_by_day, cv_by_day, cv,
            wcost_by_day, wcv_by_day, wimp_by_day, wclk_by_day,
            adperf, adperf_day, ad_day)


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


def build(token, sheets_token, channel_map, webinar_cfg, campaign_cfg,
          vendor_ids, end_date):
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

    # ここから下の direct / agency / direct_day / agency_day は
    # **すべてコンタクトの実効獲得日を軸にする**（コホート軸）。
    # leads も deals も won も appts も「そのリードを獲得した日」に載せる。
    # 率を出すときに分子と分母が同じ集団になるのはこの軸だけ。
    #
    # もう一方の軸（イベント軸＝そのできごとが起きた日）は
    # calls / fs / is_attr が持っている。**この2つを混ぜてはいけない。**
    # 混ぜると「7月に獲得したリード798件」と「7月に入った予約10件」を
    # 割ることになり、別の集団の割り算になる。
    direct = {
        w: {k: {"leads": 0, "cost": 0, "appts": 0, "mtgs": 0, "props": 0,
                "deals": 0, "won": 0, "won_amount": 0}
            for k in ["event", "web", "line", "referral", "other"]}
        for w in week_starts
    }
    agency = {
        w: {"leads": 0, "appts": 0, "deals": 0, "won": 0, "won_amount": 0}
        for w in week_starts
    }

    # 日別の直契約／代理店。期間指定を1日や数日に絞ったとき、週次だけだと
    # その週まるごとの値が出てしまうため、同じ数え方で日別にも積んでおく。
    # チャネル別の内訳は週次表でしか使わないので、ここでは合計だけ持つ。
    direct_day, agency_day = {}, {}
    CHANNELS = ["event", "web", "line", "referral", "other"]

    def dday_direct(d):
        return direct_day.setdefault(d.isoformat(), {
            k: {"leads": 0, "cost": 0, "appts": 0, "mtgs": 0, "props": 0,
                "deals": 0, "won": 0, "won_amount": 0}
            for k in CHANNELS})

    def dday_agency(d):
        return agency_day.setdefault(
            d.isoformat(),
            {"leads": 0, "appts": 0, "deals": 0, "won": 0, "won_amount": 0})

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
            dday_agency(d)["leads"] += 1
        else:
            direct[mon][ch]["leads"] += 1
            dday_direct(d)[ch]["leads"] += 1
        if d >= CALLS_START:
            daily_leads[d] = daily_leads.get(d, 0) + 1

    # --- 商談・成約（週はコンタクトの実効獲得日。取引の作成日ではない）
    # ウェビナー別の集計でも同じ紐付けを使うので、コンタクト単位でも持っておく。
    per_contact = {}
    route_deals, route_won = {}, {}
    orphan_deals = 0
    daily_appts = {}
    daily_mtgs = {}
    daily_props = {}
    daily_wons = {}
    daily_wonamt = {}
    # --- 取引作成者別（直契約のみ）
    # 取引は相談申込に入った瞬間に作られるので、作成者＝面談予約を取った人。
    # 架電履歴から「直前にかけたのは誰か」を推定する必要はない。推定に頼ると
    # コールの記録漏れ・発信着信欄の空白でそのまま結果が狂う（実際に狂った）。
    # 集計期間の頭（PERIOD_START）から出す。
    is_attr = {}
    unknown_creators = {}

    def dattr(d):
        return is_attr.setdefault(d.isoformat(), {
            b: {"appts": 0, "deals": 0, "mtgs": 0, "props": 0,
                "wons": 0, "wonamt": 0}
            for b in ("vendor", "inhouse")})

    for deal in deals:
        p = deal.get("properties") or {}
        assoc = ((deal.get("associations") or {}).get("contacts") or {}).get("results") or []
        info = None
        for a in assoc:
            info = cinfo.get(str(a.get("id")))
            if info:
                break
        # 面談実施は「相談済み」か「提案・見積もり」に入った最初の日で数える。
        # 相談済みを飛ばして提案・見積もりへ動かされる取引があるため、
        # 相談済みだけを見ると面談実施が実態より少なく出る。早い方を採り、
        # 1つの取引を2回数えないようにする。
        # 面談予約日が無いときの代わりにも使うので、ここで先に出しておく。
        mtg_days = [
            d for d in (
                parse_hs_datetime(p.get("hs_v2_date_entered_" + st))
                for st in STAGE_MEETING_DONE
            ) if d
        ]
        mtg_day = min(mtg_days) if mtg_days else None
        # 面談予約数は取引側の「相談申込ステージ入り日」で数える。
        # コンタクトが紐づいていなくても成立するので先に処理する。
        appt_day = parse_hs_datetime(p.get(f"hs_v2_date_entered_{STAGE_APPT}"))
        # 相談申込を通っていない取引が14件ある。全部2025-09〜2025-12で、
        # 当時は相談申込を飛ばして「相談済み」から取引を作っていた。
        # ステージ自体は2025-10-31から存在するので、無かったのではなく運用が
        # 不統一だっただけ。面談まで進んでいるのに面談予約0件として扱われるため、
        # 相談申込の日が無い取引は面談実施日を面談予約日として使う。
        # 面談したなら予約はあったはずで、記録が残っていないだけである。
        # この2つが同日になるが、ファネルに「予約→実施率」の行は無いので
        # 画面に不自然な100%は出ない。
        if not appt_day:
            appt_day = mtg_day
        if appt_day and appt_day >= CALLS_START:
            daily_appts[appt_day] = daily_appts.get(appt_day, 0) + 1
        # ここから下は上部の週次表と同じ母集団に揃える必要があるので、
        # コンタクトが紐づき route が有効で期間内のものだけを対象にする。
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
        # --- フィールドセールスの活動（直契約のみ）
        # 代理店経由は代理店が売っているので自社FSの活動ではない。
        # 上部の直契約カードと同じ母集団（route有効・獲得週が期間内・直契約）に
        # 揃えるため、ここで数える。日付はステージに入った日で、
        # 週次表の「獲得週に載せる」とは軸が違う（FSがいつ動いたかを見るため）。
        if ch != "agency":
            if mtg_day:
                daily_mtgs[mtg_day] = daily_mtgs.get(mtg_day, 0) + 1
            prop_day = parse_hs_datetime(p.get("hs_v2_date_entered_qualifiedtobuy"))
            if prop_day:
                daily_props[prop_day] = daily_props.get(prop_day, 0) + 1
            # 成約は「今も成約ステージにあること」を条件にする。日付だけで
            # 数えると、一度成約して後から不成約に戻された取引が残り続ける
            # （実際に2件あり、上部の成約数と1件ずれていた）。
            # 上部と同じ won / amount を使うことで定義のズレを作らない。
            won_day = parse_hs_datetime(p.get(f"hs_v2_date_entered_{STAGE_WON}"))
            if won and won_day:
                daily_wons[won_day] = daily_wons.get(won_day, 0) + 1
                daily_wonamt[won_day] = daily_wonamt.get(won_day, 0) + amount
            # 作成者別の内訳はFS指標と同じ母集団（直契約・route有効・
            # 獲得週が期間内）で数える。揃えないと「業者作成＋社内作成」の
            # 合計が上のFSカードと一致せず、どちらが正しいのか判断できない。
            # コンタクト未紐付けの取引は作成者だけなら判定できるが、route が
            # 引けず直契約か代理店かを決められないので、ここでも母集団から
            # 外している（他のセクション全部と同じ扱い）。
            creator = str(p.get("hs_created_by_user_id") or "").strip()
            if not creator:
                unknown_creators["(空)"] = unknown_creators.get("(空)", 0) + 1
            bucket = "vendor" if creator in vendor_ids else "inhouse"
            # 面談実施と提案も作成者別に持つ。面談を実施するのは社内のFSだが、
            # ここで見たいのは「誰が実施したか」ではなく「業者が取った予約が
            # その後どこまで進んだか」。だから取引の作成者で振り分けるのが正しい。
            for field, day in (("appts", appt_day),
                               ("deals", parse_hs_datetime(p.get("createdate"))),
                               ("mtgs", mtg_day),
                               ("props", prop_day),
                               ("wons", won_day if won else None)):
                if not day or day < PERIOD_START or day > end_date:
                    continue
                dattr(day)[bucket][field] += 1
                if field == "wons":
                    dattr(day)[bucket]["wonamt"] += amount

        for a in assoc:
            acid = str(a.get("id"))
            if acid in cinfo:
                pc = per_contact.setdefault(acid, {"deals": 0, "won": 0, "amount": 0})
                pc["deals"] += 1
                if won:
                    pc["won"] += 1
                    pc["amount"] += amount
                break

        target = agency[mon] if ch == "agency" else direct[mon][ch]
        target["deals"] += 1
        if won:
            target["won"] += 1
            target["won_amount"] += amount
        # 日別も同じ軸（コンタクトの実効獲得日）で数える
        td = (dday_agency(info["date"]) if ch == "agency"
              else dday_direct(info["date"])[ch])
        td["deals"] += 1
        if won:
            td["won"] += 1
            td["won_amount"] += amount
        # 面談予約をコホート軸（獲得日）でも数える。
        # 「そのリードは予約に至ったか」を見るための数字で、リード数と
        # 同じ集団になる。予約が入った日で数えた calls / is_attr の appts
        # とは別物。**足し合わせたり、片方を分子・片方を分母にしない。**
        if appt_day:
            target["appts"] += 1
            td["appts"] += 1
        # 面談実施と提案もコホート軸に載せる。ファネルで
        # リード→予約→実施→提案→成約 を1本に並べたとき、途中で軸が
        # 変わると段階間の率が別集団の割り算になるため。
        # 代理店は自社FSを通らないので mtgs / props を持たない。
        if ch != "agency":
            if mtg_day:
                target["mtgs"] += 1
                td["mtgs"] += 1
            if prop_day:
                target["props"] += 1
                td["props"] += 1
    if orphan_deals:
        warn(
            f"コンタクト未紐付け、または route 未設定/除外の取引 {orphan_deals} 件を"
            "どのチャネルにも入れていません（入れると商談化率の分子だけ増えて率が歪むため）。"
        )
    if unknown_creators:
        warn(
            "作成者が読めない取引がありました（社内作成として数えています）: "
            + ", ".join(f"{k}={v}件" for k, v in unknown_creators.items())
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
        dday_direct(peak)["event"]["cost"] += cost
    expos.sort(key=lambda e: e["date"])

    # --- web 費用
    (web_cost, web_cost_day, web_cv_day, web_cv,
     wb_cost_day, wb_cv_day, wb_imp_day, wb_clk_day, adperf, adperf_day,
     ad_day) = fetch_ad_daily(sheets_token, week_starts, campaign_cfg)
    for w in week_starts:
        direct[w]["web"]["cost"] = web_cost[w]
        # CPLの分母は広告側のCV（申込延べ数）に揃える。HubSpotのリード数は
        # ユニークなので、同じ人の再申込があると分母が小さくCPLが高く出る。
        direct[w]["web"]["cv"] = web_cv[w]
    # LINE・紹介・その他は常に費用0（不明ではなく、発生していないことが分かっている）
    for w in week_starts:
        for ch in ("line", "referral", "other"):
            direct[w][ch]["cost"] = 0
    # 日別のweb費用・CV。週次と同じ元データ（広告日次シート）から引く。
    for d, v in web_cost_day.items():
        dday_direct(d)["web"]["cost"] = int(round(v))
    for d, v in web_cv_day.items():
        dday_direct(d)["web"]["cv"] = int(round(v))

    # --- 日次架電
    daily_calls, daily_conn = {}, {}
    first_call_of_contact = {}
    unknown_disp = {}
    no_direction = 0
    for c in calls:
        p = c.get("properties") or {}
        ts = parse_hs_datetime(p.get("hs_timestamp"))
        if not ts or ts < CALLS_START or ts > end_date:
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
                | set(daily_mtgs) | set(daily_called) | set(daily_leads)
)
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

    # --- ウェビナー別（掲載期間で切る）
    # 同じ「ウェビナー申込みフォーム」から入るため、お題の区別は期間でしかできない。
    # 期間が重なると同じリードを2回数えるので、設定側で重ならないようにしてある。
    # 商談・成約は期間内に獲得したリードに紐づくものを、いつ発生したかに関係なく数える
    # （獲得してから決まるまで時間がかかるため、期間で切ると成果が消える）。
    webinars_out = []
    wroutes = set(webinar_cfg.get("webinar_routes") or [])
    for wb in webinar_cfg.get("webinars") or []:
        try:
            ws = dt.date.fromisoformat(wb["start"])
            we = dt.date.fromisoformat(wb["end"])
        except (KeyError, ValueError):
            warn(f"webinars.json の期間が読めません: {wb}")
            continue
        use = set(wb.get("routes") or wroutes)
        leads = deals_n = won_n = amt = 0
        for cid, info in cinfo.items():
            if info["route"] not in use:
                continue
            if not (ws <= info["date"] <= we):
                continue
            leads += 1
            pc = per_contact.get(cid)
            if pc:
                deals_n += pc["deals"]
                won_n += pc["won"]
                amt += pc["amount"]
        # 費用とCVはウェビナーキャンペーンの日次だけを合計する。
        # 週次タブにはキャンペーン別の内訳が無いため、日次入力が始まる
        # 2026-06-09 より前は算出できない。その期間は設定ファイルに
        # 実額を書いて上書きする（cost / cv）。推定で埋めるより、
        # 手元にある正しい数字を入れたほうが確かなため。
        days = [ws + dt.timedelta(days=i) for i in range((we - ws).days + 1)]
        cost_days = [d for d in days if d in wb_cost_day]
        cost = int(round(sum(wb_cost_day[d] for d in cost_days))) if cost_days else None
        cv_days = [d for d in days if d in wb_cv_day]
        cv = int(round(sum(wb_cv_day[d] for d in cv_days))) if cv_days else None
        exact_days = len(cost_days)
        est_days = 0
        cv_est = 0
        # 設定に実額があればそれを優先する。
        if wb.get("cost") is not None:
            cost = int(wb["cost"])
            exact_days = len(days)
        if wb.get("cv") is not None:
            cv = int(wb["cv"])
        # 表示回数とクリックも同じ期間で切る。CPL = CPM ÷ (CTR × CVR) なので、
        # 回ごとのCPLが動いたときに「表示単価か、クリック率か、申込率か」を
        # 切り分けられる。手入力の上書きは用意しない（cost / cv だけの経緯）。
        imp_days = [d for d in days if d in wb_imp_day]
        imp = int(round(sum(wb_imp_day[d] for d in imp_days))) if imp_days else None
        clk_days = [d for d in days if d in wb_clk_day]
        clicks = int(round(sum(wb_clk_day[d] for d in clk_days))) if clk_days else None

        webinars_out.append({
            "name": wb.get("name") or "(名前なし)",
            "start": ws.isoformat(),
            "end": we.isoformat(),
            "days": len(days),
            "cost": cost,
            "cost_days": exact_days,
            "cost_est_days": est_days,
            "cost_manual": wb.get("cost") is not None,
            "cv": cv,
            "cv_days": len(cv_days),
            "cv_est_days": cv_est,
            "cv_manual": wb.get("cv") is not None,
            "imp": imp,
            "clicks": clicks,
            "leads": leads,
            "deals": deals_n,
            "won": won_n,
            "won_amount": amt,
        })

    # --- フィールドセールスの日次（架電の窓に縛られず全期間）
    fs_days = {}
    for d in sorted(set(daily_mtgs) | set(daily_props) | set(daily_wons)):
        if d > end_date:
            continue
        fs_days[d.isoformat()] = {
            "mtgs": daily_mtgs.get(d, 0),
            "props": daily_props.get(d, 0),
            "wons": daily_wons.get(d, 0),
            "wonamt": daily_wonamt.get(d, 0),
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
        "fs": fs_days,
        "is_attr": {k: is_attr[k] for k in sorted(is_attr)},
        "webinars": webinars_out,
        "adperf": {k: {kk: int(round(vv)) for kk, vv in v.items()}
                   for k, v in sorted(adperf.items())},
        "adperf_day": {k: {kk: int(round(vv)) for kk, vv in v.items()}
                       for k, v in sorted(adperf_day.items())},
        # 日 × 媒体/キャンペーン の生データ。表示側はまだ使っていない。
        # 媒体別・キャンペーン別のどちらでも切れるように持っておくもの。
        "ad_day": {d: {p: {kk: int(round(vv)) for kk, vv in m.items()}
                       for p, m in sorted(pairs.items())}
                   for d, pairs in sorted(ad_day.items())},
        "direct_day": {k: direct_day[k] for k in sorted(direct_day)},
        "agency_day": {k: agency_day[k] for k in sorted(agency_day)},
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
    ap.add_argument("--webinars", default="config/webinars.json")
    ap.add_argument("--ad-campaigns", default="config/ad_campaigns.json")
    ap.add_argument("--callers", default="config/callers.json")
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
    # ウェビナー定義は任意。無ければそのセクションを出さないだけ。
    webinar_cfg = {}
    if os.path.exists(args.webinars):
        with open(args.webinars, encoding="utf-8") as f:
            webinar_cfg = json.load(f)
    else:
        warn(f"{args.webinars} がありません。ウェビナー別の集計は出しません。")

    # 広告のキャンペーン定義は必須。無いと媒体×キャンペーンを判定できず、
    # ウェビナー別のCPLが全キャンペーンの合計になって倍以上に出る。
    with open(args.ad_campaigns, encoding="utf-8") as f:
        campaign_cfg = json.load(f)

    # 架電業者のアカウント。無ければ全部の架電が社内扱いになり、由来の
    # 切り分けが意味を持たなくなるので、無いことを警告して先へ進む
    # （他のセクションは架電由来と無関係に出せる）。
    vendor_ids = set()
    if os.path.exists(args.callers):
        with open(args.callers, encoding="utf-8") as f:
            vendor_ids = {str(v).strip()
                          for v in (json.load(f).get("vendor_owner_ids") or [])}
    if not vendor_ids:
        warn(
            f"{args.callers} に架電業者のアカウントがありません。"
            "架電由来の切り分けで業者ぶんが常に0件になります。"
        )

    previous = None
    if args.previous and os.path.exists(args.previous):
        try:
            with open(args.previous, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            warn(f"前回の data.json を読めませんでした（比較なしで続行）: {e}")

    sheets_token = google_access_token(sa_json)
    data = build(token, sheets_token, channel_map, webinar_cfg,
                 campaign_cfg, vendor_ids, end_date)

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
