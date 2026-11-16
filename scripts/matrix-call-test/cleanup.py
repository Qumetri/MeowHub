"""Leave the test room and deactivate the two temporary accounts (self-service, no admin token needed)."""
import json, os, sys, urllib.request, urllib.parse
PW = open(sys.argv[1]).read().strip(); SFX = sys.argv[3] if len(sys.argv) > 3 else ''; T = json.load(open(sys.argv[2])); HS = 'https://' + os.environ.get('MATRIX_HOST', 'matrix.' + os.environ['BASE_DOMAIN'])
def req(path, data, tok):
    r = urllib.request.Request(HS + path, json.dumps(data).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok, 'User-Agent': 'calltest'})
    try:
        return urllib.request.urlopen(r, timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code
for u in ('calltest_a', 'calltest_b'):
    tok = T[u + '_mx']; room = urllib.parse.quote(T['room'])
    req(f'/_matrix/client/v3/rooms/{room}/leave', {}, tok); req(f'/_matrix/client/v3/rooms/{room}/forget', {}, tok)
    print(u, 'deactivate', req('/_matrix/client/v3/account/deactivate', {'erase': True, 'auth': {'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': u + SFX}, 'password': PW}}, tok))
