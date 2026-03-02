"""
config_viewer/run.py
====================
Ingests one or more plugin JSON files from an input folder, analyses every
config variant inside each file to discover all possible key-paths and value
types, then emits a single self-contained HTML viewer where you can switch
between plugins.

Input format
------------
Each file must contain a JSON array of config objects (same format as
__nexxe_grid_configs_debug.json).  The file name becomes the plugin tab label:
    input-date.json       -> "input-date"
    nexxe.json            -> "nexxe"
    supplier-widget.json  -> "supplier-widget"

Usage
-----
    # scan the default ./input/ folder
    python run.py

    # scan a custom folder
    python run.py --input /path/to/folder

    # point at specific files
    python run.py --files a.json b.json c.json

    # terminal-only (skip HTML)
    python run.py --no-html

    # control terminal tree depth
    python run.py --depth 4

Output
------
    config_viewer/config_viewer.html  (always; unless --no-html)
"""

import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict


# ── 1. Schema analysis ────────────────────────────────────────────────────────

def _js_type(value) -> str:
    if value is None:       return "null"
    if isinstance(value, bool):  return "boolean"
    if isinstance(value, int):   return "number"
    if isinstance(value, float): return "number"
    if isinstance(value, str):   return "string"
    if isinstance(value, list):  return "array"
    if isinstance(value, dict):  return "object"
    return type(value).__name__


def _walk(node, path: str, registry: dict):
    """Recursively record every dot-separated path and its observed types."""
    registry[path].add(_js_type(node))
    if isinstance(node, dict):
        for key, val in node.items():
            _walk(val, f"{path}.{key}" if path else key, registry)
    elif isinstance(node, list):
        for item in node:
            _walk(item, f"{path}[]", registry)


