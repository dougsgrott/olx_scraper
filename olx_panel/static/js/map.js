// Coverage map: every IBGE municipality from one TopoJSON (scraped_data/geo,
// built by scripts/build_geo.py), drawn with MapLibre at three levels — states
// and DDD areas are merged from the municipalities in the browser, so borders
// line up exactly. The server sends, per metric, a class per key plus the
// legend colours (/api/map); this script only paints and handles pointer
// events. No basemap tiles or glyphs: it works offline.
(function () {
  const el = document.getElementById('map');
  if (!el || typeof maplibregl === 'undefined') return;

  const days = el.dataset.days;
  const tip = document.getElementById('map-tip');
  const legendEl = document.getElementById('map-legend');
  const metricEl = document.getElementById('map-metric');
  const levelButtons = Array.from(document.querySelectorAll('[data-level]'));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // the server names theme-dependent colours as var(--token)
  const paintColor = (c) => (c.startsWith('var(') ? cssVar(c.slice(4, -1)) : c);
  const LEVELS = ['uf', 'area', 'muni'];
  const BRAZIL = [[-74.2, -34.0], [-34.6, 5.4]];

  const hash = new URLSearchParams(location.hash.slice(1));
  const view = {
    level: LEVELS.includes(hash.get('level')) ? hash.get('level') : 'uf',
    metric: hash.get('metric') || metricEl.value,
    values: {},
    nodes: {},
    hover: null,       // {level, key}
    selected: null,    // {level, key}
    treeHover: null,
  };
  if ([...metricEl.options].some((o) => o.value === view.metric)) metricEl.value = view.metric;
  const bboxes = { uf: {}, area: {}, muni: {} };
  const located = new Map();  // path -> /api/locate result
  let map = null;

  function bboxOf(geometry) {
    let [x0, y0, x1, y1] = [180, 90, -180, -90];
    const walk = (c) => {
      if (typeof c[0] === 'number') {
        x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]);
        x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
      } else c.forEach(walk);
    };
    walk(geometry.coordinates);
    return [[x0, y0], [x1, y1]];
  }

  function features(list, level) {
    for (const f of list) bboxes[level][f.properties.key] = bboxOf(f.geometry);
    return { type: 'FeatureCollection', features: list };
  }

  function merged(topo, geometries, prop, label) {
    const groups = {};
    for (const g of geometries) {
      const key = g.properties[prop];
      if (key) (groups[key] = groups[key] || []).push(g);
    }
    return Object.entries(groups).map(([key, geoms]) => ({
      type: 'Feature',
      properties: { key, n: label(key) },
      geometry: topojson.merge(topo, geoms),
    }));
  }

  function writeHash() {
    const params = new URLSearchParams({ level: view.level, metric: view.metric });
    history.replaceState(null, '', `${location.pathname}${location.search}#${params}`);
  }

  function setState(level, key, state) {
    if (key) map.setFeatureState({ source: level, id: key }, state);
  }

  async function loadValues() {
    const { level, metric } = view;
    const res = await fetch(`/api/map?level=${level}&metric=${metric}&days=${days}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.level !== view.level || data.metric !== view.metric) return;  // superseded
    view.values = data.values;
    view.nodes = data.nodes;
    const paint = ['match', ['coalesce', ['feature-state', 'c'], -1]];
    data.legend.forEach((entry, i) => paint.push(i, paintColor(entry.color)));
    paint.push(paintColor(data.none_color));
    map.setPaintProperty(`fill-${level}`, 'fill-color', paint);
    map.removeFeatureState({ source: level });
    for (const [key, [cls, , inherited]] of Object.entries(data.values)) {
      map.setFeatureState({ source: level, id: key }, { c: cls === null ? -1 : cls, i: inherited });
    }
    if (view.selected && view.selected.level === level) setState(level, view.selected.key, { sel: true });
    legendEl.innerHTML = `<span class="legend-title">${data.title}${metric === 'fresh' ? ` ≤ ${days} d` : ''}</span>` +
      data.legend.map((e) => `<span class="swatch"><i style="background:${e.color}"></i>${e.label}</span>`).join('') +
      `<span class="swatch"><i style="background:${data.none_color}"></i>no data</span>` +
      (level === 'muni' ? '<span class="swatch faint"><i style="background:#888;opacity:.4"></i>inherited</span>' : '');
  }

  function setLevel(level) {
    view.level = level;
    for (const l of LEVELS) {
      const visible = l === level ? 'visible' : 'none';
      map.setLayoutProperty(`fill-${l}`, 'visibility', visible);
      map.setLayoutProperty(`hl-${l}`, 'visibility', visible);
    }
    map.setLayoutProperty('muni-lines', 'visibility', level === 'muni' ? 'visible' : 'none');
    levelButtons.forEach((b) => b.classList.toggle('on', b.dataset.level === level));
    writeHash();
    return loadValues();
  }

  function detailUrl(level, key) {
    if (level === 'muni') return `/coverage/muni?code=${key}&days=${days}`;
    const path = level === 'uf' ? `estado-${key.toLowerCase()}` : view.nodes[key];
    return path ? `/coverage/node?path=${encodeURIComponent(path)}&days=${days}` : null;
  }

  function select(level, key) {
    if (view.selected) setState(view.selected.level, view.selected.key, { sel: false });
    view.selected = key ? { level, key } : null;
    if (key) setState(level, key, { sel: true });
  }

  function showTip(event, feature) {
    const [cls, text] = view.values[feature.properties.key] || [null, 'not listed by OLX'];
    tip.innerHTML = `<b>${feature.properties.n}</b><br>${text}`;
    tip.hidden = false;
    const box = el.getBoundingClientRect();
    tip.style.left = `${Math.min(event.point.x + 14, box.width - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(event.point.y - tip.offsetHeight - 10, 8)}px`;
  }

  function wire(level) {
    map.on('mousemove', `fill-${level}`, (e) => {
      const f = e.features[0];
      if (!view.hover || view.hover.key !== f.properties.key) {
        if (view.hover) setState(view.hover.level, view.hover.key, { hover: false });
        view.hover = { level, key: f.properties.key };
        setState(level, f.properties.key, { hover: true });
      }
      map.getCanvas().style.cursor = 'pointer';
      showTip(e, f);
    });
    map.on('mouseleave', `fill-${level}`, () => {
      if (view.hover) setState(view.hover.level, view.hover.key, { hover: false });
      view.hover = null;
      map.getCanvas().style.cursor = '';
      tip.hidden = true;
    });
    map.on('click', `fill-${level}`, async (e) => {
      const key = e.features[0].properties.key;
      if (e.originalEvent.shiftKey && window.basket) {  // Shift-click: select for a scrape
        const res = await fetch(`/api/unit-paths?level=${level}&key=${encodeURIComponent(key)}`);
        const paths = res.ok ? (await res.json()).paths : [];
        if (paths.length) window.basket.add(paths);
        return;
      }
      select(level, key);
      const url = detailUrl(level, key);
      if (url) htmx.ajax('GET', url, { target: '#detail', swap: 'innerHTML' });
    });
  }

  async function locate(path) {
    if (!located.has(path)) {
      const res = await fetch(`/api/locate?path=${encodeURIComponent(path)}`);
      located.set(path, res.ok ? await res.json() : null);
    }
    return located.get(path);
  }

  function keyAt(level, keys) {
    if (!keys) return null;
    if (level === 'uf') return keys.uf;
    if (level === 'area') return keys.areas[0] || null;
    return keys.muni;
  }

  // Public hooks for the tree and the details panel (panel.js).
  window.coverageMap = {
    async hoverPath(path) {
      if (!map || !map.isStyleLoaded()) return;
      const key = keyAt(view.level, await locate(path));
      if (view.treeHover) setState(view.treeHover.level, view.treeHover.key, { hover: false });
      view.treeHover = key ? { level: view.level, key } : null;
      if (key) setState(view.level, key, { hover: true });
    },
    clearHover() {
      if (view.treeHover) setState(view.treeHover.level, view.treeHover.key, { hover: false });
      view.treeHover = null;
    },
    async showPath(path, { zoom = true, scroll = false } = {}) {
      if (!map) return;
      const keys = await locate(path);
      if (!keys) return;
      const depth = path.split('/').length;
      const level = depth === 1 ? 'uf' : (depth === 2 || !keys.muni) ? 'area' : 'muni';
      if (zoom && level !== view.level) await setLevel(level);
      const key = keyAt(view.level, keys);
      if (!key) return;
      select(view.level, key);
      if (zoom && bboxes[view.level][key]) {
        map.fitBounds(bboxes[view.level][key], { padding: 40, maxZoom: view.level === 'muni' ? 9 : 7 });
      }
      if (scroll) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },
  };

  async function start() {
    const res = await fetch('/geo/br-municipios.topo.json');
    if (!res.ok) {
      el.innerHTML = '<p class="empty">Map assets missing — run <code>uv run python scripts/build_geo.py</code>.</p>';
      return;
    }
    const topo = await res.json();
    const obj = topo.objects.municipios;
    const munis = topojson.feature(topo, obj).features.map((f) => ({
      type: 'Feature',
      properties: { key: f.properties.c, n: `${f.properties.n} (${f.properties.u})` },
      geometry: f.geometry,
    }));
    const ufs = merged(topo, obj.geometries, 'u', (k) => k);
    const areas = merged(topo, obj.geometries, 'a', (k) => {
      const [uf, ddd] = k.split('-');
      return `${uf} · DDD ${ddd}`;
    });

    map = new maplibregl.Map({
      container: el,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: 'bg', type: 'background', paint: { 'background-color': cssVar('--map-bg') } }],
      },
      bounds: BRAZIL,
      fitBoundsOptions: { padding: 12 },
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      boxZoom: false,  // Shift is for selecting (Shift-click adds to the basket)
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'Malhas: IBGE · DDD: kelvins/municipios-brasileiros' }));

    map.on('load', () => {
      map.addSource('muni', { type: 'geojson', data: features(munis, 'muni'), promoteId: 'key' });
      map.addSource('area', { type: 'geojson', data: features(areas, 'area'), promoteId: 'key' });
      map.addSource('uf', { type: 'geojson', data: features(ufs, 'uf'), promoteId: 'key' });
      map.addSource('area-lines', { type: 'geojson', data: topojson.mesh(topo, obj, (a, b) => a !== b && a.properties.a !== b.properties.a && a.properties.u === b.properties.u) });
      map.addSource('uf-lines', { type: 'geojson', data: topojson.mesh(topo, obj, (a, b) => a === b || a.properties.u !== b.properties.u) });
      for (const level of LEVELS) {
        map.addLayer({
          id: `fill-${level}`, type: 'fill', source: level, layout: { visibility: 'none' },
          paint: { 'fill-color': cssVar('--map-none'), 'fill-opacity': ['case', ['==', ['feature-state', 'i'], 1], 0.4, 0.92] },
        });
      }
      map.addLayer({ id: 'muni-lines', type: 'line', source: 'muni', minzoom: 5, layout: { visibility: 'none' },
        paint: { 'line-color': cssVar('--map-line'), 'line-width': 0.3 } });
      map.addLayer({ id: 'area-lines', type: 'line', source: 'area-lines',
        paint: { 'line-color': cssVar('--map-line'), 'line-width': 0.9 } });
      map.addLayer({ id: 'uf-lines', type: 'line', source: 'uf-lines',
        paint: { 'line-color': cssVar('--map-strong'), 'line-width': 1.3 } });
      for (const level of LEVELS) {
        map.addLayer({
          id: `hl-${level}`, type: 'line', source: level, layout: { visibility: 'none' },
          paint: {
            'line-color': ['case', ['boolean', ['feature-state', 'sel'], false], cssVar('--map-select'), cssVar('--text')],
            'line-width': ['case', ['boolean', ['feature-state', 'sel'], false], 2.6,
              ['boolean', ['feature-state', 'hover'], false], 1.6, 0],
          },
        });
        wire(level);
      }
      setLevel(view.level);
    });

    levelButtons.forEach((b) => b.addEventListener('click', () => setLevel(b.dataset.level)));
    metricEl.addEventListener('change', () => {
      view.metric = metricEl.value;
      writeHash();
      loadValues();
    });
  }

  start();
})();
