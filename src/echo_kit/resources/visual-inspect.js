async ({ selector, limit, timeoutMs }) => {
  let timer;
  try {
    await Promise.race([document.fonts.ready, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Font readiness timed out')), timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
  const properties = ['display', 'position', 'box-sizing', 'width', 'height', 'max-width', 'min-width',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'padding-top', 'padding-right',
    'padding-bottom', 'padding-left', 'gap', 'align-items', 'justify-content', 'font-family', 'font-size',
    'font-weight', 'line-height', 'letter-spacing', 'color', 'background-color', 'border-top-width',
    'border-top-color', 'border-radius', 'box-shadow', 'opacity', 'overflow-x'];
  const box = element => {
    const r = element.getBoundingClientRect();
    return Object.fromEntries(['x', 'y', 'width', 'height'].map(k => [k, Math.round(r[k] * 100) / 100]));
  };
  const visible = element => {
    const r = element.getBoundingClientRect(), s = getComputedStyle(element);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const styles = (element, pseudo) => {
    const computed = getComputedStyle(element, pseudo);
    return Object.fromEntries(properties.map(k => [k, computed.getPropertyValue(k).slice(0, 256)]));
  };
  const safeURL = value => {
    try {
      const u = new URL(value, location.href);
      if (u.protocol === 'data:') return 'data:' + u.pathname.split(/[;,]/, 1)[0] + ';[inline]';
      if (u.protocol === 'blob:') return 'blob:[opaque]';
      return (u.origin + u.pathname).slice(0, 512);
    } catch { return ''; }
  };
  const nodes = [...document.querySelectorAll(selector)];
  const selected = nodes.slice(0, limit);
  const pictures = [...document.images].filter(visible).slice(0, 50);
  return {
    matched: nodes.length, truncated: nodes.length > limit,
    elements: selected.map(element => ({ tag: element.tagName.toLowerCase(), box: box(element),
      document_box: { ...box(element), x: Math.round((box(element).x + scrollX) * 100) / 100,
        y: Math.round((box(element).y + scrollY) * 100) / 100 },
      visible: visible(element), styles: styles(element),
      pseudo: Object.fromEntries(['::before', '::after'].map(pseudo => [pseudo, {
        present: !['none', 'normal', '""'].includes(getComputedStyle(element, pseudo).content),
        styles: styles(element, pseudo)
      }])) })),
    profile: { viewport: { width: innerWidth, height: innerHeight }, device_scale_factor: devicePixelRatio,
      color_scheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
      reduced_motion: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'reduce' : 'no-preference',
      locale: navigator.language, timezone_id: Intl.DateTimeFormat().resolvedOptions().timeZone },
    scroll: { x: scrollX, y: scrollY },
    diagnostics: { failed_fonts: [...document.fonts].filter(f => f.status === 'error').length,
      pending_fonts: [...document.fonts].filter(f => f.status !== 'loaded' && f.status !== 'unloaded').length,
      pending_visible_images: pictures.filter(i => !i.complete).length,
      failed_visible_images: pictures.filter(i => i.complete && !i.naturalWidth).length,
      failed_resources: performance.getEntriesByType('resource').filter(r => r.responseStatus >= 400).length,
      horizontal_overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      images: pictures.map(i => ({ source: safeURL(i.currentSrc || i.src), width: i.naturalWidth,
        height: i.naturalHeight, loaded: i.complete && i.naturalWidth > 0 })) }
  };
}
