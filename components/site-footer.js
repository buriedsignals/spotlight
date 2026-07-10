// Shared site footer — ONE source of truth for every page (landing, setup,
// going-sovereign, …). Rendered as a Shadow-DOM custom element so the host
// page's CSS (e.g. a global `a { color: … }`) physically cannot bleed in and
// make one page's copy look different from another's. Drop `<site-footer>` on a
// page and include this script; links are root-relative so the same element
// works at any URL depth.
//
// Usage:
//   <site-footer></site-footer>
//   <script src="/components/site-footer.js" defer></script>
//
// The scroll-reveal fade (landing/setup) is preserved by tagging the HOST with
// data-reveal in the page markup — the page's IntersectionObserver fades the
// whole element in; the shadow content rides along.

const CREAM = '#ede8dc';                       // canonical footer text
const MUTED = 'rgba(237, 232, 220, 0.55)';     // summary / meta
const FAINT = 'rgba(237, 232, 220, 0.18)';     // hairline + button border
const ACCENT = '#c16a34';                       // terracotta hover
const ACCENT_14 = 'rgba(193, 106, 52, 0.14)';   // hover fill

const BRAND_MARK =
  '<path d="M6 2h4v4h-4zM10 2h4v4h-4zM14 2h4v4h-4zM6 6h4v4h-4zM6 10h4v4h-4zM14 10h4v4h-4zM14 14h4v4h-4zM6 18h4v4h-4zM10 18h4v4h-4zM14 18h4v4h-4z"/>';

class SiteFooter extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { display: block; color: ${CREAM}; background: transparent;
          padding: 96px 48px 32px; position: relative; z-index: 10; overflow: hidden;
          font-family: inherit; }
        a { text-decoration: none; color: inherit; }
        .footer-simple {
          display: grid; grid-template-columns: minmax(0, 56ch) auto;
          align-items: start; gap: 32px; padding-top: 28px;
          border-top: 1px solid ${FAINT};
        }
        .footer-summary { margin: 0; max-width: 56ch; font-size: 14px;
          line-height: 1.7; color: ${MUTED}; }
        .footer-links { display: flex; flex-wrap: wrap; gap: 14px;
          justify-content: flex-end; align-content: flex-start; }
        .footer-links a {
          display: inline-flex; align-items: center; padding: 10px 14px;
          border: 1px solid ${FAINT}; color: ${CREAM};
          font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
          transition: background .2s ease, border-color .2s ease, color .2s ease;
        }
        .footer-links a:hover { background: ${ACCENT_14}; border-color: ${ACCENT}; color: ${ACCENT}; }
        .footer-meta {
          display: flex; justify-content: space-between; align-items: baseline;
          font-size: 10px; letter-spacing: 0.2em; text-transform: uppercase;
          opacity: 0.5; margin-top: 30px;
        }
        .brand-mini { display: flex; align-items: center; gap: 10px; }
        .brand-mini svg { width: 14px; height: 14px; opacity: 0.7; }
        @media (max-width: 640px) {
          :host { padding: 64px 20px 28px; }
          .footer-simple { grid-template-columns: 1fr; gap: 18px; }
          .footer-summary { font-size: 13px; }
          .footer-links { gap: 10px; justify-content: flex-start; }
          .footer-meta { flex-direction: column; gap: 12px; align-items: flex-start; margin-top: 28px; }
        }
      </style>
      <div class="footer-simple">
        <p class="footer-summary">An open-source investigative system for AI agents, built for journalists. One agent reports, one agent checks, and you stay the editor. Local when the case is too sensitive for the cloud.</p>
        <div class="footer-links">
          <a href="/docs/index.html">Docs</a>
          <a href="/setup.html">Install</a>
          <a href="https://buriedsignals.com/consulting" target="_blank" rel="noopener">Work with me ↗</a>
          <a href="https://buriedsignals.com" target="_blank" rel="noopener">Buried Signals ↗</a>
        </div>
      </div>
      <div class="footer-meta">
        <div class="brand-mini">
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${BRAND_MARK}</svg>
          <span>Spotlight · v1.0</span>
        </div>
        <span>© 2026 Buried Signals — MIT licensed</span>
      </div>`;
  }
}

customElements.define('site-footer', SiteFooter);
