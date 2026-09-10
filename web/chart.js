/* 被害推移グラフ。素の canvas で描く（SPEC 7.6.9）。
   プレイ中は折れ点の位置を描かない。カーブが跳ねたようにしか見えないのが正しい。
   講評では markers に kind:'world' として渡ってくる（答えを開ける場所だから）。 */

(function (global) {
  'use strict';

  function draw(canvas, series, options) {
    var opts = options || {};
    var markers = opts.markers || [];
    var ctx = canvas.getContext('2d');
    var dpr = global.devicePixelRatio || 1;
    var cssW = canvas.clientWidth;

    // 表示上の高さは dataset に退避してから使う。
    // canvas.height への代入は height 属性そのものを書き換えるので、
    // 次の描画でその属性を読み直すと cssH × dpr が毎回積算される
    // （dpr 2 なら 86 → 172 → 344 → … と倍々に増え、Chrome の上限を超えて
    //  確保に失敗し、壊れた画像として表示される）。dpr 1 では表面化しない。
    var cssH = parseInt(canvas.dataset.h || canvas.getAttribute('height'), 10) || 120;
    canvas.dataset.h = cssH;
    canvas.style.height = cssH + 'px';

    // まだレイアウトされていない（幅0）なら、次のフレームで描き直す。
    // 諦めて固定幅で描くと、実寸と合わずに見えなくなる
    if (!cssW) {
      if (!opts.__retry) {
        global.requestAnimationFrame(function () {
          opts.__retry = true;
          draw(canvas, series, opts);
        });
      }
      return;
    }

    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var padL = 4, padR = 4, padT = 8;
    var padB = markers.length ? 14 + Math.min(markers.length - 1, 5) * 11 : 10;
    var w = cssW - padL - padR;
    var h = cssH - padT - padB;

    ctx.strokeStyle = '#2b323c';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + h + 0.5);
    ctx.lineTo(padL + w, padT + h + 0.5);
    ctx.stroke();

    if (!series || series.length < 2) { return; }

    var maxY = 0;
    for (var i = 0; i < series.length; i++) {
      if (series[i] > maxY) { maxY = series[i]; }
    }
    if (maxY <= 0) { maxY = 1; }
    var maxX = series.length - 1;

    function px(i) { return padL + (i / maxX) * w; }
    function py(v) { return padT + h - (v / maxY) * h; }

    // 面
    ctx.beginPath();
    ctx.moveTo(px(0), padT + h);
    for (var j = 0; j < series.length; j++) { ctx.lineTo(px(j), py(series[j])); }
    ctx.lineTo(px(maxX), padT + h);
    ctx.closePath();
    ctx.fillStyle = 'rgba(122, 162, 247, .13)';
    ctx.fill();

    // 線
    ctx.beginPath();
    for (var k = 0; k < series.length; k++) {
      if (k === 0) { ctx.moveTo(px(k), py(series[k])); }
      else { ctx.lineTo(px(k), py(series[k])); }
    }
    ctx.strokeStyle = opts.color || '#7aa2f7';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    // 注釈（判定した時点・封じ込めた時点）
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'center';
    var placed = [];   // 既に置いたラベルの範囲。重ねると読めなくなる
    for (var m = 0; m < markers.length; m++) {
      var mk = markers[m];
      if (mk.minute == null || mk.minute < 0) { continue; }
      // 終了直前の出来事は右端に寄せる。落とすと封じ込めの印が消える
      var x = px(Math.min(mk.minute, maxX));
      ctx.beginPath();
      ctx.setLineDash([2, 3]);
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + h);
      var col = mk.kind === 'world' ? '#f7768e' : '#e0af68';
      ctx.strokeStyle = col;
      ctx.lineWidth = mk.kind === 'world' ? 1.4 : 1;
      ctx.stroke();
      ctx.setLineDash([]);

      var label = mk.label;
      var tw = ctx.measureText(label).width;
      var lx = Math.min(Math.max(x, tw / 2 + 2), padL + w - tw / 2 - 2);
      // 重なる相手がいる段を避けて、下から順に段をずらす
      var row = 0;
      while (row < 6 && placed.some(function (p) {
        return p.row === row && Math.abs(p.x - lx) < (p.w + tw) / 2 + 6;
      })) { row++; }
      placed.push({ x: lx, w: tw, row: row });

      ctx.fillStyle = col;
      ctx.fillText(label, lx, cssH - 3 - row * 11);
    }
  }

  global.DamageChart = { draw: draw };
})(window);
