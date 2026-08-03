import unittest
from pathlib import Path
from types import SimpleNamespace

from hospital_agent.runner import _build_runtimes
from hospital_agent.state import AgentState


class PollingRuntimeTests(unittest.TestCase):
    def test_protocol_polling_uses_its_own_switch_and_interval(self):
        xa_polling = SimpleNamespace(state=True, interval_min=7)
        study_polling = SimpleNamespace(
            state=False,
            interval_min=1,
            operations_dirs=[Path("operations")],
        )
        config = SimpleNamespace(
            user_requests_polling=SimpleNamespace(state=True, interval_min=1),
            study_polling=study_polling,
            ct_polling=SimpleNamespace(state=False, interval_min=10),
            xa_polling=xa_polling,
        )

        runtimes = _build_runtimes(config, object(), AgentState())
        protocols = next(runtime for runtime in runtimes if runtime.name == "protocols")

        self.assertIs(protocols.config, study_polling)
        self.assertIsNot(protocols.config, xa_polling)

if __name__ == "__main__":
    unittest.main()
