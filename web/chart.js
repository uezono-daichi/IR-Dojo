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

    // 目盛りを出すのは講評だけ（axis: true）。プレイ中の帯は狭く、
    // 数字を添えると「この先どう伸びるか」を読ませる図になってしまう
    var axis = !!opts.axis;
    var padL = axis ? 56 : 4, padR = axis ? 10 : 4;
    var padT = axis ? 22 : 8;
    var padB = axis ? 30 : 10;
    var w = cssW - padL - padR;
    var h = cssH - padT - padB;

    ctx.strokeStyle = '#2b323c';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + h + 0.5);
    ctx.lineTo(padL + w, padT + h + 0.5);
    ctx.stroke();

    if (!series || series.length < 2) { return; }

    // 復旧地平の延長（講評のみ）。実線の終端から破線で続く。
    // x 軸は実線と破線を合わせた全長で割る — 別々に割ると傾きが嘘になる
    var proj = opts.projection || [];

    var maxY = 0;
    for (var i = 0; i < series.length; i++) {
      if (series[i] > maxY) { maxY = series[i]; }
    }
    for (var pi = 0; pi < proj.length; pi++) {
      if (proj[pi] > maxY) { maxY = proj[pi]; }
    }
    if (maxY <= 0) { maxY = 1; }
    var maxX = series.length - 1 + proj.length;
    if (maxX <= 0) { maxX = 1; }

    function px(i) { return padL + (i / maxX) * w; }
    function py(v) { return padT + h - (v / maxY) * h; }

    // 面
    ctx.beginPath();
    ctx.moveTo(px(0), padT + h);
    for (var j = 0; j < series.length; j++) { ctx.lineTo(px(j), py(series[j])); }
    // 面を閉じるのは実線の終端。maxX まで引くと、破線の下まで塗ってしまう
    ctx.lineTo(px(series.length - 1), padT + h);
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

    // 復旧地平の延長。破線で、面は塗らない。
    // 「まだ起きていないこと」を実線と同じ強さで描くと、
    // 積み上がった被害と見分けが付かなくなる
    if (proj.length) {
      ctx.beginPath();
      ctx.setLineDash([4, 3]);
      ctx.moveTo(px(series.length - 1), py(series[series.length - 1]));
      for (var q = 0; q < proj.length; q++) {
        ctx.lineTo(px(series.length + q), py(proj[q]));
      }
      ctx.strokeStyle = opts.projectionColor || '#f7768e';
      ctx.lineWidth = 1.4;
      ctx.stroke();
      ctx.setLineDash([]);

      // 手を止めた位置に細い区切りを入れる。
      // ここから先は「起きること」であって「起きたこと」ではない
      ctx.beginPath();
      ctx.moveTo(px(series.length - 1), padT);
      ctx.lineTo(px(series.length - 1), padT + h);
      ctx.strokeStyle = '#2b323c';
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // 目盛り。縦軸は被害額、横軸は分。
    // 「どこまで伸びたのか」が数字で読めないと、傾きしか分からない
    if (axis) {
      ctx.font = '10px ui-monospace, monospace';
      ctx.fillStyle = '#6b7280';
      ctx.textAlign = 'right';
      ctx.fillText(fmt(maxY), padL - 6, padT + 4);
      ctx.fillText('0', padL - 6, padT + h + 4);
      ctx.textAlign = 'left';
      ctx.fillText('被害額', padL, padT - 9);

      // 上端の薄い基準線。目盛りの数字がどの高さを指すかを示す
      ctx.beginPath();
      ctx.moveTo(padL, padT + 0.5);
      ctx.lineTo(padL + w, padT + 0.5);
      ctx.strokeStyle = '#22272f';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = '#6b7280';
      ctx.textAlign = 'left';
      ctx.fillText('0分', padL, padT + h + 15);
      if (proj.length) {
        ctx.textAlign = 'center';
        var bx = px(series.length - 1);
        ctx.fillText(series.length + '分', bx, padT + h + 15);
        ctx.fillText('手を止めた時点', bx, padT + h + 26);
        ctx.textAlign = 'right';
        ctx.fillText('＋' + Math.round(proj.length / 60) + '時間',
                     padL + w, padT + h + 15);
      }
    }

    // 注釈（判定した時点・封じ込めた時点）。
    // **図に描くのは番号だけ。名前は凡例に置く。**
    // 名前を図に直接置いていた頃は、同時刻の線の上にラベルが3段に折り重なり、
    // どの線がどれかを x 座標の近さで推測するしかなかった。
    // 線を実線にしてあるのは、破線を1種類（復旧までの見込み）に限るため
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    var placed = [];   // 同じ位置に立つ番号は縦にずらす
    for (var m = 0; m < markers.length; m++) {
      var mk = markers[m];
      if (mk.minute == null || mk.minute < 0) { continue; }
      // 終了直前の出来事は右端に寄せる。落とすと封じ込めの印が消える
      var x = px(Math.min(mk.minute, maxX));
      var col = mk.kind === 'world' ? '#f7768e' : '#e0af68';
      ctx.beginPath();
      ctx.moveTo(x + 0.5, padT);
      ctx.lineTo(x + 0.5, padT + h);
      ctx.strokeStyle = col;
      ctx.lineWidth = mk.kind === 'world' ? 1.4 : 1;
      ctx.stroke();

      var row = 0;
      while (row < 8 && placed.some(function (p) {
        return p.row === row && Math.abs(p.x - x) < 15;
      })) { row++; }
      placed.push({ x: x, row: row });

      var by = padT + 8 + row * 15;
      var n = String(mk.index || (m + 1));
      ctx.beginPath();
      ctx.arc(x, by, 7, 0, Math.PI * 2);
      ctx.fillStyle = '#12151a';
      ctx.fill();
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = col;
      ctx.fillText(n, x, by + 0.5);
    }
    ctx.textBaseline = 'alphabetic';
  }

  function fmt(v) { return Math.round(v).toLocaleString('ja-JP'); }

  global.DamageChart = { draw: draw };
})(window);
