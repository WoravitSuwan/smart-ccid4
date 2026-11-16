/** Colors come only from CSS variables in static/css/tokens.css (no raw hex in templates). */
const v = (name) => `var(--${name})`;
module.exports = {
  content: ['./templates/**/*.html', './**/templates/**/*.html', './static/js/**/*.js'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'), surface: v('surface'), 'surface-2': v('surface-2'), border: v('border'),
        primary: v('primary'), 'primary-hover': v('primary-hover'), 'primary-soft': v('primary-soft'), 'on-primary': v('on-primary'),
        accent: v('accent'), 'accent-ink': v('accent-ink'), text: v('text'), muted: v('text-muted'),
        success: v('success'), warning: v('warning'), 'warning-ink': v('warning-ink'), danger: v('danger'), ai: v('ai'),
      },
      fontFamily: { sans: ['"IBM Plex Sans Thai"', '"Noto Sans Thai"', 'Tahoma', 'sans-serif'] },
      borderRadius: { card: '12px', btn: '8px' },
    },
  },
  plugins: [],
};
