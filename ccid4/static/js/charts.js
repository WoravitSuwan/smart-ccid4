// แดชบอร์ด: tooltip ของกราฟ SVG (ชี้หรือแตะแท่งเพื่อดูค่า) · ข้อความเป็น text token ไม่ใช้สีข้อมูล
(() => {
  const tip = document.createElement('div');
  tip.className = 'chart-tip';
  tip.setAttribute('role', 'status');
  tip.hidden = true;
  document.body.appendChild(tip);
  const show = (el, x, y) => {
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(window.innerWidth - r.width - 8, Math.max(8, x - r.width / 2))}px`;
    tip.style.top = `${Math.max(8, y - r.height - 12)}px`;
  };
  document.addEventListener('pointermove', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    document.querySelectorAll('.chart-hit.on').forEach((h) => { if (h !== el) h.classList.remove('on'); });
    if (!el) { tip.hidden = true; return; }
    el.classList.add('on');
    show(el, e.clientX, e.clientY);
  });
  document.addEventListener('focusin', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (!el) return;
    const r = el.getBoundingClientRect();
    show(el, r.left + r.width / 2, r.top);
  });
  document.addEventListener('focusout', () => { tip.hidden = true; });
})();
