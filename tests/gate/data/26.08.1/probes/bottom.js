(() => {
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  // The four answer buttons (Again/Hard/Good/Easy on a new card: the last
  // three). "button" = a stable mid button for the fill sample; buttons are
  // min-width 60px so their center is fill, not glyph.
  const buttons = [...document.querySelectorAll("button")].map((b) => ({
    label: (b.textContent || "").trim(),
    rect: rect(b),
    bg: getComputedStyle(b).backgroundColor,
  }));
  return {
    dpr: window.devicePixelRatio,
    buttons,
    button: buttons.length
      ? buttons[Math.min(1, buttons.length - 1)].rect
      : null,
    bodyBg: getComputedStyle(document.body).backgroundColor,
  };
})();
