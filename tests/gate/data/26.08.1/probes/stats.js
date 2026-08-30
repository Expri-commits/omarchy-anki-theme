(() => {
  const rect = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { x: b.x, y: b.y, w: b.width, h: b.height };
  };
  const intro = document.querySelector("#intro canvas.flot-base");
  return {
    dpr: window.devicePixelRatio,
    title: document.title,
    body_bg: getComputedStyle(document.body).backgroundColor,
    intro_canvas: rect(intro),
    h1: document.querySelector("h1")?.textContent || null,
  };
})();
