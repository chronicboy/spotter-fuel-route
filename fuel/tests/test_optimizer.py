import random

from django.test import SimpleTestCase

from fuel.optimizer import Candidate, InfeasibleRouteError, greedy_min_cost, plan_fuel_stops

R, MPG = 500.0, 10.0


def c(mile, price):
    return Candidate(mile=mile, price=price)


class PlanFuelStopsTests(SimpleTestCase):
    def test_no_stop_when_start_fuel_covers_trip(self):
        plan = plan_fuel_stops(450, [c(100, 3.0)], R, MPG)
        self.assertEqual(plan.purchases, [])
        self.assertEqual(plan.total_cost, 0)
        self.assertAlmostEqual(plan.fuel_left_gallons, 5.0)

    def test_skips_expensive_station_for_cheaper_one(self):
        # Full tank (500 mi). Cheapest is at mile 400: buy exactly what's
        # needed for the remaining 400 mi (100 mi still in the tank).
        plan = plan_fuel_stops(800, [c(100, 3.0), c(400, 2.0)], R, MPG)
        self.assertEqual(len(plan.purchases), 1)
        p = plan.purchases[0]
        self.assertEqual(p.candidate.mile, 400)
        self.assertAlmostEqual(p.gallons, 30.0)
        self.assertAlmostEqual(plan.total_cost, 60.0)
        self.assertAlmostEqual(p.fuel_on_arrival_gallons, 10.0)
        self.assertAlmostEqual(plan.fuel_left_gallons, 0.0)

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        # Cheap at 300, expensive at 700: fill at 300 to minimise fuel bought at 700.
        plan = plan_fuel_stops(1100, [c(300, 2.0), c(700, 4.0)], R, MPG, stop_penalty=0)
        self.assertEqual([p.candidate.mile for p in plan.purchases], [300, 700])
        self.assertAlmostEqual(plan.purchases[0].gallons, 30.0)  # 20 left + 30 = full
        self.assertAlmostEqual(plan.purchases[1].gallons, 30.0)  # 10 left, need 40
        self.assertAlmostEqual(plan.total_cost, 30 * 2.0 + 30 * 4.0)

    def test_partial_start_fuel(self):
        plan = plan_fuel_stops(300, [c(50, 3.0)], R, MPG, start_fuel_miles=60)
        self.assertEqual(len(plan.purchases), 1)
        self.assertAlmostEqual(plan.purchases[0].gallons, 24.0)  # 1 gal left, need 25
        self.assertAlmostEqual(plan.total_cost, 72.0)

    def test_infeasible_gap_raises(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops(1200, [c(400, 3.0)], R, MPG)  # 800-mile gap after mile 400

    def test_start_fuel_too_low_raises(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops(300, [c(100, 3.0)], R, MPG, start_fuel_miles=50)

    def test_stop_penalty_trades_cents_for_fewer_stops(self):
        # Station 2 is 1 cent cheaper but needs an extra stop.
        stations = [c(450, 3.00), c(460, 2.99), c(900, 3.50)]
        no_pen = plan_fuel_stops(1300, stations, R, MPG, stop_penalty=0)
        with_pen = plan_fuel_stops(1300, stations, R, MPG, stop_penalty=5)
        self.assertGreaterEqual(len(no_pen.purchases), len(with_pen.purchases))
        self.assertLessEqual(no_pen.total_cost, with_pen.total_cost + 1e-9)

    def test_dp_matches_greedy_optimum_on_random_routes(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(300):
            total = rng.uniform(200, 2500)
            stations = [c(rng.uniform(0, total), round(rng.uniform(2.7, 4.2), 3))
                        for _ in range(rng.randint(3, 60))]
            start = rng.choice([None, rng.uniform(100, 500)])
            try:
                expected = greedy_min_cost(total, stations, R, MPG, start)
            except InfeasibleRouteError:
                with self.assertRaises(InfeasibleRouteError):
                    plan_fuel_stops(total, stations, R, MPG, start, stop_penalty=0)
                continue
            plan = plan_fuel_stops(total, stations, R, MPG, start, stop_penalty=0)
            self.assertAlmostEqual(plan.total_cost, expected, places=6)
            # Fuel bought + starting fuel must cover the trip exactly.
            start_gal = (start if start is not None else R) / MPG
            used = total / MPG
            self.assertAlmostEqual(plan.total_gallons + start_gal - plan.fuel_left_gallons, used, places=6)
            checked += 1
        self.assertGreater(checked, 100)

    def test_plan_never_exceeds_tank_or_runs_dry(self):
        rng = random.Random(7)
        for _ in range(200):
            total = rng.uniform(500, 3000)
            stations = [c(m, round(rng.uniform(2.7, 4.2), 3))
                        for m in sorted(rng.uniform(0, total) for _ in range(80))]
            try:
                plan = plan_fuel_stops(total, stations, R, MPG, stop_penalty=rng.choice([0, 5]))
            except InfeasibleRouteError:
                continue
            fuel, pos = R, 0.0
            for p in plan.purchases:
                fuel -= p.candidate.mile - pos
                self.assertGreaterEqual(fuel, -1e-6)
                self.assertAlmostEqual(fuel / MPG, p.fuel_on_arrival_gallons, places=6)
                fuel += p.gallons * MPG
                self.assertLessEqual(fuel, R + 1e-6)
                pos = p.candidate.mile
            self.assertGreaterEqual(fuel - (total - pos), -1e-6)