def analyze(filepath: Path) -> dict:
    """
    Load a plugin JSON file and return the flat schema dict:
        { "dot.path.key": ["type", ...], ... }
    """
    with open(filepath, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        data = [data]

    registry: dict = defaultdict(set)
    for doc in data:
        _walk(doc, "", registry)

    return {
        path: sorted(types)
        for path, types in registry.items()
        if path  # skip empty root entry
    }


# ── 2. Plugin metadata ────────────────────────────────────────────────────────

def plugin_name(filepath: Path) -> str:
    """Use the filename stem (without extension) directly as the plugin name."""
    return filepath.stem   # e.g. "input-date", "nexxe", "supplier-widget"


def build_plugin(filepath: Path) -> dict:
    schema = analyze(filepath)

    type_counts: dict = defaultdict(int)
    for types in schema.values():
        for t in types:
            type_counts[t] += 1

    multi_type_paths = [p for p, t in schema.items() if len(t) > 1]

    return {
        "name":           plugin_name(filepath),
        "filename":       filepath.name,
        "totalPaths":     len(schema),
        "multiTypeCount": len(multi_type_paths),
        "typeCounts":     dict(type_counts),
        "multiTypePaths": multi_type_paths,
        "schema":         schema,          # flat dict – tree built in JS
    }


# ── 3. Rich terminal output ───────────────────────────────────────────────────

TYPE_STYLE = {
    "string":  "dodger_blue2",
    "number":  "chartreuse3",
    "boolean": "gold1",
    "null":    "bright_black",
    "array":   "cyan1",
    "object":  "medium_purple1",
}

TYPE_COLOR_CSS = {
    "string":  "#4dabf7",
    "number":  "#69db7c",
    "boolean": "#ffd43b",
    "null":    "#868e96",
    "array":   "#66d9e8",
    "object":  "#cc99ff",
}


def _badge(t: str) -> str:
    s = TYPE_STYLE.get(t, "white")
    return f"[{s}]{t}[/{s}]"


def _add_rich_children(rich_node, node_dict: dict, depth: int, max_depth: int):
    if depth > max_depth:
        if node_dict:
            rich_node.add("[dim italic]... (increase --depth to see more)[/dim italic]")
        return
    for key in sorted(node_dict):
        child = node_dict[key]
        types = child.get("types", [])
        kids  = child.get("children", {})
        multi = len(types) > 1
        type_str = " [dim]|[/dim] ".join(_badge(t) for t in types)
        if key == "[]":
            label = f"[dim]\\[][/dim]  {type_str}"
        elif multi:
            label = f"[bold red]{key}[/bold red]  {type_str}"
        else:
            label = f"[bright_white]{key}[/bright_white]  {type_str}"
        child_node = rich_node.add(label)
        _add_rich_children(child_node, kids, depth + 1, max_depth)


def _build_python_tree(schema: dict) -> dict:
    """Build a nested dict tree from the flat schema (for terminal display)."""
    root: dict = {}
    for path, types in schema.items():
        node = root
        for part in path.split("."):
            node = node.setdefault(part, {"types": [], "children": {}})["children"]
        # walk again to set types at the leaf
    # rebuild properly
    root2: dict = {}
    for path, types in schema.items():
        parts = path.split(".")
        node = root2
        for i, part in enumerate(parts):
            if part not in node:
                node[part] = {"types": [], "children": {}}
            if i == len(parts) - 1:
                node[part]["types"] = types
            node = node[part]["children"]
    return root2


def render_terminal(plugins: list, max_depth: int):
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    try:
        from rich.console import Console
        from rich.tree import Tree
        from rich.panel import Panel
        from rich.table import Table
        from rich import box
    except ImportError:
        print("[!] rich not installed  ->  python -m pip install rich")
        return

    console = Console()
    console.print()

    # ── summary table across all plugins ──
    tbl = Table(
        title="[bold]Config Viewer  —  Plugin Summary[/bold]",
        box=box.ROUNDED, border_style="dim",
    )
    tbl.add_column("Plugin",      style="bold bright_white")
    tbl.add_column("File",        style="dim")
    tbl.add_column("Paths",       justify="right")
    tbl.add_column("Multi-type",  justify="right", style="red")
    tbl.add_column("Top types")

    for p in plugins:
        top = sorted(p["typeCounts"].items(), key=lambda x: -x[1])[:4]
        type_str = "  ".join(
            f"[{TYPE_STYLE.get(t,'white')}]{t}[/{TYPE_STYLE.get(t,'white')}]({c})"
            for t, c in top
        )
        tbl.add_row(
            p["name"],
            p["filename"],
            f"{p['totalPaths']:,}",
            str(p["multiTypeCount"]),
            type_str,
        )
    console.print(tbl)
    console.print()

    # ── per-plugin tree ──
    for p in plugins:
        tree = Tree(
            f"[bold bright_blue]{p['name']}[/bold bright_blue]  "
            f"[dim]{p['totalPaths']:,} paths  •  depth limit {max_depth}[/dim]"
        )
        py_tree = _build_python_tree(p["schema"])
        _add_rich_children(tree, py_tree, 1, max_depth)
        console.print(tree)
        console.print()


# ── 4. HTML generation ────────────────────────────────────────────────────────

def generate_html(plugins: list) -> str:
    # Strip the large flat schema from JS payload after embedding —
    # the tree is built client-side from the schema dict.
    plugins_json      = json.dumps(plugins,          separators=(",", ":"))
    type_colors_json  = json.dumps(TYPE_COLOR_CSS,   separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Config Viewer</title>
<style>
/* ── Reset & base ── */
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0d1117;--surface:#161b22;--surface2:#21262d;
  --border:#30363d;--border2:#484f58;
  --text:#c9d1d9;--dim:#8b949e;
  --accent:#58a6ff;--red:#f85149;--green:#3fb950;--yellow:#d29922;
}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden;display:flex;flex-direction:column}}

/* ── Header ── */
.hdr{{padding:12px 24px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;flex-shrink:0}}
.hdr-title{{font-size:1.1rem;font-weight:700;color:var(--accent)}}
.hdr-sub{{font-size:.78rem;color:var(--dim)}}

