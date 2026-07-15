"use strict";
/* tododo web client — full-featured HTML+JS mirror of the pygame GUI.
 * Talks to the local data server (see api.raml). Same origin, no auth. */

// ------------------------------------------------------------------ API
const API = {
  async _req(method, path, { body, query } = {}) {
    let url = path;
    if (query) {
      const qs = Object.entries(query)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join("&");
      if (qs) url += `?${qs}`;
    }
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      State.online = false;
      throw new Error("server unreachable");
    }
    State.online = true;
    const text = await resp.text();
    const data = text ? JSON.parse(text) : null;
    if (resp.status >= 400) {
      const err = new Error((data && data.error) || `HTTP ${resp.status}`);
      err.status = resp.status;
      err.payload = data;
      throw err;
    }
    return data;
  },
  user: () => API._req("GET", "/user"),
  getItem: (id) => API._req("GET", "/item", { query: { id } }),
  listItems: (filters) => API._req("POST", "/item/list", { body: filters || {} }).then(r => r.items || []),
  createItem: (fields) => API._req("POST", "/item", { body: fields }),
  updateItem: (id, fields) => API._req("PUT", "/item", { body: { id, ...fields } }),
  deleteItem: (id) => API._req("DELETE", "/item", { query: { id } }),
  lockItem: (id) => API._req("POST", "/item/lock", { body: { id } }),
  unlockItem: (id) => API._req("DELETE", "/item/lock", { query: { id } }),
  itemHistory: (id) => API._req("GET", "/item/history", { query: { id } }).then(r => r.events || []),
  getBoard: (name) => API._req("GET", "/board", { query: { name } }),
  listBoards: () => API._req("GET", "/board/list").then(r => r.boards || []),
  createBoard: (name, columns) => API._req("POST", "/board", { body: { name, columns } }),
  updateBoard: (name, patch) => API._req("PUT", "/board", { body: { name, ...patch } }),
  deleteBoard: (name) => API._req("DELETE", "/board", { query: { name } }),
  getKeybindings: () => API._req("GET", "/keybindings").then(r => r.bindings || {}),
  updateKeybinding: (action, value) => API._req("PUT", "/keybindings", { body: { action, value } }).then(r => r.bindings || {}),
  getSettings: () => API._req("GET", "/settings").then(r => r.settings || {}),
  updateSetting: (key, value) => API._req("PUT", "/settings", { body: { key, value } }).then(r => r.settings || {}),
};

// ------------------------------------------------------------------ state
const State = {
  online: false,
  user: null,          // { user, email, github }
  actor: "",           // github login (lock owner id)
  boards: [],          // [{name, columns}]
  boardName: "",
  columns: [],
  items: [],           // raw item dicts
  selectedId: null,
  search: "",
  keys: {},
  settings: {},
  modalOpen: false,
};

// value of a provenance field
const fv = (item, key) => {
  const f = item[key];
  return f && typeof f === "object" ? (f.value || "") : "";
};
const lockHolder = (item) => (item.lock && item.lock.value) || null;
const lockedByOther = (item) => {
  const h = lockHolder(item);
  return h && h !== State.actor;
};

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  Object.entries(props).forEach(([k, v]) => {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v != null) n.setAttribute(k, v);
  });
  kids.flat().forEach(k => n.append(k?.nodeType ? k : document.createTextNode(k ?? "")));
  return n;
};

