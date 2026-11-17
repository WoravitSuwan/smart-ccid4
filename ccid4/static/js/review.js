// หน้าตรวจคำให้การจากเสียง: คลื่นเสียง (wavesurfer) · กดคำ/ป้ายเวลาแล้วเล่นช่วงนั้น · ไฮไลต์คำที่กำลังพูด
(() => {
  const player = document.querySelector('[data-player]');
  if (!player || !window.WaveSurfer) return;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const ws = window.WaveSurfer.create({
    container: player.querySelector('[data-waveform]'), url: player.dataset.audioUrl, height: 72, barWidth: 2, barGap: 1, barRadius: 2,
    waveColor: css('--border'), progressColor: css('--primary'), cursorColor: css('--accent'), normalize: true,
  });
  const clock = player.querySelector('[data-clock]');
  const fmt = (t) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
  let stopAt = null;
  const words = [...document.querySelectorAll('.word[data-s]')].map((el) => ({ el, s: parseFloat(el.dataset.s), e: parseFloat(el.dataset.e) }));
  let lit = null;
  ws.on('timeupdate', (t) => {
    clock.textContent = fmt(t);
    if (stopAt !== null && t >= stopAt) { ws.pause(); stopAt = null; }
    const w = words.find((x) => t >= x.s && t < x.e);
    if (w !== lit) { if (lit) lit.el.classList.remove('speaking'); if (w) w.el.classList.add('speaking'); lit = w; }
  });
  player.querySelector('[data-play]').addEventListener('click', () => { stopAt = null; ws.playPause(); });
  document.addEventListener('click', (e) => {
    const word = e.target.closest('.word[data-s]');
    const seek = e.target.closest('[data-seek]');
    if (!word && !seek) return;
    const start = parseFloat((word || seek).dataset.s || seek.dataset.seek);
    const until = seek && seek.dataset.until ? parseFloat(seek.dataset.until) : null;
    ws.setTime(Math.max(0, start - 0.2));
    stopAt = until;
    ws.play();
  });
})();

// สรุปจากเสียง: แท็บ (สรุปใจความสำคัญ / เล่าเรื่อง / คำที่พบบ่อย) + กดคำที่พบบ่อยแล้วไฮไลต์ในข้อความถอดเสียง
(() => {
  const box = document.querySelector('[data-tabs]');
  if (!box) return;
  const tabs = [...box.querySelectorAll('[data-tab]')];
  const show = (name) => {
    tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
    box.querySelectorAll('[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== name; });
    try { sessionStorage.setItem(`ccid4-summary-tab-${location.pathname}`, name); } catch (e) { /* private mode */ }
  };
  tabs.forEach((t) => t.addEventListener('click', () => show(t.dataset.tab)));
  let first = tabs[0] && tabs[0].dataset.tab;
  try { first = sessionStorage.getItem(`ccid4-summary-tab-${location.pathname}`) || first; } catch (e) { /* ignore */ }
  if (location.hash === '#story') first = 'story';
  show(first);

  const result = box.querySelector('[data-kw-result]');
  const clear = () => {
    document.querySelectorAll('.kw-hit').forEach((el) => el.classList.remove('kw-hit'));
    document.querySelectorAll('.kw-seg').forEach((el) => el.classList.remove('kw-seg'));
  };
  box.querySelectorAll('[data-kw]').forEach((chip) => chip.addEventListener('click', () => {
    const on = chip.getAttribute('aria-pressed') !== 'true';
    box.querySelectorAll('[data-kw]').forEach((c) => c.setAttribute('aria-pressed', 'false'));
    clear();
    if (!on) { result.textContent = 'กดคำเพื่อไฮไลต์ในข้อความถอดเสียง'; return; }
    chip.setAttribute('aria-pressed', 'true');
    const kw = chip.dataset.kw;
    let segs = 0, first = null;
    document.querySelectorAll('[data-transcript] .seg').forEach((seg) => {
      const hits = [...seg.querySelectorAll('.word')].filter((w) => w.textContent.includes(kw));
      if (!hits.length) return;
      hits.forEach((w) => w.classList.add('kw-hit'));
      seg.classList.add('kw-seg');
      segs += 1;
      first = first || seg;
    });
    result.textContent = segs ? `“${kw}” พบใน ${segs} ช่วงพูด (ไฮไลต์สีส้มในข้อความถอดเสียง)` : `“${kw}” ไม่พบเป็นคำแยกในข้อความถอดเสียง`;
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }));
})();
