"""Human gate with a repo-local workflow override present.

Regression: the gate check resolved workflows through a single directory, so a
consumer repo with ANY file in ``.nebuko/workflows/`` hid the shipped
``default.yaml``. The check then failed to load the workflow, returned False,
and an ``advance`` out of the gated ``plan`` step went straight to
``implement`` without a human approval.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import default_paths
from build_output import _load_gated_step_def, apply_human_gate_intercept
from workflow_state import Dispatch

_SHIPPED = Path(default_paths.checkout_root()) / "defaults" / "workflows"

_UNGATED_DEFAULT = """\
name: default
steps:
  - name: plan
    agent: architect
"""


class TestGateWithLocalOverrides(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.local = Path(self.repo) / ".nebuko" / "workflows"
        self.local.mkdir(parents=True)
        self._cwd = os.getcwd()
        os.chdir(self.repo)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("WORKFLOWS_DIR", None)

    def tearDown(self):
        self._env.stop()
        os.chdir(self._cwd)
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_unrelated_local_workflow_does_not_hide_shipped_default(self):
        shutil.copy(_SHIPPED / "review-only.yaml", self.local / "my-custom.yaml")
        self.assertTrue(_load_gated_step_def("default", "plan"))

    def test_advance_on_gated_step_is_held_with_local_overrides(self):
        shutil.copy(_SHIPPED / "review-only.yaml", self.local / "my-custom.yaml")
        result = apply_human_gate_intercept(
            Dispatch(status="advance"), workflow_name="default", workflow_step="plan"
        )
        self.assertEqual(result.status, "hold")
        self.assertEqual(result.reason, "human_gate:plan")

    def test_local_workflow_of_same_name_takes_precedence(self):
        (self.local / "default.yaml").write_text(_UNGATED_DEFAULT)
        self.assertFalse(_load_gated_step_def("default", "plan"))

    def test_workflow_dirs_reports_both_layers(self):
        shipped, local = default_paths.workflow_dirs()
        self.assertEqual(Path(shipped).resolve(), _SHIPPED.resolve())
        self.assertEqual(Path(local).resolve(), self.local.resolve())

    def test_workflow_dirs_without_local_layer(self):
        shutil.rmtree(self.local)
        shipped, local = default_paths.workflow_dirs()
        self.assertEqual(Path(shipped).resolve(), _SHIPPED.resolve())
        self.assertIsNone(local)

    def test_workflows_dir_env_pins_shipped_layer(self):
        os.environ["WORKFLOWS_DIR"] = str(_SHIPPED)
        shutil.copy(_SHIPPED / "review-only.yaml", self.local / "my-custom.yaml")
        self.assertTrue(_load_gated_step_def("default", "plan"))


if __name__ == "__main__":
    unittest.main()
