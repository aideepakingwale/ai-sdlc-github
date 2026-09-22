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
          50: '#eef3fa',
          100: '#dfe7f2',  // BA light blue (selected/current)
          200: '#c2d3ea',
          300: '#9ab6da',
          400: '#6690c6',
          500: '#3468ad',  // BA blue
          600: '#2c5896',
          700: '#244873',
          800: '#1d3a5c',
          900: '#16304f',
        },
        // BA navy / "Midnight" — primary text & dark chrome
        navy: {
          DEFAULT: '#021b41',
          900: '#021b41',
          800: '#0a2350',
          700: '#16304f',
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
          50: '#f9f9fa',
          100: '#f1f2f4',
          200: '#e7e8ec',
          300: '#cfd0d9',
          400: '#b7b9c6',
          500: '#989cae',
          600: '#70758f',
          700: '#3d4761',
          800: '#1b2b4d',
          900: '#021b41',
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
