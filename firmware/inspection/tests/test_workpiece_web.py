import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from utinspect.visual_selftest import demo_workpiece_image
from utinspect.workpiece import VisualSettings, WorkpieceSpec
from utinspect.workpiece_app import WorkpieceSession


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "JavaScript checks require the existing Node.js runtime")
class WorkpieceWebTests(unittest.TestCase):
    def test_page_javascript_is_valid_and_element_identifiers_are_unique(self):
        html = (ROOT / "web/workpiece.html").read_text(encoding="utf-8")
        identifiers = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        source = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        result = subprocess.run([shutil.which("node"), "--check"], input=source, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_letterbox_click_mapping_and_state_rendering_without_a_browser_or_camera(self):
        html = (ROOT / "web/workpiece.html").read_text(encoding="utf-8")
        source = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        identifiers = re.findall(r'\bid="([^"]+)"', html)
        verification = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const nodes = new Map(input.identifiers.map(identifier => [identifier, {
  width:640, height:480, style:{}, checked:false, value:'', disabled:false,
  getContext:() => ({}), addEventListener:() => {}, replaceChildren:() => {}, append:() => {}
}]));
const context = {console, setTimeout:() => 0, clearTimeout:() => {},
  fetch:() => new Promise(() => {}),
  document:{getElementById:identifier => {
    assert(nodes.has(identifier), 'Missing HTML element: ' + identifier);
    return nodes.get(identifier);
  }}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
function equalPixel(actual, expected) {
  assert(actual);
  assert(Math.abs(actual[0] - expected[0]) < 0.001);
  assert(Math.abs(actual[1] - expected[1]) < 0.001);
}
equalPixel(context.imagePixel(410,170,{left:10,top:20,width:800,height:300},640,480), [320,240]);
assert.equal(context.imagePixel(100,170,{left:10,top:20,width:800,height:300},640,480), null);
equalPixel(context.imagePixel(200,300,{left:0,top:0,width:400,height:600},640,480), [320,240]);
assert.equal(context.imagePixel(200,100,{left:0,top:0,width:400,height:600},640,480), null);
assert.equal(context.imagePixel(640,240,{left:0,top:0,width:640,height:480},640,480), null);
assert.equal(context.imagePixel(0,0,{left:0,top:0,width:0,height:0},640,480), null);
const requests = [];
context.fetch = async (route, options) => {
  if (route === '/api/state') return {ok:true,json:async () => input.state};
  requests.push(JSON.parse(options.body));
  return {ok:true,json:async () => ({ok:true})};
};
(async () => {
  await context.updateState();
  assert(nodes.get('probeStatus').textContent.includes('圆形外观候选'));
  assert.equal(nodes.get('contact').disabled, true);
  assert.equal(nodes.get('confirmed').checked, true);
  nodes.get('length').oninput();
  assert.equal(nodes.get('confirmed').checked, false);
  assert.equal(nodes.get('apiVersion').textContent, 'VISUAL API / 2');
  assert(nodes.get('frameDebug').textContent.includes('离线图片'));
  input.state = {...input.state, frozen:true};
  await context.updateState();
  assert(nodes.get('connectionText').textContent.includes('非实时'));
  vm.runInContext("displayedFrameId = 'camera-session:327'", context);
  input.state = {...input.state, source:'LIVE_CAMERA', frame_id:'camera-session:327', frame_count:900,
    frozen:true, display_age_s:220.5};
  await context.updateState();
  assert(nodes.get('frameInfo').textContent.includes('冻结帧 327'));
  assert(!nodes.get('frameInfo').textContent.includes('900'));
  assert(nodes.get('frameInfo').title.includes('后台累计 900 帧'));
  assert.equal(nodes.get('connection').className, 'badge frozen');
  assert.equal(nodes.get('freeze').className, 'primary');
  assert(nodes.get('guidance').textContent.includes('220.5 秒'));
  assert(nodes.get('guidance').textContent.includes('移动探头不会更新画面'));
  input.state = {...input.state, frozen:false, display_probe:null, probe:null,
    probe_reason:'round_probe_missing', probe_candidates:[], focus_warning:false};
  await context.updateState();
  assert(nodes.get('frameInfo').textContent.includes('显示帧 327'));
  assert.equal(nodes.get('connection').className, 'badge');
  assert.equal(nodes.get('freeze').className, '');
  assert(nodes.get('guidance').textContent.includes('请将探头放入青色顶面框内'));
  assert(nodes.get('probeDebug').textContent.includes('未检出不等于失焦'));
  assert.equal(nodes.get('mark').disabled, true);
  input.state = {...input.state, face:null, candidates:[], probe_reason:'select_workpiece_face'};
  await context.updateState();
  assert.equal(nodes.get('faceStatus').textContent, '待选顶面 · 可手选四角');
  assert(nodes.get('guidance').textContent.includes('手动圈选四角'));
  assert.equal(await context.ensureFrozen(), true);
  assert.equal(requests[0].action, 'freeze');
  input.state = {...input.state, visual_api_version:undefined};
  await context.updateState();
  assert(nodes.get('connectionText').textContent.includes('需要重启'));
  assert.equal(nodes.get('probe').disabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        with tempfile.TemporaryDirectory() as directory:
            session = WorkpieceSession(demo_workpiece_image(), Path(directory) / "not_saved", WorkpieceSpec(dimensions_confirmed=True),
                                       visual_settings=VisualSettings(probe_mode="round"))
            try:
                session.action({"action": "lock_auto"})
                state = session.state()
            finally:
                session.close()
            source_path, verification_path, state_path = (Path(directory) / filename for filename in ("page.js", "verify.js", "input.json"))
            source_path.write_text(source, encoding="utf-8")
            verification_path.write_text(verification, encoding="utf-8")
            state_path.write_text(json.dumps({"identifiers": identifiers, "state": state}), encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(verification_path), str(source_path), str(state_path)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
