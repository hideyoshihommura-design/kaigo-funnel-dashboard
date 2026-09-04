#!/usr/bin/env python3
"""介護校ファネルダッシュボード HTML ジェネレータ.

data.json（生の実数のみ）を受け取り、集計・4週移動平均・Chart.js グラフを含む
単一 HTML ファイルを出力する。

    python3 build_dashboard.py data.json -o funnel_dashboard.html

出力に分析コメント・示唆・改善提案・データ品質注記は一切含めない。
表・数値・グラフのみ。
"""

import argparse
import datetime as dt
import json
import os
import sys

# Chart.js はスキル同梱のものをHTMLに埋め込む。CDN参照だと、社内ネットワークや
# オフラインで開いたときにグラフ枠が空白になり、しかも「データが無い」のか
# 「読み込めていない」のか閲覧者に区別がつかない。埋め込めばその事故が起きない。
CHARTJS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "assets", "chart.umd.js"
)

CHANNELS = [
    ("event", "イベント"),
    ("web", "web"),
    ("line", "LINE"),
    ("referral", "紹介"),
    ("other", "その他"),
]
CHANNEL_KEYS = [k for k, _ in CHANNELS]

# 週次表で CPL を表示するチャネル。展示会は開催日とリード発生日がズレるため
# イベント行・合計行の週次 CPL は意味を持たない → 空欄にする。
CPL_VISIBLE_ROWS = {"web"}

# 架電業者の稼働開始日。ここからの累計を別カードで出す。業者を変えたり
# 体制が変わったらこの日付を変える。既存の「累計（期間内）」は期間指定に
# 連動するままで、こちらは日付固定。両者は入れ子（業者ぶんは期間内累計の一部）。
VENDOR_START = dt.date(2026, 8, 19)

# 取引作成者別の内訳。取引は相談申込に入った瞬間に作られるので、作成者＝
# 面談予約を取った人。判定は fetch_data.py 側（業者アカウントのIDは
# config/callers.json）。ここは並び順と表示名だけを持つ。
# 順番は 業者 → 社内 で固定。カードとテーブルで並びが違うと、上下の数字が
# どちらのものか毎回読み直すことになる。
ATTR_BUCKETS = [
    ("vendor", "架電業者 作成"),
    ("inhouse", "社内 作成"),
]
ATTR_KEYS = [k for k, _ in ATTR_BUCKETS]
ATTR_FIELDS = ("appts", "deals", "wons", "wonamt")

# FS活動量（data.json の fs）で持っている指標。
FS_FIELDS = ("mtgs", "props", "wons", "wonamt")

# 月次ファネルの列幅（px）。指標名・累計・各月。中身で決めさせない。
# 118 + 88 + 76×13 = 1,194px で、13ヶ月が横スクロールなしで収まる。
FU_W_K, FU_W_C, FU_W_M = 118, 88, 76

MA_WINDOW = 4

# ---- 日次架電ブロック ----
# 直近この日数は1日1行で出し、それより前は週次に丸めて下に続ける。
# 古い日を捨てずに丸めて残すので、行数を増やさずに履歴が追える。
#
# **このブロックの目的は「1日の活動量を確認すること」。**
# 窓を狭くすると、活動していた期間が週次に丸められて日単位の量が見えなくなる。
# 実際に30日にしていたとき、架電が最も多かった月がまるごと丸め側に入り、
# 日次の行はほぼ架電0の日で埋まっていた。窓は「日で見たい期間」より広く取る。
# 90日あれば四半期分の稼働が1日単位で残る。
DAILY_WINDOW_DAYS = 90

# 日次の行は平日だけ出す。土日に架電0の行が並ぶと、
# 「稼働日なのに架電していない日」が0の羅列に埋もれて見えなくなる。
# ただし土日に実績がある日は落とさず出す（データを消さないため）。
DAILY_WEEKDAYS_ONLY = True

WD_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 率のグラフを描くのに必要な、4週窓の最小母数。
# 2025年10〜12月は週のリードが0〜4件しかなく、「4人中4人が商談になった週」が
# 4週移動平均でも100%として立ち上がる。事実ではあるが、母数4人の100%と
# 母数600人の3%を同じ縦軸に並べても比較にならず、縦軸が0〜100%に広がって
# 直近の1〜10%の動きが潰れる。母数が足りない区間はグラフでは線を切り、
# 実数は週次表にそのまま残す（表は一切間引かない）。
# 母数の感覚が変わったらここだけ直せばよい。
MIN_LEADS_FOR_RATE = 10   # 商談化率
MIN_DEALS_FOR_RATE = 5    # 成約率

# 率のグラフを全期間で描き、母数が0の週は0%として線をつなぐか。
# True  … 2025年9月から1本の線になる。リードが1件も無い週は「何も起きていない」
#         として0%を打つ。真下の母数の棒が空なので、0%が「商談化が落ちた」ではなく
#         「そもそもリードが無い」ことは見て取れる。
# False … 母数のある期間だけに横軸を絞る（0÷0は計算できないので点を打たない）。
ZERO_FILL_EMPTY_WEEKS = True

# 商談化率・成約率のグラフを「期間累計」で描くか。
# 各週の値 = その週までの累計商談数 ÷ 累計リード数。
# 母数が週を追うごとに厚くなるので、リード1〜4件の週があっても率が跳ねず、
# 2025年9月から週次のまま1本で途切れずに描ける。
# しかも最終点が期間累計サマリの数字（商談化率2.7% / 成約率8.9%）と一致するので、
# 上に固定している総量と、そこに至る動きが同じ画面でつながって読める。
CUMULATIVE_RATE_CHARTS = True

# Aozora-cg コーポレートカラー
#   ブルー #00C4CC / グレー #575656 / ベージュ #f8f5ee
# 面（ベージュ地・白カード・グレー文字）はブランド色そのまま。
# 系列色は5チャネルを見分ける必要があるためブランド色だけでは足りず、
# ブルーを軸に配色を組み、dataviz の validate_palette.js で検証済み：
#   lightness band / chroma floor / CVD separation / normal-vision floor / contrast すべてPASS
# 並び順が検証時の隣接ペア順そのものなので、**色の割り当てを入れ替えないこと**。
# 入れ替えると隣接ペアのΔEが崩れ、色覚特性のある人が区別できなくなる
# （特に web のティールと緑は隣り合うとΔE 14.6 で不合格になる）。
BRAND = {
    "blue": "#00C4CC",    # コーポレートブルー
    "gray": "#575656",    # コーポレートグレー
    "beige": "#f8f5ee",   # コーポレートベージュ
}
COLORS = {
    "event": "#C25E22",
    "web": "#08959C",     # コーポレートブルーを線用に暗くした段（白地でコントラスト3:1以上）
    "line": "#8459A5",
    "referral": "#B8860B",
    "other": "#4E7A2E",
    "total": BRAND["gray"],
    "leads": "#08959C",
    "won": "#C25E22",
    # 率の系列。商談化率＝ブルー系、成約率＝オレンジ系で色を分ける。
    # 同じグレーの実線と破線で描くと、どちらがどちらか一目で分からず、
    # 4枚並んだときに他のグラフと見分けもつかない。
    # 淡い方は週次の生値、濃い方が4週移動平均。読ませたいのは移動平均なので
    # そちらを濃く太くし、断続する生値は背景に退かせる。
    "mtg": "#08959C",
    "mtg_raw": "#8FCFD3",
    "win": "#C25E22",
    "win_raw": "#E0AF8A",
    # 架電の棒は「接続できた分」と「できなかった分」の2色だけ。
    # 5チャネルの系列色とは別軸の対比なので、検証済みパレットの並びには触れない。
    # 接続はブルーの暗い段（週次CPLのwebと同じ）、未接続は既存の母数バーと
    # 同じ中立グレー。中立色を使うことで「未接続が多い」ことが色として
    # 悪目立ちせず、棒の高さ＝架電数がそのまま読める。
    "call_conn": "#08959C",
    "call_noans": "#C9C4B4",
}


# --------------------------------------------------------------------------
# 検証
# --------------------------------------------------------------------------

def fail(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"[WARN] {msg}", file=sys.stderr)


def validate(data):
    weeks = data.get("week_starts")
    if not weeks:
        fail("week_starts が空です。")

    dates = []
    for w in weeks:
        try:
            d = dt.date.fromisoformat(w)
        except (ValueError, TypeError):
            fail(f"week_starts に不正な日付があります: {w!r}")
        if d.weekday() != 0:
            fail(f"week_starts は月曜日である必要があります: {w} ({d.strftime('%A')})")
        dates.append(d)

    for prev, cur in zip(dates, dates[1:]):
        gap = (cur - prev).days
        if gap != 7:
            fail(
                f"week_starts が連続していません: {prev.isoformat()} → {cur.isoformat()} "
                f"({gap}日)。リード0の週も飛ばさず入れてください。"
            )

    direct = data.get("direct")
    agency = data.get("agency")
    if not isinstance(direct, dict):
        fail("direct がありません。")
    if not isinstance(agency, dict):
        fail("agency がありません。")

    for w in weeks:
        if w not in direct:
            fail(f"direct に週 {w} がありません（0埋めで入れてください）。")
        if w not in agency:
            fail(f"agency に週 {w} がありません（0埋めで入れてください）。")

        row = direct[w]
        extra = set(row) - set(CHANNEL_KEYS)
        missing = set(CHANNEL_KEYS) - set(row)
        if extra:
            fail(f"direct[{w}] に未知のチャネルキー: {sorted(extra)}")
        if missing:
            fail(f"direct[{w}] にチャネルキーが不足: {sorted(missing)}")

        for ch in CHANNEL_KEYS:
            cell = row[ch]
            for f in ("leads", "deals", "won"):
                v = cell.get(f)
                if not isinstance(v, int) or isinstance(v, bool):
                    fail(f"direct[{w}][{ch}].{f} は整数である必要があります: {v!r}")
                if v < 0:
                    fail(f"direct[{w}][{ch}].{f} が負数です: {v}")
            cost = cell.get("cost", None)
            if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
                fail(f"direct[{w}][{ch}].cost が不正です: {cost!r}")
            wa = cell.get("won_amount", 0)
            if not isinstance(wa, (int, float)) or wa < 0:
                fail(f"direct[{w}][{ch}].won_amount が不正です: {wa!r}")
            if cell["won"] == 0 and wa:
                warn(f"direct[{w}][{ch}]: 成約0件なのに成約金額 {wa}")
            if cell["won"] > cell["deals"]:
                warn(f"direct[{w}][{ch}]: 成約数 {cell['won']} > 商談数 {cell['deals']}")
            # 商談はリード獲得週に置く決まりなので、商談数がリード数を超えるのは
            # 1コンタクトが複数取引を持つ場合だけ。多発するなら週の寄せ方を疑う。
            if cell["deals"] > cell["leads"]:
                warn(f"direct[{w}][{ch}]: 商談数 {cell['deals']} > リード数 {cell['leads']}")

        a = agency[w]
        for f in ("leads", "deals", "won"):
            v = a.get(f)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                fail(f"agency[{w}].{f} が不正です: {v!r}")
        if a["won"] > a["deals"]:
            warn(f"agency[{w}]: 成約数 {a['won']} > 商談数 {a['deals']}")
        if a["deals"] > a["leads"]:
            warn(f"agency[{w}]: 商談数 {a['deals']} > リード数 {a['leads']}")

    lo, hi = dates[0], dates[-1] + dt.timedelta(days=6)
    for e in data.get("expos", []):
        try:
            d = dt.date.fromisoformat(e["date"])
        except (ValueError, TypeError, KeyError):
            fail(f"expos の date が不正です: {e!r}")
        if not (lo <= d <= hi):
            fail(f"expos '{e.get('name')}' の date {e['date']} が集計期間外です。")

    validate_calls(data)


def validate_calls(data):
    """calls は任意。無ければ日次架電ブロックを出さないだけで、他は従来通り動く。"""
    calls = data.get("calls")
    if calls is None:
        return
    if not isinstance(calls, dict):
        fail("calls は日付をキーにしたオブジェクトである必要があります。")
    for k, v in sorted(calls.items()):
        try:
            dt.date.fromisoformat(k)
        except (ValueError, TypeError):
            fail(f"calls に不正な日付キーがあります: {k!r}（YYYY-MM-DD）")
        if not isinstance(v, dict):
            fail(f"calls[{k}] がオブジェクトではありません。")
        # mtgs/props/wons/wonamt は一時期ここに入れていた（今は fs ブロック）。
        # 生成の途中で古い data.json を読むことがあるので、あっても弾かない。
        unknown = set(v) - {"calls", "connected", "appts", "called", "leads",
                            "mtgs", "props", "wons", "wonamt",
                            "vcalls", "vconn"}
        if unknown:
            fail(f"calls[{k}] に未知のキー: {sorted(unknown)}")
        for f in ("calls", "connected", "appts", "called", "leads"):
            n = v.get(f, 0)
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                fail(f"calls[{k}].{f} は0以上の整数である必要があります: {n!r}")
        if v.get("connected", 0) > v.get("calls", 0):
            warn(f"calls[{k}]: 接続数 {v.get('connected')} > 架電数 {v.get('calls', 0)}")

    validate_conversion(data)


def validate_conversion(data):
    """call_conversion は週次（月曜キー）。架電→面談予約の転換。任意。"""
    conv = data.get("call_conversion")
    if conv is None:
        return
    if not isinstance(conv, dict):
        fail("call_conversion は週（月曜日）をキーにしたオブジェクトである必要があります。")
    for k, v in sorted(conv.items()):
        try:
            d = dt.date.fromisoformat(k)
        except (ValueError, TypeError):
            fail(f"call_conversion に不正な日付キーがあります: {k!r}")
        if d.weekday() != 0:
            fail(f"call_conversion のキーは月曜日である必要があります: {k}")
        unknown = set(v) - {"called", "appointed"}
        if unknown:
            fail(f"call_conversion[{k}] に未知のキー: {sorted(unknown)}")
        for f in ("called", "appointed"):
            n = v.get(f, 0)
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                fail(f"call_conversion[{k}].{f} は0以上の整数である必要があります: {n!r}")
        # 分母より分子が多いのは突き合わせ間違い。転換率が100%を超える。
        if v.get("appointed", 0) > v.get("called", 0):
            warn(f"call_conversion[{k}]: 面談予約 {v.get('appointed')} > "
                 f"架電したリード {v.get('called', 0)}")

    validate_is_attr(data)


def validate_is_attr(data):
    """is_attr は日次。取引作成者別（架電業者／社内）の内訳。任意."""
    attr = data.get("is_attr")
    if attr is None:
        return
    if not isinstance(attr, dict):
        fail("is_attr は日付をキーにしたオブジェクトである必要があります。")
    fs = data.get("fs") or {}
    tot = {f: 0 for f in ATTR_FIELDS}
    for k, v in sorted(attr.items()):
        try:
            dt.date.fromisoformat(k)
        except (ValueError, TypeError):
            fail(f"is_attr に不正な日付キーがあります: {k!r}（YYYY-MM-DD）")
        unknown = set(v) - set(ATTR_KEYS)
        if unknown:
            fail(f"is_attr[{k}] に未知の作成者キー: {sorted(unknown)}")
        for b in ATTR_KEYS:
            row = v.get(b) or {}
            for f in ATTR_FIELDS:
                n = row.get(f, 0)
                if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                    fail(f"is_attr[{k}][{b}].{f} は0以上の整数である必要があります: {n!r}")
                tot[f] += n
            if row.get("wons", 0) == 0 and row.get("wonamt", 0):
                warn(f"is_attr[{k}][{b}]: 成約0件なのに成約金額 {row['wonamt']}")
    # 作成者2つを足した成約数は、FS側の成約数と同じ母集団（直契約）を
    # 割ったものなので一致するはず。ズレたらどちらかの絞り込みが変わっている。
    # FS側が多い方向は、成約日が集計期間の頭より前という稀なケースで起きうる。
    fs_wons = sum(v.get("wons", 0) for v in fs.values())
    if tot["wons"] > fs_wons:
        warn(f"is_attr の成約数合計 {tot['wons']} が fs の成約数合計 {fs_wons} を"
             "超えています。母集団の絞り込みがズレています。")

    # 面談予約は2つの軸で持っている。is_attr は「予約が入った日」、
    # direct は「そのリードを獲得した日」。同じ取引を違う日付に載せている
    # だけなので、直契約の合計は一致するはず。ズレたら軸の取り違えか、
    # どちらかの絞り込みが変わっている。
    direct_appts = sum(
        cell.get("appts", 0)
        for wk in (data.get("direct") or {}).values()
        for cell in wk.values()
    )
    if direct_appts and tot["appts"] != direct_appts:
        warn(f"面談予約の合計が軸によって違います: is_attr(予約日軸) {tot['appts']} "
             f"／ direct(獲得日軸) {direct_appts}。軸の取り違えを疑ってください。")


