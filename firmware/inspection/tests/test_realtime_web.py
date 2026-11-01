import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "JavaScript checks require the existing Node.js runtime")
class RealtimeWebTests(unittest.TestCase):
    def page(self):
        html = (Path(__file__).resolve().parents[1] / "web/realtime.html").read_text(encoding="utf-8")
        return html, re.search(r"<script>(.*?)</script>", html, re.S).group(1)

    def test_javascript_syntax_and_unique_ids(self):
        html, source = self.page()
        identifiers = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        result = subprocess.run([shutil.which("node"), "--check"], input=source, capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_automatic_start_or_stale_waveform_on_disconnect(self):
        html, source = self.page()
        identifiers = re.findall(r'\bid="([^"]+)"', html)
        check = r'''
const assert=require('node:assert/strict');
const vm=require('node:vm');
const nodes=new Map(IDENTIFIERS.map(name=>[name,{style:{},value:name==='range'?'40':'128',checked:false,
    disabled:false,textContent:'',addEventListener:()=>{},getBoundingClientRect:()=>({width:800,height:360}),
    getContext:()=>new Proxy({},{get:()=>()=>{},set:()=>true})}]));
const requests=[];
const context={console,Uint8Array,devicePixelRatio:1,setTimeout:()=>0,
    crypto:{getRandomValues:values=>values.fill(1)},
    window:{addEventListener:()=>{}},document:{hidden:false,addEventListener:()=>{},getElementById:name=>nodes.get(name)},
    fetch:path=>{requests.push(path);return new Promise(()=>{});}};
vm.createContext(context);vm.runInContext(SOURCE,context);
assert.deepEqual(requests,['/api/live']);
vm.runInContext('render({available:true,active:true,state:"RUNNING",fresh:true,record:{minimum:55,maximum:207,sequence:17,gain_code:128},preview:{bins:[[55,207]],stride:16,trigger_index:0,sample_period_us:0.04},diagnosis:{status:"UNCALIBRATED",calibrated:false,reason:"needs_calibration",depth_mm:null}})',context);
assert.equal(nodes.get('amplitude').textContent,152);
assert.equal(nodes.get('depth').textContent,'—');
assert.equal(nodes.get('empty').style.display,'none');
vm.runInContext('current.diagnosis={status:"UNCALIBRATED",calibrated:false,reason:"flaw_gate_threshold_crossed",flaw_gate_crossed:true,depth_mm:null,estimated_depth_mm:22.1};render(current)',context);
assert.equal(nodes.get('diagnosis').textContent,'可疑反射（参考）');
assert.equal(nodes.get('depth').textContent,'≈ 22.10 mm（参考）');
vm.runInContext('current.diagnosis.status="INVALID";render(current)',context);
assert.equal(nodes.get('depth').textContent,'—');
vm.runInContext('current.diagnosis={status:"UNCALIBRATED",calibrated:false,reason:"normal_reference_overlap",depth_mm:null};render(current)',context);
assert.equal(nodes.get('diagnosis').textContent,'未见新增反射');
assert.equal(nodes.get('depth').textContent,'—');
for(const [reason,label] of [['insufficient_baseline_select_a_verified_quiet_gate','静区未配置'],['baseline_gate_not_quiet','静区噪声过大'],['backwall_missing_check_coupling','未见有效底波']]){
    vm.runInContext('current.diagnosis={status:"INVALID",calibrated:false,depth_mm:null,reason:'+JSON.stringify(reason)+'};render(current)',context);
    assert.equal(nodes.get('diagnosis').textContent,label);assert.equal(nodes.get('depth').textContent,'—');
}
vm.runInContext('render({available:true,active:false,state:"STOPPED",fresh:false})',context);
assert.equal(nodes.get('amplitude').textContent,'—');
assert.equal(nodes.get('sequence').textContent,'—');
assert.equal(nodes.get('empty').style.display,'grid');
assert.equal(nodes.get('start').disabled,true);
console.log('REALTIME_WEB_PASS: no auto-arm, no uncalibrated depth, no stale LIVE after stop');
'''
        check = check.replace("IDENTIFIERS", json.dumps(identifiers)).replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=check, capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
