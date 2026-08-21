# 介護校 ファネルダッシュボード

ホリエモンAI学校 介護校（Aozora-cg）のリード→商談→成約ファネルを週次で可視化した静的ダッシュボード。

- 公開URL: https://hideyoshihommura-design.github.io/kaigo-funnel-dashboard/
- 集計期間・生成日はページ冒頭に表示（2025-09-01 起点、週次・月曜始まり）

## 構成

`index.html` の1ファイル完結。Chart.js v4.4.1 をインラインで同梱しているため外部通信は発生しない。

- 直契約: チャネル別（イベント / web / LINE / 紹介 / その他）の週次リード数・費用・CPL・商談化率・成約率
- 代理店: 週次リード数・商談化率・成約率
- 展示会別CPL

各セルの下段グレー数値は4週移動平均。

## 更新

毎週金曜18:00に Claude のスケジュールタスク `kaigo-funnel-weekly` が HubSpot とスプレッドシートから再集計し、
`index.html` を差し替えて main に push する（GitHub Pages が自動で再デプロイ）。

タスク定義: `~/.claude/scheduled-tasks/kaigo-funnel-weekly/SKILL.md`

金曜実行のため、最新週の行は月〜金までの途中集計になる。
手動で最新化したい場合は Claude に「ファネルダッシュボードを更新して」と指示すれば同じ処理が走る。

## 検索エンジン対策

業績数値を含むため `robots.txt` と `<meta name="robots" content="noindex,nofollow">` で検索避けをしている。
パスワードは設定していないので、URLを知っている人は誰でも閲覧できる。
