(() => {
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const row =
    document.querySelector("tr.deck.current") ||
    document.querySelector("tr.deck");
  const link = row?.querySelector("a.deck");
  // Clean fill inside the current row: past the deck-name link (the last td
  // holds the gears icon and the count tds hold digits — all glyphs).
  let rowfill = null;
  if (row && link) {
    const rr = row.getBoundingClientRect();
    const lr = link.getBoundingClientRect();
    rowfill = {
      x: lr.x + lr.width + 10,
      y: rr.y + rr.height / 2 - 1,
      w: 10,
      h: 2,
    };
  }
  return {
    dpr: window.devicePixelRatio,
    night: document.documentElement.className,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    table: rect(document.querySelector("table")),
    row: rect(row),
    rowfill: rowfill,
    link: rect(link),
    linkColor: link ? getComputedStyle(link).color : null,
    rowCount: document.querySelectorAll("tr.deck").length,
  };
})();
