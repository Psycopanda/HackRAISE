// File-tree explorer (VS Code-like). Builds a nested tree from flat file names
// where "/" denotes folders. Folders are collapsible; files and folders expose
// a delete affordance. ".keep" placeholder files (which materialise otherwise
// empty folders) are hidden.

/**
 * @param {HTMLElement} container
 * @param {Array<{fileId:string,name:string,contextDirty?:boolean}>} files
 * @param {string|null} activeId
 * @param {{
 *   onOpen: (fileId:string)=>void,
 *   onDelete: (path:string)=>void,
 *   onToggle: (path:string)=>void,
 *   collapsed: Set<string>,
 * }} handlers
 */
export function renderExplorer(container, files, activeId, handlers) {
  container.innerHTML = "";
  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "explorer__empty";
    empty.textContent = "Aucun fichier";
    container.appendChild(empty);
    return;
  }
  const root = buildTree(files);
  if (!root.dirs.size && !root.files.length) {
    const empty = document.createElement("div");
    empty.className = "explorer__empty";
    empty.textContent = "Aucun fichier";
    container.appendChild(empty);
    return;
  }
  container.appendChild(renderNode(root, activeId, handlers));
}

function buildTree(files) {
  const root = { dirs: new Map(), files: [] };
  for (const file of files) {
    const parts = String(file.name).split("/").filter(Boolean);
    if (parts.length === 0) continue;
    let node = root;
    let path = "";
    for (let i = 0; i < parts.length - 1; i++) {
      path = path ? `${path}/${parts[i]}` : parts[i];
      if (!node.dirs.has(parts[i])) {
        node.dirs.set(parts[i], { dirs: new Map(), files: [], path });
      }
      node = node.dirs.get(parts[i]);
    }
    const leaf = parts[parts.length - 1];
    if (leaf === ".keep") continue; // placeholder: keep the folder, hide the file
    node.files.push({ ...file, label: leaf });
  }
  return root;
}

function renderNode(node, activeId, handlers) {
  const ul = document.createElement("ul");
  ul.className = "tree";

  for (const [name, dir] of [...node.dirs.entries()].sort((a, b) =>
    a[0].localeCompare(b[0])
  )) {
    const li = document.createElement("li");
    const isCollapsed = handlers.collapsed.has(dir.path);

    const row = document.createElement("div");
    row.className = "tree__row tree__row--dir";

    const arrow = document.createElement("span");
    arrow.className = "tree__arrow" + (isCollapsed ? " tree__arrow--collapsed" : "");
    arrow.textContent = "▾";

    const label = document.createElement("span");
    label.className = "tree__foldername";
    label.textContent = name;

    row.append(arrow, label, makeDeleteButton(dir.path, handlers.onDelete));
    row.addEventListener("click", () => handlers.onToggle(dir.path));
    li.appendChild(row);

    if (!isCollapsed) li.appendChild(renderNode(dir, activeId, handlers));
    ul.appendChild(li);
  }

  for (const file of node.files.sort((a, b) => a.label.localeCompare(b.label))) {
    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = "tree__row tree__row--file";
    if (file.fileId === activeId) row.classList.add("tree__row--active");
    row.title = file.name;

    const label = document.createElement("span");
    label.className = "tree__filename";
    label.textContent = file.label;
    row.appendChild(label);

    if (file.contextDirty) {
      const dot = document.createElement("span");
      dot.className = "tree__dirty";
      row.appendChild(dot);
    }
    row.appendChild(makeDeleteButton(file.name, handlers.onDelete));

    row.addEventListener("click", () => handlers.onOpen(file.fileId));
    li.appendChild(row);
    ul.appendChild(li);
  }

  return ul;
}

function makeDeleteButton(path, onDelete) {
  const btn = document.createElement("button");
  btn.className = "tree__delete";
  btn.title = "Supprimer";
  btn.setAttribute("aria-label", "Supprimer");
  btn.textContent = "×";
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    onDelete(path);
  });
  return btn;
}
