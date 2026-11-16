import json,os,urllib.request,sys,re
PW=open(sys.argv[1]).read().strip(); SFX=sys.argv[3] if len(sys.argv)>3 else ''; HS='https://'+os.environ.get('MATRIX_HOST','matrix.'+os.environ['BASE_DOMAIN'])
def req(url,data=None,tok=None,method=None):
    h={'Content-Type':'application/json','User-Agent':'calltest'}
    if tok: h['Authorization']='Bearer '+tok
    r=urllib.request.Request(url,json.dumps(data).encode() if data is not None else None,h,method=method)
    try:
        with urllib.request.urlopen(r,timeout=20) as f: return f.status,json.loads(f.read() or b'{}')
    except urllib.error.HTTPError as e: return e.code,e.read()[:300]
wk=req('https://'+os.environ['BASE_DOMAIN']+'/.well-known/matrix/client')[1]
jwt=wk['org.matrix.msc4143.rtc_foci'][0]['livekit_service_url']
out={}
room=None
for u in ('calltest_a','calltest_b'):
    s,l=req(HS+'/_matrix/client/v3/login',{'type':'m.login.password','identifier':{'type':'m.id.user','user':u+SFX},'password':PW,'device_id':'CT'+u[-1].upper()})
    tok=l['access_token']; uid=l['user_id']
    if room is None:
        s,r=req(HS+'/_matrix/client/v3/createRoom',{'preset':'public_chat','name':'calltest'},tok); room=r['room_id']
    else:
        s,r=req(HS+'/_matrix/client/v3/join/'+urllib.parse.quote(room),{},tok)
    s,o=req(f"{HS}/_matrix/client/v3/user/{urllib.parse.quote(uid)}/openid/request_token",{},tok)
    for ep in ('/sfu/get','/get_token'):
        s,j=req(jwt+ep,{'room':room,'openid_token':o,'device_id':l['device_id']})
        print(u,ep,s, (list(j.keys()) if isinstance(j,dict) else j))
        if s==200:
            out[u]=j; break
    out[u+'_mx']=tok
import urllib.parse
json.dump({'room':room,**out},open(sys.argv[2],'w'))
print('url:',re.sub(r'/[a-z]+-[0-9a-f]{8,}/','/<path>/',out.get('calltest_a',{}).get('url','-')))
