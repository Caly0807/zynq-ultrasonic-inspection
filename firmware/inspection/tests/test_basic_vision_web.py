import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "web/camera_basic.html"
HARNESS = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
let input = '';
process.stdin.setEncoding('utf8');process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  try {
    const fixture = JSON.parse(input), nodes = new Map(), operations = [], timers = [], markerArcs = [];
    const drawings = new Proxy({}, {get: (target, key) => target[key] || ((...arguments) => {
      for (const value of arguments) if (typeof value === 'number') assert(Number.isFinite(value), key);
      if(key==='arc'&&arguments[2]===14)markerArcs.push(arguments);
    })});
    function element(identifier) {
      return {identifier, style:{}, checked:false, disabled:false, textContent:'', className:'', events:{},
        width:640, height:480, getContext:()=>drawings, getBoundingClientRect:()=>({left:0,top:0,width:640,height:480}),
        addEventListener(name, callback){this.events[name]=callback;}, click(){operations.push(['download',this.download]);},
        toBlob(callback){callback({fixture:true});}};
    }
    for (const identifier of fixture.identifiers) nodes.set(identifier, element(identifier));
    let current={preview_available:false,source:'UNAVAILABLE',calibrated:false,position_mm:null,error:'No camera'}, failed=false;
    function copy(value){return JSON.parse(JSON.stringify(value));}
    const runtime = {
      console, document:{getElementById:identifier=>{assert(nodes.has(identifier),identifier);return nodes.get(identifier);},createElement:element},
      Image:class {constructor(){this.naturalWidth=640;this.naturalHeight=480;}set src(value){Promise.resolve().then(()=>this.onload());}},
      URL:{createObjectURL:()=> 'blob:host-fixture',revokeObjectURL:()=>{}},
      setInterval:callback=>{timers.push(callback);return timers.length;},setTimeout:()=>0,
      fetch:async(route,options={})=>{
        operations.push([options.method||'GET',route,options.body]);
        if (failed) return {ok:false,json:async()=>({error:'Device login required'})};
        if (route.startsWith('/api/vision/frame.png')) return {ok:true,blob:async()=>({fixture:true})};
        if (route==='/api/vision/freeze') current.selection={...current.frame,calibration_id:'fixture-selection'};
        if (route==='/api/vision/calibrate') {
          const payload=JSON.parse(options.body);assert.equal(payload.fixed_confirmed,true);assert.equal(payload.corners.length,4);
          assert.equal(payload.calibration_id,'fixture-selection');
          current.calibrated=true;current.position_mm=[40,40];current.corners=payload.corners;delete current.selection;
        }
        if (route==='/api/vision/clear'){current.calibrated=false;current.position_mm=null;delete current.corners;delete current.selection;}
        return {ok:true,json:async()=>copy(current)};
      }
    };
    vm.createContext(runtime);vm.runInContext(fixture.source,runtime);
    async function settled(){for(let turn=0;turn<25;turn++)await Promise.resolve();}
    async function refresh(){await settled();await runtime.refresh();await settled();}
    await settled();assert.equal(nodes.get('positionX').textContent,'—');assert(nodes.get('freeze').disabled);
    current={preview_available:true,source:'USB_CAMERA',calibrated:false,position_mm:null,
      frame:{frame_id:'camera-1',width:640,height:480,sequence:1,age_ms:1},marker:{status:'FOUND',pixel:[320,240]}};
    await refresh();assert.equal(nodes.get('source').textContent,'USB CAMERA');assert(!nodes.get('freeze').disabled);
    assert.equal(nodes.get('placeholder').style.display,'none');assert.equal(nodes.get('positionX').textContent,'—');
    await nodes.get('freeze').onclick();await settled();assert(!nodes.get('resume').disabled);
    const click=nodes.get('view').events.click;
    for(const point of [[50,50],[590,50],[590,430],[50,430]])click({clientX:point[0],clientY:point[1]});
    assert(nodes.get('calibrate').disabled);nodes.get('fixed').checked=true;nodes.get('fixed').onchange();
    assert(!nodes.get('calibrate').disabled);await nodes.get('calibrate').onclick();await settled();
    assert.equal(nodes.get('positionX').textContent,'40.0');assert(nodes.get('resume').disabled);
    current.frame.frame_id='camera-2';current.marker={status:'AMBIGUOUS',pixel:null};current.position_mm=null;
    await refresh();assert.equal(nodes.get('positionX').textContent,'—');assert(nodes.get('status').textContent.includes('多个'));
    const previousArcs=markerArcs.length;
    for(const status of ['NOT_FOUND','AMBIGUOUS','OUTSIDE_WORKPIECE']){
      current.marker={status,pixel:[320,240]};await refresh();assert.equal(markerArcs.length,previousArcs);
    }
    current.source='HOST_FIXTURE';await refresh();assert(nodes.get('source').textContent.includes('非相机'));
    failed=true;await refresh();assert.equal(nodes.get('positionX').textContent,'—');assert(nodes.get('save').disabled);
    assert.equal(nodes.get('placeholder').style.display,'grid');assert(nodes.get('status').textContent.includes('Device login required'));
    failed=false;current={preview_available:false,source:'UNAVAILABLE',calibrated:false,position_mm:null,error:'Camera disconnected'};
    await refresh();assert(nodes.get('freeze').disabled);assert(nodes.get('save').disabled);
    assert(operations.every(item=>item[0]!=='POST'||['/api/vision/freeze','/api/vision/calibrate','/api/vision/clear'].includes(item[1])));
    console.log('BASIC_VISION_BROWSER_LOGIC_PASSED; no camera, acquisition, HV or motion opened');
  } catch(error) { console.error(error);process.exitCode=1; }
});
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript checks use the existing Node.js runtime")
class BasicVisionWebTests(unittest.TestCase):
    def source(self):
        page = PAGE.read_text(encoding="utf-8")
        identifiers = re.findall(r'\bid="([^"]+)"', page)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        return re.search(r"<script>(.*?)</script>", page, re.S).group(1), identifiers

    def test_camera_page_is_self_contained_and_has_valid_javascript(self):
        source, _ = self.source()
        result = subprocess.run([shutil.which("node"), "--check"], input=source, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotRegex(PAGE.read_text(encoding="utf-8"), r'<(?:script|link)[^>]+(?:src|href)="https?://')

    def test_calibration_disconnect_and_fixture_labels_in_browser_logic(self):
        source, identifiers = self.source()
        result = subprocess.run([shutil.which("node"), "-e", HARNESS],
                                input=json.dumps({"source": source, "identifiers": identifiers}, ensure_ascii=False),
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BASIC_VISION_BROWSER_LOGIC_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