# --------------------------------------------------------------------------
# 集計
# --------------------------------------------------------------------------

def safe_div(num, den):
    """分母が 0/None、分子が None なら None を返す。0 や Infinity を作らない。"""
    if num is None or den is None:
        return None
    if den == 0:
        return None
    return num / den


def sum_costs(values):
    """判明している費用だけを合算。全部不明なら None。

    合計行は「その週に判明している支出額」を表す。期間累計サマリの総費用も
    同じ数え方なので、合計行を縦に足すとサマリの総費用に一致する。
    ここで「1つでも不明なら合計も不明」にすると、イベント費だけ判明している週
    （web費用が未取得な2026年4月以前の展示会週）で合計が空欄になり、
    イベント行に ¥220,000 と出ているのに合計は空欄という不可解な表になるうえ、
    列を足してもサマリの総費用に届かなくなる。

    合計行のCPLはどのみち常に空欄なので、部分的な費用で誤ったCPLが出る心配はない。
    """
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def build_rows(data):
    """週 → チャネル（+ total）→ 実数 の入れ子を作る。"""
    weeks = data["week_starts"]
    rows = {}
    for w in weeks:
        src = data["direct"][w]
        cells = {}
        for ch in CHANNEL_KEYS:
            c = src[ch]
            cells[ch] = {
                "leads": c["leads"],
                "cost": c.get("cost"),
                "cv": c.get("cv"),
                "deals": c["deals"],
                "won": c["won"],
                "won_amount": c.get("won_amount", 0),
            }
        cells["total"] = {
            "leads": sum(cells[ch]["leads"] for ch in CHANNEL_KEYS),
            "cost": sum_costs([cells[ch]["cost"] for ch in CHANNEL_KEYS]),
            "deals": sum(cells[ch]["deals"] for ch in CHANNEL_KEYS),
            "won": sum(cells[ch]["won"] for ch in CHANNEL_KEYS),
            "won_amount": sum(cells[ch]["won_amount"] for ch in CHANNEL_KEYS),
        }
        rows[w] = cells
    return rows


def moving_series(weeks, rows, key, field):
    """件数系の4週移動平均。窓が埋まらない先頭は None。

    費用は None（不明）を含む週があると平均が実態とズレるため、
    窓内に None が1つでもあれば None を返す。
    """
    raw = [rows[w][key][field] for w in weeks]
    out = []
    for i in range(len(raw)):
        if i + 1 < MA_WINDOW:
            out.append(None)
            continue
        window = raw[i - MA_WINDOW + 1: i + 1]
        if any(v is None for v in window):
            out.append(None)
        else:
            out.append(sum(window) / MA_WINDOW)
    return out


def suppress_weekly_small_base(weeks, rows, key, den_field, series, min_base):
    """その週の母数が min_base 未満の点を None にする（週次の生値用）。

    移動平均側は4週分をためた母数で判定するが、生値はその週の母数がすべてなので
    週単位で見る。リード1件の週の100%を線に載せないためのもの。
    """
    out = []
    for i, w in enumerate(weeks):
        den = rows[w][key][den_field]
        out.append(series[i] if (den or 0) >= min_base else None)
    return out


def suppress_small_base(weeks, rows, key, den_field, series, min_base):
    """4週窓の母数が min_base 未満の点を None にする（グラフ用）。

    率そのものは正しくても、母数が数件だと 0% か 100% にしか動かず、
    傾向を読む線としては情報がない。表には実数が残るので、
    ここで消しているのは「線」だけで、データではない。
    """
    dens = [rows[w][key][den_field] for w in weeks]
    out = []
    for i, v in enumerate(series):
        if v is None or i + 1 < MA_WINDOW:
            out.append(None)
            continue
        window = dens[i - MA_WINDOW + 1: i + 1]
        base = sum(x for x in window if x is not None)
        out.append(v if base >= min_base else None)
    return out


def moving_ratio(weeks, rows, key, num_field, den_field):
    """比率系の4週移動平均。窓内の合計÷合計で出す。

    比率の単純平均だと、リードが数件しかない週の外れ値が
    数百件の週と同じ重みで効いてしまい、実態から離れる。
    """
    nums = [rows[w][key][num_field] for w in weeks]
    dens = [rows[w][key][den_field] for w in weeks]
    out = []
    for i in range(len(weeks)):
        if i + 1 < MA_WINDOW:
            out.append(None)
            continue
        nw = nums[i - MA_WINDOW + 1: i + 1]
        dw = dens[i - MA_WINDOW + 1: i + 1]
        if any(v is None for v in nw) or any(v is None for v in dw):
            out.append(None)
        else:
            out.append(safe_div(sum(nw), sum(dw)))
    return out


def compute_direct(data):
    weeks = data["week_starts"]
    rows = build_rows(data)
    keys = CHANNEL_KEYS + ["total"]

    # CPLの分母。広告側のCV（申込延べ数）があればそれ、無ければリード数。
    # 移動平均は行のフィールドを合計するので、あらかじめ値として持たせる。
    for w in weeks:
        cells = rows[w]
        for ch in CHANNEL_KEYS:
            r = cells[ch]
            r["cvden"] = r.get("cv") or r["leads"]
        # 合計はチャネルごとの分母を足す。webだけCV、他はリード数になる。
        cells["total"]["cvden"] = sum(cells[ch]["cvden"] for ch in CHANNEL_KEYS)

    metrics = {}
    for k in keys:
        metrics[k] = {
            "leads": [rows[w][k]["leads"] for w in weeks],
            "cost": [rows[w][k]["cost"] for w in weeks],
            "deals": [rows[w][k]["deals"] for w in weeks],
            "won": [rows[w][k]["won"] for w in weeks],
            # CPLの分母は広告側のCV（申込延べ数）。HubSpotのリード数はユニークで、
            # 同じ人の再申込があると分母が小さくなりCPLが高く出る。
            # CVが無い週（広告シートに記入が無い期間）はリード数で代用する。
            "cpl": [safe_div(rows[w][k]["cost"], rows[w][k]["cvden"])
                    for w in weeks],
            "mtg_rate": [safe_div(rows[w][k]["deals"], rows[w][k]["leads"]) for w in weeks],
            "win_rate": [safe_div(rows[w][k]["won"], rows[w][k]["deals"]) for w in weeks],
            "ma": {
                "leads": moving_series(weeks, rows, k, "leads"),
                "cost": moving_series(weeks, rows, k, "cost"),
                "deals": moving_series(weeks, rows, k, "deals"),
                "won": moving_series(weeks, rows, k, "won"),
                "cpl": moving_ratio(weeks, rows, k, "cost", "cvden"),
                "mtg_rate": moving_ratio(weeks, rows, k, "deals", "leads"),
                "win_rate": moving_ratio(weeks, rows, k, "won", "deals"),
            },
        }
    return rows, metrics


def compute_agency(data):
    weeks = data["week_starts"]
    rows = {w: dict(data["agency"][w]) for w in weeks}
    wrapped = {w: {"a": rows[w]} for w in weeks}
    return rows, {
        "leads": [rows[w]["leads"] for w in weeks],
        "deals": [rows[w]["deals"] for w in weeks],
        "won": [rows[w]["won"] for w in weeks],
        "mtg_rate": [safe_div(rows[w]["deals"], rows[w]["leads"]) for w in weeks],
        "win_rate": [safe_div(rows[w]["won"], rows[w]["deals"]) for w in weeks],
        "ma": {
            "leads": moving_series(weeks, wrapped, "a", "leads"),
            "deals": moving_series(weeks, wrapped, "a", "deals"),
            "won": moving_series(weeks, wrapped, "a", "won"),
            "mtg_rate": moving_ratio(weeks, wrapped, "a", "deals", "leads"),
            "win_rate": moving_ratio(weeks, wrapped, "a", "won", "deals"),
        },
    }


def totals_direct(rows, weeks):
    leads = sum(rows[w]["total"]["leads"] for w in weeks)
    # 週次の合計行と同じ数え方（判明分だけ合算）。合計行を縦に足すと
    # ここの総費用に一致する。閲覧者が検算できることを優先している。
    cost = sum_costs([rows[w]["total"]["cost"] for w in weeks])
    deals = sum(rows[w]["total"]["deals"] for w in weeks)
    won = sum(rows[w]["total"]["won"] for w in weeks)
    amount = sum(rows[w]["total"]["won_amount"] for w in weeks)
    # CPLの分母は週次表と揃える（webはCV、他はリード数）。
    cvden = sum(rows[w]["total"]["cvden"] for w in weeks)
    return {
        "leads": leads,
        "cost": cost,
        "cpl": safe_div(cost, cvden),
        "deals": deals,
        "mtg_rate": safe_div(deals, leads),
        "won": won,
        "win_rate": safe_div(won, deals),
        "amount": amount,
    }


def totals_agency(rows, weeks):
    leads = sum(rows[w]["leads"] for w in weeks)
    deals = sum(rows[w]["deals"] for w in weeks)
    won = sum(rows[w]["won"] for w in weeks)
    amount = sum(rows[w].get("won_amount", 0) for w in weeks)
    return {
        "leads": leads,
        "deals": deals,
        "mtg_rate": safe_div(deals, leads),
        "won": won,
        "win_rate": safe_div(won, deals),
        "amount": amount,
    }


def monday_of(d):
    return d - dt.timedelta(days=d.weekday())


def compute_daily(data):  # noqa: C901
    """日次架電ブロックの行・グラフ・累計を組み立てる。

    直近 DAILY_WINDOW_DAYS 日は1日1行、それより前は週次に丸める。
    丸める側の一番新しい行が「途中までの週」にならないよう、日次区間の開始を
    週頭（月曜）まで戻して境界を揃える。揃えないと前後の週と比べられない行が
    1つだけ表に混ざる。
    """
    calls = data.get("calls")
    if not calls:
        return None

    dates = sorted(dt.date.fromisoformat(k) for k in calls)
    end = dates[-1]
    gen = data.get("generated_at")
    if gen:
        try:
            g = dt.date.fromisoformat(gen)
            # 生成日まで行を伸ばす。架電0の日を歯抜けにすると
            # 「架電していない日」が表から消えてしまう。
            if g > end:
                end = g
        except ValueError:
            pass

    def get(d, f):
        return calls.get(d.isoformat(), {}).get(f, 0)

    FIELDS = ("calls", "connected", "appts", "called", "leads")
    # データが1件も無い期間まで行を伸ばさない。窓の起点がデータの開始より前だと、
    # 先頭に全部0の行が並び、「架電していない日」と「まだ記録が始まっていない日」が
    # 同じ見た目になる。
    daily_from = max(monday_of(end - dt.timedelta(days=DAILY_WINDOW_DAYS - 1)),
                     monday_of(dates[0]))

    rows = []

    # ---- 週次に丸める区間 ----
    buckets = {}
    for d in dates:
        if d >= daily_from:
            continue
        b = buckets.setdefault(monday_of(d), dict.fromkeys(FIELDS, 0))
        for f in FIELDS:
            b[f] += get(d, f)
    for m in sorted(buckets):
        b = buckets[m]
        rows.append(dict(kind="week", key=m.isoformat(),
                         label=week_label(m.isoformat()),
                         rate=safe_div(b["connected"], b["calls"]), **b))

    # ---- 日次区間 ----
    d = daily_from
    while d <= end:
        v = {f: get(d, f) for f in FIELDS}
        # 土日は実績がある日だけ出す。
        if not (DAILY_WEEKDAYS_ONLY and d.weekday() >= 5 and not any(v.values())):
            rows.append(dict(kind="day", key=d.isoformat(),
                             label=f"{d.month}/{d.day}({WD_JA[d.weekday()]})",
                             rate=safe_div(v["connected"], v["calls"]), **v))
        d += dt.timedelta(days=1)

    day_rows = [r for r in rows if r["kind"] == "day"]
    tot = {f: sum(get(x, f) for x in dates) for f in FIELDS}

    # ---- 活動量 ----
    # 累計だけでは「今どれだけ動いているか」が読めない。
    # 3ヶ月で356件と言われても、1日あたり何件なのか、今週やれているのかが
    # 分からない。日次ブロックの用途は行動を今日直すことなので、
    # 直近稼働日・今週・1稼働日平均を前に出す。
    worked = [d for d in dates if get(d, "calls") > 0]
    wk_start = monday_of(end)
    # 「本日」と「今週」で軸を揃える。架電は直近稼働日、面談予約は本日、のように
    # 別の日を並べると、2つの数字が同じ日のものだと誤読される。
    activity = {
        "today": f"{end.month}/{end.day}({WD_JA[end.weekday()]})",
        "today_key": end.isoformat(),
        "today_calls": get(end, "calls"),
        "today_conn": get(end, "connected"),
        "today_called": get(end, "called"),
        "today_rate": safe_div(get(end, "connected"), get(end, "calls")),
        "today_conv": safe_div(get(end, "appts"), get(end, "called")),
        "today_appts": get(end, "appts"),
        "week_calls": sum(get(d, "calls") for d in dates if d >= wk_start),
        "week_appts": sum(get(d, "appts") for d in dates if d >= wk_start),
        # 平均の分母は「架電した日」。暦日で割ると、架電していない日が
        # 多いほど平均が下がり、稼働した日の量が見えなくなる。
        "per_day": safe_div(tot["calls"], len(worked)) if worked else None,
        "worked_days": len(worked),
    }

    # 架電業者ぶん。**日付（VENDOR_START 以降）では切らない。**
    # 業者アカウントのコール（vcalls / vconn）と、業者アカウントが作った
    # 面談予約（is_attr の vendor）で数える。日付で切ると、社内が1件でも
    # 架電した日から業者ぶんに混ざる。
    vcalls = data.get("calls") or {}
    vattr = data.get("is_attr") or {}
    vt = {
        "calls": sum(v.get("vcalls", 0) for v in vcalls.values()),
        "connected": sum(v.get("vconn", 0) for v in vcalls.values()),
        "appts": sum((v.get("vendor") or {}).get("appts", 0)
                     for v in vattr.values()),
    }
    vworked = [k for k, v in vcalls.items() if v.get("vcalls", 0) > 0]
    vfirst = min(vworked) if vworked else VENDOR_START.isoformat()
    vendor = {
        "from": vfirst,
        "calls": vt["calls"],
        "connected": vt["connected"],
        "rate": safe_div(vt["connected"], vt["calls"]),
        "appts": vt["appts"],
        # 商談化率の分母は架電数。接続数だと入力の有無で分母が動く。
        "conv": safe_div(vt["appts"], vt["calls"]),
        "per_day": safe_div(vt["calls"], len(vworked)) if vworked else None,
        "worked_days": len(vworked),
    }

    return {
        "rows": rows,
        "kpi": dict(rate=safe_div(tot["connected"], tot["calls"]), **tot),
        "activity": activity,
        "vendor": vendor,
        # グラフは日次区間だけ。日の棒と週の棒を同じ横軸に混ぜると、
        # 棒の高さが1日分なのか1週間分なのか区別できなくなる。
        "chart": {
            "labels": [r["label"] for r in day_rows],
            "dates": [r["key"] for r in day_rows],
            "connected": [r["connected"] for r in day_rows],
            "noans": [r["calls"] - r["connected"] for r in day_rows],
        },
        "span": f"{daily_from.isoformat()} 〜 {end.isoformat()}",
        "first": dates[0].isoformat(),
    }


# --------------------------------------------------------------------------
# 書式
# --------------------------------------------------------------------------

def f_int(v):
    return "" if v is None else f"{int(round(v)):,}"


def f_dec(v, nd=1):
    return "" if v is None else f"{v:,.{nd}f}"


def f_yen(v):
    return "" if v is None else f"¥{int(round(v)):,}"


def f_man_v(m):
    """万単位の数値部分。1000万以上は小数を落とす（桁が増えるため）."""
    return f"{round(m):,}" if abs(m) >= 1000 else f"{m:.1f}"


# ---- 月を横に並べる表 ----------------------------------------------------
# 前月「比」ではなく前月「差」で出す。比だと母数が小さい月に +19200%
# （3月1件→4月193件）のような値が出るうえ、母数0の月は計算できず空欄になる。
# 空欄の理由が「計算できない」「意味が無いので出さない」「そもそも出さない
# 設計」の3種類に分かれ、見た目で区別できなくなる。差ならどの月でも出せて、
# 空欄は一番左の月だけになる。


def d_num(cur, prev):
    """件数の前月差."""
    if cur is None or prev is None:
        return ""
    r = cur - prev
    return "±0" if r == 0 else ("+" if r > 0 else "") + f"{r:,}"


