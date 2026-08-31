"""Shared Freshdesk client for the bulk seeders.

Everything here exists because of one number: the trial allows 50 requests a
minute. A seeder that ignores that does not fail loudly - it 429s, and if the
caller swallows the error the run *looks* complete while silently dropping
records. So: every call goes through one throttle, 429 is retried rather than
skipped, and callers get an exception if a write really did not land.
"""
import base64, json, os, random, socket, sys, time
import urllib.error, urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BOUNDARY = "----fdmigrateBulkSeed"


class Client:
    def __init__(self, domain, api_key, budget=50, verbose=True, timeout=60):
        self.base = f"https://{domain}.freshdesk.com/api/v2"
        self.auth = base64.b64encode(f"{api_key}:X".encode()).decode()
        self.budget = budget          # requests per minute the plan allows
        self.window = []              # timestamps of calls in the last 60s
        self.verbose = verbose
        self.timeout = timeout
        self.calls = 0
        self.throttled = 0
        self.stalls = 0

    # -- rate limiting ----------------------------------------------------
    def _throttle(self):
        """Client-side leaky bucket. Cheaper than discovering the limit by
        being rejected, and keeps `x-ratelimit-remaining` off the floor so a
        concurrent human using the UI is not locked out."""
        now = time.time()
        self.window = [t for t in self.window if now - t < 60]
        if len(self.window) >= self.budget - 2:
            nap = 60 - (now - self.window[0]) + 0.5
            if nap > 0:
                time.sleep(nap)
            self.window = [t for t in self.window if time.time() - t < 60]
        self.window.append(time.time())

    # -- transport --------------------------------------------------------
    def _raw(self, method, path, data=None, ctype=None):
        self._throttle()
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Basic " + self.auth,
                     **({"Content-Type": ctype} if ctype else {})})
        # A read with no timeout hangs forever on a stalled socket - the run
        # goes quiet and looks finished. Always bound it.
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            self.calls += 1
            body = resp.read()
            return json.loads(body) if body else None

    def call(self, method, path, payload=None, files=None, tries=6):
        for attempt in range(tries):
            try:
                if files:
                    data, ctype = _multipart(payload or {}, files)
                    return self._raw(method, path, data, ctype)
                if payload is None:
                    return self._raw(method, path)
                return self._raw(method, path,
                                 json.dumps(payload).encode("utf-8"),
                                 "application/json")
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.throttled += 1
                    wait = int(e.headers.get("Retry-After", 30)) + 1
                    if self.verbose:
                        print(f"      429 - sleeping {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                if e.code in (502, 503, 504) and attempt < tries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise FDError(e.code, e.read().decode("utf-8", "replace"),
                              method, path) from None
            except (urllib.error.URLError, socket.timeout, TimeoutError,
                    ConnectionError) as e:
                # Transport-level stall or reset. Retry; a write that already
                # landed will be caught by the manifest, never duplicated.
                self.stalls += 1
                if attempt >= tries - 1:
                    raise FDError(0, "transport: {}".format(e), method, path)
                if self.verbose:
                    print("      transport stall ({}) - retry {}"
                          .format(type(e).__name__, attempt + 1), flush=True)
                time.sleep(3 * (attempt + 1))
                continue
        raise FDError(429, "exhausted retries", method, path)

    def get(self, path):            return self.call("GET", path)
    def post(self, path, p, files=None): return self.call("POST", path, p, files)
    def put(self, path, p):         return self.call("PUT", path, p)

    def paginate(self, path, per_page=100, cap=100):
        """List endpoints only. `per_page` on a DETAIL endpoint is a 400."""
        out, page = [], 1
        while page <= cap:
            sep = "&" if "?" in path else "?"
            batch = self.get(f"{path}{sep}per_page={per_page}&page={page}")
            if not batch:
                break
            out += batch
            if len(batch) < per_page:
                break
            page += 1
        return out


class FDError(RuntimeError):
    def __init__(self, code, body, method, path):
        self.code, self.body = code, body
        super().__init__(f"{method} {path} -> {code}: {body[:400]}")


def _fmt(v):
    """Multipart carries everything as text, so a Python bool would go out as
    "True" and Freshdesk rejects it with datatype_mismatch. Booleans have to be
    lower-cased on the wire; JSON bodies are unaffected."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _multipart(fields, files):
    """Freshdesk accepts JSON or multipart, never form-urlencoded."""
    buf = bytearray()
    def part(hdr, val=b""):
        buf.extend(f"--{BOUNDARY}\r\n{hdr}\r\n\r\n".encode())
        buf.extend(val)
        buf.extend(b"\r\n")
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for item in v:
                part(f'Content-Disposition: form-data; name="{k}[]"',
                     _fmt(item).encode("utf-8"))
        elif isinstance(v, dict):
            for sub, sv in v.items():
                part(f'Content-Disposition: form-data; name="{k}[{sub}]"',
                     _fmt(sv).encode("utf-8"))
        else:
            part(f'Content-Disposition: form-data; name="{k}"',
                 _fmt(v).encode("utf-8"))
    for name, blob, mime in files:
        part(f'Content-Disposition: form-data; name="attachments[]"; '
             f'filename="{name}"\r\nContent-Type: {mime}', blob)
    buf.extend(f"--{BOUNDARY}--\r\n".encode())
    return bytes(buf), f"multipart/form-data; boundary={BOUNDARY}"


class ConcurrentRun(RuntimeError):
    pass


class Manifest:
    """Crash-safe resume. Written after every record, so an interrupted run
    never duplicates and never restarts from zero.

    It also takes an exclusive lock. Two processes sharing one manifest each
    hold their own stale copy in memory and overwrite the other's on save, so
    progress appears to freeze or go backwards while both keep writing to the
    helpdesk - proven live 31 Aug 2026, which is how three untracked tickets
    got created. A checkpoint file is only safe with a single writer.
    """
    def __init__(self, path, lock=True):
        self.path = path
        self.lockpath = path + ".lock"
        self.lockfd = None
        if lock:
            self._acquire()
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)

    def _acquire(self):
        try:
            # O_EXCL is the atomic part: it fails rather than truncating if
            # another run already holds the lock.
            self.lockfd = os.open(self.lockpath,
                                  os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.lockfd, str(os.getpid()).encode())
        except FileExistsError:
            try:
                owner = open(self.lockpath).read().strip()
            except OSError:
                owner = "unknown"
            raise ConcurrentRun(
                "another run holds {} (pid {}). Stop it first, or delete the "
                "lock if that process is gone.".format(self.lockpath, owner))

    def release(self):
        if self.lockfd is not None:
            os.close(self.lockfd)
            self.lockfd = None
            try:
                os.unlink(self.lockpath)
            except OSError:
                pass

    def has(self, key):  return key in self.data
    def get(self, key):  return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


def client_from_env(which="SOURCE"):
    dom = os.environ.get(f"FD_{which}_DOMAIN")
    key = os.environ.get(f"FD_{which}_API_KEY")
    if not dom or not key:
        raise SystemExit(f"set FD_{which}_DOMAIN and FD_{which}_API_KEY")
    return Client(dom, key, budget=int(os.environ.get("FD_RATE_BUDGET", "50")))
