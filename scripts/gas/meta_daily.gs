/**
 * Meta広告 → 「介護校ダッシュボード 広告日次データ」シート 自動入力
 *
 * 毎朝1回、直近7日ぶんをMetaから取り直してシートに書く。
 * 日付＋媒体＋キャンペーン＋広告 が同じ行があれば上書き、無ければ末尾に追加する。
 *
 * ── 広告単位（2026-11-18〜） ──────────────────────────
 * ウェビナーのキャンペーンだけ level=ad で取り、H列「広告」に広告名を入れて
 * 1日に複数行書く。回ごとの広告が前後の回の掲載期間にはみ出して走っていて、
 * キャンペーン単位だと重なった日の費用が一方の回に丸ごと乗るため。
 * 詳しくは MD_AD_LEVEL のコメント。
 *
 * ── なぜ7日ぶんを取り直すのか ──────────────────────────
 * Metaの数字はその日のうちに確定しない。手入力していた頃は入力した時点の
 * 速報値がそのまま残っていた（2026-11-10 のクリックは 102 で入っていたが、
 * 確定値は 127 だった）。毎日さかのぼって上書きすれば確定値に追随する。
 *
 * ── キャンペーン名について ──────────────────────────
 * Meta側のキャンペーン名とシートのキャンペーン列は一致しない。
 * シートの「ウェビナー」は実際には「【リード】ホリエモンAI学校介護校_総合」
 * の数字が入っている。config/ad_campaigns.json は完全一致で集計するので、
 * Metaの名前をそのまま書くとウェビナー別の集計が空になる。
 * 下の CAMPAIGN_MAP で変換する。**キャンペーンIDで引く**（名前は変わりうる）。
 *
 * ── 黙って止まらないこと ──────────────────────────
 * 鍵の期限切れ・未知のキャンペーン・APIエラーは全部メールで知らせる。
 * シートが黙って更新されなくなるのが一番まずい。
 *
 * ── 設置手順 ──────────────────────────────────────
 * 1. スクリプト プロパティ（⚙️ プロジェクトの設定）
 *      META_TOKEN     … システムユーザートークン（ads_read のみ・60日で切れる）
 *      NOTIFY_EMAIL   … 省略可。未設定なら実行ユーザー宛に送る
 *    **ここに入れるのはトークンだけ。** 広告アカウントは下の MD_ACCOUNTS に書く。
 *    アカウントIDは秘密ではないので、コードに置いたほうが履歴に残って経緯を追える。
 *    設定画面にはトークンが平文で並ぶので、用がないなら開かない。
 * 2. 関数 setupMetaTrigger を1回だけ実行（毎朝7時のトリガーが作られる）
 * 3. 動作確認は syncMetaDaily を手で実行
 */

var MD_API = 'https://graph.facebook.com/v21.0';

/**
 * 読みに行く広告アカウント。**ここが正。**スクリプトプロパティの
 * META_ACCOUNTS は見ていない（古い値が残っていても影響しない）。
 *
 *   act_1733426553997050 … ホリエモンAI学校介護校
 *   act_295486751582822  … ACG1（CareTech／Claude講座LP）
 *     所有者が別ポートフォリオ（大牟禮康佑's Business = 409243572945361）。
 *     介護校（1333643245086340）へ**パートナー共有**したうえで
 *     `Conversions API System User` に「パフォーマンスを確認」で割り当ててある。
 *     **共有を外すとこのアカウントだけ静かに0件になる。**
 */
var MD_ACCOUNTS = [
  'act_1733426553997050',
  'act_295486751582822'
];

/** 何日さかのぼって取り直すか。Metaの数字が確定するまでの猶予。 */
var MD_LOOKBACK_DAYS = 7;

/** シートの媒体列に入れる値。この広告アカウントはMetaのみ。 */
var MD_MEDIUM = 'Meta';

/**
 * Metaのキャンペーン → シートのキャンペーン列。
 * ここに無いキャンペーンは書き込まず、メールで知らせる（黙って落とさない）。
 * 新しいキャンペーンを始めたら、ここに1行足すこと。
 */
