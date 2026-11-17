// ส่วนกลางของกราฟ Cytoscape: สีจาก design token (ไม่มีค่าสีตรง ๆ) · ส่งออก PNG · ข้อความปลอดภัย
window.CCID4Graph = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const palette = () => ({
    bg: css('--surface'), text: css('--text'), muted: css('--text-muted'), border: css('--border'), primary: css('--primary'),
    accent: css('--accent'), danger: css('--danger'), warning: css('--warning'), success: css('--success'), surface2: css('--surface-2'), ai: css('--ai'),
  });
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const font = getComputedStyle(document.body).fontFamily;
  function png(cy, name) {
    const p = palette();
    const data = cy.png({ full: true, scale: 2, bg: p.bg });
    const a = document.createElement('a');
    a.href = data; a.download = `${name}.png`; document.body.appendChild(a); a.click(); a.remove();
  }
  return { palette, esc, font, png };
})();
