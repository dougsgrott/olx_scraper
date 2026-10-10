// Tree expand/collapse: the first click also loads the children (hx-trigger
// "click once"); later clicks only toggle visibility.
function toggleNode(button) {
  const node = button.closest('.node');
  const open = node.classList.toggle('open');
  button.textContent = open ? '▾' : '▸';
  button.setAttribute('aria-expanded', String(open));
}

function selectRow(link) {
  document.querySelectorAll('.row.selected').forEach((r) => r.classList.remove('selected'));
  link.closest('.row').classList.add('selected');
}

function toggleRunRows(button, cellId) {
  const row = document.getElementById(cellId).parentElement;
  row.hidden = !row.hidden;
  button.textContent = row.hidden ? '▸' : '▾';
}

document.addEventListener('click', (event) => {
  if (event.target.closest('a.node-link, .crumbs a')) event.preventDefault();
});
