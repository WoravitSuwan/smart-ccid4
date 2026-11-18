// B9 บัตรประชาชน: card-agent (ws://127.0.0.1) หรือภาพบัตร (OCR ในเครื่อง) → หน้าตรวจทาน → เติมฟอร์ม → บันทึกประวัติการใช้งาน
(() => {
  const dlg = document.getElementById('card-dialog');
  if (!dlg) return;
  const $ = (s) => dlg.querySelector(s);
  const csrf = () => (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || document.querySelector('meta[name="csrf-token"]').content;
  const FIELDS = [['title', 'คำนำหน้า'], ['first_name', 'ชื่อ'], ['last_name', 'นามสกุล'], ['citizen_id', 'เลขประจำตัวประชาชน'], ['birth_date', 'วันเกิด'],
    ['gender', 'เพศ'], ['house_no', 'บ้านเลขที่'], ['moo', 'หมู่'], ['building', 'อาคาร/หมู่บ้าน'], ['soi', 'ซอย'], ['road', 'ถนน'],
    ['tambon', 'ตำบล/แขวง'], ['amphoe', 'อำเภอ/เขต'], ['province', 'จังหวัด']];
  let prefix = '', data = null, source = 'reader';
  const status = (text, tone) => { const el = $('[data-card-status]'); el.textContent = text; el.className = `text-sm ${tone ? 'chip chip-' + tone : ''}`; };
  const open = (src) => {
    source = src; data = null;
    $('[data-card-source]').textContent = src === 'reader' ? 'จากเครื่องอ่านบัตร (card-agent)' : 'จากภาพบัตร (OCR ในเครื่อง — ต้องตรวจทุกตัวอักษร)';
    $('[data-card-body]').hidden = true; $('[data-card-apply]').disabled = true; $('[data-card-rows]').innerHTML = ''; $('[data-card-warn]').innerHTML = '';
    if (!dlg.open) dlg.showModal();
  };
  const show = (d, photo) => {
    data = d;
    const rows = FIELDS.filter(([k]) => (d[k] || '').toString().trim()).map(([k, label]) => {
      const tr = document.createElement('tr');
      const td0 = document.createElement('td'); const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true; cb.dataset.field = k; cb.setAttribute('aria-label', `ใช้${label}`); td0.appendChild(cb);
      const td1 = document.createElement('td'); td1.textContent = label;
      const td2 = document.createElement('td'); td2.textContent = d[k]; td2.className = 'font-semibold';
      tr.append(td0, td1, td2); return tr;
    });
    $('[data-card-rows]').replaceChildren(...rows);
    const warn = [];
    if (d.citizen_id && d.cid_valid === false) warn.push('เลขประจำตัวประชาชนไม่ผ่านการตรวจหลักสุดท้าย — ตรวจกับบัตรอีกครั้ง');
    if (d.expires && d.expires < new Date().toISOString().slice(0, 10)) warn.push(`บัตรหมดอายุแล้ว (${d.expires})`);
    $('[data-card-warn]').replaceChildren(...warn.map((w) => Object.assign(document.createElement('p'), { className: 'chip chip-warning self-start text-xs', textContent: w })));
    const img = $('[data-card-photo]'); img.hidden = !photo; if (photo) img.src = photo;
    $('[data-card-body]').hidden = false; $('[data-card-apply]').disabled = !rows.length;
    status(rows.length ? 'ตรวจทานแล้วเลือกช่องที่จะใช้' : 'อ่านไม่พบข้อมูลที่ใช้ได้', rows.length ? '' : 'warning');
  };

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-card-read]');
    if (!btn) return;
    prefix = btn.dataset.cardRead; open('reader'); status('กำลังเชื่อมต่อโปรแกรม card-agent บนเครื่องนี้…');
    let done = false, ws;
    const fail = (m) => { if (!done) { done = true; status(m, 'danger'); } };
    try { ws = new WebSocket(dlg.dataset.agent); } catch (err) { fail('เชื่อมต่อ card-agent ไม่ได้'); return; }
    const timer = setTimeout(() => { fail('ไม่มีการตอบกลับจากเครื่องอ่านบัตร ลองเสียบบัตรใหม่ หรือใช้ "อ่านจากภาพบัตร"'); ws.close(); }, 25000);
    ws.onopen = () => { status('กำลังอ่านบัตร… อย่าดึงบัตรออก'); ws.send(JSON.stringify({ type: 'read' })); };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data); clearTimeout(timer); done = true; ws.close();
      if (msg.type === 'card') show(msg.data, msg.photo); else status(msg.message || 'อ่านบัตรไม่สำเร็จ', 'danger');
    };
    ws.onerror = () => { clearTimeout(timer); fail('ไม่พบโปรแกรม card-agent บนเครื่องนี้ — ติดตั้งตามคู่มือ หรือใช้ "อ่านจากภาพบัตร"'); };
  });

  document.addEventListener('change', async (e) => {
    const input = e.target.closest('[data-card-ocr]');
    if (!input || !input.files.length) return;
    prefix = input.dataset.cardOcr; open('ocr'); status('กำลังอ่านภาพบัตรด้วย OCR ในเครื่อง…');
    const body = new FormData(); body.append('image', input.files[0]); input.value = '';
    try {
      const r = await fetch(dlg.dataset.ocrUrl, { method: 'POST', body, headers: { 'X-CSRFToken': csrf() } });
      const j = await r.json();
      if (!r.ok) { status(j.error || 'อ่านภาพไม่สำเร็จ', 'danger'); return; }
      show(j.data, '');
    } catch (err) { status('อ่านภาพไม่สำเร็จ', 'danger'); }
  });

  $('[data-card-apply]').addEventListener('click', async () => {
    if (!data) return;
    let filled = 0;
    dlg.querySelectorAll('[data-card-rows] input[type=checkbox]:checked').forEach((cb) => {
      const el = document.getElementById(`id_${prefix}-${cb.dataset.field}`);
      if (!el) return;
      el.value = data[cb.dataset.field];
      el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
      filled += 1;
    });
    const body = new URLSearchParams({ source, name: `${data.title || ''}${data.first_name || ''} ${data.last_name || ''}`.trim(), cid: data.citizen_id || '' });
    fetch(dlg.dataset.auditUrl, { method: 'POST', body, headers: { 'X-CSRFToken': csrf() } }).catch(() => {});
    dlg.close();
    const first = document.getElementById(`id_${prefix}-first_name`); if (first) first.focus();
    status(`เติมแล้ว ${filled} ช่อง`);
  });
})();
