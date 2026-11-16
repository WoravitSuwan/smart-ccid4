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
