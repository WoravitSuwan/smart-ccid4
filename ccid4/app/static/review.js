// Review page: any element with data-seek jumps the audio there; the segment being heard is highlighted.
(function () {
  const player = document.getElementById('player');
  if (!player) return;
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-seek]');
    if (!b) return;
    e.preventDefault();
    player.currentTime = Math.max(0, parseFloat(b.dataset.seek) - 0.3);
    player.play();
  });
  const segs = Array.from(document.querySelectorAll('.seg[data-start]'));
  let cur = null;
  player.addEventListener('timeupdate', () => {
    const t = player.currentTime;
    const s = segs.find((el) => t >= parseFloat(el.dataset.start) && t < parseFloat(el.dataset.end) + 0.5);
    if (s !== cur) {
      if (cur) cur.classList.remove('cur');
      if (s) { s.classList.add('cur'); if (!player.paused) s.scrollIntoView({ block: 'nearest' }); }
      cur = s;
    }
  });
})();
