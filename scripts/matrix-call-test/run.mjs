const { chromium } = await import(process.env.PLAYWRIGHT_MJS || 'playwright');
import http from 'http'; import fs from 'fs';
const S=process.argv[2], policy=process.argv[3]||'all';
const T=JSON.parse(fs.readFileSync(S+'/tokens.json'));
const srv=http.createServer((q,r)=>{ if(q.url=='/lk.js'){r.setHeader('content-type','text/javascript');r.end(fs.readFileSync(S+'/node_modules/livekit-client/dist/livekit-client.umd.js'))} else {r.setHeader('content-type','text/html');r.end('<!doctype html><script src="/lk.js"></script>')}}).listen(18999,'127.0.0.1');
const b=await chromium.launch({executablePath:process.env.CHROME||undefined,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--no-sandbox']});
async function join(u,pub){
  const p=await (await b.newContext()).newPage(); await p.goto('http://127.0.0.1:18999/');
  p.on('console',m=>{const t=m.text(); if(/error|fail/i.test(t)) console.log(u,'console:',t.slice(0,200))});
  await p.evaluate(async ({url,jwt,pub,policy})=>{
    const L=window.LivekitClient; const room=new L.Room(); window.room=room; window.got=0;
    room.on(L.RoomEvent.TrackSubscribed,()=>{window.got++});
    await room.connect(url,jwt,{rtcConfig:{iceTransportPolicy:policy}});
    if(pub){ await room.localParticipant.setMicrophoneEnabled(true); await room.localParticipant.setCameraEnabled(true); }
  },{url:T[u].url,jwt:T[u].jwt,pub,policy});
  return p;
}
async function stats(p){ return p.evaluate(async()=>{
  const out=[]; const e=window.room.engine; 
  for (const [n,t] of [['pub',e.pcManager?.publisher],['sub',e.pcManager?.subscriber]]){ if(!t) continue; const pc=t.pc||t._pc; if(!pc) continue;
    const st=await pc.getStats(); let pair=null,bytesIn=0,bytesOut=0; const c={};
    st.forEach(s=>{ if(s.type=='local-candidate'||s.type=='remote-candidate') c[s.id]=s; });
    st.forEach(s=>{ if(s.type=='candidate-pair'&&s.nominated&&s.state=='succeeded') pair=s; if(s.type=='inbound-rtp') bytesIn+=s.bytesReceived||0; if(s.type=='outbound-rtp') bytesOut+=s.bytesSent||0;});
    const l=pair&&c[pair.localCandidateId], r=pair&&c[pair.remoteCandidateId];
    out.push({n,ice:pc.iceConnectionState,local:l&&`${l.candidateType}/${l.protocol}/${l.relayProtocol||''}`,remote:r&&`${r.candidateType}/${r.protocol}:${r.port}`,bytesIn,bytesOut,rtt:pair&&pair.currentRoundTripTime});
  } return {state:window.room.state,got:window.got,out}; }); }
try{
  const a=await join('calltest_a',true); const c=await join('calltest_b',false);
  await new Promise(r=>setTimeout(r,9000));
  console.log('A',JSON.stringify(await stats(a))); console.log('B',JSON.stringify(await stats(c)));
}catch(e){console.log('ERR',String(e).slice(0,400))}
await b.close(); srv.close();
