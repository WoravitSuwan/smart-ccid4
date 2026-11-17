// เครือข่ายคดี (B4): คดี ↔ ตัวระบุที่พบซ้ำ · คดีที่ไม่มีสิทธิ์เห็นแสดงเฉพาะเลขคดีและพนักงานสอบสวน
(() => {
  const box = document.querySelector('[data-net]');
  if (!box || !window.cytoscape) return;
  const G = window.CCID4Graph;
  const panel = document.querySelector('[data-net-panel]');
  const other = document.querySelector('[data-merge-other]');
  let cy = null;
  fetch(box.dataset.url, { headers: { Accept: 'application/json' } }).then((r) => r.json()).then((data) => {
    const count = document.querySelector('[data-net-count]');
    if (count) count.textContent = `${data.identifiers} ตัวระบุ · ${data.cases} คดี`;
    if (!data.nodes.length) { box.hidden = true; document.querySelector('[data-net-empty]').hidden = false; return; }
    const p = G.palette();
    cy = window.cytoscape({
      container: box, elements: [...data.nodes, ...data.edges], wheelSensitivity: 0.25,
      layout: { name: 'cose', animate: false, padding: 40, nodeRepulsion: 14000, idealEdgeLength: 120, nodeOverlap: 20 },
      style: [
        { selector: 'node', style: { label: 'data(label)', 'font-family': G.font, 'font-size': 11, color: p.text, 'text-valign': 'bottom', 'text-margin-y': 5,
          'text-wrap': 'wrap', 'text-max-width': 140 } },
        { selector: 'node[type = "case"]', style: { shape: 'round-rectangle', width: 44, height: 30, 'background-color': p.primary, 'border-width': 2, 'border-color': p.border } },
        { selector: 'node[type = "case"][!visible]', style: { 'background-color': p.surface2, 'border-color': p.muted, 'border-style': 'dashed' } },
        { selector: 'node[?focus]', style: { 'border-color': p.accent, 'border-width': 5 } },
        { selector: 'node[type = "identifier"]', style: { shape: 'ellipse', width: 26, height: 26, 'background-color': p.warning } },
        { selector: 'node[kind = "crypto"]', style: { shape: 'diamond' } },
        { selector: 'node[kind = "bank_account"]', style: { shape: 'hexagon' } },
        { selector: 'node:selected', style: { 'border-color': p.text, 'border-width': 4 } },
        { selector: 'edge', style: { width: 1.6, 'line-color': p.muted, 'curve-style': 'bezier', 'font-family': G.font, 'font-size': 9, color: p.text,
          'text-rotation': 'autorotate', 'text-background-color': p.bg, 'text-background-opacity': 0.9 } },
        { selector: 'edge.show', style: { label: 'data(label)', width: 3, 'line-color': p.accent } },
      ],
    });
    cy.on('tap', 'node', (e) => {
      const d = e.target.data();
      cy.edges().removeClass('show');
      e.target.connectedEdges().addClass('show');
      if (d.type === 'case') {
        panel.innerHTML = `<b>คดี ${G.esc(d.label)}</b><span class="text-sm">พนักงานสอบสวน ${G.esc(d.owner)}</span>
          ${d.visible ? `<a class="btn btn-secondary btn-sm self-start" href="${G.esc(d.url)}">เปิดหน้าคดี</a><a class="btn btn-secondary btn-sm self-start" href="${G.esc(d.money_url)}">เส้นทางเงิน</a>`
            : '<span class="chip chip-muted text-xs self-start">ไม่มีสิทธิ์ดูเนื้อหาคดีนี้ ติดต่อพนักงานสอบสวนเจ้าของ</span>'}
          <span class="text-xs text-muted">เชื่อมกับตัวระบุ ${e.target.degree()} รายการ</span>`;
        if (other && !d.focus) {
          const id = d.id.slice(2);
          if (![...other.options].some((o) => o.value === id)) other.add(new Option(`${d.label} · ${d.owner}`, id));
          other.value = id;
        }
      } else {
        const cases = e.target.neighborhood('node').map((n) => `<li class="text-sm">${G.esc(n.data('label'))} · ${G.esc(n.data('owner'))}</li>`).join('');
        panel.innerHTML = `<b>${G.esc(d.kind_label)}</b><code class="text-sm">${G.esc(d.label)}</code><span class="link-badge self-start">พบใน ${d.cases} คดี</span><ul>${cases}</ul>`;
      }
    });
    cy.on('dbltap', 'node[type = "case"][?visible]', (e) => { window.location.href = e.target.data('url'); });
  });
  const btn = document.querySelector('[data-net-png]');
  if (btn) btn.addEventListener('click', () => { if (cy) G.png(cy, 'เครือข่ายคดี'); });
})();