/* ── Plugin tabs ── */
.tabs{{display:flex;gap:4px;padding:8px 24px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0;flex-wrap:wrap;align-items:center}}
.tab{{padding:5px 14px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--dim);cursor:pointer;font-size:.82rem;font-family:inherit;transition:all .15s;white-space:nowrap}}
.tab:hover{{background:var(--surface2);color:var(--text);border-color:var(--border2)}}
.tab.active{{background:var(--accent);border-color:var(--accent);color:#0d1117;font-weight:600}}
.no-plugins{{color:var(--dim);font-size:.85rem;padding:4px 0}}

/* ── Stats bar ── */
.stats{{display:flex;flex-wrap:wrap;gap:10px;padding:10px 24px;background:var(--bg);border-bottom:1px solid var(--border);flex-shrink:0;align-items:center;min-height:52px}}
.stat-num{{font-size:1.35rem;font-weight:700;line-height:1}}
.stat-lbl{{font-size:.65rem;color:var(--dim);margin-top:2px;text-transform:uppercase;letter-spacing:.03em}}
.vdiv{{width:1px;height:34px;background:var(--border);flex-shrink:0}}
.type-row{{display:flex;flex-wrap:wrap;gap:5px;align-items:center}}
.tb{{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:20px;border:1px solid;font-size:.73rem;background:var(--surface);white-space:nowrap}}
.tb strong{{font-weight:600}}

/* ── Controls ── */
.ctrl{{display:flex;gap:8px;padding:8px 24px;border-bottom:1px solid var(--border);flex-shrink:0;align-items:center;flex-wrap:wrap;background:var(--surface)}}
.search-wrap{{position:relative;flex:1;max-width:380px}}
.search-wrap svg{{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--dim);pointer-events:none}}
input[type=search]{{width:100%;padding:7px 12px 7px 32px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.87rem;outline:none;font-family:inherit}}
input[type=search]:focus{{border-color:var(--accent)}}
input[type=search]::placeholder{{color:var(--dim)}}
.match-ct{{font-size:.75rem;color:var(--dim);white-space:nowrap}}
.btn{{padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:pointer;font-size:.8rem;font-family:inherit;transition:background .12s}}
.btn:hover{{background:var(--border)}}
.btn.warn{{border-color:#5a3e1b;background:#2d1f0e;color:#e6a817}}
.btn.warn:hover{{background:#3d2a10}}

/* ── Tree ── */
.tree-wrap{{flex:1;overflow:auto;padding:10px 24px 24px}}
ul{{list-style:none;padding-left:20px}}
ul.root{{padding-left:0}}
li{{margin:1px 0;line-height:1.75}}
.tog{{cursor:pointer;display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;font-size:.58rem;color:var(--accent);user-select:none;transition:transform .12s;vertical-align:middle;flex-shrink:0}}
.tog.o{{transform:rotate(90deg)}}
.dot{{display:inline-block;width:14px;text-align:center;color:var(--border);vertical-align:middle;font-size:.7rem}}
.k{{color:#e6edf3;font-family:'Cascadia Code','Fira Code','Consolas',monospace;font-size:.85rem}}
.k-m{{color:var(--red);font-family:'Cascadia Code','Fira Code','Consolas',monospace;font-size:.85rem;font-weight:700}}
.k-a{{color:#79c0ff;font-family:'Cascadia Code','Fira Code','Consolas',monospace;font-size:.85rem;font-style:italic}}
.badge{{display:inline-block;padding:0 6px;border-radius:10px;font-size:.67rem;font-weight:700;margin-left:4px;color:#0d1117;vertical-align:middle;line-height:1.6}}

/* ── Highlights ── */
li.hl>.k,li.hl>.k-m,li.hl>.k-a{{background:rgba(255,214,0,.18);border-radius:3px;padding:0 2px}}

/* ── Empty / placeholder ── */
.placeholder{{color:var(--dim);padding:48px 0;text-align:center;font-size:.9rem}}
.placeholder p{{margin-top:6px;font-size:.78rem}}

/* ── Scrollbar ── */
::-webkit-scrollbar{{width:5px;height:5px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px}}
::-webkit-scrollbar-thumb:hover{{background:var(--border2)}}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-title">Config Viewer</div>
    <div class="hdr-sub" id="hdrSub">Drop JSON files into the <code>input/</code> folder and re-run <code>run.py</code></div>
  </div>
</div>

<div class="tabs" id="tabBar">
  <span class="no-plugins" id="noPlugins" style="display:none">No plugins loaded.</span>
</div>

<div class="stats" id="statsBar"></div>

<div class="ctrl">
  <div class="search-wrap">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input type="search" id="q" placeholder="Search key paths…" oninput="onSearch(this.value)" disabled>
  </div>
  <span class="match-ct" id="mc"></span>
  <button class="btn" onclick="doExpand()" id="btnExpand" disabled>Expand All</button>
  <button class="btn" onclick="doCollapse()" id="btnCollapse" disabled>Collapse All</button>
  <button class="btn warn" onclick="doMultiOnly()" id="btnMulti" disabled>Multi-type Only</button>
</div>

<div class="tree-wrap" id="treeWrap">
  <div class="placeholder" id="placeholder">
    <strong>No plugin selected</strong>
    <p>Pick a plugin tab above to explore its schema.</p>
  </div>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────────
const PLUGINS = {plugins_json};
const TC      = {type_colors_json};  // type -> hex colour

// ── State ─────────────────────────────────────────────────────────────────────
let cur    = null;   // current plugin object
let curTree = null;  // nested tree built from cur.schema

// ── Boot ──────────────────────────────────────────────────────────────────────
(function init() {{
  const bar = document.getElementById('tabBar');
  if (!PLUGINS.length) {{
    document.getElementById('noPlugins').style.display = '';
    return;
  }}
  PLUGINS.forEach((p, i) => {{
    const btn = document.createElement('button');
    btn.className = 'tab';
    btn.textContent = p.name;
    btn.title = p.filename;
    btn.onclick = () => selectPlugin(i);
    bar.appendChild(btn);
  }});
  selectPlugin(0);
}})();

// ── Plugin selection ──────────────────────────────────────────────────────────
function selectPlugin(idx) {{
  cur = PLUGINS[idx];
  curTree = buildTree(cur.schema);

  document.getElementById('q').value = '';
  document.getElementById('q').disabled = false;
  document.getElementById('mc').textContent = '';
  document.getElementById('btnExpand').disabled   = false;
  document.getElementById('btnCollapse').disabled = false;
  document.getElementById('btnMulti').disabled    = false;

  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', i === idx));
  document.getElementById('hdrSub').textContent =
    cur.filename + '  —  ' + cur.totalPaths.toLocaleString() + ' key paths';

  renderStats(cur);
  renderTree(curTree);
}}

// ── Build tree from flat schema ───────────────────────────────────────────────
// Produces: {{ key: {{ t: [types], c: {{children}} }} }}
function buildTree(schema) {{
  const root = {{}};
  for (const [path, types] of Object.entries(schema)) {{
    const parts = path.split('.');
    let node = root;
    for (let i = 0; i < parts.length; i++) {{
      const p = parts[i];
      if (!node[p]) node[p] = {{ t: [], c: {{}} }};
      if (i === parts.length - 1) node[p].t = types;
      node = node[p].c;
    }}
  }}
  return root;
}}

// ── Stats bar ─────────────────────────────────────────────────────────────────
function renderStats(p) {{
  const order = Object.entries(p.typeCounts).sort((a, b) => b[1] - a[1]);
  const badges = order.map(([t, c]) => {{
    const col = TC[t] || '#aaa';
    return `<span class="tb" style="border-color:${{col}}"><span style="color:${{col}}">${{t}}</span><strong>${{c}}</strong></span>`;
  }}).join('');
  document.getElementById('statsBar').innerHTML = `
    <div>
      <div class="stat-num" style="color:var(--accent)">${{p.totalPaths.toLocaleString()}}</div>
      <div class="stat-lbl">Paths</div>
    </div>
    <div class="vdiv"></div>
    <div>
      <div class="stat-num" style="color:var(--red)">${{p.multiTypeCount}}</div>
      <div class="stat-lbl">Multi-type</div>
    </div>
    <div class="vdiv"></div>
    <div class="type-row">${{badges}}</div>`;
}}

// ── Tree DOM rendering (lazy) ─────────────────────────────────────────────────
function renderTree(treeData) {{
  const wrap = document.getElementById('treeWrap');
  const ul   = document.createElement('ul');
  ul.className = 'root';
  appendChildren(ul, treeData);
  wrap.innerHTML = '';
  wrap.appendChild(ul);
}}

function appendChildren(ul, data) {{
  for (const key of Object.keys(data).sort()) {{
    ul.appendChild(makeNode(key, data[key]));
  }}
}}

function makeNode(key, node) {{
  const li      = document.createElement('li');
  li.dataset.k  = key;
  li._nd        = node;      // keep reference for lazy expand
  li._open      = false;

  const hasKids = node.c && Object.keys(node.c).length > 0;
  const multi   = node.t && node.t.length > 1;
  const isArr   = key === '[]';

  // toggle / dot
  if (hasKids) {{
    const tog  = document.createElement('span');
    tog.className = 'tog';
    tog.textContent = '▶';
    tog.onclick = e => {{ e.stopPropagation(); toggle(li, tog); }};
    li.appendChild(tog);
  }} else {{
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.textContent = '·';
    li.appendChild(dot);
  }}

  // key label
  const kEl = document.createElement('span');
  kEl.className = isArr ? 'k-a' : (multi ? 'k-m' : 'k');
  kEl.textContent = isArr ? '[ ]' : key;
  li.appendChild(kEl);

  // type badges
  if (node.t) {{
    node.t.forEach(t => {{
      const b = document.createElement('span');
      b.className = 'badge';
      b.style.background = TC[t] || '#aaa';
      b.textContent = t;
      li.appendChild(b);
    }});
  }}

  return li;
}}

// ── Expand / collapse ─────────────────────────────────────────────────────────
function toggle(li, tog) {{
  if (li._open) {{
    li.querySelector(':scope>ul')?.remove();
    li._open = false;
    tog.classList.remove('o');
  }} else {{
    const ul = document.createElement('ul');
    appendChildren(ul, li._nd.c);
    li.appendChild(ul);
    li._open = true;
    tog.classList.add('o');
  }}
}}

function expandNodeOpen(li) {{
  if (li._open || !li._nd?.c || !Object.keys(li._nd.c).length) return;
  const ul = document.createElement('ul');
  appendChildren(ul, li._nd.c);
  li.appendChild(ul);
  li._open = true;
  const tog = li.querySelector(':scope>.tog');
  if (tog) tog.classList.add('o');
}}

// Expand a dot-path string, creating DOM nodes as needed
function expandPath(dotPath) {{
  const parts   = dotPath.split('.');
  let ul        = document.querySelector('#treeWrap ul.root');

  for (const part of parts) {{
    if (!ul) return;
    let li = Array.from(ul.children).find(l => l.dataset.k === part);
    if (!li) return;
    expandNodeOpen(li);
    ul = li.querySelector(':scope>ul') || null;
  }}
}}

function doExpand() {{
  if (!cur) return;
  expandRecursive(document.querySelector('#treeWrap ul.root'), 0, 5);
}}

function expandRecursive(ul, depth, maxD) {{
  if (!ul || depth >= maxD) return;
  Array.from(ul.children).forEach(li => {{
    expandNodeOpen(li);
    const childUl = li.querySelector(':scope>ul');
    if (childUl) expandRecursive(childUl, depth + 1, maxD);
  }});
}}

function doCollapse() {{
  document.querySelectorAll('#treeWrap li').forEach(li => {{
    li.querySelector(':scope>ul')?.remove();
    li._open = false;
    const tog = li.querySelector(':scope>.tog');
    if (tog) tog.classList.remove('o');
  }});
}}

// ── Multi-type only ───────────────────────────────────────────────────────────
function doMultiOnly() {{
  if (!cur) return;
  document.getElementById('q').value = '';
  document.getElementById('mc').textContent = '';
  doCollapse();
  cur.multiTypePaths.forEach(p => expandPath(p));
  // highlight the multi-type leaves
  setTimeout(() => {{
    document.querySelectorAll('#treeWrap li').forEach(li => {{
      li.classList.toggle('hl', li.querySelector('.k-m') !== null);
    }});
  }}, 20);
}}

// ── Search ────────────────────────────────────────────────────────────────────
function onSearch(raw) {{
  const q  = raw.toLowerCase().trim();
  const mc = document.getElementById('mc');

  // Clear highlights
  document.querySelectorAll('#treeWrap li').forEach(l => l.classList.remove('hl'));

  if (!q) {{
    mc.textContent = '';
    doCollapse();
    return;
  }}
  if (!cur) return;

  const matches = Object.keys(cur.schema).filter(p => p.toLowerCase().includes(q));
  mc.textContent = matches.length
    ? matches.length.toLocaleString() + ' match' + (matches.length === 1 ? '' : 'es')
    : 'no matches';

  if (!matches.length) return;

  // Limit DOM expansion to avoid hanging on very broad queries
  const toExpand = matches.length > 200 ? matches.slice(0, 200) : matches;
  if (matches.length > 200) mc.textContent += ' (showing first 200)';

  doCollapse();
  toExpand.forEach(p => expandPath(p));

  // Highlight matching leaf nodes
  setTimeout(() => {{
    document.querySelectorAll('#treeWrap li').forEach(li => {{
      const kEl = li.querySelector('.k,.k-m,.k-a');
      if (kEl && kEl.textContent !== '[ ]' && kEl.textContent.toLowerCase().includes(q)) {{
        li.classList.add('hl');
      }}
    }});
  }}, 20);
}}
</script>
</body>
</html>"""


# ── 5. Main ───────────────────────────────────────────────────────────────────

def main():
    base = Path(__file__).parent

    ap = argparse.ArgumentParser(description="Config Viewer — multi-plugin schema visualiser")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--input",  metavar="DIR",
                     help="Folder containing plugin JSON files (default: ./input/)")
    src.add_argument("--files",  metavar="FILE", nargs="+",
                     help="Explicit list of JSON files to process")
    ap.add_argument("--no-html", action="store_true", help="Skip HTML generation")
    ap.add_argument("--depth",   type=int, default=4,
                    help="Max depth for terminal tree (default: 4)")
    args = ap.parse_args()

    # ── resolve input files ──
    if args.files:
        files = [Path(f) for f in args.files]
    else:
        input_dir = Path(args.input) if args.input else base / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(input_dir.glob("*.json"))

    if not files:
        print("[!] No JSON files found. Drop files into the input/ folder or use --files.")
        sys.exit(0)

    # ── analyse each file ──
    plugins = []
    for f in files:
        print(f"  Analysing  {f.name} ...", end="  ", flush=True)
        try:
            p = build_plugin(f)
            plugins.append(p)
            print(f"{p['totalPaths']:,} paths  |  {p['multiTypeCount']} multi-type")
        except Exception as e:
            print(f"ERROR: {e}")

    if not plugins:
        print("[!] No plugins processed successfully.")
        sys.exit(1)

    print()

    # ── terminal visualisation ──
    render_terminal(plugins, max_depth=args.depth)

    # ── HTML ──
    if not args.no_html:
        out_path = base / "config_viewer.html"
        out_path.write_text(generate_html(plugins), encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"[HTML]  {out_path}  ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
