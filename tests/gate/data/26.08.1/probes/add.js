(() => {
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const out = {
    dpr: window.devicePixelRatio,
    url: location.href.slice(0, 80),
    ready: document.readyState,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    viewport: {
      w: document.documentElement.clientWidth,
      h: document.documentElement.clientHeight,
    },
  };
  // The editing surface: an editable inside the first .editor-field (the
  // sveltekit editor renders fields as divs, not iframes, on 26.08.1).
  // Focus it so the focus ring renders for the capture.
  const editable =
    document.querySelector(".editor-field [contenteditable='true']") ||
    document.querySelector("[contenteditable='true']") ||
    document.querySelector(".editor-field");
  if (editable) {
    const before = getComputedStyle(editable);
    out.field0 = rect(editable);
    out.field0Style = {
      bg: before.backgroundColor,
      border: before.borderColor,
      outline: before.outlineColor,
    };
    editable.focus();
    const after = getComputedStyle(editable);
    out.field0Focused = {
      border: after.borderColor,
      outline: after.outlineColor,
    };
  } else {
    out.field0 = null;
  }
  return out;
})();