var MD_CAMPAIGN_MAP = {
  // act_1733426553997050（ホリエモンAI学校介護校）
  '120243893187730769': 'ウェビナー',     // 【リード】ホリエモンAI学校介護校_総合
  // act_295486751582822（ACG1／大牟禮康佑's Business からパートナー共有）
  // 1本のキャンペーンに Claude講座LP と CareTech のLPが同居している。
  // Meta側では分けられないので、今の実態（CareTech）に寄せて あおぞらTech にする。
  '120248495898370169': 'あおぞらTech'   // Claude講座LP・あおぞらCareTech研究所_リード獲得
};

/**
 * **広告単位で書き出すキャンペーン。**ここに入れたものだけ level=ad で取り直し、
 * シートのH列「広告」に広告名を入れて1日に複数行書く。それ以外は従来どおり
 * キャンペーン単位の1行。
 *
 * なぜウェビナーだけか: 1本のキャンペーンの中に「第N回ウェビナー」という広告が
 * 回ごとにあり、**次の回の広告を前の回の掲載期間中から走らせている。**
 * 2026-11-18 に広告マネージャで実測した重なり:
 *   第16回の広告 … 2026-11-04 開始（第15回の掲載期間は 10-28〜11-04）
 *   第15回の広告 … 2026-11-09 まで継続（第16回の掲載期間は 11-05〜11-11）
 *   2026-11-11   … 第16回 ¥8,736 と 第17回 ¥9,580 が同じ日に走っていた
 * キャンペーン単位のままだと、重なった日の費用は掲載期間を持っている側の回に
 * 丸ごと乗る。広告名があれば config/webinars.json の ads で正しく割れる。
 *
 * **他のキャンペーンを広告単位にしない理由**: 広告単位の合計とキャンペーン単位の
 * 数字は必ずしも一致しない（Meta側の重複排除）。必要の無いところを広告単位に
 * 変えると、過去に公開した数字が理由なく動く。
 */
var MD_AD_LEVEL = {
  '120243893187730769': true   // ウェビナー（【リード】ホリエモンAI学校介護校_総合）
};

/** シートのH列。広告単位で書くときだけ値が入る。 */
var MD_AD_HEADER = '広告';

/** actions のどれをCV数として採るか。シートの手入力値と一致したのが lead。 */
var MD_CV_ACTION = 'lead';


/** 毎朝これが動く。直近 MD_LOOKBACK_DAYS 日ぶんを取り直す。 */
function syncMetaDaily() {
  var until = new Date();
  var since = new Date(until.getTime() - (MD_LOOKBACK_DAYS - 1) * 86400000);
  // **日付は文字列にしてから渡す。** Dateのまま持ち回すと、シートのTZと
  // 広告アカウントのTZ（日本）の差で1日ずれる。ここで広告アカウントのTZに
  // 確定させておけば、以降は文字列なのでずれようがない。
  mdRun_(mdApiYmd_(since), mdApiYmd_(until));
}


/**
 * 期間を指定して取り直す。**1回だけ手で動かす用。**
 *
 * 毎朝の実行は直近7日しか見ないので、長く抜けた期間はこれで埋める。
 * 下の2つの日付を直してから、関数プルダウンで backfillMetaDaily を選んで実行する。
 *
 * 2026-11-17: ACG1（CareTech）を足したとき、2026-09-21〜11-16 が
 * 丸ごと抜けていたのでこれで埋めた。
 * 2026-11-18: ウェビナーを広告単位に変えたので、同じ期間を取り直した
 * （キャンペーン単位の古い行は mdWriteRows_ が消す）。
 */
function backfillMetaDaily() {
  var SINCE = '2026-09-21';
  var UNTIL = '2026-11-18';
  mdRun_(SINCE, UNTIL);
}


