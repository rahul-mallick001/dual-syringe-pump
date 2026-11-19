"""Integration test for web application FastAPI endpoints."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from app import app
from pump import engine
try:
    from fake_pump import FakeFactory
except ImportError:
    from tests.fake_pump import FakeFactory
from pump.device import PumpDevice


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Configure controller with fake serial
        self.factory = FakeFactory()
        engine.controller.disconnect()
        engine.controller._device_factory = lambda port: PumpDevice(port=port, serial_factory=self.factory)

    def test_root_and_state(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])

        res_state = self.client.get("/api/state")
        self.assertEqual(res_state.status_code, 200)
        data = res_state.json()
        self.assertIn("racks", data)
        self.assertIn("A", data["racks"])
        self.assertIn("B", data["racks"])

    def test_update_pump_endpoint(self):
        # Update Pump 1
        res = self.client.post("/api/pump", json={
            "axis": "A",
            "diameter": 15.0,
            "flowRate": 2.5,
            "flowRateUnit": 3,
            "durationType": 0,
            "duration": 30.0,
            "durationUnit": 0,
            "enabled": True,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["racks"]["A"]["diameter"], 15.0)
        self.assertEqual(data["racks"]["A"]["flowRate"], 2.5)

        # Update Pump 2
        res2 = self.client.post("/api/pump", json={
            "axis": "B",
            "diameter": 22.0,
            "flowRate": 1.2,
            "flowRateUnit": 2,
            "enabled": True,
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["racks"]["B"]["diameter"], 22.0)
        self.assertEqual(data2["racks"]["B"]["flowRate"], 1.2)

    def test_homing_endpoint(self):
        # Connect first
        res_conn = self.client.post("/api/connect", json={"port": "COM_TEST"})
        self.assertEqual(res_conn.status_code, 200)

        # Home both
        res_home = self.client.post("/api/home", json={"target": "both"})
        self.assertEqual(res_home.status_code, 200)

        # Home rack 2
        res_home2 = self.client.post("/api/home", json={"target": "B"})
        self.assertEqual(res_home2.status_code, 200)

    def test_withdrawal_endpoint(self):
        res = self.client.post("/api/withdrawal", json={"value": True})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["withdrawal"])

        res2 = self.client.post("/api/withdrawal", json={"value": False})
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(res2.json()["withdrawal"])

    def test_step_update_and_selection(self):
        # Steps 0..4 exist by default
        res = self.client.post("/api/step", json={
            "index": 2,
            "diameter": 16.0,
            "flowRate": 4.0,
            "duration": 45.0,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["steps"][2]["diameter"], 16.0)
        self.assertEqual(data["steps"][2]["flowRate"], 4.0)
        self.assertEqual(data["steps"][2]["duration"], 45.0)

        # Select steps 0 and 2
        sel_res = self.client.post("/api/selected", json={
            "selected": [True, False, True, False, False]
        })
        self.assertEqual(sel_res.status_code, 200)
        self.assertEqual(sel_res.json()["selected"], [True, False, True, False, False])

    def test_mode_switch_and_step_run(self):
        # Switch mode to steps
        mode_res = self.client.post("/api/mode", json={"mode": "steps"})
        self.assertEqual(mode_res.status_code, 200)
        self.assertEqual(mode_res.json()["mode"], "steps")
        self.assertFalse(mode_res.json()["isDualMode"])

        # Connect and run
        self.client.post("/api/connect", json={"port": "COM_TEST"})
        run_res = self.client.post("/api/run", json={"mode": "steps"})
        self.assertEqual(run_res.status_code, 200)

