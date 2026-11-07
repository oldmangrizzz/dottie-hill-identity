"""Narrow read-only Pi Durable v1 client. Identity belongs to GrizOS, not Pi."""
from __future__ import annotations
from dataclasses import dataclass
import ipaddress
import json
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

class PiError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise PiError('redirects are forbidden at the compute boundary')

@dataclass(frozen=True)
class PiConfig:
    id: str
    base_url: str
    token: str
    cwd: str
    provider: str
    model_id: str

    def __post_init__(self):
        u=urlsplit(self.base_url)
        if u.scheme not in ('http','https') or not u.hostname or u.path not in ('','/') or u.query or u.fragment or u.username or u.password:
            raise ValueError('Pi backend URL must contain only an http(s) origin')
        if u.scheme=='http':
            try: ip=ipaddress.ip_address(u.hostname)
            except ValueError: raise ValueError('Pi plaintext HTTP permitted only on literal loopback') from None
            if not ip.is_loopback:raise ValueError('remote Pi backends require TLS')
        if not self.token or len(self.token)<8 or any(c.isspace() for c in self.token):
            raise ValueError('independent device token required')
        if not self.cwd.startswith('/') or '..' in self.cwd.split('/') or not self.cwd.strip('/'):
            raise ValueError('Pi working directory must be an absolute confined directory')
        if not self.provider or not self.model_id:
            raise ValueError('exact available model provider and id required')


def validate_boundaries(configs):
    """One device, OS identity and credential per intelligence. No shared shell-capable backend."""
    ids=set(); urls=set(); tokens=set(); dirs=set()
    for c in configs:
        if c.id in ids or c.base_url.rstrip('/') in urls or c.token in tokens or c.cwd.rstrip('/') in dirs:
            raise ValueError('Pi identities require isolated credentials, devices, and work roots')
        ids.add(c.id); urls.add(c.base_url.rstrip('/')); tokens.add(c.token);dirs.add(c.cwd.rstrip('/'))


class PiClient:
    def __init__(self,config,transport=None):
        self.config=config
        self.transport=transport or self._request
        self._session=None

    def _request(self,method,path,body=None,headers=None):
        if not path.startswith('/v1/') or '//' in path or '..' in path:
            raise PiError('forbidden Pi API path')
        h={'Authorization':'Bearer '+self.config.token,'Accept':'application/json'}
        if body is not None:h['Content-Type']='application/json'
        h.update(headers or {})
        data=json.dumps(body).encode() if body is not None else None
        req=Request(self.config.base_url.rstrip('/')+path,method=method,headers=h,data=data)
        try:
            with build_opener(NoRedirect()).open(req,timeout=45) as res:
                raw=res.read(2_000_001)
            if len(raw)>2_000_000:raise PiError('Pi response too large')
            out=json.loads(raw)
            if not isinstance(out,(dict,list)):raise PiError('unexpected Pi response')
            return out
        except (HTTPError,URLError,TimeoutError,ValueError,json.JSONDecodeError) as e:
            raise PiError('Pi backend unavailable or refused request: '+type(e).__name__) from e

    def _call(self,method,path,body=None,headers=None):
        return self.transport(method,path,body,headers)

    def ensure_session(self,instructions):
        info=self._call('GET','/v1/device')
        if not isinstance(info,dict) or info.get('apiVersion')!=1:
            raise PiError('Pi backend API version unverified or incompatible')
        available=info.get('models')
        if not isinstance(available,list) or not any(
            isinstance(m,dict) and m.get('provider')==self.config.provider and m.get('id')==self.config.model_id
            for m in available
        ):
            raise PiError('exact configured provider/model id absent from live Pi device roster; no substitution')
        name='GMRI-'+self.config.id
        sessions=self._call('GET','/v1/sessions')
        if not isinstance(sessions,list): raise PiError('malformed Pi sessions response')
        matches=[s for s in sessions if isinstance(s,dict) and s.get('name')==name]
        if len(matches)>1: raise PiError('duplicate persistent Pi session identity')
        newly_created=False
        if matches: session=matches[0]
        else:
            session=self._call('POST','/v1/sessions',{
                'name':name,'cwd':self.config.cwd,
                'model':{'provider':self.config.provider,'modelId':self.config.model_id},
            },{'Idempotency-Key':'gmri-session-'+self.config.id})
            newly_created=True
        if not isinstance(session,dict) or not session.get('id'):
            raise PiError('Pi session identification unavailable')
        if session.get('cwd')!=self.config.cwd or session.get('model')!=self.config.provider+'/'+self.config.model_id:
            raise PiError('persisted Pi session has different work root or model; refusing to adopt or rewrite')
        sid=quote(str(session['id']),safe='')
        cp='/v1/sessions/'+sid+'/agents/main/config'
        old=self._call('GET',cp)
        if not isinstance(old,dict):raise PiError('Pi read-only status malformed')
        if not newly_created and old.get('instructions')!=instructions:
            raise PiError('persistent Pi agent instructions changed; identity lineage requires explicit review')
        # Enforce real Pi tool filtering in backend, not through a system prompt.
        if old.get('readOnly') is not True or newly_created:
            update={'readOnly':True}
            if newly_created:update['instructions']=instructions
            updated=self._call('PATCH',cp,update)
            if not isinstance(updated,dict) or updated.get('readOnly') is not True:
                raise PiError('Pi backend did not confirm read-only policy')
            if newly_created and updated.get('instructions')!=instructions:
                raise PiError('Pi backend did not acknowledge governing instructions')
        self._session=sid
        return sid

    def message(self,request_id,content,instructions):
        if not isinstance(request_id,str) or not request_id or len(request_id)>128:raise PiError('invalid message request id')
        sid=self.ensure_session(instructions)
        return self._call('POST','/v1/sessions/'+sid+'/agents/main/messages',{
            'content':content,'requestId':request_id,'whenBusy':'followUp','waitMs':30000
        })

    def recover(self,submission_id):
        if not self._session: raise PiError('Pi session must be resolved before reconciliation')
        if not str(submission_id).isdigit():raise PiError('invalid submission reference')
        return self._call('GET','/v1/sessions/'+self._session+'/submissions/'+str(submission_id))