function toast(msg, isErr = false) {
  const t = el("div", { class: "toast" + (isErr ? " err" : ""), text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

function initials(s) {
  if (!s) return "?";
  const parts = s.replace(/[<>@].*/, "").trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return s[0].toUpperCase();
  return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
}
function colorFor(s) {
  let h = 0;
  for (const c of (s || "")) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 55% 42%)`;
}

// timestamp formatting: relative within 24h, else absolute
function fmtTime(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (isNaN(t)) return iso;
  const diff = (Date.now() - t.getTime()) / 1000;
  if (diff >= 0 && diff < 86400) {
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    const h = Math.floor(diff / 3600), m = Math.floor((diff % 3600) / 60);
    return m ? `${h}h ${m}m ago` : `${h}h ago`;
  }
  return t.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}
function newestEdit(item) {
  let best = "";
  for (const k of ["title", "description", "start", "end", "column", "board", "assigned_to", "report_to"]) {
    const f = item[k];
    if (f && typeof f === "object" && (f.last_edited_at || "") > best) best = f.last_edited_at;
  }
  return best;
}
function newestEditor(item) {
  let bestAt = "", bestBy = "";
  for (const k of ["title", "column", "board", "description"]) {
    const f = item[k];
    if (f && typeof f === "object" && (f.last_edited_at || "") >= bestAt) {
      bestAt = f.last_edited_at || ""; bestBy = f.last_edited_by || "";
    }
  }
  return bestBy;
}

// ------------------------------------------------------------------ status bar
function setStatus(text, ok = true) {
  $("#status-text").textContent = text;
  const dot = $("#status-conn");
  dot.classList.toggle("ok", ok && State.online);
}

// ================================================================== data ops
async function refreshUser() {
  State.user = await API.user();
  State.actor = State.user.github || State.user.user || "someone";
  const badge = $("#user-badge");
  badge.textContent = initials(State.user.user || State.actor);
  badge.style.background = colorFor(State.actor);
  badge.title = `${State.user.user || ""} <${State.user.email || ""}>` +
    (State.user.github ? ` (@${State.user.github})` : "");
}

async function refreshBoards() {
  State.boards = await API.listBoards();
  if (!State.boards.length) return;
  if (!State.boardName || !State.boards.find(b => b.name === State.boardName)) {
    State.boardName = localStorage.getItem("tododo.board") || State.boards[0].name;
    if (!State.boards.find(b => b.name === State.boardName)) State.boardName = State.boards[0].name;
  }
  renderBoardSelect();
}

async function refreshItems() {
  if (!State.boardName) { State.items = []; State.columns = []; renderBoard(); return; }
  const board = State.boards.find(b => b.name === State.boardName);
  State.columns = board ? board.columns : [];
  State.items = await API.listItems({ in_board: State.boardName });
  renderBoard();
  setStatus(`${State.items.length} items · ${State.boardName}`, true);
}

async function reloadAll() {
  try {
    await refreshBoards();
    await refreshItems();
    setStatus(`${State.items.length} items · ${State.boardName}`, true);
  } catch (e) {
    setStatus("offline — " + e.message, false);
  }
}

// merge a fresh copy of one item into State.items
function patchItem(updated) {
  if (!updated || !updated.id) return;
  const i = State.items.findIndex(x => x.id === updated.id);
  if (i >= 0) State.items[i] = updated; else State.items.push(updated);
}

// ================================================================== board render
function renderBoardSelect() {
  const sel = $("#board-select");
  sel.innerHTML = "";
  State.boards.forEach(b => {
    sel.append(el("option", { value: b.name, ...(b.name === State.boardName ? { selected: "" } : {}) }, b.name));
  });
}

function matchesSearch(item) {
  if (!State.search) return true;
  const q = State.search.toLowerCase();
  return fv(item, "title").toLowerCase().includes(q) ||
    fv(item, "description").toLowerCase().includes(q) ||
    fv(item, "assigned_to").toLowerCase().includes(q) ||
    fv(item, "report_to").toLowerCase().includes(q);
}

function renderBoard() {
  const board = $("#board");
  board.innerHTML = "";
  if (!State.columns.length) {
    board.append(el("div", { class: "empty", text: "No columns. Use Edit to add some, or create a board." }));
    return;
  }
  const showDesc = State.settings.descriptions === "all";
  const showTs = State.settings.timestamps === "all";
  for (const col of State.columns) {
    const items = State.items
      .filter(it => fv(it, "column") === col && matchesSearch(it));
    const colEl = el("section", { class: "column", "data-col": col });
    colEl.append(el("div", { class: "column-head" },
      el("span", { text: col }),
      el("span", { class: "column-count", text: String(items.length) })
    ));
    const body = el("div", { class: "column-body" });
    // drag & drop targets
    colEl.addEventListener("dragover", e => { e.preventDefault(); colEl.classList.add("dragover"); });
    colEl.addEventListener("dragleave", () => colEl.classList.remove("dragover"));
    colEl.addEventListener("drop", async e => {
      e.preventDefault();
      colEl.classList.remove("dragover");
      const id = e.dataTransfer.getData("text/plain");
      if (id) await moveItem(id, col);
    });
    for (const it of items) body.append(renderCard(it, showDesc, showTs));
    body.append(el("button", { class: "column-add", text: "+ Add", onclick: () => openItemEditor(null, col) }));
    colEl.append(body);
    board.append(colEl);
  }
}

function renderCard(item, showDesc, showTs) {
  const tpl = $("#tpl-card").content.firstElementChild.cloneNode(true);
  tpl.dataset.id = item.id;
  if (item.id === State.selectedId) tpl.classList.add("selected");
  if (lockedByOther(item)) tpl.classList.add("locked-other");

  $(".card-title", tpl).textContent = fv(item, "title") || "(untitled)";

  const desc = fv(item, "description");
  const descEl = $(".card-desc", tpl);
  if (desc && (showDesc || item.id === State.selectedId)) {
    descEl.textContent = desc;
    const max = Number(State.settings.max_description_height || 0);
    if (max > 0) descEl.style.maxHeight = max + "px";
  } else descEl.remove();

  // lock indicator
  const lockEl = $(".card-lock", tpl);
  const holder = lockHolder(item);
  if (holder) { lockEl.classList.add("show"); lockEl.title = `locked by ${holder}`; }

  // avatar (monogram)
  if (State.settings.git_avatars !== false) {
    const who = newestEditor(item);
    if (who) {
      const av = $(".card-avatar", tpl);
      av.style.display = "flex";
      av.style.background = colorFor(who);
      av.textContent = initials(who);
      av.title = "last edited by " + who;
    }
  }

  // meta chips
  const meta = $(".card-meta", tpl);
  const assignee = fv(item, "assigned_to");
  if (assignee) meta.append(el("span", { class: "chip", text: "→ " + assignee }));
  const due = fv(item, "end");
  if (due) {
    const overdue = new Date(due) < new Date();
    meta.append(el("span", { class: "chip " + (overdue ? "overdue" : "due"), text: "due " + fmtTime(due) }));
  }
  if (showTs || item.id === State.selectedId) {
    const t = newestEdit(item);
    if (t) meta.append(el("span", { class: "chip", text: fmtTime(t) }));
  }
  if (!meta.children.length) meta.remove();

  // interactions
  tpl.addEventListener("click", () => selectCard(item.id));
  tpl.addEventListener("dblclick", () => openItemEditor(item.id));
  tpl.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); openItemEditor(item.id); }
  });
  tpl.addEventListener("dragstart", e => {
    e.dataTransfer.setData("text/plain", item.id);
    e.dataTransfer.effectAllowed = "move";
    tpl.classList.add("dragging");
    selectCard(item.id);
  });
  tpl.addEventListener("dragend", () => tpl.classList.remove("dragging"));
  return tpl;
}

function selectCard(id) {
  State.selectedId = id;
  renderBoard();
  const node = $(`.card[data-id="${id}"]`);
  if (node) node.focus({ preventScroll: true });
}

function selectedItem() {
  return State.items.find(it => it.id === State.selectedId) || null;
}

// ================================================================== mutations
async function withLock(id, fn) {
  const r = await API.lockItem(id);
  if (!r.locked) { toast(`locked by ${r.holder || "someone"}`, true); return false; }
  try { return await fn(); }
  finally { try { await API.unlockItem(id); } catch {} }
}

async function moveItem(id, column) {
  const item = State.items.find(x => x.id === id);
  if (!item || fv(item, "column") === column) return;
  try {
    await withLock(id, async () => {
      const updated = await API.updateItem(id, { column });
      patchItem(updated);
    });
    renderBoard();
  } catch (e) { toast("move failed: " + e.message, true); }
}

async function moveSelectedColumn(delta) {
  const item = selectedItem();
  if (!item) return;
  const idx = State.columns.indexOf(fv(item, "column"));
  if (idx < 0) return;
  const next = Math.max(0, Math.min(State.columns.length - 1, idx + delta));
  if (next !== idx) await moveItem(item.id, State.columns[next]);
}

function selectVertical(delta) {
  const item = selectedItem();
  const col = item ? fv(item, "column") : State.columns[0];
  const inCol = State.items.filter(it => fv(it, "column") === col && matchesSearch(it));
  if (!inCol.length) return;
  let idx = item ? inCol.findIndex(x => x.id === item.id) : -1;
  idx = Math.max(0, Math.min(inCol.length - 1, idx + delta));
  selectCard(inCol[idx].id);
}

async function deleteSelected() {
  const item = selectedItem();
  if (!item) return;
  confirmDialog(`Delete “${fv(item, "title") || "untitled"}”?`, async () => {
    try {
      await withLock(item.id, async () => { await API.deleteItem(item.id); });
      State.items = State.items.filter(x => x.id !== item.id);
      State.selectedId = null;
      renderBoard();
      toast("deleted");
    } catch (e) { toast("delete failed: " + e.message, true); }
  });
}

// ================================================================== modals
function openModal(node) {
  const ov = $("#overlay");
  ov.innerHTML = "";
  ov.append(node);
  ov.classList.remove("hidden");
  State.modalOpen = true;
  const focusable = node.querySelector("input,textarea,select,button");
  if (focusable) setTimeout(() => focusable.focus(), 20);
}
function closeModal() {
  $("#overlay").classList.add("hidden");
  $("#overlay").innerHTML = "";
  State.modalOpen = false;
}
$("#overlay").addEventListener("mousedown", e => { if (e.target.id === "overlay") closeModal(); });

function modalShell(title, bodyKids, footKids, wide = false) {
  return el("div", { class: "modal" + (wide ? " wide" : "") },
    el("div", { class: "modal-head" },
      el("span", { text: title }),
      el("button", { class: "close-x", text: "✕", onclick: closeModal })
    ),
    el("div", { class: "modal-body" }, bodyKids),
    footKids ? el("div", { class: "modal-foot" }, footKids) : ""
  );
}

function confirmDialog(msg, onYes) {
  const yes = el("button", { class: "danger", text: "Confirm", onclick: () => { closeModal(); onYes(); } });
  const no = el("button", { text: "Cancel", onclick: closeModal });
  openModal(modalShell("Confirm", [el("div", { text: msg })], [no, yes]));
}

// ---- item editor (create or edit) --------------------------------------
async function openItemEditor(id, defaultColumn) {
  let item = id ? State.items.find(x => x.id === id) : null;
  if (id && !item) { try { item = await API.getItem(id); } catch {} }
  const isNew = !item;

  // acquire lock for existing items
  let holdsLock = false;
  if (item && lockedByOther(item)) {
    toast(`locked by ${lockHolder(item)} — read-only`, true);
  } else if (item) {
    try { const r = await API.lockItem(item.id); holdsLock = r.locked; if (!r.locked) toast(`locked by ${r.holder}`, true); }
    catch {}
  }

  const get = (k) => item ? fv(item, k) : "";
  const readonly = item && !holdsLock ? { readonly: "" } : {};

  const inTitle = el("input", { value: get("title"), placeholder: "Title", ...readonly });
  const inDesc = el("textarea", { placeholder: "Description", ...readonly }, get("description"));
  const inCol = el("select", readonly);
  (State.columns.length ? State.columns : [defaultColumn || "Todo"]).forEach(c =>
    inCol.append(el("option", { value: c, ...(c === (get("column") || defaultColumn) ? { selected: "" } : {}) }, c)));
  const inStart = el("input", { type: "datetime-local", value: isoToLocal(get("start")), ...readonly });
  const inEnd = el("input", { type: "datetime-local", value: isoToLocal(get("end")), ...readonly });
  const inAssign = el("input", { value: get("assigned_to"), placeholder: "assignee", ...readonly });
  const inReport = el("input", { value: get("report_to"), placeholder: "reporter", ...readonly });

  const field = (label, ctl, prov) => el("div", { class: "field" },
    el("label", { text: label }), ctl,
    prov ? el("div", { class: "prov", text: prov }) : "");

  const provOf = (k) => {
    if (!item) return "";
    const f = item[k];
    if (!f || !f.last_edited_by) return "";
    return `last edited by ${f.last_edited_by} · ${fmtTime(f.last_edited_at)}`;
  };

  const body = [
    field("Title", inTitle, provOf("title")),
    field("Description", inDesc, provOf("description")),
    el("div", { class: "row2" }, field("Column", inCol), field("Assignee", inAssign, provOf("assigned_to"))),
    el("div", { class: "row2" }, field("Start", inStart), field("Due", inEnd, provOf("end"))),
    field("Reporter", inReport, provOf("report_to")),
  ];

  const foot = [];
  if (!isNew) {
    foot.push(el("button", { text: "History", onclick: () => { releaseIfHeld(); openHistory(item.id); } }));
    foot.push(el("button", { text: "YAML", onclick: () => openYaml(item.id) }));
    foot.push(el("button", { class: "danger", text: "Delete", onclick: () => { releaseIfHeld(); State.selectedId = item.id; deleteSelected(); } }));
  }
  foot.push(el("button", { text: "Cancel", onclick: () => { releaseIfHeld(); closeModal(); } }));
  const saveBtn = el("button", { class: "primary", text: isNew ? "Create" : "Save" });

  async function releaseIfHeld() { if (holdsLock && item) { try { await API.unlockItem(item.id); } catch {} holdsLock = false; } }

  saveBtn.addEventListener("click", async () => {
    const vals = {
      title: inTitle.value,
      description: inDesc.value,
      column: inCol.value,
      start: localToIso(inStart.value),
      end: localToIso(inEnd.value),
      assigned_to: inAssign.value,
      report_to: inReport.value,
    };
    try {
      if (isNew) {
        const created = await API.createItem({ board: State.boardName, ...vals });
        patchItem(created);
        State.selectedId = created.id;
      } else {
        if (!holdsLock) { toast("no lock — cannot save", true); return; }
        const updated = await API.updateItem(item.id, vals);
        patchItem(updated);
        await releaseIfHeld();
      }
      closeModal();
      renderBoard();
      toast(isNew ? "created" : "saved");
    } catch (e) { toast("save failed: " + e.message, true); }
  });
  if (!(item && !holdsLock)) foot.push(saveBtn);

  openModal(modalShell(isNew ? "New item" : "Edit item", body, foot));
}

function isoToLocal(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function localToIso(local) {
  if (!local) return "";
  const d = new Date(local);
  return isNaN(d) ? "" : d.toISOString();
}

// ---- history -----------------------------------------------------------
async function openHistory(id) {
  let events = [];
  try { events = await API.itemHistory(id); } catch (e) { toast(e.message, true); }
  const list = el("div", { class: "hist-list" });
  if (!events.length) list.append(el("div", { class: "prov", text: "No history yet." }));
  events.forEach(ev => list.append(el("div", { class: "hist-row" },
    el("span", { class: "h-hash", text: (ev.commit || "").slice(0, 7) }),
    el("span", { text: ev.name || ev.email || "?" }),
    el("span", { class: "h-when", text: fmtTime(ev.timestamp) })
  )));
  openModal(modalShell("Item history", [list], [el("button", { text: "Close", onclick: closeModal })]));
}

// ---- raw yaml/json -----------------------------------------------------
async function openYaml(id) {
  let item = State.items.find(x => x.id === id);
  try { item = await API.getItem(id) || item; } catch {}
  const text = toYaml(item);
  openModal(modalShell("Raw item", [el("pre", { class: "yaml", text })],
    [el("button", { text: "Close", onclick: closeModal })], true));
}
// tiny YAML-ish serializer (read-only display)
function toYaml(obj, indent = 0) {
  const pad = "  ".repeat(indent);
  if (obj === null) return "null";
  if (typeof obj !== "object") return String(obj);
  return Object.entries(obj).map(([k, v]) => {
    if (v && typeof v === "object") return `${pad}${k}:\n${toYaml(v, indent + 1)}`;
    return `${pad}${k}: ${v === "" ? '""' : v}`;
  }).join("\n");
}

// ---- board editor (rename / columns) -----------------------------------
async function openBoardEditor() {
  if (!State.boardName) return;
  const board = State.boards.find(b => b.name === State.boardName);
  // grab coarse board lock
  try { const r = await API._req("POST", "/board/lock", { body: { name: board.name } });
        if (!r.locked) { toast(`board locked by ${r.holder}`, true); return; } } catch {}

  const nameInput = el("input", { value: board.name });
  const colWrap = el("div", { class: "col-editor" });
  function addColRow(val = "") {
    const inp = el("input", { value: val, placeholder: "column name" });
    const row = el("div", { class: "col-row" },
      el("span", { class: "drag-handle", text: "≡" }), inp,
      el("button", { class: "danger", text: "✕", onclick: () => row.remove() }));
    colWrap.append(row);
  }
  board.columns.forEach(c => addColRow(c));

  const body = [
    el("div", { class: "field" }, el("label", { text: "Board name" }), nameInput),
    el("div", { class: "field" }, el("label", { text: "Columns" }), colWrap,
      el("button", { text: "+ Column", onclick: () => addColRow() })),
  ];
  async function releaseBoardLock() { try { await API._req("DELETE", "/board/lock", { query: { name: board.name } }); } catch {} }

  const del = el("button", { class: "danger", text: "Delete board", onclick: () => {
    confirmDialog(`Delete board “${board.name}” and keep its items orphaned?`, async () => {
      await releaseBoardLock();
      try { await API.deleteBoard(board.name); State.boardName = ""; await reloadAll(); toast("board deleted"); }
      catch (e) { toast(e.message, true); }
    });
  }});
  const cancel = el("button", { text: "Cancel", onclick: async () => { await releaseBoardLock(); closeModal(); } });
  const save = el("button", { class: "primary", text: "Save", onclick: async () => {
    const cols = $$(".col-row input", colWrap).map(i => i.value.trim()).filter(Boolean);
    const patch = { columns: cols };
    if (nameInput.value.trim() && nameInput.value.trim() !== board.name) patch.new_name = nameInput.value.trim();
    try {
      const updated = await API.updateBoard(board.name, patch);
      await releaseBoardLock();
      State.boardName = updated.name;
      localStorage.setItem("tododo.board", updated.name);
      closeModal();
      await reloadAll();
      toast("board saved");
    } catch (e) { toast("save failed: " + e.message, true); }
  }});

  openModal(modalShell("Edit board", body, [del, cancel, save]));
}

async function openNewBoard() {
  const nameInput = el("input", { placeholder: "Board name" });
  const colsInput = el("input", { value: "Todo, Doing, Done", placeholder: "comma-separated columns" });
  const body = [
    el("div", { class: "field" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field" }, el("label", { text: "Columns" }), colsInput),
  ];
  const create = el("button", { class: "primary", text: "Create", onclick: async () => {
    const name = nameInput.value.trim();
    if (!name) { toast("name required", true); return; }
    const cols = colsInput.value.split(",").map(s => s.trim()).filter(Boolean);
    try {
      await API.createBoard(name, cols.length ? cols : ["Todo", "Doing", "Done"]);
      State.boardName = name;
      localStorage.setItem("tododo.board", name);
      closeModal();
      await reloadAll();
      toast("board created");
    } catch (e) { toast(e.message, true); }
  }});
  openModal(modalShell("New board", body, [el("button", { text: "Cancel", onclick: closeModal }), create]));
}

// ---- settings ----------------------------------------------------------
async function openSettings() {
  let settings = {};
  try { settings = await API.getSettings(); } catch (e) { toast(e.message, true); return; }
  const list = el("div", { class: "kv-list" });
  for (const [key, val] of Object.entries(settings)) {
    let ctl;
    if (typeof val === "boolean") {
      ctl = el("input", { type: "checkbox", ...(val ? { checked: "" } : {}) });
      ctl._get = () => ctl.checked;
    } else if (typeof val === "number") {
      ctl = el("input", { type: "number", value: String(val), step: "any" });
      ctl._get = () => Number(ctl.value);
    } else {
      ctl = el("input", { value: val == null ? "" : String(val) });
      ctl._get = () => ctl.value;
    }
    ctl.addEventListener("change", async () => {
      try { State.settings = await API.updateSetting(key, ctl._get()); toast("saved " + key); renderBoard(); }
      catch (e) { toast(e.message, true); }
    });
    list.append(el("div", { class: "kv-row" },
      el("label", { text: key }), el("span", { class: "ctl" }, ctl)));
  }
  openModal(modalShell("Settings", [list], [el("button", { text: "Close", onclick: closeModal })], true));
}

// ---- keybindings -------------------------------------------------------
async function openKeybindings() {
  let binds = {};
  try { binds = await API.getKeybindings(); } catch (e) { toast(e.message, true); return; }
  const list = el("div", { class: "kv-list" });
  for (const [action, key] of Object.entries(binds)) {
    const inp = el("input", { value: key });
    inp.addEventListener("change", async () => {
      try { State.keys = await API.updateKeybinding(action, inp.value.trim()); toast("bound " + action); }
      catch (e) { toast(e.message, true); }
    });
    list.append(el("div", { class: "kv-row" },
      el("label", { text: action }), el("span", { class: "ctl" }, inp)));
  }
  openModal(modalShell("Keybindings", [list], [el("button", { text: "Close", onclick: closeModal })], true));
}

// ================================================================== command palette
const ACTIONS = [
  { id: "create", label: "New item", run: () => openItemEditor(null) },
  { id: "delete", label: "Delete selected item", run: deleteSelected },
  { id: "move_left", label: "Move item left", run: () => moveSelectedColumn(-1) },
  { id: "move_right", label: "Move item right", run: () => moveSelectedColumn(1) },
  { id: "move_up", label: "Select previous item", run: () => selectVertical(-1) },
  { id: "move_down", label: "Select next item", run: () => selectVertical(1) },
  { id: "view_history", label: "View item history", run: () => selectedItem() && openHistory(selectedItem().id) },
  { id: "view_yaml", label: "View item YAML", run: () => selectedItem() && openYaml(selectedItem().id) },
  { id: "relationships", label: "Edit item / relationships", run: () => selectedItem() && openItemEditor(selectedItem().id) },
  { id: "due_date", label: "Edit item (set due date)", run: () => selectedItem() && openItemEditor(selectedItem().id) },
  { id: "new_board", label: "New board", run: openNewBoard },
  { id: "switch_board", label: "Switch board", run: () => $("#board-select").focus() },
  { id: "search", label: "Search items", run: () => $("#search").focus() },
  { id: "keybindings", label: "Edit keybindings", run: openKeybindings },
  { id: "themes", label: "Settings", run: openSettings },
  { id: "select_column", label: "Refresh board", run: reloadAll },
];

function openPalette() {
  const input = el("input", { placeholder: "Type a command…" });
  const listEl = el("div", { class: "palette-list" });
  const pal = el("div", { class: "palette" }, input, listEl);
  let active = 0, filtered = ACTIONS.slice();

  const render = () => {
    listEl.innerHTML = "";
    filtered.forEach((a, i) => {
      const key = State.keys[a.id];
      const row = el("div", { class: "palette-item" + (i === active ? " active" : ""), onclick: () => run(a) },
        el("span", { text: a.label }),
        key ? el("span", { class: "kbd", text: "Ctrl+" + key }) : "");
      listEl.append(row);
    });
  };
  const run = (a) => { closeModal(); a.run(); };
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase();
    filtered = ACTIONS.filter(a => a.label.toLowerCase().includes(q) || a.id.includes(q));
    active = 0; render();
  });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { active = Math.min(filtered.length - 1, active + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
    else if (e.key === "Enter") { if (filtered[active]) run(filtered[active]); e.preventDefault(); }
    else if (e.key === "Escape") closeModal();
  });
  render();
  const ov = $("#overlay");
  ov.innerHTML = ""; ov.append(pal); ov.classList.remove("hidden"); State.modalOpen = true;
  setTimeout(() => input.focus(), 20);
}

// ================================================================== keyboard
// normalize a browser KeyboardEvent to a pygame-style key name
function eventKeyName(e) {
  const k = e.key;
  const map = { " ": "space", "ArrowLeft": "left", "ArrowRight": "right", "ArrowUp": "up",
    "ArrowDown": "down", "Enter": "return", "Escape": "escape", "Delete": "delete", "Backspace": "backspace" };
  if (map[k]) return map[k];
  if (k.length === 1) return k.toLowerCase();
  return k.toLowerCase();
}

document.addEventListener("keydown", e => {
  const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

  // Escape closes modals
  if (e.key === "Escape" && State.modalOpen) { closeModal(); return; }

  const name = eventKeyName(e);

  // Ctrl + open_palette
  if (e.ctrlKey && name === (State.keys.open_palette || "space")) {
    e.preventDefault(); if (!State.modalOpen) openPalette(); return;
  }
  if (State.modalOpen) return;

  // Ctrl + <action key>
  if (e.ctrlKey && !e.altKey && !e.metaKey) {
    for (const a of ACTIONS) {
      if (State.keys[a.id] && name === State.keys[a.id]) { e.preventDefault(); a.run(); return; }
    }
  }

  if (inField) return;

  // bare arrows for navigation when a card has focus / selection exists
  if (!e.ctrlKey) {
    if (name === "up") { selectVertical(-1); e.preventDefault(); }
    else if (name === "down") { selectVertical(1); e.preventDefault(); }
    else if (name === "left") { selectCardHorizontal(-1); e.preventDefault(); }
    else if (name === "right") { selectCardHorizontal(1); e.preventDefault(); }
    else if (name === "return" && selectedItem()) { openItemEditor(State.selectedId); e.preventDefault(); }
    else if (name === (State.keys.delete || "d") && selectedItem()) { deleteSelected(); e.preventDefault(); }
    else if (name === (State.keys.create || "n")) { openItemEditor(null); e.preventDefault(); }
  }
});

function selectCardHorizontal(delta) {
  const item = selectedItem();
  const col = item ? fv(item, "column") : State.columns[0];
  const idx = State.columns.indexOf(col);
  const nextCol = State.columns[Math.max(0, Math.min(State.columns.length - 1, idx + delta))];
  const inCol = State.items.filter(it => fv(it, "column") === nextCol && matchesSearch(it));
  if (inCol.length) selectCard(inCol[0].id);
}

// ================================================================== wiring
function wireTopbar() {
  $("#board-select").addEventListener("change", async e => {
    State.boardName = e.target.value;
    localStorage.setItem("tododo.board", State.boardName);
    State.selectedId = null;
    await refreshItems();
  });
  $("#new-board-btn").addEventListener("click", openNewBoard);
  $("#edit-board-btn").addEventListener("click", openBoardEditor);
  $("#new-item-btn").addEventListener("click", () => openItemEditor(null));
  $("#settings-btn").addEventListener("click", openSettings);
  $("#keys-btn").addEventListener("click", openKeybindings);
  let searchTimer;
  $("#search").addEventListener("input", e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { State.search = e.target.value.trim(); renderBoard(); }, 120);
  });
}

// live refresh — the server reloads from git in the background; poll to see peers' edits
let pollTimer = null;
function startPolling() {
  const tick = async () => {
    if (!State.modalOpen && !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
      try { await refreshItems(); } catch { setStatus("offline", false); }
    }
    pollTimer = setTimeout(tick, 3000);
  };
  pollTimer = setTimeout(tick, 3000);
}

async function boot() {
  wireTopbar();
  try {
    await refreshUser();
    State.keys = await API.getKeybindings();
    State.settings = await API.getSettings();
    await refreshBoards();
    await refreshItems();
    setStatus(`${State.items.length} items · ${State.boardName}`, true);
  } catch (e) {
    setStatus("cannot reach server — is `python -m tododo.server` running? (" + e.message + ")", false);
  }
  startPolling();
}
boot();
