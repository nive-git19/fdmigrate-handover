"""Shared test doubles - no network, no real Freshdesk. Kept deliberately small."""
from __future__ import annotations

import logging


def silent_logger() -> logging.Logger:
    lg = logging.getLogger("fdmigrate-test")
    if not lg.handlers:
        lg.addHandler(logging.NullHandler())
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    return lg


class FakeResp:
    """Stand-in for a requests.Response."""
    def __init__(self, status_code=201, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers: dict = {}

    def json(self):
        return self._payload


class FakeClient:
    """Serves canned GET/get_raw data by path; records + programs writes.
      get_map:  {path: json}                 for .get()
      raw_map:  {path: FakeResp}             for .get_raw()
      post_seq: [FakeResp, ...]              popped in order by .post()
    """
    def __init__(self, get_map=None, raw_map=None, post_seq=None):
        self.get_map = get_map or {}
        self.raw_map = raw_map or {}
        self.post_seq = list(post_seq or [])
        self.writes: list = []

    def whoami(self):
        return {"id": 1, "contact": {"email": "agent@x.com"}}

    def get(self, path, **kw):
        return self.get_map.get(path, [])

    def get_raw(self, path, **kw):
        return self.raw_map.get(path, FakeResp(404, text=""))

    def post(self, path, **kw):
        self.writes.append(("POST", path, kw))
        return self.post_seq.pop(0) if self.post_seq else FakeResp(201, {"id": 999})

    def put(self, path, **kw):
        self.writes.append(("PUT", path, kw))
        return FakeResp(200, {})

    def delete(self, path, **kw):
        self.writes.append(("DELETE", path, kw))
        return FakeResp(204, {})


class FakeCtx:
    """Minimal phases.base.Context stand-in for pure-logic tests."""
    def __init__(self, cfg, src=None, tgt=None, store=None):
        self.cfg = cfg
        self.src = src
        self.tgt = tgt
        self.store = store
        self.logger = silent_logger()
        self.events: list = []

    def log(self, entity, source_id, level, action, message):
        self.events.append((entity, source_id, level, action, message))

    def actions(self):
        return [e[3] for e in self.events]
