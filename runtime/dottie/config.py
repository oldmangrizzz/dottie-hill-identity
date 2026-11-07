"""Explicit configuration and first-run provisioning; never invent model routes."""
from __future__ import annotations
import json
import os
import secrets
from pathlib import Path
from .core import Core, INDEPENDENT_IDENTITIES
from .pi import PiConfig,PiClient,validate_boundaries
from .adapters import VikingTenant, LayaAdvisor
from .server import Application
from .watch import WatchLoop, WatchDefinition


def initialize(home):
    home=Path(home).expanduser().resolve()
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=='posix':home.chmod(0o700)
    token=home/'operator.token'
    config=home/'config.json'
    if not token.exists():
        fd=os.open(str(token),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(48)+'\n')
    if not config.exists():
        settings={
            'schema':'gmri.dottie.config.v1',
            'state_database':str(home/'state'/'gateway.sqlite3'),
            'operator_token_file':str(token),
            'host':'127.0.0.1','port':8768,
            'pi_devices':{},'viking_tenants':{},'laya':None,
            'watch_root':None,'watch_sources':[],
            'notes':'Populate separately isolated Pi device endpoints only after verifying their installed model ids and secure OS users.'
        }
        fd=os.open(str(config),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(settings,f,indent=2);f.write('\n')
    return {'config_file':str(config),'token_file':str(token),'modified_existing':False}


def _secret(config,token_file_key,env_key):
    env_name=config.get(env_key)
    if env_name:
        key=os.environ.get(env_name)
        if not key:raise ValueError('missing configured secret environment variable '+env_name)
        return key
    file=config.get(token_file_key)
    if not file:raise ValueError('configured secret source required')
    p=Path(file)
    if not p.is_file():raise ValueError('secret file unavailable')
    if os.name=='posix' and p.stat().st_mode&0o077:
        raise ValueError('secret file is readable by other users')
    return p.read_text().strip()


def build(config_path):
    path=Path(config_path).resolve()
    cfg=json.loads(path.read_text())
    if cfg.get('schema')!='gmri.dottie.config.v1':raise ValueError('unexpected Dottie config schema')
    if cfg.get('host')!='127.0.0.1':raise ValueError('direct binding to network interfaces is not allowed')
    port=cfg.get('port',8768)
    if not isinstance(port,int) or not 0<port<65536:raise ValueError('invalid port')
    gateway_token=_secret(cfg,'operator_token_file','operator_token_env')
    clients={};configs=[]
    if not isinstance(cfg.get('pi_devices'),dict):raise ValueError('pi_devices mapping required')
    for ident,info in cfg['pi_devices'].items():
        if ident not in INDEPENDENT_IDENTITIES:raise ValueError('unknown intelligence in Pi device config')
        if not isinstance(info,dict):raise ValueError('invalid device config')
        pi=PiConfig(id=ident,base_url=info['base_url'],token=_secret(info,'token_file','token_env'),cwd=info['cwd'],provider=info['provider'],model_id=info['model_id'])
        configs.append(pi);clients[ident]=PiClient(pi)
    validate_boundaries(configs)
    viking={};viking_tokens=set()
    for ident,info in cfg.get('viking_tenants',{}).items():
        token=_secret(info,'token_file','token_env')
        if token in viking_tokens:raise ValueError('OpenViking tenant credentials must be independent')
        viking_tokens.add(token)
        viking[ident]=VikingTenant(ident,info['base_url'],token)
    reflex=None
    if cfg.get('laya'):
        info=cfg['laya'];token=_secret(info,'token_file','token_env') if ('token_file' in info or 'token_env' in info) else None
        reflex=LayaAdvisor(info['base_url'],token)
    core=Core(cfg['state_database'])
    app=Application(core,clients,gateway_token,external_memory=viking,reflex=reflex)
    watches=cfg.get('watch_sources',[])
    if watches:
        if not cfg.get('watch_root'):raise ValueError('opt-in watch sources require explicit watch_root')
        sources=[WatchDefinition(id=item['id'],relative_path=item['relative_path'],intelligence_id=item['intelligence_id'],instruction=item['instruction'],interval_seconds=item.get('interval_seconds',60)) for item in watches]
        app.watch_loop=WatchLoop(app,cfg['watch_root'],sources)
    return app,'127.0.0.1',port
