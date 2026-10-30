import json
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from utinspect.echo import EchoConfig
from utinspect.mapping import MapSession, MapSpec, embedded_csv, synthetic_map_waveform


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const source = fs.readFileSync(process.argv[2], 'utf8');
function create(offline, storage=new Map()) {
  const drawings = [], downloads = [], blobs = [], nodes = new Map(), timers=[];
  const canvasContext = new Proxy({}, {
    get(target, key) {
      if (key in target) return target[key];
      return (...arguments) => {
        for (const value of arguments) if (typeof value === 'number') assert(Number.isFinite(value), key);
        drawings.push([key, ...arguments]);
      };
    }
  });
  function element(identifier='') {
    return {textContent:'', value:'', checked:false, disabled:false, files:[], style:{}, className:'',
      width:640, height:360, events:{}, children:[],
      addEventListener(name, callback) {this.events[name]=callback;},
      getBoundingClientRect:() => ({left:0,top:0,width:640,height:360}),
      getContext:() => canvasContext, setPointerCapture:() => {},
      replaceChildren() {this.children=[];}, append(item) {this.children.push(item);},
      toDataURL:() => 'data:image/png;base64,synthetic-test',
      click() {downloads.push({name:this.download,href:this.href});}
    };
  }
  for (const identifier of input.identifiers) nodes.set(identifier,element(identifier));
  nodes.get('boot').textContent=JSON.stringify(offline?input.state:null);
  const context = {console,Blob,JSON,Math,Number,Date,Error,AbortController,encodeURIComponent,
    crypto:{randomUUID:require('node:crypto').randomUUID},
    sessionStorage:{getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    setTimeout(callback,delay){const timer={callback,delay,cancelled:false};timers.push(timer);return timer;},clearTimeout(timer){if(timer)timer.cancelled=true;},
    fetch:() => new Promise(() => {}),
    URL:{createObjectURL(blob) {blobs.push(blob);return 'blob:test'+blobs.length;},revokeObjectURL:() => {}},
    location:{href:''},window:{devicePixelRatio:2,addEventListener:() => {}},
    document:{getElementById(identifier) {assert(nodes.has(identifier),'Missing element '+identifier);return nodes.get(identifier);},
      createElement:element,documentElement:{outerHTML:input.html}}
  };
  vm.createContext(context);
  vm.runInContext(source,context);
  return {context,nodes,drawings,downloads,blobs,timers,storage,evaluate:code => vm.runInContext(code,context)};
}
async function verify() {
  const view=create(true), {context,nodes,evaluate}=view;
  assert(nodes.get('sourceLabel').textContent.includes('DEMO'));
  assert(nodes.get('acquire').disabled);
  assert.equal(nodes.get('pointSource').textContent,'DEMO');
  assert(nodes.get('pointDepth').textContent.startsWith(input.state.points[0].reflector_depth_mm.toFixed(2)));
  assert.equal(nodes.get('positionX').value,'27.50');
  assert(view.drawings.length>100);
  context.selectCell(15,15);
  assert.equal(nodes.get('pointStatus').textContent,'该网格尚无记录');
  assert.equal(nodes.get('pointDepth').textContent,'— mm');
  assert(nodes.get('downloadWave').disabled);
  context.refreshView();
  assert.equal(nodes.get('pointStatus').textContent,'该网格尚无记录');
  nodes.get('positionConfirmed').checked=true;
  context.updateButton();
  assert(nodes.get('acquire').disabled);
  context.selectCell(15,0);context.nextCell();
  assert.equal(evaluate('selectedColumn'),15);assert.equal(evaluate('selectedRow'),1);
  context.nextCell();assert.equal(evaluate('selectedColumn'),14);
  context.selectCell(0,1);context.nextCell();assert.equal(evaluate('selectedColumn'),0);assert.equal(evaluate('selectedRow'),2);
  nodes.get('mapCanvas').events.click({clientX:-50,clientY:0});
  assert.equal(evaluate('selectedRow'),2);
  const bounds=evaluate('mapBounds');
  nodes.get('mapCanvas').events.click({clientX:bounds.left+7.5*bounds.scale,clientY:bounds.top+7.5*bounds.scale});
  assert.equal(evaluate('selectedColumn'),1);assert.equal(evaluate('selectedRow'),1);
  nodes.get('volumeCanvas').events.pointerdown({clientX:100,clientY:100,pointerId:1});
  nodes.get('volumeCanvas').events.pointermove({clientX:130,clientY:120});
  assert.notEqual(evaluate('yaw'),-.64);
  nodes.get('volumeCanvas').events.pointerup();
  nodes.get('volumeCanvas').events.pointerdown({clientX:100,clientY:100,pointerId:1});
  nodes.get('volumeCanvas').events.lostpointercapture();
  const capturedYaw=evaluate('yaw');
  nodes.get('volumeCanvas').events.pointermove({clientX:200,clientY:200});
  assert.equal(evaluate('yaw'),capturedYaw);
  nodes.get('resetView').onclick();assert.equal(evaluate('yaw'),-.64);
  nodes.get('depthMode').onclick();assert.equal(evaluate('colorMode'),'depth');
  assert.equal(context.cellColor(input.state.cells.at(-1)),'#203044');
  nodes.get('exportJson').onclick();
  const exported=JSON.parse(await view.blobs.at(-1).text());
  assert.equal(exported.source,'DEMO');assert.equal(exported.points[0].hole_height_mm,null);
  nodes.get('exportCsv').onclick();
  const csv=await view.blobs.at(-1).text();
  assert.equal(csv.split('\n')[0].split(',').at(-2),'DEMO');
  assert.equal(csv.split('\n').slice(1).join('\n'),input.csv.split('\n').slice(1).join('\n'));
  nodes.get('exportPng').onclick();
  assert.equal(view.downloads.at(-1).name,'UT_DEMO_map.png');
  assert(view.drawings.some(call=>call[0]==='fillText'&&String(call[1]).includes('DEMO')));
  nodes.get('exportReport').onclick();assert.equal(view.downloads.at(-1).name,'inspection_report.html');

  const online=create(false), liveNodes=online.nodes, live=online.context;
  let state=structuredClone(input.state), requested=[];
  live.fetch=async(route,options) => {
    if(route==='/api/state') return {ok:true,json:async()=>structuredClone(state)};
    if(route==='/api/point') {
      const payload=JSON.parse(options.body);requested.push(payload);state=structuredClone(input.next_state);
      state.points.at(-1).submission_id=payload.submission_id;
      return {ok:true,json:async()=>({point:state.points.at(-1),submission_id:payload.submission_id,replayed:false})};
    }
    throw new Error('Unexpected route '+route);
  };
  await live.poll();
  assert(liveNodes.get('acquire').disabled);
  liveNodes.get('positionX').value='';liveNodes.get('positionConfirmed').checked=true;live.updateButton();
  assert(liveNodes.get('acquire').disabled);
  liveNodes.get('positionX').value='80.01';live.updateButton();assert(liveNodes.get('acquire').disabled);
  liveNodes.get('positionX').value='2.5';liveNodes.get('positionY').value='2.5';
  liveNodes.get('positionConfirmed').checked=true;live.updateButton();
  assert(!liveNodes.get('acquire').disabled);
  live.fetch=async()=>{throw new Error('Synthetic network failure');};
  await live.poll();assert(liveNodes.get('acquire').disabled);
  liveNodes.get('positionConfirmed').onchange();assert(liveNodes.get('acquire').disabled);
  live.fetch=async(route,options) => {
    if(route==='/api/state')return {ok:true,json:async()=>structuredClone(state)};
    const payload=JSON.parse(options.body);requested.push(payload);state=structuredClone(input.next_state);
    state.points.at(-1).submission_id=payload.submission_id;
    return {ok:true,json:async()=>({point:state.points.at(-1),submission_id:payload.submission_id,replayed:false})};
  };
  await live.poll();
  liveNodes.get('positionX').value='42.5';liveNodes.get('positionY').value='62.5';
  liveNodes.get('positionConfirmed').checked=true;liveNodes.get('advance').checked=false;live.updateButton();
  await liveNodes.get('acquire').onclick();
  assert.deepEqual(requested[0].position_mm,[42.5,62.5]);
  assert.equal(requested[0].session_id,input.state.session_id);
  assert.equal(online.evaluate('selectedColumn'),8);assert.equal(online.evaluate('selectedRow'),12);
  assert.equal(liveNodes.get('pointPosition').textContent,'42.50 / 62.50 mm');
  assert(!liveNodes.get('positionConfirmed').checked);
  assert(liveNodes.get('acquire').disabled);
  const savedCount=online.evaluate('snapshot.point_count');
  state=structuredClone(input.state);await live.poll();
  assert.equal(online.evaluate('snapshot.point_count'),savedCount);
  state=structuredClone(input.next_state);
  live.selectRecord(state.points[0]);
  assert.equal(liveNodes.get('positionX').value,state.points[0].position_mm[0].toFixed(2));
  assert(!liveNodes.get('positionConfirmed').checked);
  let prevented=0;
  liveNodes.get('mapCanvas').events.keydown({key:'ArrowRight',preventDefault(){prevented++;}});
  assert.equal(prevented,1);
  assert.equal(online.evaluate('selectedColumn'),6);
  liveNodes.get('positionConfirmed').checked=true;live.updateButton();
  let finishCapture;
  live.fetch=async(route,options)=>{
    if(route==='/api/state')return {ok:true,json:async()=>structuredClone(state)};
    requested.push(JSON.parse(options.body));
    return await new Promise(resolve=>{finishCapture=resolve;});
  };
  const selectedBefore=online.evaluate('[selectedColumn,selectedRow]');
  const pending=liveNodes.get('acquire').onclick();
  assert(liveNodes.get('positionX').disabled&&liveNodes.get('positionConfirmed').disabled&&liveNodes.get('nextCell').disabled);
  live.selectCell(0,0);live.selectRecord(state.points.at(-1));
  liveNodes.get('mapCanvas').events.keydown({key:'ArrowRight',preventDefault(){throw new Error('Busy key should not act');}});
  assert.deepEqual(online.evaluate('[selectedColumn,selectedRow]'),selectedBefore);
  finishCapture({ok:false,json:async()=>({error:'Synthetic rejected request',submission_state:'REJECTED'})});
  await pending;
  assert(!liveNodes.get('positionX').disabled&&!liveNodes.get('positionConfirmed').checked);
  assert(liveNodes.get('acquire').disabled);
  live.fetch=async()=>({ok:true,json:async()=>structuredClone(state)});
  state.session_id='new-session-for-browser-test';
  liveNodes.get('positionConfirmed').checked=true;await live.poll();
  assert(!liveNodes.get('positionConfirmed').checked);
  const acceptedSession=online.evaluate('snapshot.session_id');
  state.columns=0;await live.poll();
  assert(!online.evaluate('connected'));
  assert.equal(online.evaluate('snapshot.session_id'),acceptedSession);
  assert.equal(online.evaluate('snapshot.columns'),16);
  state=structuredClone(input.next_state);
  await live.poll();
  state.stopped=true;await live.poll();liveNodes.get('positionConfirmed').checked=true;live.updateButton();
  assert(liveNodes.get('acquire').disabled);
  state.stopped=false;state.source='LIVE';state.capture_adapter_configured=false;await live.poll();live.updateButton();
  assert(liveNodes.get('acquire').disabled);
  const preservedCount=online.evaluate('snapshot.point_count');
  for(const damage of [
    value=>value.points[0].reflector_depth_mm=31,
    value=>value.points[0].source='LIVE',
    value=>value.points[0].preview=null,
    value=>value.cells.pop(),
    value=>value.grid_coverage_percent=NaN,
    value=>value.suspect_cells=999,
    value=>value.echo=null
  ]){state=structuredClone(input.next_state);damage(state);await live.poll();assert(!online.evaluate('connected'));assert.equal(online.evaluate('snapshot.point_count'),preservedCount);assert(liveNodes.get('acquire').disabled);}
  let bodyWaiting=false;
  live.fetch=async(route,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
    bodyWaiting=true;
    const aborted=()=>{const error=new Error('Synthetic incomplete body');error.name='AbortError';reject(error);};
    if(options.signal.aborted)aborted();else options.signal.addEventListener('abort',aborted,{once:true});
  })});
  const incomplete=live.requestJson('/api/state');
  for(let step=0;step<4&&!bodyWaiting;step++)await Promise.resolve();
  assert(bodyWaiting);
  const responseTimer=online.timers.findLast(timer=>timer.delay===4000&&!timer.cancelled);
  assert(responseTimer);responseTimer.callback();
  await assert.rejects(incomplete,/响应超时/);
  assert(responseTimer.cancelled);
  await verifySubmissions();
  console.log('PASS: multi-view, source-preserving exports, acquisition guards, receipt replay, reload recovery, locked uncertainty and no automatic POST retries');
}
async function verifySubmissions(){
  const reply=(value,ok=true)=>({ok,status:ok?200:409,json:async()=>structuredClone(value)});
  const sourceState=source=>{const value=structuredClone(input.state);value.source=source;for(const point of value.points)point.source=source;return value;};
  async function prepare(source='DEMO',storage){
    const page=create(false,storage),state=sourceState(source);
    page.context.fetch=async()=>reply(state);await page.context.poll();
    page.context.selectCell(8,12);page.nodes.get('positionConfirmed').checked=true;page.nodes.get('advance').checked=true;page.context.updateButton();
    return {page,state};
  }
  const {page,state}=await prepare();
  let received=[],saved=structuredClone(input.next_state),stateNow=state;
  page.context.fetch=async(route,options)=>{
    if(route==='/api/state')return reply(stateNow);
    const payload=JSON.parse(options.body);received.push(payload);saved.points.at(-1).submission_id=payload.submission_id;
    throw new Error('Response lost after save');
  };
  await page.nodes.get('acquire').onclick();
  assert.equal(received.length,1);assert(page.evaluate('pendingSubmission')!==null);
  assert(page.nodes.get('positionX').disabled&&page.nodes.get('waveFile').disabled);
  assert(!page.nodes.get('positionConfirmed').checked);assert.equal(page.storage.size,1);
  page.context.selectCell(0,0);assert.equal(page.nodes.get('positionX').value,'42.50');
  page.nodes.get('positionX').value='2.5';page.nodes.get('positionConfirmed').checked=true;
  await page.nodes.get('acquire').onclick();assert.equal(received.length,1);
  page.nodes.get('restoreSubmission').onclick();assert.equal(page.nodes.get('positionX').value,'42.5');
  page.nodes.get('positionConfirmed').checked=true;
  page.context.fetch=async(route,options)=>{
    if(route==='/api/state')return reply(saved);
    const payload=JSON.parse(options.body);received.push(payload);
    return reply({point:saved.points.at(-1),submission_id:payload.submission_id,replayed:true});
  };
  await page.nodes.get('acquire').onclick();
  assert.deepEqual(received[1],received[0]);assert.equal(page.evaluate('pendingSubmission'),null);assert.equal(page.storage.size,0);
  assert.equal(page.evaluate('selectedColumn'),8);assert.equal(page.evaluate('selectedRow'),12);
  assert(page.nodes.get('toast').textContent.includes('没有再次采集'));

  const recorded=await prepare('RECORDED');let originalReads=0,recordedPayload;
  recorded.page.nodes.get('waveFile').files=[{size:20,text:async()=>{originalReads++;return 'original waveform csv';}}];
  recorded.page.context.fetch=async(route,options)=>{
    if(route==='/api/state')return reply(recorded.state);
    recordedPayload=JSON.parse(options.body);throw new Error('Upload response lost');
  };
  await recorded.page.nodes.get('acquire').onclick();assert.equal(originalReads,1);
  const reload=create(false,recorded.page.storage);let reloadPosts=[];
  reload.context.fetch=async(route,options)=>{
    if(route==='/api/state')return reply(recorded.state);
    reloadPosts.push(JSON.parse(options.body));return reply({error:'Synthetic rejected input',submission_state:'REJECTED'},false);
  };
  await reload.context.poll();assert.equal(reloadPosts.length,0);assert.equal(reload.nodes.get('positionX').value,'42.5');
  assert(!reload.nodes.get('positionConfirmed').checked);assert(reload.nodes.get('acquire').disabled);
  reload.nodes.get('positionConfirmed').checked=true;await reload.nodes.get('acquire').onclick();
  assert.deepEqual(reloadPosts,[recordedPayload]);assert.equal(originalReads,1);assert.equal(reload.storage.size,0);

  for(const status of ['BUSY','UNCONFIRMED','CONFLICT']){
    const uncertain=await prepare();let posts=0;
    uncertain.page.context.fetch=async(route)=>route==='/api/state'?reply(uncertain.state):(posts++,reply({error:status,submission_state:status},false));
    await uncertain.page.nodes.get('acquire').onclick();assert.equal(posts,1);
    assert.equal(uncertain.page.evaluate('pendingSubmission.state'),status);
    const restored=create(false,uncertain.page.storage);let restoredPosts=0;
    restored.context.fetch=async route=>{if(route!=='/api/state'){restoredPosts++;return reply({error:'Unexpected retry',submission_state:'UNCONFIRMED'},false);}return reply(uncertain.state);};
    await restored.context.poll();assert(restored.nodes.get('positionX').disabled);
    restored.nodes.get('positionConfirmed').checked=true;restored.context.updateButton();
    assert.equal(restored.nodes.get('acquire').disabled,status!=='BUSY');
    if(status!=='BUSY'){await restored.nodes.get('acquire').onclick();assert.equal(restoredPosts,0);}
    assert(restored.nodes.get('releaseSubmission').disabled);restored.nodes.get('releaseSubmission').onclick();assert(restored.evaluate('pendingSubmission')!==null);
    const another={...uncertain.state,session_id:'another-session'};restored.context.fetch=async()=>reply(another);await restored.context.poll();
    restored.nodes.get('positionConfirmed').checked=true;restored.context.updateButton();assert(restored.nodes.get('acquire').disabled);
    assert(!restored.nodes.get('releaseSubmission').disabled);restored.nodes.get('releaseSubmission').onclick();assert.equal(restored.storage.size,0);
    assert(!restored.nodes.get('positionConfirmed').checked);
  }

  const committed=await prepare();let committedPayload,committedState=structuredClone(input.next_state),committedPosts=0;
  committed.page.context.fetch=async(route,options)=>{
    if(route==='/api/state')throw new Error('State refresh unavailable');
    committedPosts++;committedPayload=JSON.parse(options.body);committedState.points.at(-1).submission_id=committedPayload.submission_id;
    return reply({point:committedState.points.at(-1),submission_id:committedPayload.submission_id,replayed:false});
  };
  await committed.page.nodes.get('acquire').onclick();assert.equal(committed.page.evaluate('pendingSubmission.state'),'COMMITTED');
  committed.page.nodes.get('positionConfirmed').checked=true;await committed.page.nodes.get('acquire').onclick();assert.equal(committedPosts,1);
  const receiptReload=create(false,committed.page.storage);let receiptPosts=0;
  receiptReload.context.fetch=async route=>{if(route!=='/api/state')receiptPosts++;return reply(committedState);};
  await receiptReload.context.poll();assert.equal(receiptPosts,0);assert.equal(receiptReload.evaluate('pendingSubmission'),null);
  assert.equal(receiptReload.evaluate('selectedColumn'),8);assert.equal(receiptReload.evaluate('selectedRow'),12);

  const quota=await prepare();let quotaPosts=0;
  quota.page.context.sessionStorage.setItem=()=>{throw new Error('Quota exceeded');};
  quota.page.context.fetch=async()=>{quotaPosts++;return reply({});};
  await quota.page.nodes.get('acquire').onclick();assert.equal(quotaPosts,0);assert.equal(quota.page.evaluate('pendingSubmission'),null);
  assert(quota.page.nodes.get('toast').textContent.includes('未发送采集请求'));
  const mismatch=await prepare();let mismatchPosts=0;
  mismatch.page.context.fetch=async route=>{
    if(route==='/api/state')return reply(mismatch.state);
    mismatchPosts++;return reply({point:input.next_state.points.at(-1),submission_id:'different-id',replayed:false});
  };
  await mismatch.page.nodes.get('acquire').onclick();assert.equal(mismatchPosts,1);
  assert.equal(mismatch.page.evaluate('pendingSubmission.state'),'REQUESTED');assert.equal(mismatch.page.storage.size,1);
  assert(mismatch.page.nodes.get('toast').textContent.includes('凭证'));
  const malformed=create(false,new Map([['utinspect.pending-submission.v1','not JSON']]));
  malformed.context.fetch=async()=>reply(state);await malformed.context.poll();
  malformed.nodes.get('positionConfirmed').checked=true;malformed.context.updateButton();assert(malformed.nodes.get('acquire').disabled);
  malformed.nodes.get('releaseSubmission').onclick();assert.equal(malformed.storage.size,0);assert(!malformed.nodes.get('positionConfirmed').checked);
  const duplicates=structuredClone(input.state);for(const point of duplicates.points)point.submission_id='duplicate-id';
  malformed.context.fetch=async()=>reply(duplicates);await malformed.context.poll();assert(!malformed.evaluate('connected'));

  const timeout=await prepare();let bodyWaiting=false;
  timeout.page.context.fetch=async(route,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
    bodyWaiting=true;options.signal.addEventListener('abort',()=>{const error=new Error('Interrupted response');error.name='AbortError';reject(error);},{once:true});
  })});
  const waiting=timeout.page.nodes.get('acquire').onclick();
  for(let step=0;step<4&&!bodyWaiting;step++)await Promise.resolve();assert(bodyWaiting);
  const timer=timeout.page.timers.findLast(value=>value.delay===15000&&!value.cancelled);assert(timer);timer.callback();await waiting;
  assert.equal(timeout.page.evaluate('pendingSubmission.state'),'REQUESTED');assert(timeout.page.nodes.get('positionX').disabled);
  assert(!timeout.page.nodes.get('positionConfirmed').checked);assert(timer.cancelled);
}
verify().catch(error=>{console.error(error);process.exitCode=1;});
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript checks use the existing Node.js runtime")
class MappingWebTests(unittest.TestCase):
    def test_self_contained_page_has_valid_script_and_unique_identifiers(self):
        html = (ROOT / "web/inspection.html").read_text(encoding="utf-8")
        identifiers = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertNotRegex(html, r'<(?:script|link)[^>]+(?:src|href)="https?://')
        source = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        result = subprocess.run([shutil.which("node"), "--check"], input=source, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_multi_view_and_acquisition_guards_without_camera_or_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = MapSpec(normal_incidence_confirmed=True)
            echo = replace(EchoConfig(), calibrated=True, calibration_id="DEMO_ONLY_BROWSER_TEST")
            session = MapSession(root / "session", spec, echo, "DEMO")
            for position in ((27.5, 32.5), (2.5, 2.5)):
                session.add(synthetic_map_waveform(*position, spec), *position, True)
            state = session.snapshot()
            session.add(synthetic_map_waveform(42.5, 62.5, spec), 42.5, 62.5, True)
            html = (ROOT / "web/inspection.html").read_text(encoding="utf-8")
            source = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
            (root / "source.js").write_text(source, encoding="utf-8")
            (root / "verify.cjs").write_text(HARNESS, encoding="utf-8")
            (root / "input.json").write_text(json.dumps({"identifiers": re.findall(r'\bid="([^"]+)"', html),
                "state": state, "next_state": session.snapshot(), "html": html, "csv": embedded_csv(state)}), encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(root / "verify.cjs"), str(root / "source.js"), str(root / "input.json")],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
