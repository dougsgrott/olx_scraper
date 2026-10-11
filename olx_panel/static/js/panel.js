// Tree, details panel and their links to the map (map.js exposes
// window.coverageMap on the Coverage page).

function daysParam() {
  const holder = document.querySelector('[data-days]');
  return holder ? holder.dataset.days : '';
}

function findNode(path) {
  return document.querySelector(`.node[data-path="${CSS.escape(path)}"]`);
}

function setOpen(node, open) {
  node.classList.toggle('open', open);
  const button = node.querySelector(':scope > .row .toggle');
  if (button) {
    button.textContent = open ? '▾' : '▸';
    button.setAttribute('aria-expanded', String(open));
  }
}

// Children load once, on first expansion; later toggles only show/hide.
async function openNode(node) {
  if (!node.dataset.loaded) {
    node.dataset.loaded = '1';
    const url = `/coverage/tree?parent=${encodeURIComponent(node.dataset.path)}&days=${daysParam()}`;
    await htmx.ajax('GET', url, { target: node.querySelector(':scope > .kids'), swap: 'innerHTML' });
  }
  setOpen(node, true);
}

function toggleNode(button) {
  const node = button.closest('.node');
  if (node.classList.contains('open')) setOpen(node, false);
  else openNode(node);
}

function selectRow(row) {
  document.querySelectorAll('.row.selected').forEach((r) => r.classList.remove('selected'));
  if (row) row.classList.add('selected');
}

