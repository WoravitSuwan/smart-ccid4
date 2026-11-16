// CCID4 client behaviour. Alpine's CSP build only allows components registered here (no inline JS).
document.addEventListener('alpine:init', () => {
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

  Alpine.data('shell', () => ({
    collapsed: false,
    init() {
      try { this.collapsed = localStorage.getItem('ccid4.nav') === 'collapsed'; } catch (e) { /* storage blocked */ }
    },
    toggleNav() {
      this.collapsed = !this.collapsed;
      try { localStorage.setItem('ccid4.nav', this.collapsed ? 'collapsed' : 'open'); } catch (e) { /* ignore */ }
    },
    get navLabel() { return this.collapsed ? 'ขยายเมนู' : 'ย่อเมนู'; },
    get expanded() { return !this.collapsed; },
    get navClass() { return this.collapsed ? 'w-[72px]' : 'w-64'; },
    toggleTheme() {
      const root = document.documentElement;
      const next = root.dataset.theme === 'light' ? 'dark' : 'light';
      root.dataset.theme = next;
      const body = new URLSearchParams({ theme: next });
      fetch('/accounts/theme/', { method: 'POST', body, headers: { 'X-CSRFToken': csrf(), 'HX-Request': 'true' } });
    },
  }));

  // Numbers that count up when they appear: <b x-data="countUp" data-value="42"></b>
  Alpine.data('countUp', () => ({
    shown: '0',
    init() {
      const target = parseFloat(this.$el.dataset.value || '0');
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce || !target) { this.shown = target.toLocaleString('th-TH'); this.$el.textContent = this.shown; return; }
      const t0 = performance.now(), dur = 900;
      const step = (t) => {
        const k = Math.min(1, (t - t0) / dur), eased = 1 - Math.pow(1 - k, 3);
        this.$el.textContent = Math.round(target * eased).toLocaleString('th-TH');
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    },
  }));

  // Toast messages slide in and leave on their own (errors stay until closed).
  Alpine.data('toast', () => ({
    open: true,
    init() { if (!this.$el.classList.contains('toast-error')) setTimeout(() => { this.open = false; }, 5000); },
    close() { this.open = false; },
  }));

  // Unsaved-changes guard used by forms with x-data="dirtyForm".
  Alpine.data('dirtyForm', () => ({
    dirty: false,
    init() {
      this.$el.addEventListener('input', () => { this.dirty = true; });
      this.$el.addEventListener('submit', () => { this.dirty = false; });
      window.addEventListener('beforeunload', (e) => { if (this.dirty) { e.preventDefault(); e.returnValue = ''; } });
    },
  }));
});

// HTMX sends the CSRF token on every request.
document.addEventListener('htmx:configRequest', (e) => {
  e.detail.headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').content;
});

// Stagger elements marked .reveal inside the same parent.
document.addEventListener('DOMContentLoaded', () => {
  const groups = new Map();
  document.querySelectorAll('.reveal').forEach((el) => {
    const n = groups.get(el.parentElement) || 0;
    el.style.setProperty('--i', String(n));
    groups.set(el.parentElement, n + 1);
  });
});
