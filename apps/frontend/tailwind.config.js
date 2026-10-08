/** @type {import('tailwindcss').Config} */
// Theme aligned to the British Airways "BAgel" design language (britishairways.design):
// navy/midnight #021b41, BA blue #3468ad, BA red #ce210f, Open Sans type, ~9px radii.
// Brand assets/logos (the Speedmarque) are BA trademarks and are intentionally NOT bundled.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // "Mylius Modern" is BA's proprietary face (not shipped); Open Sans is the
        // documented web fallback. Degrades to Helvetica/Arial offline/air-gapped.
        sans: ['"Mylius Modern"', '"Open Sans"', '"Helvetica Neue"', 'Helvetica', 'Arial', 'sans-serif'],
      },
      colors: {
        // Primary interactive — BA blue
        brand: {
          50: 'rgb(var(--c-brand-50) / <alpha-value>)',
          100: 'rgb(var(--c-brand-100) / <alpha-value>)',  // BA light blue (selected/current)
          200: 'rgb(var(--c-brand-200) / <alpha-value>)',
          300: 'rgb(var(--c-brand-300) / <alpha-value>)',
          400: 'rgb(var(--c-brand-400) / <alpha-value>)',
          500: 'rgb(var(--c-brand-500) / <alpha-value>)',  // BA blue
          600: 'rgb(var(--c-brand-600) / <alpha-value>)',
          700: 'rgb(var(--c-brand-700) / <alpha-value>)',
          800: 'rgb(var(--c-brand-800) / <alpha-value>)',
          900: 'rgb(var(--c-brand-900) / <alpha-value>)',
        },
        // BA navy / "Midnight" — primary text & dark chrome
        navy: {
          DEFAULT: 'rgb(var(--c-navy-default) / <alpha-value>)',
          900: 'rgb(var(--c-navy-900) / <alpha-value>)',
          800: 'rgb(var(--c-navy-800) / <alpha-value>)',
          700: 'rgb(var(--c-navy-700) / <alpha-value>)',
        },
        // BA red / alert accent
        bared: {
          200: '#f7dbd9',
          500: '#ce210f',
          600: '#ad1c0d',
          700: '#8c160a',
        },
        // Neutrals shifted to BA's cool navy-grey family so existing slate-* utilities
        // adopt the BA palette without touching every component.
        slate: {
          50: 'rgb(var(--c-slate-50) / <alpha-value>)',
          100: 'rgb(var(--c-slate-100) / <alpha-value>)',
          200: 'rgb(var(--c-slate-200) / <alpha-value>)',
          300: 'rgb(var(--c-slate-300) / <alpha-value>)',
          400: 'rgb(var(--c-slate-400) / <alpha-value>)',
          500: 'rgb(var(--c-slate-500) / <alpha-value>)',
          600: 'rgb(var(--c-slate-600) / <alpha-value>)',
          700: 'rgb(var(--c-slate-700) / <alpha-value>)',
          800: 'rgb(var(--c-slate-800) / <alpha-value>)',
          900: 'rgb(var(--c-slate-900) / <alpha-value>)',
        },
      },
      borderRadius: {
        DEFAULT: '6px',
        md: '9px',   // BA input radius
        lg: '10px',
        xl: '14px',
      },
    },
  },
  plugins: [],
};
