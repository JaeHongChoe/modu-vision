#!/usr/bin/env node
// Generates src/renderer/styles/theme-light.css: a light-background counterpart for every colour utility class the
// renderer uses. The studios were written for one dark palette with ~5,800 hard-coded colour classes, so the light
// theme is a remap of those classes under html[data-theme="light"] rather than a second set of markup.
//
//   node scripts/generate_light_theme.cjs            # rewrite the stylesheet
//   node scripts/generate_light_theme.cjs --check    # exit 1 when the stylesheet is stale
//
// Rules (hue kept, lightness flipped):
//   dark surfaces  -> page #F7F8F3 / surface #FFFFFF / inset #EEF1EA / hover #E4E9DF, by lightness tier
//   dark borders   -> #D9DED3 (faint) or #93A395 (strong)
//   light text     -> #22392E (strongest) / #42554A (secondary); light coloured text -> same hue at 30% lightness
//   accent tints   -> bg-X-900/950 -> X-100; text-X-100..400 -> X-700; border-X-700..900 -> X-300
//   solid accent buttons (bg-X-500..700 with white text) stay as they are.
const fs = require('fs');
const path = require('path');
const colors = require('tailwindcss/colors');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src', 'renderer');
const OUT = path.join(SRC, 'styles', 'theme-light.css');
const ACCENTS = ['sky', 'cyan', 'blue', 'emerald', 'amber', 'rose', 'red', 'orange', 'teal', 'indigo', 'violet', 'purple', 'lime', 'green', 'yellow', 'fuchsia', 'pink'];
const PROPERTY = { bg: 'background-color', text: 'color', border: 'border-color', ring: '--tw-ring-color', divide: 'border-color', placeholder: 'color' };

function* sources(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* sources(file);
    else if (/\.(tsx?|cjs|css)$/.test(entry.name) && !/\.test\.cjs$/.test(entry.name) && entry.name !== 'theme-light.css') yield file;
  }
}

