/* SinoBee: lets the app take a picture of what a page shows right now (the share card's thumbnail).
   Loaded into pages opened through /view (an opaque-origin sandbox). It answers only its parent, only with an image
   of the page's main figure (the biggest canvas / svg / img / video on screen), and changes nothing on the page. */
(function () {
  if (window.__sbCapture) return; window.__sbCapture = true;
  function figure() {
    var best = null, area = 0, W = innerWidth, H = innerHeight;
    document.querySelectorAll('canvas, svg, img, video').forEach(function (el) {
      if (el.tagName.toLowerCase() === 'svg' && el.parentElement && el.parentElement.closest('svg')) return;
      var r = el.getBoundingClientRect();
      var w = Math.min(r.right, W) - Math.max(r.left, 0), h = Math.min(r.bottom, H) - Math.max(r.top, 0);
      if (w > 0 && h > 0 && w * h > area) { area = w * h; best = el; }
    });
    return area >= 40000 ? best : null;
  }
  function flat(g, w, h) {
    try {
      var d = g.getImageData(0, 0, w, h).data, lo = 765, hi = 0, step = Math.max(4, (d.length / 4096) | 0) * 4;
      for (var i = 0; i < d.length; i += step) { var v = d[i] + d[i + 1] + d[i + 2]; if (v < lo) lo = v; if (v > hi) hi = v; }
      return hi - lo < 24;
    } catch (e) { return false; }
  }
  function svgImage(el, w, h) {
    var copy = el.cloneNode(true), css = '';
    try { document.querySelectorAll('style').forEach(function (s) { css += s.textContent + '\n'; }); } catch (e) {}
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('width', w); copy.setAttribute('height', h);
    if (css) { var st = document.createElementNS('http://www.w3.org/2000/svg', 'style'); st.textContent = css; copy.insertBefore(st, copy.firstChild); }
    var bg = getComputedStyle(document.body).backgroundColor;
    var url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], {type: 'image/svg+xml'}));
    return new Promise(function (res) { var im = new Image(); im.onload = function () { res([im, bg, url]); };
      im.onerror = function () { URL.revokeObjectURL(url); res(null); }; im.src = url; });
  }
  async function capture() {
    var el = figure(); if (!el) return null;
    var r = el.getBoundingClientRect(), s = Math.min(1, 1280 / r.width);
    var c = document.createElement('canvas'); c.width = Math.round(r.width * s); c.height = Math.round(r.height * s);
    var g = c.getContext('2d'), tag = el.tagName.toLowerCase();
    if (tag === 'canvas') {
      // read a WebGL canvas right after the page drew it: a callback asked for now runs after the page's own one
      // in the next frame, while the drawing is still there (no preserveDrawingBuffer needed)
      for (var i = 0; i < 4; i++) {
        await new Promise(function (ok) { requestAnimationFrame(function () { try { g.clearRect(0, 0, c.width, c.height); g.drawImage(el, 0, 0, c.width, c.height); } catch (e) {} ok(); }); });
        if (!flat(g, c.width, c.height)) break;
      }
    } else if (tag === 'svg') {
      var got = await svgImage(el, r.width, r.height); if (!got) return null;
      g.fillStyle = got[1] && got[1] !== 'rgba(0, 0, 0, 0)' ? got[1] : '#fff'; g.fillRect(0, 0, c.width, c.height);
      g.drawImage(got[0], 0, 0, c.width, c.height); URL.revokeObjectURL(got[2]);
    } else {
      g.drawImage(el, 0, 0, c.width, c.height);
    }
    if (flat(g, c.width, c.height)) return null;
    return c.toDataURL('image/jpeg', 0.85);      // throws for a tainted canvas: the app then takes its own picture
  }
  addEventListener('message', function (e) {
    var d = e.data; if (!d || d.sinobee !== 'capture' || e.source !== parent) return;
    capture().then(function (img) { parent.postMessage({sinobee: 'capture', id: d.id, image: img}, '*'); },
                   function () { parent.postMessage({sinobee: 'capture', id: d.id, image: null}, '*'); });
  });
})();
