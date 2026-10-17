/* SinoBee math helpers (MIT): repair LaTeX that lost its backslashes on the way through JSON, and typeset the
   formulas in a page with KaTeX — $…$, $$…$$, \(…\), \[…\], and formula boxes written without delimiters. */
(function (g) {
  var GREEK = 'alpha|beta|gamma|Gamma|delta|Delta|epsilon|varepsilon|zeta|eta|theta|Theta|vartheta|iota|kappa|lambda|Lambda|mu|nu|xi|Xi|pi|Pi|rho|sigma|Sigma|tau|upsilon|phi|Phi|varphi|chi|psi|Psi|omega|Omega';
  var OPS = 'approx|cdot|cdots|ldots|times|div|pm|mp|leq|geq|neq|equiv|propto|simeq|infty|partial|nabla|circ|rightarrow|leftarrow|Rightarrow|quad|qquad|sum|prod|int|oint|lim|ln|log|exp|sin|cos|tan|max|min';
  var ARG = 'frac|dfrac|tfrac|sqrt|dot|ddot|hat|bar|vec|tilde|overline|underline|text|mathrm|mathbf|mathit|mathcal|operatorname|left|right';
  var RE_ARG = new RegExp('(^|[^\\\\a-zA-Z])(' + ARG + ')(?=\\s*[{(\\[|.])', 'g');
  var RE_WORD = new RegExp('(^|[^\\\\a-zA-Z])(' + GREEK + '|' + OPS + ')(?![a-zA-Z])', 'g');
  function fix(t) {
    t = String(t);
    var bare = !/\\[a-zA-Z]/.test(t);           // no command written properly anywhere: the backslashes were lost
    t = t.replace(/\f/g, '\\f').replace(/\x08/g, '\\b').replace(/\x0b/g, '\\v').replace(/\x07/g, '\\a')
      .replace(/\t(?=(?:imes|heta|ext|extbf|au|ilde|riangle|op|o)(?![a-z]))/g, '\\t')
      .replace(/\r(?=(?:ho|ightarrow|ight|angle)(?![a-z]))/g, '\\r');
    if (bare) t = t.replace(RE_ARG, '$1\\$2').replace(RE_WORD, '$1\\$2');
    return t;
  }
  var MATH = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<!\s)\$(?!\d)/g;
  var BOX = /(^|[-_\s])(math|maths|formula|formulas|equation|equations|eq|latex|tex)([-_\s]|$)/i;
  var SKIP = /^(SCRIPT|STYLE|TEXTAREA|CODE|NOSCRIPT|KBD|SAMP|SVG|MATH|INPUT|SELECT|OPTION)$/;
  function tex(src, display) {
    try { return g.katex.renderToString(fix(src), {displayMode: display, throwOnError: false, strict: 'ignore', trust: false}); }
    catch (e) { return null; }
  }
  function render(root) {
    if (!g.katex || !root) return 0;
    var n = 0;
    // formula boxes written without delimiters: <div class="formula">v_e = \sqrt{…}</div>
    root.querySelectorAll('[class]').forEach(function (el) {
      if (!BOX.test(el.getAttribute('class') || '') || el.children.length || el.closest('.katex')) return;
      var t = el.textContent.trim();
      if (!t || /\$|\\\(|\\\[/.test(t) || !/[_^{}=]|\\[a-zA-Z]/.test(t) || t.length > 600) return;
      var h = tex(t, el.tagName !== 'SPAN'); if (h) { el.innerHTML = h; n++; }
    });
    var walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: function (node) {
      for (var p = node.parentNode; p && p !== root.parentNode; p = p.parentNode)
        if (p.nodeType === 1 && (SKIP.test(p.tagName.toUpperCase()) || (p.classList && p.classList.contains('katex')))) return NodeFilter.FILTER_REJECT;
      return /\$|\\\(|\\\[/.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    }});
    var nodes = []; while (walk.nextNode()) nodes.push(walk.currentNode);
    nodes.forEach(function (node) {
      var s = node.nodeValue, out = document.createDocumentFragment(), last = 0, m, hit = false;
      MATH.lastIndex = 0;
      while ((m = MATH.exec(s))) {
        var display = m[1] != null || m[2] != null, h = tex(m[1] || m[2] || m[3] || m[4], display);
        if (!h) continue;
        out.appendChild(document.createTextNode(s.slice(last, m.index)));
        var span = document.createElement(display ? 'div' : 'span'); span.innerHTML = h; out.appendChild(span);
        last = MATH.lastIndex; hit = true; n++;
      }
      if (!hit) return;
      out.appendChild(document.createTextNode(s.slice(last)));
      node.parentNode.replaceChild(out, node);
    });
    return n;
  }
  g.sbMath = {fix: fix, render: render};
})(window);