/** 本体。since / until は 'YYYY-MM-DD'（広告アカウントのTZで切った日付）。 */
function mdRun_(sinceYmd, untilYmd) {
  var token = PropertiesService.getScriptProperties().getProperty('META_TOKEN');
  var accounts = MD_ACCOUNTS;

  if (!token) {
    mdNotify_('広告日次の自動入力が設定されていません',
      'META_TOKEN が未設定です。\n'
      + 'スプレッドシート → 拡張機能 → Apps Script → プロジェクトの設定 →\n'
      + 'スクリプト プロパティ に META_TOKEN を登録してください。');
    return;
  }

  var rows = [];        // 書き込む行
  var unknown = {};     // 対応表に無かったキャンペーン

  // ── 1周目: アカウント単位・キャンペーンレベル（従来どおり）
  // MD_AD_LEVEL のキャンペーンはここでは書かない。2周目で広告単位に取り直す。
  for (var i = 0; i < accounts.length; i++) {
    var res = mdFetchInsights_(accounts[i], 'campaign', token, sinceYmd, untilYmd);
    if (res.error) {
      mdNotify_('広告日次の自動入力が失敗しました', res.error);
      return;   // 鍵切れなどで全滅しているので、ここで止める
    }
    for (var j = 0; j < res.data.length; j++) {
      var r = res.data[j];
      var name = MD_CAMPAIGN_MAP[r.campaign_id];
      if (!name) {
        unknown[r.campaign_id] = r.campaign_name;
        continue;
      }
      if (MD_AD_LEVEL[r.campaign_id]) { continue; }
      rows.push({
        day: r.date_start,
        campaign: name,
        ad: '',
        spend: Math.round(Number(r.spend || 0)),
        imp: Number(r.impressions || 0),
        clicks: Number(r.clicks || 0),
        cv: mdPickAction_(r.actions, MD_CV_ACTION)
      });
    }
  }

  // ── 2周目: キャンペーン単位・広告レベル
  var adIds = Object.keys(MD_AD_LEVEL);
  for (var a = 0; a < adIds.length; a++) {
    var cid = adIds[a];
    var ares = mdFetchInsights_(cid, 'ad', token, sinceYmd, untilYmd);
    if (ares.error) {
      mdNotify_('広告日次の自動入力が失敗しました（広告単位）', ares.error);
      return;
    }
    for (var k = 0; k < ares.data.length; k++) {
      var ar = ares.data[k];
      var cname = MD_CAMPAIGN_MAP[cid];
      // 広告名が空のことは無いはずだが、空のまま書くとキャンペーン単位の
      // 行と見分けがつかなくなる。広告IDで代用して、必ず何か入れる。
      rows.push({
        day: ar.date_start,
        campaign: cname,
        ad: String(ar.ad_name || ar.ad_id || '(名前なし)'),
        spend: Math.round(Number(ar.spend || 0)),
        imp: Number(ar.impressions || 0),
        clicks: Number(ar.clicks || 0),
        cv: mdPickAction_(ar.actions, MD_CV_ACTION)
      });
    }
  }

  var result = mdWriteRows_(rows);

  var unknownIds = Object.keys(unknown);
  if (unknownIds.length) {
    var lines = unknownIds.map(function (id) {
      return '  ' + unknown[id] + '  (id=' + id + ')';
    });
    mdNotify_('広告日次：対応表に無いキャンペーンがあります',
      '次のキャンペーンに配信実績がありますが、シートに書き込んでいません。\n\n'
      + lines.join('\n')
      + '\n\nscripts/gas/meta_daily.gs の MD_CAMPAIGN_MAP に追記してください。\n'
      + 'シートのキャンペーン列に入れる名前は config/ad_campaigns.json の\n'
      + 'campaigns に載っているものに合わせること（完全一致で集計しています）。');
  }

  Logger.log('更新 ' + result.updated + '行 / 追加 ' + result.added + '行'
    + ' / 古いキャンペーン単位の行を削除 ' + result.removed + '行');
}


/**
 * 日次実績を取る。失敗したら error に理由を入れて返す。
 * node   … 広告アカウント（act_…）またはキャンペーンID
 * level  … 'campaign' か 'ad'
 * since / until は 'YYYY-MM-DD' の文字列（広告アカウントのTZで切ったもの）。
 */
