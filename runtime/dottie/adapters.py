"""Optional scoped retrieval and advisory reflex. Neither owns identity nor authority."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError,URLError
from .pi import NoRedirect
from .core import INDEPENDENT_IDENTITIES, bounded

class BoundaryViolation(Exception):
    pass


def validate_origin(url):
    u=urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.path not in ('','/') or u.username or u.password or u.query or u.fragment:
        raise BoundaryViolation('only explicit API origins allowed')
    if u.scheme=='http':
        try: addr=ipaddress.ip_address(u.hostname)
        except ValueError: raise BoundaryViolation('unencrypted remote API disallowed') from None
        if not addr.is_loopback:raise BoundaryViolation('unencrypted remote API disallowed')
    return url.rstrip('/')


def json_post(origin,path,payload,headers):
    req=Request(origin+path,method='POST',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json',**headers})
    try:
        with build_opener(NoRedirect()).open(req,timeout=12) as r:
            blob=r.read(2_000_001)
        if len(blob)>2_000_000:raise BoundaryViolation('upstream response too large')
        return json.loads(blob)
    except (HTTPError,URLError,ValueError,TimeoutError) as exc:
        raise BoundaryViolation('optional adapter failed: '+type(exc).__name__) from exc

class VikingTenant:
    def __init__(self,identity,base_url,token,transport=None):
        if identity not in INDEPENDENT_IDENTITIES:raise BoundaryViolation('tenant must match named identity')
        self.identity=identity
        self.origin=validate_origin(base_url)
        if not token:raise BoundaryViolation('per-user Viking API credential required')
        self.token=token
        self.transport=transport
        self.prefix='viking://user/'+identity+'/memories'

    def _call(self,path,data):
        return self.transport(path,data) if self.transport else json_post(self.origin,path,data,{'X-API-Key':self.token})

    def search(self,query):
        q=bounded(query,'query',1500)
        response=self._call('/api/v1/search/find',{'query':q,'target_uri':self.prefix,'context_type':['memory'],'limit':10,'read_content':False,'include_provenance':True})
        if not isinstance(response,dict) or response.get('status')!='ok' or not isinstance(response.get('result'),dict):
            raise BoundaryViolation('OpenViking response invalid')
        result=response['result']
        for key in ('memories','resources','skills'):
            for hit in result.get(key,[]):
                if not isinstance(hit,dict) or not isinstance(hit.get('uri'),str) or not hit['uri'].startswith(self.prefix+'/'):
                    raise BoundaryViolation('out-of-scope OpenViking hit blocked')
        return result

    def write_explicit(self,memory_id,content,source_ref,approved=False):
        if approved is not True:raise BoundaryViolation('external memory promotion requires a separate explicit approval')
        if not isinstance(memory_id,str) or not re.fullmatch(r'mem_[a-z0-9_-]{3,100}',memory_id):
            raise BoundaryViolation('invalid approved memory id')
        content=bounded(content,'content',20000)
        source_ref=bounded(source_ref,'source reference',500)
        digest=hashlib.sha256(content.encode()).hexdigest()
        uri=self.prefix+'/gmri-promoted/'+memory_id+'.md'
        markdown='Source: '+source_ref.replace('\n',' ')+'\nSHA-256: '+digest+'\nProvenance: GMRI operator approved\n\n'+content
        response=self._call('/api/v1/content/write',{'uri':uri,'content':markdown,'mode':'replace','wait':True,'tags':['source=gmri','promotion=operator_approved']})
        if not isinstance(response,dict) or response.get('status')!='ok':
            raise BoundaryViolation('OpenViking promotion unverified')
        return {'uri':uri,'sha256':digest,'upstream_status':'ok'}

class LayaAdvisor:
    def __init__(self,base_url,token=None,transport=None):
        self.origin=validate_origin(base_url)
        self.token=token
        self.transport=transport

    def decide(self,request):
        if not isinstance(request,dict) or not isinstance(request.get('state'),dict) or not isinstance(request.get('questions'),dict):
            raise BoundaryViolation('typed reflex state and questions required')
        if len(json.dumps(request))>12000:raise BoundaryViolation('reflex input too large')
        questions=request['questions']
        if not questions or len(questions)>10:raise BoundaryViolation('reflex question count exceeds bounded scope')
        for q in questions.values():
            if not isinstance(q,dict) or q.get('type') not in ('choice','score','noul'):
                raise BoundaryViolation('only typed non-authority reflex verdicts allowed')
        body={'state':request['state'],'questions':questions}
        result=(self.transport('/v1/systemone',body) if self.transport else json_post(self.origin,'/v1/systemone',body,{'Authorization':'Bearer '+self.token} if self.token else {}))
        if not isinstance(result,dict):raise BoundaryViolation('invalid LAYA response')
        # A reflex result is advisory; caller must not map it to permissions.
        return result
