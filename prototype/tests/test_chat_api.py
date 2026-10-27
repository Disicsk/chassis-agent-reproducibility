import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prototype.chat_api import complete,compact_context,load_config,public_settings,ModelServiceError
from prototype.workflow import Workflow
from prototype.experiment import fixtures

class FakeResponse:
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return json.dumps(self.payload).encode()

class ChatAdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'provider':'test','model':'requested','api_key':'TEST_SECRET_SENTINEL_NOT_REAL',
                  'base_url':'https://example.org','api_mode':'chat_completions','allowed_response_models':['requested']}
        self.response={'model':'requested','choices':[{'finish_reason':'stop','message':{'content':'{"status":"ok"}'}}]}

    def test_secret_only_in_authorization(self):
        with patch('prototype.chat_api.build_opener') as b:
            b.return_value.open.return_value=FakeResponse(self.response)
            r=complete(self.cfg,[{'role':'user','content':'Return JSON'}])
            request=b.return_value.open.call_args.args[0]
            self.assertIn(self.cfg['api_key'],request.headers['Authorization'])
            self.assertNotIn(self.cfg['api_key'],json.dumps(r))
            self.assertNotIn(self.cfg['api_key'],json.dumps(public_settings(self.cfg)))

    def test_unexpected_model_rejected(self):
        self.response['model']='other'
        with patch('prototype.chat_api.build_opener') as b:
            b.return_value.open.return_value=FakeResponse(self.response)
            with self.assertRaises(ModelServiceError):complete(self.cfg,[])

    def test_response_secret_is_discarded(self):
        self.response['choices'][0]['message']['content']=self.cfg['api_key']
        with patch('prototype.chat_api.build_opener') as b:
            b.return_value.open.return_value=FakeResponse(self.response)
            with self.assertRaises(ModelServiceError):complete(self.cfg,[])

    def test_fixture_expected_and_hidden_values(self):
        fs=list(fixtures());self.assertEqual(len(fs),8)
        for s in fs:
            if s['task']=='infeasible':self.assertIsNone(s['expected_final'])
            if s['task']=='missing':
                self.assertNotIn('working_mass_kg',s['initial_case']['requirements'])
                self.assertNotIn('travel_shaft_power_budget_kw',s['initial_case']['requirements'])
            if s['task']=='revision':self.assertNotEqual(s['expected_initial'],s['expected_final'])

    def test_compaction_preserves_constraint_failure(self):
        with tempfile.TemporaryDirectory() as d:
            f=Workflow.create(Path(d)/'run','test')
            f.state['last_tool_result']={'name':'check_constraints','result':{'status':'constraint_failed','selected':None,
                'candidates':[{'candidate_id':'test','passes_modeled_checks':False,'failures':[{'constraint':'flow','required':10,'available':5}]}]}}
            r=compact_context(f)['last_tool_result']['result']
            self.assertEqual(r['failed_constraint_counts'],{'flow':1})
            self.assertEqual(r['candidate_details'][0]['failures'][0]['required'],10)

    def test_completion_flags_follow_current_version(self):
        with tempfile.TemporaryDirectory() as d:
            f=Workflow.create(Path(d)/'run','test')
            f.state['version']=3;f.state['validated_version']=2
            self.assertFalse(compact_context(f)['execution_progress']['input_validated_for_current_version'])
            f.state['validated_version']=3
            self.assertTrue(compact_context(f)['execution_progress']['input_validated_for_current_version'])

if __name__=='__main__':unittest.main()