function mdFetchInsights_(node, level, token, since, until) {
  var fields = 'campaign_id,campaign_name,spend,impressions,clicks,actions';
  if (level === 'ad') { fields += ',ad_id,ad_name'; }
  var q = node + '/insights'
    + '?level=' + level
    + '&fields=' + fields
    + '&time_increment=1'
    + '&time_range=' + encodeURIComponent(JSON.stringify({
        since: since, until: until
      }))
    + '&limit=500';

  var url = MD_API + '/' + q + '&access_token=' + token;
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  var body = res.getContentText();

  if (res.getResponseCode() !== 200) {
    var msg = body;
    try {
      var e = JSON.parse(body).error || {};
      // 190 は期限切れ・失効。60日ごとに必ずここに来るので、文言を分けておく。
      if (e.code === 190) {
        msg = 'Metaの鍵（アクセストークン）が切れています。\n\n'
          + '作り直す場所:\n'
          + 'https://business.facebook.com/latest/settings/system_users?business_id=1333643245086340\n'
          + '  → Conversions API System User → トークンを生成\n'
          + '  → アプリ: kaigo-ads-reader / 60日間 / ads_read だけ\n\n'
          + '作ったら スプレッドシート → 拡張機能 → Apps Script →\n'
          + 'プロジェクトの設定 → スクリプト プロパティ の META_TOKEN を差し替えてください。\n\n'
          + 'Metaからの応答: ' + (e.message || '');
      } else {
        msg = 'Metaがエラーを返しました（code=' + e.code + '）\n' + (e.message || body);
      }
    } catch (ignore) {}
    return { error: msg, data: [] };
  }

  try {
    return { error: null, data: (JSON.parse(body).data || []) };
  } catch (e) {
    return { error: 'Metaの応答が読めませんでした。\n' + body.slice(0, 500), data: [] };
  }
}


/** actions から目的の action_type の値を取る。無ければ0。 */
function mdPickAction_(actions, type) {
  if (!actions) { return 0; }
  for (var i = 0; i < actions.length; i++) {
    if (actions[i].action_type === type) { return Number(actions[i].value || 0); }
  }
  return 0;
}


/**
 * シートに書く。日付＋媒体＋キャンペーン＋広告 が同じ行は上書き、無ければ追加。
 * 手で入れた行（広告は空）も同じキーなので、重複せずに上書きされる。
 *
 * **広告単位に切り替えた日の後始末がここに要る。** 同じ日・同じキャンペーンに
 * 「キャンペーン単位の古い行（広告が空）」と「広告単位の新しい行」が両方残ると、
 * 合計が二重になって総web費用もCPLも狂う。広告単位で書くキーについては、
 * 広告が空の古い行を消してから書く。
 */
function mdWriteRows_(rows) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  mdEnsureAdHeader_(sh);

  var last = sh.getLastRow();
  var index = {};
  var plainRow = {};   // 日|媒体|キャンペーン → 広告が空の既存行の行番号

  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 8).getValues();
    for (var i = 0; i < vals.length; i++) {
      var d = vals[i][0];
      if (!d) { continue; }
      var ymd = (d instanceof Date ? mdSheetYmd_(d) : String(d));
      var base = ymd + '|' + vals[i][1] + '|' + vals[i][2];
      var ad = String(vals[i][7] || '').trim();
      index[base + '|' + ad] = i + 2;          // シート上の行番号
      if (!ad) { plainRow[base] = i + 2; }
    }
  }

  // 広告単位で書くキーに、広告が空の古い行が残っていたら消す。
  // 行を消すと行番号がずれるので、**下から順に**消してから索引を作り直す。
  var stale = {};
  for (var s = 0; s < rows.length; s++) {
    if (!rows[s].ad) { continue; }
    var b = rows[s].day + '|' + MD_MEDIUM + '|' + rows[s].campaign;
    if (plainRow[b]) { stale[plainRow[b]] = true; }
  }
  var staleRows = Object.keys(stale).map(Number).sort(function (x, y) { return y - x; });
  for (var t = 0; t < staleRows.length; t++) { sh.deleteRow(staleRows[t]); }
  if (staleRows.length) {
    // 索引を作り直す（行番号が繰り上がっている）
    index = {};
    last = sh.getLastRow();
    if (last >= 2) {
      var v2 = sh.getRange(2, 1, last - 1, 8).getValues();
      for (var m = 0; m < v2.length; m++) {
        var d2 = v2[m][0];
        if (!d2) { continue; }
        var ymd2 = (d2 instanceof Date ? mdSheetYmd_(d2) : String(d2));
        index[ymd2 + '|' + v2[m][1] + '|' + v2[m][2]
          + '|' + String(v2[m][7] || '').trim()] = m + 2;
      }
    }
  }

  var added = [], updated = 0;

  for (var j = 0; j < rows.length; j++) {
    var r = rows[j];
    var k = r.day + '|' + MD_MEDIUM + '|' + r.campaign + '|' + (r.ad || '');
    var line = [r.spend, r.imp, r.clicks, r.cv, r.ad || ''];
    if (index[k]) {
      sh.getRange(index[k], 4, 1, 5).setValues([line]);
      updated++;
    } else {
      // 日付は文字列ではなくDateで入れる（A列の書式が yyyy-mm-dd のため）。
      // **必ずシートのタイムゾーンで作る。** new Date(y, m, d) はスクリプト側の
      // タイムゾーンで解釈されるので、スクリプトとシートがずれていると前日に
      // 転ぶ（実際に 11/16 が 11/15 で入った。正午にしても直らなかったので
      // 差は13時間以上あった）。parseDate にシートのTZを渡せば、スクリプト側の
      // 設定が何であっても一致する。
      added.push([mdDate_(r.day), MD_MEDIUM, r.campaign].concat(line));
    }
  }

  if (added.length) {
    sh.getRange(sh.getLastRow() + 1, 1, added.length, 8).setValues(added);
  }
  return { updated: updated, added: added.length, removed: staleRows.length };
}


