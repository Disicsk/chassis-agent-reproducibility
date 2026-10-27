"""Run the released offline unit tests with a writable local temporary directory."""
import os, socket, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
tmp=ROOT/'.repro/test_tmp';tmp.mkdir(parents=True,exist_ok=True)
tempfile.tempdir=str(tmp)
def blocked(*args,**kwargs):raise RuntimeError('network_disabled_for_offline_tests')
socket.socket.connect=blocked;socket.create_connection=blocked
suite=unittest.defaultTestLoader.loadTestsFromNames([
 'prototype.tests.test_prototype','prototype.tests.test_dual_power','prototype.tests.test_chat_api',
 'prototype.process_comparison.test_study','prototype.chassis_skill_v4.test_decision','prototype.chassis_skill_v4.test_runtime'])
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