def d_man(cur, prev):
    """金額の前月差。万単位."""
    if cur is None or prev is None:
        return ""
    r = cur - prev
    if r == 0:
        return "±0"
    return ("+¥" if r > 0 else "-¥") + f_man_v(abs(r) / 10000) + "万"


def d_yen(cur, prev):
    """円のままの前月差。CPM・CPC・CPL のように桁が小さいものに使う
    （万単位にすると ¥751 が ¥0.1万 になって読めない）."""
    if cur is None or prev is None:
        return ""
    r = round(cur) - round(prev)
    return "±0" if r == 0 else ("+¥" if r > 0 else "-¥") + f"{abs(r):,}"


def f_pct2(v):
    """CTR・CVR は小数第2位まで。1%を下回る値が多く、1桁だと0.0%に潰れる."""
    return f_pct(v, 2)


def d_pt2(cur, prev):
    if cur is None or prev is None:
        return ""
    r = (cur - prev) * 100
    if abs(r) < 0.005:
        return "±0pt"
    return ("+" if r > 0 else "") + f"{r:.2f}pt"


def d_pt(cur, prev):
    """率の前月差はポイント。%の%は読み分けられない."""
    if cur is None or prev is None:
        return ""
    r = (cur - prev) * 100
    if abs(r) < 0.05:
        return "±0pt"
    return ("+" if r > 0 else "") + f"{r:.1f}pt"


def month_keys(day_keys):
    """日付キー（YYYY-MM-DD）から連続した月キーの列を作る.

    実績が1件も無い月も返す。消すと「12月の次が2月」になり、
    時間の流れが読めなくなる。
    """
    months = sorted({k[:7] for k in day_keys})
    if not months:
        return []
    out = []
    y, m = int(months[0][:4]), int(months[0][5:7])
    ey, em = int(months[-1][:4]), int(months[-1][5:7])
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def week_col_label(wk):
    """週の列見出し。月曜日を M/D で出す."""
    d = dt.date.fromisoformat(wk)
    return f"{d.month}/{d.day}"


def month_col_label(mk):
    return f"{mk[2:4]}/{int(mk[5:7])}"


def month_table(keys, buckets, rows, totals):
    """月を横に並べた表."""
    return period_table(keys, buckets, rows, totals, "data-m", month_col_label)


def week_table(keys, buckets, rows, totals):
    """週を横に並べた表。列が50を超えることがあるので横スクロール前提."""
    return period_table(keys, buckets, rows, totals, "data-w", week_col_label)


def period_table(keys, buckets, rows, totals, attr, labeler):
    """期間を横に並べた表のHTML.

    rows は (表示名, 値の取り方, 整形, 前期間差の出し方|None, 強調, 内訳か, 区切り線)。
    列幅は colgroup で固定する。auto のままだと ¥2,101,000 の列だけ広くなり、
    期間ごとに幅が変わって目が列を追えない。幅がそろえば数字は右揃え＋等幅なので
    桁が縦に並び、縦罫線が不要になる。
    """
    if not keys:
        return ""
    last = keys[-1]
    colg = (f'<colgroup><col style="width:{FU_W_K}px">'
            f'<col style="width:{FU_W_C}px">'
            + f'<col style="width:{FU_W_M}px">' * len(keys) + "</colgroup>")
    width = FU_W_K + FU_W_C + FU_W_M * len(keys)
    head = ('<tr><th class="fk">指標</th><th class="fc">累計</th>'
            + "".join(f"<th {attr}=\"{mk}\""
                      + (' class="now"' if mk == last else "")
                      + f">{labeler(mk)}</th>" for mk in keys)
            + "</tr>")
    body = []
    for name, get, fmt, delta, hl, sub, sep in rows:
        vals = [get(buckets.get(mk) or {}) for mk in keys]
        ds = [""] + [delta(vals[i], vals[i - 1]) if delta else ""
                     for i in range(1, len(vals))]
        cls = " ".join(c for c in (
            "main", "hl" if hl else "", "sub" if sub else "",
            "sep" if sep else "") if c)
        cells = []
        for mk, val, dtxt in zip(keys, vals, ds):
            # 値と前月差を1つのセルに上下2段で入れる。行を分けると指標7つで
            # 14行になり、半分が補助情報の行になってしまう。
            # 0を薄くしてはいけない。「成約0が続いている」はこの表で一番
            # 重要な事実で、薄くすると空欄と区別がつかず消えてしまう。
            cells.append(
                f"<td {attr}=\"{mk}\""
                + (' class="now"' if mk == last else "")
                + f"><b>{fmt(val)}</b>"
                + (f"<i>{dtxt or '&nbsp;'}</i>" if delta else "")
                + "</td>")
        body.append(
            f'<tr class="{cls}"><td class="fk">{name}</td>'
            f'<td class="fc"><b>{fmt(get(totals))}</b>'
            + ("<i>&nbsp;</i>" if delta else "") + "</td>"
            + "".join(cells) + "</tr>")
    return (f'<div class="fnl"><table style="width:{width}px">{colg}'
            f"<thead>{head}</thead><tbody>{''.join(body)}</tbody>"
            "</table></div>")


def f_man(v):
    """月次ファネルの金額。列幅を決めていたのが金額で、¥2,101,000 のままだと
    1列が100px近くなり13ヶ月が画面に収まらない。正確な額はヘッダーにある。"""
    if v is None:
        return ""
    return "0" if v == 0 else f"¥{f_man_v(v / 10000)}万"


def f_pct(v, nd=1):
    return "" if v is None else f"{v * 100:.{nd}f}%"


def week_label(iso):
    d = dt.date.fromisoformat(iso)
    e = d + dt.timedelta(days=6)
    return f"{d.month}/{d.day}–{e.month}/{e.day}"


def cell(value_txt, ma_txt):
    """本値の下に4週移動平均を小さく併記したセル。"""
    if value_txt == "" and ma_txt == "":
        return '<td class="num"></td>'
    ma = f'<span class="ma">{ma_txt}</span>' if ma_txt else ""
    return f'<td class="num">{value_txt}{ma}</td>'


def js(obj):
    return json.dumps(obj, ensure_ascii=False)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
:root{
  /* Aozora-cg コーポレートカラーをそのまま面に使う */
  --brand:#00C4CC; --ink:#575656; --beige:#f8f5ee;
  --ink-strong:#3E3D3C; --muted:#8A8785;
  --line:#E3DCCC; --bg:var(--beige); --card:#fff; --head:#F2EEE3;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN","Yu Gothic",
"Noto Sans JP",Meiryo,sans-serif;font-size:13px;line-height:1.5;}
.wrap{max-width:1440px;margin:0 auto;padding:28px 20px 64px;}
h1{font-size:22px;font-weight:700;margin:0 0 4px;letter-spacing:.01em;color:var(--ink-strong);}
.sub{color:var(--muted);font-size:12px;margin-bottom:26px;}
h2{font-size:16px;font-weight:700;margin:38px 0 14px;padding-bottom:8px;
color:var(--ink-strong);border-bottom:2px solid var(--brand);}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:22px 0 8px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;}
/* 期間累計は「常に見えている」ことが要件なので、画面上部に貼り付ける。
   週次表を49週スクロールしても、直契約と代理店の総量が視界から消えない。
   週次表は .tablewrap 内が独立したスクロール領域なので、
   表のヘッダ固定（sticky th）とこのバーは競合しない。

   直契約と代理店は横に並べて1段に収める（縦に積まない）。
   指標数が 8 と 6 で違うので、幅は 8:6 で配る。等分にすると
   代理店側だけスカスカで、直契約側の長い数字が詰まる。 */
.summary{position:sticky;top:0;z-index:60;
background:var(--bg);padding:12px 0 14px;margin:0 0 10px;
border-bottom:1px solid var(--line);
display:grid;grid-template-columns:minmax(0,8fr) minmax(0,6fr);gap:12px;
align-items:stretch;justify-content:stretch;}
/* 直契約と代理店の区別は、カードの左辺を色で塗って高さいっぱいに出す。
   見出しの横に細い線を置いても、カード自体が枠線で分かれている以上
   足せる情報がほとんど無く、区別の役に立たない。 */
.summary .card{padding:0 10px 0 0;display:flex;align-items:stretch;gap:0;
overflow:hidden;}
.summary .card .tag{flex:0 0 auto;display:flex;flex-direction:column;justify-content:center;
padding:14px 12px;margin-right:12px;min-width:74px;
font-size:12px;font-weight:700;line-height:1.35;white-space:nowrap;}
/* 見出し直下の副題も .sub を使っているため、ここは別名にする。
   同名だと .sub{color:var(--muted)} に文字色を奪われて、
   タグの中の「期間累計」がグレーになり読めなくなる。 */
/* 背景はコーポレートブルーそのまま（#00C4CC）だと白文字のコントラストが約2:1で
   足りないので、線用に用意した暗い段を使う。 */
.summary .card.d .tag{background:#08959C;color:#fff;}
.summary .card.a .tag{background:var(--ink);color:#fff;}
.summary .card .tag .taglabel{display:block;color:#fff;
font-size:10.5px;font-weight:400;opacity:.9;}
/* 指標は何があっても1行。折り返すと「2段」になり、
   一目で全部が視界に入るという固定バーの目的が崩れる。 */
.kpis{display:flex;flex-wrap:nowrap;gap:0;flex:1;min-width:0;}
/* 幅を等分（flex:1 1 0）すると、"5" のような1桁と ¥4,455,106 が同じ幅になり、
   長い数字だけが枠にぎゅうぎゅうに詰まる。中身に応じて幅を配る。 */
/* 幅が足りないときは、枠は縮めてよい。守るのは「1行に収まること」で、
   文字を小さくして対応する（下の .v の clamp が vw に連動する）。
   枠側に最低幅を持たせると、縮められないぶんが横にはみ出して切れる。 */
.kpi{flex:1 1 auto;min-width:0;padding:14px 8px;
border-left:1px solid var(--line);}
.kpi:first-child{border-left:none;}
.kpi .k{font-size:clamp(8.5px,0.68vw,10.5px);color:var(--muted);white-space:nowrap;
overflow:hidden;text-overflow:ellipsis;letter-spacing:.03em;line-height:1.2;}
/* 総費用・成約金額は7桁+¥で最長になる。clamp で枠幅に追従させ、桁が切れないようにする。
   係数を 1.2vw から下げてある。1.2vw だと 1280px 幅で 15.4px になり、
   直契約8枠＋代理店6枠の合計が枠幅を 61px 超えて成約金額の末尾が切れていた。 */
.kpi .v{font-size:clamp(10px,0.98vw,16px);font-weight:700;margin-top:4px;
color:var(--ink-strong);
font-variant-numeric:tabular-nums;letter-spacing:-.02em;white-space:nowrap;}
.kpi .v.empty::after{content:"—";color:var(--line);}
/* 1440px級だと直契約8＋代理店6の計14枠を横1列に置けるが、
   700〜800pxのパネル幅では物理的に入らない。そこではカードを縦に積むが、
   カードの中は必ず1行のまま（タグは左、指標は折り返さない）。 */
/* かつてここに `@media(min-width:1251px){.kpi{min-width:max-content}}` があったが、
   これが切れる原因だった。枠を content 幅より縮めない指定なので、2枚並べるのに
   必要な約1390pxに満たない 1251〜1390px では、はみ出したぶんが
   .card の overflow:hidden で切り落とされていた（1280px で61px）。
   枠は縮めてよく、代わりに上の clamp で文字を小さくして1行を守る。 */
@media(max-width:1250px){
  .summary{grid-template-columns:minmax(0,1fr);}
  .kpi{padding:12px 9px;}
  .summary .card .tag{min-width:64px;padding:12px 10px;margin-right:10px;}
}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));
gap:16px;margin-bottom:8px;}
.charts.one{grid-template-columns:minmax(0,1fr);}
.chart{position:relative;height:300px;}
.chart.tall{height:232px;}
.base{position:relative;height:72px;margin-top:2px;}
.baselabel{font-size:10px;color:var(--muted);text-align:right;margin-top:2px;}
/* 表は折りたたんであるので、開いた時は幅をいっぱいに使う。 */
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;
background:var(--card);max-height:min(74vh,900px);overflow-y:auto;}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:12px;
font-variant-numeric:tabular-nums;}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;
white-space:nowrap;}
/* 列の少ない表で、余った幅を末尾の空き列に吸わせる。
   全幅のまま列を均等に伸ばすと、3桁の数字ひとつに400px超の列が割り当たり、
   週ラベルと数値が離れて同じ行を追いにくくなる。枠は全幅のまま、
   実際の列だけ内容幅に寄せて左に固める。 */
th.pad,td.pad{width:100%;padding:0;}
/* 表はそれぞれ全幅を使うので縦に積む。折りたたみがあるので、
   閉じている間は見出し1行しか場所を取らない。 */
.tabgrid{display:block;}

/* 表は開いたときだけ場所を取ればいい。閉じているときは見出しだけ残す。
   <details> を使うのは、JSが無くても開閉でき、キーボードでも操作でき、
   閉じた中身もブラウザのページ内検索が拾うため。自前の開閉ボタンにすると
   このどれも自分で作り直すことになる。 */
details.fold{margin:14px 0 0;}
/* 幅は中身に合わせず、横いっぱいに広げる。inline-flex にしていたので
   ラベルの長さで1つずつ幅が変わり、押せる場所の大きさが不揃いだった。
   横幅をそろえると、複数並んだときに「同じ種類の操作」だと分かる。 */
details.fold>summary{list-style:none;cursor:pointer;
display:flex;align-items:center;gap:9px;
font-size:13.5px;font-weight:700;color:var(--ink);
padding:11px 14px;border:1px solid var(--line);border-radius:8px;
background:var(--head);user-select:none;}
details.fold>summary::-webkit-details-marker{display:none;}
details.fold>summary:hover{border-color:var(--brand);background:#eaf4f5;}
details.fold>summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px;}
/* 三角は塗った四角に白抜きで置く。文字色だけの三角は装飾に見えて、
   押せる場所だと気づかれない。 */
details.fold>summary .tri{flex:0 0 auto;width:18px;height:18px;
display:inline-flex;align-items:center;justify-content:center;
font-size:9px;color:#fff;background:#08959C;border-radius:4px;
transition:transform .15s;}
details.fold[open]>summary .tri{transform:rotate(90deg);}
/* 閉じていても「中に何がどれだけあるか」は出す。件数が見えないと、
   開くまで中身の見当がつかず、結局すべて開いて確認することになる。
   右端に寄せると、縦に並んだボタンで数字の位置がそろって見比べられる。 */
details.fold>summary .cnt{margin-left:auto;padding-left:14px;
font-weight:400;font-size:11.5px;color:var(--muted);text-align:right;}
/* 折りたたみの中に月次テーブルを入れるときの余白。テーブルが枠に
   ぴったり付くと、開閉の境目が分からなくなる。 */
details.fold>.foldin{padding:0 0 10px;}
details.fold[open]>summary{margin-bottom:9px;}
details.fold>.legend{margin:0 2px 7px;}
@media(prefers-reduced-motion:reduce){details.fold>summary .tri{transition:none;}}
/* 横スクロールした時に日付・週を見失わないよう左端は残す。 */
.tabgrid td.wk,.tabgrid thead th:first-child{position:sticky;left:0;}
.tabgrid td.wk{z-index:1;}
.tabgrid thead th:first-child{z-index:3;}
th{background:var(--head);font-weight:600;color:var(--ink);text-align:right;
position:sticky;top:0;z-index:2;font-size:11px;}
th:first-child,th:nth-child(2){text-align:left;}
td.wk{text-align:left;font-weight:600;background:var(--card);
border-right:1px solid var(--line);vertical-align:top;padding-top:8px;
color:var(--ink-strong);}
td.ch{text-align:left;color:var(--muted);}
td.num{font-variant-numeric:tabular-nums;}
tr.total td{font-weight:700;background:var(--beige);color:var(--ink-strong);}
tr.wkstart td{border-top:2px solid var(--line);}
.ma{display:block;font-size:10px;color:var(--muted);font-weight:400;margin-top:1px;}
.legend{font-size:11px;color:var(--muted);margin:6px 2px 0;}
/* 期間指定。固定サマリの上に置く（サマリは sticky なので、その下に置くと
   スクロール時にバーの裏に隠れてしまう）。 */
