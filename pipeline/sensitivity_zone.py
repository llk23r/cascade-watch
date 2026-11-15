"""Would the watch list have covered the place that failed, at the scale a warning needs? (NOT pre-registered.)

For zone sizes (km around the truth, same drainage as the truth) and list sizes N, count passes where a top-N patch
falls in the zone, the hits expected if the N patches were picked at random (hypergeometric, per pass), the
Poisson-binomial p-value of the observed count, and whether both of the last two passes before the event were covered.
Several zones and N were tried after seeing results, so a single small p-value here is a lead to pre-register and test
on new events, not proof.

Usage: python pipeline/sensitivity_zone.py  ->  data/sensitivity_zone.json
"""
from __future__ import annotations

import json
from math import comb

import backtest as bt
from grid import DATA

ZONES = [2, 3, 5, 8, None]   # None = the whole source tributary
SIZES = [5, 10]


def chance(n, k, N):
    if not n or not k:
        return 0.0
    N = min(N, n)
    return 1 - comb(n - k, N) / comb(n, N)


def tail(ps, m):
    d = [1.0]
    for p in ps:
        d = [(d[i] if i < len(d) else 0) * (1 - p) + (d[i - 1] if i > 0 else 0) * p for i in range(len(d) + 1)]
    return sum(d[m:])


def main():
    out = {"note": __doc__.strip().splitlines()[0], "caveat": "Zones and list sizes chosen after seeing results; test the chosen one on new events.",
           "sites": {}}
    for site in ("langtang", "budhigandaki"):
        S = bt.load_site(site); bt.TOP_N = bt.CFG["pass_condition"]["top_n"]
        r = bt.site_result(site)
        wins = [x["window"] for x in r["rank_series"] if x["status"] == "ok"]
        B = {w: bt.blobs(S, w) for w in wins}
        last2 = S["s"]["last_two_ascending"]
        rows = []
        for z in ZONES:
            for N in SIZES:
                ps, best = [], []
                for w in wins:
                    zone = [p for p in B[w] if p.get("same_channel") and (z is None or p["dist_truth_km"] <= z)]
                    ps.append(chance(len(B[w]), len(zone), N)); best.append(min((p["rank"] for p in zone), default=None))
                hits = sum(1 for b in best if b and b <= N)
                l2 = [best[wins.index(w)] if w in wins else None for w in last2]
                rows.append(dict(zone_km=z or "tributary", top_n=N, passes=len(wins), hits=hits, expected_by_chance=round(sum(ps), 2),
                                 p_value=round(tail(ps, hits), 4), last_two_best_rank=l2, last_two_covered=sum(1 for b in l2 if b and b <= N)))
        valleys = {str(N): round(sum(len({(p.get("channel_name") or p["id"]) for p in B[w][:N]}) for w in wins) / len(wins), 1) for N in SIZES}
        out["sites"][site] = dict(role=r["role"], tributary_to=r.get("source_path_first_river"), rows=rows, valleys_on_list_per_pass=valleys)
    bt.TOP_N = bt.CFG["pass_condition"]["top_n"]
    (DATA / "sensitivity_zone.json").write_text(json.dumps(out, indent=1))
    for s, v in out["sites"].items():
        print(s)
        for x in v["rows"]:
            print(f"  zone {str(x['zone_km']):>9}  top {x['top_n']:>2}: {x['hits']:>2}/{x['passes']} (chance {x['expected_by_chance']:>4})  p={x['p_value']:<6}  last two covered {x['last_two_covered']}/2 {x['last_two_best_rank']}")


if __name__ == "__main__":
    main()
