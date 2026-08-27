#!/usr/bin/env python3
"""生成された index.html に、公開用に必要なものを足す.

    python3 scripts/postprocess.py index.html

build_dashboard.py は毎回HTMLをゼロから作り直すので、ここで足しているものを
HTMLに手で書き込んでも次の更新で消える。公開に必要なものは必ずこのスクリプトに
入れること。とくに noindex は、消えると検索結果に出てしまう。

足すもの:
  1. noindex,nofollow    … 検索避け。robots.txt と合わせて二重にかける
  2. PWA manifest 一式   … スマホのホーム画面に置いて全画面で開けるようにする
  3. 「生成日」→「数字の更新 日時」… 1時間ごとに見に行くので、日付だけでは
                                      今の数字なのか判断できない

「数字の更新」は**数字が実際に変わった時刻**で、ジョブが走った時刻ではない。
変化が無かった回は公開物を差し替えないので、この時刻は動かない。
18時に開いて「9:00」と出ていれば「9時以降は動きがない」という意味で、
古い数字を見せているわけではない。

ジョブ自体が壊れて更新が止まった場合は、GitHub Actions の失敗通知で気づく。
ダッシュボード側に「更新が止まっています」等の断り書きは出さない
（このダッシュボードは注記・免責を一切書かない方針）。

Service Worker は意図的に入れていない。オフラインキャッシュを持たせると
古い数字が残り続け、自動更新にした意味が消えるうえ、閲覧者には
「古い数字を見ている」ことが分からない。
"""

import datetime as dt
import re
import sys

JST = dt.timezone(dt.timedelta(hours=9))

HEAD_ADDITIONS = """<meta name="robots" content="noindex,nofollow">
<link rel="manifest" href="./manifest.webmanifest">
<meta name="theme-color" content="#08959C">
<link rel="icon" type="image/svg+xml" href="./icon.svg">
<link rel="apple-touch-icon" href="./icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="介護校ファネル">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
"""

ANCHOR = '<meta name="viewport" content="width=device-width,initial-scale=1">'


def main():
    if len(sys.argv) < 2:
        sys.exit("使い方: postprocess.py <index.html>")
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        html = f.read()

    if ANCHOR not in html:
        sys.exit(
            f"{path} に viewport の meta が見つかりません。"
            "build_dashboard.py の <head> の作り方が変わった可能性があります。"
        )
    if 'name="robots"' in html:
        sys.exit(f"{path} には既に robots の meta があります。二重挿入を避けて中止します。")

    html = html.replace(ANCHOR, ANCHOR + "\n" + HEAD_ADDITIONS, 1)

    now = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    html, n = re.subn(r"生成日\s*\d{4}-\d{2}-\d{2}", f"数字の更新 {now}", html, count=1)
    if n == 0:
        print(
            "[warn] 『生成日 YYYY-MM-DD』が見つかりませんでした。"
            "更新時刻を出せていません。",
            file=sys.stderr,
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] {path} に公開用のメタ情報を追加（数字の更新 {now} JST）", file=sys.stderr)


if __name__ == "__main__":
    main()
