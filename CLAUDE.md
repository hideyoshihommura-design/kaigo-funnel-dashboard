# 介護校ファネルダッシュボード

ホリエモンAI学校 介護校のリード→商談→成約を週次集計して公開している静的サイト。

- 公開URL: https://hideyoshihommura-design.github.io/kaigo-funnel-dashboard/
- リポジトリ: `hideyoshihommura-design/kaigo-funnel-dashboard`（**public**）
- 閲覧者は社内外。パスワードなし。

## 構成

| パス | 役割 |
|---|---|
| `scripts/fetch_data.py` | HubSpot + Googleスプレッドシート → `data.json` |
| `scripts/build_dashboard.py` | `data.json` → `index.html`（標準ライブラリのみ。Chart.jsは`assets/`から埋め込む） |
| `scripts/postprocess.py` | noindex・PWA・「生成日」→「数字の更新」の置換 |
| `config/channel_map.json` | 流入経路→チャネル（event/web/line/referral/other/agency）、除外経路 |
| `config/webinars.json` | ウェビナー各回の掲載期間と、広告費・CVの手動上書き |
| `data.json` / `index.html` | **生成物。手で編集しない** |

`check_hubspot_access.py` と `probe_ads.py` は読み取り専用の調査用。

## 変更のしかた

1. `scripts/` か `config/` を直す。`index.html` を直接いじっても次の自動実行で消える。
2. 認証情報なしで見た目だけ確認できる:
   ```
   python scripts/build_dashboard.py data.json -o /tmp/preview.html
   ```
   `fetch_data.py` はこのPCでは動かない（キーは GitHub Secrets にしか無い）。
3. commit → push → `gh workflow run update-dashboard.yml` → 完了を待つ →
   公開ページのタイムスタンプが変わったことまで確認する。

自動実行は3時間ごと（cron `5 */3 * * *`, UTC）。GitHub側に間引かれることがある。

## 集計上の決めごと（過去に間違えた箇所）

- **CPLの分母は週次テンプレのCV数。** HubSpotのユニーク連絡先ではない。
  同じ人が複数回フォームを出すため、ユニークだと分母が小さくなりCPLが跳ね上がる。
  ページ内のCPLは全部この分母で揃える。
- **広告費はウェビナーキャンペーンのみ。** 日次入力タブのキャンペーン名に「ウェビナー」を
  含む行だけ合計する。全キャンペーンを足すと倍以上になる。
- **取り消された取引は成約に数えない。** `closedwon` に入った履歴があっても、
  現在のステージが `closedwon` でなければ除外する。
- **ウェビナー各回の期間は重ねない。** 掲載期間が重複する回は境界で切る。
  第1〜3回は日次入力タブが2026-06-09開始で遡れないため、`config/webinars.json` に
  広告費とCVを手入力してある。
- **FS指標は直契約のみ**（`ch != "agency"`）。全期間にすると上部の総商談数と一致する必要がある。
- 商談はコンタクト獲得週のコホート、FS指標はステージ到達日ベース。
- 率は加重（合計÷合計）。週ごとの率を平均しない。

主な定数: `PERIOD_START=2025-09-01` / `CALLS_START=2026-06-01` /
`VENDOR_START=2026-08-19`（架電業者稼働開始）/ `AD_DATA_FIRST_MONDAY=2026-04-06`

## 表示の決めごと

- **分析コメント・注釈・改善提案は書かない。数字だけ。**
- 用語: 転換率ではなく**商談化率**。面談予約は**面談予約 獲得数**。
- インサイドセールス（架電）とフィールドセールス（面談）は別セクション。
- セクション順: リード獲得 → ウェビナー別 → IS活動量 → FS活動量 → 直契約 → 代理店 → 展示会別CPL
- 折りたたみは既定で閉じる。開閉状態は localStorage で保持。
- 期間指定を変えたら各カードの数値も再計算されること（架電業者カードは対象外）。

## 触ってはいけないこと

- **トークンやキーの発行・入力をこちらでやらない。** ユーザー本人が GitHub Secrets に入れる。
  中身を見せてもらう必要もない。
- 公開リポジトリなので、認証情報をコミットしない（`.gitignore` で名前を弾いている）。
- 公開ページから再集計を起動するボタンは作れない（トークンが露出する）。
