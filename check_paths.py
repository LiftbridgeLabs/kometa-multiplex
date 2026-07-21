"""Static preflight check: verify every /config path referenced by config.yml
and its definition files exists locally, and that nothing collides."""
import os
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))

def to_local(p):
    return os.path.join(BASE, p.replace("/config/", "", 1).replace("/", os.sep))

class DupeCheckLoader(yaml.SafeLoader):
    pass

def no_dupes(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise ValueError(f"duplicate mapping key: {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)

DupeCheckLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_dupes)

problems, ok = [], []

with open(os.path.join(BASE, "config.yml"), encoding="utf-8") as f:
    cfg = yaml.load(f, DupeCheckLoader)

def walk(node, trail):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, trail + [str(k)])
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, trail + [f"[{i}]"])
    elif isinstance(node, str) and node.startswith("/config/"):
        where = ".".join(trail)
        if "<<" in node:  # templated path: check the parent directory instead
            parent = to_local(node.rsplit("/", 1)[0])
            if os.path.isdir(parent):
                ok.append(f"dir exists (templated): {node}  [{where}]")
            else:
                problems.append(f"MISSING dir for templated path: {node}  [{where}]")
        elif trail[-1] == "report_path" or node.startswith("/config/logs/"):
            parent = to_local(node.rsplit("/", 1)[0])
            (ok if os.path.isdir(parent) else problems).append(
                ("log dir exists: " if os.path.isdir(parent) else "MISSING log dir: ")
                + node + f"  [{where}]")
        else:
            local = to_local(node)
            if os.path.isfile(local) or os.path.isdir(local):
                ok.append(f"exists: {node}  [{where}]")
            else:
                problems.append(f"MISSING: {node}  [{where}]")

walk(cfg, [])

# also walk every definition yml the config references, for image paths
for top in ("collections", "metadata", "overlays"):
    for root, dirs, files in os.walk(os.path.join(BASE, top)):
        dirs[:] = [d for d in dirs if d not in ("images", "fonts")
                   and not d.endswith("Original Posters")]
        for fn in files:
            if not fn.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as f:
                try:
                    data = yaml.load(f, DupeCheckLoader)
                except ValueError as e:
                    problems.append(f"{fn}: {e}")
                    continue
            walk(data, [os.path.relpath(path, BASE)])

# uniqueness checks
libs = cfg["libraries"]
rps = [v.get("report_path") for v in libs.values()]
if len(rps) != len(set(rps)):
    problems.append(f"report_path collision: {rps}")
else:
    ok.append(f"all {len(rps)} report_paths unique")

names = [(k, v.get("library_name", k), str(v.get("plex", {}).get("url", "global")))
         for k, v in libs.items()]
combos = [(n[1], n[2]) for n in names]
if len(combos) != len(set(combos)):
    problems.append("two mappings target the same library on the same server!")
else:
    ok.append("each mapping targets a distinct (server, library) pair")

print(f"== {len(ok)} checks passed ==")
for line in ok:
    print("  OK", line)
print(f"\n== {len(problems)} problems ==")
for line in problems:
    print("  !!", line)
