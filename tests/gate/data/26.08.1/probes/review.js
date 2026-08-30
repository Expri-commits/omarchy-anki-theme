(() => {
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const opaque = (bg) => bg && !/^rgba\(\s*0,\s*0,\s*0,\s*0\s*\)$/.test(bg);
  // The reviewer injects the card into #qa (aqt reviewer.py revHtml +
  // js/reviewer.js); the first opaque child of #qa is the card surface —
  // the notetype's own CSS layer, untouched by app theming (ticket 06).
  const qa = document.getElementById("qa");
  let card = null;
  let cardBg = null;
  for (let node = qa?.firstElementChild; node; node = node.nextElementSibling) {
    const bg = getComputedStyle(node).backgroundColor;
    if (opaque(bg)) {
      card = node;
      cardBg = bg;
      break;
    }
  }
  return {
    dpr: window.devicePixelRatio,
    night: document.documentElement.className,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    qa: rect(qa),
    qaHtml: qa ? qa.innerHTML.slice(0, 220) : null,
    card: rect(card),
    cardBg,
    viewport: {
      w: document.documentElement.clientWidth,
      h: document.documentElement.clientHeight,
    },
  };
})();
