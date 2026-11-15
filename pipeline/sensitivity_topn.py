"""Sensitivity of the backtest to the watch-list size (NOT pre-registered; the pre-registered list is top 5).

For each list size N it reruns exactly the backtest's metrics (same files, same hit definition: a ranked patch within
the truth's hit radius that drains to the same channel) and adds what chance alone would give: if the N listed
patches were drawn at random from the n ranked ones, the probability that at least one of the k hit patches is listed
is 1 - C(n-k, N) / C(n, N). Summing that over passes gives the hits expected by luck.

Usage: python pipeline/sensitivity_topn.py  ->  data/sensitivity_topn.json
"""
from __future__ import annotations

import json
from math import comb

import backtest as bt

SIZES = [5, 10, 11, 15, 20]
SITES = ["langtang", "budhigandaki", "manang"]


def chance(n: int, k: int, N: int) -> float:
    if not n or not k:
        return 0.0
    N = min(N, n)
    return 1 - comb(n - k, N) / comb(n, N)


def main():
    out = {"note": "Sensitivity only. The pre-registered rule is top 5 in BOTH of the last two ascending passes before the event; "
                   "changing N after seeing results is a post-hoc choice.", "hit_radius_km": 2.0, "sizes": SIZES, "sites": {}}
    for site in SITES:
        S = bt.load_site(site)
        rows = {}
        for N in SIZES:
            bt.TOP_N = N
            r = bt.site_result(site)
            entry = {"false_alarms": r["false_alarm"]["n_false_alarms"], "false_alarm_passes": r["false_alarm"]["n_passes"],
                     "false_alarm_rate_per_pass": r["false_alarm"]["false_alarm_rate_per_pass"]}
            if r.get("rank_series"):
                ser = [x for x in r["rank_series"] if x["status"] == "ok"]
                hits = [x for x in ser if x["rank"] is not None and x["rank"] <= N]
                exp = 0.0
                per = []
                for x in ser:
                    k = sum(1 for p in bt.blobs(S, x["window"]) if bt.is_hit(S, p))
                    pc = chance(x["n_ranked"], k, N)
                    exp += pc
                    per.append({"window": x["window"], "rank": x["rank"], "n_ranked": x["n_ranked"], "hit_patches": k,
                                "listed": bool(x["rank"] is not None and x["rank"] <= N), "chance_listed": round(pc, 3)})
                pp = r["positive_pass"]
                entry.update(pass_test=pp["result"], last_two_ranks=pp["ranks"], passes=len(ser), hits=len(hits),
                             hits_expected_by_chance=round(exp, 2), per_pass=per,
                             spanning_event=[{"window": o["window"], "rank": o["rank"], "n_ranked": o["n_ranked"]}
                                             for o in r["other_windows"] if o.get("note") == "spans the event"])
            rows[str(N)] = entry
        out["sites"][site] = rows
    bt.TOP_N = bt.CFG["pass_condition"]["top_n"]
    (bt.DATA if hasattr(bt, "DATA") else None)
    from grid import DATA
    (DATA / "sensitivity_topn.json").write_text(json.dumps(out, indent=1))
    for site in SITES:
        print(f"\n{site}")
        print(" N | pass test (last 2) | hits / passes | by chance | false alarms")
        for N in SIZES:
            e = out["sites"][site][str(N)]
            if "hits" in e:
                print(f"{N:>2} | {e['pass_test']:<4} {str(e['last_two_ranks']):<12} | {e['hits']:>2} / {e['passes']:<2}       | {e['hits_expected_by_chance']:>5} | "
                      f"{e['false_alarms']} in {e['false_alarm_passes']} ({e['false_alarm_rate_per_pass']}/pass)")
            else:
                print(f"{N:>2} | {'—':<17} | {'—':<14} | {'—':>5} | {e['false_alarms']} in {e['false_alarm_passes']} ({e['false_alarm_rate_per_pass']}/pass)")


if __name__ == "__main__":
    main()
