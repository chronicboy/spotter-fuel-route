"""Minimum-cost refuelling along a fixed route.

Model: stations sit at known route miles with known prices; the tank holds
`max_range_miles` of fuel; any amount can be bought at a stop. Fuel is
tracked in miles of range (gallons = miles / mpg).

`plan_fuel_stops` solves it exactly with the dynamic program from Khuller,
Malekian & Mestre, "To Fill or not to Fill: The Gas Station Problem"
(ACM TALG 2011). Their key result is that some optimal plan, at every stop,
either fills the tank or buys just enough to reach the next stop. So the only
fuel levels you can arrive at a station with are:

    0                         (previous stop bought "just enough")
    R - dist(w, v)            (filled up at an earlier station w)
    start_fuel - dist(0, v)   (still on the starting tank)

That keeps the state space to O(n * k) for n stations and k stations per
tank-range, with O(k) transitions each.

An optional `stop_penalty` (USD per stop) is added to the objective so the
planner won't make an extra stop to save a few cents. With a penalty of 0 the
result is the pure minimum fuel cost (the same as the classic greedy, kept
below as `greedy_min_cost` for cross-checking in tests).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from functools import lru_cache

EPS = 1e-6


class InfeasibleRouteError(Exception):
    """No sequence of stops in the corridor can cover the route."""


@dataclass(frozen=True, eq=False)
class Candidate:
    mile: float          # position along the route
    price: float         # USD / gallon
    ref: object = None   # opaque payload (station record + metadata)


@dataclass
class Purchase:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass
class FuelPlan:
    purchases: list[Purchase] = field(default_factory=list)
    total_gallons: float = 0.0
    total_cost: float = 0.0
    fuel_used_gallons: float = 0.0
    fuel_left_gallons: float = 0.0


def _prepare(total_miles, candidates, max_range_miles, start_fuel_miles):
    if start_fuel_miles is None:
        start_fuel_miles = max_range_miles
    start_fuel_miles = min(max(start_fuel_miles, 0.0), max_range_miles)
    stops = sorted((c for c in candidates if 0.0 <= c.mile < total_miles), key=lambda c: c.mile)
    return stops, start_fuel_miles


def _infeasible(stops, total_miles, max_range_miles, start_fuel_miles):
    marks = [c.mile for c in stops] + [total_miles]
    prev, reach = 0.0, start_fuel_miles
    for m in marks:
        if m - prev > reach + EPS:
            return InfeasibleRouteError(
                f"No fuel station within {reach:.0f} miles after route mile {prev:.0f} "
                f"(next option is at mile {m:.0f}). Try a wider corridor_miles."
            )
        prev, reach = m, max_range_miles
    return InfeasibleRouteError("No feasible refuelling plan for this route.")


def plan_fuel_stops(
    total_miles: float,
    candidates: list[Candidate],
    max_range_miles: float,
    mpg: float,
    start_fuel_miles: float | None = None,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    stops, start_fuel = _prepare(total_miles, candidates, max_range_miles, start_fuel_miles)
    R, n = max_range_miles, len(stops)
    x = [c.mile for c in stops]
    cost_per_mile = [c.price / mpg for c in stops]
    # last[u]: index one past the furthest station reachable from u on a full tank.
    last, j = [0] * n, 0
    for u in range(n):
        j = max(j, u + 1)
        while j < n and x[j] - x[u] <= R + EPS:
            j += 1
        last[u] = j

    sys.setrecursionlimit(max(1000, 4 * n + 100))

    @lru_cache(maxsize=None)
    def best(u: int, g: float):
        """Min cost to finish from station u, arriving with g miles of fuel.
        Returns (cost, decision) with decision = (buy_miles, next_index|None)."""
        to_dest = total_miles - x[u]
        if g >= to_dest - EPS:
            return 0.0, (0.0, None)
        result = (math.inf, None)
        if to_dest <= R + EPS:
            buy = to_dest - g
            result = (buy * cost_per_mile[u] + stop_penalty, (buy, None))
        for v in range(u + 1, last[u]):
            d = x[v] - x[u]
            if g <= d + EPS:  # buy just enough to reach v, arrive empty
                buy = max(d - g, 0.0)
                cost = buy * cost_per_mile[u] + (stop_penalty if buy > EPS else 0.0)
                cost += best(v, 0.0)[0]
                if cost < result[0] - EPS:
                    result = (cost, (buy, v))
            buy = R - g  # fill the tank, arrive with R - d
            cost = buy * cost_per_mile[u] + (stop_penalty if buy > EPS else 0.0)
            cost += best(v, R - d)[0]
            if cost < result[0] - EPS:
                result = (cost, (buy, v))
        return result

    # From the origin (nothing to buy): reach the finish directly, or go to
    # any station within the starting fuel.
    if total_miles <= start_fuel + EPS:
        first, first_cost = None, 0.0
    else:
        first, first_cost = None, math.inf
        for v in range(n):
            if x[v] > start_fuel + EPS:
                break
            c = best(v, start_fuel - x[v])[0]
            if c < first_cost - EPS:
                first, first_cost = v, c
        if first is None or math.isinf(first_cost):
            raise _infeasible(stops, total_miles, R, start_fuel)

    plan = FuelPlan(fuel_used_gallons=total_miles / mpg)
    u, g = first, (start_fuel - x[first]) if first is not None else start_fuel
    while u is not None:
        _, (buy, nxt) = best(u, g)
        if buy > EPS:
            gallons = buy / mpg
            cost = gallons * stops[u].price
            plan.purchases.append(Purchase(stops[u], gallons, cost, fuel_on_arrival_gallons=g / mpg))
            plan.total_gallons += gallons
            plan.total_cost += cost
        tank = g + buy
        if nxt is None:
            g = tank - (total_miles - x[u])
            break
        g = tank - (x[nxt] - x[u])
        if g < EPS:
            g = 0.0
        u = nxt
    if first is None:
        g = start_fuel - total_miles
    plan.fuel_left_gallons = max(g, 0.0) / mpg
    best.cache_clear()
    return plan


def greedy_min_cost(
    total_miles: float,
    candidates: list[Candidate],
    max_range_miles: float,
    mpg: float,
    start_fuel_miles: float | None = None,
) -> float:
    """Reference solution (pure cost, no stop penalty): at each station, if a
    cheaper one is in range buy just enough to reach the first such; otherwise
    fill up and go to the cheapest in range. Used to cross-check the DP."""
    stops, fuel = _prepare(total_miles, candidates, max_range_miles, start_fuel_miles)
    R = max_range_miles
    nodes = [(c.mile, c.price) for c in stops] + [(total_miles, 0.0)]
    pos, price, can_buy, cost, i = 0.0, math.inf, False, 0.0, 0
    while pos < total_miles - EPS:
        reach = pos + (R if can_buy else fuel)
        in_reach = [nd for nd in nodes[i:] if pos < nd[0] <= reach + EPS]
        if not in_reach:
            raise _infeasible(stops, total_miles, R, fuel)
        cheaper = next((nd for nd in in_reach if nd[1] < price), None)
        if cheaper:
            nxt = cheaper
            buy = max(0.0, (nxt[0] - pos) - fuel)
        else:
            nxt = min(in_reach, key=lambda nd: (nd[1], -nd[0]))
            buy = R - fuel
        cost += buy / mpg * price if buy > EPS else 0.0
        fuel += buy - (nxt[0] - pos)
        pos, price, can_buy = nxt[0], nxt[1], True
        i = nodes.index(nxt)
    return cost
