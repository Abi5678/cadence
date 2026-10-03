const assert=require('node:assert/strict');
const {create}=require('../web/model.js');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const doc={id:'DOC-104',case_id:'CASE-104',encounter:'ENC-104',assigned:'Dr. Chen',status:'signed',attested:true,hash:'fixture-hash'};
(async()=>{
 const m=create();assert.equal(m.state.role,'clinician');assert.throws(()=>m.complete());
 assert.throws(()=>m.replay());assert.throws(()=>m.setAudio({name:'demo.wav'}));m.switchRole('patient');assert.throws(()=>m.checkIn(false));assert.throws(()=>m.checkIn(true,{preferredName:'P-104',contactPreference:'Local kiosk',diagnosis:'not permitted'}));m.checkIn(true,{preferredName:'Demo patient',contactPreference:'Local kiosk'});assert.equal(m.state.profile.preferredName,'Demo patient');const checkinEvents=m.state.events.length;m.checkIn(true);assert.equal(m.state.events.length,checkinEvents);m.switchRole('clinician');m.replay();m.review('reject',1);assert.equal(m.state.packet,'BLOCKED');assert.throws(()=>m.complete());
 m.edit('Reviewed synthetic summary',1);assert.throws(()=>m.review('accept',1));m.review('accept',2);m.complete();assert.equal(m.state.request,true);assert.deepEqual(m.unresolved,[{item:'Signed encounter document',owner:'Dr. Chen'}]);
 assert.throws(()=>m.upload({...doc,encounter:'ENC-999'}));assert.throws(()=>m.upload({...doc,status:'unsigned'}));assert.throws(()=>m.upload({...doc,attested:false}));
 m.switchRole('patient');assert.throws(()=>m.upload(doc));m.switchRole('clinician');m.upload(doc);assert.equal(m.state.packet,'ASSEMBLING');
 const count=m.state.events.length;m.upload(doc);assert.equal(m.state.events.length,count);
 await wait(1200);assert.equal(m.state.packet,'READY_FOR_COORDINATOR_REVIEW');assert.equal(m.state.manifest.claim_submitted,false);assert.deepEqual(m.unresolved,[]);assert.equal(m.state.manifest.review.text,m.state.manifest.reviewed_text);assert.equal(m.state.manifest.independent_backend_verification,false);
 m.switchRole('coordinator');m.acknowledge();assert.equal(m.state.ack,true);m.switchRole('clinician');m.edit('Changed exact text',2);assert.equal(m.state.packet,'BLOCKED');assert.equal(m.state.manifest,null);assert.equal(m.state.ack,false);
 m.review('accept',3);await wait(650);assert.equal(m.state.packet,'VERIFYING');m.switchRole('operator');m.invalidate();await wait(650);assert.equal(m.state.packet,'BLOCKED');assert.equal(m.state.manifest,null);
 m.reset();assert.equal(m.state.role,'operator');assert.equal(m.state.draft,null);console.log('PASS: review/version guards, ownership, unsigned and unattested documents, role guards, automatic readiness, duplicates, acknowledgment, stale verification and reset');
})().catch(e=>{console.error(e);process.exitCode=1;});
