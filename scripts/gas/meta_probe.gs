/**
 * Meta広告 調査用スクリプト（1回だけ動かす。書き込みはしない）
 *
 * 目的は2つ。
 *   1. 広告アカウントに、どういう名前のキャンペーンがあるか見る
 *   2. Metaが返してくる「成果」の種類を見て、シートのCV列に入れるものを決める
 *
 * ── 使い方 ──────────────────────────────────────────────
 * 1. 広告日次シートを開く → 拡張機能 → Apps Script
 * 2. このコードを貼り付けて保存
 * 3. 左の歯車（プロジェクトの設定）→ スクリプト プロパティ に追加
 *      META_TOKEN     … Metaのシステムユーザートークン（ads_read だけでよい）
 *      META_ACCOUNTS  … act_1234567890,act_0987654321  （カンマ区切り・2つ）
 * 4. 上の関数プルダウンで probe を選んで実行
 * 5. 表示 → ログ に出た内容をそのまま貼って渡してください
 *
 * このスクリプトはシートに一切書き込みません。読むだけです。
 */

var META_API = 'https://graph.facebook.com/v21.0';

function probe() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('META_TOKEN');
  var accounts = (props.getProperty('META_ACCOUNTS') || '')
    .split(',').map(function (s) { return s.trim(); }).filter(String);

  if (!token) { Logger.log('META_TOKEN が未設定です'); return; }
  if (!accounts.length) { Logger.log('META_ACCOUNTS が未設定です'); return; }

  // 直近7日を見る。1日だけだと配信していない日に当たって空になる。
  var until = new Date();
  var since = new Date(until.getTime() - 6 * 86400000);

  accounts.forEach(function (acct) {
    Logger.log('==================================================');
    Logger.log('広告アカウント: ' + acct);

    // --- アカウント名
    var me = call(acct + '?fields=name,currency,account_status', token);
    if (me) {
      Logger.log('  名前: ' + me.name + ' / 通貨: ' + me.currency
        + ' / 状態: ' + me.account_status + '（1=有効）');
    }

    // --- キャンペーン一覧
    var camps = call(acct + '/campaigns?fields=id,name,status&limit=100', token);
    Logger.log('--- キャンペーン ---');
    if (camps && camps.data) {
      camps.data.forEach(function (c) {
        Logger.log('  [' + c.status + '] ' + c.name + '  (id=' + c.id + ')');
      });
      if (!camps.data.length) { Logger.log('  （0件）'); }
    }

    // --- 直近7日の日次実績。actions に何が入るかを見るのが本題。
    var q = acct + '/insights'
      + '?level=campaign'
      + '&fields=campaign_name,spend,impressions,clicks,actions'
      + '&time_increment=1'
      + '&time_range=' + encodeURIComponent(JSON.stringify({
          since: ymd(since), until: ymd(until)
        }))
      + '&limit=200';
    var ins = call(q, token);
    Logger.log('--- 直近7日の日次（' + ymd(since) + ' 〜 ' + ymd(until) + '）---');
    if (ins && ins.data && ins.data.length) {
      ins.data.forEach(function (r) {
        Logger.log('  ' + r.date_start + '  ' + r.campaign_name
          + '  消費=' + r.spend + ' IMP=' + r.impressions
          + ' クリック=' + r.clicks);
        // ここが肝。どの action_type をCVとして採るかを決めるために全部出す。
        (r.actions || []).forEach(function (a) {
          Logger.log('      成果: ' + a.action_type + ' = ' + a.value);
        });
        if (!r.actions) { Logger.log('      成果: （なし）'); }
      });
    } else {
      Logger.log('  （データなし。配信していないか、期間内に実績がない）');
    }
  });

  Logger.log('==================================================');
  Logger.log('以上をそのまま貼って渡してください。');
}

/** Graph APIを1回叩く。失敗しても止めず、内容をログに出す。 */
function call(path, token) {
  var url = META_API + '/' + path
    + (path.indexOf('?') >= 0 ? '&' : '?') + 'access_token=' + token;
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  var body = res.getContentText();
  if (res.getResponseCode() !== 200) {
    Logger.log('  [エラー ' + res.getResponseCode() + '] ' + body.slice(0, 400));
    return null;
  }
  try {
    return JSON.parse(body);
  } catch (e) {
    Logger.log('  [JSONが読めません] ' + body.slice(0, 200));
    return null;
  }
}

/** Date → YYYY-MM-DD（Metaのtime_rangeはこの形式） */
function ymd(d) {
  return Utilities.formatDate(d, 'Asia/Tokyo', 'yyyy-MM-dd');
}