.range{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
margin:0 0 16px;font-size:12px;color:var(--muted);}
.range label{font-weight:600;color:var(--ink);letter-spacing:.03em;}
.range input[type=date]{font:inherit;font-size:12px;color:var(--ink);
padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);}
.range .dash{color:var(--muted);}
.range button{font:inherit;font-size:12px;font-weight:600;cursor:pointer;
padding:6px 14px;border-radius:6px;border:1px solid #08959C;
background:#08959C;color:#fff;}
.range button.ghost{background:var(--card);color:#08959C;}
/* 日次架電の累計。サマリ（.summary）は直契約8＋代理店6の指標を1行に収める
   前提で幅を検証してあるので、そこに3枚目のカードを差すと1行が保てない。
   日次架電セクションを最上段に置き、その先頭にこの帯を出すことで、
   固定サマリの直下＝開いた瞬間に見える位置に累計が入る。 */
.callsum{margin:0 0 14px;}
.callsum .kpis{flex-wrap:wrap;}
/* 活動量と累計は役割が違うので分けて並べる。累計だけを並べると
   「今どれだけ動いているか」が読めず、日次ブロックの用途を果たさない。
   活動量を左（先に目に入る側）に置く。 */
/* 本日と累計は上下に積む。横に並べると1枚あたりの幅が半分になり、
   6〜8項目が1行に収まらなくなる。項目が折り返して2段になると、
   上下の同じ位置にある数字が対応しなくなり、見比べられない。 */
.actsum{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;margin:0 0 14px;
align-items:stretch;}
.actsum .card{padding:0 10px 0 0;display:flex;align-items:stretch;overflow:hidden;}
/* 中は必ず1行。入らない時は折り返さず、文字を縮めて収める
   （.kpi .v の clamp が画面幅に追従する）。 */
.actsum .kpis{flex-wrap:nowrap;}
.actsum .card .tag{flex:0 0 auto;display:flex;flex-direction:column;
justify-content:center;padding:14px 12px;margin-right:12px;min-width:74px;
font-size:12px;font-weight:700;line-height:1.35;white-space:nowrap;}
.actsum .card.act .tag{background:#08959C;color:#fff;}
.actsum .card.cum .tag{background:var(--ink);color:#fff;}
/* 業者ぶんは期間指定で動かない固定の集計。上2枚と役割が違うので色も変える。 */
.actsum .card.vendor .tag{background:#8459A5;color:#fff;}
/* 作成者別の2枚は同じ指標を分けたものなので、IS活動量で使っている色を流用する。
   業者はIS活動量の業者カードと同じ紫、社内は活動量と同じ青緑。ここで別の色を
   当てると、同じ「架電業者」が場所によって違う色になり結び付けられなくなる。 */
.actsum .card .tag .taglabel{display:block;color:#fff;font-size:10.5px;
font-weight:400;opacity:.9;}

/* 月次ファネル。月を横に並べる唯一の表なので、専用の指定を持つ。
   指標名と累計の2列を左に固定しないと、右にスクロールした瞬間に
   どの行を見ているのか分からなくなる（横並びの表で一番効く）。 */
h2 .h2sub{font-size:11.5px;color:var(--muted);font-weight:400;margin-left:10px;}
.fnl{overflow-x:auto;border:1px solid var(--line);border-radius:8px;
background:var(--bg);margin:0 0 16px;}
/* 列幅は colgroup で固定する（table-layout:fixed）。auto のままだと
   ¥2,101,000 の月だけ広くなり、月ごとに幅が変わって目が列を追えない。
   幅がそろえば数字は右揃え＋等幅なので桁が縦に並び、縦罫線が不要になる。 */
.fnl table{border-collapse:separate;border-spacing:0;white-space:nowrap;
font-size:13px;font-variant-numeric:tabular-nums;table-layout:fixed;}
.fnl th,.fnl td{padding:7px 8px;text-align:right;overflow:hidden;
vertical-align:top;border-bottom:1px solid var(--line);}
.fnl thead th{background:var(--head);font-size:11.5px;color:var(--muted);
font-weight:700;position:sticky;top:0;z-index:1;}
.fnl .fk{text-align:left;position:sticky;left:0;background:var(--bg);z-index:2;
border-right:1px solid var(--line);vertical-align:middle;}
.fnl thead .fk{background:var(--head);z-index:3;}
.fnl .fc{position:sticky;left:118px;background:#eef2f6;z-index:2;
border-right:2px solid #8fa0b0;font-weight:700;}
.fnl thead .fc{background:#e2e8ee;z-index:3;}
/* 値と前月差は1つのセルの中で上下2段。行を分けると指標7つで14行になり、
   半分が補助情報の行になる。
   **薄い扱いは前月差の1種類だけにする。** 0を薄く、値を細く、背景を淡く、
   線を薄く、と薄いものを重ねた版を作ったが、全体が霞んでどこを見ればいいか
   分からなくなった。とくに0を薄くしたのが致命的で、「成約0が3ヶ月続いている」
   という一番重要な事実が消えていた。濃さの差で階層を作る。 */
.fnl td b{display:block;font-weight:600;line-height:1.3;}
.fnl td i{display:block;font-style:normal;font-size:11.5px;color:#7a8794;
line-height:1.3;margin-top:1px;}
.fnl tr.main.hl td b{font-weight:700;}
/* 直近の月。列全体を塗ると、中身が0だらけの月では「空欄の列を強調している」
   ように見える。見出しだけを塗って位置を示す。 */
.fnl thead th.now{background:#08959C;color:#fff;}
/* ファネルの段の切れ目。通常の罫線と区別がつく濃さにする。 */
.fnl tr.sep td{border-top:2px solid #8fa0b0;}
/* 段階間の率は一段下げて、上下の行の「あいだ」の数字だと分かるようにする。 */
.fnl tr.main.sub td.fk{padding-left:22px;color:var(--muted);font-weight:400;}

/* 週次に丸めた行は、日次の行と地色で区別する。
   同じ見た目だと「8/25」と「7/13–7/19」が同列に見えて、
   棒の高さや数値が1日分か1週間分か取り違える。 */
tr.roll td{background:var(--head);color:var(--muted);}
tr.roll td.wk{background:var(--head);color:var(--muted);font-weight:600;}
"""

CHART_JS = """
/* 期間フィルタでグラフを作り直すため、生成したインスタンスを id で覚えておく。
   destroy せずに同じ canvas へ new Chart すると、Chart.js が
   「Canvas is already in use」で落ちる。 */
var CH={};
function reg(id,cfg){
  if(CH[id]){CH[id].destroy();}
  CH[id]=new Chart(document.getElementById(id),cfg);
}

Chart.defaults.font.family='-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",'
 +'"Yu Gothic","Noto Sans JP",Meiryo,sans-serif';
Chart.defaults.font.size=11;
Chart.defaults.color='#8A8785';

function baseOpts(fmt,intAxis){
  return {
    responsive:true, maintainAspectRatio:false,
    interaction:{mode:'index',intersect:false},
    spanGaps:false,
    plugins:{
      legend:{position:'bottom',labels:{boxWidth:10,boxHeight:10,usePointStyle:true,
        pointStyle:'line',padding:12}},
      tooltip:{callbacks:{label:function(c){
        if(c.parsed.y===null||c.parsed.y===undefined) return null;
        return c.dataset.label+': '+fmt(c.parsed.y);}}}
    },
    scales:{
      x:{grid:{display:false},ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:14}},
      y:{beginAtZero:true,grid:{color:'#EFEADC'},
         ticks:{precision:intAxis?0:undefined,callback:function(v){return fmt(v);}}}
    },
    elements:{point:{radius:0,hitRadius:12,hoverRadius:4},line:{borderWidth:2,tension:.25}}
  };
}
var fInt=function(v){return Math.round(v).toLocaleString();};
var fYen=function(v){return '¥'+Math.round(v).toLocaleString();};
var fPct=function(v){return (v*100).toFixed(1)+'%';};

function mkLine(id,labels,datasets,fmt,intAxis,hideX){
  var o=baseOpts(fmt,intAxis);
  if(hideX){
    o.scales.x.ticks.display=false;
    /* 下に母数の帯が続くので、凡例が間に挟まると上下が別グラフに見えてしまう。
       ペアで読ませたいので凡例は上に出す。 */
    o.plugins.legend.position='top';
    o.plugins.legend.align='start';
  }
  reg(id,{type:'line',data:{labels:labels,datasets:datasets},options:o});
}

/* リード数は「合計がいくつで、その内訳がどのチャネルか」を見る量的データ。
   6本の折れ線を重ねると、値の小さいLINE・紹介・その他が軸に張り付いて
   区別できず、合計線と各チャネル線も交差して読み取れない。
   積み上げ棒なら棒の高さがそのまま合計で、色の厚みが構成比になり、
   1つの図で「合計」と「内訳」を同時に読める。 */
function mkStackedBar(id,labels,bars,maLabel,maData,maColor,fmt){
  var dsets=bars.map(function(b){return {type:'bar',label:b.label,data:b.data,
    backgroundColor:b.color,borderWidth:0,stack:'s',
    barPercentage:1,categoryPercentage:.9};});
  /* 凡例で棒（塗り）と線を区別させたいので、線の方は塗りを持たせない。
     背景色を線色にすると凡例が黒い四角になり、積み上げの1チャネルに見えてしまう。 */
  dsets.push({type:'line',label:maLabel,data:maData,borderColor:maColor,
    backgroundColor:'transparent',borderDash:[5,4],borderWidth:1.5,fill:false,
    pointRadius:0,tension:.25});
  reg(id,{data:{labels:labels,datasets:dsets},
    options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{position:'bottom',labels:{boxWidth:10,boxHeight:10,padding:12}},
        tooltip:{callbacks:{label:function(c){
          if(c.parsed.y===null||c.parsed.y===undefined||c.parsed.y===0) return null;
          return c.dataset.label+': '+fmt(c.parsed.y);}}}},
      scales:{x:{stacked:true,grid:{display:false},
                 ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:12}},
              y:{stacked:true,beginAtZero:true,grid:{color:'#EFEADC'},
                 ticks:{precision:0,callback:function(v){return fmt(v);}}}}}});
}

/* 架電数の日次棒。1本の棒の高さが架電数で、内訳が接続できた／できなかった。
   接続数を別の棒や別グラフにすると、架電を増やしたのか繋がるようになったのかを
   2つの図を見比べて判断することになる。積み上げなら1本で両方読める。 */
function mkCallBar(id,labels,connected,noans,cConn,cNo){
  reg(id,{type:'bar',
    data:{labels:labels,datasets:[
      {label:'接続',data:connected,backgroundColor:cConn,borderWidth:0,stack:'c',
       barPercentage:1,categoryPercentage:.86},
      {label:'未接続',data:noans,backgroundColor:cNo,borderWidth:0,stack:'c',
       barPercentage:1,categoryPercentage:.86}]},
    options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{position:'bottom',labels:{boxWidth:10,boxHeight:10,padding:12}},
        tooltip:{callbacks:{
          label:function(c){
            if(!c.parsed.y) return null;
            return c.dataset.label+': '+Math.round(c.parsed.y).toLocaleString();},
          footer:function(items){
            var t=0;items.forEach(function(i){t+=i.parsed.y||0;});
            return '架電数: '+t.toLocaleString();}}}},
      scales:{x:{stacked:true,grid:{display:false},
                 ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:16}},
              y:{stacked:true,beginAtZero:true,grid:{color:'#EFEADC'},
                 ticks:{precision:0,callback:function(v){
                   return Math.round(v).toLocaleString();}}}}}});
}

/* 率の線の真下に置く「母数」の帯。率と件数は単位が違うので同じ縦軸には重ねず、
   時間軸だけを揃えて上下に並べる。1枚のグラフに縦軸を2本置くと、
   どちらの目盛りで読むのか分からなくなり、率の変化も件数の変化も誤読される。 */
function mkBar(id,labels,label,data,color){
  reg(id,{type:'bar',
    data:{labels:labels,datasets:[{label:label,data:data,
      backgroundColor:color,borderWidth:0,barPercentage:1,categoryPercentage:.92}]},
    options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:function(c){
          return label+': '+Math.round(c.parsed.y).toLocaleString();}}}},
      scales:{x:{grid:{display:false},
                 ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:14}},
              y:{beginAtZero:true,grid:{color:'#EFEADC'},
                 ticks:{precision:0,maxTicksLimit:3,
                        callback:function(v){return Math.round(v).toLocaleString();}}}},
      }});
}
"""


DRAW_JS = r"""
/* 期間を受け取って全グラフを描き直す。
   移動平均・CPLなどは Python で計算した配列を「切って」使う（値は再計算しない）。
   商談化率・成約率は期間累計なので、期間の頭から数え直す。 */
function drawAll(from,to){
  var iw=weekIdx(WK,from,to);

  var bars=[],b;
  for(b=0;b<LEADS_BARS.length;b++){
    bars.push({label:LEADS_BARS[b].label,
               data:pick(LEADS_BARS[b].data,iw),
               color:LEADS_BARS[b].color});
  }
  mkStackedBar('c_leads',pick(L,iw),bars,'合計 (4週移動平均)',
               pick(LEADS_MA,iw),TOTC,fInt);

  var ic=weekIdx(CPL_W,from,to),cds=[],k;
  for(k=0;k<CPL_DS.length;k++){cds.push(sliceDs(CPL_DS[k],ic));}
  /* CPL は費用が判明している週だけを横軸に取る。ラベルは週キーから引く。 */
  mkLine('c_cpl',pick(CPL_W,ic).map(function(w){return RAW.wklabel[w];}),cds,fYen);

  var mds,wds;
  if(CUM){
    /* 期間累計を期間の頭からやり直す。こうすると線の右端が
       上のKPI（商談化率・成約率）と必ず一致する。 */
    var n=0,d=0,ms=[],i;
    for(i=0;i<iw.length;i++){
      var w=RAW.weeks[WK[iw[i]]];
      n+=w.deals; d+=w.leads;
      /* 累計の分母が0の週は点を打たない。0%を打つとKPIが空欄なのに
         グラフだけ0%の線が伸び、どちらが正しいのか読めなくなる。
         先頭に点が無い週が並ぶだけで、線はデータのある週から始まる。 */
      ms.push(d===0?null:n/d);
    }
    var n2=0,d2=0,vs=[];
    for(i=0;i<iw.length;i++){
      var w2=RAW.weeks[WK[iw[i]]];
      n2+=w2.won; d2+=w2.deals;
      vs.push(d2===0?null:n2/d2);
    }
    mds=[sliceDs(MTG_DS[0],[])]; mds[0].data=ms;
    wds=[sliceDs(WIN_DS[0],[])]; wds[0].data=vs;
  }else{
    mds=[];wds=[];
    for(k=0;k<MTG_DS.length;k++){mds.push(sliceDs(MTG_DS[k],iw));}
    for(k=0;k<WIN_DS.length;k++){wds.push(sliceDs(WIN_DS[k],iw));}
  }
  mkLine('c_mtg',pick(L,iw),mds,fPct,false,true);
  mkBar('c_mtg_b',pick(L,iw),'週次リード数',pick(MTG_BASE,iw),'#C9C4B4');
  mkLine('c_win',pick(L,iw),wds,fPct,false,true);
  mkBar('c_win_b',pick(L,iw),'週次商談数',pick(WIN_BASE,iw),'#C9C4B4');

  var ads=[],ards=[];
  for(k=0;k<AG_DS.length;k++){ads.push(sliceDs(AG_DS[k],iw));}
  for(k=0;k<AG_RATE_DS.length;k++){ards.push(sliceDs(AG_RATE_DS[k],iw));}
  mkLine('c_ag',pick(L,iw),ads,fInt,true);
  mkLine('c_agr',pick(L,iw),ards,fPct,false,true);
  mkBar('c_agr_b',pick(L,iw),'リード数',pick(AG_LEADS,iw),'#C9C4B4');

  if(document.getElementById('c_call')){
    var id_=idxOf(DAY_D,from,to);
    mkCallBar('c_call',pick(DAY_L,id_),pick(DAY_C,id_),pick(DAY_N,id_),CC,CN);
  }
}
"""

FILTER_JS = r"""
/* ---------------- 期間フィルタ ----------------
   日次の数字はこのHTMLに全部埋め込んであるので、再生成せずブラウザ側で切れる。

   切り方の原則が2つある。取り違えると数字が食い違う。

   1. **移動平均は切らない。** 4週移動平均は、その週の値を出すのに前の4週を使う。
      期間の頭でも窓は期間外まで遡る。それが移動平均の正しい姿なので、
      表示範囲だけを絞り、値は再計算しない。

   2. **期間累計は期間の頭からやり直す。** 商談化率・成約率のグラフは
      「その週までの累計商談数 ÷ 累計リード数」で描いている。期間を切ったら
      累計も切った頭から数え直す。そうしないと線の右端が上のKPIと一致しない。
      一致しないグラフは、どちらが正しいのか閲覧者に判断できない。

   週次の数字は週単位でしか切れないので、指定した日付を含む週は
   まるごと入る（週の途中で切ると分母と分子が別の集団になる）。 */
