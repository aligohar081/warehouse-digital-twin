"""Which cells are cut vertices of the ground graph (a robot stopped there disconnects something)
for an AMR and a forklift, and which of those are missing from cells_jobs_need?"""
import sys, tempfile
sys.setrecursionlimit(10000)
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
w = twin.warehouse
need = w.cells_jobs_need()
for name, asset, start in (("TR50-201", "AST-000201", (8, 11)), ("PF1200-205", "AST-000205", (7, 4))):
    prof = twin.add_robot(name=name, asset_id=asset, position=start).mobility
    nodes = set()
    stack = [start]
    while stack:
        c = stack.pop()
        if c in nodes: continue
        nodes.add(c)
        stack.extend(n for n in w.neighbors(c, prof, "GROUND") if n not in nodes)
    disc, low, cut, t = {}, {}, set(), [0]
    def dfs(u, parent):
        disc[u] = low[u] = t[0]; t[0] += 1
        kids = 0
        for v in w.neighbors(u, prof, "GROUND"):
            if v not in disc:
                kids += 1
                dfs(v, u)
                low[u] = min(low[u], low[v])
                if parent is not None and low[v] >= disc[u]:
                    cut.add(u)
            elif v != parent:
                low[u] = min(low[u], disc[v])
        if parent is None and kids > 1:
            cut.add(u)
    dfs(start, None)
    stoppable_missing = sorted(c for c in cut if c not in need and w.may_stop(c, prof, "GROUND"))
    print(prof.embodiment_class, "reachable", len(nodes), "cut cells", len(cut), "cut & stoppable & not in cells_jobs_need:",
          stoppable_missing)
