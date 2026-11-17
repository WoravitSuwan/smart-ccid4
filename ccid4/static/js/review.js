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