function pick(a,ix){var o=[],k;for(k=0;k<ix.length;k++){o.push(a[ix[k]]);}return o;}
function idxOf(keys,from,to){
  var o=[],i;for(i=0;i<keys.length;i++){if(keys[i]>=from&&keys[i]<=to){o.push(i);}}
  return o;
}
/* 週の配列に対する添字。**週の終わりで判定する**（開始日ではない）。
   指定日を含む週はまるごと入れる、というKPI側の数え方と揃えるため。
   ここを idxOf（開始日で判定）にすると、KPIとグラフで週が1つズレて、
   期間累計の線の右端がKPIと一致しなくなる。実際に8月で
   KPI 1.9% / グラフ 2.8% という食い違いが出た。 */
function weekIdx(keys,from,to){
  var o=[],i;
  for(i=0;i<keys.length;i++){
    var e=RAW.wkend[keys[i]];
    if(e>=from && keys[i]<=to){o.push(i);}
  }
  return o;
}
function sliceDs(d,ix){
  var c={},k;for(k in d){if(d.hasOwnProperty(k)){c[k]=d[k];}}
  c.data=pick(d.data,ix);return c;
}
function jInt(v){return (v===null||v===undefined)?'':Math.round(v).toLocaleString();}
function jDec(v){return (v===null||v===undefined)?'':v.toFixed(1);}
function jYen(v){return (v===null||v===undefined)?'':'¥'+Math.round(v).toLocaleString();}
function jPct(v,nd){return (v===null||v===undefined)?'':(v*100).toFixed(nd===undefined?1:nd)+'%';}
function div(n,d){return (d===0||d===null||n===null)?null:n/d;}
function setK(id,txt){
  var e=document.getElementById(id);
  if(!e){return;}
  e.textContent=txt;
  if(txt===''){e.classList.add('empty');}else{e.classList.remove('empty');}
}
/* 週の集合。指定日を含む週はまるごと対象にする。 */
function weekKeys(from,to){
  var o=[],i;
  for(i=0;i<WK.length;i++){
    var end=RAW.wkend[WK[i]];
    if(end>=from && WK[i]<=to){o.push(WK[i]);}
  }
  return o;
}
function showRows(from,to){
  var trs=document.querySelectorAll('tr[data-d]'),i;
  var wk={},ws=weekKeys(from,to),k;
  for(k=0;k<ws.length;k++){wk[ws[k]]=1;}
  for(i=0;i<trs.length;i++){
    var d=trs[i].getAttribute('data-d');
    /* 日次の行は日付で、週次の行は週キーで判定する。
       週次の行を日付で切ると、週の頭が範囲外なだけで週ごと消える。 */
    var keep = wk[d] ? true : (d>=from && d<=to);
    trs[i].style.display = keep ? '' : 'none';
  }
}
function apply(from,to){
  if(!from){from='0000-01-01';}
  if(!to){to='9999-12-31';}
  var ws=weekKeys(from,to),i,w;
  /* 日別の広告費が揃っている期間なら、日単位でそのまま切る。
     それより前は日次入力タブが無いので、従来どおり週で足す。 */
  var dayMode = (from>=RAW.dfrom);

  /* ---- 直契約・代理店 ---- */
  var t={leads:0,cost:0,cvden:0,known:0,deals:0,won:0,amt:0,
         al:0,ad:0,aw:0,aa:0};
  var cost;
  if(dayMode){
    for(var dk in RAW.dkpi){
      if(!RAW.dkpi.hasOwnProperty(dk)){continue;}
      if(dk<from||dk>to){continue;}
      var dv=RAW.dkpi[dk];
      t.leads+=dv[0]; t.cost+=dv[1]; t.cvden+=dv[2];
      t.deals+=dv[3]; t.won+=dv[4]; t.amt+=dv[5];
      t.al+=dv[6]; t.ad+=dv[7]; t.aw+=dv[8]; t.aa+=dv[9];
    }
    cost = t.cost;
  }else{
    for(i=0;i<ws.length;i++){
      w=RAW.weeks[ws[i]];
      t.leads+=w.leads; t.cvden+=(w.cvden||0);
      t.deals+=w.deals; t.won+=w.won; t.amt+=w.amt;
      /* 費用は判明分だけ足す。null（不明）を0として足すと、
         データが無い期間を「0円で回した」ことにしてしまう。 */
      if(w.cost!==null){t.cost+=w.cost; t.known++;}
      t.al+=w.al; t.ad+=w.ad; t.aw+=w.aw; t.aa+=w.aa;
    }
    cost = t.known>0 ? t.cost : null;
  }
  setK('d_leads',jInt(t.leads)); setK('d_cost',jYen(cost));
  setK('d_cpl',jYen(div(cost,t.cvden))); setK('d_deals',jInt(t.deals));
  setK('d_mtg',jPct(div(t.deals,t.leads))); setK('d_won',jInt(t.won));
  setK('d_win',jPct(div(t.won,t.deals))); setK('d_amt',jYen(t.amt));
  setK('a_leads',jInt(t.al)); setK('a_deals',jInt(t.ad));
  setK('a_mtg',jPct(div(t.ad,t.al))); setK('a_won',jInt(t.aw));
  setK('a_win',jPct(div(t.aw,t.ad))); setK('a_amt',jYen(t.aa));

  /* ---- 日次架電KPI（日付でそのまま切る） ---- */
  var c={calls:0,conn:0,appt:0,called:0,worked:0};
  for(var d in RAW.days){
    if(!RAW.days.hasOwnProperty(d)){continue;}
    if(d<from||d>to){continue;}
    var r=RAW.days[d];
    c.calls+=r[0]; c.conn+=r[1]; c.appt+=r[2];
    c.called+=r[4]||0;
    if(r[0]>0){c.worked++;}
  }
  setK('c_calls',jInt(c.calls)); setK('c_called',jInt(c.called));
  setK('c_conn',jInt(c.conn));
  setK('c_rate',jPct(div(c.conn,c.calls)));
  setK('c_appt',jInt(c.appt));
  setK('c_conv',jPct(div(c.appt,c.called)));
  setK('c_perday',c.worked?jDec(c.calls/c.worked):'');
  setK('c_wdays',jInt(c.worked));
  /* FSは架電と別の集計。期間で絞り直す。 */
  var f={mtgs:0,props:0,wons:0,amt:0};
  for(var fd in RAW.fs){
    if(!RAW.fs.hasOwnProperty(fd)){continue;}
    if(fd<from||fd>to){continue;}
    var fr=RAW.fs[fd];
    f.mtgs+=fr[0]; f.props+=fr[1]; f.wons+=fr[2]; f.amt+=fr[3];
  }
  setK('fs_mtgs',jInt(f.mtgs)); setK('fs_props',jInt(f.props));
  setK('fs_wons',jInt(f.wons)); setK('fs_amt',jYen(f.amt));
  setK('fs_close',jPct(div(f.wons,f.mtgs)));
  setK('fs_avg',f.wons?jYen(f.amt/f.wons):'');

  /* ---- リード獲得（web広告） ----
     日次入力タブは日別に持っているので、日付でそのまま切る。
     週キーで足すと、1日だけ指定してもその週まるごとの値が出る。
     日次が無い期間（日次入力タブの開始前）は週次で補う。 */
  var ad={spend:0,imp:0,clicks:0,cv:0}, adDay=dayMode, apd;
  if(dayMode){
    for(var ad1 in RAW.adpd){
      if(!RAW.adpd.hasOwnProperty(ad1)){continue;}
      if(ad1<from||ad1>to){continue;}
      apd=RAW.adpd[ad1];
      ad.spend+=apd[0]; ad.imp+=apd[1]; ad.clicks+=apd[2]; ad.cv+=apd[3];
    }
  }
  if(!adDay){
    for(i=0;i<ws.length;i++){
      var ar=RAW.adperf[ws[i]];
      if(ar){ad.spend+=ar[0]; ad.imp+=ar[1]; ad.clicks+=ar[2]; ad.cv+=ar[3];}
    }
  }
  setK('ap_spend',jYen(ad.spend)); setK('ap_imp',jInt(ad.imp));
  setK('ap_clicks',jInt(ad.clicks));
  setK('ap_ctr',jPct(div(ad.clicks,ad.imp),2));
  setK('ap_cpc',jYen(div(ad.spend,ad.clicks)));
  setK('ap_cpm',ad.imp?jYen(ad.spend/ad.imp*1000):'');
  setK('ap_cv',jInt(ad.cv));
  setK('ap_cvr',jPct(div(ad.cv,ad.clicks),2));
  setK('ap_cpl',jYen(div(ad.spend,ad.cv)));

  /* ---- 月次ファネルは月を「列」に並べているので、行ではなく列を隠す ----
     他の表は showRows が行を隠すが、この表だけ向きが違う。
     月はまるごと入れる（指定日を含む月は残す）。週次表と同じ考え方で、
     月の途中で切ると分母と分子が別の集団になるため。 */
  var mcell=document.querySelectorAll('[data-m]'),mi;
  for(mi=0;mi<mcell.length;mi++){
    var mk=mcell[mi].getAttribute('data-m');
    mcell[mi].style.display=((mk+'-31')>=from && (mk+'-01')<=to)?'':'none';
  }
  /* 週を列に並べた表も同じ。週はまるごと入れる（月曜〜日曜のどこかが
     期間に入っていれば残す）。data-w は月曜日の日付。 */
  var wcell=document.querySelectorAll('[data-w]'),wi;
  for(wi=0;wi<wcell.length;wi++){
    var wk=wcell[wi].getAttribute('data-w');
    var we=new Date(wk+'T00:00:00Z');
    we.setUTCDate(we.getUTCDate()+6);
    var wend=we.toISOString().slice(0,10);
    wcell[wi].style.display=(wend>=from && wk<=to)?'':'none';
  }

  showRows(from,to);
  drawAll(from,to);

  var lab=document.getElementById('period');
  if(lab){
    var a,b;
    if(dayMode){
      a=from; b=to>RAW.end?RAW.end:to;
      lab.textContent=a+' 〜 '+b+'（日次）';
    }else{
      a=ws.length?ws[0]:from; b=ws.length?RAW.wkend[ws[ws.length-1]]:to;
      if(b>RAW.end){b=RAW.end;}
      lab.textContent=a+' 〜 '+b+'（週次・月曜始まり）';
    }
  }
}
/* 表示中のページはブラウザやCDNのキャッシュで古いことがある。
   クエリを変えて読み直すことで、確実に配信中の最新を取りに行く。
   ここで押しても再集計は走らない（数字の作り直しはGitHub Actions側）。
   静的サイトに再集計ボタンは置けない。起動にはトークンが要り、
   公開ページに置くと誰でも読めてしまうため。 */
function reloadFresh(){
  var u = location.pathname + '?t=' + Date.now();
  location.replace(u);
}
/* 「数字の更新」がいつかを、経過時間で添える。日時だけだと
   それが今の数字なのか判断できない。 */
