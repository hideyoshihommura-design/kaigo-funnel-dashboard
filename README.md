# 介護校 ファネルダッシュボード

ホリエモンAI学校 介護校のリード→商談→成約ファネル。
1時間ごとに HubSpot / Googleスプレッドシートから集計し直す仕組みを用意してある。
**ただし現時点では未稼働。** 稼働にはリポジトリへの Secret 登録が必要（下記「稼働までに残っていること」）。
現在の `index.html` は 2026-08-26 時点の数字を手で配置したもの。

公開URL: https://hideyoshihommura-design.github.io/kaigo-funnel-dashboard/

パスワードなし・URLを知る全員が閲覧できる（意図的な選択）。
検索避けだけ `robots.txt` と `noindex` の meta で二重にかけている。
スマホでは「ホーム画面に追加」でアプリとして開ける（`manifest.webmanifest`）。

## 仕組み

```
HubSpot ─┐
         ├─→ scripts/fetch_data.py ──→ data.json ──→ scripts/build_dashboard.py ──→ index.html
シート ──┘        （生の実数だけ）                     （集計・移動平均・グラフ）
                                                            ↓
                                                   scripts/postprocess.py
                                                   （noindex / PWA / 更新時刻）
```

`.github/workflows/update-dashboard.yml`（稼働時）が毎時5分（UTC）にこれを回し、
**data.json が変わったときだけ** index.html を差し替えて main に push する。
GitHub Pages は main ブランチ配信なので、push がそのまま公開になる。

手で今すぐ更新したいときは、Actions タブ →「ダッシュボード更新」→ Run workflow。

### 数字が壊れないようにしていること

自動更新でいちばん怖いのは「取れなかった」が「0件だった」として公開されることなので、
そうならない側に倒してある。**失敗した回は公開物を差し替えない**ので、
前回の正常な内容がそのまま残る。

| 段階 | 見ているもの | 落ちたときの挙動 |
|---|---|---|
| 取得 | HubSpot / Sheets のAPIエラー、ページング切れ、`total` との不一致 | 例外で停止 |
| 妥当性 | 総リード数 < 100、総商談数 < 10、展示会0件 | 停止 |
| 妥当性 | 総リード数が**前回より5%以上減っている** | 停止 |
| 生成 | index.html が 200KB 未満、見出しが無い、`noindex` が無い | 停止 |

停止すると GitHub から失敗通知メールが届く。それが「更新が止まった」の合図。
**ダッシュボード側には注記や免責を一切出さない**（数字だけを載せる方針）。

### 「数字の更新」の時刻の意味

見出し下に出る時刻は、**数字が実際に変わった時刻**。ジョブが走った時刻ではない。
変化が無かった回は差し替えないので時刻は動かない。
18時に開いて「9:00」なら「9時以降は動きがない」という意味で、古い数字ではない。

## ファイル

| パス | 役割 |
|---|---|
| `index.html` | 公開されている生成物。**手で編集しない**（次の自動更新で消える） |
| `data.json` | 生の実数。自動更新の入力かつ、次回の妥当性チェックの比較対象 |
| `scripts/fetch_data.py` | HubSpot REST API と Sheets API から `data.json` を作る |
| `scripts/build_dashboard.py` | `data.json` → HTML。集計・4週移動平均・グラフは全部ここ |
| `scripts/postprocess.py` | 生成後のHTMLに noindex / PWA / 更新時刻を足す |
| `config/channel_map.json` | HubSpot の `route` 内部値 → チャネル（イベント/web/LINE/紹介/その他/代理店） |
| `config/data-sources-reference.md` | 各データソースの仕様と落とし穴。**仕様を変える前に必読** |
| `assets/chart.umd.js` | Chart.js v4.4.1。HTMLにインライン埋め込みする（外部通信なし） |

### 公開に必要なものは postprocess.py に入れる

`build_dashboard.py` は毎回HTMLをゼロから作り直す。
`index.html` に手で書き足したものは次の更新で消えるので、
noindex や manifest のような**公開に必要なものは必ず `postprocess.py` に入れる**。
消えると検索結果に出てしまうため、ワークフローの検査でも `noindex` の有無を見ている。

## 初期設定

認証情報の発行手順（画面つき・知識ゼロ前提）:
https://claude.ai/code/artifact/be643e4f-b054-4d8b-b1ae-40090c95f8ad

必要な GitHub Secrets:

| 名前 | 中身 |
|---|---|
| `HUBSPOT_TOKEN` | HubSpot 非公開アプリのトークン（`pat-na1-` で始まる）。スコープは `crm.objects.contacts.read` / `crm.objects.deals.read` / `crm.objects.calls.read` の読み取りのみ |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google サービスアカウントのJSONキーの中身。対象2シートに閲覧者で共有しておく |

## 手元で動かす

```bash
pip install google-auth requests
export HUBSPOT_TOKEN='pat-na1-...'
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat ~/Downloads/kaigo-funnel-xxxx.json)"

python scripts/fetch_data.py -o data.json --previous data.json
python scripts/build_dashboard.py data.json -o index.html
python scripts/postprocess.py index.html
```

`fetch_data.py` は気づいた点（`channel_map.json` に無い route、
コンタクト未紐付けの取引、`hs_call_direction` 未設定のコール、
対応表に無いコール成果GUIDなど）を標準エラーにまとめて出す。
**HTMLには出さない**ので、ここを読むこと。

`--end YYYY-MM-DD` で集計終端を指定できる（既定は実行日・JST）。

## 稼働までに残っていること

1. リポジトリに Secret を2つ登録する（**リポジトリ管理者本人の作業**）

   - `HUBSPOT_TOKEN` … HubSpotプライベートアプリのアクセストークン
   - `GOOGLE_SERVICE_ACCOUNT_JSON` … Googleサービスアカウントの鍵JSON（全文）

   Settings → Secrets and variables → Actions → New repository secret

2. `.github/workflows/update-dashboard.yml` をコミットする

   **1より先に2をやると毎時失敗し続ける**ので順番を守る。

### 検証済みのこと

`data.json` → `build_dashboard.py` → `postprocess.py` はローカルで実行し、
公開中のページと KPI 14項目すべてが一致することを確認済み（2026-08-27）。
`fetch_data.py` は認証情報が要るため未実行。