// Expand every ancestor of `path` and scroll to it. The flat (filtered) list
// has no ancestors to expand, so reload the tree view with ?reveal=.
async function revealInTree(path) {
  const parts = path.split('/');
  for (let i = 1; i < parts.length; i++) {
    const node = findNode(parts.slice(0, i).join('/'));
    if (!node || document.querySelector('.tree.flat')) {
      location.href = `/coverage?days=${daysParam()}&reveal=${encodeURIComponent(path)}`;
      return;
    }
    await openNode(node);
  }
  const target = findNode(path);
  if (target) {
    const row = target.querySelector(':scope > .row');
    selectRow(row);
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function toggleRunRows(button, cellId) {
  const row = document.getElementById(cellId).parentElement;
  row.hidden = !row.hidden;
  button.textContent = row.hidden ? '▸' : '▾';
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('a.node-link, .crumbs a, a.detail-link');
  if (link) event.preventDefault();
  if (link && link.classList.contains('node-link')) {
    selectRow(link.closest('.row'));
    if (window.coverageMap) window.coverageMap.showPath(link.closest('.node').dataset.path, { zoom: false });
  }
  const action = event.target.closest('[data-action]');
  if (!action) return;
  if (action.dataset.action === 'reveal') revealInTree(action.dataset.path);
  if (action.dataset.action === 'map' && window.coverageMap) {
    window.coverageMap.showPath(action.dataset.path, { zoom: true, scroll: true });
  }
});

// Hovering a tree row outlines its polygon on the map.
document.addEventListener('mouseover', (event) => {
  const row = event.target.closest('.tree .row:not(.head)');
  if (row && window.coverageMap) window.coverageMap.hoverPath(row.parentElement.dataset.path);
});
document.addEventListener('mouseout', (event) => {
  if (event.target.closest('.tree') && !event.relatedTarget?.closest?.('.tree') && window.coverageMap) {
    window.coverageMap.clearHover();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const reveal = new URLSearchParams(location.search).get('reveal');
  if (reveal && findNode(reveal.split('/')[0])) revealInTree(reveal);
});

// -- selection basket (localStorage; travels to the server only as a plan) ---

const BASKET_KEY = 'olx-panel-basket';

window.basket = {
  paths() {
    try {
      return JSON.parse(localStorage.getItem(BASKET_KEY) || '[]');
    } catch (e) {
      return [];
    }
  },
  save(paths) {
    try {
      localStorage.setItem(BASKET_KEY, JSON.stringify([...new Set(paths)]));
    } catch (e) { /* private window: the selection just won't persist */ }
    renderBasket();
  },
  add(paths) { this.save(this.paths().concat(paths)); },
  remove(path) { this.save(this.paths().filter((p) => p !== path)); },
  has(path) { return this.paths().includes(path); },
  clear() { this.save([]); },
};

function renderBasket() {
  const bar = document.getElementById('basket');
  if (!bar) return;
  const paths = window.basket.paths();
  bar.hidden = paths.length === 0;
  document.getElementById('basket-count').textContent = paths.length;
  const list = document.getElementById('basket-list');
  list.replaceChildren(...paths.map((p) => {
    const li = document.createElement('li');
    const remove = document.createElement('button');
    remove.className = 'btn btn-small btn-ghost';
    remove.textContent = '×';
    remove.dataset.action = 'basket-remove';
    remove.dataset.path = p;
    li.append(remove, p.startsWith('=') ? ` ${p.slice(1)} (its own URL only)` : ` ${p}`);
    return li;
  }));
  document.querySelectorAll('input.pick').forEach((box) => {
    box.checked = paths.includes(box.dataset.path);
  });
}

function previewPaths(paths) {
  const form = document.getElementById('basket-form');
  if (!form) return;
  form.replaceChildren(...paths.map((p) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'path';
    input.value = p;
    return input;
  }));
  htmx.trigger(form, 'preview');
}

async function addFrom(url) {
  const res = await fetch(url);
  if (!res.ok) return;
  const data = await res.json();
  window.basket.add(data.paths);
  const note = [`${data.paths.length} scope(s) added`];
  if (data.skipped && data.skipped.length) note.push(`${data.skipped.length} off-tree URL(s) skipped`);
  if (data.offsets) note.push(`${data.offsets} resume offset(s) dropped`);
  flash(note.join(' · '));
}

function flash(message) {
  const bar = document.getElementById('basket');
  if (!bar) return;
  let note = bar.querySelector('.flash');
  if (!note) {
    note = document.createElement('span');
    note.className = 'flash muted small';
    bar.prepend(note);
  }
  note.textContent = message;
  bar.hidden = false;
  setTimeout(() => { note.textContent = ''; renderBasket(); }, 4000);
}

document.addEventListener('change', (event) => {
  const box = event.target.closest('input.pick');
  if (!box) return;
  if (box.checked) window.basket.add([box.dataset.path]);
  else window.basket.remove(box.dataset.path);
});

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]');
  if (!action) return;
  const days = action.dataset.days || daysParam();
  switch (action.dataset.action) {
    case 'basket-add': window.basket.add([action.dataset.path]); flash('Added to selection'); break;
    case 'basket-remove': window.basket.remove(action.dataset.path); break;
    case 'basket-clear': window.basket.clear(); break;
    case 'basket-list': {
      const list = document.getElementById('basket-list');
      list.hidden = !list.hidden;
      break;
    }
    case 'basket-preview': previewPaths(window.basket.paths()); break;
    case 'preview-one': previewPaths([action.dataset.path]); break;
    case 'basket-stale': addFrom(`/coverage/stale?days=${days}`); break;
    case 'basket-due': addFrom('/coverage/due'); break;
    case 'basket-import': addFrom('/coverage/import-run-config'); break;
    case 'basket-add-shown':
      window.basket.add([...document.querySelectorAll('.tree .node[data-path] > .row input.pick')]
        .map((b) => b.dataset.path));
      break;
    case 'copy-urls': {
      const text = document.getElementById('plan-urls');
      if (text && navigator.clipboard) navigator.clipboard.writeText(text.value).then(() => flash('URLs copied'));
      break;
    }
    default: break;
  }
});

document.addEventListener('htmx:afterSwap', renderBasket);
document.addEventListener('DOMContentLoaded', renderBasket);
