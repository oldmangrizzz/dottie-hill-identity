"""Command-line entrypoint: python -m dottie init|doctor|serve."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from .config import build,initialize
from .server import create_server
from .recovery import RecoveryLoop


def main():
    p=argparse.ArgumentParser(prog='dottie',description='Dottie Hill read-only continuity gateway')
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init',help='Generate private operator token and local config without overwriting existing files')
    init.add_argument('--data-dir',default=str(Path.home()/'.local/share/gmri/dottie'))
    for c in ('doctor','serve'):
        s=sub.add_parser(c)
        s.add_argument('--config',required=True)
    args=p.parse_args()
    if args.command=='init':
        print(json.dumps(initialize(args.data_dir),indent=2));return
    app,host,port=build(args.config)
    if args.command=='doctor':
        status=app.core.summary()
        probes={}
        for name,pi in app.pi_clients.items():
            try:
                probe=pi._call('GET','/v1/device')
                probes[name]={'reachable':probe.get('apiVersion')==1,'api_version':probe.get('apiVersion')}
            except Exception as e:probes[name]={'reachable':False,'error_type':type(e).__name__}
        print(json.dumps({'gateway':'config-valid','model_devices':probes,'field_accepted':False,'receipt_integrity':status['receipts'],'identities':status['identity_count']},indent=2))
        return
    srv=create_server(app,host,port)
    recovery=RecoveryLoop(app)
    recovery.start()
    if getattr(app,'watch_loop',None):app.watch_loop.start()
    print('Dottie gateway serving on loopback port '+str(port)+'. Field acceptance is not implied.',flush=True)
    try:srv.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:pass
    finally:
        if getattr(app,'watch_loop',None):app.watch_loop.stop()
        recovery.stop()
        srv.server_close()
if __name__=='__main__':main()
