"""Bounded, opt-in, local source polling for watch continuity."""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import threading
from .core import INDEPENDENT_IDENTITIES

class SourceBoundaryError(Exception):pass

@dataclass(frozen=True)
class WatchDefinition:
    id: str
    relative_path: str
    intelligence_id: str
    instruction: str
    interval_seconds: int = 60
    def __post_init__(self):
        if not self.id or not self.id.replace('-','').replace('_','').isalnum() or len(self.id)>50:
            raise ValueError('invalid source watch id')
        p=Path(self.relative_path)
        if p.is_absolute() or '..' in p.parts or not self.relative_path or self.relative_path.startswith('.'):
            raise ValueError('watch file must be a relative path inside authorized root')
        if self.intelligence_id not in INDEPENDENT_IDENTITIES:raise ValueError('unknown watch recipient identity')
        if type(self.interval_seconds)!=int or self.interval_seconds<15 or self.interval_seconds>86400:
            raise ValueError('bounded polling interval required')
        if not isinstance(self.instruction,str) or not self.instruction.strip() or len(self.instruction)>2000:
            raise ValueError('watch source instruction invalid')

class WatchLoop:
    def __init__(self,app,root,sources):
        self.app=app
        self.root=Path(root).resolve(strict=True)
        if not self.root.is_dir():raise ValueError('source watch root not a directory')
        self.sources=list(sources)
        if len(set(s.id for s in self.sources))!=len(self.sources):raise ValueError('duplicate watch source ids')
        self.stop_event=threading.Event()
        self._last={}

    def _read(self,name):
        resolved=(self.root/name).resolve(strict=True)
        if not resolved.is_relative_to(self.root):raise SourceBoundaryError('watch path escapes approved root')
        # Avoid following a swapped symlink as the final file.
        fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>60000:
                raise SourceBoundaryError('watch source not a bounded regular file')
            with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(60001)
            if len(data)>60000:raise SourceBoundaryError('watch source expanded beyond limit')
            return data.decode('utf-8')
        finally:os.close(fd)

    def poll_once(self):
        for src in self.sources:
            content=self._read(src.relative_path)
            event=self.app.core.register_watch_observation(src.id,src.intelligence_id,content,str((self.root/src.relative_path).resolve()),src.instruction)
            if event.get('changed'):
                try:self.app.run({'identity_id':src.intelligence_id,'content':event['content'],'kind':'OBSERVE','request_id':event['request_id']})
                except Exception:pass # outcome is captured as blocked/uncertain, never silently retried

    def run(self):
        while not self.stop_event.is_set():
            try:self.poll_once()
            except (OSError,UnicodeError,SourceBoundaryError):pass # no false evidence or alert if source unavailable
            self.stop_event.wait(min((s.interval_seconds for s in self.sources),default=60))

    def start(self):
        if not self.sources:return None
        t=threading.Thread(target=self.run,daemon=True,name='gmri-watch');t.start();return t

    def stop(self):self.stop_event.set()
