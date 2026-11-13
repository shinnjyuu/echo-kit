async ({ target, timeoutMs }) => {
  const deadline = performance.now() + timeoutMs;
  const bounded = async (promise) => {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Observation readiness timed out')),
          Math.max(1, deadline - performance.now()));
      })]);
    } finally { clearTimeout(timer); }
  };
  await bounded(document.fonts.ready);
  const root = target ? document.querySelector(target) : document.body;
  const box = element => {
    const rect = element.getBoundingClientRect();
    return Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, Math.round(rect[key] * 100) / 100]));
  };
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight
      && rect.right > 0 && rect.left < innerWidth && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const images = [...root.querySelectorAll('img')];
  if (root.matches('img')) images.unshift(root);
  const selected = images.filter(visible).slice(0, 50);
  await bounded(Promise.all(selected.filter(image => !image.complete).map(image =>
    new Promise(resolve => {
      image.addEventListener('load', resolve, { once: true });
      image.addEventListener('error', resolve, { once: true });
    }))));
  let previous = '', stable = 0;
  while (stable < 3) {
    await bounded(new Promise(resolve => requestAnimationFrame(resolve)));
    const next = JSON.stringify(target ? box(root) : { width: innerWidth, height: innerHeight });
    stable = next === previous ? stable + 1 : 0;
    previous = next;
  }
  const elements = [root, ...root.querySelectorAll('h1,h2,h3,p,button,label')].filter(visible).slice(0, 12);
  return {
    target: box(root),
    horizontal_overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    typography: elements.map(element => ({ tag: element.tagName.toLowerCase(), box: box(element),
      font_size: getComputedStyle(element).fontSize, font_family: getComputedStyle(element).fontFamily })),
    failed_visible_images: selected.filter(image => !image.naturalWidth).length,
    failed_fonts: [...document.fonts].filter(font => font.status === 'error').length,
  };
}
