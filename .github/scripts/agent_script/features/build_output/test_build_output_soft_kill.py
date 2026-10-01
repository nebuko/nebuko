"""Soft-kill integration in build_output (issue #704).

A gracefully timed-out run must emit an explicit soft-kill error dispatch so
the workflow retries the interrupted step instead of advancing.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    ),
)

from build_output import _soft_kill_dispatch
from workflow_state import Dispatch


class TestSoftKillDispatch(unittest.TestCase):
    def test_dispatch_carries_the_soft_kill_error_status(self):
        dispatch = _soft_kill_dispatch()
        self.assertIsInstance(dispatch, Dispatch)
        self.assertEqual(dispatch.status, "soft_kill")

    def test_dispatch_is_not_a_normal_advance(self):
        self.assertNotEqual(_soft_kill_dispatch().status, "advance")

    def test_dispatch_explains_the_timeout(self):
        self.assertTrue(_soft_kill_dispatch().reason)


if __name__ == "__main__":
    unittest.main()
