import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('legacy_ui', ROOT / 'legacy_ui_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
limits = json.loads((ROOT / 'limits.json').read_text())['channels']
homes = [1600,1450,1600,1550,850,2200,1450,1450,1500,1500]

class FakeUpstream:
    def __init__(self):
        self.calls = []
        self.state = {'ok': True, 'channels':[{'id':i,'min':250,'max':2600,'home':v} for i,v in enumerate(homes)], 'values':{str(i):v for i,v in enumerate(homes)}, 'active':{str(i):i<6 for i in range(10)}, 'sync':{'active':False}, 'saved_positions':[]}
    def __call__(self,path,body=None):
        if body is not None:
            self.calls.append((path,copy.deepcopy(body)))
            if path == '/api/set': self.state['values'][str(body['channel'])]=body['us']
            elif path == '/api/release': self.state['active'][str(body['channel'])]=False
            elif path == '/api/center': self.state['values'][str(body['channel'])]=homes[body['channel']]
            elif path == '/api/move_saved': self.state['active']={str(i):True for i in range(10)}
        return copy.deepcopy(self.state)

class LegacyUIBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeUpstream()
        self.controller = module.Controller(limits,self.fake)
    def test_nudge_cannot_cross_upper_or_lower_limit(self):
        self.fake.state['values']['0']=1690
        self.controller.execute('/api/nudge',{'channel':0,'delta':200})
        self.assertEqual(self.fake.calls[-1],('/api/set',{'channel':0,'us':1700}))
        self.fake.state['values']['0']=1210
        self.controller.execute('/api/nudge',{'channel':0,'delta':-200})
        self.assertEqual(self.fake.calls[-1],('/api/set',{'channel':0,'us':1200}))
    def test_center_only_changes_eyes(self):
        self.controller.execute('/api/center',{})
        self.assertEqual([body['channel'] for _,body in self.fake.calls],list(range(6)))

    def test_latest_horizontal_limits_clamp_manual_adjustment(self):
        self.fake.state['values']['1']=2190
        self.controller.execute('/api/nudge',{'channel':1,'delta':200})
        self.assertEqual(self.fake.calls[-1],('/api/set',{'channel':1,'us':2200}))
        self.fake.state['values']['2']=1110
        self.controller.execute('/api/nudge',{'channel':2,'delta':-200})
        self.assertEqual(self.fake.calls[-1],('/api/set',{'channel':2,'us':1100}))
    def test_release_only_changes_eyes(self):
        self.controller.execute('/api/release',{})
        self.assertEqual([body['channel'] for _,body in self.fake.calls],list(range(6)))
    def test_brow_and_nonfinite_inputs_rejected_without_writes(self):
        for payload in [{'channel':6,'delta':50},{'channel':True,'delta':50},{'channel':0,'delta':float('nan')},{'channel':0,'delta':1000}]:
            with self.assertRaises(module.Rejected):self.controller.execute('/api/nudge',payload)
        self.assertEqual(self.fake.calls,[])
    def test_saved_pose_outside_limits_rejected_without_writes(self):
        values=copy.deepcopy(self.fake.state['values']);values['4']=2100
        self.fake.state['saved_positions']=[{'id':0,'values':values}]
        with self.assertRaises(module.Rejected):self.controller.execute('/api/move_saved',{'to_id':'0','duration':0.7})
        self.assertEqual(self.fake.calls,[])
    def test_saved_pose_cannot_change_brows(self):
        values=copy.deepcopy(self.fake.state['values']);values['6']=1500
        self.fake.state['saved_positions']=[{'id':0,'values':values}]
        with self.assertRaises(module.Rejected):self.controller.execute('/api/move_saved',{'to_id':'0','duration':0.7})
        self.assertEqual(self.fake.calls,[])
    def test_valid_pose_preserves_brow_release(self):
        self.fake.state['saved_positions']=[{'id':0,'values':copy.deepcopy(self.fake.state['values'])}]
        self.controller.execute('/api/move_saved',{'to_id':'0','duration':0.7})
        self.assertEqual(self.fake.calls[0][0],'/api/move_saved')
        self.assertEqual([body['channel'] for _,body in self.fake.calls[1:]],list(range(6,10)))
        self.assertTrue(all(not self.fake.state['active'][str(i)] for i in range(6,10)))
    def test_current_pose_outside_limits_not_moved(self):
        self.fake.state['values']['0']=1800
        with self.assertRaises(module.Rejected):self.controller.execute('/api/nudge',{'channel':0,'delta':-50})
        self.assertEqual(self.fake.calls,[])
    def test_active_sync_rejected_except_release(self):
        self.fake.state['sync']['active']=True
        with self.assertRaises(module.Rejected):self.controller.execute('/api/center',{})
        self.assertEqual(self.fake.calls,[])
        self.controller.execute('/api/release',{'channel':0})
        self.assertEqual(len(self.fake.calls),1)
    def test_public_state_has_six_calibrated_ranges(self):
        state=self.controller.public_state(self.fake.state)
        self.assertEqual(len(state['channels']),6)
        for channel in state['channels']:
            self.assertEqual(channel['min'],limits[str(channel['id'])]['min'])
            self.assertEqual(channel['max'],limits[str(channel['id'])]['max'])
        self.assertEqual(self.fake.state['channels'][0]['min'],250)

if __name__ == '__main__': unittest.main()
