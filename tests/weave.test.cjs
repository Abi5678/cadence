const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();let motions=0;const q=s=>{if(!nodes.has(s))nodes.set(s,{innerHTML:'',textContent:'',className:'',dataset:{},classList:{add(v){if(v==='weave-progress')motions++},remove(){}},close(){},showModal(){}});return nodes.get(s)};
const location={hash:'#clinician',pathname:'/'};const context={CadenceModel:require('../web/model.js'),document:{querySelector:q,addEventListener(){},querySelectorAll(){return[]}},location,history:{replaceState(a,b,h){location.hash=h}},window:{addEventListener(){}},setTimeout,clearTimeout,console,Blob,URL};vm.createContext(context);
for(const file of ['weave.js','app.js'])vm.runInContext(fs.readFileSync(__dirname+'/../web/'+file,'utf8'),context);
const run=s=>vm.runInContext(s,context),html=()=>q('#app').innerHTML;
(async()=>{
assert.match(html(),/Every detail, connected/);assert.match(html(),/weave-enter/);assert.match(html(),/gap-outline/);
for(const [role,routes] of Object.entries({clinician:['clinician','review','documents','timeline'],patient:['kiosk','timeline']})){
 run(`model.switchRole('${role}')`);for(const route of routes){location.hash='#'+route;run('render(true)');assert.match(html(),/data-weave/);assert.match(html(),/weave-enter/);assert.doesNotMatch(html(),/undefined|NaN/);}
}
run("model.switchRole('patient');model.checkIn(true);model.switchRole('clinician');model.replay();model.review('accept',1);model.complete();");
location.hash='#clinician';run('render(true)');assert.match(html(),/gap-outline/);
run("model.upload({id:'DOC-104',case_id:'CASE-104',encounter:'ENC-104',assigned:'Dr. Chen',status:'signed',attested:true,hash:'abc',name:'signed.txt',bytes:20,content:'fixture'});");
assert.doesNotMatch(html(),/gap-outline/);assert.match(html(),/data-ready="false"/);const before=motions;
await new Promise(r=>setTimeout(r,1250));assert.match(html(),/data-ready="true"/);assert.ok(motions>before,'verification transitions animate even with unchanged source revision');
run("model.switchRole('operator');model.invalidate()");assert.match(html(),/gap-outline/);assert.match(html(),/data-ready="false"/);
location.hash='#timeline';run('selectedHistory=0;render(true)');assert.match(html(),/Historical snapshot/);assert.match(html(),/data-ready="false"/);
console.log('PASS: all role routes, entry weaving, evidence gap, readiness animation, source removal and historical snapshots.');
})().catch(e=>{console.error(e);process.exitCode=1});
