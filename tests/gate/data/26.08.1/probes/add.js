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
  // The sveltekit editor renders each field's editing surface inside a
  // same-origin iframe (Anki's editor legacy shape kept); walk in, report
  // each surface's page-relative rect (iframe rect + inner rect) and focus
  // the first one so its focus ring renders.
  out.frames = [...document.querySelectorAll("iframe")]
    .slice(0, 8)
    .map((frame) => {
      const fr = rect(frame);
      let inner = null;
      let focused = false;
      try {
        const doc = frame.contentDocument;
        if (doc?.body) {
          const editable = doc.querySelector(
            "[contenteditable='true'], .editable",
          );
          const er = editable ? rect(editable) : rect(doc.body);
          inner = {
            bodyBg: getComputedStyle(doc.body).backgroundColor,
            editable: er,
            editableBg: editable
              ? getComputedStyle(editable).backgroundColor
              : null,
            editableBorder: editable
              ? getComputedStyle(editable).borderColor
              : null,
          };
          if (editable) {
            editable.focus();
            focused = true;
          }
        }
      } catch (_e) {
        inner = "cross-origin";
      }
      return { rect: fr, src: (frame.src || "").slice(0, 70), inner, focused };
    });
  out.fieldish = [...document.querySelectorAll('[class*="field" i], textarea')]
    .slice(0, 8)
    .map((el) => ({
      tag: el.tagName,
      cls: String(el.className || "").slice(0, 60),
      rect: rect(el),
      bg: getComputedStyle(el).backgroundColor,
    }));
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