function usedClasses() {
  const counts = new Map();
  const pattern = /\b(bg|text|border|ring|divide|placeholder)-(\[#[0-9A-Fa-f]{6}\]|slate-\d+|gray-\d+|zinc-\d+|white|black|(?:sky|cyan|blue|emerald|amber|rose|red|orange|teal|indigo|violet|purple|lime|green|yellow|fuchsia|pink)-\d+)(\/\d+)?(?![\w-])/g;
  for (const file of sources(SRC)) {
    const text = fs.readFileSync(file, 'utf8');
    for (const match of text.matchAll(pattern)) counts.set(match[0], (counts.get(match[0]) || 0) + 1);
  }
  return counts;
}

const hex = value => value.replace('#', '').match(/.{2}/g).map(part => parseInt(part, 16) / 255);
function hsl(value) {
  const [r, g, b] = hex(value);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? ((g - b) / d + (g < b ? 6 : 0)) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: h * 60, s, l };
}
function fromHsl({ h, s, l }) {
  const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return '#' + [f(0), f(8), f(4)].map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('').toUpperCase();
}
const SURFACE = { page: '#F7F8F3', surface: '#FFFFFF', inset: '#EEF1EA', hover: '#E4E9DF', raised: '#D9DED3' };
const luminance = value => { const [r, g, b] = hex(value).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [luminance(a), luminance(b)]; return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
/** Text colours are darkened until they read at AA (4.5:1) on the darkest light surface (the inset grey). */
function readable(value) {
  let { h, s, l } = hsl(value.toUpperCase());
  let out = fromHsl({ h, s, l });
  while (contrast(out, SURFACE.inset) < 4.5 && l > 0.05) { l -= 0.02; out = fromHsl({ h, s, l }); }
  return out;
}

function lightFor(prefix, token) {
  const [name, opacity] = token.split('/');
  if (name === 'white') return prefix === 'text' ? '#22392E' : prefix === 'bg' ? SURFACE.surface : '#D9DED3';
  if (name === 'black') return prefix === 'bg' && opacity ? null : prefix === 'bg' ? SURFACE.page : '#22392E';
  if (name.startsWith('[#')) {
    const value = name.slice(1, -1).toUpperCase();
    const { h, s, l } = hsl(value);
    const grey = s < 0.3;
    if (prefix === 'bg') {
      if (!grey && l < 0.3) return fromHsl({ h, s: Math.min(s, 0.8), l: 0.93 });   // dark tint of a hue -> light tint
      if (l <= 0.08) return SURFACE.page;
      if (l <= 0.11) return SURFACE.surface;
      if (l <= 0.16) return SURFACE.inset;
      if (l <= 0.3) return SURFACE.hover;
      if (grey && l <= 0.45) return SURFACE.raised;
      return null;                                                                 // solid accent: unchanged
    }
    if (prefix === 'border' || prefix === 'ring' || prefix === 'divide') {
      if (grey && l < 0.3) return '#D9DED3';
      if (grey && l < 0.45) return '#93A395';
      if (!grey && l < 0.3) return fromHsl({ h, s, l: 0.75 });
      return null;
    }
    if (prefix === 'text' || prefix === 'placeholder') {
      if (grey && l >= 0.85) return '#22392E';
      if (grey && l >= 0.6) return '#42554A';
      if (grey && l >= 0.4) return '#526458';
      if (!grey && l >= 0.35) return fromHsl({ h, s, l: 0.3 });
      return null;                                                                 // already dark enough
    }
    return null;
  }
  const [family, shadeText] = name.split('-');
  const shade = Number(shadeText);
  const scale = colors[family];
  if (!scale || !scale[shade]) return null;
  if (['slate', 'gray', 'zinc'].includes(family)) {
    if (prefix === 'text' || prefix === 'placeholder') {
      const flipped = { 50: 900, 100: 900, 200: 800, 300: 700, 400: 600, 500: 600, 600: 600 }[shade];
      return flipped ? scale[flipped] : null;
    }
    if (prefix === 'bg') return { 950: SURFACE.page, 900: SURFACE.inset, 800: SURFACE.surface, 700: SURFACE.hover, 600: SURFACE.raised }[shade] || null;
    return { 900: '#E4E9DF', 800: '#E4E9DF', 700: '#D9DED3', 600: '#D9DED3', 500: '#93A395' }[shade] || null;
  }
  if (!ACCENTS.includes(family)) return null;
  if (prefix === 'text' || prefix === 'placeholder') return shade <= 400 ? scale[700] : null;
  if (prefix === 'bg') return shade >= 900 ? scale[100] : shade === 800 && opacity ? scale[100] : null;
  return shade >= 700 ? scale[300] : null;
}

const escape = cls => cls.replace(/[\[\]#\/.]/g, ch => '\\' + ch);
const LIGHT_TEXT = new Set(['text-white', 'text-slate-50', 'text-slate-100', 'text-[#F7F8F3]', 'text-[#E4E9DF]']);
/** bg classes the light theme leaves as solid accents: light text on them must stay light. */
function solidAccentBackgrounds(counts) {
  return [...counts.keys()].filter(cls => cls.startsWith('bg-') && lightFor('bg', cls.slice(3)) === null && !/^bg-(white|black|slate|gray|zinc)/.test(cls));
}

function build() {
  const counts = usedClasses();
  const rules = [];
  const keepLight = solidAccentBackgrounds(counts).map(cls => `:not(.${escape(cls)})`).join('');
  for (const [cls, count] of [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
    const [prefix, ...rest] = cls.split('-');
    let value = lightFor(prefix, rest.join('-'));
    if (!value) continue;
    if (prefix === 'text' || prefix === 'placeholder') value = readable(value);
    const selector = LIGHT_TEXT.has(cls) ? `.${escape(cls)}${keepLight}` : `.${escape(cls)}`;
    rules.push(`html[data-theme="light"] ${selector} { ${PROPERTY[prefix]}: ${value}; } /* ${count} */`);
  }
  return `/* Generated by scripts/generate_light_theme.cjs: do not edit by hand. ${rules.length} colour utilities remapped. */
html[data-theme="light"] { color-scheme: light; }
html[data-theme="light"] body, html[data-theme="light"] #root { background-color: #F7F8F3; color: #22392E; }
html[data-theme="light"] {
  --color-chassis: #F7F8F3; --color-panel: #FFFFFF; --color-card: #FFFFFF; --color-border: #D9DED3; --color-hover: #E4E9DF; --color-active: #D9DED3;
  --color-metric-readout: #22392E; --color-metric-dim: #526458; --color-metric-unit: #5D7063;
  --workspace-bg: #F7F8F3; --workspace-surface: #FFFFFF; --workspace-inset: #EEF1EA; --workspace-border: #D9DED3;
  --workspace-text: #22392E; --workspace-muted: #526458; --workspace-accent: #245B43; --workspace-focus: #246D4B;
  --workspace-success: #047857; --workspace-warning: #B45309; --workspace-danger: #B91C1C;
}
html[data-theme="light"] ::-webkit-scrollbar-track { background: #EEF1EA; }
html[data-theme="light"] ::-webkit-scrollbar-thumb { background: #D9DED3; }
html[data-theme="light"] ::selection { background-color: #D9EBDD; color: #22392E; }
html[data-theme="light"] .workspace-button { color: #22392E; background: #FFFFFF; border-color: #93A395; }
html[data-theme="light"] .workspace-button:hover:not(:disabled) { background: #E4E9DF; }
html[data-theme="light"] .workspace-button--primary { color: #FFFFFF; background: #245B43; border-color: #245B43; }
html[data-theme="light"] .workspace-button--primary:hover:not(:disabled) { background: #194831; }
html[data-theme="light"] .workspace-button--quiet { color: var(--workspace-muted); background: transparent; border-color: transparent; }
html[data-theme="light"] .workspace-button--quiet:hover:not(:disabled) { color: var(--workspace-text); background: var(--workspace-surface); border-color: var(--workspace-border); }
html[data-theme="light"] .workspace-badge[data-tone="success"] { background: #D1FAE5; }
html[data-theme="light"] .workspace-badge[data-tone="warning"] { background: #FEF3C7; }
html[data-theme="light"] .workspace-badge[data-tone="danger"] { background: #FEE2E2; }
html[data-theme="light"] .workspace-badge[data-tone="info"] { background: #E0F2FE; }
html[data-theme="light"] .workspace-field :is(input, select, textarea) { border-color: #93A395; }
html[data-theme="light"] .flow-canvas-grid { --flow-grid-line: #E4E9DF; }
${rules.join('\n')}
`;
}

const css = build();
if (process.argv.includes('--check')) {
  const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
  if (current !== css) { console.error('theme-light.css is stale: run node scripts/generate_light_theme.cjs'); process.exit(1); }
  console.log('theme-light.css is current');
} else {
  fs.writeFileSync(OUT, css);
  console.log(`wrote ${path.relative(ROOT, OUT)} (${css.split('\n').length} lines)`);
}
