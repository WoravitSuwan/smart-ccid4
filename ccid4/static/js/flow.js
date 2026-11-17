// แผนภาพเส้นทางเงิน (B3): ผู้เสียหาย → บัญชีชั้นที่ 1 → ชั้นที่ 2 … คลิกโหนดดูธุรกรรม
(() => {
  const box = document.querySelector('[data-flow]');
  if (!box || !window.cytoscape) return;
  const panel = document.querySelector('[data-flow-panel]');
  const G = window.CCID4Graph;
  let cy = null;
  async function draw() {
    if (cy) { cy.resize(); cy.fit(undefined, 30); return; }
    const r = await fetch(box.dataset.url, { headers: { Accept: 'application/json' } });
    if (!r.ok) return;
    const data = await r.json();
    const p = G.palette();
    const accounts = data.elements.filter((e) => e.data.kind === 'account' || e.data.kind === 'victim_account');
    if (!accounts.length) { box.innerHTML = '<p class="empty-state text-sm">ยังไม่มีธุรกรรม</p>'; return; }
    cy = window.cytoscape({
      container: box, elements: data.elements, wheelSensitivity: 0.25,
      layout: { name: 'breadthfirst', directed: true, roots: '#victim', spacingFactor: 1.7, padding: 40 },
      style: [
        { selector: 'node', style: { label: 'data(label)', 'text-wrap': 'wrap', 'text-max-width': 150, 'font-family': G.font, 'font-size': 11,
          color: p.text, 'text-valign': 'bottom', 'text-margin-y': 6, width: 38, height: 38, 'background-color': p.primary, 'border-width': 2, 'border-color': p.border } },
        { selector: 'node[kind = "victim"]', style: { shape: 'round-rectangle', 'background-color': p.accent, width: 46, height: 46, 'text-valign': 'top', 'text-margin-y': -8 } },
        { selector: 'node[kind = "victim_account"]', style: { 'background-color': p.accent } },
        { selector: 'node[others > 0]', style: { 'border-color': p.warning, 'border-width': 5 } },
        { selector: 'node[status = "frozen"]', style: { 'background-color': p.success } },
        { selector: 'node:selected', style: { 'border-color': p.text, 'border-width': 4 } },
        { selector: 'edge', style: { width: 'mapData(amount, 0, 200000, 1.5, 9)', 'line-color': p.muted, 'target-arrow-color': p.muted,
          'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: 'data(label)', 'font-family': G.font, 'font-size': 10, color: p.muted,
          'text-background-color': p.bg, 'text-background-opacity': 0.9, 'text-background-padding': 2, 'text-rotation': 'autorotate' } },
        { selector: 'edge[label = "เป็นเจ้าของ"]', style: { label: '', 'line-style': 'dashed', 'target-arrow-shape': 'none' } },
      ],
    });
    cy.on('tap', 'node', (e) => {
      const d = e.target.data();
      const rows = (data.transactions[d.id] || []).map((t) => `<li class="text-sm border-b border-border py-1"><b>${G.esc(t.amount)} บาท</b> · ${G.esc(t.at)}<br>
        <span class="text-xs text-muted">${G.esc(t.from)} → ${G.esc(t.to)}${t.channel ? ' · ' + G.esc(t.channel) : ''}${t.ref ? ' · อ้างอิง ' + G.esc(t.ref) : ''}</span></li>`).join('');
      panel.innerHTML = `<b>${G.esc(d.label).replace(/\n/g, '<br>')}</b>${d.name ? `<span class="text-sm">${G.esc(d.name)}</span>` : ''}
        ${d.status_label ? `<span class="chip chip-muted text-xs self-start">${G.esc(d.status_label)}</span>` : ''}
        ${d.others ? `<span class="link-badge self-start">พบในคดีอื่น ${d.others} คดี</span>` : ''}
        ${d.layer > 0 && d.layer < 99 ? `<span class="text-xs text-muted">บัญชีชั้นที่ ${d.layer}</span>` : ''}
        <ul class="flex flex-col max-h-[50vh] overflow-y-auto">${rows || '<li class="text-sm text-muted">ไม่มีธุรกรรม</li>'}</ul>`;
    });
  }
  const tab = document.querySelector('[role="tab"][data-tab="flow"]');
  if (tab) tab.addEventListener('click', () => setTimeout(draw, 30));
  if (location.hash === '#flow') setTimeout(draw, 60);
  const btn = document.querySelector('[data-flow-png]');
  if (btn) btn.addEventListener('click', async () => { await draw(); if (cy) G.png(cy, box.dataset.name); });
})();
