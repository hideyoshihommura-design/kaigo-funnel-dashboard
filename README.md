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

自動更新はしていない。数値は生成時点のスナップショット。

最新化するときは、ダッシュボードHTMLを生成したうえで Claude に
「このHTMLでGitHub Pagesを更新して」と指示すれば、`index.html` を差し替えて push する。
手作業でやる場合も `index.html` を上書きして main に push すれば GitHub Pages が再デプロイする。

公開URLは差し替えても変わらない。

## 検索エンジン対策

業績数値を含むため `robots.txt` と `<meta name="robots" content="noindex,nofollow">` で検索避けをしている。
パスワードは設定していないので、URLを知っている人は誰でも閲覧できる。
