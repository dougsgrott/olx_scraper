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
