/* Shared animation lifecycle. Uploads take priority; greetings and celebrations queue. */
window.WeaverWork=(()=>{
let pending=0,phase=null,timer=null;const queue=[];
const emit=()=>document.dispatchEvent(new CustomEvent('weaver-work-change'));
function next(){if(pending||phase||!queue.length)return;phase=queue.shift();emit();timer=setTimeout(()=>{phase=null;timer=null;emit();next()},phase==='greeting'?1900:2700)}
function request(kind){if(phase===kind){clearTimeout(timer);phase=null}if(!queue.includes(kind))queue.push(kind);next()}
return {
get greeting(){return phase==='greeting'},get celebrating(){return phase==='celebrating'},get active(){return pending>0},
greet(){request('greeting');document.querySelectorAll?.('.weaver-pose[data-pose=greeting]')?.forEach(n=>n.getAnimations?.().forEach(a=>{a.currentTime=0}))},celebrate(){request('celebrating')},
begin(){pending++;if(phase){if(!queue.includes(phase))queue.unshift(phase);clearTimeout(timer);timer=null;phase=null}emit();let ended=false;return ()=>{if(ended)return;ended=true;pending=Math.max(0,pending-1);emit();next()}},
markup(){return `<svg class="weaver-knot" viewBox="0 0 180 90" aria-hidden="true"><g fill="none" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"><path class="knot-thread knot-iris" pathLength="100" d="M4 49 C29 49 34 20 62 23 C93 26 113 69 88 73 C59 78 62 14 98 16 C130 17 127 53 175 45"/><path class="knot-thread knot-gold" pathLength="100" d="M4 60 C45 67 64 21 87 23 C114 26 103 71 75 63 C44 53 91 7 117 29 C140 50 139 59 175 57"/><path class="knot-thread knot-blue" pathLength="100" d="M4 38 C40 28 51 67 83 60 C110 54 105 18 125 24 C148 32 141 39 175 33"/><path class="knot-over" d="M66 42 C71 27 80 16 98 16" stroke="#B5C7DC" stroke-width="12"/><path class="knot-over" d="M66 42 C71 27 80 16 98 16" stroke="#9286DB" stroke-width="9"/></g></svg><span class="weaver-celebration" aria-hidden="true">${Array.from({length:8},(_,i)=>`<i style="--burst:${i};--angle:${i*45}deg"></i>`).join('')}</span>`}}})();
