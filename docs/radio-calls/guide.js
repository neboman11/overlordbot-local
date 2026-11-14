const sections = [...document.querySelectorAll('section[data-source]')];
const markdown = new Map();

function loadGuide(source) {
  if (!markdown.has(source)) {
    markdown.set(source, fetch(source).then(async response => {
      if (!response.ok) throw new Error(`Guide request failed (${response.status})`);
      return response.text();
    }));
  }
  return markdown.get(source);
}

function fail(section, error) {
  section.querySelector('.status').textContent = `Chart unavailable: ${error.message}. Open the linked GitHub phrase guide instead.`;
  section.dataset.state = 'error';
}

function attachControls(section, svg) {
  const viewport = section.querySelector('.viewport');
  const { width, height } = svg.viewBox.baseVal;
  if (!(width > 0 && height > 0)) throw new Error('Diagram has no usable dimensions');
  let scale = 1;
  const output = section.querySelector('output');
  const resize = value => {
    scale = Math.max(0.001, Math.min(3, value));
    svg.style.width = `${width * scale}px`;
    svg.style.height = `${height * scale}px`;
    output.textContent = `${Math.round(scale * 100)}%`;
  };
  const showStart = () => {
    resize(1);
    const entryId = {
      'airfield-departure': 'D_START', 'airfield-arrival': 'A_START',
      'carrier-departure': 'CL_W_SPAWN', 'carrier-recovery': 'CR_F_INBOUND',
    }[section.id];
    const entry = svg.querySelector(`[id^="flowchart-${entryId}-"]`);
    if (!entry) throw new Error('Diagram start node missing');
    const node = entry.getBoundingClientRect();
    const frame = viewport.getBoundingClientRect();
    viewport.scrollLeft += node.left - frame.left - (viewport.clientWidth - node.width) / 2;
    viewport.scrollTop += node.top - frame.top - 32;
  };
  showStart();
  for (const button of section.querySelectorAll('button[data-action]')) {
    button.disabled = false;
    button.addEventListener('click', () => {
      switch (button.dataset.action) {
        case 'in': resize(scale * 1.25); break;
        case 'out': resize(scale / 1.25); break;
        case 'fit':
          resize((viewport.clientWidth - 32) / width);
          viewport.scrollTo(0, 0);
          break;
        case 'actual': showStart(); break;
        case 'download': {
          const copy = svg.cloneNode(true);
          copy.style.width = `${width}px`;
          copy.style.height = `${height}px`;
          copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
          const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], { type: 'image/svg+xml' }));
          const anchor = document.createElement('a');
          anchor.href = url;
          anchor.download = `${section.id}.svg`;
          anchor.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          break;
        }
      }
    });
  }
}

try {
  const { default: mermaid } = await import('https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.esm.min.mjs');
  const { default: elkLayouts } = await import('https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.2.0/dist/mermaid-layout-elk.esm.min.mjs');
  mermaid.registerLayoutLoaders(elkLayouts);
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', htmlLabels: false, maxTextSize: 50000,
    layout: 'elk', elk: { mergeEdges: false, nodePlacementStrategy: 'NETWORK_SIMPLEX' },
    flowchart: { useMaxWidth: false, htmlLabels: false, curve: 'linear' } });
  for (const section of sections) {
    try {
      const text = await loadGuide(section.dataset.source);
      const charts = [...text.matchAll(/^```mermaid\s*\n([\s\S]*?)^```\s*$/gm)];
      const definition = charts[Number(section.dataset.chart)]?.[1];
      if (!definition) throw new Error('Requested chart is missing from the phrase guide');
      const { svg, bindFunctions } = await mermaid.render(`flow-${section.id}`, definition);
      const viewport = section.querySelector('.viewport');
      viewport.innerHTML = svg;
      bindFunctions?.(viewport);
      attachControls(section, viewport.querySelector('svg'));
      section.dataset.state = 'ready';
      section.querySelector('.status').textContent = 'Ready — complete flow, including alternative and recovery paths. Start at 100% for readable calls; use Fit for an overview.';
    } catch (error) {
      fail(section, error);
    }
  }
} catch (error) {
  for (const section of sections) fail(section, error);
}