/** H列の見出しが無ければ入れる。fetch_data.py は見出しで列を探す。 */
function mdEnsureAdHeader_(sh) {
  var cur = String(sh.getRange(1, 8).getValue() || '').trim();
  if (cur !== MD_AD_HEADER) { sh.getRange(1, 8).setValue(MD_AD_HEADER); }
}


/**
 * タイムゾーンは2種類ある。**混ぜると必ず1日ずれる。**
 *
 *   Metaに渡す日付   … 広告アカウントのタイムゾーン（日本）。MD_ACCOUNT_TZ
 *   シート上の日付   … このスプレッドシートのタイムゾーン。mdTz_()
 *
 * このシートはアメリカ時間で作られている。スクリプト側は日本時間。
 * 最初に new Date(y,m,d) で書いたら 11/16 が 11/15 で入り、
 * 次に全部シートのTZに寄せたら「今日」が 11/15 と判定されて
 * 11/16 を取りに行かなくなった。両方やって初めて正しくなる。
 */
var MD_ACCOUNT_TZ = 'Asia/Tokyo';

function mdTz_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone() || MD_ACCOUNT_TZ;
}

/** Metaに渡す日付。広告アカウントのTZで切る。 */
function mdApiYmd_(d) {
  return Utilities.formatDate(d, MD_ACCOUNT_TZ, 'yyyy-MM-dd');
}

/** シート上のDate → YYYY-MM-DD。シートのTZで読む。 */
function mdSheetYmd_(d) {
  return Utilities.formatDate(d, mdTz_(), 'yyyy-MM-dd');
}

/** 'YYYY-MM-DD' → その日の正午のDate（シートのTZで） */
function mdDate_(s) {
  return Utilities.parseDate(s + ' 12:00:00', mdTz_(), 'yyyy-MM-dd HH:mm:ss');
}


/** 知らせる。宛先は NOTIFY_EMAIL、無ければ実行ユーザー。 */
function mdNotify_(subject, body) {
  var to = PropertiesService.getScriptProperties().getProperty('NOTIFY_EMAIL')
    || Session.getEffectiveUser().getEmail();
  Logger.log('[通知] ' + subject + '\n' + body);
  if (!to) { return; }
  try {
    MailApp.sendEmail(to, '[広告日次] ' + subject, body);
  } catch (e) {
    Logger.log('メールを送れませんでした: ' + e);
  }
}


/** 毎朝7時のトリガーを作る。1回だけ手で実行する。 */
function setupMetaTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'syncMetaDaily') { ScriptApp.deleteTrigger(t); }
  });
  ScriptApp.newTrigger('syncMetaDaily').timeBased().atHour(7).everyDays(1).create();
  Logger.log('毎朝7時台に syncMetaDaily が動くようにしました。');
}
