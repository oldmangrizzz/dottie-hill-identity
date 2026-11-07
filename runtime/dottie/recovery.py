"""Unattended bounded reconciliation by read-only submission lookup, never redispatch."""
from __future__ import annotations
import threading


class RecoveryLoop:
    def __init__(self,app,interval_seconds=30):
        if not isinstance(interval_seconds,int) or not 15<=interval_seconds<=3600:
            raise ValueError('recovery polling interval must be 15 to 3600 seconds')
        self.app=app
        self.interval=interval_seconds
        self.stop_event=threading.Event()
        self.thread=None

    def poll_once(self):
        # Only watch jobs explicitly registered by the watch controller may resume
        # while still QUEUED, i.e. before any Pi submission could have happened.
        for row in self.app.core.pending_watch_dispatch(limit=100):
            if row['identity_id'] not in self.app.pi_clients:continue
            try:
                self.app.run({'identity_id':row['identity_id'],'content':row['content'],
                              'kind':row['kind'],'request_id':row['request_key']})
            except Exception:pass  # ledger records blocked or uncertain; no blind retry
        for row in self.app.core.pending_reconciliation(limit=100):
            if row['identity_id'] not in self.app.pi_clients:
                continue
            try:
                self.app.reconcile(row['id'])  # GET Pi submission; absolutely no POST message
            except Exception:
                # Preserve uncertainty. Next scheduled pass is an independent read, not redispatch.
                continue

    def _run(self):
        while not self.stop_event.is_set():
            try:self.poll_once()
            except Exception:pass
            self.stop_event.wait(self.interval)

    def start(self):
        if self.thread and self.thread.is_alive():return self.thread
        self.thread=threading.Thread(target=self._run,daemon=True,name='gmri-recovery')
        self.thread.start()
        return self.thread

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