function showAge(){
  var el = document.getElementById('age');
  if(!el){return;}
  var m = document.querySelector('.sub').textContent.match(
    /(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?/);
  if(!m){return;}
  var t = new Date(m[1]+'-'+m[2]+'-'+m[3]+'T'+(m[4]||'00')+':'+(m[5]||'00')+':00+09:00');
  var mins = Math.floor((Date.now() - t.getTime()) / 60000);
  if(mins < 0 || !isFinite(mins)){return;}
  var s;
  if(mins < 60){ s = mins + '分前'; }
  else if(mins < 60*24){ s = Math.floor(mins/60) + '時間前'; }
  else { s = Math.floor(mins/1440) + '日前'; }
  el.textContent = '（' + s + '）';
}
function resetRange(){
  document.getElementById('from').value=RAW.start;
  document.getElementById('to').value=RAW.end;
  apply(RAW.start,RAW.end);
}
function onApply(){
  var f=document.getElementById('from').value||RAW.start;
  var t=document.getElementById('to').value||RAW.end;
  if(f>t){var x=f;f=t;t=x;}
  /* データの外を指定されたら端に寄せる。input の min/max は日付ピッカーを
     絞るだけで、直接入力や貼り付けは素通りする。素通りさせると該当週が
     0件になり、見出しが「2027-01-01 〜 2026-08-26」のように開始が終了より
     後ろの状態で出てしまう（総費用も空欄になる）。 */
  if(f<RAW.start){f=RAW.start;}
  if(f>RAW.end){f=RAW.end;}
  if(t<RAW.start){t=RAW.start;}
  if(t>RAW.end){t=RAW.end;}
  /* 丸めた値を入力欄にも戻す。表示している期間と入力欄が食い違うと、
     何を見ているのか分からなくなる。 */
  document.getElementById('from').value=f;
  document.getElementById('to').value=t;
  apply(f,t);
}
"""


FOLD_JS = """
/* 開閉はこの端末に憶えておく。毎回開き直すなら畳んでおく意味がない。
   プライベートウィンドウや設定によっては localStorage が例外を投げるので、
   読み書きが失敗してもページは普通に動くようにしておく（既定は閉じたまま）。 */
(function(){
  var K='kfold:';
  var ds=document.querySelectorAll('details.fold');
  for(var i=0;i<ds.length;i++){
    (function(d){
      var k=K+d.id;
      try{var v=localStorage.getItem(k);
        if(v==='1'){d.open=true;}else if(v==='0'){d.open=false;}
      }catch(e){}
      d.addEventListener('toggle',function(){
        try{localStorage.setItem(k,d.open?'1':'0');}catch(e){}
        /* 閉じている間に幅0で初期化されたグラフがあれば、開いた時に測り直す。
           今は折りたたむのは表だけだが、後でグラフを入れても崩れないように。 */
        if(d.open&&window.Chart&&Chart.getChart){
          var cs=d.querySelectorAll('canvas');
          for(var j=0;j<cs.length;j++){
            var ch=Chart.getChart(cs[j]); if(ch){ch.resize();}
          }
        }
      });
    })(ds[i]);
  }
})();

/* 月や週が横に並ぶ表は、画面より広ければ横スクロールになる。開いた瞬間は
   最新が見えている方がいい。左端に寄っていると毎回スクロールしてから読む
   ことになる。列幅は固定なので、右端に寄せると画面幅ぶん（週なら直近
   2〜3ヶ月）が最初から見える。収まっていれば何も起きない。 */
function toLatest(root){
  var t=(root||document).querySelectorAll('.fnl'),i;
  for(i=0;i<t.length;i++){t[i].scrollLeft=t[i].scrollWidth;}
}
toLatest();
/* 折りたたみの中は閉じている間は幅0で、開くまでスクロール位置を決められない。
   開いた時点で測り直す。 */
(function(){
  var ds=document.querySelectorAll('details.fold'),i;
  for(i=0;i<ds.length;i++){
    (function(d){
      d.addEventListener('toggle',function(){if(d.open){toLatest(d);}});
    })(ds[i]);
  }
})();
"""


def ds(label, data, color, dashed=False, width=2, hidden=False):
    d = {
        "label": label,
        "data": [None if v is None else round(v, 4) for v in data],
        "borderColor": color,
        "backgroundColor": color,
        "borderDash": [5, 4] if dashed else [],
        "borderWidth": width,
        "fill": False,
    }
    if hidden:
        # 凡例をクリックすればすぐ出せる。データは消していない。
        d["hidden"] = True
    return d


def render(data):
    weeks = data["week_starts"]
    labels = [week_label(w) for w in weeks]
    rows, dm = compute_direct(data)
    arows, am = compute_agency(data)
    td = totals_direct(rows, weeks)
    ta = totals_agency(arows, weeks)
    daily = compute_daily(data)

    title = data.get("title") or "ホリエモンAI学校 介護校 ファネルダッシュボード"
    gen = data.get("generated_at", "")
    # 期間の終端は「最終週の日曜」ではなく「実際にデータが入っている最後の日」。
    # 進行中の週を含める運用なので、単純に日曜を書くと未来の日付が出て、
    # まだ存在しないデータまで入っているように見える。
    # generated_at（生成日）がその週の日曜より前なら、生成日を終端として表示する。
    period_end = dt.date.fromisoformat(weeks[-1]) + dt.timedelta(days=6)
    if gen:
        try:
            gen_d = dt.date.fromisoformat(gen)
            if gen_d < period_end:
                period_end = gen_d
        except ValueError:
            pass
    period = f"{weeks[0]} 〜 {period_end.isoformat()}"

    # ---- 期間累計サマリ ----
    def kpi(k, v, kid=None):
        # id を振るのは、期間フィルタが値だけを書き換えられるようにするため。
        # 表は行を隠すだけで済むが、KPIは期間に応じて計算し直す必要がある。
        # ここが再計算されないと「表は8月だけ、KPIは全期間」という食い違いが出る。
        cls = ' class="v empty"' if v == "" else ' class="v"'
        i = f' id="{kid}"' if kid else ""
        return f'<div class="kpi"><div class="k">{k}</div><div{cls}{i}>{v}</div></div>'

    direct_kpis = "".join([
        kpi("総リード数", f_int(td["leads"]), "d_leads"),
        kpi("総費用", f_yen(td["cost"]), "d_cost"),
        kpi("全体CPL", f_yen(td["cpl"]), "d_cpl"),
        kpi("総商談数", f_int(td["deals"]), "d_deals"),
        kpi("商談化率", f_pct(td["mtg_rate"]), "d_mtg"),
        kpi("総成約数", f_int(td["won"]), "d_won"),
        kpi("成約率", f_pct(td["win_rate"]), "d_win"),
        kpi("成約金額", f_yen(td["amount"]), "d_amt"),
    ])
    agency_kpis = "".join([
        kpi("総リード数", f_int(ta["leads"]), "a_leads"),
        kpi("総商談数", f_int(ta["deals"]), "a_deals"),
        kpi("商談化率", f_pct(ta["mtg_rate"]), "a_mtg"),
        kpi("総成約数", f_int(ta["won"]), "a_won"),
        kpi("成約率", f_pct(ta["win_rate"]), "a_win"),
        kpi("成約金額", f_yen(ta["amount"]), "a_amt"),
    ])

    # ---- 直契約 表 ----
    body = []
    for idx, w in enumerate(weeks):
        for i, (ch, chlabel) in enumerate(CHANNELS + [("total", "合計")]):
            m = dm[ch]
            tr_cls = []
            if ch == "total":
                tr_cls.append("total")
            if i == 0:
                tr_cls.append("wkstart")
            cls = f' class="{" ".join(tr_cls)}"' if tr_cls else ""
            # 週の6行すべてに同じ日付を振る。1行だけに振ると、期間で隠したとき
            # rowspan の週ラベルだけが残って行がずれる。
            cls += f' data-d="{w}"'

            wk_td = (f'<td class="wk" rowspan="6">{week_label(w)}</td>'
                     if i == 0 else "")

            show_cpl = ch in CPL_VISIBLE_ROWS
            cpl_v = f_yen(m["cpl"][idx]) if show_cpl else ""
            cpl_ma = f_yen(m["ma"]["cpl"][idx]) if show_cpl else ""

            body.append(
                f"<tr{cls}>{wk_td}"
                f'<td class="ch">{chlabel}</td>'
                + cell(f_int(m["leads"][idx]), f_dec(m["ma"]["leads"][idx]))
                + cell(f_yen(m["cost"][idx]), f_yen(m["ma"]["cost"][idx]))
                + cell(cpl_v, cpl_ma)
                + cell(f_int(m["deals"][idx]), f_dec(m["ma"]["deals"][idx]))
                + cell(f_pct(m["mtg_rate"][idx]), f_pct(m["ma"]["mtg_rate"][idx]))
                + cell(f_int(m["won"][idx]), f_dec(m["ma"]["won"][idx]))
                + cell(f_pct(m["win_rate"][idx]), f_pct(m["ma"]["win_rate"][idx]))
                + "</tr>"
            )
    direct_table = "".join(body)

    # ---- 代理店 表 ----
    abody = []
    for idx, w in enumerate(weeks):
        abody.append(
            f'<tr data-d="{w}">'
            f'<td class="wk">{week_label(w)}</td>'
            + cell(f_int(am["leads"][idx]), f_dec(am["ma"]["leads"][idx]))
            + cell(f_int(am["deals"][idx]), f_dec(am["ma"]["deals"][idx]))
            + cell(f_pct(am["mtg_rate"][idx]), f_pct(am["ma"]["mtg_rate"][idx]))
            + cell(f_int(am["won"][idx]), f_dec(am["ma"]["won"][idx]))
            + cell(f_pct(am["win_rate"][idx]), f_pct(am["ma"]["win_rate"][idx]))
            + "</tr>"
        )
    agency_table = "".join(abody)

    # ---- 展示会 表 ----
    ebody = []
    for e in sorted(data.get("expos", []), key=lambda x: x["date"]):
        cpl = safe_div(e.get("cost"), e.get("leads"))
        ebody.append(
            f'<tr data-d="{e["date"]}">'
            f'<td class="ch">{e["name"]}</td>'
            f'<td class="ch">{e["date"]}</td>'
            f'<td class="num">{f_yen(e.get("cost"))}</td>'
            f'<td class="num">{f_int(e.get("leads"))}</td>'
            f'<td class="num">{f_yen(cpl)}</td>'
            f'<td class="num">{f_int(e.get("deals"))}</td>'
            f'<td class="num">{f_int(e.get("won"))}</td>'
            "</tr>"
        )
    expo_table = "".join(ebody) or '<tr><td colspan="7" class="ch"></td></tr>'

    # ---- グラフ ----
    leads_bars = [{"label": lbl, "data": dm[k]["leads"], "color": COLORS[k]}
                  for k, lbl in CHANNELS]
    leads_ma = [None if v is None else round(v, 2)
                for v in dm["total"]["ma"]["leads"]]

    # このグラフは web の CPL しか描かない（展示会は開催週に費用が一括で立ち、
    # リードは数週にわたって登録されるため、週次のCPLが意味を持たない）。
    # だから見出しも「web CPL推移」と名乗る。「CPL推移」と書くと、
    # 展示会も含んだ全体のCPLだと誤解される。
    #
    # 横軸も、広告費データが存在する期間だけに絞る。
    # web費用は2026年4月以降しか無く、全期間で描くと左の6割以上が空白になり、
    # 実際に描かれている18週分が右端に押し込まれて変化が読めない。
    cpl_from = next((i for i, v in enumerate(dm["web"]["cost"]) if v is not None), None)
    if cpl_from is None:
        cpl_labels, cpl_ds, cpl_title = labels, [], "web CPL推移"
    else:
        cpl_labels = labels[cpl_from:]
        cpl_ds = [
            ds("web CPL", dm["web"]["cpl"][cpl_from:], COLORS["web"]),
            ds("web CPL (4週移動平均)", dm["web"]["ma"]["cpl"][cpl_from:],
               COLORS["web"], dashed=True, width=1.5),
        ]
        cpl_title = f"web CPL推移（{weeks[cpl_from]}〜）"

    # 商談化率・成約率のグラフは4週移動平均だけを描く。
    # リードが1〜4件しかない週（2025年10〜12月に集中）が週次の生値では
    # 100%や0%に振れ、縦軸を占領して肝心の傾向が読めなくなる。
    # 「4人中4人が商談になった週」は事実だが、母数4人の100%と母数600人の3%を
    # 同じ縦軸に並べても比較にならない。週次の生値は表に全部載っているので、
    # グラフは母数を4週分ためた移動平均で傾向を見る役割に振り切る。
    def mtg_line(k):
        return suppress_small_base(weeks, rows, k, "leads",
                                   dm[k]["ma"]["mtg_rate"], MIN_LEADS_FOR_RATE)

    def win_line(k):
        return suppress_small_base(weeks, rows, k, "deals",
                                   dm[k]["ma"]["win_rate"], MIN_DEALS_FOR_RATE)

    # 率のグラフはチャネル別の線を出さず、合計だけにする。
    # 紹介やその他は母数が1桁で、率が0%か100%にしか動かない。
    # それを合計と同じ縦軸に並べると縦軸が0〜100%まで広がり、
    # 実勢である3〜10%の動きが底に張り付いて読めなくなる。
    # チャネル別の率は週次表に全部載っているので、グラフは全体の推移に絞る。
    # 率のグラフも、線が途切れずに続く期間だけに絞る。
    # 集客が展示会単発だった時期は、リードが1件も入らない週が並ぶ
    # （2026年3月までの31週のうち17週がリード0件）。0÷0は計算できないので
    # 打つ点が無く、線がブツ切れになる。それを全期間で描くと、
    # 読みたい直近の連続した動きが右端に押し込まれる。
    # 前半の実数は週次表と展示会別CPLテーブルに残っているので、
    # グラフは「連続して追える期間」に役割を絞る。
    mtg_raw = suppress_weekly_small_base(
        weeks, rows, "total", "leads", dm["total"]["mtg_rate"], MIN_LEADS_FOR_RATE)
    win_raw = suppress_weekly_small_base(
        weeks, rows, "total", "deals", dm["total"]["win_rate"], MIN_DEALS_FOR_RATE)

    def continuous_from(series):
        """線が途切れずに続く区間の開始位置を返す。

        最終週の値が無いことは普通にある（直近は母数がまだ積み上がっていない）。
        末尾から素直に遡ると、その1週だけで探索が止まって区間が空になるので、
        値が入っている最後の週を起点にして、そこから遡る。
        """
        last = next((i for i in range(len(series) - 1, -1, -1)
                     if series[i] is not None), None)
        if last is None:
            return None
        i = last
        while i > 0 and series[i - 1] is not None:
            i -= 1
        return i

    def rate_panel(raw, ma, raw_color, ma_color, base_series, title):
        i = continuous_from(ma)
        if i is None:
            return labels, [], base_series, title
        return (labels[i:],
                [ds("週次", raw[i:], raw_color, width=1.5),
                 ds("4週移動平均", ma[i:], ma_color, width=2.5)],
                base_series[i:],
                f"{title}（{weeks[i]}〜）")

    def by_month(field):
        """週次を月次に畳む（週の開始月で寄せる）。"""
        out = {}
        for i, w in enumerate(weeks):
            out.setdefault(w[:7], 0)
            out[w[:7]] += dm["total"][field][i]
        return out

    m_leads, m_deals, m_won = by_month("leads"), by_month("deals"), by_month("won")
    months = list(m_leads.keys())
    m_labels = [f"{int(k[5:7])}月" if k[5:7] != "01" else f"{k[:4]}年1月"
                for k in months]

    def zero_fill(series, base):
        """母数が0の週だけ0%で埋める。母数がある週の値には手を触れない。

        埋めてよいのは「リードが1件も来ていない」週に限る。
        リードが数件あって実際に商談化していた週まで0%にすると、
        起きていたことを無かったことにしてしまう。
        """
        return [0.0 if (base[i] or 0) == 0 else v for i, v in enumerate(series)]

    if CUMULATIVE_RATE_CHARTS:
        def running(field):
            out, acc = [], 0
            for v in dm["total"][field]:
                acc += v
                out.append(acc)
            return out

        c_leads, c_deals, c_won = running("leads"), running("deals"), running("won")
        # 累計の分母が0の週は点を打たない（zero_fill は使わない）。
        # 0%を打つと、期間を絞って母数が0になったときにKPIが空欄なのに
        # グラフだけ0%の線が伸び、どちらが正しいのか読めなくなる。
        # 期間フィルタ側（drawAll）も同じルールで描いている。
        mtg_labels, mtg_base, mtg_title = labels, dm["total"]["leads"], "商談化率推移（期間累計）"
        mtg_ds = [ds("期間累計 商談化率",
                     [safe_div(c_deals[i], c_leads[i]) for i in range(len(weeks))],
                     COLORS["mtg"], width=2.5)]
        win_labels, win_base, win_title = labels, dm["total"]["deals"], "成約率推移（期間累計）"
        win_ds = [ds("期間累計 成約率",
                     [safe_div(c_won[i], c_deals[i]) for i in range(len(weeks))],
                     COLORS["win"], width=2.5)]
    elif MONTHLY_RATE_CHARTS:
        # 率のグラフだけ月次にする。
        # 見たいのは「期間内で商談化率・成約率がどう動いたか」。
        # 週次のままだと、リード1〜4件の週が100%に跳ねて縦軸を占領し、
        # 実勢の1〜10%が下端に潰れて動きが読めない。
        # 月でまとめれば母数が確保できるので100%の針が消え、
        # 全期間が1本でつながり、縦軸も実勢のレンジに収まる。
        # 週単位の数字は週次表にそのまま載っているので、失うものはない。
        mtg_labels, mtg_base, mtg_title = m_labels, list(m_leads.values()), "商談化率推移（月次）"
        mtg_ds = [ds("商談化率",
                     zero_fill([safe_div(m_deals[k], m_leads[k]) for k in months],
                               list(m_leads.values())),
                     COLORS["mtg"], width=2.5)]
        win_labels, win_base, win_title = m_labels, list(m_deals.values()), "成約率推移（月次）"
        win_ds = [ds("成約率",
                     zero_fill([safe_div(m_won[k], m_deals[k]) for k in months],
                               list(m_deals.values())),
                     COLORS["win"], width=2.5)]
    elif ZERO_FILL_EMPTY_WEEKS:
        mtg_labels, mtg_base, mtg_title = labels, dm["total"]["leads"], "商談化率推移"
        mtg_ds = [
            ds("週次", zero_fill(dm["total"]["mtg_rate"], mtg_base),
               COLORS["mtg_raw"], width=1.5),
            ds("4週移動平均", zero_fill(dm["total"]["ma"]["mtg_rate"], mtg_base),
               COLORS["mtg"], width=2.5),
        ]
        win_labels, win_base, win_title = labels, dm["total"]["deals"], "成約率推移"
        win_ds = [
            ds("週次", zero_fill(dm["total"]["win_rate"], win_base),
               COLORS["win_raw"], width=1.5),
            ds("4週移動平均", zero_fill(dm["total"]["ma"]["win_rate"], win_base),
               COLORS["win"], width=2.5),
        ]
    else:
        mtg_labels, mtg_ds, mtg_base, mtg_title = rate_panel(
            mtg_raw, mtg_line("total"), COLORS["mtg_raw"], COLORS["mtg"],
            dm["total"]["leads"], "商談化率推移")
        win_labels, win_ds, win_base, win_title = rate_panel(
            win_raw, win_line("total"), COLORS["win_raw"], COLORS["win"],
            dm["total"]["deals"], "成約率推移")

    ag_ds = [
        ds("リード数", am["leads"], COLORS["leads"]),
        ds("リード数 (4週移動平均)", am["ma"]["leads"], COLORS["leads"],
           dashed=True, width=1.5),
        ds("成約数", am["won"], COLORS["won"]),
        ds("成約数 (4週移動平均)", am["ma"]["won"], COLORS["won"],
           dashed=True, width=1.5),
    ]

    # 代理店には母数フィルタをかけない。
    # フィルタは「母数の大きいチャネルと小さいチャネルが同じ縦軸に載る」ことへの
    # 対処であって、代理店セクションは全期間で20リードしかなく、どの週も小さい。
    # ここに直契約と同じ閾値を当てると線がほぼ全部消え、グラフが成立しない。
    # 代理店は母数が小さいことを承知で見るセクションなので、そのまま描く。
    ag_rate_ds = [
        ds("商談化率", am["ma"]["mtg_rate"], COLORS["mtg"], width=2.5),
        ds("成約率", am["ma"]["win_rate"], COLORS["win"], width=2.5),
    ]

    # ---- 日次架電 ----
    if daily:
        ac = daily["activity"]
        # 「本日」「今週」は期間フィルタでは動かさない（常に生成日基準）。
        # 期間を切った状態でも「今どれだけ動いているか」は見たいので、
        # ここが期間に追従すると本来の役割が消える。
        # 本日と累計で同じ並びにする。並びが違うと、上下の数字を
        # 見比べる時にどれとどれが対応するのか毎回探すことになる。
        act_items = "".join([
            kpi("架電数", f_int(ac["today_calls"])),
            kpi("リード数", f_int(ac["today_called"])),
            kpi("接続数", f_int(ac["today_conn"])),
            kpi("接続率", f_pct(ac["today_rate"])),
            kpi("面談予約 獲得数", f_int(ac["today_appts"])),
            kpi("商談化率", f_pct(ac["today_conv"])),
        ])
        # 「日次の行動量」と「架電したリード → 面談予約」の折りたたみは廃止した。
        # 前者は架電数・接続数・接続率が上のグラフと全体の週次と重複し、
        # 後者は「面談予約 ÷ 架電したリード数」という、全体の週次から
        # 外したのと同じ意味を持たない割り算を出していた
        # （分子に社内が入力したウェビナー・フォーム由来が入る）。
        # 累計（期間内）と架電業者のカードは廃止した。同じ数字を週次の
        # 折りたたみ（f-is-all / f-vendor-wk）で出しているので、
        # カードは「本日」だけにしてある。

        # ---- FS活動量（直契約のみ・全期間） ----
        # 上部の直契約カードと同じ母集団に揃えてある。揃えないと
        # 「面談実施が商談数より多い」という論理的にありえない状態になる
        # （実際、揃える前は 面談実施72 > 商談64 になっていた）。
        # 架電データは2026-06からしか無いが、面談・提案・成約は架電と無関係なので
        # 全期間で数える。calls ブロックとは別建てにしているのはこのため。
        fsd = data.get("fs") or {}

        fs_t = {f: sum((v.get(f, 0) for v in fsd.values()))
                for f in FS_FIELDS}
        # 「本日」カードは置かない。面談・提案・成約のどれかが起きた日は
        # この12ヶ月で48日しかなく、ほとんどの日が全部0のカードになる。
        # 今日の動きは月次の一番右の列に入っている。
        fs_total = "".join([
            kpi("面談実施", f_int(fs_t["mtgs"]), "fs_mtgs"),
            kpi("提案", f_int(fs_t["props"]), "fs_props"),
            kpi("成約数", f_int(fs_t["wons"]), "fs_wons"),
            kpi("成約金額", f_yen(fs_t["wonamt"]), "fs_amt"),
            kpi("実施→成約率", f_pct(safe_div(fs_t["wons"], fs_t["mtgs"])), "fs_close"),
            kpi("平均成約単価", f_yen(safe_div(fs_t["wonamt"], fs_t["wons"])), "fs_avg"),
        ])
        # 月次。週次にしないのは動きが少ないため。この12ヶ月で面談・提案・成約の
        # どれかが起きた日は48日しかなく、週で切ると50列ちかくが0で埋まる。
        # 率は1つも出さない。成約は面談の数週間〜数ヶ月あとに決まるので、
        # 同じ月に分子と分母がそろわない（2025-11は面談実施0で成約1、
        # 2026-02と2026-03は1/1で100%になる）。正しい実施→成約率は
        # コホート軸の月次ファネルにある。
        fs_mk = month_keys(fsd)
        fs_mo = {mk: {f: 0 for f in FS_FIELDS} for mk in fs_mk}
        for fs_d, fs_v in fsd.items():
            slot = fs_mo[fs_d[:7]]
            for f in FS_FIELDS:
                slot[f] += fs_v.get(f, 0)
        fs_mrows = [
            ("面談実施", lambda v: v.get("mtgs"), f_int, d_num, True, False,
             False),
            ("提案", lambda v: v.get("props"), f_int, d_num, False, True,
             True),
            ("成約数", lambda v: v.get("wons"), f_int, d_num, True, False,
             True),
            ("成約金額", lambda v: v.get("wonamt"), f_man, d_man, True, False,
             False),
        ]
        fs_section = f"""
<h2>FS活動量</h2>
<div class="actsum">
  <div class="card cum"><div class="tag">累計<span class="taglabel">全期間・直契約</span></div>
    <div class="kpis">{fs_total}</div></div>
</div>
<div class="tabgrid">
  <details class="fold" id="f-fs-m"><summary><span class="tri">▶</span>月次<span class="cnt">{len(fs_mk)}ヶ月分　面談実施 {f_int(fs_t["mtgs"])}　成約 {f_int(fs_t["wons"])}　成約金額 {f_man(fs_t["wonamt"])}</span></summary>
    <div class="foldin">{month_table(fs_mk, fs_mo, fs_mrows, fs_t)}</div></details>
</div>"""
        # ---- IS活動量の週次（全体 / 架電業者） ----
        # 業者ぶんの架電数・接続数は calls の vcalls / vconn（業者アカウントの
        # コール）、面談予約は is_attr の vendor 側（業者アカウントが作った
        # 取引）。どちらもアカウントで切っているので、社内が架電しても
        # 混ざらない（廃止した日付起点の業者カードはここが崩れる作りだった）。
        # 面談予約は全体・業者ともに is_attr（取引の作成者）から取る。
        # 全体を calls の appts（その日に入った予約すべて）にすると、
        # 代理店やコンタクト未紐付けも入って業者ぶんと引き算できなくなる。
        # 同じ規則で数えることで「全体 − 業者 = 社内」が成立する。
        # 稼働日数は「1件でも架電があった日」の数。累計カードを畳んだので
        # ここに置いておかないとページから消える。
        VN_F = ("calls", "conn", "appts", "days",
                "vcalls", "vconn", "vappts", "vdays")
        vn_wk = {}

        def vn_slot_of(day):
            key = monday_of(dt.date.fromisoformat(day)).isoformat()
            return vn_wk.setdefault(key, {f: 0 for f in VN_F})

        for vn_d, vn_v in (data.get("calls") or {}).items():
            vn_slot = vn_slot_of(vn_d)
            vn_slot["calls"] += vn_v.get("calls", 0)
            vn_slot["conn"] += vn_v.get("connected", 0)
            vn_slot["days"] += 1 if vn_v.get("calls", 0) else 0
            vn_slot["vcalls"] += vn_v.get("vcalls", 0)
            vn_slot["vconn"] += vn_v.get("vconn", 0)
            vn_slot["vdays"] += 1 if vn_v.get("vcalls", 0) else 0
        for vn_d, vn_v in (data.get("is_attr") or {}).items():
            vn_slot = vn_slot_of(vn_d)
            vn_ven = (vn_v.get("vendor") or {}).get("appts", 0)
            vn_slot["vappts"] += vn_ven
            vn_slot["appts"] += vn_ven + (vn_v.get("inhouse") or {}).get(
                "appts", 0)

        def vn_block(label, fold_id, ck, nk, dk_, ak=None):
            """週次の折りたたみを1つ作る。全体と業者で見る列だけが違う.

            ak を渡すと面談予約と商談化率の行が付く。全体では渡さない
            （理由は下の vn_folds のコメント）。
            """
            # 架電0の週は出さない。架電が始まるのは2026-05-12で、それ以前の
            # 週を並べると「架電0」の行が18週続く。
            keys = [k for k in sorted(vn_wk) if vn_wk[k][ck]]
            if not keys:
                return ""
            tot = {f: sum(vn_wk[k][f] for k in keys) for f in VN_F}
            rows = [
                ("架電数", lambda v: v.get(ck), f_int, d_num, True, False,
                 False),
                ("稼働日数", lambda v: v.get(dk_), f_int, None, False, True,
                 True),
                ("接続数", lambda v: v.get(nk), f_int, d_num, False, False,
                 True),
                ("接続率", lambda v: safe_div(v.get(nk), v.get(ck)),
                 f_pct, d_pt, not ak, True, False),
            ]
            cnt = (f'　架電 {f_int(tot[ck])}'
                   f'　接続率 {f_pct(safe_div(tot[nk], tot[ck]))}')
            if ak:
                rows += [
                    ("面談予約", lambda v: v.get(ak), f_int, d_num, True,
                     False, True),
                    ("商談化率", lambda v: safe_div(v.get(ak), v.get(ck)),
                     f_pct, d_pt, True, True, False),
                ]
                cnt += (f'　面談予約 {f_int(tot[ak])}'
                        f'　商談化率 {f_pct(safe_div(tot[ak], tot[ck]))}')
            return (
                f'<details class="fold" id="{fold_id}"><summary>'
                f'<span class="tri">▶</span>{label}'
                f'<span class="cnt">{len(keys)}週分{cnt}'
                "</span></summary>"
                '<div class="foldin">'
                + week_table(keys, vn_wk, rows, tot)
                + "</div></details>")

        # 全体には面談予約と商談化率を出さない。分子（作成者ベースの面談予約）
        # にはウェビナーやフォームから来て社内が入力しただけのものが入るので、
        # 架電数で割ると意味を持たない。実際に出してみると8/10の週は
        # 架電1件・面談予約3件で商談化率300%になった。
        # 業者は架電しかせず、その架電から取った予約しか作らないので成立する。
        vn_folds = (
            vn_block("全体の週次", "f-is-all", "calls", "conn", "days")
            + vn_block("架電業者の週次", "f-vendor-wk",
                       "vcalls", "vconn", "vdays", "vappts")
        )
        vn_fold = f'<div class="tabgrid">{vn_folds}</div>' if vn_folds else ""

        daily_section = f"""
<h2>IS活動量</h2>
<div class="actsum">
  <div class="card act"><div class="tag">本日<span class="taglabel">{daily["activity"]["today"]}</span></div>
    <div class="kpis">{act_items}</div></div>
</div>
{vn_fold}
<div class="charts one">
  <div class="card"><h3>架電数の日次推移（{daily["span"]}）</h3>
    <div class="chart"><canvas id="c_call"></canvas></div></div>
</div>"""
        daily_js = (
            f"mkCallBar('c_call',{js(daily['chart']['labels'])},"
            f"{js(daily['chart']['connected'])},{js(daily['chart']['noans'])},"
            f"'{COLORS['call_conn']}','{COLORS['call_noans']}');\n"
        )
    else:
        daily_section = ""
        fs_section = ""
        daily_js = ""

    # ---- 月次ファネル（直契約・コホート軸） ----
    # 段階と段階の「あいだ」を見るための唯一の常設ブロック。折りたたまない。
    # 全部 direct_day から引く。あそこはコンタクトの実効獲得日を軸にしていて、
    # リードも予約も実施も成約も同じ集団に載っている。だから段階間の率が
    # 成立する。calls / fs / is_attr はイベント軸なので**ここに混ぜてはいけない**。
    FU_F = ('leads', 'appts', 'mtgs', 'props', 'won', 'won_amount')
    fu_day_src = data.get('direct_day') or {}
    fu_keys = month_keys(fu_day_src)
    fu = {mk: {x: 0 for x in FU_F} for mk in fu_keys}
    for fu_day, fu_cells in fu_day_src.items():
        fu_slot = fu[fu_day[:7]]
        for fu_ch in CHANNEL_KEYS:
            fu_cell = fu_cells.get(fu_ch) or {}
            for fu_f in FU_F:
                fu_slot[fu_f] += fu_cell.get(fu_f, 0)
    fu_tot = {x: sum(fu[mk][x] for mk in fu_keys) for x in FU_F}

    # 段階間の率は「リード→予約」のように両端を名前にする。「成約率」だと
    # ヘッダーの成約率（成約÷商談）と同じ名前で違う数字（成約÷面談実施）が
    # 並び、どちらかが間違っていると読まれる。分母を名前に含めれば取り違えない。
    # 提案の行は外した。面談実施は「相談済みか提案の早い方」なので定義が
    # 重なり、13ヶ月のうち9ヶ月が面談実施と同値だった。通過率も85.5%で
    # ここでは誰も止まっていない。提案件数はFS活動量に残してある。
    fu_rows = [
        ('リード数', lambda v: v.get('leads'), f_int, d_num, True, False, False),
        ('面談予約', lambda v: v.get('appts'), f_int, d_num, True, False, True),
        ('リード→予約', lambda v: safe_div(v.get('appts'), v.get('leads')),
         f_pct, d_pt, False, True, False),
        ('面談実施', lambda v: v.get('mtgs'), f_int, d_num, False, False, True),
        ('成約', lambda v: v.get('won'), f_int, d_num, True, False, True),
        ('実施→成約', lambda v: safe_div(v.get('won'), v.get('mtgs')),
         f_pct, d_pt, False, True, False),
        ('成約金額', lambda v: v.get('won_amount'), f_man, d_man, True, False,
         False),
    ]
    fu_html = month_table(fu_keys, fu, fu_rows, fu_tot)
    funnel_section = (
        '\n<h2>月次ファネル'
        '<span class="h2sub">直契約・獲得月ベース</span></h2>\n'
        + fu_html + "\n"
    ) if fu_html else ""

    # 「取引作成者別」のセクションは廃止した。作成者で切った数字のうち
    # 見たいのは架電業者ぶんで、それはIS活動量の「架電業者の週次」に入って
    # いる。data.json の is_attr はそちらと、ファネルとの突き合わせ
    # （validate_is_attr）で使い続けている。

    # ---- リード獲得（web広告） ----
    # CPLが悪化した時の切り分け材料。CPL＝CPM÷(CTR×CVR) なので、
    # 単価が上がったのか（CPM）、クリックされなくなったのか（CTR）、
    # 申し込まれなくなったのか（CVR）を分けて見られるようにしておく。
    # 率は表示用に丸めた値からではなく実数から計算する。
    # 全キャンペーン合計で見る。以前は adperf（ウェビナーキャンペーンだけ）を
    # 使っていて、全web費用 ¥293万 のうち ¥153万 しか見せていなかった。
    # ad_day は 日 × 媒体/キャンペーン の生データなので、そこから足し直す。
    ADF = ("spend", "imp", "clicks", "cv")
    ap_day = {}
    for ap_d, ap_pairs in (data.get("ad_day") or {}).items():
        ap_slot = ap_day.setdefault(ap_d, {f: 0 for f in ADF})
        for ap_v in ap_pairs.values():
            for ap_f in ADF:
                ap_slot[ap_f] += ap_v.get(ap_f, 0)
    ap = {}
    for ap_d, ap_v in ap_day.items():
        ap_wk = monday_of(dt.date.fromisoformat(ap_d)).isoformat()
        ap_slot = ap.setdefault(ap_wk, {f: 0 for f in ADF})
        for ap_f in ADF:
            ap_slot[ap_f] += ap_v[ap_f]
    if ap:
        # 期間内のまとめ。率は週ごとの率を平均するのではなく、
        # 期間の合計から計算し直す。週ごとの率を単純平均すると、
        # 配信が少ない週が多い週と同じ重みになり実態から外れる。
        ap_tot = {k: sum(v[k] for v in ap.values())
                  for k in ("spend", "imp", "clicks", "cv")}
        ap_items = "".join([
            kpi("消費金額", f_yen(ap_tot["spend"]), "ap_spend"),
            kpi("IMP", f_int(ap_tot["imp"]), "ap_imp"),
            kpi("クリック", f_int(ap_tot["clicks"]), "ap_clicks"),
            kpi("CTR", f_pct(safe_div(ap_tot["clicks"], ap_tot["imp"]), 2), "ap_ctr"),
            kpi("CPC", f_yen(safe_div(ap_tot["spend"], ap_tot["clicks"])), "ap_cpc"),
            kpi("CPM", f_yen(safe_div(ap_tot["spend"], ap_tot["imp"]) * 1000
                             if ap_tot["imp"] else None), "ap_cpm"),
            kpi("CV", f_int(ap_tot["cv"]), "ap_cv"),
            kpi("CVR", f_pct(safe_div(ap_tot["cv"], ap_tot["clicks"]), 2), "ap_cvr"),
            kpi("CPL", f_yen(safe_div(ap_tot["spend"], ap_tot["cv"])), "ap_cpl"),
        ])
        # ---- Metaのキャンペーン別（月次） ----
        # Metaが費用の93%・CVの99%を占めるので、まずここを割る。
        # 媒体を混ぜた率は使えない。クリックの78%がLINE（CPC ¥1台）なので、
        # 全媒体のCTR 3.58% / CPC ¥21 は実質LINEの数字になる。
        # CPL = CPM ÷ (CTR × CVR) なので、9指標を縦に並べると
        # 「表示単価が上がったのか、クリックされないのか、申し込まれないのか」
        # を切り分けられる。
        APC_F = ("spend", "imp", "clicks", "cv")
        apc_pairs = sorted({
            pr for day in (data.get("ad_day") or {}).values()
            for pr in day if pr.startswith("Meta/")
        })
        apc_rows = [
            ("消費金額", lambda v: v.get("spend"), f_man, d_man, True, False,
             False),
            ("IMP", lambda v: v.get("imp"), f_int, d_num, False, False, True),
            ("CPM", lambda v: safe_div(v.get("spend"), v.get("imp")) * 1000
             if v.get("imp") else None, f_yen, d_yen, False, True, False),
            ("クリック", lambda v: v.get("clicks"), f_int, d_num, False, False,
             True),
            ("CTR", lambda v: safe_div(v.get("clicks"), v.get("imp")), f_pct2,
             d_pt2, False, True, False),
            ("CPC", lambda v: safe_div(v.get("spend"), v.get("clicks")), f_yen,
             d_yen, False, True, False),
            ("CV", lambda v: v.get("cv"), f_int, d_num, True, False, True),
            ("CVR", lambda v: safe_div(v.get("cv"), v.get("clicks")), f_pct2,
             d_pt2, False, True, False),
            ("CPL", lambda v: safe_div(v.get("spend"), v.get("cv")), f_yen,
             d_yen, True, True, False),
        ]
        apc_built = {}
        for apc_pair in apc_pairs:
            apc_days = {d_: pv[apc_pair]
                        for d_, pv in (data.get("ad_day") or {}).items()
                        if apc_pair in pv}
            apc_keys = month_keys(apc_days)
            if not apc_keys:
                continue
            apc_m = {mk: {f: 0 for f in APC_F} for mk in apc_keys}
            for apc_d, apc_v in apc_days.items():
                for apc_f in APC_F:
                    apc_m[apc_d[:7]][apc_f] += apc_v.get(apc_f, 0)
            apc_tot = {f: sum(apc_m[mk][f] for mk in apc_keys) for f in APC_F}
            apc_built[apc_pair] = (apc_keys, apc_m, apc_tot)
        # 消費金額の大きい順に並べる。名前順だと Claude が先頭に来て、
        # 費用の1割にも満たない配信が一番上になる。順番を固定で書くと
        # キャンペーンが増えたときに漏れるので、金額で決める。
        apc_blocks = []
        for apc_pair in sorted(apc_built,
                               key=lambda k: -apc_built[k][2]["spend"]):
            apc_keys, apc_m, apc_tot = apc_built[apc_pair]
            apc_name = apc_pair.replace("Meta/", "")
            apc_blocks.append(
                f'<details class="fold" id="f-ap-{apc_name}">'
                f'<summary><span class="tri">▶</span>Meta / {apc_name}'
                f'<span class="cnt">消費 {f_man(apc_tot["spend"])}'
                f'　CV {f_int(apc_tot["cv"])}'
                f'　CPL {f_yen(safe_div(apc_tot["spend"], apc_tot["cv"]))}'
                "</span></summary>"
                '<div class="foldin">'
                + month_table(apc_keys, apc_m, apc_rows, apc_tot)
                + "</div></details>")
        apc_html = ('<div class="tabgrid">' + "".join(apc_blocks) + "</div>"
                    if apc_blocks else "")

        # 週次も同じ形（指標を縦・週を横）にする。以前は週が行・指標が列で、
        # キャンペーン別の表と向きが逆だった。同じ9指標を並べているのに
        # 向きが違うと、見比べるたびに読み替えることになる。
        ap_week_keys = sorted(ap)
        ap_week_html = week_table(ap_week_keys, ap, apc_rows, ap_tot)

        adperf_section = f"""
<h2>リード獲得<span class="h2sub">web広告のみ・展示会は含まない</span></h2>
<div class="actsum">
  <div class="card cum"><div class="tag">web広告<span class="taglabel">期間内・全キャンペーン</span></div>
    <div class="kpis">{ap_items}</div></div>
</div>
{apc_html}
<div class="tabgrid">
  <details class="fold" id="f-adperf"><summary><span class="tri">▶</span>web広告の週次<span class="cnt">{len(ap)}週分・全媒体</span></summary>
    <div class="foldin">{ap_week_html}</div></details>
</div>"""
    else:
        adperf_section = ""

    # ---- ウェビナー別 ----
    # 同じ申込フォームから入るため、お題の区別は掲載期間でしかできない。
    # 期間フィルタの対象にはしない。ウェビナーごとの期間は固定で、
    # 上の期間指定で切ると「掲載期間の一部だけ」という意味の無い数字になる。
    wbs = data.get("webinars") or []
    if wbs:
        wrows = []
        for w in wbs:
            cost = w.get("cost")
            # 分母は広告シートのCV（申込の延べ数）。HubSpotのコンタクト数は
            # ユニークなので、同じ人が複数回申し込むと少なく出てCPLが高く見える。
            # シート側の定義も CPL＝消費金額÷CV、歩留まり＝商談数÷CV。
            cv = w.get("cv")
            leads = cv if cv is not None else w.get("leads", 0)
            won_amt = w.get("won_amount", 0)
            cpl = safe_div(cost, leads) if cost is not None else None
            roi = safe_div(won_amt, cost) if cost else None
            span = f'{w["start"][5:].replace("-", "/")}〜{w["end"][5:].replace("-", "/")}'
            # 日次入力が無い期間は週次から日割りで埋めている。厳密な実額ではない
            # ので、その旨を出す。何日ぶんが概算かまで見せないと、
            # 「多少ズレている」のか「ほぼ全部が推定」なのか区別できない。
            # 注記は「期間の一部しか数字が無い」場合だけ出す。実額を設定に
            # 書いた回は、シート由来かどうかは見る側に関係が無いので出さない。
            def mark(manual, got, need):
                if manual:
                    return ""
                if got and got < need:
                    return f'<span class="ma">{got}/{need}日分</span>'
                return ""

            days_n = w.get("days", 0)
            note = mark(w.get("cost_manual"), w.get("cost_days", 0), days_n)
            cvnote = mark(w.get("cv_manual"), w.get("cv_days", 0), days_n)
            if cv is None:
                cvnote = '<span class="ma">未取得</span>'
            # 広告指標も同じ列に並べる。CPL = CPM ÷ (CTR × CVR) なので、
            # 回ごとのCPLが動いたときに「表示単価か、クリック率か、申込率か」
            # を切り分けられる。掲載期間は回ごとに固定なので、時系列ではなく
            # 並列な選択肢の比較になる。だから回は縦に置く（月次テーブルとは
            # 向きが逆で正しい。展示会別も同じ）。
            imp = w.get("imp")
            clicks = w.get("clicks")
            wrows.append(
                "<tr>"
                f'<td class="wk">{w["name"]}</td>'
                f'<td class="ch">{span}</td>'
                f'<td class="num">{f_yen(cost)}{note}</td>'
                f'<td class="num">{f_int(imp)}</td>'
                f'<td class="num">{f_yen(safe_div(cost, imp) * 1000 if imp else None)}</td>'
                f'<td class="num">{f_int(clicks)}</td>'
                f'<td class="num">{f_pct(safe_div(clicks, imp), 2)}</td>'
                f'<td class="num">{f_yen(safe_div(cost, clicks))}</td>'
                f'<td class="num">{f_int(leads)}{cvnote}</td>'
                f'<td class="num">{f_pct(safe_div(cv, clicks), 2)}</td>'
                f'<td class="num">{f_yen(cpl)}</td>'
                f'<td class="num">{f_int(w.get("leads", 0))}</td>'
                f'<td class="num">{f_int(w.get("deals", 0))}</td>'
                f'<td class="num">{f_pct(safe_div(w.get("deals", 0), leads))}</td>'
                f'<td class="num">{f_int(w.get("won", 0))}</td>'
                f'<td class="num">{f_yen(won_amt)}</td>'
                f'<td class="num">{f_pct(roi)}</td>'
                '<td class="pad"></td></tr>')
        webinar_section = f"""
<h2>ウェビナー別</h2>
<div class="tabgrid">
  <details class="fold" id="f-webinar"><summary><span class="tri">▶</span>お題ごとの成果<span class="cnt">{len(wbs)}回分</span></summary>
    <div class="tablewrap"><table>
    <thead><tr><th>ウェビナー</th><th>掲載期間</th><th>広告費</th>
    <th>IMP</th><th>CPM</th><th>クリック</th><th>CTR</th><th>CPC</th>
    <th>CV（申込）</th><th>CVR</th><th>CPL</th>
    <th>リード</th><th>商談数</th><th>商談化率</th><th>成約数</th><th>成約金額</th><th>回収率</th>
    <th class="pad" aria-hidden="true"></th></tr></thead>
    <tbody>{"".join(wrows)}</tbody></table></div></details>
</div>"""
    else:
        webinar_section = ""

    # ---- 期間フィルタに渡す生の数字 ----
    # KPIは期間ごとに計算し直す必要があるので、集計済みの表示値ではなく
    # 週ごと・日ごとの素の数を持たせる。費用の null はそのまま null で運ぶ
    # （0にすると「データが無い期間」を「0円で回した」ことにしてしまう）。
    raw_weeks = {}
    for w in weeks:
        r = rows[w]
        a = arows[w]
        raw_weeks[w] = {
            "leads": r["total"]["leads"], "cost": r["total"]["cost"],
            "cvden": r["total"]["cvden"],
            "deals": r["total"]["deals"], "won": r["total"]["won"],
            "amt": r["total"]["won_amount"],
            "al": a["leads"], "ad": a["deals"], "aw": a["won"],
            "aa": a.get("won_amount", 0),
        }
    wkend = {w: (dt.date.fromisoformat(w) + dt.timedelta(days=6)).isoformat()
             for w in weeks}

    # 日別のKPI。週次と同じ数え方（CPLの分母は web だけCV、他はリード数）で
    # 組み直す。期間を1日に絞ったとき、週次だけだとその週まるごとが出るため。
    dd_src = data.get("direct_day") or {}
    ad_src = data.get("agency_day") or {}
    raw_dkpi, dcost_first = {}, None
    for day in sorted(set(dd_src) | set(ad_src)):
        chs = dd_src.get(day) or {}
        agy = ad_src.get(day) or {}
        cost, cvden, leads, deals, won, amt = 0, 0, 0, 0, 0, 0
        for ch in CHANNEL_KEYS:
            r = chs.get(ch) or {}
            leads += r.get("leads", 0)
            deals += r.get("deals", 0)
            won += r.get("won", 0)
            amt += r.get("won_amount", 0)
            cost += r.get("cost", 0) or 0
            cvden += r.get("cv") or r.get("leads", 0)
            if ch == "web" and (r.get("cost") or r.get("cv")):
                if dcost_first is None or day < dcost_first:
                    dcost_first = day
        raw_dkpi[day] = [leads, cost, cvden, deals, won, amt,
                         agy.get("leads", 0), agy.get("deals", 0),
                         agy.get("won", 0), agy.get("won_amount", 0)]
    raw = {
        "weeks": raw_weeks,
        "wkend": wkend,
        "dkpi": raw_dkpi,
        # この日以降なら日別の広告費が揃っているので、日単位で正確に切れる。
        # それより前は日次入力タブが無く週次テンプレしか無いので週で出す。
        "dfrom": dcost_first or "9999-12-31",
        "days": {k: [v.get("calls", 0), v.get("connected", 0),
                     v.get("appts", 0), v.get("leads", 0),
                     v.get("called", 0)]
                 for k, v in (data.get("calls") or {}).items()},
        "fs": {k: [v.get("mtgs", 0), v.get("props", 0),
                   v.get("wons", 0), v.get("wonamt", 0)]
               for k, v in (data.get("fs") or {}).items()},
        # web広告カードの再計算用。全キャンペーン合計（ap / ap_day）を渡す。
        # data.json の adperf / adperf_day はウェビナーだけなので使わない。
        "adperf": {k: [v["spend"], v["imp"], v["clicks"], v["cv"]]
                   for k, v in sorted(ap.items())},
        "adpd": {k: [v["spend"], v["imp"], v["clicks"], v["cv"]]
                 for k, v in sorted(ap_day.items())},
        "wklabel": {w: week_label(w) for w in weeks},
        "start": weeks[0],
        "end": period_end.isoformat(),
    }

    day_dates = js(daily["chart"]["dates"]) if daily else "[]"
    day_labels = js(daily["chart"]["labels"]) if daily else "[]"
    day_conn = js(daily["chart"]["connected"]) if daily else "[]"
    day_no = js(daily["chart"]["noans"]) if daily else "[]"

    charts_js = f"""
var RAW={js(raw)};
var CUM={"true" if CUMULATIVE_RATE_CHARTS else "false"};
var WK={js(weeks)};
var CPL_W={js(weeks[cpl_from:] if cpl_from is not None else [])};
var LEADS_BARS={js(leads_bars)};
var LEADS_MA={js(leads_ma)};
var CPL_DS={js(cpl_ds)};
var MTG_DS={js(mtg_ds)}; var MTG_BASE={js(mtg_base)};
var WIN_DS={js(win_ds)}; var WIN_BASE={js(win_base)};
var AG_DS={js(ag_ds)}; var AG_RATE_DS={js(ag_rate_ds)};
var AG_LEADS={js(am["leads"])};
var DAY_D={day_dates}; var DAY_L={day_labels};
var DAY_C={day_conn}; var DAY_N={day_no};
var TOTC='{COLORS["total"]}';
var CC='{COLORS["call_conn"]}'; var CN='{COLORS["call_noans"]}';
var L={js(labels)};
{DRAW_JS}{FILTER_JS}
resetRange();
showAge();
"""

    try:
        with open(CHARTJS_PATH, encoding="utf-8") as f:
            chartjs = f.read()
    except OSError:
        fail(
            f"Chart.js が見つかりません: {CHARTJS_PATH}\n"
            "スキルの assets/chart.umd.js が欠けています。"
        )

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script>{chartjs}</script>
<style>{CSS}</style></head>
<body><div class="wrap">

<h1>{title}</h1>
<div class="sub"><span id="period">{period}（週次・月曜始まり）</span>{('　/　生成日 ' + gen) if gen else ''}<span id="age"></span></div>
<div class="range">
  <label>期間</label>
  <input type="date" id="from" value="{weeks[0]}" min="{weeks[0]}" max="{period_end.isoformat()}">
  <span class="dash">〜</span>
  <input type="date" id="to" value="{period_end.isoformat()}" min="{weeks[0]}" max="{period_end.isoformat()}">
  <button type="button" onclick="onApply()">適用</button>
  <button type="button" class="ghost" onclick="resetRange()">全期間</button>
  <button type="button" class="ghost" onclick="reloadFresh()">最新を取得</button>
</div>

<div class="summary">
  <div class="card d"><div class="tag">直契約<span class="taglabel">期間累計</span></div>
    <div class="kpis">{direct_kpis}</div></div>
  <div class="card a"><div class="tag">代理店<span class="taglabel">期間累計</span></div>
    <div class="kpis">{agency_kpis}</div></div>
</div>

{funnel_section}
{adperf_section}
{webinar_section}
{daily_section}
{fs_section}
<h2>直契約</h2>
<div class="charts">
  <div class="card"><h3>チャネル別 リード数推移（積み上げ＝合計）</h3>
    <div class="chart"><canvas id="c_leads"></canvas></div></div>
  <div class="card"><h3>{cpl_title}</h3>
    <div class="chart"><canvas id="c_cpl"></canvas></div></div>
  <div class="card"><h3>{mtg_title}</h3>
    <div class="chart tall"><canvas id="c_mtg"></canvas></div>
    <div class="base"><canvas id="c_mtg_b"></canvas></div>
    <div class="baselabel">母数：週次リード数</div></div>
  <div class="card"><h3>{win_title}</h3>
    <div class="chart tall"><canvas id="c_win"></canvas></div>
    <div class="base"><canvas id="c_win_b"></canvas></div>
    <div class="baselabel">母数：週次商談数</div></div>
</div>
<details class="fold" id="f-direct"><summary><span class="tri">▶</span>週次テーブル<span class="cnt">{len(weeks)}週 × チャネル別</span></summary>
<div class="legend">各セルの下段グレー数値は4週移動平均</div>
<div class="tablewrap"><table>
<thead><tr><th>週</th><th>チャネル</th><th>リード数</th><th>費用</th><th>CPL</th>
<th>商談数</th><th>商談化率</th><th>成約数</th><th>成約率</th></tr></thead>
<tbody>{direct_table}</tbody></table></div></details>

<h2>代理店</h2>
<div class="charts">
  <div class="card"><h3>リード数・成約数の推移</h3>
    <div class="chart"><canvas id="c_ag"></canvas></div></div>
  <div class="card"><h3>商談化率・成約率推移（4週移動平均）</h3>
    <div class="chart tall"><canvas id="c_agr"></canvas></div>
    <div class="base"><canvas id="c_agr_b"></canvas></div>
    <div class="baselabel">母数：週次リード数</div></div>
</div>
<details class="fold" id="f-agency"><summary><span class="tri">▶</span>週次テーブル<span class="cnt">{len(weeks)}週</span></summary>
<div class="legend">各セルの下段グレー数値は4週移動平均</div>
<div class="tablewrap"><table>
<thead><tr><th>週</th><th>リード数</th><th>商談数</th><th>商談化率</th>
<th>成約数</th><th>成約率</th></tr></thead>
<tbody>{agency_table}</tbody></table></div></details>

<h2>展示会別CPL</h2>
<details class="fold" id="f-expo" open><summary><span class="tri">▶</span>展示会別CPL<span class="cnt">{len(data.get("expos") or [])}件</span></summary>
<div class="tablewrap"><table>
<thead><tr><th>展示会名</th><th>開催日</th><th>費用</th><th>リード数</th><th>CPL</th>
<th>商談数</th><th>成約数</th></tr></thead>
<tbody>{expo_table}</tbody></table></div></details>

</div>
<script>{CHART_JS}{charts_js}{FOLD_JS}</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="介護校ファネルダッシュボードHTMLを生成")
    ap.add_argument("data", help="data.json のパス")
    ap.add_argument("-o", "--output", default="funnel_dashboard.html")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)

    validate(data)
    html = render(data)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {args.output} ({len(html):,} bytes, {len(data['week_starts'])} weeks)")


if __name__ == "__main__":
    main()
